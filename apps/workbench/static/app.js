/* AI 闭环研发验证工作台 —— 左侧分组导航 + 保活子页面 + 主题切换 + hash 路由
 * （纯静态，零构建；P5 总实施计划）
 *
 * 关键约束：
 * - iframe 首次访问才创建（ensureFrame），之后切页只隐藏/显示，禁止重设 src 或销毁。
 * - 当前页面写入 URL hash，支持刷新/深链/前进后退。
 * - 桌面端侧栏默认展开、可折叠为图标栏（localStorage 保存）；窄屏为可键盘关闭的抽屉。
 */
(function () {
  "use strict";

  // 页面注册表：id / title / src / group（验证 | 设备 | 辅助 | 维护）
  const PAGES = [
    { id: "workbench", title: "验证工作台", src: "/static/workbench.html", group: "验证" },
    { id: "serial-profile", title: "串口配置", src: "/static/pages/serial-profile/serial-profile.html", group: "设备" },
    { id: "module", title: "模块日志", src: "/static/pages/module-serial/module-serial.html", group: "设备" },
    { id: "listener", title: "侦听台", src: "/static/pages/listener/index.html", group: "设备" },
    { id: "simcon", title: "模拟集中器", src: "/static/pages/simcon/simcon.html", group: "设备" },
    { id: "trace", title: "报文追踪", src: "/static/pages/trace/trace.html", group: "辅助" },
    { id: "dict", title: "协议字典", src: "/static/pages/dict/dict.html", group: "辅助" },
    { id: "scenario", title: "场景脚本", src: "/static/pages/scenario/scenario.html", group: "辅助" },
    { id: "maintenance", title: "工作台状态", src: "/static/pages/maintenance/maintenance.html", group: "维护" },
  ];

  const GROUPS = [
    { name: "验证", pages: ["workbench"] },
    { name: "设备", pages: ["serial-profile", "module", "listener", "simcon"] },
    { name: "辅助", pages: ["trace", "dict", "scenario"] },
    { name: "维护", pages: ["maintenance"] },
  ];

  const sidebarEl = document.getElementById("wb-sidebar");
  const navGroupsEl = document.getElementById("wbNavGroups");
  const overlayEl = document.getElementById("wbOverlay");
  const panelsEl = document.getElementById("wb-panels");
  const statusEl = document.getElementById("wb-status");
  const themesEl = document.getElementById("wbThemes");
  const collapseBtnEl = document.getElementById("wbCollapseBtn");
  const framesByPage = new Map();
  let current = PAGES[0].id;

  // ---------- 状态栏 ----------
  fetch("/api/platform-version")
    .then(function (response) { return response.json(); })
    .then(function (info) {
      const parts = [];
      parts.push("module_log: " + (info.module_log_mounted ? "✓" : "✗"));
      parts.push("listener: " + (info.listener_mounted ? "✓" : "✗"));
      statusEl.textContent = "统一集成程序 · " + parts.join(" · ");
    })
    .catch(function () {});

  // ---------- 主题 ----------
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

  // ---------- iframe 保活（首次创建只赋一次 src） ----------
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

  // ---------- 左侧分组导航 ----------
  function renderGroups() {
    if (!navGroupsEl) return;
    navGroupsEl.innerHTML = "";
    GROUPS.forEach(function (group) {
      const section = document.createElement("section");
      section.className = "wb-nav-group";
      section.setAttribute("aria-label", group.name + "组");

      const heading = document.createElement("h2");
      heading.className = "wb-nav-group-title";
      heading.textContent = group.name;
      section.appendChild(heading);

      const list = document.createElement("ul");
      list.className = "wb-nav-list";
      group.pages.forEach(function (pageId) {
        const page = PAGES.find(function (item) { return item.id === pageId; });
        if (!page) return;
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.className = "wb-nav-item" + (pageId === current ? " active" : "");
        button.textContent = page.title;
        button.dataset.id = pageId;
        button.setAttribute("aria-current", pageId === current ? "page" : "false");
        button.addEventListener("click", function () { switchTab(pageId); });
        li.appendChild(button);
        list.appendChild(li);
      });
      section.appendChild(list);
      navGroupsEl.appendChild(section);
    });
  }

  // ---------- 页面切换（保活 + hash 路由） ----------
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
    // hash 路由：写入当前页 id，支持深链/刷新/前进后退
    if (location.hash !== "#" + id) {
      try { location.hash = "#" + id; } catch (error) {}
    }
    renderGroups();
    closeDrawer();
  }

  // ---------- hash 初始化 / 前进后退 ----------
  function pageIdFromHash() {
    const h = location.hash.replace(/^#/, "");
    const match = PAGES.find(function (item) { return item.id === h; });
    return match ? match.id : null;
  }

  function applyHash() {
    const id = pageIdFromHash();
    if (id && id !== current) switchTab(id);
  }

  // ---------- 侧栏折叠（桌面）/ 抽屉（窄屏） ----------
  const COLLAPSE_KEY = "wb-sidebar-collapsed";

  function isNarrow() {
    return window.matchMedia && window.matchMedia("(max-width: 860px)").matches;
  }

  function setCollapsed(collapsed) {
    document.body.classList.toggle("wb-sidebar-collapsed", collapsed);
    if (collapseBtnEl) {
      collapseBtnEl.setAttribute("aria-expanded", String(!collapsed));
      collapseBtnEl.setAttribute("title", collapsed ? "展开侧栏" : "折叠侧栏");
    }
    try { localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0"); } catch (error) {}
  }

  function openDrawer() {
    if (!isNarrow()) return;
    document.body.classList.add("wb-drawer-open");
    if (overlayEl) overlayEl.hidden = false;
    if (sidebarEl) sidebarEl.classList.add("wb-drawer-active");
    // 焦点回归：记住触发元素，关闭时归还
    const active = document.activeElement;
    if (active && active.dataset && active.dataset.id) drawerReturnFocus = active;
  }

  function closeDrawer() {
    document.body.classList.remove("wb-drawer-open");
    if (overlayEl) overlayEl.hidden = true;
    if (sidebarEl) sidebarEl.classList.remove("wb-drawer-active");
    if (drawerReturnFocus && typeof drawerReturnFocus.focus === "function") {
      try { drawerReturnFocus.focus(); } catch (error) {}
    }
    drawerReturnFocus = null;
  }
  let drawerReturnFocus = null;

  if (overlayEl) {
    overlayEl.addEventListener("click", closeDrawer);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeDrawer();
  });

  if (collapseBtnEl) {
    collapseBtnEl.addEventListener("click", function () {
      if (isNarrow()) {
        openDrawer();
      } else {
        const collapsed = document.body.classList.contains("wb-sidebar-collapsed");
        setCollapsed(!collapsed);
      }
    });
  }

  // ---------- 主题 ----------
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

  // ---------- 初始化 ----------
  try {
    const saved = localStorage.getItem("wb-theme");
    if (saved && THEMES[saved]) switchTheme(saved);
  } catch (error) {}

  try {
    if (localStorage.getItem(COLLAPSE_KEY) === "1") setCollapsed(true);
  } catch (error) {}

  renderGroups();

  // hash 深链优先；无 hash 则默认第一页
  if (pageIdFromHash()) {
    applyHash();
  } else {
    switchTab(PAGES[0].id);
  }

  // 前进/后退（hashchange）时恢复页面
  window.addEventListener("hashchange", applyHash);
})();
