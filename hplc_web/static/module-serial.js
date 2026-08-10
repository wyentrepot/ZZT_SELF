// 模块日志/烧录串口独立页面逻辑（ModuleSerialService）
// 与侦听台 app.js 完全独立：新标签页运行，800ms 增量轮询日志 + 状态。
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  let lastSeq = -1; // 增量轮询游标（-1 = 首次拉全部）
  let pollTimer = null;
  let portRefreshing = false;

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
      $("ms-state").textContent = st.state;
      $("ms-port").textContent = st.port || "-";
      $("ms-baud").textContent = st.baudrate;
      $("ms-logfile").textContent = st.log_file || "-";
      $("ms-lines").textContent = st.lines;
      $("ms-flash-state").textContent = st.flash && st.flash.flashing
        ? `烧录中 ${st.flash.packet}/${st.flash.total || "?"} ${st.flash.message || ""}`
        : (st.flash && st.flash.phase ? st.flash.phase : "-");
      $("ms-server-state").textContent = "已连接";
      // 烧录进行中禁用重复触发
      $("ms-flash").disabled = !!(st.flash && st.flash.flashing);
      // 进度条
      const total = st.flash && st.flash.total ? st.flash.total : 0;
      const pkt = st.flash ? st.flash.packet : 0;
      if (st.flash && st.flash.flashing && total > 0) {
        const pct = Math.round((pkt * 100) / total);
        $("ms-progress-bar").style.width = pct + "%";
        $("ms-progress-text").textContent = `传输中 ${pkt}/${total} (${pct}%)`;
      } else if (st.flash && st.flash.phase === "done") {
        $("ms-progress-bar").style.width = "100%";
        $("ms-progress-text").textContent = "烧录完成";
      } else if (st.flash && st.flash.phase === "error") {
        $("ms-progress-bar").style.width = "0%";
        $("ms-progress-text").textContent = "烧录失败：" + (st.flash.message || "");
      } else {
        $("ms-progress-text").textContent = st.flash && st.flash.message ? st.flash.message : "未开始";
      }
      return st;
    } catch (err) {
      $("ms-server-state").textContent = "连接失败：" + err.message;
      return null;
    }
  }

  // ---------- 日志增量轮询 ----------
  async function pollLogs() {
    try {
      const data = await request(`/api/module-serial/logs?after=${lastSeq}`);
      if (!data.lines || data.lines.length === 0) {
        return;
      }
      const box = $("ms-log-box");
      const autoscroll = $("ms-autoscroll").checked;
      for (const line of data.lines) {
        const cls = line.dir === "RX" ? "rx" : line.dir === "TX" ? "tx" : "ev";
        const div = document.createElement("div");
        // 每行一个独立 div（天然换行）；去掉行尾换行符，避免多余回车字符
        const text = String(line.text).replace(/\r?\n/g, "").replace(/\r/g, "");
        div.innerHTML = `<span class="t">[${line.ts}]</span> [${line.dir}] <span class="${cls}">${escapeHtml(text)}</span>`;
        box.appendChild(div);
      }
      if (autoscroll) box.scrollTop = box.scrollHeight;
      lastSeq = data.last_seq >= 0 ? data.last_seq : lastSeq;
      // 行数显示取 status 更准，这里简单用 DOM 计数
      $("ms-lines").textContent = box.childElementCount;
    } catch (_) { /* 下次再试 */ }
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ---------- 端口 ----------
  async function refreshPorts() {
    if (portRefreshing) return;
    portRefreshing = true;
    try {
      const data = await request("/api/module-serial/ports");
      const sel = $("ms-port-select");
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
      console.error("refreshPorts:", err);
    } finally {
      portRefreshing = false;
    }
  }

  // ---------- 动作 ----------
  async function startSerial() {
    const port = $("ms-port-select").value;
    if (!port) { alert("请先选择串口"); return; }
    const baud = parseInt($("ms-baud-select").value, 10);
    try {
      await request("/api/module-serial/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port, baudrate: baud, bytesize: 8, parity: "N", stopbits: 1 }),
      });
      lastSeq = -1; // 重新拉取
      $("ms-log-box").innerHTML = "";
      refreshStatus();
    } catch (err) {
      alert("启动失败：" + err.message);
    }
  }

  async function stopSerial() {
    try {
      await request("/api/module-serial/stop", { method: "POST" });
      refreshStatus();
    } catch (err) {
      alert("停止失败：" + err.message);
    }
  }

  // ---------- 内置文件浏览器（复用 /api/fs/*，与原侦听台一致）----------
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

  function pickerClose() {
    picker.overlay.hidden = true;
  }

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
    row.addEventListener("dblclick", () => {
      select();
      pickerConfirm();
    });
    return row;
  }

  function pickerConfirm() {
    if (!picker.chosenFile) return;
    $("ms-bin").value = picker.chosenFile;
    pickerClose();
  }

  function pickFile() {
    pickerOpen();
  }

  async function startFlash() {
    const binPath = $("ms-bin").value.trim();
    if (!binPath) { alert("请先选择 .bin 固件路径"); return; }
    const slot = parseInt($("ms-slot").value || "0", 10);
    const noReboot = $("ms-no-reboot").checked;
    if (!confirm(`确认向 ${$("ms-port-select").value} 烧录 ${binPath}？\nimage=${slot} 波特率=当前串口波特率`)) {
      return;
    }
    $("ms-progress-bar").style.width = "0%";
    $("ms-progress-text").textContent = "开始烧录…";
    try {
      await request("/api/module-serial/flash", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bin_path: binPath, slot, baud_plan: null, no_reboot_after: noReboot }),
      });
      // 烧录在线程中执行：这里立即返回，进度靠状态轮询体现
    } catch (err) {
      $("ms-progress-text").textContent = "烧录失败：" + err.message;
    }
  }

  // ---------- 绑定 ----------
  function bind() {
    $("ms-refresh").addEventListener("click", refreshPorts);
    $("ms-start").addEventListener("click", startSerial);
    $("ms-stop").addEventListener("click", stopSerial);
    $("ms-pick").addEventListener("click", pickFile);
    $("ms-flash").addEventListener("click", startFlash);
    $("ms-clear").addEventListener("click", () => {
      $("ms-log-box").innerHTML = "";
      $("ms-lines").textContent = "0";
    });

    // 内置文件浏览器事件绑定
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
        .then((data) => {
          if (data.parent) pickerList(data.parent);
        })
        .catch(() => {});
    });
    picker.confirm.addEventListener("click", pickerConfirm);
  }

  function boot() {
    bind();
    refreshPorts();
    refreshStatus();
    pollLogs();
    pollTimer = setInterval(() => {
      refreshStatus();
      pollLogs();
    }, 800);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
