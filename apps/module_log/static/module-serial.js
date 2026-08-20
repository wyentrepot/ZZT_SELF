// 模块日志/烧录串口独立页面逻辑（ModuleSerialService · 双通道）
// 与侦听台 app.js 完全独立：新标签页运行，800ms 增量轮询两路日志 + 状态。
// 双通道：cco / sta 固定两路，各自独立串口/启动/停止/烧录/日志；
// 底部发送框选择目标通道，回车即发送当前行。
(function () {
  "use strict";

  const $ = (sel) =>
    sel.startsWith(".") || sel.startsWith("#") || sel.startsWith("[")
      ? document.querySelector(sel)
      : document.getElementById(sel);
  const API_BASE = document.body.dataset.apiBase || "/api";
  const api = function (suffix) { return API_BASE + suffix; };
  const REFRESH_SPEED_MS = { fast: 100, medium: 500, slow: 800 };
  const DEFAULT_REFRESH_SPEED = "medium";
  const MAX_LOG_ROWS = 3000;
  const sessionsById = new Map();
  const lastSeqBySessionId = new Map();
  const viewStateBySessionId = new Map();
  let activeSessionId = null;
  let pollTimer = null;
  let portDetails = [];

  async function request(url, options) {
    const response = await fetch(url, options);
    let body = {};
    try { body = await response.json(); } catch (error) {}
    if (!response.ok) throw new Error(body.detail || response.statusText || "请求失败");
    return body;
  }

  function escapeHtml(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function currentSession() {
    return activeSessionId ? sessionsById.get(activeSessionId) : null;
  }

  function viewState(sessionId) {
    if (!viewStateBySessionId.has(sessionId)) {
      viewStateBySessionId.set(sessionId, {
        lines: [], firmwarePath: "", slot: "0", noReboot: false, autoScroll: true,
      });
    }
    return viewStateBySessionId.get(sessionId);
  }

  function isRunning(session) {
    return !!session && (session.state === "running" || session.state === "starting");
  }

  function setSelectValue(id, value) {
    const element = $(id);
    if (!element || value === undefined || value === null) return;
    const expected = String(value);
    for (let index = 0; index < element.options.length; index += 1) {
      if (String(element.options[index].value) === expected) {
        element.value = expected;
        return;
      }
    }
  }

  function renderSessionTabs() {
    const host = $("ms-session-tabs");
    host.replaceChildren();
    sessionsById.forEach(function (session) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ms-session-tab " + session.state;
      if (session.session_id === activeSessionId) button.classList.add("active");
      button.dataset.sessionId = session.session_id;
      const dot = document.createElement("span");
      dot.className = "dot";
      button.appendChild(dot);
      button.appendChild(document.createTextNode(session.title || session.session_id));
      button.addEventListener("click", function () { switchSession(session.session_id); });
      host.appendChild(button);
    });
  }

  function renderLog() {
    const session = currentSession();
    const box = $("ms-log-box");
    box.replaceChildren();
    if (!session) return;
    const view = viewState(session.session_id);
    view.lines.forEach(function (line) {
      const row = document.createElement("div");
      const direction = line.dir || "EVENT";
      const cls = direction === "RX" ? "rx" : direction === "TX" ? "tx" : "ev";
      const timestamp = document.createElement("span");
      timestamp.className = "t";
      timestamp.textContent = "[" + (line.ts || "") + "] ";
      const body = document.createElement("span");
      body.className = cls;
      body.textContent = "[" + direction + "] " +
        String(line.text || "").split(String.fromCharCode(13)).join("").split(String.fromCharCode(10)).join("");
      row.append(timestamp, body);
      box.appendChild(row);
    });
    if (view.autoScroll) box.scrollTop = box.scrollHeight;
  }

  function renderActiveSession() {
    const session = currentSession();
    const panel = $("ms-session-panel");
    const closeButton = $("ms-close-session");
    if (!session) {
      panel.hidden = true;
      closeButton.disabled = true;
      $("ms-session-message").textContent = "正在创建默认页面…";
      renderSessionTabs();
      return;
    }
    panel.hidden = false;
    closeButton.disabled = false;
    renderSessionTabs();

    const config = session.serial_config || {};
    const identity = session.port_identity || {};
    const view = viewState(session.session_id);
    const running = isRunning(session);

    $("ms-title").value = session.title || "";
    $("ms-module").value = session.module || "cco";
    if (session.port) setSelectValue("ms-port", session.port);
    setSelectValue("ms-baud", config.baudrate || session.baudrate || 115200);
    setSelectValue("ms-parity", config.parity || session.parity || "N");
    setSelectValue("ms-bytesize", config.bytesize || session.bytesize || 8);
    setSelectValue("ms-stopbits", config.stopbits || session.stopbits || 1);
    $("ms-bin").value = view.firmwarePath;
    $("ms-slot").value = view.slot;
    $("ms-no-reboot").checked = view.noReboot;
    $("ms-autoscroll").checked = view.autoScroll;
    $("ms-module-badge").textContent = (session.module || "cco").toUpperCase();
    $("ms-module-badge").className = "ms-channel-badge " + (session.module || "cco");
    $("ms-session-title-display").textContent = session.title || "实时日志";
    $("ms-session-status").textContent =
      (session.state || "idle") + " · " + (identity.label || session.port || "未连接") +
      " · " + (config.baudrate || session.baudrate || 115200);
    $("ms-send-target").textContent = "→ " + (session.title || session.session_id);

    $("ms-toggle").textContent = running ? "停止" : "启动";
    $("ms-toggle").className = running ? "secondary-button" : "primary-button";
    ["ms-title", "ms-module", "ms-port", "ms-baud", "ms-parity", "ms-bytesize", "ms-stopbits"].forEach(function (id) {
      $(id).disabled = running;
    });
    $("ms-refresh-ports").disabled = running;
    $("ms-flash").disabled = !running || !!(session.flash && session.flash.flashing);

    const flash = session.flash || {};
    if (flash.flashing && flash.total) {
      const percent = Math.round((flash.packet || 0) * 100 / flash.total);
      $("ms-progress-bar").style.width = percent + "%";
      $("ms-progress-text").textContent = "传输中 " + (flash.packet || 0) + "/" + flash.total + " (" + percent + "%)";
    } else if (flash.phase === "done") {
      $("ms-progress-bar").style.width = "100%";
      $("ms-progress-text").textContent = "烧录完成";
    } else if (flash.phase === "error") {
      $("ms-progress-bar").style.width = "0%";
      $("ms-progress-text").textContent = "烧录失败：" + (flash.message || "");
    } else {
      $("ms-progress-bar").style.width = "0%";
      $("ms-progress-text").textContent = flash.message || "未开始";
    }
    $("ms-log-path").textContent = session.log_file || "尚未生成日志文件";
    $("ms-session-message").textContent = identity.mapping_id
      ? "映射：" + identity.mapping_id + " · " + (identity.label || identity.device || "")
      : "未映射串口按实际设备名独占";
    renderLog();
  }

  async function refreshPorts() {
    const selected = $("ms-port").value;
    try {
      const data = await request(api("/module-serial/ports"));
      portDetails = data.port_details || [];
      const ports = portDetails.length ? portDetails : (data.ports || []).map(function (device) {
        return { device: device, label: "", online: true };
      });
      const select = $("ms-port");
      select.replaceChildren();
      if (!ports.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "（未发现串口）";
        select.appendChild(option);
        return;
      }
      ports.forEach(function (item) {
        const option = document.createElement("option");
        option.value = item.device;
        option.disabled = item.online === false || item.enabled === false;
        const prefix = item.label ? item.label + " · " : "";
        const suffix = item.online === false ? "（离线）" : "";
        option.textContent = prefix + item.device + suffix;
        select.appendChild(option);
      });
      const session = currentSession();
      if (session && session.port) setSelectValue("ms-port", session.port);
      else if (selected) setSelectValue("ms-port", selected);
    } catch (error) {
      $("ms-session-message").textContent = "端口列表加载失败：" + error.message;
    }
  }

  async function refreshSessions() {
    try {
      const data = await request(api("/module-serial/sessions"));
      const sessions = data.sessions || [];
      sessionsById.clear();
      sessions.forEach(function (session) {
        sessionsById.set(session.session_id, session);
        viewState(session.session_id);
        if (!lastSeqBySessionId.has(session.session_id)) lastSeqBySessionId.set(session.session_id, -1);
      });
      if (!activeSessionId || !sessionsById.has(activeSessionId)) {
        activeSessionId = sessions.length ? sessions[0].session_id : null;
      }
      renderActiveSession();
      if (typeof cmpRefreshSessions === "function") cmpRefreshSessions();
      return sessions;
    } catch (error) {
      $("ms-server-state").textContent = "连接失败：" + error.message;
      return [];
    }
  }

  async function createSession(title, module) {
    try {
      const session = await request(api("/module-serial/sessions"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title || "", module: module || "cco" }),
      });
      sessionsById.set(session.session_id, session);
      activeSessionId = session.session_id;
      viewState(session.session_id);
      lastSeqBySessionId.set(session.session_id, -1);
      renderActiveSession();
      await refreshPorts();
      if (typeof cmpRefreshSessions === "function") cmpRefreshSessions();
      return session;
    } catch (error) {
      alert("新增页面失败：" + error.message);
      return null;
    }
  }

  async function ensureDefaultSession() {
    const sessions = await refreshSessions();
    if (!sessions.length) await createSession("", "cco");
  }

  async function switchSession(sessionId) {
    if (!sessionsById.has(sessionId)) return;
    activeSessionId = sessionId;
    renderActiveSession();
    await refreshPorts();
    await pollActiveLogs();
  }

  async function updateCurrentSession(patch) {
    const session = currentSession();
    if (!session) return null;
    try {
      const updated = await request(api("/module-serial/sessions/" + session.session_id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      sessionsById.set(updated.session_id, updated);
      renderActiveSession();
      if (typeof cmpRefreshSessions === "function") cmpRefreshSessions();
      return updated;
    } catch (error) {
      alert("更新页面失败：" + error.message);
      renderActiveSession();
      return null;
    }
  }

  async function applyMappedPortDefaults() {
    const session = currentSession();
    if (!session || isRunning(session)) return;
    const detail = portDetails.find(function (item) { return item.device === $("ms-port").value; });
    if (!detail) return;
    if (detail.baudrate) setSelectValue("ms-baud", detail.baudrate);
    if (detail.parity) setSelectValue("ms-parity", detail.parity);
    if (detail.bytesize) setSelectValue("ms-bytesize", detail.bytesize);
    if (detail.stopbits) setSelectValue("ms-stopbits", detail.stopbits);

    const patch = {};
    if (detail.module && detail.module !== session.module) patch.module = detail.module;
    if (detail.label && (!session.title || /^实时日志 /.test(session.title))) patch.title = detail.label;
    if (Object.keys(patch).length) await updateCurrentSession(patch);
  }

  async function toggleSerial() {
    const session = currentSession();
    if (!session) return;
    const button = $("ms-toggle");
    button.disabled = true;
    try {
      if (isRunning(session)) {
        await request(api("/module-serial/sessions/" + session.session_id + "/stop"), { method: "POST" });
      } else {
        const port = $("ms-port").value;
        if (!port) throw new Error("请先选择串口");
        await request(api("/module-serial/sessions/" + session.session_id + "/start"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            port: port,
            baudrate: parseInt($("ms-baud").value, 10),
            parity: $("ms-parity").value,
            bytesize: parseInt($("ms-bytesize").value, 10),
            stopbits: parseInt($("ms-stopbits").value, 10),
          }),
        });
        const view = viewState(session.session_id);
        view.lines = [];
        lastSeqBySessionId.set(session.session_id, -1);
      }
      await refreshSessions();
    } catch (error) {
      alert("串口操作失败：" + error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function pollActiveLogs() {
    const session = currentSession();
    if (!session) return;
    const after = lastSeqBySessionId.get(session.session_id);
    try {
      const data = await request(api("/module-serial/sessions/" + session.session_id + "/logs?after=" + after));
      const view = viewState(session.session_id);
      (data.lines || []).forEach(function (line) { view.lines.push(line); });
      while (view.lines.length > MAX_LOG_ROWS) view.lines.shift();
      if (typeof data.last_seq === "number") lastSeqBySessionId.set(session.session_id, data.last_seq);
      renderLog();
    } catch (error) {}
  }

  async function chooseFirmware() {
    try {
      const data = await request(api("/fs/pick"));
      if (data.path) {
        $("ms-bin").value = data.path;
        viewState(activeSessionId).firmwarePath = data.path;
      }
    } catch (error) {
      alert("无法打开文件选择器，请手动输入固件路径");
    }
  }

  async function startFlash() {
    const session = currentSession();
    if (!session) return;
    const view = viewState(session.session_id);
    const binPath = $("ms-bin").value.trim();
    if (!binPath) { alert("请先选择固件 .bin 路径"); return; }
    view.firmwarePath = binPath;
    view.slot = $("ms-slot").value;
    view.noReboot = $("ms-no-reboot").checked;
    if (!confirm("确认向当前页面“" + session.title + "”烧录 " + binPath + "？")) return;
    try {
      await request(api("/module-serial/sessions/" + session.session_id + "/flash"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bin_path: binPath, slot: parseInt(view.slot, 10),
          baud_plan: null, no_reboot_after: view.noReboot,
        }),
      });
      await refreshSessions();
    } catch (error) {
      alert("烧录失败：" + error.message);
    }
  }

  async function sendText(text, appendNewline) {
    const session = currentSession();
    if (!session) return;
    try {
      await request(api("/module-serial/sessions/" + session.session_id + "/write-text"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, append_newline: appendNewline }),
      });
      $("ms-send-text").value = "";
    } catch (error) {
      alert("发送失败：" + error.message);
    }
  }

  async function closeActiveSession() {
    const session = currentSession();
    if (!session) return;
    if (isRunning(session)) {
      if (!confirm("当前页面仍在采集。确认后将先停止串口再关闭页面。")) return;
      try {
        await request(api("/module-serial/sessions/" + session.session_id + "/stop"), { method: "POST" });
      } catch (error) {
        alert("停止串口失败：" + error.message);
        return;
      }
    }
    try {
      await request(api("/module-serial/sessions/" + session.session_id), { method: "DELETE" });
      sessionsById.delete(session.session_id);
      viewStateBySessionId.delete(session.session_id);
      lastSeqBySessionId.delete(session.session_id);
      activeSessionId = null;
      await refreshSessions();
      if (!currentSession()) await createSession("", "cco");
    } catch (error) {
      alert("关闭页面失败：" + error.message);
    }
  }

  function bind() {
    $("ms-add-session").addEventListener("click", function () { createSession("", "cco"); });
    $("ms-close-session").addEventListener("click", closeActiveSession);
    $("ms-refresh-ports").addEventListener("click", refreshPorts);
    $("ms-toggle").addEventListener("click", toggleSerial);
    $("ms-port").addEventListener("change", applyMappedPortDefaults);
    $("ms-title").addEventListener("change", function () {
      const value = $("ms-title").value.trim();
      if (value) updateCurrentSession({ title: value });
    });
    $("ms-module").addEventListener("change", function () {
      updateCurrentSession({ module: $("ms-module").value });
    });
    $("ms-pick").addEventListener("click", chooseFirmware);
    $("ms-flash").addEventListener("click", startFlash);
    $("ms-clear").addEventListener("click", function () {
      const session = currentSession();
      if (!session) return;
      viewState(session.session_id).lines = [];
      renderLog();
    });
    $("ms-autoscroll").addEventListener("change", function () {
      const session = currentSession();
      if (session) viewState(session.session_id).autoScroll = $("ms-autoscroll").checked;
    });
    $("ms-send-btn").addEventListener("click", function () {
      sendText($("ms-send-text").value, $("ms-send-append-nl").checked);
    });
    $("ms-send-newline").addEventListener("click", function () { sendText("", true); });
    $("ms-send-clear").addEventListener("click", function () { $("ms-send-text").value = ""; });
    $("ms-send-text").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendText($("ms-send-text").value, $("ms-send-append-nl").checked);
      }
    });
    $("ms-sender-hide").addEventListener("click", function () {
      $("ms-sender").hidden = true;
      $("ms-sender-showbar").hidden = false;
    });
    $("ms-sender-show").addEventListener("click", function () {
      $("ms-sender").hidden = false;
      $("ms-sender-showbar").hidden = true;
    });
  }

  function startPolling(interval) {
    if (pollTimer !== null) clearInterval(pollTimer);
    pollTimer = setInterval(function () {
      refreshSessions();
      pollActiveLogs();
    }, interval);
    window.__pollIntervalMs = interval;
  }

  function setRefreshSpeed(speed) {
    const interval = REFRESH_SPEED_MS[speed] || REFRESH_SPEED_MS[DEFAULT_REFRESH_SPEED];
    startPolling(interval);
    return interval;
  }

  async function refreshStatus() { return refreshSessions(); }
  async function pollLogs() { return pollActiveLogs(); }

  // ---------- 对照解析页 ----------
  const cmp = {
    source: "file", module: "cco", sessionId: null, events: [], lines: [],
    selectedEvent: -1, selectedLine: -1, realtimeTimer: null,
  };
  const CMP_ICONS = { join: "🛜", collect: "📊", send: "⬆️", beacon: "📡", state: "⚙️", flash: "⚡", error: "⚠️", other: "🔍" };
  const CMP_LEVELS = { info: "info", warn: "warn", error: "error" };

  function cmpSetMeta(text) { $("cmp-meta").textContent = text || ""; }

  function cmpCurrentSession() {
    return cmp.sessionId ? sessionsById.get(cmp.sessionId) : null;
  }

  function cmpSyncModuleButtons() {
    document.querySelectorAll(".cmp-modseg-btn").forEach(function (button) {
      button.classList.toggle("active", button.dataset.mod === cmp.module);
    });
  }

  function cmpRefreshSessions() {
    const select = $("cmp-session");
    if (!select) return;
    const previous = cmp.sessionId || select.value;
    select.replaceChildren();
    sessionsById.forEach(function (session) {
      const option = document.createElement("option");
      option.value = session.session_id;
      const identity = session.port_identity || {};
      option.textContent = (session.title || session.session_id) + " · " +
        (identity.label || session.port || "未连接");
      select.appendChild(option);
    });
    if (!select.options.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "（暂无实时页面）";
      select.appendChild(option);
    }
    if (previous) setSelectValue("cmp-session", previous);
    cmp.sessionId = select.value || null;
    const session = cmpCurrentSession();
    if (session && session.module) {
      cmp.module = session.module;
      cmpSyncModuleButtons();
    }
    cmpRefreshSerialStatus();
  }

  function cmpRealtimeUrl() {
    if (!cmp.sessionId) return "";
    return api("/loghooks/realtime?session_id=" + encodeURIComponent(cmp.sessionId) + "&limit=8000");
  }

  async function cmpScan() {
    const module = cmp.module;
    const runBtn = cmp.source === "file" ? $("cmp-run-file") : $("cmp-run-realtime");
    if (runBtn) runBtn.disabled = true;
    cmpSetMeta("解析中…");
    try {
      let data;
      if (cmp.source === "file") {
        const path = $("cmp-file").value.trim();
        if (!path) { alert("请先选择日志文件或目录"); return; }
        data = await request(api("/loghooks/scan?path=" + encodeURIComponent(path) +
          "&module=" + encodeURIComponent(module) + "&limit=8000"));
      } else {
        const session = cmpCurrentSession();
        if (!session) throw new Error("请先新增并选择一个实时日志页面");
        cmp.module = session.module || "cco";
        const url = cmpRealtimeUrl();
        data = await request(url);
      }
      cmp.events = data.events || [];
      cmp.lines = (data.lines || []).map(function (line) {
        return {
          key: (line.file || "") + ":" + line.line,
          line: line.line,
          file: line.file || "",
          raw: line.raw,
        };
      });
      cmp.events.forEach(function (event, index) {
        event.__i = index;
        event.__key = (event.file || "") + ":" + event.line;
      });
      cmp.selectedEvent = -1;
      cmp.selectedLine = -1;
      cmpRenderEvents();
      cmpRenderLog();
      cmpUpdateStats(data, cmp.module);
      cmpSetMeta(data.module ? cmp.module.toUpperCase() + " 来源" : "");
      if (cmp.source === "realtime") cmpStartRealtime();
    } catch (error) {
      cmpSetMeta("解析失败");
      alert("解析失败：" + error.message);
    } finally {
      if (runBtn) runBtn.disabled = false;
    }
  }

  function cmpUpdateStats(data, module) {
    $("cmp-stats").hidden = false;
    $("cmp-stat-events").textContent = (data.event_count || 0);
    $("cmp-stat-lines").textContent = (data.total_lines || 0);
    const files = (data.files && data.files.length) ? data.files.length : (data.module ? 1 : 0);
    $("cmp-stat-files").textContent = files;
    const drifts = (data.events || []).filter((e) => e.line_drift).length;
    $("cmp-stat-drift").textContent = drifts;
    // 来源文件显示
    const logFile = $("cmp-log-file");
    if (data.files && data.files.length) logFile.textContent = data.files.length === 1 ? data.files[0] : `${data.files.length} 个文件`;
    else logFile.textContent = data.module ? `${module.toUpperCase()} 实时` : "";
  }

  function cmpRenderEvents() {
    const list = $("cmp-event-list");
    list.innerHTML = "";
    if (!cmp.events.length) { list.innerHTML = '<div class="cmp-empty">未解析到事件</div>'; return; }
    cmp.events.forEach((ev) => {
      const bar = document.createElement("div");
      bar.className = "cmp-ev-bar " + (CMP_LEVELS[ev.level] || "info");
      const icon = document.createElement("div");
      icon.className = "cmp-ev-icon";
      icon.textContent = CMP_ICONS[ev.category] || "🔍";
      const label = document.createElement("span");
      label.className = "cmp-ev-label";
      label.textContent = ev.label || ev.type;
      const time = document.createElement("span");
      time.className = "cmp-ev-time";
      time.textContent = ev.time;
      const typeTag = document.createElement("span");
      typeTag.className = "cmp-ev-type";
      typeTag.textContent = ev.type;
      const head = document.createElement("div");
      head.className = "cmp-ev-head";
      head.appendChild(label);
      head.appendChild(typeTag);
      head.appendChild(time);
      const msg = document.createElement("div");
      msg.className = "cmp-ev-msg";
      msg.textContent = ev.message;
      const body = document.createElement("div");
      body.className = "cmp-ev-body";
      body.appendChild(head);
      body.appendChild(msg);
      const card = document.createElement("div");
      card.className = "cmp-ev";
      card.dataset.ev = ev.__i;
      card.appendChild(bar);
      card.appendChild(icon);
      card.appendChild(body);
      card.addEventListener("click", () => cmpSelectEvent(ev.__i));
      list.appendChild(card);
    });
  }

// 虚拟滚动：右侧日志全量渲染但只画可视行
  const CMP_ROW_H = 18; // 固定行高（与 CSS .cmp-log-line 一致）

  function cmpLineKey(line) { return line.key; }

  function cmpBuildLineHTML(raw) {
    const m = raw.match(/^\[([^\]]*)\]\s*\[(RX|TX|EVENT)\]\s*(.*)$/);
    if (m) {
      const dir = m[2];
      const cls = dir === "RX" ? "rx" : dir === "TX" ? "tx" : "ev";
      return `<span class="t">[${escapeHtml(m[1])}]</span> [${dir}] <span class="${cls}">${escapeHtml(m[3])}</span>`;
    }
    return escapeHtml(raw);
  }

  function cmpRenderLog() {
    const list = $("cmp-log-list");
    const spacer = $("cmp-log-spacer");
    const window = $("cmp-log-window");
    cmp.virtStart = -1; cmp.virtEnd = -1; // 强制重绘
    list.innerHTML = "";
    list.appendChild(spacer);
    list.appendChild(window);
    if (!cmp.lines.length) {
      window.innerHTML = '<div class="cmp-empty">暂无日志行</div>';
      spacer.style.height = "0px";
      return;
    }
    // 撑起总高（虚拟滚动必需）
    spacer.style.height = (cmp.lines.length * CMP_ROW_H) + "px";
    // 移除旧滚动监听，绑定新的一次
    if (list.__virtBound) list.removeEventListener("scroll", cmpVirtUpdate);
    list.addEventListener("scroll", cmpVirtUpdate);
    list.__virtBound = true;
    cmpVirtUpdate();
  }

  function cmpVirtUpdate() {
    const list = $("cmp-log-list");
    const window = $("cmp-log-window");
    if (!cmp.lines.length) return;
    const scrollTop = list.scrollTop;
    const viewH = list.clientHeight;
    // 可视范围（含缓冲 10 行）
    let start = Math.max(0, Math.floor(scrollTop / CMP_ROW_H) - 10);
    let end = Math.min(cmp.lines.length, Math.ceil((scrollTop + viewH) / CMP_ROW_H) + 10);
    // 只重绘当范围变化时
    if (cmp.virtStart === start && cmp.virtEnd === end) return;
    cmp.virtStart = start; cmp.virtEnd = end;
    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      const ln = cmp.lines[i];
      const div = document.createElement("div");
      div.className = "cmp-log-line";
      div.style.top = (i * CMP_ROW_H) + "px";
      div.dataset.key = ln.key;
      div.dataset.line = i;
      div.innerHTML = cmpBuildLineHTML(ln.raw);
      if (cmp.selectedLine >= 0 && cmp.selectedLine === i && cmp.lines[i].file === (cmp.selectedFile || "")) {
        div.classList.add("selected");
      }
      div.addEventListener("click", () => cmpSelectLine(ln.key));
      frag.appendChild(div);
    }
    window.innerHTML = "";
    window.appendChild(frag);
  }

  // 定位到某行（file:line）——事件点击入口
  function cmpScrollToLine(key, select) {
    // key 形如 "file:line"，找到对应行索引
    const idx = cmp.lines.findIndex((ln) => ln.key === key);
    if (idx < 0) return;
    const list = $("cmp-log-list");
    // 记录选中（按行索引 + file）
    const ln = cmp.lines[idx];
    cmp.selectedLine = idx;
    cmp.selectedFile = ln.file || "";
    // 滚动容器到目标行位置（居中）
    const targetScroll = idx * CMP_ROW_H - (list.clientHeight - CMP_ROW_H) / 2;
    list.scrollTop = Math.max(0, targetScroll);
    cmpVirtUpdate();
    // 高亮该行（重新标记）
    $("cmp-log-window").querySelectorAll(".cmp-log-line").forEach((el) => {
      el.classList.toggle("selected", parseInt(el.dataset.line, 10) === idx);
    });
  }

  // 点击左侧事件卡片：高亮左栏 + 跳转右侧对应日志行
  function cmpSelectEvent(i) {
    cmp.selectedEvent = i;
    const ev = cmp.events[i];
    // 左栏高亮
    $("cmp-event-list").querySelectorAll(".cmp-ev").forEach((el) => {
      el.classList.toggle("selected", parseInt(el.dataset.ev, 10) === i);
    });
    if (ev) {
      cmpScrollToLine(ev.__key, true);
      cmp.selectedLine = ev.line;
    }
  }

  function cmpSelectLine(key) {
    const idx = cmp.lines.findIndex((ln) => ln.key === key);
    if (idx >= 0) {
      const ln = cmp.lines[idx];
      cmp.selectedLine = idx;
      cmp.selectedFile = ln.file || "";
      cmpVirtUpdate();
    }
    // 联动左栏事件
    const evIdx = cmp.events.findIndex((e) => e.__key === key);
    if (evIdx >= 0) {
      cmp.selectedEvent = evIdx;
      const evEl = $("cmp-event-list").querySelector(`.cmp-ev[data-ev="${evIdx}"]`);
      if (evEl) {
        $("cmp-event-list").querySelectorAll(".cmp-ev").forEach((el) => el.classList.remove("selected"));
        evEl.classList.add("selected");
        evEl.scrollIntoView({ block: "nearest" });
      }
    }
  }

  function cmpStartRealtime() {
    cmpStopRealtime();
    if (!cmpCurrentSession()) return;
    cmp.realtimeTimer = setInterval(function () {
      const url = cmpRealtimeUrl();
      if (!url) return;
      request(url)
        .then(function (data) {
          cmp.events = data.events || [];
          cmpRenderEvents();
          cmp.lines = (data.lines || []).map(function (line) {
            return {
              key: (line.file || "") + ":" + line.line,
              line: line.line,
              file: line.file || "",
              raw: line.raw,
            };
          });
          cmpRenderLog();
          cmpUpdateStats(data, cmp.module);
          cmpSetMeta(cmp.module.toUpperCase() + " 实时");
        })
        .catch(function () {});
    }, 2000);
  }
  function cmpStopRealtime() {
    if (cmp.realtimeTimer) { clearInterval(cmp.realtimeTimer); cmp.realtimeTimer = null; }
  }

  function cmpSetSource(src) {
    cmp.source = src;
    cmpStopRealtime();
    document.querySelectorAll(".cmp-srccard").forEach((c) => {
      c.classList.toggle("active", c.dataset.src === src);
    });
    $("cmp-cfg-file").hidden = src !== "file";
    $("cmp-cfg-realtime").hidden = src !== "realtime";
    $("cmp-stats").hidden = true;
    cmpSetMeta("");
  }

  async function cmpPickFile() {
    try {
      const data = await request(api("/fs/pick"));
      if (data && data.path) { $("cmp-file").value = data.path; }
    } catch (_) { alert("无法打开文件选择器，请手动输入路径"); }
  }

  async function cmpPickDir() {
    // 打开目录：复用 fs/pick 选一个文件，取其所在目录；或提示手动输入目录
    try {
      const data = await request(api("/fs/pick"));
      if (data && data.path) {
        const idx = data.path.lastIndexOf(/[\/]/.source);
        const dir = idx > 0 ? data.path.slice(0, idx) : data.path;
        $("cmp-file").value = dir;
      }
    } catch (_) { alert("无法打开目录选择器，请手动输入目录路径"); }
  }

  async function cmpRefreshSerialStatus() {
    const session = cmpCurrentSession();
    const el = $("cmp-rt-status");
    if (!session) {
      el.textContent = "请选择实时页面";
      el.classList.remove("on");
      $("cmp-src-realtime-status").textContent = "无页面";
      $("cmp-run-realtime").textContent = "开始解析";
      return;
    }
    const running = session.state === "running" || session.state === "starting";
    const identity = session.port_identity || {};
    if (running) {
      el.textContent = (session.module || "cco").toUpperCase() + " · " +
        (identity.label || session.port || "-") + " · " +
        ((session.serial_config || {}).baudrate || session.baudrate || "-") + " · 采集中";
      el.classList.add("on");
      $("cmp-src-realtime-status").textContent = "运行中";
      $("cmp-run-realtime").textContent = "重新解析";
    } else {
      el.textContent = (session.module || "cco").toUpperCase() + " 页面串口未运行";
      el.classList.remove("on");
      $("cmp-src-realtime-status").textContent = "空闲";
      $("cmp-run-realtime").textContent = "开始解析";
    }
  }

  function cmpBind() {
    document.querySelectorAll(".ms-tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        document.querySelectorAll(".ms-tab").forEach(function (item) {
          item.classList.toggle("active", item === tab);
        });
        const name = tab.dataset.tab;
        $("ms-tab-live").hidden = name !== "live";
        $("ms-tab-compare").hidden = name !== "compare";
        $("ms-tab-simcon").hidden = name !== "simcon";
        if (name === "live") cmpStopRealtime();
        if (name === "compare") {
          cmpRefreshSessions();
          cmpRefreshSerialStatus();
        }
        if (name === "simcon") simconRefreshStatus();
      });
    });

    document.querySelectorAll(".cmp-srccard").forEach(function (card) {
      card.addEventListener("click", function () {
        cmpSetSource(card.dataset.src);
        if (card.dataset.src === "realtime") cmpRefreshSerialStatus();
      });
      card.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          cmpSetSource(card.dataset.src);
        }
      });
    });

    document.querySelectorAll(".cmp-modseg-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        if (cmp.source === "realtime") return;
        cmp.module = button.dataset.mod;
        cmpSyncModuleButtons();
      });
    });

    $("cmp-session").addEventListener("change", function () {
      cmp.sessionId = $("cmp-session").value || null;
      const session = cmpCurrentSession();
      if (session && session.module) {
        cmp.module = session.module;
        cmpSyncModuleButtons();
      }
      cmpRefreshSerialStatus();
      if (cmp.source === "realtime" && cmp.realtimeTimer) cmpScan();
    });

    $("cmp-pick-file").addEventListener("click", cmpPickFile);
    $("cmp-pick-dir").addEventListener("click", cmpPickDir);
    $("cmp-run-file").addEventListener("click", cmpScan);
    $("cmp-file").addEventListener("keydown", function (event) {
      if (event.key === "Enter") cmpScan();
    });
    $("cmp-run-realtime").addEventListener("click", cmpScan);

    cmp.source = "realtime";
    cmpSetSource("realtime");
    cmpRefreshSessions();
  }
  // ========== 模拟集中器（第三页签）==========
  const simcon = { open: false, port: null, portDetails: new Map() };

  async function simconFetch(url, options) {
    return request(url, options);
  }

  function simconApplyPortDetail(detail) {
    if (!detail) return;
    if (detail.baudrate) setSelectValue("simcon-baud", detail.baudrate);
    if (detail.parity) setSelectValue("simcon-parity", detail.parity);
    if (detail.bytesize) setSelectValue("simcon-bytesize", detail.bytesize);
    if (detail.stopbits) setSelectValue("simcon-stopbits", detail.stopbits);
  }

  async function simconRefreshPorts() {
    try {
      const data = await simconFetch(api("/simcon/ports"));
      const details = data.port_details || (data.ports || []).map(function (device) {
        return { device: device, label: "", online: true };
      });
      const sel = $("simcon-port");
      const prev = sel.value;
      simcon.portDetails = new Map();
      sel.innerHTML = "";
      details.forEach(function (detail) {
        const device = String(detail.device || "");
        if (!device) return;
        simcon.portDetails.set(device, detail);
        const opt = document.createElement("option");
        opt.value = device;
        opt.disabled = detail.online === false || detail.enabled === false;
        const label = detail.label ? detail.label + " · " : "";
        const com = detail.windows_com ? " [" + detail.windows_com + "]" : "";
        const offline = detail.online === false ? "（离线）" : "";
        opt.textContent = label + device + com + offline;
        sel.appendChild(opt);
      });
      if (prev && [...sel.options].some((option) => option.value === prev)) {
        sel.value = prev;
      }
      simconApplyPortDetail(simcon.portDetails.get(sel.value));
    } catch (err) {
      console.error("simconRefreshPorts:", err);
    }
  }

  async function simconRefreshStatus() {
    try {
      const s = await simconFetch(api("/simcon/status"));
      simcon.open = s.open;
      simcon.port = s.port;
      const st = $("simcon-status");
      if (s.open) {
        st.textContent = `已连接 ${s.port} · 待处理 ${s.pending_frames} 帧`;
        st.className = "simcon-status on";
        $("simcon-open").disabled = true;
        $("simcon-close").disabled = false;
      } else {
        st.textContent = "未连接";
        st.className = "simcon-status";
        $("simcon-open").disabled = false;
        $("simcon-close").disabled = true;
      }
    } catch (err) {
      console.error("simconRefreshStatus:", err);
    }
  }

  async function simconRefreshRules() {
    const box = $("simcon-rule-list");
    try {
      const data = await simconFetch(api("/simcon/responders"));
      const rules = data.rules || [];
      if (!rules.length) {
        box.innerHTML = '<div class="simcon-empty">（无应答规则）</div>';
        return;
      }
      box.innerHTML = "";
      for (const r of rules) {
        const item = document.createElement("div");
        item.className = "simcon-rule";
        const badge = r.builtin === false ? "覆盖" : "内置";
        item.innerHTML =
          `<div class="simcon-rule-id">${escapeHtml(r.id)} <span class="simcon-rule-badge">${badge}</span></div>` +
          `<div class="simcon-rule-detail">match: ${escapeHtml(JSON.stringify(r.match))}</div>` +
          `<div class="simcon-rule-detail">reply: ${escapeHtml(JSON.stringify(r.reply))}</div>`;
        box.appendChild(item);
      }
    } catch (err) {
      box.innerHTML = `<div class="simcon-empty">规则加载失败：${escapeHtml(err.message)}</div>`;
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  async function simconOpen() {
    const port = $("simcon-port").value;
    const detail = simcon.portDetails.get(port) || {};
    const baud = parseInt($("simcon-baud").value, 10);
    const parity = $("simcon-parity").value;
    const bytesize = parseInt($("simcon-bytesize").value, 10);
    const stopbits = parseInt($("simcon-stopbits").value, 10);
    if (!port) { alert("请先选择串口"); return; }
    try {
      const r = await simconFetch(api("/simcon/open"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          port, mapping_id: detail.mapping_id || undefined,
          baudrate: baud, bytesize, parity, stopbits,
        }),
      });
      simcon.open = r.open;
      await simconRefreshStatus();
    } catch (err) {
      alert("串口打开失败：" + err.message);
    }
  }

  async function simconClose() {
    try {
      await simconFetch(api("/simcon/close"), { method: "POST" });
      await simconRefreshStatus();
    } catch (err) {
      alert("关闭失败：" + err.message);
    }
  }

  async function simconRunTask() {
    let task;
    try {
      task = JSON.parse($("simcon-task-input").value);
    } catch (err) {
      alert("任务 JSON 解析失败：" + err.message);
      return;
    }
    const resultBox = $("simcon-result");
    resultBox.hidden = false;
    resultBox.innerHTML = '<div class="simcon-running">执行中…</div>';
    try {
      const out = await simconFetch(api("/simcon/verify"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(task),
      });
      renderSimconResult(out);
    } catch (err) {
      resultBox.innerHTML = `<div class="simcon-err">执行失败：${escapeHtml(err.message)}</div>`;
    }
  }

  function renderSimconResult(out) {
    const box = $("simcon-result");
    const sm = out.summary || { total: 0, pass: 0, fail: 0, verdict: "fail" };
    const verdict = sm.verdict === "pass" ? "通过" : "失败";
    const verdictCls = sm.verdict === "pass" ? "pass" : "fail";
    const stepsHtml = (out.steps || []).map((s) => {
      const ok = s.result === "pass";
      const mark = ok ? "✓" : "✗";
      const cls = ok ? "pass" : "fail";
      let detail = "";
      if (s.sent_hex) detail += `<div class="simcon-step-detail">sent: ${escapeHtml(s.sent_hex)}</div>`;
      if (s.matched) detail += `<div class="simcon-step-detail">recv: ${escapeHtml(s.matched)}</div>`;
      if (s.reason) detail += `<div class="simcon-step-detail">reason: ${escapeHtml(s.reason)}</div>`;
      return `<div class="simcon-step ${cls}"><span class="simcon-step-mark">${mark}</span>` +
             `<span class="simcon-step-name">${escapeHtml(s.name || `步骤 ${s.index + 1}`)}</span></div>${detail}`;
    }).join("");
    box.innerHTML =
      `<div class="simcon-verdict ${verdictCls}">结论：${verdict}（${sm.pass} 通过 / ${sm.fail} 失败 / ${sm.total} 总）</div>` +
      `<div class="simcon-steps">${stepsHtml || '<div class="simcon-empty">（无步骤）</div>'}</div>`;
  }

  function simconBind() {
    $("simcon-open").addEventListener("click", simconOpen);
    $("simcon-close").addEventListener("click", simconClose);
    $("simcon-run-task").addEventListener("click", simconRunTask);
    $("simcon-port").addEventListener("change", function () {
      simconApplyPortDetail(simcon.portDetails.get($("simcon-port").value));
    });
    $("simcon-refresh-ports").addEventListener("click", () => {
      simconRefreshPorts();
      simconRefreshStatus();
    });
    simconRefreshPorts();
    simconRefreshRules();
    simconRefreshStatus();
  }

  function boot() {
    bind();
    cmpBind();
    simconBind();
    refreshPorts().then(ensureDefaultSession);
    setRefreshSpeed(DEFAULT_REFRESH_SPEED);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();