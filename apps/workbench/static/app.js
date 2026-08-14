/* AI 闭环研发验证工作台 —— 页签注册表 + 路由（纯静态，零构建） */
(function () {
  "use strict";

  // 页签注册表（详细设计 §5.1）
  // 方案①（ADR-18）：页面物理复制到 /static/pages/{pkg}/，API 前缀已改写，
  // 不再 iframe 指向子应用原页面（子应用前端 /api/ 绝对路径与代理前缀对齐）。
  const PAGES = [
    { id: "workbench", title: "验证工作台", src: "/static/workbench.html" },
    { id: "module", title: "模块日志", src: "/static/pages/module-serial/module-serial.html" },
    { id: "listener", title: "侦听台", src: "/static/pages/listener/index.html" },
  ];

  const tabsEl = document.getElementById("wb-tabs");
  const frame = document.getElementById("wb-frame");
  const statusEl = document.getElementById("wb-status");
  let current = PAGES[0].id;

  // 侦听台挂载状态（后端 /api/platform-version 报告）
  fetch("/api/platform-version")
    .then(function (r) { return r.json(); })
    .then(function (info) {
      const parts = [];
      parts.push("module_log: " + (info.module_log_mounted ? "✓" : "✗"));
      parts.push("listener: " + (info.listener_mounted ? "✓" : "✗"));
      statusEl.textContent = "统一集成程序 · " + parts.join(" · ");
    })
    .catch(function () { /* 忽略状态刷新失败 */ });

  function renderTabs() {
    tabsEl.innerHTML = "";
    PAGES.forEach(function (p) {
      const btn = document.createElement("button");
      btn.className = "wb-tab" + (p.id === current ? " active" : "");
      btn.textContent = p.title;
      btn.dataset.id = p.id;
      btn.addEventListener("click", function () { switchTab(p.id); });
      tabsEl.appendChild(btn);
    });
  }

  function switchTab(id) {
    const page = PAGES.find(function (p) { return p.id === id; });
    if (!page) return;
    current = id;
    renderTabs();
    frame.src = page.src;
  }

  renderTabs();
  frame.src = PAGES[0].src;
})();
