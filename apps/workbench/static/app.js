/* AI 闭环研发验证工作台 —— 软件化统一壳：菜单栏 + 分组导航 + 保活子页面
 * + 全局 Dock + 状态栏 + 主题切换 + hash 路由（纯静态，零构建）
 * （REQS-0031 P1；上游 P5 总实施计划 / REQS-0012 主题系统）
 *
 * 关键约束（机制红线，改动需评审）：
 * - iframe 首次访问才创建（ensureFrame），之后切页只隐藏/显示，禁止重设 src 或销毁。
 * - 当前页面写入 URL hash，支持刷新/深链/前进后退。
 * - 桌面端侧栏默认展开、可折叠为图标栏（localStorage 保存）；窄屏为可键盘关闭的抽屉。
 * - 主题单一数据源 = tokens-v2.css 的 --theme-registry；切换后向保活 iframe 广播。
 * - 全局 Dock 通过 postMessage 接收子页面推送（wb-dock-push），壳不反向注入子页 DOM。
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

  // 模块图标（内联 SVG，随侧栏渲染注入；线宽 1.8 与子页面图标一致）
  const ICONS = {
    workbench: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
    "serial-profile": '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
    module: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>',
    listener: '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19.1"/>',
    simcon: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z"/>',
    trace: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    dict: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    scenario: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    maintenance: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c0 .66.39 1.26 1 1.51.55.22 1.18.09 1.63-.26"/>'
  };

  const sidebarEl = document.getElementById("wb-sidebar");
  const navGroupsEl = document.getElementById("wbNavGroups");
  const overlayEl = document.getElementById("wbOverlay");
  const panelsEl = document.getElementById("wb-panels");
  const statusEl = document.getElementById("wb-status");
  const themesEl = document.getElementById("wbThemes");
  const collapseBtnEl = document.getElementById("wbCollapseBtn");
  const framesByPage = new Map();
  let current = PAGES[0].id;

  const THEME_LABEL = { midnight: "墨夜", daylight: "晴昼" };

  // ---------- 状态栏（服务健康；原 wb-sub 数据源迁至底部状态栏） ----------
  fetch("/api/platform-version")
    .then(function (response) { return response.json(); })
    .then(function (info) {
      const parts = [];
      parts.push("module_log: " + (info.module_log_mounted ? "✓" : "✗"));
      parts.push("listener: " + (info.listener_mounted ? "✓" : "✗"));
      statusEl.innerHTML = '<span class="wb-sb-dot"></span>统一集成程序 · ' + parts.join(" · ");
    })
    .catch(function () {});

  // ---------- 主题 ----------
  // 注意：读取的是 <html data-theme>，不再读 className。
  // 旧实现用 className 兼作「主题名」与「其他 class 容器」，双向脆弱：
  // 赋值会清空 html 上其他 class，读取又依赖它只含主题名（REQS-0012 D2）。
  function postTheme(frame) {
    const currentTheme = document.documentElement.dataset.theme;
    if (!currentTheme) return;
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

  // ---------- 左侧分组导航（REQS-0031：图标 + 收缩态悬浮提示） ----------
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
        button.dataset.id = pageId;
        button.dataset.tip = page.title;
        button.setAttribute("aria-current", pageId === current ? "page" : "false");
        button.innerHTML =
          '<svg class="wb-nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
          (ICONS[pageId] || '<circle cx="12" cy="12" r="9"/>') + "</svg>" +
          '<span class="wb-nav-txt"></span>';
        button.querySelector(".wb-nav-txt").textContent = page.title;
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

  function toggleSidebar() {
    setCollapsed(!document.body.classList.contains("wb-sidebar-collapsed"));
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
    if (event.key === "Escape") { closeDrawer(); closeMenus(); }
    // REQS-0031：Ctrl+B 折叠侧栏 / Ctrl+J 折叠 Dock（输入框聚焦时不拦截）
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey) {
      const key = event.key.toLowerCase();
      if (key === "b") {
        const tag = (document.activeElement && document.activeElement.tagName) || "";
        if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
          event.preventDefault();
          toggleSidebar();
        }
      } else if (key === "j") {
        event.preventDefault();
        setDockFold(!dockEl.classList.contains("wb-dock-fold"));
      }
    }
  });

  if (collapseBtnEl) {
    collapseBtnEl.addEventListener("click", function () {
      if (isNarrow()) {
        openDrawer();
      } else {
        toggleSidebar();
      }
    });
  }

  // ---------- 菜单栏（REQS-0031 P1） ----------
  const menubarEl = document.querySelector(".wb-menubar");

  function closeMenus() {
    if (!menubarEl) return;
    menubarEl.querySelectorAll(".wb-menu.open").forEach(function (menu) {
      menu.classList.remove("open");
    });
  }

  if (menubarEl) {
    menubarEl.querySelectorAll(".wb-menu").forEach(function (menu) {
      const btn = menu.querySelector(":scope > .wb-menu-btn");
      if (!btn) return;
      btn.addEventListener("click", function (event) {
        event.stopPropagation();
        const wasOpen = menu.classList.contains("open");
        closeMenus();
        if (!wasOpen) menu.classList.add("open");
      });
    });
    document.addEventListener("click", closeMenus);
    // 菜单里的模块快捷入口
    menubarEl.querySelectorAll("[data-goto-page]").forEach(function (item) {
      item.addEventListener("click", function () {
        closeMenus();
        switchTab(item.dataset.gotoPage);
      });
    });
  }

  const menuThemeEl = document.getElementById("wbMenuTheme");
  if (menuThemeEl) {
    menuThemeEl.addEventListener("click", function () {
      closeMenus();
      const keys = THEMES.map(function (item) { return item.key; });
      if (!keys.length) return;
      const at = keys.indexOf(document.documentElement.dataset.theme);
      switchTheme(keys[(at + 1) % keys.length]);
    });
  }
  const menuRailEl = document.getElementById("wbMenuRail");
  if (menuRailEl) {
    menuRailEl.addEventListener("click", function () { closeMenus(); toggleSidebar(); });
  }
  const menuDockEl = document.getElementById("wbMenuDock");
  if (menuDockEl) {
    menuDockEl.addEventListener("click", function () {
      closeMenus();
      setDockFold(!dockEl.classList.contains("wb-dock-fold"));
    });
  }
  const menuKeysEl = document.getElementById("wbMenuKeys");
  if (menuKeysEl) {
    menuKeysEl.addEventListener("click", function () {
      closeMenus();
      window.alert(
        "键盘快捷键：\n\n" +
        "Ctrl+B —— 折叠 / 展开左侧模块导航\n" +
        "Ctrl+J —— 折叠 / 展开底部全局面板\n" +
        "Esc —— 关闭菜单 / 窄屏抽屉"
      );
    });
  }
  const menuAboutEl = document.getElementById("wbMenuAbout");
  if (menuAboutEl) {
    menuAboutEl.addEventListener("click", function () {
      closeMenus();
      fetch("/api/platform-version")
        .then(function (response) { return response.json(); })
        .then(function (info) {
          window.alert(
            "AI 闭环研发验证工作台（统一集成程序）\n\n" +
            "module_log 挂载: " + (info.module_log_mounted ? "✓" : "✗") + "\n" +
            "listener 挂载: " + (info.listener_mounted ? "✓" : "✗") + "\n\n" +
            "软件化统一壳 · REQS-0031"
          );
        })
        .catch(function () {
          window.alert("AI 闭环研发验证工作台（统一集成程序）\n\n软件化统一壳 · REQS-0031");
        });
    });
  }

  // ---------- 全局 Dock（REQS-0031 P1：跨模块"示波器"） ----------
  const dockEl = document.getElementById("wbDock");
  const dockViews = {
    frame: document.getElementById("wbDockViewFrame"),
    log: document.getElementById("wbDockViewLog"),
    event: document.getElementById("wbDockViewEvent")
  };
  const dockTxEl = document.getElementById("wbDockTx");
  const dockRxEl = document.getElementById("wbDockRx");
  const sbDockEl = document.getElementById("wbSbDock");
  const sbThemeEl = document.getElementById("wbSbTheme");
  const DOCK_FOLD_KEY = "wb-dock-fold";
  const DOCK_HEIGHT_KEY = "wb-dock-height";
  let dockTxCount = 0;
  let dockRxCount = 0;
  let dockMaxRows = 300;

  function setDockFold(fold) {
    if (!dockEl) return;
    dockEl.classList.toggle("wb-dock-fold", fold);
    if (sbDockEl) sbDockEl.textContent = fold ? "折叠" : "展开";
    try { localStorage.setItem(DOCK_FOLD_KEY, fold ? "1" : "0"); } catch (error) {}
  }

  function setDockHeight(px) {
    if (!dockEl) return;
    dockEl.style.height = px + "px";
    try { localStorage.setItem(DOCK_HEIGHT_KEY, String(px)); } catch (error) {}
  }

  if (dockEl) {
    const dockCar = document.getElementById("wbDockCar");
    if (dockCar) {
      dockCar.addEventListener("click", function () {
        setDockFold(!dockEl.classList.contains("wb-dock-fold"));
      });
    }
    dockEl.querySelectorAll(".wb-dock-tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        dockEl.querySelectorAll(".wb-dock-tab").forEach(function (item) {
          item.classList.toggle("on", item === tab);
        });
        Object.keys(dockViews).forEach(function (key) {
          if (dockViews[key]) dockViews[key].classList.toggle("on", key === tab.dataset.dockView);
        });
      });
    });
    // 拖顶边调高
    const grip = document.getElementById("wbDockGrip");
    if (grip) {
      let startY = 0;
      let startH = 0;
      let dragging = false;
      grip.addEventListener("mousedown", function (event) {
        if (dockEl.classList.contains("wb-dock-fold")) return;
        dragging = true;
        startY = event.clientY;
        startH = dockEl.offsetHeight;
        event.preventDefault();
      });
      document.addEventListener("mousemove", function (event) {
        if (!dragging) return;
        const max = Math.floor(window.innerHeight * 0.7);
        const h = Math.min(Math.max(startH + (startY - event.clientY), 34), max);
        setDockHeight(h);
        dockEl.classList.remove("wb-dock-fold");
        if (sbDockEl) sbDockEl.textContent = "展开";
      });
      document.addEventListener("mouseup", function () { dragging = false; });
    }
    try {
      if (localStorage.getItem(DOCK_FOLD_KEY) === "1") setDockFold(true);
      const savedH = parseInt(localStorage.getItem(DOCK_HEIGHT_KEY) || "", 10);
      if (savedH >= 34) setDockHeight(savedH);
    } catch (error) {}
  }

  // 子页面推送协议：{type:"wb-dock-push", view:"frame"|"log"|"event",
  //                  dir:"tx"|"rx"|"ev", source:"模拟集中器", tag:"10H-F1", text:"68 …"}
  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function dockPush(message) {
    const viewKey = dockViews[message.view] ? message.view : "frame";
    const host = dockViews[viewKey];
    if (!host) return;
    const empty = host.querySelector(".wb-dock-empty");
    if (empty) empty.remove();

    const now = new Date();
    const pad = function (n) { return (n < 10 ? "0" : "") + n; };
    const ts = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());

    const dir = message.dir === "tx" ? "tx" : message.dir === "rx" ? "rx" : "ev";
    const dirLabel = dir === "tx" ? "发送" : dir === "rx" ? "接收" : "事件";
    if (dir === "tx" && dockTxEl) dockTxEl.textContent = String(++dockTxCount);
    if (dir === "rx" && dockRxEl) dockRxEl.textContent = String(++dockRxCount);

    const row = document.createElement("div");
    row.className = "wb-dock-row";
    row.innerHTML =
      '<span class="wb-dock-tm">' + ts + "</span>" +
      '<span class="wb-dock-dir wb-dock-dir--' + dir + '">' + dirLabel + "</span>" +
      '<span class="wb-dock-src">' + esc(message.source || "—") + "</span>" +
      '<span class="wb-dock-tag">' + esc(message.tag || "") + "</span>" +
      '<span class="wb-dock-text"></span>';
    row.querySelector(".wb-dock-text").textContent = String(message.text || "");
    host.insertBefore(row, host.firstChild);
    while (host.children.length > dockMaxRows) host.removeChild(host.lastChild);
  }

  window.addEventListener("message", function (event) {
    const data = event.data;
    if (!data || data.type !== "wb-dock-push") return;
    try { dockPush(data); } catch (error) {}
  });

  // ---------- 主题 ----------
  // 单一数据源：主题清单只存在于 tokens-v2.css 的 --theme-registry。
  // 新增/删除一套主题 = 只改 CSS 那一处，本文件的按钮与逻辑自动跟随。
  // 这是 REQS-0012 P2 的验收项 8（旧架构需同步 CSS 选择器 / JS THEMES / HTML .theme-dot 三处）。
  function readThemeRegistry() {
    let raw = "";
    try {
      raw = getComputedStyle(document.documentElement).getPropertyValue("--theme-registry") || "";
    } catch (error) {
      return [];
    }
    return raw.replace(/["']/g, "").split(",").map(function (entry) {
      const parts = entry.split("|");
      return {
        key: (parts[0] || "").trim(),
        label: (parts[1] || "").trim(),
        icon: (parts[2] || "").trim()
      };
    }).filter(function (theme) { return theme.key; });
  }

  const THEMES = readThemeRegistry();

  function switchTheme(theme) {
    if (!THEMES.some(function (item) { return item.key === theme; })) return;
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("wb-theme", theme); } catch (error) {}

    const dots = document.querySelectorAll(".theme-dot");
    for (let index = 0; index < dots.length; index += 1) {
      dots[index].classList.toggle("active", dots[index].dataset.themeKey === theme);
    }
    if (sbThemeEl) sbThemeEl.textContent = THEME_LABEL[theme] || theme;
    framesByPage.forEach(function (frame) { postTheme(frame); });
  }

  // 由注册表渲染切换按钮。注意按钮用 data-theme-key，避开已被 <html> 占用的 data-theme。
  function renderThemes() {
    if (!themesEl) return;
    themesEl.textContent = "";
    const currentTheme = document.documentElement.dataset.theme;
    THEMES.forEach(function (theme) {
      const dot = document.createElement("button");
      dot.type = "button";
      dot.className = "theme-dot";
      dot.dataset.themeKey = theme.key;
      dot.title = theme.label;
      dot.setAttribute("aria-label", "切换主题：" + theme.label);
      dot.textContent = theme.icon || "●";
      if (theme.key === currentTheme) dot.classList.add("active");
      dot.addEventListener("click", function () { switchTheme(theme.key); });
      themesEl.appendChild(dot);
    });
    if (sbThemeEl) sbThemeEl.textContent = THEME_LABEL[currentTheme] || currentTheme || "—";
  }

  // ---------- 初始化 ----------
  // 主题已在 <head> 内联脚本中应用（防闪跳），此处只补渲染按钮，
  // 不再重复应用一次（旧实现在这里调用 switchTheme 会重复 postMessage）。
  renderThemes();

  // 漂移检测：CSS 注册表与 <html> 当前值若不一致，说明某处出现了硬编码残留。
  if (THEMES.length && !THEMES.some(function (item) {
    return item.key === document.documentElement.dataset.theme;
  })) {
    console.warn("[wb-theme] 当前 data-theme 不在 --theme-registry 中：",
                 document.documentElement.dataset.theme, THEMES.map(function (t) { return t.key; }));
  }

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
