/* apps/workbench/static/pages/cases/cases.js 的无浏览器冒烟（REQS-0042 UI-1..7）。
 *
 * 由 apps/workbench/test_cases_page.py::test_page_logic_smoke 调用：
 *     node apps/workbench/testdata/cases_smoke.js <cases.js 绝对路径>
 *
 * 自带最小 DOM / fetch / localStorage 桩，不引第三方库。跑 4 条路径：
 *   ① 后端全 404（优雅降级，不白屏）   ② 正常路径（分组/排序/方案恢复/运行/轮询/断点）
 *   ③ 台架未就绪（运行置灰 + 逐项原因） ④ 刷新接回上一轮 job
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
      needs: ["bench.sta_online"], tags: ["smoke"],
      category: { usage: "common", domain: "STA", scene: "抄表", region: "国网通用" },
      protocols: ["645"],
      steps: [
        { no: 1, do: "下发档案", expect: "档案存在" },
        { no: 2, do: "查拓扑", expect: "节点数达标" },
        { no: 3, do: "绑表", expect: "拿到应答" },
        { no: 4, do: "抄表", expect: "读到电能" },
      ],
    },
    {
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
    if (!list.includes("4 步")) throw new Error("步骤数未渲染");
    if (!list.includes("下发档案")) throw new Error("步骤摘要未渲染");
    if (!list.includes("未分类")) throw new Error("category 缺失用例未归「未分类」");
    if (!list.includes('data-group=""') && !list.includes("未分类")) throw new Error("分组渲染缺失");
    // 常用度排序：common 组必须排在 normal / 未分类之前
    const iCommon = list.indexOf("抄表日常");
    const iNormal = list.indexOf("组网入网");
    const iLegacy = list.indexOf("无分类老用例");
    if (!(iCommon < iNormal && iNormal < iLegacy)) {
      throw new Error("默认排序不对：common=" + iCommon + " normal=" + iNormal + " legacy=" + iLegacy);
    }
    // 方案恢复：勾选应按 suite 恢复
    if (!el.get("sel-count").textContent.includes("2")) throw new Error("方案勾选未恢复：" + el.get("sel-count").textContent);
    if (el.get("btn-run-suite").disabled) throw new Error("台架就绪 + 有方案时「按方案运行」应可用");
    const portsHtml = sandbox.__hook('[data-hook="bench-ports"]').innerHTML;
    if (portsHtml.includes("off")) throw new Error("台架就绪却渲染了不可用端口");
    const factsHtml = sandbox.__hook('[data-hook="bench-facts"]').innerHTML;
    if (!factsHtml.includes("yes")) throw new Error("事实三态未渲染");

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
    if (!reasons.includes("nwk.nodes_online_min")) throw new Error("事实原因未列出");
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

  await new Promise((resolve) => setImmediate(resolve));
  console.log(process.exitCode ? "SMOKE FAILED" : "SMOKE PASS");
})();
