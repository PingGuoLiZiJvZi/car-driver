from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class UserRecord:
    username: str
    password: str
    role: str
    created_at: float


@dataclass
class SessionRecord:
    token: str
    username: str
    role: str
    created_at: float
    last_seen: float


@dataclass
class ControlRequestRecord:
    request_id: str
    from_user: str
    to_user: str
    created_at: float


class AccessError(RuntimeError):
    pass


class AccessManager:
    def __init__(
        self,
        users_path: Path,
        audit_path: Path,
        admin_username: str,
        admin_password: str,
        session_timeout_s: float = 120.0,
    ) -> None:
        self.users_path = users_path
        self.audit_path = audit_path
        self.session_timeout_s = max(30.0, float(session_timeout_s))

        self._lock = asyncio.Lock()
        self.users: dict[str, UserRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.user_to_token: dict[str, set[str]] = {}
        self.control_owner: str | None = None
        self.pending_requests: dict[str, ControlRequestRecord] = {}
        self.audit_events: list[dict[str, Any]] = []

        self._load_users()
        self._ensure_admin(admin_username, admin_password)

    @staticmethod
    def _format_ts(ts: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

    def _load_users(self) -> None:
        if not self.users_path.exists():
            self.users = {}
            return

        raw = json.loads(self.users_path.read_text(encoding="utf-8"))
        users: dict[str, UserRecord] = {}
        for item in raw:
            rec = UserRecord(
                username=str(item.get("username", "")).strip(),
                password=str(item.get("password", "")),
                role=str(item.get("role", "user")),
                created_at=float(item.get("created_at", time.time())),
            )
            if rec.username:
                users[rec.username] = rec
        self.users = users

    def _save_users(self) -> None:
        self.users_path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for rec in sorted(self.users.values(), key=lambda x: x.username):
            item = asdict(rec)
            item["created_at_text"] = self._format_ts(rec.created_at)
            rows.append(item)
        self.users_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ensure_admin(self, admin_username: str, admin_password: str) -> None:
        admin_name = admin_username.strip() or "admin"
        admin_pwd = admin_password or "admin123"
        rec = self.users.get(admin_name)

        if rec is None:
            self.users[admin_name] = UserRecord(
                username=admin_name,
                password=admin_pwd,
                role="admin",
                created_at=time.time(),
            )
            self._save_users()
            return

        if rec.role != "admin":
            rec.role = "admin"
            self._save_users()

    def _append_audit(self, event: str, actor: str, details: dict[str, Any] | None = None) -> None:
        now = time.time()
        rec = {
            "ts": now,
            "ts_text": self._format_ts(now),
            "event": event,
            "actor": actor,
            "details": details or {},
        }
        self.audit_events.append(rec)
        if len(self.audit_events) > 2000:
            self.audit_events = self.audit_events[-2000:]

        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _cleanup_expired_locked(self) -> None:
        now = time.time()
        expired = [token for token, sess in self.sessions.items() if (now - sess.last_seen) > self.session_timeout_s]
        for token in expired:
            self._logout_locked(token, reason="timeout")

    def _logout_locked(self, token: str, reason: str) -> SessionRecord | None:
        sess = self.sessions.pop(token, None)
        if sess is None:
            return None

        tokens = self.user_to_token.get(sess.username)
        if tokens is not None:
            tokens.discard(token)
            if not tokens:
                self.user_to_token.pop(sess.username, None)

        user_still_online = bool(self.user_to_token.get(sess.username))

        if (not user_still_online) and self.control_owner == sess.username:
            self.control_owner = None
            self._append_audit("control_release", sess.username, {"reason": reason})

        if not user_still_online:
            for rid, req in list(self.pending_requests.items()):
                if req.from_user == sess.username or req.to_user == sess.username:
                    del self.pending_requests[rid]

            self._append_audit("offline", sess.username, {"reason": reason})

        return sess

    async def register_user(self, username: str, password: str) -> None:
        uname = username.strip()
        if not uname:
            raise AccessError("username is empty")
        if not password:
            raise AccessError("password is empty")
        if len(uname) > 32:
            raise AccessError("username too long")

        async with self._lock:
            if uname in self.users:
                raise AccessError("username already exists")

            self.users[uname] = UserRecord(
                username=uname,
                password=password,
                role="user",
                created_at=time.time(),
            )
            self._save_users()
            self._append_audit("register", uname, {"role": "user"})

    async def login(self, username: str, password: str) -> SessionRecord:
        uname = username.strip()
        async with self._lock:
            self._cleanup_expired_locked()
            user = self.users.get(uname)
            if user is None or user.password != password:
                raise AccessError("invalid username or password")

            was_first_login = not bool(self.user_to_token.get(uname))

            now = time.time()
            token = secrets.token_urlsafe(32)
            sess = SessionRecord(
                token=token,
                username=user.username,
                role=user.role,
                created_at=now,
                last_seen=now,
            )
            self.sessions[token] = sess
            self.user_to_token.setdefault(user.username, set()).add(token)
            if was_first_login:
                self._append_audit("online", user.username, {"role": user.role})
            else:
                self._append_audit(
                    "online_session",
                    user.username,
                    {"role": user.role, "sessions": len(self.user_to_token[user.username])},
                )

            if self.control_owner is None:
                self.control_owner = user.username
                self._append_audit("control_acquire", user.username, {"reason": "auto-on-login"})
            return sess

    async def logout(self, token: str, reason: str = "logout") -> SessionRecord | None:
        async with self._lock:
            self._cleanup_expired_locked()
            return self._logout_locked(token, reason)

    async def get_session(self, token: str | None, touch: bool = True) -> SessionRecord | None:
        if not token:
            return None

        async with self._lock:
            self._cleanup_expired_locked()
            sess = self.sessions.get(token)
            if sess is None:
                return None
            if touch:
                sess.last_seen = time.time()
            return sess

    async def acquire_control(self, sess: SessionRecord, force: bool = False) -> str | None:
        async with self._lock:
            self._cleanup_expired_locked()

            if force:
                if sess.role != "admin":
                    raise AccessError("only admin can force acquire control")
                prev = self.control_owner
                self.control_owner = sess.username
                self.pending_requests.clear()
                self._append_audit("control_force_take", sess.username, {"from": prev})
                return prev

            if self.control_owner and self.control_owner != sess.username:
                raise AccessError(f"control is occupied by {self.control_owner}")

            prev = self.control_owner
            self.control_owner = sess.username
            if prev != sess.username:
                self._append_audit("control_acquire", sess.username, {"from": prev})
            return prev

    async def release_control(self, sess: SessionRecord) -> str | None:
        async with self._lock:
            self._cleanup_expired_locked()
            if self.control_owner is None:
                return None

            if sess.role != "admin" and self.control_owner != sess.username:
                raise AccessError("you do not own control")

            prev = self.control_owner
            self.control_owner = None
            self._append_audit("control_release", sess.username, {"from": prev})
            return prev

    async def request_control(self, sess: SessionRecord, target_user: str | None) -> dict[str, Any]:
        async with self._lock:
            self._cleanup_expired_locked()

            requester = sess.username
            if self.control_owner is None:
                self.control_owner = requester
                self._append_audit("control_acquire", requester, {"reason": "no-owner"})
                return {"granted": True, "owner": requester}

            if self.control_owner == requester:
                return {"granted": True, "owner": requester}

            target = (target_user or self.control_owner).strip()
            if not target:
                raise AccessError("target user is empty")
            if target == requester:
                raise AccessError("cannot request from yourself")
            if not self.user_to_token.get(target):
                raise AccessError("target user is not online")

            for req in self.pending_requests.values():
                if req.from_user == requester and req.to_user == target:
                    return {
                        "granted": False,
                        "requestId": req.request_id,
                        "to": req.to_user,
                    }

            req_id = uuid.uuid4().hex
            req = ControlRequestRecord(
                request_id=req_id,
                from_user=requester,
                to_user=target,
                created_at=time.time(),
            )
            self.pending_requests[req_id] = req
            self._append_audit("control_request", requester, {"to": target, "requestId": req_id})
            return {"granted": False, "requestId": req_id, "to": target}

    async def respond_request(self, sess: SessionRecord, request_id: str, approve: bool) -> str | None:
        async with self._lock:
            self._cleanup_expired_locked()
            req = self.pending_requests.get(request_id)
            if req is None:
                raise AccessError("request not found")

            if sess.role != "admin" and sess.username != req.to_user and sess.username != self.control_owner:
                raise AccessError("no permission to process request")

            del self.pending_requests[request_id]

            if not approve:
                self._append_audit(
                    "control_request_rejected",
                    sess.username,
                    {"requestId": request_id, "fromUser": req.from_user, "toUser": req.to_user},
                )
                return self.control_owner

            prev = self.control_owner
            self.control_owner = req.from_user
            self.pending_requests.clear()
            self._append_audit(
                "control_request_approved",
                sess.username,
                {"requestId": request_id, "from": prev, "to": req.from_user},
            )
            return self.control_owner

    async def kick_user(self, sess: SessionRecord, target_username: str) -> bool:
        async with self._lock:
            self._cleanup_expired_locked()
            if sess.role != "admin":
                raise AccessError("only admin can kick user")

            target = self.users.get(target_username)
            if target is None:
                raise AccessError("user not found")
            if target.role != "user":
                raise AccessError("admin account cannot be kicked")

            tokens = list(self.user_to_token.get(target_username) or [])
            if not tokens:
                return False

            self._append_audit("kick_user", sess.username, {"target": target_username})
            for token in tokens:
                self._logout_locked(token, reason=f"kicked-by-{sess.username}")
            return True

    async def get_control_owner(self) -> str | None:
        async with self._lock:
            self._cleanup_expired_locked()
            return self.control_owner

    async def get_snapshot(self, sess: SessionRecord, audit_limit: int = 100) -> dict[str, Any]:
        async with self._lock:
            self._cleanup_expired_locked()

            user_rows: dict[str, dict[str, Any]] = {}
            for s in self.sessions.values():
                row = user_rows.get(s.username)
                if row is None:
                    user_rows[s.username] = {
                        "username": s.username,
                        "role": s.role,
                        "onlineSince": s.created_at,
                        "lastSeen": s.last_seen,
                        "sessionCount": 1,
                        "isController": s.username == self.control_owner,
                    }
                    continue

                row["onlineSince"] = min(float(row["onlineSince"]), s.created_at)
                row["lastSeen"] = max(float(row["lastSeen"]), s.last_seen)
                row["sessionCount"] = int(row["sessionCount"]) + 1

            users = [user_rows[username] for username in sorted(user_rows)]

            pending = []
            for req in sorted(self.pending_requests.values(), key=lambda r: r.created_at):
                if sess.role == "admin" or req.from_user == sess.username or req.to_user == sess.username:
                    pending.append(
                        {
                            "requestId": req.request_id,
                            "fromUser": req.from_user,
                            "toUser": req.to_user,
                            "createdAt": req.created_at,
                        }
                    )

            _ = audit_limit

            return {
                "user": {
                    "username": sess.username,
                    "role": sess.role,
                },
                "controlOwner": self.control_owner,
                "canControl": self.control_owner == sess.username,
                "onlineUsers": users,
                "pendingRequests": pending,
            }
