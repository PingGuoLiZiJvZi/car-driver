from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from server.access import AccessError, AccessManager, SessionRecord
from server.chassis_driver import ChassisDriver, ChassisError
from server.media import CameraConfig, MicConfig, MjpegCameraStream, PcmMicBroadcaster, TalkbackPcmPlayer
from tts.engine import TTSEngine, TTSError

LOG_LEVEL = os.environ.get("CAR_SERVER_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger("car-server")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "server" / "static"
USERS_DB_PATH = PROJECT_ROOT / "server" / "users.json"
AUDIT_LOG_PATH = PROJECT_ROOT / "server" / "audit.log"
SESSION_COOKIE_NAME = "car_session"
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"


class AuthRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class ControlAcquireRequest(BaseModel):
    force: bool = False


class ControlRequestCreate(BaseModel):
    targetUser: str | None = Field(default=None, max_length=32)


class ControlRequestDecision(BaseModel):
    requestId: str = Field(min_length=1, max_length=128)
    approve: bool


class KickRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=240)


@dataclass
class DriverClientState:
    username: str
    keys: set[str] = field(default_factory=set)
    speed: float = 0.6
    last_seen: float = field(default_factory=lambda: time.monotonic())


class DriveCoordinator:
    def __init__(self, chassis: ChassisDriver, hz: float = 30.0, timeout_s: float = 1.0, lr_fix: bool = True) -> None:
        self.chassis = chassis
        self.hz = max(5.0, hz)
        self.timeout_s = max(0.2, timeout_s)
        self.lr_fix = lr_fix

        self._clients: dict[str, DriverClientState] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_evt = asyncio.Event()
        self._last_cmd = (None, None, None)
        self._active_client: str | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._run(), name="drive-coordinator")

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._send_cmd(0.0, 0.0, 0.0)

    async def join(self, client_id: str, username: str) -> None:
        async with self._lock:
            self._clients[client_id] = DriverClientState(username=username)

    async def leave(self, client_id: str) -> None:
        async with self._lock:
            client = self._clients.pop(client_id, None)
            if client is None:
                return

            if self._active_client == client.username:
                has_same_user = any(st.username == client.username for st in self._clients.values())
                if not has_same_user:
                    self._active_client = None

    async def set_active_client(self, client_id: str | None) -> None:
        async with self._lock:
            self._active_client = client_id
            for st in self._clients.values():
                if st.username != client_id:
                    st.keys.clear()

    async def update_event(self, client_id: str, payload: dict) -> None:
        msg_type = str(payload.get("type", "")).lower()

        async with self._lock:
            st = self._clients.get(client_id)
            if st is None:
                return
            st.last_seen = time.monotonic()

            if msg_type == "key":
                key = str(payload.get("key", "")).lower()
                is_down = bool(payload.get("isDown", False))
                if key in {"w", "a", "s", "d"}:
                    if is_down:
                        st.keys.add(key)
                    else:
                        st.keys.discard(key)

            elif msg_type == "stop":
                st.keys.clear()

            elif msg_type == "speed":
                val = float(payload.get("value", st.speed))
                st.speed = max(0.1, min(1.0, val))

            elif msg_type == "heartbeat":
                return

    async def _run(self) -> None:
        period = 1.0 / self.hz
        while not self._stop_evt.is_set():
            vx, vy, omega = await self._compute_cmd()
            await self._send_cmd(vx, vy, omega)
            await asyncio.sleep(period)

    async def _compute_cmd(self) -> tuple[float, float, float]:
        now = time.monotonic()

        active_keys: set[str] = set()
        speed = 0.0

        async with self._lock:
            stale_ids = [cid for cid, st in self._clients.items() if (now - st.last_seen) > self.timeout_s]
            for cid in stale_ids:
                self._clients[cid].keys.clear()

            if self._active_client is not None:
                for st in self._clients.values():
                    if st.username == self._active_client:
                        active_keys.update(st.keys)
                        speed = max(speed, st.speed)

        vx = 0.0
        vy = 0.0

        if "w" in active_keys:
            vy += speed
        if "s" in active_keys:
            vy -= speed

        if self.lr_fix:
            if "a" in active_keys:
                vx -= speed
            if "d" in active_keys:
                vx += speed
        else:
            if "a" in active_keys:
                vx += speed
            if "d" in active_keys:
                vx -= speed

        return _clamp_unit(vx), _clamp_unit(vy), 0.0

    async def _send_cmd(self, vx: float, vy: float, omega: float) -> None:
        cmd = (vx, vy, omega)
        if cmd == self._last_cmd:
            return

        try:
            self.chassis.set_velocity(vx, vy, omega)
        except ChassisError:
            LOGGER.exception("Failed to set chassis velocity")
            return

        self._last_cmd = cmd


@dataclass
class AppState:
    access: AccessManager
    chassis: ChassisDriver
    camera: MjpegCameraStream
    mic: PcmMicBroadcaster
    talk: TalkbackPcmPlayer
    tts: TTSEngine
    drive: DriveCoordinator


def create_app() -> FastAPI:
    app = FastAPI(title="Car Driver Remote Control", version="0.1.0")

    speaker_device = os.environ.get("SPEAKER_DEVICE", "plughw:CARD=Device,DEV=0")
    access = AccessManager(
        users_path=USERS_DB_PATH,
        audit_path=AUDIT_LOG_PATH,
        admin_username=os.environ.get("ADMIN_USERNAME", "pglzjz"),
        admin_password=os.environ.get("ADMIN_PASSWORD", "pglzjz"),
        session_timeout_s=float(os.environ.get("SESSION_TIMEOUT", "1200")),
    )

    camera_cfg = CameraConfig(
        device=os.environ.get("CAMERA_DEVICE", "/dev/video0"),
        width=int(os.environ.get("CAMERA_WIDTH", "640")),
        height=int(os.environ.get("CAMERA_HEIGHT", "480")),
        fps=int(os.environ.get("CAMERA_FPS", "25")),
        input_format=os.environ.get("CAMERA_INPUT_FORMAT", "mjpeg"),
    )

    mic_cfg = MicConfig(
        device=os.environ.get("MIC_DEVICE", "default"),
        sample_rate=int(os.environ.get("MIC_SAMPLE_RATE", "16000")),
        channels=int(os.environ.get("MIC_CHANNELS", "1")),
        chunk_ms=int(os.environ.get("MIC_CHUNK_MS", "100")),
    )

    talk_sample_rate = int(os.environ.get("TALK_SAMPLE_RATE", "16000"))
    talk_channels = int(os.environ.get("TALK_CHANNELS", str(mic_cfg.channels)))

    chassis = ChassisDriver.from_env(PROJECT_ROOT)
    camera = MjpegCameraStream(camera_cfg)
    mic = PcmMicBroadcaster(mic_cfg)
    talk = TalkbackPcmPlayer(
        sample_rate=talk_sample_rate,
        channels=talk_channels,
        output_device=speaker_device,
    )
    tts = TTSEngine(
        tts_key=os.environ.get("TTS_KEY"),
        app_id=os.environ.get("TTS_APP_ID"),
        resource_id=os.environ.get("TTS_RESOURCE_ID", "seed-tts-1.0"),
        speaker=os.environ.get("TTS_SPEAKER", "zh_female_cancan_mars_bigtts"),
        model=os.environ.get("TTS_MODEL"),
        sample_rate=int(os.environ.get("TTS_SAMPLE_RATE", "24000")),
        speech_rate=int(os.environ.get("TTS_SPEECH_RATE", "0")),
        loudness_rate=int(os.environ.get("TTS_LOUDNESS_RATE", "0")),
        ws_url=os.environ.get("TTS_WS_URL", "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"),
        timeout_s=float(os.environ.get("TTS_TIMEOUT", "20")),
        output_device=speaker_device,
    )
    drive = DriveCoordinator(
        chassis=chassis,
        hz=float(os.environ.get("CONTROL_HZ", "30")),
        timeout_s=float(os.environ.get("CONTROL_TIMEOUT", "1.0")),
        lr_fix=os.environ.get("CONTROL_LR_FIX", "1") == "1",
    )
    state = AppState(access=access, chassis=chassis, camera=camera, mic=mic, talk=talk, tts=tts, drive=drive)
    app.state.core = state

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    async def _sync_drive_owner() -> None:
        owner = await state.access.get_control_owner()
        await state.drive.set_active_client(owner)

    async def _get_session(request: Request, touch: bool = True) -> SessionRecord:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        sess = await state.access.get_session(token, touch=touch)
        if sess is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        return sess

    async def _get_ws_session(ws: WebSocket) -> tuple[str, SessionRecord] | None:
        token = ws.cookies.get(SESSION_COOKIE_NAME)
        sess = await state.access.get_session(token, touch=True)
        if not token or sess is None:
            await ws.accept()
            await ws.send_json({"type": "error", "message": "unauthorized"})
            await ws.close(code=4401)
            return None
        return token, sess

    async def _require_control_permission(request: Request) -> SessionRecord:
        sess = await _get_session(request)
        owner = await state.access.get_control_owner()
        if owner != sess.username:
            raise HTTPException(status_code=403, detail="no control permission")
        return sess

    def _set_session_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            secure=SESSION_COOKIE_SECURE,
            max_age=int(state.access.session_timeout_s),
            path="/",
        )

    def _clear_session_cookie(resp: Response) -> None:
        resp.delete_cookie(key=SESSION_COOKIE_NAME, path="/")

    def _raise_access_http(exc: AccessError) -> None:
        msg = str(exc)
        status = 400
        if "invalid username or password" in msg:
            status = 401
        elif "already online" in msg:
            status = 409
        elif "only admin" in msg or "no permission" in msg:
            status = 403
        elif "not found" in msg:
            status = 404
        raise HTTPException(status_code=status, detail=msg)

    @app.on_event("startup")
    async def _startup() -> None:
        LOGGER.info("Starting car driver service")
        LOGGER.info("Audio output device: %s", speaker_device)
        if os.geteuid() != 0 and os.environ.get("CHASSIS_DRY_RUN", "0") != "1":
            LOGGER.warning("Not running as root; GPIO access may fail")

        state.chassis.open(log_level=int(os.environ.get("CHASSIS_LOG_LEVEL", "1")))
        state.camera.start()
        state.mic.start(asyncio.get_running_loop())
        await state.drive.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        LOGGER.info("Stopping car driver service")
        await state.drive.stop()
        state.mic.stop()
        state.camera.stop()
        state.talk.close()
        state.chassis.close()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "service": "car-driver", "timestamp": time.time()})

    @app.post("/api/auth/register")
    async def api_auth_register(req: AuthRequest) -> JSONResponse:
        try:
            await state.access.register_user(req.username, req.password)
        except AccessError as exc:
            _raise_access_http(exc)
        return JSONResponse({"ok": True})

    @app.post("/api/auth/login")
    async def api_auth_login(req: AuthRequest) -> JSONResponse:
        try:
            sess = await state.access.login(req.username, req.password)
        except AccessError as exc:
            _raise_access_http(exc)

        payload = {
            "ok": True,
            "user": {
                "username": sess.username,
                "role": sess.role,
            },
        }
        resp = JSONResponse(payload)
        _set_session_cookie(resp, sess.token)
        return resp

    @app.post("/api/auth/logout")
    async def api_auth_logout(request: Request) -> JSONResponse:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            await state.access.logout(token, reason="logout")
            await _sync_drive_owner()

        resp = JSONResponse({"ok": True})
        _clear_session_cookie(resp)
        return resp

    @app.get("/api/auth/me")
    async def api_auth_me(request: Request) -> JSONResponse:
        sess = await _get_session(request)
        snapshot = await state.access.get_snapshot(sess)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, **snapshot})

    @app.get("/api/state")
    async def api_state(request: Request) -> JSONResponse:
        sess = await _get_session(request)
        snapshot = await state.access.get_snapshot(sess)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, **snapshot})

    @app.post("/api/control/acquire")
    async def api_control_acquire(req: ControlAcquireRequest, request: Request) -> JSONResponse:
        sess = await _get_session(request)
        try:
            prev = await state.access.acquire_control(sess, force=req.force)
        except AccessError as exc:
            _raise_access_http(exc)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, "owner": sess.username, "previous": prev})

    @app.post("/api/control/release")
    async def api_control_release(request: Request) -> JSONResponse:
        sess = await _get_session(request)
        try:
            prev = await state.access.release_control(sess)
        except AccessError as exc:
            _raise_access_http(exc)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, "previous": prev, "owner": None})

    @app.post("/api/control/request")
    async def api_control_request(req: ControlRequestCreate, request: Request) -> JSONResponse:
        sess = await _get_session(request)
        try:
            result = await state.access.request_control(sess, req.targetUser)
        except AccessError as exc:
            _raise_access_http(exc)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, **result})

    @app.post("/api/control/respond")
    async def api_control_respond(req: ControlRequestDecision, request: Request) -> JSONResponse:
        sess = await _get_session(request)
        try:
            owner = await state.access.respond_request(sess, req.requestId, req.approve)
        except AccessError as exc:
            _raise_access_http(exc)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, "owner": owner})

    @app.post("/api/admin/kick")
    async def api_admin_kick(req: KickRequest, request: Request) -> JSONResponse:
        sess = await _get_session(request)
        try:
            kicked = await state.access.kick_user(sess, req.username)
        except AccessError as exc:
            _raise_access_http(exc)
        await _sync_drive_owner()
        return JSONResponse({"ok": True, "kicked": kicked})

    @app.post("/api/tts")
    async def api_tts(req: TTSRequest, request: Request) -> JSONResponse:
        await _require_control_permission(request)
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is empty")

        # Release talkback stream before TTS to avoid speaker device conflict.
        state.talk.suspend()
        try:
            await asyncio.to_thread(state.tts.speak, text)
        except TTSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=500, detail=f"tts process failed: {exc}") from exc

        return JSONResponse({"ok": True})

    @app.get("/video.mjpg")
    async def video_mjpeg(request: Request) -> StreamingResponse:
        await _get_session(request)

        async def _iter_mjpeg() -> AsyncGenerator[bytes, None]:
            boundary = b"--frame"
            while True:
                frame = state.camera.latest_frame()
                if frame is None:
                    await asyncio.sleep(0.03)
                    continue

                header = (
                    boundary
                    + b"\r\n"
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                )
                yield header + frame + b"\r\n"
                await asyncio.sleep(0.03)

        return StreamingResponse(
            _iter_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @app.websocket("/ws/control")
    async def ws_control(ws: WebSocket) -> None:
        auth = await _get_ws_session(ws)
        if auth is None:
            return

        token, sess = auth
        await ws.accept()
        client_id = token
        await state.drive.join(client_id, sess.username)
        await _sync_drive_owner()

        try:
            owner = await state.access.get_control_owner()
            await ws.send_json(
                {
                    "type": "welcome",
                    "clientId": sess.username,
                    "username": sess.username,
                    "role": sess.role,
                    "owner": owner,
                    "canControl": owner == sess.username,
                }
            )
            while True:
                if await state.access.get_session(token, touch=True) is None:
                    await ws.send_json({"type": "error", "message": "session expired"})
                    await ws.close(code=4401)
                    break

                data = await ws.receive_text()
                payload = json.loads(data)
                msg_type = str(payload.get("type", "")).lower()
                owner = await state.access.get_control_owner()
                if owner != sess.username and msg_type in {"key", "stop", "speed"}:
                    await ws.send_json({"type": "error", "message": "no control permission"})
                    continue

                await state.drive.update_event(client_id, payload)
        except WebSocketDisconnect:
            pass
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "message": "invalid JSON"})
        finally:
            await state.drive.leave(client_id)

    @app.websocket("/ws/audio-out")
    async def ws_audio_out(ws: WebSocket) -> None:
        auth = await _get_ws_session(ws)
        if auth is None:
            return

        token, _ = auth
        await ws.accept()
        queue = state.mic.subscribe()
        next_touch = time.monotonic() + 1.0

        try:
            await ws.send_json(
                {
                    "type": "config",
                    "sampleRate": mic_cfg.sample_rate,
                    "channels": mic_cfg.channels,
                    "sampleFormat": "s16le",
                    "device": mic_cfg.device,
                }
            )
            while True:
                now = time.monotonic()
                if now >= next_touch:
                    if await state.access.get_session(token, touch=True) is None:
                        await ws.send_json({"type": "error", "message": "session expired"})
                        await ws.close(code=4401)
                        break
                    next_touch = now + 1.0

                chunk = await queue.get()
                await ws.send_bytes(chunk)
        except WebSocketDisconnect:
            pass
        finally:
            state.mic.unsubscribe(queue)

    @app.websocket("/ws/talk")
    async def ws_talk(ws: WebSocket) -> None:
        auth = await _get_ws_session(ws)
        if auth is None:
            return

        token, sess = auth
        await ws.accept()

        owner = await state.access.get_control_owner()
        if owner != sess.username:
            await ws.send_json({"type": "error", "message": "no control permission"})
            await ws.close(code=4403)
            return

        await ws.send_json(
            {
                "type": "config",
                "sampleRate": talk_sample_rate,
                "channels": talk_channels,
                "sampleFormat": "s16le",
            }
        )

        try:
            while True:
                if await state.access.get_session(token, touch=True) is None:
                    await ws.send_json({"type": "error", "message": "session expired"})
                    await ws.close(code=4401)
                    break

                owner = await state.access.get_control_owner()
                if owner != sess.username:
                    await ws.send_json({"type": "error", "message": "no control permission"})
                    await ws.close(code=4403)
                    break

                message = await ws.receive()
                if message.get("type") == "websocket.disconnect":
                    break

                data = message.get("bytes")
                text = message.get("text")

                if data:
                    await asyncio.to_thread(state.talk.push_pcm, data)
                    continue

                if text:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") == "stop":
                        await asyncio.to_thread(state.talk.suspend)
        except WebSocketDisconnect:
            pass
        finally:
            await asyncio.to_thread(state.talk.suspend)

    return app


def _clamp_unit(v: float) -> float:
    return max(-1.0, min(1.0, float(v)))


app = create_app()
