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
      // 启动/停止切换按钮：根据状态更新文本与样式
      updateToggleButton(st.state);
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
  function updateToggleButton(state) {
    const btn = $("ms-toggle");
    const running = state === "running" || state === "starting";
    btn.textContent = running ? "停止" : "启动";
    btn.classList.toggle("secondary-button", running);
    btn.classList.toggle("primary-button", !running);
    btn.disabled = false;
  }

  async function toggleSerial() {
    const port = $("ms-port-select").value;
    if (!port) { alert("请先选择串口"); return; }
    const btn = $("ms-toggle");
    btn.disabled = true; // 防连点
    try {
      // 根据当前按钮状态决定启动或停止
      const isStop = btn.textContent === "停止";
      if (isStop) {
        await request("/api/module-serial/stop", { method: "POST" });
      } else {
        const baud = parseInt($("ms-baud-select").value, 10);
        await request("/api/module-serial/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ port, baudrate: baud, bytesize: 8, parity: "N", stopbits: 1 }),
        });
        lastSeq = -1; // 重新拉取
        $("ms-log-box").innerHTML = "";
      }
      await refreshStatus();
    } catch (err) {
      alert((isStop ? "停止失败：" : "启动失败：") + err.message);
    } finally {
      btn.disabled = false;
    }
  }


  // ---------- 文件选择：浏览器原生 file 选择框 + 上传后端 ----------
  async function pickFile() {
    $("ms-file-input").click();  // 触发浏览器原生文件选择框（能选 Windows/WSL 路径文件）
  }

  async function onFilePicked(event) {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";  // 允许重复选同一文件
    if (!file) return;
    const btn = $("ms-pick");
    btn.disabled = true;
    btn.textContent = "上传中…";
    try {
      // FileReader 读 base64，POST 上传到后端保存，返回可烧录路径
      const base64 = await readFileAsBase64(file);
      const data = await request("/api/module-serial/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, base64: base64 }),
      });
      if (data && data.path) {
        $("ms-bin").value = data.path;
        alert("固件已上传：" + data.path);
      } else {
        throw new Error("上传未返回路径");
      }
    } catch (err) {
      alert("文件选择失败：" + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "选择文件…";
    }
  }

  function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        // 去掉 data:...;base64, 前缀
        const result = String(reader.result || "");
        const idx = result.indexOf(",");
        resolve(idx >= 0 ? result.slice(idx + 1) : result);
      };
      reader.onerror = () => reject(reader.error || new Error("读取文件失败"));
      reader.readAsDataURL(file);
    });
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
    $("ms-toggle").addEventListener("click", toggleSerial);
    $("ms-pick").addEventListener("click", pickFile);
    $("ms-flash").addEventListener("click", startFlash);
    $("ms-clear").addEventListener("click", () => {
      $("ms-log-box").innerHTML = "";
      $("ms-lines").textContent = "0";
    });

    // 浏览器原生文件选择框：选中后上传
    $("ms-file-input").addEventListener("change", onFilePicked);
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
