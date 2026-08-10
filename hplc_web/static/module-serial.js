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
        div.innerHTML = `<span class="t">[${line.ts}]</span> [${line.dir}] <span class="${cls}">${escapeHtml(line.text)}</span>`;
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

  async function pickFile() {
    try {
      const data = await request("/api/fs/pick");
      if (data && data.path) $("ms-bin").value = data.path;
    } catch (err) {
      alert("选择文件失败：" + err.message);
    }
  }

  async function startFlash() {
    const binPath = $("ms-bin").value.trim();
    if (!binPath) { alert("请先选择 .bin 固件路径"); return; }
    const slot = parseInt($("ms-slot").value || "0", 10);
    const planText = $("ms-baud-plan").value.trim();
    const baudPlan = planText ? planText.split(/[,，\s]+/).map(Number).filter((n) => !isNaN(n) && n > 0) : null;
    const noReboot = $("ms-no-reboot").checked;
    if (!confirm(`确认向 ${$("ms-port-select").value} 烧录 ${binPath}？\nslot=${slot} 波特率方案=[${(baudPlan || []).join(",")}]`)) {
      return;
    }
    $("ms-progress-bar").style.width = "0%";
    $("ms-progress-text").textContent = "开始烧录…";
    try {
      await request("/api/module-serial/flash", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bin_path: binPath, slot, baud_plan: baudPlan, no_reboot_after: noReboot }),
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
