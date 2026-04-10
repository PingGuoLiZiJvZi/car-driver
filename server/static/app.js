const state = {
  controlWs: null,
  audioWs: null,
  talkWs: null,
  pressedKeys: new Set(),
  speed: 0.6,
  heartbeatTimer: null,
  loggedIn: false,
  username: "",
  role: "",
  controlOwner: null,
  canControl: false,
  onlineUsers: [],
  pendingRequests: [],
  auditEvents: [],
  statePollTimer: null,

  monitorCtx: null,
  monitorGain: null,
  monitorPlaybackTime: 0,
  monitorConfig: { sampleRate: 16000, channels: 1 },
  monitorVolume: 1.2,
  monitorLastVolume: 1.2,
  monitorMuted: false,
  monitorRxBytes: 0,
  monitorRxTimer: null,

  talkCtx: null,
  talkStream: null,
  talkSource: null,
  talkProcessor: null,
  talking: false,
};

const MONITOR_VOLUME_STORAGE_KEY = "car-driver.monitor-volume";

const statusText = document.getElementById("statusText");
const logBox = document.getElementById("logBox");
const speedRange = document.getElementById("speedRange");
const speedValue = document.getElementById("speedValue");
const btnStop = document.getElementById("btnStop");
const btnAudio = document.getElementById("btnAudio");
const audioState = document.getElementById("audioState");
const audioRx = document.getElementById("audioRx");
const monitorVolume = document.getElementById("monitorVolume");
const monitorVolumeValue = document.getElementById("monitorVolumeValue");
const btnAudioMute = document.getElementById("btnAudioMute");
const btnTalk = document.getElementById("btnTalk");
const btnTts = document.getElementById("btnTts");
const ttsInput = document.getElementById("ttsInput");
const videoFeed = document.getElementById("videoFeed");

const currentUser = document.getElementById("currentUser");
const btnLogout = document.getElementById("btnLogout");
const authUsername = document.getElementById("authUsername");
const authPassword = document.getElementById("authPassword");
const btnLogin = document.getElementById("btnLogin");
const btnRegister = document.getElementById("btnRegister");

const controlOwnerText = document.getElementById("controlOwnerText");
const requestTarget = document.getElementById("requestTarget");
const btnRequestControl = document.getElementById("btnRequestControl");
const btnForceControl = document.getElementById("btnForceControl");
const btnReleaseControl = document.getElementById("btnReleaseControl");
const onlineUsers = document.getElementById("onlineUsers");
const pendingRequests = document.getElementById("pendingRequests");
const auditEvents = document.getElementById("auditEvents");

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function loadSavedMonitorVolume() {
  const raw = Number(localStorage.getItem(MONITOR_VOLUME_STORAGE_KEY));
  if (Number.isFinite(raw)) {
    return clamp(raw, 0, 3);
  }
  return 1.2;
}

function saveMonitorVolume(value) {
  localStorage.setItem(MONITOR_VOLUME_STORAGE_KEY, String(value));
}

function updateMonitorVolumeUi() {
  if (!monitorVolume || !monitorVolumeValue) {
    return;
  }
  monitorVolume.value = String(state.monitorVolume);
  const pct = Math.round(state.monitorVolume * 100);
  monitorVolumeValue.textContent = state.monitorMuted ? `${pct}% (静音)` : `${pct}%`;
}

function applyMonitorGain() {
  if (!state.monitorGain || !state.monitorCtx) {
    return;
  }
  state.monitorGain.gain.setTargetAtTime(state.monitorMuted ? 0 : state.monitorVolume, state.monitorCtx.currentTime, 0.02);
}

function resetMonitorRx() {
  state.monitorRxBytes = 0;
  if (audioRx) {
    audioRx.textContent = "下行速率：0 KB/s";
  }
}

function startMonitorRxTicker() {
  if (state.monitorRxTimer) {
    clearInterval(state.monitorRxTimer);
  }
  state.monitorRxTimer = setInterval(() => {
    if (!audioRx) {
      return;
    }
    const kbps = state.monitorRxBytes / 1024;
    audioRx.textContent = `下行速率：${kbps.toFixed(1)} KB/s`;
    state.monitorRxBytes = 0;
  }, 1000);
}

function stopMonitorRxTicker() {
  if (state.monitorRxTimer) {
    clearInterval(state.monitorRxTimer);
    state.monitorRxTimer = null;
  }
  resetMonitorRx();
}

function wsUrl(path) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}${path}`;
}

function setStatus(text, ok = true) {
  statusText.textContent = text;
  statusText.style.color = ok ? "#167f53" : "#ac2a2a";
}

function appendLog(message) {
  const ts = new Date().toLocaleTimeString();
  logBox.textContent = `[${ts}] ${message}\n` + logBox.textContent;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function apiFetchJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }

  if (!response.ok) {
    const message = payload?.detail || payload?.message || `${response.status}`;
    const err = new Error(String(message));
    err.status = response.status;
    throw err;
  }

  return payload;
}

function stopStatePolling() {
  if (state.statePollTimer) {
    clearInterval(state.statePollTimer);
    state.statePollTimer = null;
  }
}

function setControlPermission(canControl) {
  state.canControl = Boolean(canControl);

  const controlDisabled = !state.loggedIn || !state.canControl;
  speedRange.disabled = controlDisabled;
  btnStop.disabled = controlDisabled;
  btnTalk.disabled = controlDisabled;
  btnTts.disabled = controlDisabled;
  ttsInput.disabled = !state.loggedIn;

  document.querySelectorAll(".ctrl[data-key]").forEach((btn) => {
    btn.disabled = controlDisabled;
  });

  btnReleaseControl.disabled = !state.loggedIn || !state.canControl;
  btnRequestControl.disabled = !state.loggedIn;
  btnForceControl.disabled = !state.loggedIn || state.role !== "admin";

  if (!state.canControl) {
    stopAll();
  }
}

function applyLoginUi() {
  currentUser.textContent = state.loggedIn ? `${state.username} (${state.role})` : "访客";
  btnLogout.disabled = !state.loggedIn;
  btnLogin.disabled = state.loggedIn;
  btnRegister.disabled = state.loggedIn;
  authUsername.disabled = state.loggedIn;
  authPassword.disabled = state.loggedIn;

  btnAudio.disabled = !state.loggedIn;
  btnAudioMute.disabled = !state.loggedIn;
  monitorVolume.disabled = !state.loggedIn;
  requestTarget.disabled = !state.loggedIn;

  if (!state.loggedIn) {
    setStatus("未登录", false);
    audioState.textContent = "音频监听：未开启";
    btnAudio.textContent = "开启监听音频";
    if (videoFeed) {
      videoFeed.src = "";
    }
  } else if (videoFeed) {
    const streamSrc = videoFeed.dataset.src || "/video.mjpg";
    const currentSrcAttr = videoFeed.getAttribute("src") || "";
    if (currentSrcAttr !== streamSrc) {
      videoFeed.src = streamSrc;
    }
  }
}

function applySnapshot(snapshot) {
  if (!snapshot || !snapshot.user) {
    return;
  }
  state.loggedIn = true;
  state.username = snapshot.user.username || "";
  state.role = snapshot.user.role || "user";
  state.controlOwner = snapshot.controlOwner || null;
  state.onlineUsers = Array.isArray(snapshot.onlineUsers) ? snapshot.onlineUsers : [];
  state.pendingRequests = Array.isArray(snapshot.pendingRequests) ? snapshot.pendingRequests : [];
  state.auditEvents = Array.isArray(snapshot.auditEvents) ? snapshot.auditEvents : [];
  applyLoginUi();
  setControlPermission(Boolean(snapshot.canControl));
  renderAccessPanels();
}

function resetAuthState(reasonText) {
  state.loggedIn = false;
  state.username = "";
  state.role = "";
  state.controlOwner = null;
  state.onlineUsers = [];
  state.pendingRequests = [];
  state.auditEvents = [];
  stopStatePolling();
  closeRealtimeChannels();
  setControlPermission(false);
  applyLoginUi();
  renderAccessPanels();
  authPassword.value = "";
  if (reasonText) {
    appendLog(reasonText);
  }
}

async function refreshState(silent = false) {
  if (!state.loggedIn) {
    return;
  }
  try {
    const snapshot = await apiFetchJson("/api/state", { method: "GET" });
    applySnapshot(snapshot);
  } catch (err) {
    if (err.status === 401) {
      resetAuthState("登录状态失效，请重新登录");
      return;
    }
    if (!silent) {
      appendLog(`状态刷新失败: ${String(err.message || err)}`);
    }
  }
}

function startStatePolling() {
  stopStatePolling();
  state.statePollTimer = setInterval(() => {
    refreshState(true);
  }, 2000);
}

function formatDateTime(ts) {
  const value = Number(ts);
  if (!Number.isFinite(value) || value <= 0) {
    return "-";
  }
  return new Date(value * 1000).toLocaleString();
}

function describeAuditEvent(item) {
  const actor = item?.actor || "unknown";
  const details = item?.details || {};

  switch (item?.event) {
    case "register":
      return `${actor} 注册账号`;
    case "online":
      return `${actor} 上线`;
    case "offline":
      return `${actor} 下线`;
    case "control_acquire":
      return `${actor} 获取控制权`;
    case "control_release":
      return `${actor} 释放控制权`;
    case "control_force_take":
      return `${actor} 强制接管控制权`;
    case "control_request":
      return `${actor} 向 ${details.to || "unknown"} 发起控制权申请`;
    case "control_request_approved":
      return `${actor} 通过控制权申请，转交给 ${details.to || "unknown"}`;
    case "control_request_rejected":
      return `${actor} 拒绝控制权申请`;
    case "kick_user":
      return `${actor} 踢出 ${details.target || "unknown"}`;
    default:
      return `${actor} 执行 ${item?.event || "unknown"}`;
  }
}

function renderAccessPanels() {
  const ownerText = state.controlOwner ? state.controlOwner : "无";
  controlOwnerText.textContent = `当前控制者：${ownerText}`;

  if (!state.loggedIn) {
    onlineUsers.innerHTML = '<div class="list-empty">请先登录后查看</div>';
    pendingRequests.innerHTML = '<div class="list-empty">请先登录后查看</div>';
    auditEvents.innerHTML = '<div class="list-empty">请先登录后查看</div>';
    return;
  }

  if (!state.onlineUsers.length) {
    onlineUsers.innerHTML = '<div class="list-empty">暂无在线用户</div>';
  } else {
    onlineUsers.innerHTML = state.onlineUsers
      .map((user) => {
        const roleTag =
          user.role === "admin" ? '<span class="tag admin">管理员</span>' : '<span class="tag">用户</span>';
        const ownerTag = user.isController ? '<span class="tag owner">控制者</span>' : "";
        const ownTag = user.username === state.username ? '<span class="tag">我</span>' : "";

        let actions = "";
        if (
          !state.canControl &&
          state.controlOwner &&
          user.username === state.controlOwner &&
          user.username !== state.username
        ) {
          actions += `<button class="btn mini" data-action="ask" data-user="${escapeHtml(user.username)}">向他申请</button>`;
        }
        if (state.role === "admin" && user.role === "user" && user.username !== state.username) {
          actions += `<button class="btn mini danger" data-action="kick" data-user="${escapeHtml(user.username)}">踢出</button>`;
        }

        return `
          <div class="list-row">
            <div class="row-main">
              <strong>${escapeHtml(user.username)}</strong>
              ${roleTag}${ownerTag}${ownTag}
              <span class="audit-time">在线于 ${escapeHtml(formatDateTime(user.onlineSince))}</span>
            </div>
            <div class="row-actions">${actions}</div>
          </div>
        `;
      })
      .join("");
  }

  if (!state.pendingRequests.length) {
    pendingRequests.innerHTML = '<div class="list-empty">暂无待处理申请</div>';
  } else {
    pendingRequests.innerHTML = state.pendingRequests
      .map((req) => {
        const canRespond =
          state.role === "admin" || req.toUser === state.username || state.controlOwner === state.username;
        const actions = canRespond
          ? `
            <button class="btn mini" data-action="approve" data-request-id="${escapeHtml(req.requestId)}">通过</button>
            <button class="btn mini ghost" data-action="reject" data-request-id="${escapeHtml(req.requestId)}">拒绝</button>
          `
          : '<span class="audit-time">等待处理</span>';

        return `
          <div class="list-row">
            <div class="row-main">
              <strong>${escapeHtml(req.fromUser)}</strong>
              <span>-></span>
              <strong>${escapeHtml(req.toUser)}</strong>
              <span class="audit-time">${escapeHtml(formatDateTime(req.createdAt))}</span>
            </div>
            <div class="row-actions">${actions}</div>
          </div>
        `;
      })
      .join("");
  }

  const events = state.auditEvents.slice(-40).reverse();
  if (!events.length) {
    auditEvents.innerHTML = '<div class="list-empty">日志仅保存在车端本地（server/audit.log）</div>';
    return;
  }
  auditEvents.innerHTML = '<div class="list-empty">日志仅保存在车端本地（server/audit.log）</div>';
}

function closeSocketSafely(ws) {
  if (!ws) {
    return;
  }
  try {
    ws.close();
  } catch {
    // ignore close errors
  }
}

function closeRealtimeChannels() {
  if (state.heartbeatTimer) {
    clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = null;
  }

  if (state.controlWs) {
    const ws = state.controlWs;
    state.controlWs = null;
    ws.onclose = null;
    closeSocketSafely(ws);
  }

  if (state.audioWs) {
    const ws = state.audioWs;
    state.audioWs = null;
    ws.onclose = null;
    closeSocketSafely(ws);
  }
  stopMonitorRxTicker();
  audioState.textContent = "音频监听：未开启";
  btnAudio.textContent = "开启监听音频";

  if (state.talkWs) {
    const ws = state.talkWs;
    state.talkWs = null;
    ws.onclose = null;
    closeSocketSafely(ws);
  }
  state.talking = false;
  btnTalk.classList.remove("active");
  btnTalk.textContent = "按住说话";

  state.pressedKeys.clear();
  refreshKeyUi();
}

function handleApiError(err, prefix) {
  if (err?.status === 401) {
    resetAuthState("登录状态失效，请重新登录");
    return;
  }
  appendLog(`${prefix}: ${String(err?.message || err)}`);
}

async function loginWithPassword() {
  const username = authUsername.value.trim();
  const password = authPassword.value;
  if (!username || !password) {
    appendLog("请输入用户名和密码");
    return;
  }

  try {
    const payload = await apiFetchJson("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });

    state.loggedIn = true;
    state.username = payload?.user?.username || username;
    state.role = payload?.user?.role || "user";
    applyLoginUi();
    setControlPermission(false);
    renderAccessPanels();
    connectControl();
    await refreshState();
    startStatePolling();
    authPassword.value = "";
    appendLog(`登录成功: ${state.username}`);
  } catch (err) {
    handleApiError(err, "登录失败");
  }
}

async function registerWithPassword() {
  const username = authUsername.value.trim();
  const password = authPassword.value;
  if (!username || !password) {
    appendLog("请输入用户名和密码");
    return;
  }

  try {
    await apiFetchJson("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    appendLog(`注册成功: ${username}，请登录`);
  } catch (err) {
    handleApiError(err, "注册失败");
  }
}

async function logoutCurrentUser() {
  try {
    await apiFetchJson("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (err) {
    if (err?.status !== 401) {
      appendLog(`退出请求失败: ${String(err?.message || err)}`);
    }
  }
  resetAuthState("已退出登录");
}

async function requestControl(targetUser) {
  try {
    const result = await apiFetchJson("/api/control/request", {
      method: "POST",
      body: JSON.stringify({ targetUser: targetUser || null }),
    });
    if (result.granted) {
      appendLog("已获得控制权");
    } else {
      appendLog(`已向 ${result.to || targetUser || "控制者"} 发起申请`);
    }
    await refreshState();
    connectControl();
  } catch (err) {
    handleApiError(err, "申请控制权失败");
  }
}

async function acquireControl(force) {
  try {
    const result = await apiFetchJson("/api/control/acquire", {
      method: "POST",
      body: JSON.stringify({ force: Boolean(force) }),
    });
    appendLog(`控制权已切换到 ${result.owner}`);
    await refreshState();
    connectControl();
  } catch (err) {
    handleApiError(err, force ? "强制接管失败" : "获取控制权失败");
  }
}

async function releaseControl() {
  try {
    await apiFetchJson("/api/control/release", { method: "POST", body: "{}" });
    appendLog("已释放控制权");
    await refreshState();
  } catch (err) {
    handleApiError(err, "释放控制权失败");
  }
}

async function respondControlRequest(requestId, approve) {
  try {
    await apiFetchJson("/api/control/respond", {
      method: "POST",
      body: JSON.stringify({ requestId, approve: Boolean(approve) }),
    });
    appendLog(approve ? "已通过申请" : "已拒绝申请");
    await refreshState();
  } catch (err) {
    handleApiError(err, "处理申请失败");
  }
}

async function kickOnlineUser(username) {
  try {
    await apiFetchJson("/api/admin/kick", {
      method: "POST",
      body: JSON.stringify({ username }),
    });
    appendLog(`已踢出用户: ${username}`);
    await refreshState();
  } catch (err) {
    handleApiError(err, "踢人失败");
  }
}

function initAuthControls() {
  btnLogin.addEventListener("click", () => {
    loginWithPassword();
  });

  btnRegister.addEventListener("click", () => {
    registerWithPassword();
  });

  btnLogout.addEventListener("click", () => {
    logoutCurrentUser();
  });

  authPassword.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loginWithPassword();
    }
  });
}

function initAccessPanelActions() {
  btnRequestControl.addEventListener("click", () => {
    if (!state.loggedIn) {
      appendLog("请先登录后再申请控制权");
      return;
    }
    requestControl(requestTarget.value.trim());
  });

  btnForceControl.addEventListener("click", () => {
    if (!state.loggedIn) {
      appendLog("请先登录后再操作");
      return;
    }
    acquireControl(true);
  });

  btnReleaseControl.addEventListener("click", () => {
    if (!state.loggedIn) {
      appendLog("请先登录后再操作");
      return;
    }
    releaseControl();
  });

  onlineUsers.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const action = target.dataset.action;
    const username = target.dataset.user;
    if (!action || !username) {
      return;
    }

    if (action === "ask") {
      requestTarget.value = username;
      requestControl(username);
    } else if (action === "kick") {
      kickOnlineUser(username);
    }
  });

  pendingRequests.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const action = target.dataset.action;
    const requestId = target.dataset.requestId;
    if (!action || !requestId) {
      return;
    }

    if (action === "approve") {
      respondControlRequest(requestId, true);
    } else if (action === "reject") {
      respondControlRequest(requestId, false);
    }
  });
}

async function restoreSession() {
  try {
    const snapshot = await apiFetchJson("/api/auth/me", { method: "GET" });
    applySnapshot(snapshot);
    connectControl();
    startStatePolling();
    appendLog(`已恢复登录会话: ${state.username}`);
  } catch (err) {
    if (err?.status !== 401) {
      appendLog(`会话恢复失败: ${String(err?.message || err)}`);
    }
    resetAuthState();
  }
}

function connectControl() {
  if (!state.loggedIn) {
    return;
  }
  if (
    state.controlWs &&
    (state.controlWs.readyState === WebSocket.OPEN || state.controlWs.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }

  const ws = new WebSocket(wsUrl("/ws/control"));
  state.controlWs = ws;

  ws.onopen = () => {
    setStatus("在线", true);
    appendLog("控制通道已连接");
    sendControl({ type: "speed", value: state.speed });

    if (state.heartbeatTimer) {
      clearInterval(state.heartbeatTimer);
    }
    state.heartbeatTimer = setInterval(() => {
      sendControl({ type: "heartbeat" });
    }, 300);
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "welcome") {
        state.controlOwner = payload.owner || null;
        setControlPermission(Boolean(payload.canControl));
        renderAccessPanels();
        return;
      }
      if (payload.type === "error") {
        appendLog(`控制错误: ${payload.message}`);
        if (payload.message === "session expired") {
          resetAuthState("登录状态失效，请重新登录");
        }
      }
    } catch (err) {
      appendLog(`控制消息解析失败: ${String(err)}`);
    }
  };

  ws.onclose = () => {
    if (!state.loggedIn) {
      return;
    }
    setStatus("连接中断，重连中...", false);
    appendLog("控制通道已断开");
    if (state.heartbeatTimer) {
      clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = null;
    }
    setTimeout(() => {
      if (state.loggedIn) {
        connectControl();
      }
    }, 1000);
  };

  ws.onerror = () => {
    appendLog("控制通道发生错误");
  };
}

function sendControl(payload) {
  if (!state.loggedIn || !state.canControl) {
    return;
  }
  if (!state.controlWs || state.controlWs.readyState !== WebSocket.OPEN) {
    return;
  }
  state.controlWs.send(JSON.stringify(payload));
}

function isTypingTarget(target) {
  if (!(target instanceof Element)) {
    return false;
  }
  return Boolean(target.closest("textarea, input, [contenteditable], [role='textbox']"));
}

function setKeyState(key, isDown) {
  if (!state.loggedIn || !state.canControl) {
    return;
  }

  key = key.toLowerCase();
  if (!["w", "a", "s", "d"].includes(key)) {
    return;
  }

  if (isDown) {
    if (state.pressedKeys.has(key)) {
      return;
    }
    state.pressedKeys.add(key);
  } else {
    state.pressedKeys.delete(key);
  }

  sendControl({ type: "key", key, isDown });
  refreshKeyUi();
}

function stopAll() {
  state.pressedKeys.clear();
  sendControl({ type: "stop" });
  refreshKeyUi();
}

function refreshKeyUi() {
  document.querySelectorAll(".ctrl[data-key]").forEach((btn) => {
    const key = btn.getAttribute("data-key");
    if (state.pressedKeys.has(key)) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });
}

function initControls() {
  speedRange.addEventListener("input", () => {
    state.speed = Number(speedRange.value);
    speedValue.textContent = state.speed.toFixed(2);
    sendControl({ type: "speed", value: state.speed });
  });

  btnStop.addEventListener("click", () => {
    stopAll();
    appendLog("执行停止");
  });

  document.querySelectorAll(".ctrl[data-key]").forEach((btn) => {
    const key = btn.getAttribute("data-key");

    btn.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      setKeyState(key, true);
    });

    const up = (event) => {
      event.preventDefault();
      setKeyState(key, false);
    };

    btn.addEventListener("pointerup", up);
    btn.addEventListener("pointercancel", up);
    btn.addEventListener("pointerleave", (event) => {
      if (event.buttons === 1) {
        up(event);
      }
    });
  });

  window.addEventListener("keydown", (event) => {
    if (isTypingTarget(event.target)) {
      return;
    }

    const key = event.key.toLowerCase();
    if (!["w", "a", "s", "d", " "].includes(key)) {
      return;
    }
    event.preventDefault();

    if (key === " ") {
      stopAll();
      return;
    }
    setKeyState(key, true);
  });

  window.addEventListener("keyup", (event) => {
    if (isTypingTarget(event.target)) {
      return;
    }

    const key = event.key.toLowerCase();
    if (!["w", "a", "s", "d"].includes(key)) {
      return;
    }
    event.preventDefault();
    setKeyState(key, false);
  });

  window.addEventListener("blur", () => {
    stopAll();
  });
}

function initAudioMonitor() {
  btnAudio.addEventListener("click", async () => {
    if (!state.loggedIn) {
      appendLog("请先登录后再开启监听音频");
      return;
    }

    if (state.audioWs && state.audioWs.readyState === WebSocket.OPEN) {
      state.audioWs.close();
      state.audioWs = null;
      stopMonitorRxTicker();
      audioState.textContent = "音频监听：未开启";
      btnAudio.textContent = "开启监听音频";
      appendLog("音频监听已关闭");
      return;
    }

    if (!state.monitorCtx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      state.monitorCtx = new AudioCtx();
      state.monitorGain = state.monitorCtx.createGain();
      state.monitorGain.connect(state.monitorCtx.destination);
      applyMonitorGain();
    }
    await state.monitorCtx.resume();

    const ws = new WebSocket(wsUrl("/ws/audio-out"));
    ws.binaryType = "arraybuffer";
    state.audioWs = ws;

    ws.onopen = () => {
      audioState.textContent = "音频监听：已开启";
      btnAudio.textContent = "关闭监听音频";
      resetMonitorRx();
      startMonitorRxTicker();
      appendLog("音频监听已连接");
    };

    ws.onmessage = async (event) => {
      if (typeof event.data === "string") {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "config") {
            state.monitorConfig = {
              sampleRate: Number(payload.sampleRate || 16000),
              channels: Number(payload.channels || 1),
            };
            appendLog(
              `音频配置 sampleRate=${state.monitorConfig.sampleRate} channels=${state.monitorConfig.channels} device=${payload.device || "unknown"}`
            );
          }
        } catch (err) {
          appendLog(`音频配置解析失败: ${String(err)}`);
        }
        return;
      }

      const arrayBuffer = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
      state.monitorRxBytes += arrayBuffer.byteLength;
      playPcmChunk(arrayBuffer);
    };

    ws.onclose = () => {
      stopMonitorRxTicker();
      audioState.textContent = "音频监听：已断开";
      btnAudio.textContent = "开启监听音频";
      appendLog("音频监听连接已断开");
      state.audioWs = null;
    };

    ws.onerror = () => {
      appendLog("音频监听通道错误");
    };
  });
}

function playPcmChunk(arrayBuffer) {
  if (!state.monitorCtx || !state.monitorGain || !arrayBuffer || arrayBuffer.byteLength < 2) {
    return;
  }

  const pcm = new Int16Array(arrayBuffer);
  const floatData = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i += 1) {
    floatData[i] = Math.max(-1, Math.min(1, pcm[i] / 32768));
  }

  const sampleRate = state.monitorConfig.sampleRate || 16000;
  const buffer = state.monitorCtx.createBuffer(1, floatData.length, sampleRate);
  buffer.getChannelData(0).set(floatData);

  const source = state.monitorCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(state.monitorGain);

  const now = state.monitorCtx.currentTime + 0.06;
  if (state.monitorPlaybackTime < now || state.monitorPlaybackTime - now > 1.2) {
    state.monitorPlaybackTime = now;
  }

  source.start(state.monitorPlaybackTime);
  state.monitorPlaybackTime += buffer.duration;
}

async function ensureTalkPipeline() {
  if (!state.talkCtx) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    state.talkCtx = new AudioCtx();
  }
  await state.talkCtx.resume();

  if (!state.talkStream) {
    state.talkStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
      },
      video: false,
    });
  }

  if (!state.talkWs || state.talkWs.readyState !== WebSocket.OPEN) {
    const ws = new WebSocket(wsUrl("/ws/talk"));
    ws.binaryType = "arraybuffer";
    state.talkWs = ws;

    await new Promise((resolve, reject) => {
      ws.onopen = () => {
        appendLog("对讲通道已连接");
        resolve();
      };
      ws.onerror = () => {
        reject(new Error("对讲通道连接失败"));
      };
    });
  }

  if (!state.talkSource) {
    state.talkSource = state.talkCtx.createMediaStreamSource(state.talkStream);
  }

  if (!state.talkProcessor) {
    state.talkProcessor = state.talkCtx.createScriptProcessor(4096, 1, 1);
    const sink = state.talkCtx.createGain();
    sink.gain.value = 0;

    state.talkProcessor.onaudioprocess = (event) => {
      if (!state.talking) {
        return;
      }
      if (!state.talkWs || state.talkWs.readyState !== WebSocket.OPEN) {
        return;
      }

      const input = event.inputBuffer.getChannelData(0);
      const down = downsampleFloat32(input, state.talkCtx.sampleRate, 16000);
      const pcm = floatToInt16(down);
      state.talkWs.send(pcm.buffer);
    };

    state.talkSource.connect(state.talkProcessor);
    state.talkProcessor.connect(sink);
    sink.connect(state.talkCtx.destination);
  }
}

function initTalkButton() {
  const start = async (event) => {
    event.preventDefault();
    if (!state.loggedIn) {
      appendLog("请先登录后再使用对讲");
      return;
    }
    if (!state.canControl) {
      appendLog("当前没有控制权，无法发送对讲");
      return;
    }
    try {
      await ensureTalkPipeline();
      state.talking = true;
      btnTalk.classList.add("active");
      btnTalk.textContent = "正在发送语音...";
      appendLog("开始发送对讲音频");
    } catch (err) {
      appendLog(`对讲启动失败: ${String(err)}`);
    }
  };

  const stop = (event) => {
    event.preventDefault();
    if (!state.talking) {
      return;
    }
    state.talking = false;
    btnTalk.classList.remove("active");
    btnTalk.textContent = "按住说话";
    appendLog("停止发送对讲音频");

    if (state.talkWs && state.talkWs.readyState === WebSocket.OPEN) {
      state.talkWs.send(JSON.stringify({ type: "stop" }));
    }
  };

  btnTalk.addEventListener("pointerdown", start);
  btnTalk.addEventListener("pointerup", stop);
  btnTalk.addEventListener("pointercancel", stop);
  btnTalk.addEventListener("pointerleave", (event) => {
    if (event.buttons === 1) {
      stop(event);
    }
  });
}

function initAudioOutputControls() {
  state.monitorVolume = loadSavedMonitorVolume();
  state.monitorLastVolume = state.monitorVolume > 0 ? state.monitorVolume : 1.2;
  state.monitorMuted = state.monitorVolume === 0;

  updateMonitorVolumeUi();
  btnAudioMute.textContent = state.monitorMuted ? "取消静音" : "静音";

  monitorVolume.addEventListener("input", () => {
    const value = clamp(Number(monitorVolume.value), 0, 3);
    state.monitorVolume = value;
    if (value > 0) {
      state.monitorLastVolume = value;
      state.monitorMuted = false;
      btnAudioMute.textContent = "静音";
    } else {
      state.monitorMuted = true;
      btnAudioMute.textContent = "取消静音";
    }
    saveMonitorVolume(state.monitorVolume);
    updateMonitorVolumeUi();
    applyMonitorGain();
  });

  btnAudioMute.addEventListener("click", () => {
    if (!state.monitorMuted) {
      state.monitorMuted = true;
      btnAudioMute.textContent = "取消静音";
      appendLog("监听输出已静音");
    } else {
      state.monitorMuted = false;
      if (state.monitorVolume <= 0) {
        state.monitorVolume = state.monitorLastVolume > 0 ? state.monitorLastVolume : 1.2;
        saveMonitorVolume(state.monitorVolume);
      }
      btnAudioMute.textContent = "静音";
      appendLog("监听输出已取消静音");
    }
    updateMonitorVolumeUi();
    applyMonitorGain();
  });
}

function downsampleFloat32(buffer, inputRate, targetRate) {
  if (targetRate >= inputRate) {
    return buffer;
  }

  const ratio = inputRate / targetRate;
  const outLength = Math.round(buffer.length / ratio);
  const out = new Float32Array(outLength);

  let offsetIn = 0;
  for (let i = 0; i < outLength; i += 1) {
    const nextOffset = Math.round((i + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let j = offsetIn; j < nextOffset && j < buffer.length; j += 1) {
      sum += buffer[j];
      count += 1;
    }
    out[i] = count > 0 ? sum / count : 0;
    offsetIn = nextOffset;
  }

  return out;
}

function floatToInt16(floatBuf) {
  const out = new Int16Array(floatBuf.length);
  for (let i = 0; i < floatBuf.length; i += 1) {
    const s = Math.max(-1, Math.min(1, floatBuf[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function initTts() {
  btnTts.addEventListener("click", async () => {
    if (!state.loggedIn) {
      appendLog("请先登录后再发送 TTS");
      return;
    }
    if (!state.canControl) {
      appendLog("当前没有控制权，无法发送 TTS");
      return;
    }

    const text = ttsInput.value.trim();
    if (!text) {
      appendLog("TTS 文本为空");
      return;
    }

    btnTts.disabled = true;
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (!resp.ok) {
        const msg = await resp.text();
        appendLog(`TTS 失败: ${resp.status} ${msg}`);
        if (resp.status === 401) {
          resetAuthState("登录状态失效，请重新登录");
        }
      } else {
        appendLog("TTS 已发送");
      }
    } catch (err) {
      appendLog(`TTS 请求失败: ${String(err)}`);
    } finally {
      btnTts.disabled = false;
    }
  });
}

function init() {
  initControls();
  initAuthControls();
  initAccessPanelActions();
  initAudioOutputControls();
  initAudioMonitor();
  initTalkButton();
  initTts();

  speedValue.textContent = Number(speedRange.value).toFixed(2);
  applyLoginUi();
  setControlPermission(false);
  renderAccessPanels();
  appendLog("监听音量可在“监听音量”滑块调节，静音按钮可快速开关输出");
  appendLog("监听音频不需要麦克风权限弹窗；按住说话才会请求麦克风权限");
  restoreSession();
  appendLog("页面已初始化");
}

init();
