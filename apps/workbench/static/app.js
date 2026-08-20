/* AI 闭环研发验证工作台 —— 页签注册表 + 保活子页面 + 主题切换（纯静态，零构建） */
(function () {
  "use strict";

  const PAGES = [
    { id: "workbench", title: "验证工作台", src: "/static/workbench.html" },
    { id: "module", title: "模块日志", src: "/static/pages/module-serial/module-serial.html" },
    { id: "listener", title: "侦听台", src: "/static/pages/listener/index.html" },
  ];

  const tabsEl = document.getElementById("wb-tabs");
  const panelsEl = document.getElementById("wb-panels");
  const statusEl = document.getElementById("wb-status");
  const themesEl = document.getElementById("wbThemes");
  const framesByPage = new Map();
  let current = PAGES[0].id;

  fetch("/api/platform-version")
    .then(function (response) { return response.json(); })
    .then(function (info) {
      const parts = [];
      parts.push("module_log: " + (info.module_log_mounted ? "✓" : "✗"));
      parts.push("listener: " + (info.listener_mounted ? "✓" : "✗"));
      statusEl.textContent = "统一集成程序 · " + parts.join(" · ");
    })
    .catch(function () {});

  function postTheme(frame) {
    const currentTheme = document.documentElement.className;
    if (!currentTheme || !THEMES[currentTheme]) return;
    try {
      frame.contentWindow.postMessage({
        type: "wb-theme-change",
        theme: currentTheme
      }, "*");
    } catch (error) {}
  }

  function ensureFrame(page) {
    let frame = framesByPage.get(page.id);
    if (frame) return frame;

    frame = document.createElement("iframe");
    frame.className = "wb-frame";
    frame.dataset.pageId = page.id;
    frame.title = page.title;
    frame.hidden = true;
    frame.setAttribute("aria-hidden", "true");
    frame.addEventListener("load", function () { postTheme(frame); });
    frame.src = page.src;
    panelsEl.appendChild(frame);
    framesByPage.set(page.id, frame);
    return frame;
  }

  function renderTabs() {
    tabsEl.innerHTML = "";
    PAGES.forEach(function (page) {
      const button = document.createElement("button");
      button.className = "wb-tab" + (page.id === current ? " active" : "");
      button.textContent = page.title;
      button.dataset.id = page.id;
      button.addEventListener("click", function () { switchTab(page.id); });
      tabsEl.appendChild(button);
    });
  }

  function switchTab(id) {
    const page = PAGES.find(function (item) { return item.id === id; });
    if (!page) return;
    current = id;
    const activeFrame = ensureFrame(page);
    framesByPage.forEach(function (frame) {
      const active = frame === activeFrame;
      frame.hidden = !active;
      frame.setAttribute("aria-hidden", active ? "false" : "true");
    });
    renderTabs();
  }

  const THEMES = {
    "theme-deepblue": "深蓝科技",
    "theme-emerald": "暗色翡翠",
    "theme-charcoal": "炭黑灰金",
    "theme-indigo": "靛蓝玻璃",
  };

  function switchTheme(theme) {
    if (!THEMES[theme]) return;
    document.documentElement.className = theme;
    try { localStorage.setItem("wb-theme", theme); } catch (error) {}

    const dots = document.querySelectorAll(".theme-dot");
    for (let index = 0; index < dots.length; index += 1) {
      dots[index].classList.toggle("active", dots[index].dataset.theme === theme);
    }
    framesByPage.forEach(function (frame) { postTheme(frame); });
  }

  if (themesEl) {
    const dots = themesEl.querySelectorAll(".theme-dot");
    for (let index = 0; index < dots.length; index += 1) {
      dots[index].addEventListener("click", function () {
        switchTheme(this.dataset.theme);
      });
    }
  }

  try {
    const saved = localStorage.getItem("wb-theme");
    if (saved && THEMES[saved]) switchTheme(saved);
  } catch (error) {}

  renderTabs();
  switchTab(PAGES[0].id);
})();
