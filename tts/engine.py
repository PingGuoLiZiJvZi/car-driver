from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import subprocess
import uuid
from typing import Any

try:
    import websockets
except ImportError:
    websockets = None

LOGGER = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


class TTSEngine:
    def __init__(
        self,
        tts_key: str | None = None,
        app_id: str | None = None,
        resource_id: str = "seed-tts-1.0",
        speaker: str = "zh_female_cancan_mars_bigtts",
        model: str | None = None,
        sample_rate: int = 24000,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        ws_url: str = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream",
        timeout_s: float = 20.0,
        user_uid: str = "car-driver",
        output_device: str | None = None,
    ) -> None:
        raw_key = (tts_key or os.environ.get("TTS_KEY") or "").strip()
        key_app_id, access_key = self._split_tts_key(raw_key)

        env_app_id = (app_id or os.environ.get("TTS_APP_ID") or "").strip()
        self.app_id = env_app_id or key_app_id
        self.access_key = access_key

        self.resource_id = (resource_id or os.environ.get("TTS_RESOURCE_ID") or "seed-tts-1.0").strip()
        self.speaker = (speaker or os.environ.get("TTS_SPEAKER") or "zh_female_cancan_mars_bigtts").strip()
        self.model = (model or os.environ.get("TTS_MODEL") or "").strip() or None
        self.sample_rate = int(sample_rate)
        self.speech_rate = int(speech_rate)
        self.loudness_rate = int(loudness_rate)
        self.ws_url = (ws_url or os.environ.get("TTS_WS_URL") or "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream").strip()
        self.timeout_s = max(5.0, float(timeout_s))
        self.user_uid = user_uid
        self.output_device = output_device

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            raise TTSError("text is empty")

        if websockets is None:
            raise TTSError("websockets is not installed. Please run: pip install websockets>=12.0")

        if not self.access_key:
            raise TTSError("TTS_KEY is not set")
        if not self.app_id:
            raise TTSError("TTS_APP_ID is not set (or encode appid in TTS_KEY as '<appid>:<access_key>')")

        if shutil.which("aplay") is None:
            raise TTSError("aplay not found, please install alsa-utils")

        try:
            wav_bytes = self._run_async(self._synthesize_wav_bytes(text))
            self._play_wav(wav_bytes)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"doubao tts failed: {exc}") from exc

    @staticmethod
    def _split_tts_key(raw_key: str) -> tuple[str, str]:
        if not raw_key:
            return "", ""
        if ":" in raw_key:
            maybe_appid, maybe_key = raw_key.split(":", 1)
            if maybe_appid.strip() and maybe_key.strip():
                return maybe_appid.strip(), maybe_key.strip()
        return "", raw_key

    @staticmethod
    def _run_async(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        raise TTSError("internal error: speak() must run outside an active event loop")

    async def _synthesize_wav_bytes(self, text: str) -> bytes:
        headers = {
            "X-Api-App-Id": self.app_id,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

        req: dict[str, Any] = {
            "user": {"uid": self.user_uid},
            "req_params": {
                "text": text,
                "speaker": self.speaker,
                "audio_params": {
                    "format": "wav",
                    "sample_rate": self.sample_rate,
                    "speech_rate": self.speech_rate,
                    "loudness_rate": self.loudness_rate,
                },
            },
        }
        if self.model:
            req["req_params"]["model"] = self.model

        connect_kwargs = {
            "open_timeout": self.timeout_s,
            "close_timeout": 2,
            "ping_interval": None,
            "max_size": None,
        }

        ws_cm = None
        try:
            ws_cm = websockets.connect(self.ws_url, additional_headers=headers, **connect_kwargs)
        except TypeError:
            ws_cm = websockets.connect(self.ws_url, extra_headers=headers, **connect_kwargs)

        audio_chunks: list[bytes] = []

        assert ws_cm is not None
        async with ws_cm as ws:
            await ws.send(self._build_send_text_frame(req))

            while True:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                except TimeoutError as exc:
                    raise TTSError(f"doubao websocket recv timeout ({self.timeout_s}s)") from exc

                if isinstance(frame, str):
                    continue

                event, payload, message_type = self._parse_server_frame(frame)

                if event == 352 and message_type == 0xB:
                    if isinstance(payload, (bytes, bytearray)):
                        audio_chunks.append(bytes(payload))
                    continue

                if event == 152:
                    status_code = payload.get("status_code") if isinstance(payload, dict) else None
                    if status_code is not None and int(status_code) != 20000000:
                        raise TTSError(f"doubao session finished with status_code={status_code}: {payload}")
                    break

        if not audio_chunks:
            raise TTSError("doubao returned empty audio")

        return b"".join(audio_chunks)

    @staticmethod
    def _build_send_text_frame(payload: dict[str, Any]) -> bytes:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # 0x11: protocol v1 + 4-byte header; 0x10: full client request (no event);
        # 0x10: JSON + no compression; 0x00: reserved.
        header = bytes([0x11, 0x10, 0x10, 0x00])
        return header + len(body).to_bytes(4, "big") + body

    def _parse_server_frame(self, frame: bytes) -> tuple[int | None, Any, int]:
        if len(frame) < 4:
            raise TTSError("doubao protocol error: frame too short")

        header_size = (frame[0] & 0x0F) * 4
        if header_size < 4 or len(frame) < header_size:
            raise TTSError("doubao protocol error: invalid header size")

        message_type = frame[1] >> 4
        message_flags = frame[1] & 0x0F
        serialization = frame[2] >> 4
        compression = frame[2] & 0x0F

        offset = header_size

        if message_type == 0xF:
            if len(frame) < offset + 4:
                raise TTSError("doubao protocol error: malformed error frame")
            err_code = int.from_bytes(frame[offset : offset + 4], "big")
            err_payload = frame[offset + 4 :]
            err_text = self._decode_payload_to_text(err_payload, serialization, compression)
            raise TTSError(f"doubao error {err_code}: {err_text}")

        event: int | None = None
        if message_flags == 0x4:
            if len(frame) < offset + 4:
                raise TTSError("doubao protocol error: missing event")
            event = int.from_bytes(frame[offset : offset + 4], "big")
            offset += 4

            if len(frame) >= offset + 4:
                session_id_len = int.from_bytes(frame[offset : offset + 4], "big")
                offset += 4
                if session_id_len > 0 and len(frame) >= offset + session_id_len:
                    offset += session_id_len

        payload: bytes
        if len(frame) >= offset + 4:
            payload_len = int.from_bytes(frame[offset : offset + 4], "big")
            offset += 4
            payload = frame[offset : offset + payload_len]
        else:
            payload = frame[offset:]

        if compression == 0x1 and payload:
            payload = gzip.decompress(payload)

        if message_type == 0xB:
            return event, payload, message_type

        if serialization == 0x1 and payload:
            try:
                return event, json.loads(payload.decode("utf-8")), message_type
            except Exception:
                return event, payload.decode("utf-8", errors="ignore"), message_type

        return event, payload, message_type

    @staticmethod
    def _decode_payload_to_text(payload: bytes, serialization: int, compression: int) -> str:
        data = payload
        if compression == 0x1 and data:
            try:
                data = gzip.decompress(data)
            except Exception:
                pass

        if not data:
            return ""

        if serialization == 0x1:
            try:
                obj = json.loads(data.decode("utf-8"))
                return json.dumps(obj, ensure_ascii=False)
            except Exception:
                pass

        return data.decode("utf-8", errors="ignore")

    def _play_wav(self, wav_bytes: bytes) -> None:
        if not wav_bytes:
            raise TTSError("doubao returned empty wav bytes")

        play_cmd = ["aplay", "-q"]
        if self.output_device:
            play_cmd.extend(["-D", self.output_device])
        play_cmd.extend(["-t", "wav", "-"])
        LOGGER.info("TTS playback via: %s", " ".join(play_cmd))
        subprocess.run(play_cmd, input=wav_bytes, check=True)
