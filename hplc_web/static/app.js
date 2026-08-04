const PAGE_SIZE = 100;
const SAMPLE_PATH = "hplc_web\\tests\\data\\gw_log_sample.txt";

const state = {
  offset: 0,
  total: 0,
  query: "",
  nid: "",
  selectedId: null,
  pollTimer: null,
  lastFrameCount: -1,
  detail: null,
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
  prev: $("#prev-page"),
  next: $("#next-page"),
  pageNumber: $("#page-number"),
  pageSummary: $("#page-summary"),
  detailEmpty: $("#detail-empty"),
  detailContent: $("#detail-content"),
  detailTabs: document.querySelectorAll(".detail-tab"),
  detailBase: $("#detail-base"),
  detailApp: $("#detail-app"),
  appExpandContent: $("#app-expand-content"),
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
    await request("/api/logs/open", {
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
  request("/api/fs/last")
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
  request(`/api/fs/list?path=${encodeURIComponent(picker.currentDir)}`)
    .then((data) => {
      if (data.parent) pickerList(data.parent);
    })
    .catch(() => {});
});
picker.confirm.addEventListener("click", pickerConfirm);
elements.pick.addEventListener("click", async () => {
  if (elements.pick.disabled) return;
  elements.pick.disabled = true;
  clearError();
  try {
    const data = await request("/api/fs/pick");
    if (data.path) {
      elements.path.value = data.path;
      localStorage.setItem("hplc-log-path", data.path);
    }
  } catch (error) {
    showError(`文件选择失败：${error.message}`);
  } finally {
    elements.pick.disabled = false;
  }
});

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
      updateStatus(await request("/api/logs/status"));
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

  const currentPage = Math.floor(state.offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  elements.pageNumber.textContent = `第 ${currentPage} / ${pageCount} 页`;
  elements.pageSummary.textContent = `共 ${state.total.toLocaleString()} 帧 · 每页 ${PAGE_SIZE}`;
  elements.prev.disabled = state.offset === 0;
  elements.next.disabled = state.offset + PAGE_SIZE >= state.total;
}

async function loadFrames() {
  const params = new URLSearchParams({
    offset: state.offset,
    limit: PAGE_SIZE,
    query: state.query,
    nid: state.nid,
  });
  try {
    renderFrames(await request(`/api/logs/frames?${params}`));
  } catch (error) {
    showError(error.message);
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
    renderDetail(await request(`/api/logs/frames/${id}`));
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
    const data = await request("/api/parse", {
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
  state.nid = elements.nidFilter.value.trim();
  state.offset = 0;
  loadFrames();
});
elements.filter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("#filter-button").click();
});
elements.nidFilter.addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("#filter-button").click();
});
elements.prev.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  loadFrames();
});
elements.next.addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  loadFrames();
});
$("#copy-detail").addEventListener("click", async () => {
  if (!state.detail) return;
  await navigator.clipboard.writeText(JSON.stringify(state.detail.analysis, null, 2));
  $("#copy-detail").textContent = "已复制";
  setTimeout(() => { $("#copy-detail").textContent = "复制"; }, 1000);
});

elements.path.value = localStorage.getItem("hplc-log-path") || "";
fetch("/api/version")
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
  request("/api/logs/status").then(updateStatus).catch(() => {});
}

// ---------- 分钟采集分析 ----------

const minuteElements = {
  view: $("#minute-view"),
  framesView: $("#frames-view"),
  framesData: $("#frames-data"),
  deleteConfigView: $("#delete-config-view"),
  tabs: document.querySelectorAll(".view-tab"),
  period: $("#period-input"),
  ccoTei: $("#cco-tei-input"),
  query: $("#minute-query-button"),
  error: $("#minute-error"),
  nidHint: $("#minute-nid-hint"),
  rows: $("#minute-period-table"),
  details: $("#minute-report-details"),
  deleteStats: $("#minute-delete-stats"),
  deleteDown: $("#delete-down-deduped"),
  deleteUp: $("#delete-up-deduped"),
  deleteUpSuccess: $("#delete-up-success"),
  deleteUpFail: $("#delete-up-fail"),
  deleteDetail: $("#delete-detail-button"),
  deleteConfigTei: $("#delete-config-tei-input"),
  deleteConfigRefresh: $("#delete-config-refresh"),
  deleteConfigBack: $("#delete-config-back"),
  deleteConfigError: $("#delete-config-error"),
  deleteConfigNidHint: $("#delete-config-nid-hint"),
  deleteDownCount: $("#delete-down-count"),
  deleteUpCount: $("#delete-up-count"),
  deleteDownTable: $("#delete-down-table"),
  deleteUpTable: $("#delete-up-table"),
  deleteConfigRaw: $("#delete-config-raw"),
  deleteConfigRawMeta: $("#delete-config-raw-meta"),
  deleteConfigRawText: $("#delete-config-raw-text"),
};

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
}

minuteElements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
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
    period_minutes: minuteElements.period.value,
    cco_tei: tei,
    nid: state.nid,
  });
  minuteElements.error.hidden = true;
  try {
    renderMinuteAnalysis(
      await request(`/api/logs/minute-analysis?${params}`)
    );
  } catch (error) {
    minuteElements.error.textContent = error.message;
    minuteElements.error.hidden = false;
  }
}

function renderMinuteAnalysis(data) {
  minuteElements.rows.replaceChildren();
  minuteElements.details.replaceChildren();
  renderDeleteConfigStats(data.delete_config_stats);
  if (!data.periods.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 1;
    cell.textContent = "没有符合当前筛选条件的周期";
    row.append(cell);
    minuteElements.rows.append(row);
    return;
  }

  for (const period of data.periods) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.className = "route";
    cell.textContent = `CCO ${data.filters.cco_tei} 在 ${period.description} 周期收到 ${period.report_count} 帧上报`;
    row.append(cell);
    row.addEventListener("click", () => renderMinuteReportDetails(period));
    minuteElements.rows.append(row);
  }
}

function renderDeleteConfigStats(stats) {
  const box = minuteElements.deleteStats;
  if (!stats || typeof stats.down_deduped !== "number") {
    box.hidden = true;
    return;
  }
  minuteElements.deleteDown.textContent = String(stats.down_deduped);
  minuteElements.deleteUp.textContent = String(stats.up_deduped);
  minuteElements.deleteUpSuccess.textContent = String(stats.up_success);
  minuteElements.deleteUpFail.textContent = String(stats.up_fail);
  box.hidden = false;
}

async function loadDeleteConfigDetails() {
  const tei = (minuteElements.deleteConfigTei.value.trim() || "001").toUpperCase();
  minuteElements.deleteConfigTei.value = tei;
  updateNidHint(minuteElements.deleteConfigNidHint);
  const params = new URLSearchParams({
    cco_tei: tei,
    nid: state.nid,
  });
  minuteElements.deleteConfigError.hidden = true;
  try {
    const data = await request(`/api/logs/delete-config-details?${params}`);
    renderDeleteConfigDetails(data);
    switchView("delete-config");
  } catch (error) {
    minuteElements.deleteConfigError.textContent = error.message;
    minuteElements.deleteConfigError.hidden = false;
  }
}

function renderDeleteConfigDetails(data) {
  minuteElements.deleteDownCount.textContent = String(data.down_count);
  minuteElements.deleteUpCount.textContent = String(data.up_count);
  minuteElements.deleteConfigRaw.hidden = true;

  const downBody = minuteElements.deleteDownTable;
  downBody.replaceChildren();
  if (!data.down.length) {
    downBody.append(emptyRow(4, "无下发明细"));
  } else {
    for (const r of data.down) {
      const tr = document.createElement("tr");
      tr.append(td(r.log_time), td(r.mac), td(r.task_no), td(r.seq));
      tr.addEventListener("click", () => showDeleteConfigRaw(r, "下行"));
      downBody.append(tr);
    }
  }

  const upBody = minuteElements.deleteUpTable;
  upBody.replaceChildren();
  if (!data.up.length) {
    upBody.append(emptyRow(6, "无上行应答明细"));
  } else {
    for (const r of data.up) {
      const tr = document.createElement("tr");
      const resultCell = td(r.result);
      resultCell.className = r.result === "成功" ? "status-success" : "status-fail";
      tr.append(td(r.log_time), td(r.mac), td(r.task_no), td(r.seq),
        td(r.del_flag || ""), resultCell);
      tr.addEventListener("click", () => showDeleteConfigRaw(r, "上行"));
      upBody.append(tr);
    }
  }
}

function showDeleteConfigRaw(record, kind) {
  const raw = record.app_raw || "";
  minuteElements.deleteConfigRawMeta.textContent =
    `${kind} · ${record.log_time} · ${record.mac} · 任务号 ${record.task_no}` +
    (record.result ? ` · ${record.result}` : "");
  minuteElements.deleteConfigRawText.textContent = raw
    ? formatRawHex(raw)
    : "（无 APP_RAW）";
  minuteElements.deleteConfigRaw.hidden = false;
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

minuteElements.deleteDetail.addEventListener("click", loadDeleteConfigDetails);
minuteElements.deleteConfigRefresh.addEventListener("click", loadDeleteConfigDetails);
minuteElements.deleteConfigBack.addEventListener("click", () => switchView("minute"));

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
  title.textContent = `周期 ${period.description} · ${period.report_count} 帧上报 · ${dataCount} 帧有数据（${duplicateCount} 帧重复上报） · ${noDataCount} 帧无数据`;
  box.append(title);
  (period.reports || []).forEach((report) => {
    const item = document.createElement("details");
    item.className = "minute-report-row";
    const label = document.createElement("summary");
    label.textContent = `${report.source_mac || "未知 MAC"} / ${report.source_tei || "未知 TEI"} 模块上报 · 冻结时间 ${report.freeze_time || "未解析"}`;
    const status = document.createElement("p");
    status.className = "minute-report-status";
    status.textContent = `数据状态：${report.data_status || "未确认"} · 响应结果 ${report.response_result ?? "未解析"} · 上报数量 ${report.report_count ?? "未解析"} · 数据长度 ${report.data_length ?? "未解析"} 字节`;
    const raw = document.createElement("pre");
    raw.className = "minute-app-raw";
    raw.textContent = report.application_raw || "应用层原文不可用";
    item.append(label, status, raw);
    box.append(item);
  });
  minuteElements.details.append(box);
}

minuteElements.query.addEventListener("click", loadMinuteAnalysis);
minuteElements.period.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadMinuteAnalysis();
});
