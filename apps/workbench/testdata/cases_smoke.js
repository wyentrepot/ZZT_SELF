/* apps/workbench/static/pages/cases/cases.js 的无浏览器冒烟（REQS-0042 UI-1..7）。
 *
 * 由 apps/workbench/test_cases_page.py::test_page_logic_smoke 调用：
 *     node apps/workbench/testdata/cases_smoke.js <cases.js 绝对路径>
 *
 * 自带最小 DOM / fetch / localStorage 桩，不引第三方库。跑 6 条路径：
 *   ① 后端全 404（优雅降级，不白屏）   ② 正常路径（分组/排序/方案恢复/运行/轮询/断点）
 *   ③ 台架未就绪（运行置灰 + 逐项原因） ④ 刷新接回上一轮 job
 *   ⑤ 报告卡：summary 对象 / 平铺字段 → 直接渲染；都不给 → 降级拼人话（默认区不得出现块 id）
 *   ⑥ 报告卡：全通过 → 顶部一句话「本轮 N 例全部通过」
 * 全绿打印 SMOKE PASS 并以 0 退出；任一断言失败打印 FAIL 与堆栈、退出码非 0。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.resolve(
  process.argv[2] || "apps/workbench/static/pages/cases/cases.js"
);

function makeEl(id) {
  const classes = new Set();
  const el = {
    id,
    textContent: "",
    innerHTML: "",
    className: "",
    value: "",
    checked: false,
    disabled: false,
    title: "",
    hidden: false,
    dataset: {},
    style: {},
    classList: {
      add(cls) { classes.add(cls); el.className = [...classes].join(" "); },
      remove(cls) { classes.delete(cls); el.className = [...classes].join(" "); },
      contains(cls) { return classes.has(cls); }
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    onclick: null,
    oninput: null,
    onchange: null,
  };
  return el;
}

function makeSandbox(routes, store) {
  const els = new Map();
  const getEl = (id) => {
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  };
  const hooks = new Map();
  const getHook = (sel) => {
    if (!hooks.has(sel)) hooks.set(sel, makeEl(sel));
    return hooks.get(sel);
  };
  const document = {
    documentElement: { dataset: {}, style: {} },
    getElementById: getEl,
    querySelector: (sel) => getHook(sel),
    querySelectorAll: () => [],
  };
  const calls = [];
  const sandbox = {
    console,
    document,
    Array, Object, JSON, Math, String, Number, Promise, Error, RegExp, Date, isFinite,
    encodeURIComponent,
    setTimeout: (fn) => { sandbox.__timers.push(fn); return 1; },
    clearTimeout: () => {},
    // 30s bench 轮询（横幅慢修 2026-11-25）：冒烟不真实触发（间隔远大于一次 flush）
    setInterval: () => 0,
    clearInterval: () => {},
    __timers: [],
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    window: {
      location: { origin: "http://127.0.0.1:8790" },
      addEventListener() {},
      confirm() { return true; },
    },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    fetch: (url, opts) => {
      const method = (opts && opts.method) || "GET";
      calls.push(method + " " + url);
      const key = method + " " + String(url);
      const route = routes[key] || routes[String(url)] || routes["*"];
      const result = typeof route === "function" ? route(opts) : route;
      if (!result) return Promise.resolve(response(404, { detail: "Not Found" }));
      return Promise.resolve(response(result.status || 200, result.body));
    },
    __els: els,
    __hooks: hooks,
    __hook: getHook,
    __calls: calls,
  };
  sandbox.globalThis = sandbox;
  return sandbox;
}

function response(status, body) {
  const text = body === undefined ? "" : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(text),
  };
}

function flush(sandbox, rounds = 12) {
  let chain = Promise.resolve();
  for (let i = 0; i < rounds; i += 1) {
    chain = chain.then(() => new Promise((resolve) => setImmediate(resolve)));
  }
  return chain.then(() => {
    // 跑掉排队的轮询定时器（模拟一次 poll）
    const timers = sandbox.__timers.splice(0);
    timers.forEach((fn) => fn());
    return new Promise((resolve) => setImmediate(resolve));
  });
}

const CASES = {
  items: [
    {
      id: "meter_daily", title: "抄表日常（不复位）", step_count: 4,
      // 一句话描述（144 极简口径）：后端透传声明 description 时，卡面说明行渲染它
      description: "抄表日常回归用例：不复位 STA，档案在位后经查档、组网、绑表完成日常抄读",
      needs: ["bench.sta_online"], tags: ["smoke"],
      category: { usage: "common", domain: "STA", scene: "抄表", region: "国网通用" },
      protocols: ["645"],
      steps: [
        { no: 1, do: "添加从节点档案（11H-F1 建档：表地址与通信协议取块声明默认值）", expect: "档案存在" },
        { no: 2, do: "查询网络拓扑信息判定入网（10H-F21，入网从节点达标）", expect: "节点数达标" },
        { no: 3, do: "绑表（645 DI 读表地址）", expect: "拿到应答" },
        { no: 4, do: "抄表（645 读电能）", expect: "读到电能" },
      ],
    },
    {
      // 无 description：卡面退为「用例名 + 展开箭头」（描述行不渲染）
      id: "nwk_join", title: "组网入网", step_count: 2, category: { usage: "normal", domain: "CCO", scene: "组网" },
      protocols: ["1376.2"], steps: [{ no: 1, do: "复位 STA", expect: "复位成功" }],
    },
    { id: "legacy_case", title: "无分类老用例", steps: [{ no: 1, do: "老动作", expect: "老期望" }] },
  ],
  total: 3,
};
const SUITES = {
  suites: [{ id: "smoke_meter", name: "抄表冒烟", case_ids: ["meter_daily", "nwk_join"], stop_on_fail: true }],
};
const BENCH_READY = {
  ready: true,
  ports: [
    { id: "simcon", usable: true, errors: [] }, { id: "simeter", usable: true, errors: [] },
    { id: "sta-main", usable: true, errors: [] }, { id: "cco-main", usable: true, errors: [] },
    { id: "listener", usable: true, errors: [] },
  ],
  facts: {
    "bench.sta_online": { state: "yes", detail: "口可用" },
    "bench.simcon_online": { state: "yes", detail: "" },
    "archive.contains": { state: "yes", detail: "" },
  },
};
const BENCH_BLOCKED = {
  ready: false,
  ports: [
    { id: "simcon", usable: false, errors: ["port busy: COM4"] },
    { id: "simeter", usable: true, errors: [] },
    { id: "sta-main", usable: true, errors: [] },
    { id: "cco-main", usable: true, errors: [] },
    { id: "listener", usable: true, errors: [] },
  ],
  facts: {
    "bench.sta_online": { state: "yes", detail: "" },
    "bench.simcon_online": {
      state: "unknown",
      detail: "台架正被一次运行占用（``WORKBENCH_BASE`` 注入中），快照不探测事实：改这个变量会污染那一轮的台架地址，"
        + "宁可不停也不给假结论（injected_base_url=http://127.0.0.1:18790）",
    },
    "nwk.nodes_online_min": { state: "unknown", detail: "学习态，未到抄表态" },
  },
};
const JOB_DONE = {
  job_id: "job-1", run_id: "run-1", state: "done",
  progress: { total: 2, done: 2, percent: 100, current_case_id: "nwk_join", current_case_title: "组网入网" },
  case_counts: { pass: 1, fail: 0, blocked: 1, inconclusive: 0 },
  result_path: "data/case_runs/run-1/result.json",
  results: [
    {
      case_id: "meter_daily", title: "抄表日常（不复位）", verdict: "pass",
      blocks: [
        { block_id: "archive_add", state: "pass", outcome: "pass" },
        { block_id: "meter_read_645", state: "pass", outcome: "pass" },
      ],
      self_healed: [{ action: "close+retry", target: "simcon.session", result: "ok", detail: "端口被占用" }],
    },
    {
      case_id: "nwk_join", title: "组网入网", verdict: "blocked",
      failed_block: "", blocked_block: "nwk_topology",
      unmet_facts: { "nwk.nodes_online_min": { state: "no", detail: "0/120" } },
      skipped_reason: "台架不具备条件：CCO 未进入抄表态",
      blocks: [
        { block_id: "reset_sta", state: "pass", outcome: "pass" },
        { block_id: "nwk_topology", state: "skipped", outcome: "blocked", detail: "台架不具备条件" },
      ],
    },
  ],
};

/* D11-1/D11-2/D11-3 报告卡：① 后端给了 summary（直接渲染）；② 没给 summary（降级拼人话）。
 * 块名故意用 D11-2 点名的内部 id（archive.add / nwk.topology / meter.read_645）——
 * 默认展示区一个都不许出现，只允许出现在「开发者详情」折叠里。 */
const JOB_REPORT = {
  job_id: "job-report", run_id: "run-report", state: "done",
  progress: { total: 3, done: 3, percent: 100 },
  case_counts: { pass: 0, fail: 1, blocked: 1, inconclusive: 1 },
  run_dir: "data/case_runs/run-report",
  result_path: "data/case_runs/run-report/result.json",
  results: [
    {
      case_id: "meter_daily_regression", title: "抄表日常回归", verdict: "fail",
      summary: {
        headline: "未通过：组网超时",
        reasons: [
          "组网超时",
          "档案下发异常：目标节点 3 台，实际入网 1 台，等待 305.0 秒后仍不足（只读探测 44 次，等待期未下发写帧）",
        ],
        evidence_paths: [
          { path: "data/case_runs/run-report", kind: "run_dir" },
          { path: "data/case_runs/run-report/result.json", kind: "result" },
          "data/case_runs/run-report/cli.log",
        ],
      },
      failed_block: "nwk.topology",
      blocks: [
        { block_id: "archive.add", state: "fail", outcome: "fail", detail: "档案下发异常" },
        { block_id: "nwk.topology", state: "fail", outcome: "fail", detail: "组网超时" },
        { block_id: "meter.read_645", state: "skipped", outcome: "blocked" },
      ],
    },
    {
      // 平铺字段（不带 summary 对象）同样要能直接渲染
      case_id: "meter_read_retry", title: "抄表重试", verdict: "inconclusive",
      headline: "说不清：抄读超时未应答",
      reasons: ["抄读超时未应答"],
      evidence_paths: ["data/case_runs/run-report/flat.log"],
      blocks: [{ block_id: "meter.read_645", state: "unknown", outcome: "inconclusive" }],
    },
    {
      // 无 summary：降级路径——原因行只保留一句人话，完整 detail 只进开发者详情
      case_id: "nwk_join", title: "组网入网", verdict: "blocked",
      blocked_block: "nwk.topology",
      unmet_facts: { "nwk.nodes_online_min": { state: "no", detail: "CCO 未进入抄表态" } },
      blocks: [
        { block_id: "reset.sta", state: "pass", outcome: "pass" },
        {
          block_id: "nwk.topology", state: "blocked", outcome: "blocked",
          detail: "组网：明确不足 —— 入网从节点=1 < 门槛 3（档案数=3 × 0.98 向上取整；"
            + "仅从节点口径 = 节点总数量 2 − 主节点 1）（等了 305.0 秒，只读探测 44 次）"
            + "（等待期只发只读查询，未下发任何写帧）",
        },
      ],
    },
  ],
};

const JOB_ALL_PASS = {
  job_id: "job-pass", run_id: "run-pass", state: "done",
  progress: { total: 2, done: 2, percent: 100 },
  case_counts: { pass: 2, fail: 0, blocked: 0, inconclusive: 0 },
  result_path: "data/case_runs/run-pass/result.json",
  results: [
    { case_id: "meter_daily", title: "抄表日常（不复位）", verdict: "pass" },
    { case_id: "nwk_join", title: "组网入网", verdict: "pass" },
  ],
};

// 报告卡切分：每张卡从 <article data-hook="case-report"> 开始
function cardsOf(html) {
  return html.split('<article class="case-result report').slice(1)
    .map((part) => '<article class="case-result report' + part);
}

// 默认展示区 = 卡片里「开发者详情」折叠之前的所有内容
function defaultAreaOf(card) {
  const cut = card.indexOf('data-hook="dev-details"');
  return cut >= 0 ? card.slice(0, cut) : card;
}

function devAreaOf(card) {
  const cut = card.indexOf('data-hook="dev-details"');
  return cut >= 0 ? card.slice(cut) : "";
}

const BLOCK_IDS = [
  "archive.add", "nwk.topology", "meter.read_645", "archive.contains",
  "bench.simcon_online", "bench.sta_online", "nwk.nodes_online_min",
];

// 用户能看见的文本 = 去标签（连属性一起去掉）+ 反转义
function visibleText(html) {
  return String(html)
    .replace(/<[^>]*>/g, " ")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

// 列表行的行内摘要（.case-sum）——展开区不在其中
function caseSummaries(html) {
  const out = [];
  const re = /<div class="case-sum"[^>]*>([\s\S]*?)<\/div>/g;
  let match;
  while ((match = re.exec(html))) out.push(visibleText(match[1]));
  return out;
}

function assertNoBlockIds(area, where) {
  const text = visibleText(area);
  BLOCK_IDS.forEach((id) => {
    if (text.includes(id)) throw new Error(where + " 泄漏了内部块名 " + id + "：" + text.slice(0, 240));
  });
}

// 内部词/地址/十六进制不得出现在默认展示区（失败信息/台账口径而不是甩给用户）
function assertNoInternalWords(area, where) {
  const text = visibleText(area);
  ["WORKBENCH_BASE", "injected_base_url", "http", "0x"].forEach((token) => {
    if (text.toLowerCase().includes(token.toLowerCase())) {
      throw new Error(where + " 泄漏内部词 " + token + "：" + text.slice(0, 240));
    }
  });
}


function run(name, routes, store, check) {
  const sandbox = makeSandbox(routes, store || {});
  vm.createContext(sandbox);
  const code = fs.readFileSync(SRC, "utf8");
  vm.runInContext(code, sandbox, { filename: SRC });
  return flush(sandbox).then(() => check(sandbox)).then(() => {
    console.log("OK  " + name);
  }).catch((error) => {
    console.error("FAIL " + name + "\n" + (error && error.stack ? error.stack : error));
    process.exitCode = 1;
  });
}

(async () => {
  // ① 后端未就绪：全部 404 —— 必须降级而不是抛异常
  await run("后端 404 降级", {}, {}, (sandbox) => {
    const el = sandbox.__els;
    const list = el.get("list-wrap").innerHTML;
    if (!list.includes("后端未就绪")) throw new Error("未渲染降级文案：" + list.slice(0, 120));
    if (!el.get("api-pill-text").textContent.includes("后端未就绪")) throw new Error("顶栏未反映后端未就绪");
    if (!el.get("bench-reasons").innerHTML.includes("后台") && !el.get("bench-reasons").innerHTML.includes("尚未提供")) {
      throw new Error("台架原因未列出：" + el.get("bench-reasons").innerHTML);
    }
    if (!el.get("btn-run-now").disabled || !el.get("btn-run-suite").disabled) {
      throw new Error("后端未就绪时运行按钮应置灰");
    }
  });

  // ② 后端正常 + 台架就绪：分组 / 筛选 / 勾选 / 存方案 / 运行 / 轮询 / 断点
  await run("正常路径（含断点渲染）", {
    "GET /api/cases": { body: CASES },
    "GET /api/cases/suites": { body: SUITES },
    "GET /api/cases/bench": { body: BENCH_READY },
    "POST /api/cases/run": { body: { job_id: "job-1", run_id: "run-1", state: "queued" } },
    "GET /api/cases/jobs/job-1": { body: JOB_DONE },
    "POST /api/cases/suites": { body: { id: "smoke_meter", name: "抄表冒烟" } },
  }, { "wb-cases-state": JSON.stringify({ suite_id: "smoke_meter", selected: [] }) }, (sandbox) => {
    const el = sandbox.__els;
    const list = el.get("list-wrap").innerHTML;
    if (!list.includes("meter_daily")) throw new Error("用例未渲染");
    // 144 极简卡面（2026-11-25 用户裁决）：用例名 + 一句话描述 + 展开箭头；
    // 步数芯片 / 业务动作链摘要 / 分类标签组不再上卡面（反向守卫）
    if (list.includes("步</span>") || list.includes("case-cat") || list.includes('class="tag"')) {
      throw new Error("卡面仍渲染步数/摘要/分类标签组（应已按 144 口径移除）：" + visibleText(list).slice(0, 300));
    }
    // 卡面说明行 = 声明 description 一句话（仅 meter_daily 有 → 恰 1 条）；无 description 不渲染
    const summaries = caseSummaries(list);
    if (summaries.length !== 1 || summaries[0].indexOf("抄表日常回归用例") !== 0) {
      throw new Error("卡面一句话描述未按声明 description 渲染：" + JSON.stringify(summaries));
    }
    if (/11H-F\d|10H-F\d|645\s*DI/.test(summaries.join(" "))) {
      throw new Error("卡面描述仍含帧标识：" + summaries.join(" / "));
    }
    if (!list.includes(">▸<")) throw new Error("缺少展开箭头");
    if (!list.includes("未分类")) throw new Error("category 缺失用例未归「未分类」");
    if (!list.includes('data-group=""') && !list.includes("未分类")) throw new Error("分组渲染缺失");
    // 常用度排序：common 组必须排在 normal / 未分类之前
    const iCommon = list.indexOf("抄表日常");
    const iNormal = list.indexOf("组网入网");
    const iLegacy = list.indexOf("无分类老用例");
    if (!(iCommon < iNormal && iNormal < iLegacy)) {
      throw new Error("默认排序不对：common=" + iCommon + " normal=" + iNormal + " legacy=" + iLegacy);
    }
    if (process.env.SMOKE_DEBUG) console.log("LIST " + caseSummaries(list).join(" || "));
    // 方案恢复：勾选应按 suite 恢复
    if (!el.get("sel-count").textContent.includes("2")) throw new Error("方案勾选未恢复：" + el.get("sel-count").textContent);
    if (el.get("btn-run-suite").disabled) throw new Error("台架就绪 + 有方案时「按方案运行」应可用");
    const portsHtml = sandbox.__hook('[data-hook="bench-ports"]').innerHTML;
    if (portsHtml.includes("off")) throw new Error("台架就绪却渲染了不可用端口");
    const factsHtml = sandbox.__hook('[data-hook="bench-facts"]').innerHTML;
    if (!factsHtml.includes("成立")) throw new Error("事实三态未渲染：" + factsHtml);
    assertNoBlockIds(factsHtml, "台架事实条");
    assertNoInternalWords(factsHtml, "台架事实条");

    // 触发「按方案运行」
    el.get("btn-run-suite").onclick();
    return flush(sandbox, 20).then(() => {
      const results = el.get("job-results").innerHTML;
      if (!results.includes("data-verdict=\"blocked\"")) throw new Error("blocked 结论未渲染：" + results.slice(0, 200));
      if (!results.includes("data-hook=\"breakpoint\"")) throw new Error("断点未渲染");
      if (!results.includes("nwk_topology")) throw new Error("阻断块未点名");
      if (!results.includes("nwk.nodes_online_min")) throw new Error("未满足事实未列出");
      if (!results.includes("自愈")) throw new Error("self_healed 未渲染");
      if (!results.includes("is-blocked") && !results.includes("is-pass")) throw new Error("四态类未落到结果卡片");
      if (!el.get("job-log").textContent.includes("result.json")) throw new Error("证据路径未显示");
    });
  });

  // ③ 台架未就绪：运行按钮置灰 + 逐项原因
  await run("台架未就绪置灰", {
    "GET /api/cases": { body: CASES },
    "GET /api/cases/suites": { body: SUITES },
    "GET /api/cases/bench": { body: BENCH_BLOCKED },
  }, {}, (sandbox) => {
    const el = sandbox.__els;
    const reasons = el.get("bench-reasons").innerHTML;
    if (!reasons.includes("simcon")) throw new Error("端口原因未列出：" + reasons);
    if (!reasons.includes("port busy")) throw new Error("端口错误明细未列出");
    // 事实原因用业务说法（组网节点数），不显示事实 id
    if (!reasons.includes("组网节点数")) throw new Error("事实原因未翻译成人话：" + reasons);
    // 台架被占用：内部口径一律映射成一句人话
    if (!reasons.includes("台架正忙（上一轮运行未结束）")) throw new Error("台架占用原因未人话化：" + reasons);
    if (process.env.SMOKE_DEBUG) {
      console.log("BENCH " + visibleText(sandbox.__hook('[data-hook="bench-ports"]').innerHTML)
        + " | " + visibleText(sandbox.__hook('[data-hook="bench-facts"]').innerHTML)
        + " | " + visibleText(reasons));
    }
    assertNoBlockIds(el.get("bench-reasons").innerHTML, "台架原因行");
    assertNoInternalWords(el.get("bench-reasons").innerHTML, "台架原因行");
    // 条数少、每条短：横幅不是技术日志
    const lines = el.get("bench-reasons").innerHTML.split("</li>").filter((x) => x.includes("<li"));
    lines.forEach((line) => {
      const text = visibleText(line);
      if (text.length > 44) throw new Error("台架原因行过长（" + text.length + " 字）：" + text);
    });
    // 内部细节只允许出现在开发者详情（默认收起）
    const raw = el.get("bench-raw").textContent;
    if (!raw.includes("WORKBENCH_BASE")) throw new Error("开发者详情未保留原始原因：" + raw);
    if (!raw.includes("bench.simcon_online")) throw new Error("开发者详情未保留事实 id：" + raw);
    if (el.get("bench-dev-head").hidden !== false) throw new Error("有原始细节时开发者详情入口应可见");
    if (!el.get("btn-run-suite").disabled) throw new Error("台架未就绪时「按方案运行」应置灰");
    if (!el.get("btn-run-now").disabled) throw new Error("台架未就绪时「立即运行」应置灰");
    if (!el.get("btn-run-suite").title.includes("台架未就绪")) throw new Error("置灰原因未挂到 title");
    if (!el.get("bench-state").textContent.includes("未就绪")) throw new Error("横幅状态文案不对");
  });

  // ④ 刷新恢复：localStorage 里存着上一轮 job_id —— 自动接回轮询并恢复结论
  await run("刷新接回上一轮 job", {
    "GET /api/cases": { body: CASES },
    "GET /api/cases/suites": { body: SUITES },
    "GET /api/cases/bench": { body: BENCH_READY },
    "GET /api/cases/jobs/job-1": { body: JOB_DONE },
  }, { "wb-cases-state": JSON.stringify({ suite_id: "smoke_meter", last_job_id: "job-1", selected: [] }) }, (sandbox) => {
    const el = sandbox.__els;
    if (!el.get("job-panel").className.includes("show")) throw new Error("刷新后 job 面板未恢复");
    const results = el.get("job-results").innerHTML;
    if (!results.includes("nwk_topology")) throw new Error("刷新后未恢复断点信息");
    if (!el.get("job-log").textContent.includes("result.json")) throw new Error("刷新后未恢复证据路径");
    if (!el.get("sel-count").textContent.includes("2")) throw new Error("刷新后方案勾选未恢复");
  });

  await run("报告卡（summary 与降级）", {
    "GET /api/cases": { body: CASES },
    "GET /api/cases/suites": { body: SUITES },
    "GET /api/cases/bench": { body: BENCH_READY },
    "GET /api/cases/jobs/job-report": { body: JOB_REPORT },
  }, { "wb-cases-state": JSON.stringify({ suite_id: "smoke_meter", last_job_id: "job-report", selected: [] }) }, (sandbox) => {
    const el = sandbox.__els;
    const html = el.get("job-results").innerHTML;
    if (process.env.SMOKE_DEBUG) console.log(html);
    const cards = cardsOf(html);
    if (cards.length !== 3) throw new Error("应渲染 3 张报告卡，实际 " + cards.length + "：" + html.slice(0, 240));
    if (!html.includes('data-hook="case-report"')) throw new Error("缺少 data-hook=case-report");

    // ① 后端给了 summary：标题 = 用例名（headline），多原因落在原因行
    const given = cards[0];
    if (!given.includes("抄表日常回归（未通过：组网超时）")) {
      throw new Error("summary 标题未按报告式渲染：" + defaultAreaOf(given).slice(0, 240));
    }
    if (!/data-hook="report-headline"/.test(given)) throw new Error("缺少 report-headline");
    const givenReasons = given.match(/data-hook="report-reasons"[^>]*>([^<]*)</);
    if (!givenReasons) throw new Error("原因行未渲染：" + given.slice(0, 240));
    const givenParts = givenReasons[1].split("；");
    if (givenParts[0] !== "组网超时" || !givenParts[1].startsWith("档案下发异常")) {
      throw new Error("summary 原因未按序渲染：" + givenReasons[1]);
    }
    givenParts.forEach((one) => {
      if (one.length > 41) throw new Error("summary 原因超过 40 字（" + one.length + "）：" + one);
    });
    if (!givenParts[1].endsWith("…")) throw new Error("超长原因未加省略号：" + givenParts[1]);
    const evidence = given.slice(given.indexOf('data-hook="report-evidence"'), given.indexOf('data-hook="dev-details"'));
    if (!evidence.includes("data/case_runs/run-report/result.json")) throw new Error("证据路径未渲染");
    if (!evidence.includes("运行目录")) throw new Error("证据类型标签未渲染");
    if (!evidence.includes("rc-copy")) throw new Error("证据路径缺少复制按钮");
    assertNoBlockIds(defaultAreaOf(given), "有 summary 的默认展示区");
    assertNoInternalWords(defaultAreaOf(given), "有 summary 的默认展示区");

    // 开发者详情：默认收起，块名只允许出现在折叠内
    if (!/data-hook="dev-details" hidden/.test(given)) throw new Error("开发者详情应默认 hidden");
    const dev = devAreaOf(given);
    if (!dev.includes("nwk.topology") || !dev.includes("archive.add")) {
      throw new Error("开发者详情里应保留块名与逐步明细：" + dev.slice(0, 240));
    }
    if (!dev.includes('data-hook="breakpoint"')) throw new Error("开发者详情里应保留断点信息");

    // ② 平铺字段（不带 summary 对象）：headline / reasons / evidence_paths 一样直接渲染
    const flat = cards[1];
    if (!flat.includes("抄表重试（说不清：抄读超时未应答）")) {
      throw new Error("平铺字段未渲染：" + defaultAreaOf(flat).slice(0, 240));
    }
    if (!defaultAreaOf(flat).includes("data/case_runs/run-report/flat.log")) {
      throw new Error("平铺 evidence_paths 未渲染：" + defaultAreaOf(flat));
    }
    assertNoBlockIds(defaultAreaOf(flat), "平铺字段的默认展示区");
    assertNoInternalWords(defaultAreaOf(flat), "平铺字段的默认展示区");

    // ③ 没给 summary：降级拼人话，且原因行只留一句（不贴块层 detail 全文）
    const derived = cards[2];
    const derivedDefault = defaultAreaOf(derived);
    if (!derivedDefault.includes("组网入网（台架不具备：组网未完成")) {
      throw new Error("降级卡片标题未收敛成一句人话：" + visibleText(derivedDefault).slice(0, 240));
    }
    const reasonLine = derivedDefault.match(/data-hook="report-reasons"[^>]*>([^<]*)</);
    if (!reasonLine) throw new Error("降级卡片缺少原因行");
    const reasonsText = reasonLine[1];
    if (!reasonsText.includes("组网未完成")) throw new Error("原因行未收敛成主体短语：" + reasonsText);
    if (reasonsText.includes("——") || reasonsText.includes("门槛") || reasonsText.includes("305.0")) {
      throw new Error("原因行仍在贴块层原始 detail：" + reasonsText);
    }
    reasonsText.split("；").forEach((one) => {
      if (one.length > 40) throw new Error("单条原因超过 40 字（" + one.length + "）：" + one);
    });
    if (reasonsText.split("；").length > 3) throw new Error("原因行最多 3 条：" + reasonsText);
    if (!derivedDefault.includes("CCO 未进入抄表态")) throw new Error("降级未使用 unmet_facts 的 detail：" + derivedDefault);
    if (!derivedDefault.includes("证据")) throw new Error("降级卡片缺少证据行：" + derivedDefault);
    assertNoBlockIds(derivedDefault, "无 summary 的降级展示区");
    assertNoInternalWords(derivedDefault, "无 summary 的降级展示区");
    // 完整 detail 只能在开发者详情里
    const derivedDev = devAreaOf(derived);
    if (!derivedDev.includes("nwk.topology")) throw new Error("降级卡片的开发者详情应保留块名");
    if (!derivedDev.includes("305.0")) throw new Error("完整 detail 未收进开发者详情：" + derivedDev.slice(0, 240));

    // ④ 顶部汇总一句话
    const summary = el.get("job-summary").textContent;
    if (!summary.startsWith("本轮 ")) throw new Error("顶部汇总不是一句话：" + summary);
    if (!summary.includes("1 例未通过（组网超时）")) throw new Error("顶部汇总未带原因：" + summary);
    if (summary.length > 60) throw new Error("顶部汇总过长（" + summary.length + " 字）：" + summary);
    if (!summary.includes("1 例台架不具备") || !summary.includes("1 例说不清")) {
      throw new Error("顶部汇总未覆盖四态：" + summary);
    }
    if (summary.includes("FAIL") || summary.includes("BLOCKED")) throw new Error("顶部汇总仍在报英文态：" + summary);
  });

  // 报告卡：全通过时顶部一句话 + 通过卡片只显示「（PASS）」与证据
  await run("报告卡（全部通过）", {
    "GET /api/cases": { body: CASES },
    "GET /api/cases/suites": { body: SUITES },
    "GET /api/cases/bench": { body: BENCH_READY },
    "GET /api/cases/jobs/job-pass": { body: JOB_ALL_PASS },
  }, { "wb-cases-state": JSON.stringify({ suite_id: "smoke_meter", last_job_id: "job-pass", selected: [] }) }, (sandbox) => {
    const el = sandbox.__els;
    const html = el.get("job-results").innerHTML;
    const cards = cardsOf(html);
    if (cards.length !== 2) throw new Error("应渲染 2 张报告卡，实际 " + cards.length);
    if (!cards[0].includes("抄表日常（不复位）（PASS）")) {
      throw new Error("PASS 标题不对：" + defaultAreaOf(cards[0]).slice(0, 200));
    }
    if (cards[0].includes('data-hook="report-reasons"')) throw new Error("PASS 卡片不该有原因行");
    if (!defaultAreaOf(cards[0]).includes("data/case_runs/run-pass/result.json")) {
      throw new Error("未用 job 落盘路径兜底证据");
    }
    if (el.get("job-summary").textContent !== "本轮 2 例全部通过") {
      throw new Error("全通过汇总不对：" + el.get("job-summary").textContent);
    }
  });

  await new Promise((resolve) => setImmediate(resolve));
  console.log(process.exitCode ? "SMOKE FAILED" : "SMOKE PASS");
})();
