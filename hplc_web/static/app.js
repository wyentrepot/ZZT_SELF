const PAGE_SIZE = 100;
const SAMPLE_PATH = "hplc_web\\tests\\data\\gw_log_sample.txt";

const state = {
  offset: 0,
  total: 0,
  query: "",
  selectedId: null,
  pollTimer: null,
  lastFrameCount: -1,
  detail: null,
};

const $ = (selector) => document.querySelector(selector);
const elements = {
  path: $("#log-path"),
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
  prev: $("#prev-page"),
  next: $("#next-page"),
  pageNumber: $("#page-number"),
  pageSummary: $("#page-summary"),
  detailEmpty: $("#detail-empty"),
  detailContent: $("#detail-content"),
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
    cell.colSpan = 7;
    cell.textContent = state.query ? "没有符合当前筛选条件的帧" : "正在等待日志帧…";
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
      `${summaryValue(summary, "SRC", "源地址")} → ${summaryValue(summary, "DST", "目的地址")}`,
      summaryValue(summary.Info2 || summary.Info || summary, "ChType", "通道"),
      `${frame.byte_length} B`,
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 0) cell.className = "number-cell";
      if (index === 3) cell.className = "route";
      if (index === 5) cell.className = "length-cell";
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
}

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
  state.offset = 0;
  loadFrames();
});
elements.filter.addEventListener("keydown", (event) => {
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
