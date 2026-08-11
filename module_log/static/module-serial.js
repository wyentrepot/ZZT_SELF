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

  function boot() {
    bind();
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