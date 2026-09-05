"""REQS-0024 阶段4 前端补丁:向侦听台页面双副本写入「组网观测」页签(一次性脚本)。"""
import io
import re

WORKBENCH = r"D:\2-侦听台改造\apps\workbench\static\pages\listener"
STANDALONE = r"D:\2-侦听台改造\apps\listener\static"

TAB_HTML = '''      <button class="view-tab" type="button" role="tab"
        data-view="network-observe" aria-selected="false">组网观测</button>
'''

SECTION_HTML = '''
    <section id="network-observe-view" class="network-observe" aria-label="组网观测" hidden>
      <div class="list-panel">
        <div class="panel-toolbar">
          <div>
            <span class="section-kicker">06 / 组网观测</span>
            <h2>4-2 链路层组网事件流（REQS-0024）</h2>
          </div>
          <div class="network-toolbar">
            <span id="network-observe-scan" class="observe-scan-state"></span>
            <button id="network-observe-refresh" class="primary-button" type="button">刷新</button>
          </div>
        </div>

        <div id="network-observe-error" class="error-banner" role="alert" hidden></div>

        <div class="observe-filter">
          <select id="network-observe-group" class="observe-select" aria-label="事件分组">
            <option value="">全部分组</option>
            <option value="组网">组网</option>
            <option value="维护">维护</option>
            <option value="冲突">冲突</option>
            <option value="路由">路由</option>
            <option value="信标">信标</option>
            <option value="业务">业务</option>
          </select>
          <select id="network-observe-event" class="observe-select" aria-label="事件类型">
            <option value="">全部事件</option>
          </select>
          <select id="network-observe-direction" class="observe-select" aria-label="方向">
            <option value="">全部方向</option>
            <option value="down">下行（CCO→）</option>
            <option value="up">上行（→CCO）</option>
            <option value="mesh">中继段</option>
          </select>
          <input id="network-observe-query" class="observe-query" type="text"
                 placeholder="按 TEI / 摘要搜索" maxlength="64" />
        </div>

        <div id="network-observe-cards" class="observe-cards"></div>
        <div id="network-observe-beacon" class="observe-beacon"></div>

        <div class="table-shell observe-table">
          <table>
            <thead>
              <tr>
                <th>时间</th><th>事件</th><th>方向</th><th>源</th><th>目的</th><th>摘要</th>
              </tr>
            </thead>
            <tbody id="network-observe-rows">
              <tr><td colspan="6" class="observe-empty">打开日志索引后自动扫描组网事件</td></tr>
            </tbody>
          </table>
        </div>
        <div id="network-observe-detail" class="observe-detail" hidden></div>
      </div>
    </section>
'''

CSS = '''

/* ===== 组网观测（REQS-0024） ===== */
.network-observe .observe-filter {
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--color-border-default);
}
.observe-select, .observe-query {
  background: var(--color-bg-input); color: var(--color-fg-default);
  border: 1px solid var(--color-border-default); border-radius: 6px;
  padding: 6px 8px; font-size: 12px;
}
.observe-query { flex: 1 1 160px; min-width: 140px; }
.observe-scan-state { color: var(--color-fg-subtle); font-size: 12px; margin-right: 8px; }
.observe-cards {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px; padding: 10px 12px;
}
.observe-card {
  background: var(--color-bg-raised); border: 1px solid var(--color-border-default);
  border-radius: 8px; padding: 8px 10px;
}
.observe-card .observe-card-label {
  display: block; color: var(--color-fg-muted); font-size: 11px; margin-bottom: 4px;
}
.observe-card .observe-card-value {
  color: var(--color-fg-default); font-size: 15px; font-weight: 600;
  font-family: var(--font-mono);
}
.observe-card.is-bad .observe-card-value { color: var(--color-status-fail); }
.observe-beacon { padding: 0 12px 10px; }
.observe-beacon .observe-beacon-title {
  color: var(--color-fg-muted); font-size: 12px; margin-bottom: 6px;
}
.observe-period-bar {
  display: flex; height: 26px; border-radius: 6px; overflow: hidden;
  border: 1px solid var(--color-border-default); font-size: 11px;
  color: var(--color-fg-default);
}
.observe-period-seg {
  display: flex; align-items: center; justify-content: center;
  white-space: nowrap; overflow: hidden; min-width: 18px;
}
.observe-period-seg.seg-beacon { background: var(--color-accent-soft); }
.observe-period-seg.seg-tdma { background: var(--color-status-info); color: #04121f; }
.observe-period-seg.seg-csma { background: var(--color-status-pass); color: #04140a; }
.observe-period-seg.seg-bind { background: var(--color-dir-tx); color: #141007; }
.observe-phase-chips { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.observe-phase-chip {
  font-size: 11px; color: var(--color-fg-muted);
  border: 1px solid var(--color-border-default); border-radius: 10px; padding: 2px 8px;
}
.observe-table td.observe-dir-down { color: var(--color-dir-tx); }
.observe-table td.observe-dir-up { color: var(--color-status-info); }
.observe-table td.observe-dir-mesh { color: var(--color-fg-subtle); }
.observe-table tbody tr { cursor: pointer; }
.observe-table tbody tr.observe-row-active { background: var(--color-bg-input); }
.observe-empty { color: var(--color-fg-subtle); text-align: center; padding: 18px 0; }
.observe-detail {
  margin: 0 12px 12px; padding: 10px 12px; border-radius: 8px;
  background: var(--color-bg-canvas); border: 1px solid var(--color-border-default);
  font-size: 12px; max-height: 260px; overflow: auto;
}
.observe-detail .observe-detail-title {
  color: var(--color-fg-default); font-weight: 600; margin-bottom: 6px;
}
.observe-detail pre {
  margin: 0; white-space: pre-wrap; word-break: break-all;
  font-family: var(--font-mono); color: var(--color-fg-muted);
}
'''

JS = '''
// ===== 组网观测（REQS-0024）：GET {P}/network/overview · /network/events · /network/beacons =====
// 事件流由后端增量扫描 frames 表并按 adapter_dualmac 解析落库（nwk_events）。
var observeElements = {
  view: document.getElementById("network-observe-view"),
  refresh: document.getElementById("network-observe-refresh"),
  group: document.getElementById("network-observe-group"),
  event: document.getElementById("network-observe-event"),
  direction: document.getElementById("network-observe-direction"),
  query: document.getElementById("network-observe-query"),
  scan: document.getElementById("network-observe-scan"),
  cards: document.getElementById("network-observe-cards"),
  beacon: document.getElementById("network-observe-beacon"),
  rows: document.getElementById("network-observe-rows"),
  detail: document.getElementById("network-observe-detail"),
  error: document.getElementById("network-observe-error"),
};

const OBSERVE_DIR_TEXT = { up: "上行", down: "下行", mesh: "中继" };
let observeEventNames = {};

function observeQuery(overrides = {}) {
  const params = networkRequestParams();
  const map = {
    group: observeElements.group ? observeElements.group.value : "",
    event: observeElements.event ? observeElements.event.value : "",
    direction: observeElements.direction ? observeElements.direction.value : "",
    query: observeElements.query ? observeElements.query.value.trim() : "",
  };
  Object.entries({ ...map, ...overrides }).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

async function loadNetworkObserve() {
  if (!observeElements.view || observeElements.view.hidden) return;
  observeElements.error.hidden = true;
  try {
    const [overview, events, beacons] = await Promise.all([
      fetchNetwork(`/api/{P}/network/overview?${networkRequestParams()}`),
      fetchNetwork(`/api/{P}/network/events?${observeQuery({ event: "", group: "" })}&limit=200`),
      fetchNetwork(`/api/{P}/network/beacons?${networkRequestParams()}&limit=1`),
    ]);
    if (observeElements.event && observeElements.event.options.length <= 1) {
      Object.entries((overview.groups || {})).forEach(([, list]) => {
        list.forEach((key) => {
          if ([...observeElements.event.options].some((o) => o.value === key)) return;
          const option = document.createElement("option");
          option.value = key;
          option.textContent = observeEventNames[key] || key;
          observeElements.event.appendChild(option);
        });
      });
    }
    renderObserveCards(overview);
    renderObserveBeacon((beacons.events || [])[0] || null);
    renderObserveRows(events.events || []);
    observeElements.scan.textContent = events.refresh && events.refresh.pending
      ? `增量扫描中（已扫 ${events.refresh.last_frame_id} 帧，再点刷新继续）`
      : `已扫描 ${overview.link_counters ? overview.link_counters.frames_total || 0 : 0} 帧`;
  } catch (error) {
    observeElements.error.textContent =
      error.status === 503 ? "后端未就绪（日志服务未启用）" : `组网观测加载失败：${error.message}`;
    observeElements.error.hidden = false;
  }
}

function observeCard(label, value, bad = false) {
  const card = document.createElement("div");
  card.className = "observe-card" + (bad ? " is-bad" : "");
  const labelEl = document.createElement("span");
  labelEl.className = "observe-card-label";
  labelEl.textContent = label;
  const valueEl = document.createElement("strong");
  valueEl.className = "observe-card-value";
  valueEl.textContent = value;
  card.append(labelEl, valueEl);
  return card;
}

function renderObserveCards(overview) {
  const wrap = observeElements.cards;
  wrap.textContent = "";
  const net = (overview.networks || [])[0] || {};
  const counters = overview.link_counters || {};
  const sackRate = counters.sack_fail_rate;
  wrap.append(
    observeCard("网络 NID", net.nid || "—"),
    observeCard("CCO MAC", net.cco_mac || "—"),
    observeCard("实测信标周期", net.beacon_period_ms ? `${net.beacon_period_ms} ms` : "—"),
    observeCard("站点数（去 CCO）", net.station_count != null ? String(net.station_count) : "—"),
    observeCard("组网事件数", String(overview.event_total ?? 0)),
    observeCard("SACK 失败率", sackRate != null ? `${(sackRate * 100).toFixed(2)}%` : "—",
      sackRate != null && sackRate > 0.05),
    observeCard("ICV 校验失败", String(counters.icv_fail ?? 0), (counters.icv_fail || 0) > 0),
    observeCard("截断长帧", String(counters.truncated ?? 0), (counters.truncated || 0) > 0),
  );
}

function renderObserveBeacon(beaconEvent) {
  const wrap = observeElements.beacon;
  wrap.textContent = "";
  if (!beaconEvent || !beaconEvent.fields || !beaconEvent.fields.periods_ms) {
    return;
  }
  const fields = beaconEvent.fields;
  const period = fields.beacon_period_ms || 15000;
  const title = document.createElement("div");
  title.className = "observe-beacon-title";
  title.textContent = `最新中央信标 · 周期#${fields.cycle_count || "—"} · ${beaconEvent.log_time} · 时隙重建（总长 ${period} ms）`;
  const bar = document.createElement("div");
  bar.className = "observe-period-bar";
  const segments = [
    ["seg-beacon", "信标", fields.periods_ms.beacon],
    ["seg-tdma", "TDMA", fields.periods_ms.tdma],
    ["seg-csma", "CSMA", fields.periods_ms.csma],
    ["seg-bind", "绑定CSMA", fields.periods_ms.bind_csma],
  ];
  segments.forEach(([cls, label, range]) => {
    if (!range) return;
    const width = Math.max(((range.end - range.start) / period) * 100, 1.5);
    const seg = document.createElement("div");
    seg.className = `observe-period-seg ${cls}`;
    seg.style.width = `${width}%`;
    seg.title = `${label} ${range.start}~${range.end} ms`;
    seg.textContent = width > 8 ? `${label} ${range.end - range.start}ms` : label;
    bar.appendChild(seg);
  });
  wrap.append(title, bar);
  const chips = document.createElement("div");
  chips.className = "observe-phase-chips";
  const slot = fields.csma_slots || [];
  if (slot.length) {
    const phases = { 0: "未知相", 1: "A相", 2: "B相", 3: "C相" };
    slot.forEach((item) => {
      const chip = document.createElement("span");
      chip.className = "observe-phase-chip";
      chip.textContent = `CSMA ${phases[item.phase] || item.phase} · ${item.length_ms} ms`;
      chips.appendChild(chip);
    });
    const noCentral = fields.no_central_slots;
    if (noCentral != null) {
      const chip = document.createElement("span");
      chip.className = "observe-phase-chip";
      chip.textContent = `非中央信标时隙 ${noCentral} + 中央 3`;
      chips.appendChild(chip);
    }
  }
  if (chips.childElementCount) wrap.appendChild(chips);
}

function renderObserveRows(events) {
  const body = observeElements.rows;
  body.textContent = "";
  if (!events.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "observe-empty";
    cell.textContent = "当前过滤条件下没有组网事件";
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  events.forEach((event) => {
    const row = document.createElement("tr");
    const cells = [event.log_time, observeEventNames[event.event] || event.event,
      OBSERVE_DIR_TEXT[event.direction] || event.direction,
      event.src_tei || "—", event.dst_tei || "—", event.summary];
    cells.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === 2) {
        cell.classList.add(`observe-dir-${event.direction || "mesh"}`);
      }
      if (index === 5) cell.title = value || "";
      cell.textContent = value == null ? "—" : String(value);
      row.appendChild(cell);
    });
    row.addEventListener("click", () => {
      [...body.children].forEach((item) => item.classList.remove("observe-row-active"));
      row.classList.add("observe-row-active");
      showObserveDetail(event);
    });
    body.appendChild(row);
  });
}

function showObserveDetail(event) {
  const panel = observeElements.detail;
  panel.hidden = false;
  panel.textContent = "";
  const title = document.createElement("div");
  title.className = "observe-detail-title";
  title.textContent = `${event.name} · 帧 #${event.frame_id} · ${event.log_time} · NID ${event.nid}`;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(event.fields, null, 2) || "{}";
  panel.append(title, pre);
}

if (observeElements.refresh) observeElements.refresh.addEventListener("click", loadNetworkObserve);
[observeElements.group, observeElements.event, observeElements.direction].forEach((el) => {
  if (el) el.addEventListener("change", loadNetworkObserve);
});
if (observeElements.query) {
  let observeQueryTimer = null;
  observeElements.query.addEventListener("input", () => {
    clearTimeout(observeQueryTimer);
    observeQueryTimer = setTimeout(loadNetworkObserve, 400);
  });
}
'''


def patch_html(path: str) -> None:
    with io.open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if 'data-view="network-observe"' in text:
        print("skip html (already patched):", path)
        return
    anchor = re.search(r'(<button class="view-tab"[^>]*data-view="network-assessment".*?</button>\n)', text, re.S)
    assert anchor, "network-assessment tab not found in " + path
    text = text[: anchor.end()] + TAB_HTML + text[anchor.end():]
    # 在 network-assessment-view section 之后插入新 section（深度匹配）
    start = text.index('<section id="network-assessment-view"')
    depth = 0
    end = None
    for match in re.finditer(r"<section\b|</section>", text[start:]):
        depth += 1 if match.group(0).startswith("<section") else -1
        if depth == 0:
            end = start + match.end()
            break
    assert end, "section end not found"
    text = text[:end] + "\n" + SECTION_HTML + text[end:]
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print("html patched:", path)


def patch_js(path: str, prefix: str) -> None:
    with io.open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if "network-observe-view" in text:
        print("skip js (already patched):", path)
        return
    old = '  if (networkElements) networkElements.view.hidden = name !== "network-assessment";'
    new = old + '\n  if (observeElements && observeElements.view) observeElements.view.hidden = name !== "network-observe";\n  if (name === "network-observe") loadNetworkObserve();'
    assert old in text, "switchView anchor missing in " + path
    text = text.replace(old, new)
    text = text + JS.replace("{P}", prefix)
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print("js patched:", path)


def patch_css(path: str) -> None:
    with io.open(path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    if "observe-cards" in text:
        print("skip css (already patched):", path)
        return
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text + CSS.replace("\n", "\n"))
    print("css patched:", path)


for base in (WORKBENCH, STANDALONE):
    patch_html(base + r"\index.html")
    patch_js(base + r"\app.js", "listener" if base == WORKBENCH else "")
    patch_css(base + r"\styles.css")
