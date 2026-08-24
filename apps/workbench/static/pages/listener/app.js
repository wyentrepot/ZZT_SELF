const DEFAULT_PAGE_SIZE = 100;
const SAMPLE_PATH = "listener\\test_data\\gw_log_sample.txt";

const state = {
  offset: 0,
  total: 0,
  query: "",
  nid: "",
  startTime: "",
  endTime: "",
  pageSize: DEFAULT_PAGE_SIZE,
  selectedId: null,
  pollTimer: null,
  lastFrameCount: -1,
  loadToken: 0,
  detail: null,
  pageCache: new Map(),
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  path: $("#log-path"),
  pick: $("#pick-button"),
  load: $("#load-button"),
  sample: $("#sample-button"),
  error: $("#operation-error"),
  message: $("#job-message"),
  source: $("#job-source"),
  bar: $("#progress-bar"),
  progress: $("#progress-value"),
  bytes: $("#bytes-read"),
  count: $("#frame-count"),
  errors: $("#error-count"),
  rows: $("#frame-rows"),
  filter: $("#frame-filter"),
  nidFilter: $("#nid-filter"),
  startTime: $("#start-time-filter"),
  endTime: $("#end-time-filter"),
  pageSize: $("#page-size"),
  prev: $("#prev-page"),
  next: $("#next-page"),
  pageNumber: $("#page-number"),
  pageSummary: $("#page-summary"),
  pageJumpInput: $("#page-jump-input"),
  pageJumpButton: $("#page-jump-button"),
  activeFilterSummary: $("#active-filter-summary"),
  activeFilterChips: $("#active-filter-chips"),
  pageCacheHint: $("#page-cache-hint"),
  detailEmpty: $("#detail-empty"),
  detailContent: $("#detail-content"),
  detailTabs: document.querySelectorAll(".detail-tab"),
  detailBase: $("#detail-base"),
  detailApp: $("#detail-app"),
  appExpandContent: $("#app-expand-content"),
  serialPort: $("#serial-port"),
  serialPortRefresh: $("#serial-port-refresh"),
  serialBaud: $("#serial-baud"),
  serialBytesize: $("#serial-bytesize"),
  serialParity: $("#serial-parity"),
  serialStopbits: $("#serial-stopbits"),
  serialStart: $("#serial-start"),
  serialStop: $("#serial-stop"),
  serialRefresh: $("#serial-refresh"),
  serialState: $("#serial-state"),
  serialMessage: $("#serial-message"),
};

function formatBytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.hidden = false;
}

function clearError() {
  elements.error.hidden = true;
  elements.error.textContent = "";
}

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "请求失败");
  return payload;
}

async function openLog() {
  const path = elements.path.value.trim();
  if (!path) return showError("请先输入本地日志的完整路径");
  clearError();
  elements.load.disabled = true;
  localStorage.setItem("hplc-log-path", path);
  state.offset = 0;
  state.lastFrameCount = -1;
  state.selectedId = null;
  resetDetail();

  try {
    await request("/api/listener/logs/open", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path}),
    });
    startPolling();
  } catch (error) {
    showError(error.message);
    elements.load.disabled = false;
  }
}

// ---------- 文件选择对话框 ----------

const picker = {
  overlay: $("#file-picker"),
  close: $("#picker-close"),
  cancel: $("#picker-cancel"),
  up: $("#picker-up"),
  path: $("#picker-path"),
  roots: $("#picker-roots"),
  list: $("#picker-list"),
  selected: $("#picker-selected"),
  confirm: $("#picker-confirm"),
  currentDir: null,
  chosenFile: null,
};

function formatFileSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function pickerOpen() {
  picker.overlay.hidden = false;
  picker.chosenFile = null;
  picker.confirm.disabled = true;
  picker.selected.textContent = "";
  pickerRoots();
  // 默认定位：上次打开路径（优先后端持久化，其次浏览器记忆）
  request("/api/listener/fs/last")
    .then((data) => {
      const last = (data.path || localStorage.getItem("hplc-log-path") || "").trim();
      if (last) {
        pickerList(last);
      } else if (picker.currentDir) {
        pickerList(picker.currentDir);
      }
    })
    .catch(() => {});
}

function pickerClose() {
  picker.overlay.hidden = true;
}

async function pickerRoots() {
  try {
    const data = await request("/api/listener/fs/roots");
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
    const data = await request(`/api/listener/fs/list?path=${encodeURIComponent(path)}`);
    picker.up.disabled = !data.parent;
    picker.list.textContent = "";
    if (!data.dirs.length && !data.files.length) {
      const empty = document.createElement("div");
      empty.className = "file-picker-empty";
      empty.textContent = "该目录没有子目录或日志文件";
      picker.list.appendChild(empty);
      return;
    }
    data.dirs.forEach((dir) => {
      picker.list.appendChild(pickerDirRow(dir));
    });
    data.files.forEach((file) => {
      picker.list.appendChild(pickerFileRow(file));
    });
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
  elements.path.value = picker.chosenFile;
  localStorage.setItem("hplc-log-path", picker.chosenFile);
  pickerClose();
}

picker.close.addEventListener("click", pickerClose);
picker.cancel.addEventListener("click", pickerClose);
picker.overlay.addEventListener("click", (event) => {
  if (event.target === picker.overlay) pickerClose();
});
picker.up.addEventListener("click", () => {
  if (!picker.currentDir) return;
  request(`/api/listener/fs/list?path=${encodeURIComponent(picker.currentDir)}`)
    .then((data) => {
      if (data.parent) pickerList(data.parent);
    })
    .catch(() => {});
});
picker.confirm.addEventListener("click", pickerConfirm);
elements.pick.addEventListener("click", async () => {
  // 默认打开网页内嵌选择器（fs/roots + fs/list 浏览），不依赖 tkinter——
  // 打包环境下 tkinter 弹窗（fs/pick）可能打不开；选择器内提供
  // "系统文件管理器"按钮（pickerNative，走 fs/pick，失败降级 PowerShell）。
  pickerOpen();
});
// 网页选择器内"系统文件管理器"按钮：tkinter 弹窗（失败自动降级 PowerShell）
const pickerNative = $("#picker-native");
if (pickerNative) {
  pickerNative.addEventListener("click", async () => {
    try {
      const data = await request("/api/listener/fs/pick");
      if (data.path) {
        elements.path.value = data.path;
        localStorage.setItem("hplc-log-path", data.path);
        pickerClose();
      }
    } catch (error) {
      showError(`文件选择失败：${error.message}`);
    }
  });
}

function updateStatus(status) {
  const progress = Math.max(0, Math.min(1, status.progress || 0));
  elements.message.textContent = status.message || status.state;
  elements.source.textContent = status.source_path || "文件只在建立索引时顺序读取一次";
  elements.bar.style.width = `${progress * 100}%`;
  elements.progress.textContent = `${Math.round(progress * 100)}%`;
  elements.bytes.textContent = `${formatBytes(status.bytes_read)} / ${formatBytes(status.file_size)}`;
  elements.count.textContent = Number(status.frame_count || 0).toLocaleString();
  elements.errors.textContent = Number(status.error_count || 0).toLocaleString();

  if (status.frame_count !== state.lastFrameCount) {
    state.lastFrameCount = status.frame_count;
    loadFrames();
  }

  if (status.state === "completed" || status.state === "failed") {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    elements.load.disabled = false;
    if (status.state === "failed") showError(status.message);
  }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  const tick = async () => {
    try {
      updateStatus(await request("/api/listener/logs/status"));
    } catch (error) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      showError(error.message);
      elements.load.disabled = false;
    }
  };
  tick();
  state.pollTimer = setInterval(tick, 800);
}

function summaryValue(summary, ...keys) {
  for (const key of keys) {
    if (summary && summary[key] !== undefined && summary[key] !== null) return summary[key];
  }
  return "—";
}

function renderFrames(page) {
  state.total = page.total;
  elements.rows.replaceChildren();

  if (!page.items.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = state.query || state.nid ? "没有符合当前筛选条件的帧" : "正在等待日志帧…";
    row.append(cell);
    elements.rows.append(row);
  }

  for (const frame of page.items) {
    const summary = frame.summary || {};
    const row = document.createElement("tr");
    row.dataset.id = frame.id;
    if (frame.id === state.selectedId) row.classList.add("selected");

    const values = [
      frame.sequence,
      frame.log_time,
      summaryValue(summary, "FrmType", "帧类型"),
      summaryValue(summary, "SNID"),
      `${summaryValue(summary, "SRC", "源地址")} → ${summaryValue(summary, "DST", "目的地址")}`,
      summaryValue(summary.Info2 || summary.Info || summary, "ChType", "通道"),
      `${frame.byte_length} B`,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 0) cell.className = "number-cell";
      if (index === 4) cell.className = "route";
      if (index === 6) cell.className = "length-cell";
      row.append(cell);
    });

    const statusCell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `status-pill${frame.parse_error ? " error" : ""}`;
    pill.textContent = frame.parse_error ? "摘要异常" : "可查看";
    statusCell.append(pill);
    row.append(statusCell);
    row.addEventListener("click", () => loadDetail(frame.id, row));
    elements.rows.append(row);
  }

  const currentPage = Math.floor(state.offset / state.pageSize) + 1;
  const pageCount = Math.max(1, Math.ceil(state.total / state.pageSize));
  const range = state.startTime || state.endTime
    ? ` · ${state.startTime || "00:00:00"}–${state.endTime || "23:59:59"}`
    : "";
  elements.pageNumber.textContent = `第 ${currentPage} / ${pageCount} 页`;
  elements.pageSummary.textContent = `共 ${state.total.toLocaleString()} 帧${range} · 每页 ${state.pageSize}`;
  elements.prev.disabled = state.offset === 0;
  elements.next.disabled = state.offset + state.pageSize >= state.total;
  elements.pageJumpInput.value = String(currentPage);
  elements.pageJumpInput.max = String(pageCount);
  elements.pageJumpInput.disabled = false;
  elements.pageJumpButton.disabled = false;
}

function updateActiveFilterSummary() {
  const nid = state.nid ? `NID ${state.nid}` : "全部 NID";
  const range = state.startTime || state.endTime
    ? `${state.startTime || "00:00:00"}–${state.endTime || "23:59:59"}` : "全时段";
  elements.activeFilterSummary.textContent = `当前条件：${nid} · ${range}（适用于所有分析）`;
  renderActiveFilterChips();
  document.querySelectorAll(".analysis-scope").forEach((node) => {
    node.textContent = `当前条件：${nid} · ${range}`;
  });
}

function renderActiveFilterChips() {
  const host = elements.activeFilterChips;
  if (!host) return;
  host.replaceChildren();
  const filters = [
    ["NID", state.nid, () => { elements.nidFilter.value = ""; }],
    ["起始", state.startTime, () => { elements.startTime.value = ""; }],
    ["结束", state.endTime, () => { elements.endTime.value = ""; }],
  ];
  filters.filter(([, value]) => value).forEach(([label, value, clear]) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "filter-chip";
    chip.textContent = `${label} ${value} ×`;
    chip.addEventListener("click", () => { clear(); elements.filter.click(); });
    host.append(chip);
  });
}

function setFrameLoading(loading) {
  document.querySelector("#frames-data .list-panel")?.classList.toggle("is-loading", loading);
}

function jumpToPage() {
  const maximum = Math.max(1, Math.ceil(state.total / state.pageSize));
  const requested = parseInt(elements.pageJumpInput.value, 10) || 1;
  const target = Math.min(maximum, Math.max(1, requested));
  elements.pageJumpInput.value = String(target);
  state.offset = (target - 1) * state.pageSize;
  loadFrames();
}

async function loadFrames() {
  const params = new URLSearchParams({
    query: state.query,
    nid: state.nid,
    start_time: state.startTime,
    end_time: state.endTime,
    offset: state.offset,
    limit: state.pageSize,
  });
  const cacheKey = params.toString();
  const cached = state.pageCache.get(cacheKey);
  if (cached) {
    setFrameLoading(true);
    renderFrames(cached);
    elements.pageCacheHint.textContent = `已缓存 · ${state.pageCache.size} 页`;
    requestAnimationFrame(() => setFrameLoading(false));
    return;
  }
  // 防重入令牌：丢弃过期响应，避免串口轮询触发的请求堆积
  const token = ++state.loadToken;
  setFrameLoading(true);
  try {
    const page = await request(`/api/listener/logs/frames?${params}`);
    if (token !== state.loadToken) return; // 已有更新的请求发出，丢弃本次过期结果
    state.pageCache.set(cacheKey, page);
    renderFrames(page);
    elements.pageCacheHint.textContent = `${state.pageCache.size} 页已缓存`;
  } catch (error) {
    if (token === state.loadToken) showError(error.message);
  } finally {
    if (token === state.loadToken) setFrameLoading(false);
  }
}

function resetDetail() {
  elements.detailEmpty.hidden = false;
  elements.detailContent.hidden = true;
  state.detail = null;
}

function stage(title, status, description) {
  const item = document.createElement("div");
  item.className = "stage";
  const head = document.createElement("div");
  head.className = "stage-title";
  const strong = document.createElement("strong");
  strong.textContent = title;
  const stateText = document.createElement("span");
  stateText.textContent = status;
  head.append(strong, stateText);
  const copy = document.createElement("p");
  copy.textContent = description;
  item.append(head, copy);
  return item;
}

function compactObject(value) {
  if (!value) return "未返回该层数据";
  return Object.entries(value)
    .filter(([, item]) => item !== null && typeof item !== "object")
    .slice(0, 8)
    .map(([key, item]) => `${key}: ${item}`)
    .join(" · ") || "已解析";
}

function addBadge(container, text, primary = false) {
  const badge = document.createElement("span");
  badge.className = `badge${primary ? " primary" : ""}`;
  badge.textContent = text;
  container.append(badge);
}

function renderDetail(detail) {
  state.detail = detail;
  const simple = detail.analysis.simple || {};
  const full = detail.analysis.full || {};
  const info = simple.Info2 || simple.Info || full.Info2 || full.Info || {};

  elements.detailEmpty.hidden = true;
  elements.detailContent.hidden = false;
  $("#detail-title").textContent = `${summaryValue(simple, "FrmType", "帧类型")} · #${detail.sequence}`;
  $("#detail-meta").textContent = `${detail.log_time}  |  索引 ID ${detail.id}  |  ${detail.byte_length} 字节`;

  const badges = $("#detail-badges");
  badges.replaceChildren();
  addBadge(badges, summaryValue(simple, "FrmType", "帧类型"), true);
  addBadge(badges, `${summaryValue(simple, "SRC", "源地址")} → ${summaryValue(simple, "DST", "目的地址")}`);
  addBadge(badges, summaryValue(info, "ChType", "通道"));
  addBadge(badges, summaryValue(info, "ProType", "协议"));
  addBadge(badges, `CRC ${summaryValue(info, "CRC")}`);

  const stages = $("#parse-stages");
  stages.replaceChildren();
  stages.append(
    stage("1. 侦听台外层", info.CRC === "OK" ? "已通过" : "已解析",
      `协议 ${summaryValue(info, "ProType", "协议")} · 通道 ${summaryValue(info, "ChType", "通道")} · RSSI ${summaryValue(info, "RSSI")}`),
    stage("2. MAC 路由摘要", "已解析",
      `源 TEI ${summaryValue(simple, "SRC", "源地址")} → 目的 TEI ${summaryValue(simple, "DST", "目的地址")} · SNID ${summaryValue(simple, "SNID")}`),
    stage("3. FCH 物理帧控制头", full.FCH ? "已展开" : "无数据", compactObject(full.FCH)),
    stage("4. MPDU / MSDU 载荷", full.MPDU ? "已展开" : "无数据", compactObject(full.MPDU)),
  );
  if (full.Error) stages.append(stage("5. 解析提示", "注意", String(full.Error)));
  // 详情解析失败时显示错误横幅，但保留原始帧数据
  const detailError = detail.parse_error || detail.analysis?.parse_error;
  if (detailError) {
    stages.append(stage("⚠ 解析失败", "错误", String(detailError)));
  }

  $("#detail-json").textContent = JSON.stringify(detail.analysis, null, 2);
  $("#detail-raw").textContent = detail.raw_hex;
  renderApplicationDetail(simple);
}

// 应用层展开渲染：仅对并发抄表帧（0003）与分钟采集相关帧（00E2/00E3/00E4）
// 渲染 application.fields / items / nested；其他帧显示提示。
const APP_EXPAND_IDS = new Set(["0003", "00E2", "00E3", "00E4"]);

function renderApplicationDetail(simple) {
  const container = elements.appExpandContent;
  container.replaceChildren();

  const appId = simple.APP_ID;
  const application = simple.application;

  if (!appId || !APP_EXPAND_IDS.has(appId)) {
    const hint = document.createElement("p");
    hint.className = "app-expand-hint";
    hint.textContent = "该帧不是并发抄表或分钟采集报文，无应用层展开内容";
    container.append(hint);
    return;
  }
  if (simple.application_error) {
    const hint = document.createElement("p");
    hint.className = "app-expand-hint error";
    hint.textContent = `应用层解析异常：${simple.application_error}`;
    container.append(hint);
    return;
  }
  if (!application) {
    const hint = document.createElement("p");
    hint.className = "app-expand-hint";
    hint.textContent = "该帧暂无应用层展开数据";
    container.append(hint);
    return;
  }

  const section = document.createElement("section");
  section.className = "app-expand-section";

  const title = document.createElement("h4");
  title.textContent = `应用层结构（${application.structure || "双模4-3"}）`;
  section.append(title);

  if (application.fields && application.fields.length) {
    section.append(renderFieldTable("报文头字段", application.fields));
  }
  if (application.items && application.items.length) {
    section.append(renderItemList("数据项", application.items));
  }
  if (application.nested && application.nested.length) {
    const nestedTitle = document.createElement("h5");
    nestedTitle.textContent = `内嵌帧（${application.nested.length} 条）`;
    section.append(nestedTitle);
    const tree = document.createElement("div");
    tree.className = "nested-tree";
    application.nested.forEach((nested) => tree.append(renderNestedFrame(nested)));
    section.append(tree);
  }
  if (application.warnings && application.warnings.length) {
    const warn = document.createElement("p");
    warn.className = "app-expand-hint warning";
    warn.textContent = application.warnings.join("；");
    section.append(warn);
  }

  container.append(section);
}

function renderFieldTable(titleText, fields) {
  const wrap = document.createElement("div");
  wrap.className = "app-table-wrap";
  const heading = document.createElement("h5");
  heading.textContent = titleText;
  wrap.append(heading);

  const table = document.createElement("table");
  table.className = "app-field-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const header of ["字段", "值", "十六进制", "说明"]) {
    const th = document.createElement("th");
    th.textContent = header;
    headRow.append(th);
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = document.createElement("tbody");
  for (const field of fields) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = field.name || "";
    const value = document.createElement("td");
    value.textContent = String(field.value ?? "");
    const hex = document.createElement("td");
    hex.textContent = String(field.hex ?? "");
    const desc = document.createElement("td");
    desc.textContent = String(field.desc ?? "");
    tr.append(name, value, hex, desc);
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  return wrap;
}

function renderItemList(titleText, items) {
  const wrap = document.createElement("div");
  wrap.className = "app-items";
  const heading = document.createElement("h5");
  heading.textContent = titleText;
  wrap.append(heading);
  for (const item of items) {
    const line = document.createElement("div");
    line.className = "app-item";
    const name = document.createElement("strong");
    name.textContent = item.name || "";
    const value = document.createElement("span");
    value.textContent = String(item.value ?? "");
    line.append(name, value);
    wrap.append(line);
  }
  return wrap;
}

function renderNestedFrame(nested) {
  const box = document.createElement("details");
  box.className = "nested-frame";
  box.open = true;

  const summary = document.createElement("summary");
  const tag = document.createElement("span");
  tag.className = "nested-tag";
  tag.textContent = nested.structure || "未知";
  const label = document.createElement("span");
  label.textContent = nested.address ? `地址 ${nested.address}` : "内嵌帧";
  summary.append(tag, label);
  box.append(summary);

  const body = document.createElement("div");
  body.className = "nested-body";
  if (nested.fields && nested.fields.length) {
    body.append(renderFieldTable("字段", nested.fields));
  }
  if (nested.items && nested.items.length) {
    body.append(renderItemList("数据项", nested.items));
  }
  if (nested.nested && nested.nested.length) {
    nested.nested.forEach((child) => body.append(renderNestedFrame(child)));
  }
  if (nested.warnings && nested.warnings.length) {
    const warn = document.createElement("p");
    warn.className = "app-expand-hint warning";
    warn.textContent = nested.warnings.join("；");
    body.append(warn);
  }
  box.append(body);
  return box;
}

function switchDetailTab(name) {
  elements.detailTabs.forEach((tab) => {
    const active = tab.dataset.detailTab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  elements.detailBase.hidden = name !== "base";
  elements.detailApp.hidden = name !== "app";
}

elements.detailTabs.forEach((tab) => {
  tab.addEventListener("click", () => switchDetailTab(tab.dataset.detailTab));
});

async function loadDetail(id, row) {
  state.selectedId = id;
  document.querySelectorAll("#frame-rows tr").forEach((item) => item.classList.remove("selected"));
  row.classList.add("selected");
  elements.detailEmpty.hidden = false;
  elements.detailEmpty.querySelector("h2").textContent = "正在调用 DLL 深度解析…";
  elements.detailContent.hidden = true;
  try {
    renderDetail(await request(`/api/listener/logs/frames/${id}`));
  } catch (error) {
    elements.detailEmpty.querySelector("h2").textContent = "详情解析失败";
    showError(error.message);
  }
}

async function parseSingleFrame() {
  const button = $("#parse-button");
  const output = $("#single-output");
  button.disabled = true;
  output.textContent = "正在解析…";
  try {
    const data = await request("/api/listener/parse", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({hex: $("#frame-input").value}),
    });
    output.textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

elements.load.addEventListener("click", openLog);
elements.sample.addEventListener("click", () => {
  elements.path.value = SAMPLE_PATH;
  openLog();
});
$("#single-toggle").addEventListener("click", () => {
  const panel = $("#single-debugger");
  panel.hidden = !panel.hidden;
  $("#single-toggle").textContent = panel.hidden ? "单帧调试" : "收起单帧调试";
});
$("#parse-button").addEventListener("click", parseSingleFrame);
$("#filter-button").addEventListener("click", () => {
  state.query = elements.filter.value.trim();
  state.nid = elements.nidFilter.value.trim().toUpperCase();
  state.startTime = elements.startTime.value;
  state.endTime = elements.endTime.value;
  state.offset = 0;
  state.pageCache.clear();
  updateActiveFilterSummary();
  loadFrames();
});
elements.filter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("#filter-button").click();
});
elements.nidFilter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("#filter-button").click();
});
elements.prev.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.pageSize);
  loadFrames();
});
elements.next.addEventListener("click", () => {
  state.offset += state.pageSize;
  loadFrames();
});
elements.pageJumpButton.addEventListener("click", jumpToPage);
elements.pageJumpInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") jumpToPage();
});
elements.pageSize.addEventListener("change", () => {
  const value = Math.min(500, Math.max(1, parseInt(elements.pageSize.value, 10) || DEFAULT_PAGE_SIZE));
  elements.pageSize.value = String(value);
  state.pageSize = value;
  state.offset = 0;
  loadFrames();
});
elements.pageSize.addEventListener("keydown", (event) => {
  if (event.key === "Enter") elements.pageSize.dispatchEvent(new Event("change"));
});
$("#copy-detail").addEventListener("click", async () => {
  if (!state.detail) return;
  await navigator.clipboard.writeText(JSON.stringify(state.detail.analysis, null, 2));
  $("#copy-detail").textContent = "已复制";
  setTimeout(() => { $("#copy-detail").textContent = "复制"; }, 1000);
});

elements.path.value = localStorage.getItem("hplc-log-path") || "";
updateActiveFilterSummary();
fetch("/api/listener/version")
  .then((response) => response.json())
  .then((data) => {
    $("#version").textContent = `${data.name} · ${data.version} · ${data.date}`;
  })
  .catch(() => { $("#version").textContent = "解析 DLL 连接失败"; });

const launchMode = new URLSearchParams(window.location.search).get("mode");
if (launchMode === "test") {
  elements.path.value = SAMPLE_PATH;
  setTimeout(openLog, 150);
} else {
  request("/api/listener/logs/status").then(updateStatus).catch(() => {});
}

// ---------- 分钟采集分析 ----------

const minuteElements = {
  view: $("#minute-view"),
  framesView: $("#frames-view"),
  framesData: $("#frames-data"),
  deleteConfigView: $("#delete-config-view"),
  tabs: document.querySelectorAll(".view-tab"),
  period: $("#period-input"),
  source: $("#minute-source"),
  task: $("#minute-task-input"),
  taskList: $("#minute-task-list"),
  ccoTei: $("#cco-tei-input"),
  query: $("#minute-query-button"),
  error: $("#minute-error"),
  nidHint: $("#minute-nid-hint"),
  rows: $("#minute-period-table"),
  details: $("#minute-report-details"),
  taskConfigTei: $("#task-config-tei-input"),
  taskConfigSelect: $("#task-config-task-select"),
  taskConfigRefresh: $("#task-config-refresh"),
  taskConfigQuery: $("#task-config-query"),
  taskConfigError: $("#task-config-error"),
  taskConfigNidHint: $("#task-config-nid-hint"),
  taskConfigStats: $("#task-config-stats"),
  taskConfigNumber: $("#task-config-number"),
  taskSentCount: $("#task-sent-count"),
  taskSuccessCount: $("#task-success-count"),
  taskFailedCount: $("#task-failed-count"),
  taskNoResponseCount: $("#task-no-response-count"),
  taskPendingCount: $("#task-pending-count"),
  taskUnissuedCount: $("#task-unissued-count"),
  taskConfigStaTable: $("#task-config-sta-table"),
  taskConfigMacSort: $("#task-config-mac-sort"),
  taskConfigCycle: $("#task-config-cycle-select"),
  taskConfigAnalysis: $("#task-config-analysis-content"),
};
let taskConfigRows = [];
let taskConfigMacAscending = true;

function switchView(name) {
  minuteElements.tabs.forEach((tab) => {
    const active = tab.dataset.view === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  minuteElements.framesView.hidden = name !== "frames";
  minuteElements.framesData.hidden = name !== "frames";
  minuteElements.view.hidden = name !== "minute";
  minuteElements.deleteConfigView.hidden = name !== "delete-config";
  if (networkElements) networkElements.view.hidden = name !== "network-assessment";
  if (name === "minute") loadMinuteTaskList();
  if (name === "network-assessment") loadNetworkStatus();
}

minuteElements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    switchView(tab.dataset.view);
    if (tab.dataset.view === "delete-config") loadTaskConfigTasks();
  });
});

function updateNidHint(el) {
  if (!el) return;
  if (state.nid) {
    el.textContent = `当前 NID 筛选：${state.nid}（所有分析仅统计该网络）`;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

async function loadMinuteAnalysis() {
  const tei = (minuteElements.ccoTei.value.trim() || "001").toUpperCase();
  minuteElements.ccoTei.value = tei;
  updateNidHint(minuteElements.nidHint);
  const params = new URLSearchParams({
    task_no: minuteElements.task.value.trim(),
    cco_tei: tei,
    nid: state.nid,
    start_time: state.startTime,
    end_time: state.endTime,
  });
  if (minuteElements.source.value === "manual") params.set("period_minutes", minuteElements.period.value);
  minuteElements.error.hidden = true;
  try {
    const data = await request(`/api/listener/logs/task-minute-analysis?${params}`);
    if (minuteElements.source.value === "configured" && data.derived_period_minutes) {
      minuteElements.period.value = String(data.derived_period_minutes);
    }
    renderMinuteAnalysis(data);
  } catch (error) {
    minuteElements.error.textContent = error.message;
    minuteElements.error.hidden = false;
  }
}

async function loadMinuteTaskList() {
  const tei = (minuteElements.ccoTei.value.trim() || "001").toUpperCase();
  const params = new URLSearchParams({ cco_tei: tei, nid: state.nid, start_time: state.startTime, end_time: state.endTime });
  try {
    const data = await request(`/api/listener/logs/task-config-tasks?${params}`);
    minuteElements.taskList.replaceChildren();
    for (const taskNo of data.tasks) {
      const option = document.createElement("option");
      option.value = String(taskNo);
      minuteElements.taskList.append(option);
    }
  } catch { /* 下拉列表加载失败不影响手动输入 */ }
}

async function refreshDerivedPeriod() {
  if (minuteElements.source.value !== "configured") return;
  const taskNo = minuteElements.task.value.trim();
  const tei = (minuteElements.ccoTei.value.trim() || "001").toUpperCase();
  if (!taskNo) { minuteElements.period.value = ""; return; }
  const params = new URLSearchParams({ task_no: taskNo, cco_tei: tei, nid: state.nid });
  try {
    const data = await request(`/api/listener/logs/task-derived-period?${params}`);
    minuteElements.period.value = (data.source === "configured" && data.derived_period_minutes)
      ? String(data.derived_period_minutes) : "";
  } catch { /* 推导失败保持原值 */ }
}

function updateMinuteSourceControls() {
  const manual = minuteElements.source.value === "manual";
  minuteElements.period.disabled = !manual;
  minuteElements.period.closest("label")?.classList.toggle("is-disabled", !manual);
  if (!manual) refreshDerivedPeriod();
}

function renderMinuteAnalysis(data) {
  minuteElements.rows.replaceChildren();
  minuteElements.details.replaceChildren();
  if (!data.periods.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 1;
    cell.textContent = minuteElements.source.value === "configured" && data.source === "manual"
      ? "该任务没有启用配置记录，请在「来源」切换为「手工输入」并填写周期"
      : "没有符合当前筛选条件的周期";
    row.append(cell);
    minuteElements.rows.append(row);
    return;
  }

  for (const period of data.periods) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "route";
    const expected = period.expected_count === null ? "应报未知" : `应报 ${period.expected_count} STA，缺报 ${period.missing_stas.length}`;
    cell.textContent = `任务 ${data.task_no} · ${period.description}（周期 ${period.period_minutes} 分钟） · 实报 ${period.report_count} 帧 · 去重后 ${period.deduped_app_count} 个应用层上报 / ${period.received_sta_count} 个 STA · ${expected} · 冻结正确 ${period.freeze_ok_count} / 异常 ${period.freeze_error_count}`;
    row.append(cell);
    row.addEventListener("click", () => renderMinuteReportDetails(period));
    minuteElements.rows.append(row);
  }
}

async function loadTaskConfigTasks() {
  const tei = (minuteElements.taskConfigTei.value.trim() || "001").toUpperCase();
  minuteElements.taskConfigTei.value = tei;
  updateNidHint(minuteElements.taskConfigNidHint);
  const params = new URLSearchParams({
    cco_tei: tei,
    nid: state.nid,
    start_time: state.startTime,
    end_time: state.endTime,
  });
  minuteElements.taskConfigError.hidden = true;
  minuteElements.taskConfigSelect.disabled = true;
  minuteElements.taskConfigQuery.disabled = true;
  try {
    const data = await request(`/api/listener/logs/task-config-tasks?${params}`);
    const select = minuteElements.taskConfigSelect;
    select.replaceChildren();
    if (!data.tasks.length) {
      select.append(new Option("当前条件下没有任务", ""));
      minuteElements.taskConfigStats.hidden = true;
      return;
    }
    for (const taskNo of data.tasks) select.append(new Option(`任务号 ${taskNo}`, taskNo));
    select.disabled = false;
    minuteElements.taskConfigQuery.disabled = false;
    await loadTaskConfigSummary();
  } catch (error) {
    minuteElements.taskConfigError.textContent = error.message;
    minuteElements.taskConfigError.hidden = false;
  }
}

async function loadTaskConfigSummary() {
  const taskNo = minuteElements.taskConfigSelect.value;
  if (!taskNo) return;
  const params = new URLSearchParams({
    cco_tei: minuteElements.taskConfigTei.value,
    task_no: taskNo,
    nid: state.nid,
    start_time: state.startTime,
    end_time: state.endTime,
  });
  minuteElements.taskConfigError.hidden = true;
  try {
    renderTaskConfigSummary(
      await request(`/api/listener/logs/task-config-summary?${params}`)
    );
    renderTaskConfigLifecycle(await request(`/api/listener/logs/task-config-lifecycle?${params}`));
  } catch (error) {
    minuteElements.taskConfigError.textContent = error.message;
    minuteElements.taskConfigError.hidden = false;
  }
}

function renderTaskConfigLifecycle(data) {
  const cycle = data.cycle;
  const select = minuteElements.taskConfigCycle;
  select.replaceChildren();
  data.cycles.forEach((item, index) => select.append(new Option(`第 ${index + 1} 轮：${item.start_time} · ${item.status}`, index)));
  if (!cycle) { minuteElements.taskConfigAnalysis.textContent = "该任务暂无启用配置轮次"; return; }
  select.value = String(data.cycles.indexOf(cycle));
  const notSent = cycle.stas.filter(item => !item.delete_time);
  const unanswered = cycle.stas.filter(item => item.delete_time && item.delete_result === "未下发删除");
  const anomalies = cycle.anomalies.length
    ? cycle.anomalies.map(item => `${item.type}：${item.mac}，删除成功 ${item.delete_time}，上报 ${item.report_time}`).join("\n") : "无异常";
  const notSentText = notSent.length ? `未下发删除 STA（${notSent.length}）：${notSent.map(item => item.mac).join("、")}` : "未下发删除 STA：无";
  const unansweredText = unanswered.length ? `已下发删除但未应答 STA（${unanswered.length}）：${unanswered.map(item => item.mac).join("、")}` : "已下发删除但未应答 STA：无";
  minuteElements.taskConfigAnalysis.textContent = `开始：${cycle.start_time}\n最后下发删除：${cycle.last_delete_time || "无"}\n结束：${cycle.end_time || "未完成"}\n配置 STA：${cycle.configured_sta_count}，删除成功：${cycle.delete_success_count}，失败：${cycle.delete_fail_count}，删除未下发：${cycle.delete_not_sent_count}，删除已下发未应答：${cycle.delete_pending_count}\n${notSentText}\n${unansweredText}\n状态：${cycle.status}\n${anomalies}`;
}

function renderTaskConfigSummary(data) {
  minuteElements.taskConfigNumber.textContent = data.task_no;
  minuteElements.taskSentCount.textContent = String(data.sent_sta_count);
  minuteElements.taskSuccessCount.textContent = String(data.success_sta_count);
  minuteElements.taskFailedCount.textContent = String(data.failed_sta_count);
  minuteElements.taskNoResponseCount.textContent = String(data.no_response_sta_count);
  minuteElements.taskPendingCount.textContent = String(data.pending_sta_count);
  minuteElements.taskUnissuedCount.textContent = String(data.unissued_report_sta_count);
  minuteElements.taskConfigStats.hidden = false;
  taskConfigRows = [...data.stas];
  renderTaskConfigRows();
}

function renderTaskConfigRows() {
  const body = minuteElements.taskConfigStaTable;
  body.replaceChildren();
  if (!taskConfigRows.length) {
    body.append(emptyRow(7, "该任务暂无 STA 记录"));
    return;
  }
  const rows = [...taskConfigRows].sort((a, b) => taskConfigMacAscending ? a.mac.localeCompare(b.mac) : b.mac.localeCompare(a.mac));
  minuteElements.taskConfigMacSort.textContent = `STA MAC ${taskConfigMacAscending ? "↑" : "↓"}`;
  for (const row of rows) {
    const tr = document.createElement("tr");
    const macCell = td(row.mac);
    if (row.status === "未应答") macCell.className = "task-no-response-mac";
    const statusCell = td(row.status);
    statusCell.className = row.status === "成功" ? "status-success"
      : ["失败", "未应答"].includes(row.status) ? "status-fail" : "";
    tr.append(macCell, td(row.directions), td(row.operation), td(row.sent_time), td(row.reply_time),
      statusCell, td(row.sequence));
    tr.addEventListener("click", () => toggleTaskConfigInlineDetail(tr, row));
    body.append(tr);
  }
}

function toggleTaskConfigInlineDetail(rowElement, record) {
  const next = rowElement.nextElementSibling;
  if (next?.classList.contains("task-config-inline-detail")) { next.remove(); return; }
  document.querySelectorAll(".task-config-inline-detail").forEach(item => item.remove());
  const detail = document.createElement("tr");
  detail.className = "task-config-inline-detail";
  const cell = document.createElement("td");
  cell.colSpan = 7;
  const title = document.createElement("div");
  title.textContent = `${record.mac} · ${record.status}`;
  cell.append(title);
  for (const frame of record.frames || []) {
    const item = document.createElement("div");
    item.className = `task-config-frame ${frame.direction}`;
    const colors = frame.direction === "downlink"
      ? { border: "#4dd27a", text: "#8af0a9", background: "rgba(77, 210, 122, .16)" }
      : { border: "#f2c14e", text: "#ffe08a", background: "rgba(242, 193, 78, .16)" };
    item.style.setProperty("border-left-color", colors.border);
    item.style.setProperty("color", colors.text);
    item.style.setProperty("background-color", colors.background);
    item.textContent = `${frame.label} · ${frame.log_time}\nAPS 原文：${frame.app_raw ? formatRawHex(frame.app_raw) : "（无 APP_RAW）"}`;
    cell.append(item);
  }
  detail.append(cell); rowElement.after(detail);
}

function formatRawHex(hex) {
  const bytes = hex.match(/../g) || [];
  return bytes.map((b, i) => (i > 0 && i % 8 === 0 ? "  " : "") + b).join(" ");
}

function td(text) {
  const cell = document.createElement("td");
  cell.textContent = text ?? "";
  return cell;
}

function emptyRow(colspan, message) {
  const tr = document.createElement("tr");
  tr.className = "empty-row";
  const cell = document.createElement("td");
  cell.colSpan = colspan;
  cell.textContent = message;
  tr.append(cell);
  return tr;
}

minuteElements.taskConfigRefresh.addEventListener("click", loadTaskConfigTasks);
minuteElements.taskConfigQuery.addEventListener("click", loadTaskConfigSummary);
minuteElements.taskConfigSelect.addEventListener("change", loadTaskConfigSummary);
minuteElements.taskConfigMacSort.addEventListener("click", () => { taskConfigMacAscending = !taskConfigMacAscending; renderTaskConfigRows(); });

function summarizeMinuteReports(reports) {
  let dataCount = 0;
  let noDataCount = 0;
  let duplicateCount = 0;
  const seen = new Map();
  for (const report of reports) {
    if (report.data_status === "已携带数据") {
      dataCount += 1;
      const key = report.application_raw;
      if (key) {
        const count = (seen.get(key) || 0) + 1;
        seen.set(key, count);
        if (count > 1) duplicateCount += 1;
      }
    } else {
      // 未携带采集数据即视为无数据：无数据 / 无冻结数据 / 任务不存在 /
      // 其他原因 / 应用层解析失败 / 响应结果未知，全部计入 noDataCount
      noDataCount += 1;
    }
  }
  return { dataCount, duplicateCount, noDataCount };
}

function renderMinuteReportDetails(period) {
  minuteElements.details.replaceChildren();
  const box = document.createElement("div");
  box.className = "period-detail";
  const title = document.createElement("h4");
  const { dataCount, duplicateCount, noDataCount } = summarizeMinuteReports(
    period.reports || []
  );
  title.textContent = `周期 ${period.description} · 实报 ${period.report_count} 帧 · 应报 ${period.expected_count ?? "未知"} · 缺报 ${(period.missing_stas || []).join("、") || "无"} · ${dataCount} 帧有数据 / ${noDataCount} 帧无数据 / ${duplicateCount} 帧重复上报`;
  box.append(title);
  (period.reports || []).forEach((report) => {
    const item = document.createElement("details");
    item.className = "minute-report-row";
    const label = document.createElement("summary");
    const stationMac = report.mac || report.source_mac || "未知 MAC";
    label.textContent = `${stationMac} / ${report.source_tei || "未知 TEI"} 模块上报 · 冻结时间 ${report.freeze_time || "未解析"}`;
    const status = document.createElement("p");
    status.className = "minute-report-status";
    const config = report.config_content || {};
    const configText = report.period_minutes
      ? ` · 实际配置周期 ${report.period_minutes} 分钟 · 启用 ${report.config_time || "手工筛选"}`
      : "";
    status.textContent = `STA ${report.mac || report.source_mac || "未知"} · 冻结 ${report.freeze_time || "未解析"} · 期望冻结 ${report.expected_freeze_time || "未知"} · ${report.freeze_ok ? "冻结正确" : "冻结异常"}${configText}${config.protocol_type ? ` · 协议 ${config.protocol_type}` : ""}`;
    const raw = document.createElement("pre");
    raw.className = "minute-app-raw";
    raw.textContent = report.application_raw || "应用层原文不可用";
    item.append(label, status, raw);
    box.append(item);
  });
  minuteElements.details.append(box);
}

minuteElements.query.addEventListener("click", loadMinuteAnalysis);
minuteElements.source.addEventListener("change", updateMinuteSourceControls);
updateMinuteSourceControls();
minuteElements.period.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadMinuteAnalysis();
});
minuteElements.task.addEventListener("change", refreshDerivedPeriod);
minuteElements.task.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadMinuteAnalysis();
});
loadMinuteTaskList();

// ---------- 串口实时采集 ----------

async function loadSerialPorts(preferredPort) {
  const current = preferredPort || elements.serialPort.value;
  try {
    const data = await request("/api/listener/serial/ports");
    const items = data.ports || [];
    const devices = items.map((p) => p.device);
    elements.serialPort.replaceChildren();
    if (!items.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "未发现可用串口";
      elements.serialPort.append(option);
      return;
    }
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.device;
      // 双标注：有 COM 映射显示 'COM4 (/dev/ttyUSB0)'，否则原样
      option.textContent = item.com
        ? `${item.com} (${item.device})`
        : item.device;
      elements.serialPort.append(option);
    });
    // 当前选择仍在枚举结果中则保留，否则自动选第一个实际存在的串口
    if (devices.includes(current)) {
      elements.serialPort.value = current;
    } else {
      elements.serialPort.value = devices[0];
    }
  } catch (error) {
    // 列表加载失败：保留一个占位选项，不阻塞使用
    elements.serialPort.replaceChildren();
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "串口列表加载失败";
    elements.serialPort.append(option);
  }
}

let serialPollTimer = null;

function setSerialState(active) {
  elements.serialStart.disabled = active;
  elements.serialStop.disabled = !active;
  const label = elements.serialState;
  if (active) {
    label.className = "serial-state running";
    label.textContent = "采集中";
  } else {
    label.className = "serial-state idle";
    label.textContent = "未启动";
  }
}

async function refreshSerialStatus() {
  try {
    const status = await request("/api/listener/serial/status");
    const active = status.state === "running" || status.state === "starting";
    setSerialState(active);
    if (status.state === "running") {
      elements.serialState.textContent = "采集中 · " + (status.frame_count || 0) + " 帧";
      elements.serialState.className = "serial-state running";
      state.lastFrameCount = status.frame_count || 0;
    } else if (status.state === "error") {
      elements.serialState.className = "serial-state error";
      elements.serialState.textContent = "错误";
      elements.serialMessage.textContent = status.message || "串口采集出错";
      stopSerialPolling();
    } else if (status.state === "stopped") {
      elements.serialMessage.textContent =
        "已停止，本次共采集 " + (status.frame_count || 0) + " 帧";
      stopSerialPolling();
    } else {
      elements.serialMessage.textContent = status.message || "串口未启动";
    }
    return status;
  } catch (error) {
    return null;
  }
}

function startSerialPolling() {
  if (serialPollTimer) clearInterval(serialPollTimer);
  const tick = async () => {
    const prevCount = state.lastFrameCount;
    await refreshSerialStatus();
    const nowCount = state.lastFrameCount;
    // 仅在「帧数变化」且「停在首页（offset=0）」时清缓存并自动刷新帧列表。
    // 防重入令牌保证同参数请求只发一次、过期响应被丢弃，不会堆积。
    // 深翻页/筛选中暂停自动刷新，由「刷新帧列表」按钮手动触发。
    const onFirstPage = state.offset === 0;
    if (onFirstPage && nowCount !== prevCount) {
      state.pageCache.clear();
      loadFrames();
    }
  };
  tick();
  // 节流：安静期（无新帧）降低轮询频率，减少无效请求
  serialPollTimer = setInterval(tick, 1500);
}

function stopSerialPolling() {
  if (serialPollTimer) {
    clearInterval(serialPollTimer);
    serialPollTimer = null;
  }
}

async function startSerial() {
  const port = elements.serialPort.value.trim() || "COM19";
  const baud = Number(elements.serialBaud.value) || 115200;
  const bytesize = Number(elements.serialBytesize.value) || 8;
  const parity = elements.serialParity.value || "N";
  const stopbits = Number(elements.serialStopbits.value) || 1;
  elements.serialStart.disabled = true;
  elements.serialMessage.textContent = `正在打开 ${port} ...`;
  try {
    await request("/api/listener/serial/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port, baudrate: baud, bytesize, parity, stopbits }),
    });
    elements.serialMessage.textContent = `正在监听 ${port} (${baud}, ${parity}, ${bytesize}, ${stopbits})`;
    elements.serialRefresh.disabled = false;
    startSerialPolling();
  } catch (error) {
    elements.serialMessage.textContent = error.message;
    elements.serialStart.disabled = false;
  }
}

async function stopSerial() {
  try {
    await request("/api/listener/serial/stop", { method: "POST" });
    elements.serialMessage.textContent = "正在停止串口采集...";
    elements.serialRefresh.disabled = true;
    stopSerialPolling();
    await refreshSerialStatus();
  } catch (error) {
    elements.serialMessage.textContent = error.message;
  }
}

elements.serialStart.addEventListener("click", startSerial);
elements.serialStop.addEventListener("click", stopSerial);
elements.serialPortRefresh.addEventListener("click", loadSerialPorts);
elements.serialRefresh.addEventListener("click", () => {
  // 手动刷新：清缓存后重新加载当前页，供深翻页/筛选中使用
  state.pageCache.clear();
  loadFrames();
});
// 串口状态恢复在数据源切换逻辑初始化后执行，避免默认日志模式覆盖后端运行态。

// ---------- 数据源二选一：日志文件分析 / 串口实时监听 ----------

const sourceRadios = document.querySelectorAll('input[name="data-source"]');
let dataSourceSwitchBusy = false;

function applyDataSourceMode(mode) {
  document.body.setAttribute("data-source", mode);
}

function clearFrameListForSwitch() {
  // 切换数据源后清空当前帧列表与详情，避免显示旧模式数据
  state.offset = 0;
  state.total = 0;
  state.query = "";
  state.nid = "";
  state.pageCache.clear();
  state.selectedId = null;
  state.lastFrameCount = -1;
  if (typeof resetDetail === "function") resetDetail();
  if (elements.rows) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = "切换数据源后，请重新建立索引或启动串口采集";
    row.append(cell);
    elements.rows.replaceChildren(row);
  }
  if (elements.pageSummary) elements.pageSummary.textContent = "0 帧";
  if (elements.pageNumber) elements.pageNumber.textContent = "第 1 页";
}

sourceRadios.forEach((radio) => {
  radio.addEventListener("change", async (event) => {
    if (dataSourceSwitchBusy) return;
    const mode = event.target.value;
    const previous = document.body.getAttribute("data-source") || "log";

    // 串口监听模式
    if (mode === "serial") {
      dataSourceSwitchBusy = true;
      try {
        // 数据源二选一：日志索引运行中时禁止切到串口，避免数据混在一起
        const status = await request("/api/listener/logs/status").catch(() => null);
        if (status && (status.state === "indexing" || status.state === "queued")) {
          event.target.checked = false;
          document.querySelector('input[name="data-source"][value="log"]').checked = true;
          alert("日志正在建立索引，请等待完成后再切换串口监听。");
          return;
        }
        applyDataSourceMode("serial");
        clearFrameListForSwitch();
        loadSerialPorts();
      } finally {
        dataSourceSwitchBusy = false;
      }
    }

    // 日志文件分析模式
    if (mode === "log") {
      dataSourceSwitchBusy = true;
      try {
        // 数据源二选一：串口采集运行中时禁止切到日志，避免数据混在一起
        const status = await request("/api/listener/serial/status").catch(() => null);
        if (status && (status.state === "running" || status.state === "starting")) {
          event.target.checked = false;
          document.querySelector('input[name="data-source"][value="serial"]').checked = true;
          alert("串口监听正在运行，请先停止串口采集再切换日志分析。");
          return;
        }
        applyDataSourceMode("log");
        clearFrameListForSwitch();
      } finally {
        dataSourceSwitchBusy = false;
      }
    }
  });
});
// 初始化当前模式（默认日志）；若后端串口仍在运行，随后以运行态覆盖。
applyDataSourceMode(document.querySelector('input[name="data-source"]:checked')?.value || "log");

async function loadIndexedDetailFromLocation() {
  const params = new URLSearchParams(window.location.search);
  const indexId = params.get("index_id");
  const frameId = params.get("frame_id");
  if (!indexId || !frameId || !/^[0-9]+$/.test(frameId)) return;

  state.selectedId = Number(frameId);
  elements.detailEmpty.hidden = false;
  elements.detailEmpty.querySelector("h2").textContent = "正在打开指定索引帧…";
  elements.detailContent.hidden = true;
  try {
    const url = "/api/listener/listener/indexes/" + encodeURIComponent(indexId) +
      "/frames/" + encodeURIComponent(frameId);
    renderDetail(await request(url));
  } catch (error) {
    elements.detailEmpty.querySelector("h2").textContent = "指定索引帧无法打开";
    showError(error.message);
  }
}

async function restoreSerialSession() {
  const status = await refreshSerialStatus();
  await loadSerialPorts(status && status.port ? status.port : "");

  const active = status && (status.state === "running" || status.state === "starting");
  if (!active) return status;

  const serialRadio = document.querySelector('input[name="data-source"][value="serial"]');
  if (serialRadio) serialRadio.checked = true;
  applyDataSourceMode("serial");

  if (status.port) elements.serialPort.value = status.port;
  if (status.baudrate) elements.serialBaud.value = String(status.baudrate);
  if (status.bytesize) elements.serialBytesize.value = String(status.bytesize);
  if (status.parity) elements.serialParity.value = String(status.parity);
  if (status.stopbits) elements.serialStopbits.value = String(status.stopbits);
  elements.serialRefresh.disabled = false;
  startSerialPolling();
  return status;
}

restoreSerialSession().then(loadIndexedDetailFromLocation);

// ---------- 网络承载评估 ----------
// 对接后端：GET /api/listener/network/status（轻量快照）、GET /api/listener/network/assessment（周期明细+汇总）。
// 后端未就绪时返回 503，前端需明确提示「后端未就绪」而非白屏。

const networkElements = {
  view: $("#network-assessment-view"),
  error: $("#network-error"),
  refresh: $("#network-refresh"),
  assessment: $("#network-assessment-button"),
  beaconPeriod: $("#network-beacon-period"),
  latestRate: $("#network-latest-rate"),
  latestRating: $("#network-latest-rating"),
  overallHealth: $("#network-overall-health"),
  chart: $("#network-trend-chart"),
  rows: $("#network-cycle-rows"),
};

// 评级 → 中文标签 + 着色 class。兼容英文/中文/其他取值，未知评级归为灰色。
function ratingMeta(rating) {
  const r = String(rating ?? "").trim().toLowerCase();
  if (["healthy", "good", "正常", "健康", "ok", "normal"].includes(r)) {
    return { label: "健康", className: "rating-healthy" };
  }
  if (["degraded", "warning", "warn", "亚健康", "一般", "告警"].includes(r)) {
    return { label: "亚健康", className: "rating-degraded" };
  }
  if (["fault", "error", "fail", "故障", "异常", "危险"].includes(r)) {
    return { label: "故障", className: "rating-fault" };
  }
  return { label: rating || "未知", className: "rating-unknown" };
}

function formatBeaconPeriod(ms) {
  const v = Number(ms);
  if (!Number.isFinite(v) || v <= 0) return "—";
  if (v < 1000) return `${Math.round(v)} ms`;
  if (v < 60000) return `${(v / 1000).toFixed(v % 1000 === 0 ? 0 : 1)} 秒`;
  return `${(v / 60000).toFixed(1)} 分钟`;
}

function formatRate(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(1)}%`;
}

function formatCount(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString();
}

// 专用 fetch：保留 HTTP 状态码，供 503 降级判断
async function fetchNetwork(url) {
  let response;
  try {
    response = await fetch(url);
  } catch {
    const error = new Error("网络请求失败，后端可能未启动");
    error.status = 0;
    throw error;
  }
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const error = new Error((payload && (payload.detail || payload.message)) || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setNetworkLoading(loading) {
  networkElements.view.querySelector(".list-panel")?.classList.toggle("is-loading", loading);
}

function showNetworkError(message) {
  networkElements.error.textContent = message;
  networkElements.error.hidden = false;
}

// 后端未就绪（503 / 无法连接）：快照区显示占位，不白屏
function showNetworkNotReady() {
  const placeholder = "后端未就绪";
  networkElements.beaconPeriod.textContent = placeholder;
  networkElements.latestRate.textContent = placeholder;
  networkElements.latestRating.textContent = placeholder;
  networkElements.latestRating.className = "network-stat-value rating-unknown";
  networkElements.overallHealth.textContent = placeholder;
  networkElements.overallHealth.className = "network-stat-value rating-unknown";
  showNetworkError("网络承载评估后端未就绪（503），请确认 listener 后端已实现并启动 /api/listener/network/* 接口。");
}

function renderNetworkSnapshot(data) {
  const cycle = data.latest_cycle || (data.cycles && data.cycles[0]) || {};
  const rate = cycle.success_rate ?? data.latest_success_rate ?? data.success_rate;
  const rating = cycle.rating ?? data.latest_rating ?? data.rating;
  const health = data.overall_health ?? data.health ?? data.overall;

  networkElements.beaconPeriod.textContent = formatBeaconPeriod(data.beacon_period_ms ?? data.beacon_period);
  networkElements.latestRate.textContent = formatRate(rate);

  const ratingInfo = ratingMeta(rating);
  networkElements.latestRating.textContent = ratingInfo.label;
  networkElements.latestRating.className = `network-stat-value ${ratingInfo.className}`;

  const healthInfo = ratingMeta(health);
  networkElements.overallHealth.textContent = healthInfo.label;
  networkElements.overallHealth.className = `network-stat-value ${healthInfo.className}`;
}

function renderNetworkAssessment(data) {
  renderNetworkSnapshot(data);
  const cycles = Array.isArray(data.cycles) ? data.cycles : [];
  networkElements.rows.replaceChildren();

  if (!cycles.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.textContent = "暂无周期评估数据";
    row.append(cell);
    networkElements.rows.append(row);
    drawSuccessRateChart([]);
    return;
  }

  for (const cycle of cycles) {
    const row = document.createElement("tr");
    const ratingInfo = ratingMeta(cycle.rating);

    const cells = [
      cycle.start_time && cycle.end_time
        ? `${cycle.start_time} ~ ${cycle.end_time}`
        : cycle.start_time || cycle.end_time || "—",
      formatBeaconPeriod(cycle.beacon_period_ms ?? cycle.beacon_period ?? data.beacon_period_ms),
      formatCount(cycle.frame_count),
      formatRate(cycle.success_rate),
      formatRate(cycle.offline_rate),
      formatCount(cycle.active_sta_count),
    ];
    cells.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 0) cell.className = "route";
      row.append(cell);
    });

    const ratingCell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `status-pill ${ratingInfo.className}`;
    pill.textContent = ratingInfo.label;
    ratingCell.append(pill);
    row.append(ratingCell);

    networkElements.rows.append(row);
  }

  drawSuccessRateChart(cycles);
}

// 原生 canvas 折线图：周期成功率趋势（不引入外部库）
function drawSuccessRateChart(cycles) {
  const canvas = networkElements.chart;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || 640;
  const cssHeight = 180;
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const pad = { left: 40, right: 14, top: 14, bottom: 26 };
  const plotW = Math.max(1, cssWidth - pad.left - pad.right);
  const plotH = Math.max(1, cssHeight - pad.top - pad.bottom);

  // 背景
  ctx.fillStyle = "#0a141e";
  ctx.fillRect(0, 0, cssWidth, cssHeight);

  // 网格 + Y 轴刻度（0–100%）
  ctx.strokeStyle = "rgba(83, 102, 120, .16)";
  ctx.fillStyle = "#698095";
  ctx.font = "9px Consolas, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i += 1) {
    const value = 100 - i * 25;
    const y = pad.top + (i / 4) * plotH;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(pad.left + plotW, y);
    ctx.stroke();
    ctx.fillText(`${value}%`, pad.left - 7, y);
  }

  if (!cycles.length) {
    ctx.fillStyle = "#536678";
    ctx.font = "11px Consolas, monospace";
    ctx.textAlign = "center";
    ctx.fillText("暂无趋势数据", pad.left + plotW / 2, pad.top + plotH / 2);
    return;
  }

  const points = cycles.map((cycle, index) => {
    const rate = Number(cycle.success_rate);
    if (!Number.isFinite(rate)) return null;
    const x = pad.left + (cycles.length === 1 ? plotW / 2 : (index / (cycles.length - 1)) * plotW);
    const y = pad.top + ((100 - Math.max(0, Math.min(100, rate))) / 100) * plotH;
    return { x, y, rating: cycle.rating };
  }).filter(Boolean);

  // 连线
  if (points.length > 1) {
    ctx.beginPath();
    ctx.strokeStyle = "#45e0c2";
    ctx.lineWidth = 1.6;
    ctx.lineJoin = "round";
    points.forEach((point, index) => {
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();

    // 线下渐变填充
    const last = points[points.length - 1];
    const first = points[0];
    ctx.lineTo(last.x, pad.top + plotH);
    ctx.lineTo(first.x, pad.top + plotH);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
    gradient.addColorStop(0, "rgba(69, 224, 194, .22)");
    gradient.addColorStop(1, "rgba(69, 224, 194, 0)");
    ctx.fillStyle = gradient;
    ctx.fill();
  }

  // 数据点（按评级着色）
  points.forEach((point) => {
    const meta = ratingMeta(point.rating);
    const color = meta.className === "rating-healthy" ? "#4dd27a"
      : meta.className === "rating-degraded" ? "#f2c14e" : "#ff7385";
    ctx.beginPath();
    ctx.fillStyle = color;
    ctx.arc(point.x, point.y, 2.6, 0, Math.PI * 2);
    ctx.fill();
  });

  // X 轴首尾时间标注
  ctx.fillStyle = "#698095";
  ctx.font = "9px Consolas, monospace";
  ctx.textBaseline = "top";
  const firstCycle = cycles[0];
  const lastCycle = cycles[cycles.length - 1];
  ctx.textAlign = "left";
  ctx.fillText(firstCycle.start_time || "", pad.left, pad.top + plotH + 7);
  ctx.textAlign = "right";
  ctx.fillText(lastCycle.end_time || lastCycle.start_time || "", pad.left + plotW, pad.top + plotH + 7);
}

async function loadNetworkStatus() {
  if (!networkElements.view) return;
  networkElements.error.hidden = true;
  setNetworkLoading(true);
  try {
    renderNetworkSnapshot(await fetchNetwork("/api/listener/network/status"));
  } catch (error) {
    if (error.status === 503) showNetworkNotReady();
    else showNetworkError(`快照加载失败：${error.message}`);
  } finally {
    setNetworkLoading(false);
  }
}

async function loadNetworkAssessment() {
  networkElements.error.hidden = true;
  setNetworkLoading(true);
  try {
    renderNetworkAssessment(await fetchNetwork("/api/listener/network/assessment"));
  } catch (error) {
    if (error.status === 503) showNetworkNotReady();
    else showNetworkError(`详细评估加载失败：${error.message}`);
  } finally {
    setNetworkLoading(false);
  }
}

networkElements.refresh.addEventListener("click", loadNetworkStatus);
networkElements.assessment.addEventListener("click", loadNetworkAssessment);
