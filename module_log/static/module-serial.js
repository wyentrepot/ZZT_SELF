// 模块日志/烧录串口独立页面逻辑（ModuleSerialService · 双通道）
// 与侦听台 app.js 完全独立：新标签页运行，800ms 增量轮询两路日志 + 状态。
// 双通道：cco / sta 固定两路，各自独立串口/启动/停止/烧录/日志；
// 底部发送框选择目标通道，回车即发送当前行。
(function () {
  "use strict";

  // 统一选择器：以 . / # / [ 开头视为 CSS 选择器（querySelector），否则视为元素 id。
  // 单通道旧版只支持 getElementById；双通道改造后大量使用类/属性选择器，
  // 若仍用 getElementById 会返回 null 导致 bind() 抛错、串口下拉无法填充。
  const $ = (sel) =>
    sel.startsWith(".") || sel.startsWith("#") || sel.startsWith("[")
      ? document.querySelector(sel)
      : document.getElementById(sel);
  const CHANNELS = ["cco", "sta"];
  // 日志刷新速度三档：快/中/慢（毫秒）。默认中。
  const REFRESH_SPEED_MS = { fast: 100, medium: 500, slow: 800 };
  const DEFAULT_REFRESH_SPEED = "medium";
  // 每路独立增量游标
  const lastSeq = { cco: -1, sta: -1 };
  let pollTimer = null;
  const portRefreshing = { cco: false, sta: false };
  let pickerChannel = "cco"; // 文件选择当前归属通道

  async function request(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const body = await resp.json();
        detail = body.detail || detail;
      } catch (_) { /* ignore */ }
      throw new Error(detail);
    }
    return resp.json();
  }

  // ---------- 状态 ----------
  async function refreshStatus() {
    try {
      const st = await request("/api/module-serial/status");
      $("ms-server-state").textContent = "已连接";
      const chs = st.channels || {};
      CHANNELS.forEach((ch) => {
        const c = chs[ch] || {};
        $(`ms-status-${ch}`).textContent =
          `${c.state} · ${c.port || "-"} · ${c.baudrate || "-"}` +
          (c.flash && c.flash.flashing ? ` · 烧录 ${c.flash.packet}/${c.flash.total || "?"}` : "");
        updateToggleButton(ch, c.state);
        $(`.ms-flash[data-channel="${ch}"]`).disabled = !!(c.flash && c.flash.flashing);
        // 进度
        const bar = $(`.ms-progress-bar[data-channel="${ch}"]`);
        const txt = $(`.ms-progress-text[data-channel="${ch}"]`);
        const total = c.flash && c.flash.total ? c.flash.total : 0;
        const pkt = c.flash ? c.flash.packet : 0;
        if (c.flash && c.flash.flashing && total > 0) {
          const pct = Math.round((pkt * 100) / total);
          bar.style.width = pct + "%";
          txt.textContent = `传输中 ${pkt}/${total} (${pct}%)`;
        } else if (c.flash && c.flash.phase === "done") {
          bar.style.width = "100%";
          txt.textContent = "烧录完成";
        } else if (c.flash && c.flash.phase === "error") {
          bar.style.width = "0%";
          txt.textContent = "烧录失败：" + (c.flash.message || "");
        } else {
          bar.style.width = "0%";
          txt.textContent = c.flash && c.flash.message ? c.flash.message : "未开始";
        }
      });
      return st;
    } catch (err) {
      $("ms-server-state").textContent = "连接失败：" + err.message;
      return null;
    }
  }

  // ---------- 日志增量轮询 ----------
  async function pollLogs() {
    for (const ch of CHANNELS) {
      try {
        const data = await request(`/api/module-serial/logs?after=${lastSeq[ch]}&channel=${ch}`);
        if (!data.lines || data.lines.length === 0) continue;
        const box = $(`.ms-log-box[data-channel="${ch}"]`);
        const autoscroll = $(`.ms-autoscroll[data-channel="${ch}"]`).checked;
        for (const line of data.lines) {
          const cls = line.dir === "RX" ? "rx" : line.dir === "TX" ? "tx" : "ev";
          const div = document.createElement("div");
          const text = String(line.text).replace(/\r?\n/g, "").replace(/\r/g, "");
          div.innerHTML = `<span class="t">[${line.ts}]</span> [${line.dir}] <span class="${cls}">${escapeHtml(text)}</span>`;
          box.appendChild(div);
        }
        if (autoscroll) box.scrollTop = box.scrollHeight;
        if (data.last_seq >= 0) lastSeq[ch] = data.last_seq;
      } catch (_) { /* 下次再试 */ }
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // ---------- 端口 ----------
  async function refreshPorts(ch) {
    if (portRefreshing[ch]) return;
    portRefreshing[ch] = true;
    try {
      const data = await request("/api/module-serial/ports");
      const sel = $(`#ms-port-${ch}`);
      const current = sel.value;
      sel.replaceChildren();
      if (!data.ports || data.ports.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "（未发现串口）";
        sel.appendChild(opt);
        return;
      }
      for (const p of data.ports) {
        const opt = document.createElement("option");
        opt.value = p;
        opt.textContent = p;
        sel.appendChild(opt);
      }
      if (data.ports.includes(current)) sel.value = current;
    } catch (err) {
      console.error(`refreshPorts(${ch}):`, err);
    } finally {
      portRefreshing[ch] = false;
    }
  }

  // ---------- 动作 ----------
  function updateToggleButton(ch, state) {
    const btn = $(`.ms-toggle[data-channel="${ch}"]`);
    const running = state === "running" || state === "starting";
    btn.textContent = running ? "停止" : "启动";
    btn.classList.toggle("secondary-button", running);
    btn.classList.toggle("primary-button", !running);
    btn.disabled = false;
    // 串口运行中，端口/波特率不可更改；停止后才能重新选择
    $(`#ms-port-${ch}`).disabled = running;
    $(`#ms-baud-${ch}`).disabled = running;
  }

  async function toggleSerial(ch) {
    const port = $(`#ms-port-${ch}`).value;
    if (!port) { alert(`请先选择 ${ch.toUpperCase()} 串口`); return; }
    const btn = $(`.ms-toggle[data-channel="${ch}"]`);
    btn.disabled = true;
    try {
      const isStop = btn.textContent === "停止";
      if (isStop) {
        await request("/api/module-serial/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ channel: ch }),
        });
      } else {
        const baud = parseInt($(`#ms-baud-${ch}`).value, 10);
        await request("/api/module-serial/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ port, baudrate: baud, bytesize: 8, parity: "N", stopbits: 1, channel: ch }),
        });
        lastSeq[ch] = -1;
        $(`.ms-log-box[data-channel="${ch}"]`).innerHTML = "";
      }
      await refreshStatus();
    } catch (err) {
      alert((isStop ? "停止失败：" : "启动失败：") + err.message);
    } finally {
      btn.disabled = false;
    }
  }
  // ---------- 文件选择：先试系统原生对话框（/api/fs/pick）----------
  async function pickFile(ch) {
    pickerChannel = ch;
    const btn = $(`.ms-pick[data-channel="${ch}"]`);
    btn.disabled = true;
    btn.textContent = "选择中…";
    try {
      const data = await request("/api/fs/pick");
      const path = data && data.path;
      if (path) {
        $(`#ms-bin-${ch}`).value = path;
      } else {
        pickerOpen();
      }
    } catch (err) {
      pickerOpen();
    } finally {
      btn.disabled = false;
      btn.textContent = "选择…";
    }
  }

  // ---------- 内置目录浏览器兜底 ----------
  const picker = {
    overlay: null, close: null, cancel: null, up: null, path: null,
    roots: null, list: null, selected: null, confirm: null,
    currentDir: null, chosenFile: null,
  };

  function formatFileSize(bytes) {
    if (bytes == null) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  }

  function pickerClose() { picker.overlay.hidden = true; }

  function pickerOpen() {
    picker.overlay.hidden = false;
    pickerRoots();
  }

  async function pickerRoots() {
    try {
      const data = await request("/api/fs/roots");
      picker.roots.textContent = "";
      data.roots.forEach((root) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "picker-root-button";
        button.textContent = root.name;
        button.addEventListener("click", () => pickerList(root.path));
        picker.roots.appendChild(button);
      });
    } catch (error) {
      pickerList(picker.currentDir || "");
    }
  }

  async function pickerList(path) {
    if (!path) return;
    picker.path.textContent = path;
    picker.path.title = path;
    picker.currentDir = path;
    picker.list.innerHTML = '<div class="file-picker-empty">加载中…</div>';
    picker.chosenFile = null;
    picker.confirm.disabled = true;
    picker.selected.textContent = "";
    try {
      const data = await request(`/api/fs/list?path=${encodeURIComponent(path)}`);
      picker.up.disabled = !data.parent;
      picker.list.textContent = "";
      if (!data.dirs.length && !data.files.length) {
        const empty = document.createElement("div");
        empty.className = "file-picker-empty";
        empty.textContent = "该目录没有子目录或文件";
        picker.list.appendChild(empty);
        return;
      }
      data.dirs.forEach((dir) => picker.list.appendChild(pickerDirRow(dir)));
      data.files.forEach((file) => picker.list.appendChild(pickerFileRow(file)));
    } catch (error) {
      picker.list.textContent = "";
      const empty = document.createElement("div");
      empty.className = "file-picker-empty";
      empty.textContent = error.message;
      picker.list.appendChild(empty);
      picker.up.disabled = true;
    }
  }

  function pickerDirRow(dir) {
    const row = document.createElement("div");
    row.className = "file-picker-row file-picker-dir";
    row.innerHTML = '<span class="picker-icon">📁</span>';
    const name = document.createElement("span");
    name.className = "picker-name";
    name.textContent = dir.name;
    row.appendChild(name);
    row.addEventListener("click", () => pickerList(dir.path));
    row.addEventListener("dblclick", () => pickerList(dir.path));
    return row;
  }

  function pickerFileRow(file) {
    const row = document.createElement("div");
    row.className = "file-picker-row file-picker-file";
    row.innerHTML = '<span class="picker-icon">📄</span>';
    const name = document.createElement("span");
    name.className = "picker-name";
    name.textContent = file.name;
    const size = document.createElement("span");
    size.className = "picker-size";
    size.textContent = formatFileSize(file.size);
    row.appendChild(name);
    row.appendChild(size);
    const select = () => {
      picker.list.querySelectorAll(".file-picker-row.selected").forEach((node) => {
        node.classList.remove("selected");
      });
      row.classList.add("selected");
      picker.chosenFile = file.path;
      picker.selected.textContent = file.path;
      picker.confirm.disabled = false;
    };
    row.addEventListener("click", select);
    row.addEventListener("dblclick", () => { select(); pickerConfirm(); });
    return row;
  }

  function pickerConfirm() {
    if (!picker.chosenFile) return;
    $(`#ms-bin-${pickerChannel}`).value = picker.chosenFile;
    pickerClose();
  }
  async function startFlash(ch) {
    const binPath = $(`#ms-bin-${ch}`).value.trim();
    if (!binPath) { alert(`请先选择 ${ch.toUpperCase()} 固件 .bin 路径`); return; }
    const slot = parseInt($(`#ms-slot-${ch}`).value || "0", 10);
    const noReboot = $(`.ms-no-reboot[data-channel="${ch}"]`).checked;
    if (!confirm(`确认向 ${ch.toUpperCase()} (${$(`#ms-port-${ch}`).value}) 烧录 ${binPath}？\nimage=${slot}`)) {
      return;
    }
    const bar = $(`.ms-progress-bar[data-channel="${ch}"]`);
    const txt = $(`.ms-progress-text[data-channel="${ch}"]`);
    bar.style.width = "0%";
    txt.textContent = "开始烧录…";
    try {
      await request("/api/module-serial/flash", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bin_path: binPath, slot, baud_plan: null, no_reboot_after: noReboot, channel: ch }),
      });
    } catch (err) {
      txt.textContent = "烧录失败：" + err.message;
    }
  }

  // ---------- 底部发送框：回车即发送当前行 ----------
  // 任何发送数据默认自动携带换行（append_newline，默认开，可勾选关闭）。
  // “换行”按钮只发送一个换行符；直接按换行（空输入）也是发送一个换行。
  function sendText() {
    const text = $("ms-send-text").value;
    const ch = $("ms-send-channel").value;
    const appendNl = $("ms-send-append-nl").checked;
    // 空内容也发送：空输入 + 自动补换行 = 发送一个换行
    request("/api/module-serial/write_text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, channel: ch, append_newline: appendNl }),
    })
      .then(() => { $("ms-send-text").value = ""; })
      .catch((err) => alert("发送失败：" + err.message));
  }

  // 发送一个换行符（“携带换行符作为一个按键”）
  function sendNewline() {
    const ch = $("ms-send-channel").value;
    request("/api/module-serial/write_text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "", channel: ch, append_newline: true }),
    })
      .catch((err) => alert("发送失败：" + err.message));
  }

  // ---------- 绑定 ----------
  function bind() {
    CHANNELS.forEach((ch) => {
      $(`.ms-refresh[data-channel="${ch}"]`).addEventListener("click", () => refreshPorts(ch));
      $(`.ms-toggle[data-channel="${ch}"]`).addEventListener("click", () => toggleSerial(ch));
      $(`.ms-pick[data-channel="${ch}"]`).addEventListener("click", () => pickFile(ch));
      $(`.ms-flash[data-channel="${ch}"]`).addEventListener("click", () => startFlash(ch));
      $(`.ms-clear[data-channel="${ch}"]`).addEventListener("click", () => {
        $(`.ms-log-box[data-channel="${ch}"]`).innerHTML = "";
      });
      refreshPorts(ch);
    });

    // 发送框：回车发送（Shift+Enter 换行）；按钮发送；清空；隐藏/显示
    $("ms-send-text").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendText();
      }
    });
    $("ms-send-btn").addEventListener("click", sendText);
    $("ms-send-newline").addEventListener("click", sendNewline);
    $("ms-send-clear").addEventListener("click", () => { $("ms-send-text").value = ""; });
    $("ms-sender-hide").addEventListener("click", () => {
      $("ms-sender").hidden = true;
      $("ms-sender-showbar").hidden = false;
    });
    $("ms-sender-show").addEventListener("click", () => {
      $("ms-sender").hidden = false;
      $("ms-sender-showbar").hidden = true;
    });

    // 日志刷新速度切换
    $("ms-refresh-speed").addEventListener("change", (e) => {
      setRefreshSpeed(e.target.value);
    });

    // 内置文件浏览器事件绑定（兜底）
    picker.overlay = $("ms-file-picker");
    picker.close = $("ms-picker-close");
    picker.cancel = $("ms-picker-cancel");
    picker.up = $("ms-picker-up");
    picker.path = $("ms-picker-path");
    picker.roots = $("ms-picker-roots");
    picker.list = $("ms-picker-list");
    picker.selected = $("ms-picker-selected");
    picker.confirm = $("ms-picker-confirm");
    picker.close.addEventListener("click", pickerClose);
    picker.cancel.addEventListener("click", pickerClose);
    picker.overlay.addEventListener("click", (event) => {
      if (event.target === picker.overlay) pickerClose();
    });
    picker.up.addEventListener("click", () => {
      if (!picker.currentDir) return;
      request(`/api/fs/list?path=${encodeURIComponent(picker.currentDir)}`)
        .then((data) => { if (data.parent) pickerList(data.parent); })
        .catch(() => {});
    });
    picker.confirm.addEventListener("click", pickerConfirm);
  }

  // ---------- 刷新速度：变更时重建轮询定时器 ----------
  function startPolling(ms) {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    pollTimer = setInterval(() => {
      refreshStatus();
      pollLogs();
    }, ms);
    window.__pollIntervalMs = ms;
  }

  function setRefreshSpeed(speed) {
    const ms = REFRESH_SPEED_MS[speed] || REFRESH_SPEED_MS[DEFAULT_REFRESH_SPEED];
    startPolling(ms);
    return ms;
  }

  // ---------- 对照解析页 ----------
  const cmp = {
    source: "file", module: "cco", events: [], lines: [],
    selectedEvent: -1, selectedLine: -1, realtimeTimer: null,
  };
  const CMP_ICONS = { join: "🛜", collect: "📊", send: "⬆️", beacon: "📡", state: "⚙️", flash: "⚡", error: "⚠️", other: "🔍" };
  const CMP_LEVELS = { info: "info", warn: "warn", error: "error" };

  function cmpSetMeta(text) { $("cmp-meta").textContent = text || ""; }

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
        data = await request(`/api/loghooks/scan?path=${encodeURIComponent(path)}&module=${module}&limit=8000`);
      } else {
        data = await request(`/api/loghooks/realtime?channel=${module}&limit=8000`);
      }
      cmp.events = data.events || [];
      // 右侧全量日志行：直接用后端返回的 lines（带 file/line/raw）
      cmp.lines = (data.lines || []).map((ln) => ({
        key: (ln.file || "") + ":" + ln.line,
        line: ln.line,
        file: ln.file || "",
        raw: ln.raw,
      }));
      // 给事件补充 __key（用于点击跳转到对应日志行）
      cmp.events.forEach((ev, i) => {
        ev.__i = i;
        ev.__key = (ev.file || "") + ":" + ev.line;
      });
      cmp.selectedEvent = -1;
      cmp.selectedLine = -1;
      cmpRenderEvents();
      cmpRenderLog();
      // 统计条
      cmpUpdateStats(data, module);
      cmpSetMeta(data.module ? `${module.toUpperCase()} 来源` : "");
      if (cmp.source === "realtime") cmpStartRealtime();
    } catch (err) {
      cmpSetMeta("解析失败");
      alert("解析失败：" + err.message);
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
    cmp.realtimeTimer = setInterval(() => {
      const module = cmp.module;
      request(`/api/loghooks/realtime?channel=${module}&limit=8000`)
        .then((data) => {
          cmp.events = data.events || [];
          cmpRenderEvents();
          // 实时模式下日志也在增长，更新全量行 + 虚拟滚动
          cmp.lines = (data.lines || []).map((ln) => ({
            key: (ln.file || "") + ":" + ln.line,
            line: ln.line,
            file: ln.file || "",
            raw: ln.raw,
          }));
          cmpRenderLog();
          cmpUpdateStats(data, module);
          cmpSetMeta(`${module.toUpperCase()} 实时`);
        })
        .catch(() => {});
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
      const data = await request("/api/fs/pick");
      if (data && data.path) { $("cmp-file").value = data.path; }
    } catch (_) { alert("无法打开文件选择器，请手动输入路径"); }
  }

  async function cmpPickDir() {
    // 打开目录：复用 fs/pick 选一个文件，取其所在目录；或提示手动输入目录
    try {
      const data = await request("/api/fs/pick");
      if (data && data.path) {
        const idx = data.path.lastIndexOf(/[\/]/.source);
        const dir = idx > 0 ? data.path.slice(0, idx) : data.path;
        $("cmp-file").value = dir;
      }
    } catch (_) { alert("无法打开目录选择器，请手动输入目录路径"); }
  }

  async function cmpRefreshSerialStatus() {
    try {
      const st = await request("/api/module-serial/status");
      const ch = (st.channels && st.channels[cmp.module]) || {};
      const running = ch.state === "running" || ch.state === "starting";
      const el = $("cmp-rt-status");
      if (running) {
        el.textContent = `${cmp.module.toUpperCase()} · ${ch.port || "-"} · ${ch.baudrate || "-"} · 采集中`;
        el.classList.add("on");
        $("cmp-src-realtime-status").textContent = "运行中";
        $("cmp-run-realtime").textContent = "重新解析";
      } else {
        el.textContent = `${cmp.module.toUpperCase()} 串口未运行`;
        el.classList.remove("on");
        $("cmp-src-realtime-status").textContent = "空闲";
        $("cmp-run-realtime").textContent = "开始解析";
      }
    } catch (_) {
      $("cmp-rt-status").textContent = "无法获取串口状态";
      $("cmp-rt-status").classList.remove("on");
    }
  }

  function cmpBind() {
    // 页签切换
    document.querySelectorAll(".ms-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".ms-tab").forEach((t) => t.classList.toggle("active", t === tab));
        const name = tab.dataset.tab;
        $("ms-tab-live").hidden = name !== "live";
        $("ms-tab-compare").hidden = name !== "compare";
        if (name === "live") cmpStopRealtime();
        if (name === "compare") cmpRefreshSerialStatus();
      });
    });

    // 来源卡片（互斥二选一）
    document.querySelectorAll(".cmp-srccard").forEach((card) => {
      card.addEventListener("click", () => {
        cmpSetSource(card.dataset.src);
        if (card.dataset.src === "realtime") cmpRefreshSerialStatus();
      });
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cmpSetSource(card.dataset.src); }
      });
    });

    // 模块分段控件
    document.querySelectorAll(".cmp-modseg-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".cmp-modseg-btn").forEach((b) => b.classList.toggle("active", b === btn));
        cmp.module = btn.dataset.mod;
        cmpRefreshSerialStatus();
        if (cmp.source === "realtime") cmpScan();
      });
    });

    // 文件来源动作
    $("cmp-pick-file").addEventListener("click", cmpPickFile);
    $("cmp-pick-dir").addEventListener("click", cmpPickDir);
    $("cmp-run-file").addEventListener("click", cmpScan);
    $("cmp-file").addEventListener("keydown", (e) => { if (e.key === "Enter") cmpScan(); });

    // 串口来源动作
    $("cmp-run-realtime").addEventListener("click", cmpScan);

    // 默认：实时串口来源
    cmp.source = "realtime";
    cmp.module = "cco";
    cmpSetSource("realtime");
    cmpRefreshSerialStatus();
  }
  function boot() {
    bind();
    cmpBind();
    refreshStatus();
    pollLogs();
    setRefreshSpeed(DEFAULT_REFRESH_SPEED);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();