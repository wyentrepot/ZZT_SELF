/* AI 闭环研发验证工作台 —— 页签注册表 + 路由 + 主题切换（纯静态，零构建） */
(function () {
  "use strict";

  /* ========== 页签注册表 ========== */
  const PAGES = [
    { id: "workbench", title: "验证工作台", src: "/static/workbench.html" },
    { id: "module", title: "模块日志", src: "/static/pages/module-serial/module-serial.html" },
    { id: "listener", title: "侦听台", src: "/static/pages/listener/index.html" },
  ];

  const tabsEl = document.getElementById("wb-tabs");
  const frame = document.getElementById("wb-frame");
  const statusEl = document.getElementById("wb-status");
  const themesEl = document.getElementById("wbThemes");
  let current = PAGES[0].id;

  /* ========== 子应用挂载状态 ========== */
  fetch("/api/platform-version")
    .then(function (r) { return r.json(); })
    .then(function (info) {
      const parts = [];
      parts.push("module_log: " + (info.module_log_mounted ? "✓" : "✗"));
      parts.push("listener: " + (info.listener_mounted ? "✓" : "✗"));
      statusEl.textContent = "统一集成程序 · " + parts.join(" · ");
    })
    .catch(function () { /* 忽略状态刷新失败 */ });

  /* ========== 页签渲染 ========== */
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

  /* ========== 主题切换 ========== */
  const THEMES = {
    "theme-deepblue": "深蓝科技",
    "theme-emerald": "暗色翡翠",
    "theme-charcoal": "炭黑灰金",
    "theme-indigo": "靛蓝玻璃",
  };

  function switchTheme(theme) {
    if (!THEMES[theme]) return;

    // 在 html 根元素上设置主题 class
    document.documentElement.className = theme;

    // 持久化到 localStorage
    try { localStorage.setItem("wb-theme", theme); } catch (e) {}

    // 更新按钮状态
    var dots = document.querySelectorAll(".theme-dot");
    for (var i = 0; i < dots.length; i++) {
      dots[i].classList.toggle("active", dots[i].dataset.theme === theme);
    }

    // 广播主题到 iframe（同源可直接访问）
    try {
      if (frame && frame.contentWindow) {
        frame.contentWindow.postMessage({
          type: "wb-theme-change",
          theme: theme
        }, "*");
      }
    } catch (e) {}
  }

  // 绑定主题按钮事件
  if (themesEl) {
    var dots = themesEl.querySelectorAll(".theme-dot");
    for (var i = 0; i < dots.length; i++) {
      dots[i].addEventListener("click", function () {
        switchTheme(this.dataset.theme);
      });
    }
  }

  // 恢复上次保存的主题
  try {
    var saved = localStorage.getItem("wb-theme");
    if (saved && THEMES[saved]) {
      switchTheme(saved);
    }
  } catch (e) {}

  /* ========== 监听 iframe 加载完成后的主题同步 ========== */
  frame.addEventListener("load", function () {
    var currentTheme = document.documentElement.className;
    if (currentTheme && THEMES[currentTheme]) {
      try {
        frame.contentWindow.postMessage({
          type: "wb-theme-change",
          theme: currentTheme
        }, "*");
      } catch (e) {}
    }
  });

  /* ========== 初始化 ========== */
  renderTabs();
  frame.src = PAGES[0].src;
})();