from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

VIDIOC_QUERYCAP = 0x80685600
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
V4L2_CAP_DEVICE_CAPS = 0x80000000


@dataclass
class CameraConfig:
    device: str = "/dev/video0"
    width: int = 640
    height: int = 480
    fps: int = 25
    input_format: str = "mjpeg"


@dataclass
class MicConfig:
    device: str = "default"
    sample_rate: int = 16000
    channels: int = 1
    chunk_ms: int = 100

    @property
    def chunk_bytes(self) -> int:
        # 16-bit PCM => 2 bytes per sample.
        return max(320, int(self.sample_rate * self.channels * 2 * (self.chunk_ms / 1000.0)))


class MjpegCameraStream:
    def __init__(self, cfg: CameraConfig) -> None:
        self.cfg = cfg
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

        self._frame_lock = threading.Lock()
        self._frame: bytes | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera-mjpeg-reader")
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self._terminate_proc()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def latest_frame(self) -> bytes | None:
        with self._frame_lock:
            return self._frame

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._spawn_proc()
                self._read_frames()
            except Exception:
                LOGGER.exception("Camera stream failed, retrying")
                self._terminate_proc()
            time.sleep(0.8)

    def _spawn_proc(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found, please install ffmpeg")

        capture_device = _resolve_capture_device(self.cfg.device)

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-framerate",
            str(self.cfg.fps),
            "-video_size",
            f"{self.cfg.width}x{self.cfg.height}",
        ]
        if self.cfg.input_format:
            cmd.extend(["-input_format", self.cfg.input_format])
        cmd.extend(
            [
                "-i",
                capture_device,
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "5",
                "-",
            ]
        )

        LOGGER.info("Starting camera capture: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _read_frames(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None

        buf = bytearray()
        while not self._stop_evt.is_set():
            chunk = self._proc.stdout.read(4096)
            if not chunk:
                break
            buf.extend(chunk)

            while True:
                start = buf.find(b"\xff\xd8")
                if start == -1:
                    # Keep only tail bytes, avoids unbounded growth.
                    if len(buf) > 8192:
                        del buf[:-2048]
                    break

                end = buf.find(b"\xff\xd9", start + 2)
                if end == -1:
                    if start > 0:
                        del buf[:start]
                    break

                frame = bytes(buf[start : end + 2])
                del buf[: end + 2]
                with self._frame_lock:
                    self._frame = frame

    def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


class PcmMicBroadcaster:
    def __init__(self, cfg: MicConfig) -> None:
        self.cfg = cfg
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._sub_lock = threading.Lock()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop = loop
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mic-pcm-reader")
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self._terminate_proc()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def subscribe(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=8)
        with self._sub_lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        with self._sub_lock:
            self._subscribers.discard(queue)

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._spawn_proc()
                self._broadcast_loop()
            except Exception:
                LOGGER.exception("Mic stream failed, retrying")
                self._terminate_proc()
            time.sleep(0.8)

    def _spawn_proc(self) -> None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg not found, please install ffmpeg")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "alsa",
            "-i",
            self.cfg.device,
            "-ac",
            str(self.cfg.channels),
            "-ar",
            str(self.cfg.sample_rate),
            "-f",
            "s16le",
            "-",
        ]
        LOGGER.info("Starting mic capture: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _broadcast_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        assert self._loop is not None

        while not self._stop_evt.is_set():
            chunk = self._proc.stdout.read(self.cfg.chunk_bytes)
            if not chunk:
                break
            self._fan_out(chunk)

    def _fan_out(self, chunk: bytes) -> None:
        assert self._loop is not None
        with self._sub_lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            self._loop.call_soon_threadsafe(_queue_push_drop_oldest, queue, chunk)

    def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


class TalkbackPcmPlayer:
    def __init__(self, sample_rate: int = 16000, channels: int = 1, output_device: str | None = None) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.output_device = output_device
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def push_pcm(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return

        with self._lock:
            self._ensure_proc()
            assert self._proc is not None
            assert self._proc.stdin is not None
            try:
                self._proc.stdin.write(pcm_bytes)
                self._proc.stdin.flush()
            except BrokenPipeError:
                self._terminate_proc()
                self._ensure_proc()
                assert self._proc is not None
                assert self._proc.stdin is not None
                self._proc.stdin.write(pcm_bytes)
                self._proc.stdin.flush()

    def suspend(self) -> None:
        with self._lock:
            self._terminate_proc()

    def close(self) -> None:
        self.suspend()

    def _ensure_proc(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return

        if shutil.which("aplay") is None:
            raise RuntimeError("aplay not found, please install alsa-utils")

        cmd = [
            "aplay",
        ]
        if self.output_device:
            cmd.extend(["-D", self.output_device])
        cmd.extend(
            [
            "-q",
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-c",
            str(self.channels),
            "-r",
            str(self.sample_rate),
            ]
        )
        LOGGER.info("Starting talkback player: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def _queue_push_drop_oldest(queue: asyncio.Queue[bytes], chunk: bytes) -> None:
    try:
        queue.put_nowait(chunk)
        return
    except asyncio.QueueFull:
        pass

    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass

    try:
        queue.put_nowait(chunk)
    except asyncio.QueueFull:
        # Consumer is too slow. Keep running with newest data only.
        pass


def available_cmd(candidates: Iterable[str]) -> str | None:
    for cmd in candidates:
        if shutil.which(cmd) is not None:
            return cmd
    return None


def _resolve_capture_device(preferred: str) -> str:
    pref = preferred.strip() if preferred else ""
    if pref and _is_v4l2_capture_device(pref):
        return pref

    candidates = sorted(Path("/dev").glob("video*"), key=_video_sort_key)
    capture_nodes = [str(node) for node in candidates if _is_v4l2_capture_device(str(node))]
    if capture_nodes:
        selected = capture_nodes[0]
        if pref and pref != selected:
            LOGGER.warning(
                "Configured camera device %s is not capture-capable, fallback to %s",
                pref,
                selected,
            )
        return selected

    if pref:
        raise RuntimeError(f"No capture-capable V4L2 device found (configured: {pref})")
    raise RuntimeError("No capture-capable V4L2 device found under /dev/video*")


def _video_sort_key(path: Path) -> tuple[int, str]:
    suffix = path.name.replace("video", "", 1)
    if suffix.isdigit():
        return (0, f"{int(suffix):04d}")
    return (1, path.name)


def _is_v4l2_capture_device(device_path: str) -> bool:
    try:
        fd = os.open(device_path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return False

    try:
        caps_buf = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, caps_buf, True)
        caps = int.from_bytes(caps_buf[88:92], "little")
        device_caps = int.from_bytes(caps_buf[92:96], "little")
        effective_caps = device_caps if (caps & V4L2_CAP_DEVICE_CAPS) else caps
        return bool(effective_caps & (V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_CAPTURE_MPLANE))
    except OSError:
        return False
    finally:
        os.close(fd)
