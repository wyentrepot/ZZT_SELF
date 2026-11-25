/* 用例库页面逻辑（REQS-0042 §3.7 UI-1..7 + REQS-0044 界面移植）。
 *
 * 界面基线：docs/ui/cases_v2_demo.html（清单式用例卡 + 台架就绪横幅 +
 * Excel 式结果表六列 + 汇总条 + 开发者详情折叠）。本页是该基线的**生产实现**：
 * 数据全部来自 /api/cases/*（apps/workbench/cases_api.py），零 mock 行。
 *
 * 数据源：/api/cases/*（apps/workbench/cases_api.py）。后端可能尚未挂载（404）——
 * 本页对所有端点做「字段缺失容忍 + 404 降级」：能读到什么就画什么，
 * 读不到就显式说「后端未就绪」，而不是白屏。
 *
 * 四态结论（UI-6）：pass 绿 / fail 红 / blocked 灰（台架问题，不是产品问题）/ inconclusive 黄。
 * 配色一律走 tokens-v2 的 var(--color-*)，页面零裸色值。
 *
 * 结果视图（REQS-0044 变更 3 报表契约）：GET /api/cases/runs/{run_id} 与 job 终态的
 * run.report.rows[]/summary 直接渲染；块级明细（含 observation 观测）只进「开发者详情」折叠。
 */
(function () {
  "use strict";

  var STORE_KEY = "wb-cases-state";   // 勾选 + 方案 + 最近结论 + 分组维度（刷新后恢复，UI-3）
  var POLL_MS = 1200;

  var $ = function (id) { return document.getElementById(id); };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function num(value) { return typeof value === "number" && isFinite(value) ? value : 0; }
  function arr(value) { return Array.isArray(value) ? value : []; }
  function obj(value) { return value && typeof value === "object" ? value : {}; }

  var toastTimer = null;
  function toast(message) {
    var el = $("toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2600);
  }

  // ---------- HTTP：统一错误体，保留 status 供 404 降级判定 ----------
  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (response) {
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (error) { data = null; }
        }
        if (!response.ok) {
          var detail = data && (data.detail || data.message);
          var message = typeof detail === "string" ? detail
            : (detail ? JSON.stringify(detail) : (method + " " + path + " → HTTP " + response.status));
          var failure = new Error(message);
          failure.status = response.status;
          throw failure;
        }
        return data;
      });
    });
  }

  // ---------- 分类维度（D10-2）：取值缺失一律归「未分类」，不隐藏用例 ----------
  var USAGE_LABEL = { common: "常用", normal: "常规", rare: "罕见" };
  var USAGE_ORDER = ["common", "normal", "rare"];
  var DOMAIN_ORDER = ["CCO", "STA", "ECU", "bench"];
  var SCENE_ORDER = ["组网", "抄表"];
  var REGION_ORDER = ["国网通用", "南网通用"];
  var ORDER = {
    usage: USAGE_ORDER,
    domain: DOMAIN_ORDER,
    scene: SCENE_ORDER,
    region: REGION_ORDER
  };
  var UNCAT = "__uncat__";   // 筛选下拉里「未分类」的哨兵值
  var UNCAT_LABEL = "未分类";

  function clean(value) {
    return typeof value === "string" && value.trim() ? value.trim() : "";
  }
  function rankOf(list, value) {
    var index = list.indexOf(value);
    return index < 0 ? 99 : index;
  }
  function catOf(item) {
    var category = obj(item.category);
    return {
      usage: clean(category.usage),
      domain: clean(category.domain),
      scene: clean(category.scene),
      region: clean(category.region)
    };
  }
  function dimLabel(dim, value) {
    if (!value) return UNCAT_LABEL;
    if (dim === "usage") return USAGE_LABEL[value] || value;
    return value;
  }

  // ---------- 归一化：兼容「契约字段」与「后端旧字段名」 ----------
  function normStep(raw, index) {
    if (typeof raw === "string") return { no: index + 1, do: raw, expect: "" };
    var step = obj(raw);
    return {
      no: num(step.no) || num(step.index) || index + 1,
      do: String(step.do || step.action || step.title || step.block || ""),
      expect: String(step.expect || step.expects || step.expected || "")
    };
  }
  function normCase(raw) {
    var item = obj(raw);
    var steps = arr(item.steps || item.plan).map(normStep);
    return {
      id: String(item.id || ""),
      title: String(item.title || item.name || item.id || ""),
      // 一句话描述（144 台账极简口径，2026-11-25）：来自声明 description 字段；
      // 后端未透传时为空串 → 卡片只显用例名（用户裁决：只用显示用例名即可）
      description: String(item.description || ""),
      steps: steps,
      step_count: num(item.step_count) || steps.length,
      block_count: num(item.block_count) || arr(item.blocks).length,
      blocks: arr(item.blocks).map(String),
      needs: arr(item.needs).map(String),
      tags: arr(item.tags).map(String),
      protocols: arr(item.protocols).map(String),
      category: obj(item.category),
      hasCategory: !!(item.category && typeof item.category === "object"),
      // Procedure 类别（REQS-0044 变更 3 L1 / 变更 4）：regression / scenario / setup
      kind: String(item.kind || "regression").toLowerCase()
    };
  }
  function normSuite(raw) {
    var item = obj(raw);
    return {
      id: String(item.id || ""),
      name: String(item.name || item.title || item.id || ""),
      case_ids: arr(item.case_ids).map(String),
      stop_on_fail: item.stop_on_fail !== false,
      notes: String(item.notes || "")
    };
  }
  function normPorts(bench) {
    var source = bench.ports;
    var list = [];
    if (Array.isArray(source)) {
      list = source.map(function (raw) {
        var port = obj(raw);
        return {
          id: String(port.id || port.port_id || port.mapping_id || port.name || ""),
          // enabled=false 的口（如 ecu-biz/ecu-mgmt）本就不参与台架，不能被算成「不可用」
          enabled: port.enabled !== false,
          usable: port.usable === true,
          errors: arr(port.errors).map(String),
          // 逻辑口对应的 Windows COM 号（横幅芯片展示「名 COM号」，同 Demo 口径）
          windows_com: String(port.windows_com || port.com || ""),
          label: String(port.label || "")
        };
      });
    } else {
      Object.keys(obj(source)).forEach(function (key) {
        var port = obj(source[key]);
        list.push({ id: key, usable: port.usable === true, errors: arr(port.errors).map(String),
          windows_com: String(port.windows_com || port.com || ""), label: String(port.label || "") });
      });
    }
    return list.filter(function (port) { return port.id; });
  }
  function normFacts(bench) {
    var source = bench.facts;
    var list = [];
    if (Array.isArray(source)) {
      list = source.map(function (raw) {
        var fact = obj(raw);
        return { id: String(fact.id || fact.fact_id || ""), state: normFactState(fact.state), detail: String(fact.detail || "") };
      });
    } else {
      Object.keys(obj(source)).forEach(function (key) {
        var fact = obj(source[key]);
        list.push({ id: key, state: normFactState(fact.state), detail: String(fact.detail || "") });
      });
    }
    return list.filter(function (fact) { return fact.id; });
  }
  function normFactState(value) {
    var text = String(value == null ? "" : value).toLowerCase();
    if (text === "yes" || text === "true" || text === "pass") return "yes";
    if (text === "no" || text === "false" || text === "fail") return "no";
    return "unknown";
  }

  // ---------- 四态结论（UI-6） ----------
  var VERDICT = {
    pass: { label: "PASS", name: "通过", cls: "verdict-pass" },
    fail: { label: "FAIL", name: "失败", cls: "verdict-fail" },
    blocked: { label: "BLOCKED", name: "阻断（台架）", cls: "verdict-blocked" },
    inconclusive: { label: "INCONCLUSIVE", name: "说不清", cls: "verdict-inconclusive" },
    "": { label: "未运行", name: "未运行", cls: "verdict-unknown" }
  };
  function normVerdict(value) {
    var text = String(value == null ? "" : value).toLowerCase();
    if (text === "pass" || text === "passed" || text === "ok") return "pass";
    if (text === "fail" || text === "failed") return "fail";
    if (text === "blocked" || text === "skip" || text === "skipped") return "blocked";
    if (text === "inconclusive" || text === "unknown" || text === "incomplete") return "inconclusive";
    return text ? "inconclusive" : "";
  }
  function badge(verdict) {
    var meta = VERDICT[normVerdict(verdict) || ""] || VERDICT[""];
    return '<span class="verdict ' + meta.cls + '" data-verdict="' + esc(normVerdict(verdict)) +
      '" title="' + esc(meta.name) + '"><span class="dot"></span>' + meta.label + "</span>";
  }
  function verdictText(verdict) {
    var meta = VERDICT[normVerdict(verdict) || ""] || VERDICT[""];
    return meta.label;
  }
  function stateLabel(state) {
    var text = String(state == null ? "" : state).toLowerCase();
    if (text === "queued") return "排队中…";
    if (text === "running") return "运行中…";
    if (text === "done") return "已结束";
    if (text === "failed") return "任务失败";
    if (text === "cancelled" || text === "canceled") return "已取消";
    return text || "未知状态";
  }

  // ---------- kind 徽标（REQS-0044 变更 4）：回归=默认色 / 场景=蓝 / 台架准备=灰 ----------
  // 工位 E：徽标说明（title 悬浮文案）删除——徽标文字本身就是全部信息。
  var KIND_META = {
    regression: { label: "回归", cls: "" },
    scenario: { label: "场景", cls: " scenario" },
    setup: { label: "台架准备", cls: " setup" }
  };
  function kindBadge(kind) {
    var key = String(kind || "").toLowerCase();
    var meta = KIND_META[key] || KIND_META.regression;
    return '<span class="kind-badge' + meta.cls + '" data-kind="' +
      esc(key || "regression") + '">' + meta.label + "</span>";
  }

  // ---------- 页面状态 ----------
  var S = {
    apiOk: false,
    apiError: "",
    cases: [],
    total: 0,
    problems: [],
    suites: [],
    suiteId: "",
    bench: null,
    benchMode: "checking",       // checking | ready | blocked | unavailable | unknown
    benchStale: false,           // 横幅慢修：stale/占位快照（后台检测中）→ 横幅显示「事实检测中…」徽标
    benchReasons: [],            // 默认区：每条一句人话
    benchRaw: [],                // 开发者详情：端口/事实 id、原始 errors/detail
    benchInfo: [],               // 信息性事实（不参与运行闸门，仅展示）
    benchPorts: [],
    benchFacts: [],
    // 横幅「详情」折叠（工位 E）：null = 跟随自动口径（不就绪才展开），true/false = 用户点过
    benchDetailsOpen: null,
    groupBy: "usage",
    sort: "default",
    filters: { q: "", usage: "", domain: "", scene: "", region: "", protocol: "" },
    sel: {},
    lastRun: {},
    resumeJobId: "",
    job: null,
    jobId: "",
    jobTimer: null,
    // job 面板（工位 E）：单行进度 / 终态收起一行（✔/✘ + run_id + verdict + 耗时）
    jobLabel: "",
    jobTerminal: false,
    jobExpanded: false,
    // 结果视图（REQS-0044）：当前展示的运行记录 + 最近一次结果的 run_id（刷新恢复用）
    currentRun: null,
    lastRunId: "",
    // 历史运行索引（GET /api/cases/runs?limit=10，REQS-0044 变更 4）：历史运行下拉的数据
    runsIndex: [],
    // 默认参数（工位 E）：GET /api/cases/defaults 的 items；null = 后端未提供（隐藏入口）
    defaults: null
  };

  function selectedIds() {
    return Object.keys(S.sel).filter(function (id) { return S.sel[id]; });
  }
  function suiteOf(id) {
    var found = null;
    S.suites.forEach(function (suite) { if (suite.id === id) found = suite; });
    return found;
  }

  // ---------- 持久化（UI-3）：方案 + 勾选（含未保存的临时勾选）+ 最近结论 ----------
  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        suite_id: S.suiteId,
        selected: selectedIds(),
        last_run: S.lastRun,
        last_job_id: S.jobId || "",
        last_run_id: S.lastRunId || "",
        group_by: S.groupBy,
        sort: S.sort
      }));
    } catch (error) {}
  }
  function readStore() {
    try {
      var parsed = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
      return obj(parsed);
    } catch (error) {
      return {};
    }
  }

  // ---------- 加载 ----------
  function loadCases() {
    return api("GET", "/api/cases").then(function (data) {
      var payload = obj(data);
      var items = payload.items || payload.cases || [];
      S.cases = arr(items).map(normCase);
      S.total = typeof payload.total === "number" ? payload.total : S.cases.length;
      S.problems = arr(payload.problems).map(String);
      S.apiOk = true;
      S.apiError = "";
    }).catch(function (error) {
      S.apiOk = false;
      S.apiError = error && error.status === 404
        ? "后端未就绪：GET /api/cases 不存在（cases_api 尚未挂载）"
        : ("用例列表加载失败：" + (error && error.message ? error.message : String(error)));
      S.cases = [];
      S.total = 0;
      S.problems = [];
    });
  }
  function loadSuites() {
    return api("GET", "/api/cases/suites").then(function (data) {
      var payload = obj(data);
      S.suites = arr(payload.suites || payload.items).map(normSuite);
    }).catch(function () {
      S.suites = [];
    });
  }
  function loadBench() {
    return api("GET", "/api/cases/bench").then(function (data) {
      S.bench = obj(data);
      computeBench();
      renderBench();   // 横幅先渲染（横幅慢修）：后端缓存后 <1s 返回，不等 loadCases/loadSuites
    }).catch(function (error) {
      S.bench = null;
      S.benchStale = false;
      S.benchPorts = [];
      S.benchFacts = [];
      S.benchMode = "unavailable";
      var missing = error && error.status === 404;
      S.benchReasons = [missing
        ? "后端未就绪：GET /api/cases/bench 尚未提供，无法预检台架（运行按钮保持可用，整轮可能判 blocked）"
        : "台架快照获取失败（见开发者详情）"];
      S.benchRaw = [missing
        ? "GET /api/cases/bench → HTTP 404"
        : ("GET /api/cases/bench 失败：" + (error && error.message ? error.message : String(error)))];
      renderBench();
    });
  }

  function computeBench() {
    var bench = S.bench || {};
    // 横幅慢修（2026-11-25）：stale=true = 缓存过期回旧值 / 重启后首查占位快照，
    // 后台都在真探——横幅显示轻量「事实检测中…」徽标，但**不阻塞**清单/按钮渲染，
    // 也不改闸门口径（ready 置灰逻辑不变）。
    S.benchStale = bench.stale === true;
    S.benchPorts = normPorts(bench);
    S.benchFacts = normFacts(bench);
    // 非闸门事实标注（REQS-0044 变更 4）：不在 required_facts 并集里的 = 信息性事实，
    // 芯片渲染走中性灰（见 renderBench）。后端没给 required_facts 时不瞎猜，保持旧口径。
    // 注意：闸门判定本体（unmet_required / reasons / ready）在下面原样保留，一字未动。
    var requiredFactIds = arr(bench.required_facts);
    S.benchFacts.forEach(function (fact) {
      fact.info = requiredFactIds.length > 0 && requiredFactIds.indexOf(fact.id) < 0;
    });
    S.benchInfo = [];
    // 运行闸门只看**必需事实**（后端 unmet_required = 用例库 needs 并集里不成立的）；
    // 信息性事实（如学习态的 nwk.* / 未注入能力的 sta.meter_addr_known）恒 unknown，
    // 若参与闸门会让按钮无谓置灰（2026-11-23 真机回归）。后端未给该字段时退化为全量口径。
    var requiredUnmet = (bench.unmet_required && typeof bench.unmet_required === "object")
      ? bench.unmet_required : null;
    var reasons = [];   // 默认区：一句人话
    var raw = [];       // 开发者详情：技术细节原样保留
    S.benchPorts.forEach(function (port) {
      if (port.enabled === false) return;   // 已禁用口不参与就绪判定
      if (port.usable) return;
      var detail = port.errors.join("；");
      raw.push("端口 " + port.id + "：不可用" + (detail ? "；" + detail : ""));
      reasons.push(clip("串口 " + port.id + " 不可用" +
        (detail ? "（" + oneLineBenchReason(detail) + "）" : ""), BENCH_REASON_MAX));
    });
    S.benchFacts.forEach(function (fact) {
      if (fact.state === "yes") return;
      var required = requiredUnmet ? Object.prototype.hasOwnProperty.call(requiredUnmet, fact.id) : true;
      var label = factLabel(fact.id);
      var stateText = fact.state === "no" ? "未成立" : "状态未知";
      var detail = String(fact.detail || "");
      raw.push("事实 " + fact.id + "：" + (fact.state === "no" ? "no" : "unknown") +
        (detail ? "；" + detail : "") + (required ? "（必需）" : "（信息性）"));
      var human;
      if (detail && hasInternalToken(detail)) {
        human = clip(label + "：" + oneLineBenchReason(detail), BENCH_REASON_MAX);
      } else if (detail) {
        human = clip(label + "：" + stateText + "（" + oneLineBenchReason(detail) + "）", BENCH_REASON_MAX);
      } else {
        human = label + "：" + stateText;
      }
      if (required) reasons.push(human); else S.benchInfo.push(human);
    });
    if (bench.ready === false && !reasons.length) {
      reasons.push("台架未就绪（后端未给出明细，见开发者详情）");
      raw.push("后端 ready=false，且未给出端口/事实明细");
    }
    if (!S.benchPorts.length && !S.benchFacts.length) {
      S.benchMode = "unknown";
      S.benchReasons = ["台架快照为空（既无端口也无事实），未做预检"];
      S.benchRaw = raw.length ? raw : ["GET /api/cases/bench 返回空快照"];
      return;
    }
    S.benchReasons = reasons;
    S.benchRaw = raw;
    S.benchMode = (bench.ready === false || reasons.length) ? "blocked" : "ready";
  }

  function loadAll() {
    return Promise.all([loadCases(), loadSuites(), loadBench(), loadRunsIndex(), loadDefaults()]).then(function () {
      restoreState();
      renderAll();
      // 刷新/重开后把上一轮 job 的进度接回来（终态则恢复结论着色，UI-4）
      if (S.resumeJobId) {
        var jobId = S.resumeJobId;
        S.resumeJobId = "";
        attachJob(jobId, "上一轮任务");
      }
    });
  }

  function restoreState() {
    var saved = readStore();
    S.lastRun = obj(saved.last_run);
    S.resumeJobId = clean(saved.last_job_id);
    S.lastRunId = clean(saved.last_run_id);
    if (S.lastRunId) {
      // 历史结果 run_id 回填（不自动切视图：用户点「查看结果」再进结果视图）
      var runInput = $("run-id-input");
      if (runInput && !runInput.value) runInput.value = S.lastRunId;
    }
    if (saved.group_by && ORDER[saved.group_by]) S.groupBy = saved.group_by;
    if (saved.sort) S.sort = saved.sort;

    var known = {};
    S.cases.forEach(function (item) { known[item.id] = true; });
    var missing = [];
    var restored = [];

    var suite = saved.suite_id ? suiteOf(saved.suite_id) : null;
    if (suite) {
      S.suiteId = suite.id;
      arr(suite.case_ids).forEach(function (id) {
        if (known[id]) restored.push(id); else missing.push(id);
      });
    } else if (arr(saved.selected).length) {
      arr(saved.selected).forEach(function (id) {
        if (known[id]) restored.push(id);
      });
    }

    S.sel = {};
    restored.forEach(function (id) { S.sel[id] = true; });
    if (missing.length) {
      toast("勾选恢复：" + missing.length + " 条用例已不在用例库，已跳过");
    }
  }

  // ---------- 渲染：顶栏状态 ----------
  function renderApiPill() {
    var pill = $("api-pill");
    var text = $("api-pill-text");
    if (!pill || !text) return;
    if (!S.apiOk) {
      pill.className = "pill bad";
      text.textContent = "后端未就绪";
      return;
    }
    if (S.problems.length) {
      pill.className = "pill warn";
      text.textContent = S.total + " 例 · " + S.problems.length + " 个声明问题";
      return;
    }
    pill.className = "pill ok";
    text.textContent = S.total + " 例";
  }

  function renderBenchPill() {
    var pill = $("bench-pill");
    var text = $("bench-pill-text");
    if (!pill || !text) return;
    var mode = S.benchMode;
    if (mode === "ready") { pill.className = "pill ok"; text.textContent = "台架就绪"; return; }
    if (mode === "blocked") { pill.className = "pill bad"; text.textContent = "台架未就绪"; return; }
    if (mode === "unavailable") { pill.className = "pill warn"; text.textContent = "台架快照不可用"; return; }
    if (mode === "checking") { pill.className = "pill"; text.textContent = "台架检测中…"; return; }
    pill.className = "pill warn";
    text.textContent = "台架状态说不清";
  }

  // 横幅「详情」折叠（工位 E）：就绪态只留一行，口状态/事实芯片收进详情；
  // 不就绪（blocked，即 unmet_required 非空或口不可用）自动展开列缺什么。
  // 用户点过「详情」后以用户的选择为准（benchDetailsOpen 不再是 null）。
  // setAttribute 走守卫（冒烟 DOM 桩没有该方法，真实浏览器有）。
  function setBenchDetails(open) {
    var box = $("bench-details");
    if (box) box.hidden = !open;
    var toggle = $("bench-detail-toggle");
    if (toggle) {
      toggle.textContent = open ? "▾ 详情" : "▸ 详情";
      if (toggle.setAttribute) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
  }

  function benchDetailsShouldOpen() {
    if (S.benchDetailsOpen !== null) return S.benchDetailsOpen;
    return S.benchMode === "blocked";   // 只有不就绪才自动展开（就绪/检测中/不可用都收一行）
  }

  function renderBench() {
    renderBenchPill();
    var banner = $("bench-banner");
    var stateEl = $("bench-state");
    if (banner) {
      banner.className = "bench " + { ready: "is-ready", blocked: "is-blocked", unavailable: "is-unknown", unknown: "is-unknown", checking: "" }[S.benchMode];
    }
    if (stateEl) {
      stateEl.textContent = {
        ready: "✓ 就绪",
        blocked: "✕ 未就绪 · 运行已置灰",
        unavailable: "? 快照不可用 · 未做预检",
        unknown: "? 状态说不清 · 未做预检",
        checking: "检测中…"
      }[S.benchMode] || "";
    }
    // 「事实检测中…」徽标（横幅慢修 2026-11-25）：stale 快照（缓存过期回旧值 /
    // 重启后首查占位）后台都在真探，30s 轮询会自动取回新结论
    var probingEl = $("bench-probing");
    if (probingEl) probingEl.hidden = !S.benchStale;

    var portsBox = document.querySelector('[data-hook="bench-ports"]');
    if (portsBox) {
      portsBox.innerHTML = '<span class="glabel">串口</span>' + (S.benchPorts.length
        ? S.benchPorts.map(function (port) {
          var name = esc(port.id) + (port.windows_com ? " " + esc(port.windows_com) : "");
          if (port.enabled === false) {
            // 已停用口（如 ecu-biz/ecu-mgmt）不参与就绪判定：中性灰芯片，不算「不可用」
            return '<span class="port" title="已停用，不参与台架就绪判定"><span class="dot"></span>' +
              name + "（停用）</span>";
          }
          return '<span class="port ' + (port.usable ? "on" : "off") + '" title="' +
            esc(port.errors.length ? port.errors.join("；") : (port.usable ? "可用" : "不可用")) + '">' +
            '<span class="dot"></span>' + name + "</span>";
        }).join("")
        : '<span class="hint">—</span>');
    }
    var factsBox = document.querySelector('[data-hook="bench-facts"]');
    if (factsBox) {
      var stateName = { yes: "成立", no: "未成立", unknown: "未知" };
      factsBox.innerHTML = '<span class="glabel">事实</span>' + (S.benchFacts.length
        ? S.benchFacts.map(function (fact) {
          // 非闸门事实（信息性，如运行期回写型）三态芯片一律中性灰（REQS-0044 变更 4）：
          // unknown 不是黄色「未知」，避免误读为故障；闸门判定（ready/unmet_required）不受影响
          var cls;
          if (fact.info) {
            cls = "info";
          } else {
            cls = fact.state === "yes" ? "yes" : (fact.state === "no" ? "no" : "unknown");
          }
          var tip = (fact.info ? "信息性事实（不参与运行闸门） · " : "") +
            factLabel(fact.id) + " · " + (stateName[fact.state] || "未知");
          // 只显示人话（事实 id 放 data-fact 供排查，不显示给用户）
          return '<span class="fact ' + cls + '" data-fact="' + esc(fact.id) + '" title="' +
            esc(tip) + '">' +
            '<span class="dot"></span>' + esc(factLabel(fact.id)) + " · " +
            esc(stateName[fact.state] || "未知") + "</span>";
        }).join("")
        : '<span class="hint">—</span>');
    }
    var reasonsBox = $("bench-reasons");
    if (reasonsBox) {
      reasonsBox.innerHTML = S.benchReasons.map(function (reason) {
        return '<li class="' + (S.benchMode === "unavailable" || S.benchMode === "unknown" ? "heal" : "") + '">' +
          esc(reason) + "</li>";
      }).join("");
    }
    var rawBox = $("bench-raw");
    if (rawBox) {
      rawBox.textContent = (S.benchRaw || []).join("\n");
    }
    var rawHead = $("bench-dev-head");
    if (rawHead) rawHead.hidden = !(S.benchRaw || []).length;
    // 折叠口径（工位 E）：自动 = 不就绪才展开；用户点过「详情」则以用户为准
    setBenchDetails(benchDetailsShouldOpen());
  }

  // ---------- 渲染：筛选与分组 ----------
  function fillFilter(selectId, dim, fallbackLabel) {
    var select = $(selectId);
    if (!select) return;
    var values = [];
    S.cases.forEach(function (item) {
      var value = catOf(item)[dim];
      if (value && values.indexOf(value) < 0) values.push(value);
    });
    values.sort(function (a, b) {
      return (rankOf(ORDER[dim], a) - rankOf(ORDER[dim], b)) || a.localeCompare(b, "zh");
    });
    var current = S.filters[dim];
    var html = '<option value="">' + esc(fallbackLabel) + "：全部</option>";
    values.forEach(function (value) {
      html += '<option value="' + esc(value) + '">' + esc(dimLabel(dim, value)) + "</option>";
    });
    html += '<option value="' + UNCAT + '">' + UNCAT_LABEL + "</option>";
    select.innerHTML = html;
    select.value = current === "" ? "" : (values.indexOf(current) >= 0 || current === UNCAT ? current : "");
  }

  function renderFilters() {
    fillFilter("f-usage", "usage", "常用度");
    fillFilter("f-domain", "domain", "设备域");
    fillFilter("f-scene", "scene", "场景");
    fillFilter("f-region", "region", "区域");

    var protocolSelect = $("f-protocol");
    if (protocolSelect) {
      var protocols = [];
      S.cases.forEach(function (item) {
        item.protocols.forEach(function (value) { if (protocols.indexOf(value) < 0) protocols.push(value); });
      });
      protocols.sort();
      protocolSelect.innerHTML = '<option value="">协议：全部</option>' + protocols.map(function (value) {
        return '<option value="' + esc(value) + '">' + esc(value) + "</option>";
      }).join("");
      protocolSelect.value = S.filters.protocol;
    }
    var sortSelect = $("f-sort");
    if (sortSelect) sortSelect.value = S.sort;
    var groupSelect = $("group-by");
    if (groupSelect) groupSelect.value = S.groupBy;
    var search = $("q");
    if (search && search.value !== S.filters.q) search.value = S.filters.q;
    if ($("rail-total")) {
      $("rail-total").textContent = S.cases.length ? ("共 " + S.cases.length + " 例") : "";
    }
  }

  function matches(item) {
    var category = catOf(item);
    var dims = ["usage", "domain", "scene", "region"];
    for (var i = 0; i < dims.length; i += 1) {
      var dim = dims[i];
      var want = S.filters[dim];
      if (!want) continue;
      if (want === UNCAT) {
        if (category[dim]) return false;
      } else if (category[dim] !== want) {
        return false;
      }
    }
    if (S.filters.protocol && item.protocols.indexOf(S.filters.protocol) < 0) return false;
    var query = (S.filters.q || "").toLowerCase();
    if (query) {
      var haystack = [item.id, item.title].concat(item.tags).concat(item.steps.map(function (step) {
        return step.do + " " + step.expect;
      })).join(" ").toLowerCase();
      if (haystack.indexOf(query) < 0) return false;
    }
    return true;
  }

  function comparator() {
    if (S.sort === "name") {
      return function (a, b) { return a.title.localeCompare(b.title, "zh"); };
    }
    if (S.sort === "steps") {
      return function (a, b) { return (b.step_count - a.step_count) || a.title.localeCompare(b.title, "zh"); };
    }
    // 默认排序（D10-2）：常用度 → 设备域 → 场景 → 名称
    return function (a, b) {
      var ca = catOf(a);
      var cb = catOf(b);
      return (rankOf(USAGE_ORDER, ca.usage) - rankOf(USAGE_ORDER, cb.usage))
        || (rankOf(DOMAIN_ORDER, ca.domain) - rankOf(DOMAIN_ORDER, cb.domain))
        || (rankOf(SCENE_ORDER, ca.scene) - rankOf(SCENE_ORDER, cb.scene))
        || a.title.localeCompare(b.title, "zh");
    };
  }

  function visibleCases() {
    return S.cases.filter(matches).sort(comparator());
  }

  function groupCases(items) {
    var buckets = {};
    items.forEach(function (item) {
      var key = catOf(item)[S.groupBy] || "";
      if (!buckets[key]) buckets[key] = [];
      buckets[key].push(item);
    });
    return Object.keys(buckets).map(function (key) {
      return { key: key, label: dimLabel(S.groupBy, key), items: buckets[key] };
    }).sort(function (a, b) {
      if (!a.key !== !b.key) return a.key ? -1 : 1;   // 「未分类」永远排最后
      return (rankOf(ORDER[S.groupBy], a.key) - rankOf(ORDER[S.groupBy], b.key))
        || a.label.localeCompare(b.label, "zh");
    });
  }

  // ---------- 渲染：左栏多维快捷筛选（REQS-0044 Demo 口径：常用度/设备域/场景/省份/协议） ----------
  var RAIL_DIMS = [
    { key: "usage", label: "常用度" },
    { key: "domain", label: "设备域" },
    { key: "scene", label: "业务场景" },
    { key: "region", label: "省份 / 区域" },
    { key: "protocol", label: "协议" }
  ];

  function railValues(dim) {
    var values = [];
    S.cases.forEach(function (item) {
      var raw = dim === "protocol" ? arr(item.protocols) : [catOf(item)[dim]];
      raw.forEach(function (value) {
        var v = clean(value);
        if (v && values.indexOf(v) < 0) values.push(v);
      });
    });
    var order = dim === "protocol" ? null : ORDER[dim];
    values.sort(function (a, b) {
      return order
        ? (rankOf(order, a) - rankOf(order, b)) || a.localeCompare(b, "zh")
        : a.localeCompare(b, "zh");
    });
    return values;
  }

  function renderRail() {
    var body = $("rail-body");
    if (!body) return;
    var total = S.cases.length;
    if ($("rail-total")) {
      $("rail-total").textContent = total ? ("共 " + total + " 例") : "";
    }
    if (!total) {
      body.innerHTML = '<div class="hint">用例库为空</div>';
      return;
    }
    body.innerHTML = RAIL_DIMS.map(function (dim) {
      var values = railValues(dim.key);
      if (dim.key !== "protocol") {
        var hasUncat = S.cases.some(function (item) { return !clean(catOf(item)[dim.key]); });
        if (hasUncat) values.push(UNCAT);
      }
      var items = values.map(function (value) {
        var isUncat = value === UNCAT;
        var filterVal = isUncat ? UNCAT : value;
        var label = isUncat ? UNCAT_LABEL : dimLabel(dim.key, value);
        var count = S.cases.filter(function (item) {
          if (dim.key === "protocol") return item.protocols.indexOf(value) > -1;
          var v = clean(catOf(item)[dim.key]);
          return isUncat ? !v : v === value;
        }).length;
        var on = S.filters[dim.key] === filterVal ? " on" : "";
        return '<div class="rail-item' + on + '" data-dim="' + dim.key + '" data-val="' + esc(filterVal) +
          '" role="button" tabindex="0"><span class="swatch"></span>' + esc(label) +
          '<span class="rcount">' + count + "</span></div>";
      }).join("");
      return '<div class="rail-group"><b>' + esc(dim.label) + "</b>" + items + "</div>";
    }).join("");
    Array.prototype.forEach.call(body.querySelectorAll(".rail-item"), function (row) {
      row.onclick = function () {
        var dim = row.dataset.dim;
        var val = row.dataset.val;
        S.filters[dim] = (S.filters[dim] === val) ? "" : val;
        persist();
        renderFilters();
        renderRail();
        renderList();
      };
    });
  }

  // ---------- 渲染：用例列表（UI-1 · 144 台账极简口径，2026-11-25 用户裁决） ----------
  // 卡片收敛为「用例名 + 一句话描述 + 展开箭头」：此前的步数芯片 / 业务动作链摘要 /
  // 分类标签组（usage/domain/scene/region/协议/#tags）/ kind 徽标堆叠全部移除——
  // 分类仍由左栏分组与筛选承载（renderRail / renderFilters），帧标识与完整 do/expect
  // 只进「展开」区（renderSteps）。用例 id 收进用例名悬浮提示，不再平铺在卡面。
  function renderSteps(item) {
    if (!item.steps.length) return "";
    return '<ol class="steps" data-hook="case-steps" hidden>' + item.steps.map(function (step) {
      return '<li class="step" data-step="' + esc(step.no) + '">' +
        '<span class="step-no">' + esc(step.no) + "</span>" +
        "<div><div class=\"step-do\">" + esc(step.do || "—") + "</div>" +
        (step.expect ? '<div class="step-expect"><b>期望：</b>' + esc(step.expect) + "</div>" : "") +
        "</div></li>";
    }).join("") + "</ol>";
  }

  function renderCaseRow(item) {
    var last = normVerdict(S.lastRun[item.id]);
    var html = '<article class="case" data-case-id="' + esc(item.id) + '">';
    html += '<div class="case-head">';
    html += '<input type="checkbox" class="pick" data-id="' + esc(item.id) + '"' +
      (S.sel[item.id] ? " checked" : "") + ' aria-label="勾选 ' + esc(item.id) + '">';
    html += '<b class="case-title" title="' + esc(item.id) + '">' + esc(item.title) + "</b>";
    // kind 徽标收敛为一枚低调徽标且只留给非默认类（场景/台架准备）：回归是默认态，
    // 卡面不再亮徽标（D/E 轮守卫保留的 kindBadge 词典与文案原样保留）
    if (item.kind === "scenario" || item.kind === "setup") html += kindBadge(item.kind);
    if (last) html += badge(last);
    html += '<span class="spacer"></span>';
    html += '<button class="mini run-one" data-id="' + esc(item.id) + '" title="立即运行此用例">▶</button>';
    html += '<button class="mini expand" data-id="' + esc(item.id) +
      '" title="展开步骤与预期" aria-label="展开 ' + esc(item.id) + ' 的步骤与预期">▸</button>';
    html += "</div>";
    // 一句话描述（声明 description 字段）：卡面唯一说明行；后端未透传 / 与用例名相同
    // 时不渲染，卡片退为「用例名 + 展开箭头」
    var description = String(item.description || "");
    if (description && description !== item.title) {
      html += '<div class="case-sum">' + esc(description) + "</div>";
    }
    html += renderSteps(item);
    html += "</article>";
    return html;
  }

  function renderList() {
    var wrap = $("list-wrap");
    if (!wrap) return;
    var countHint = $("list-count");
    if (countHint) countHint.textContent = "";

    if (!S.apiOk) {
      wrap.innerHTML = '<div class="empty"><b>后端未就绪</b>' + esc(S.apiError) +
        '<div class="hint" style="margin-top:8px">页面已渲染但无数据可读；cases_api 挂载后点「刷新」即可。</div></div>';
      return;
    }
    if (!S.cases.length) {
      wrap.innerHTML = '<div class="empty"><b>用例库为空</b>GET /api/cases 返回 0 条用例。</div>';
      return;
    }
    var items = visibleCases();
    if (countHint) countHint.textContent = "共 " + items.length + " 条（库 " + S.cases.length + " 条）";
    if (!items.length) {
      wrap.innerHTML = '<div class="empty"><b>无匹配用例</b>当前筛选条件下没有用例；清空筛选可恢复全部 ' +
        S.cases.length + " 例。</div>";
      return;
    }
    var problems = S.problems.length
      ? '<div class="degraded" data-hook="problems">⚠ 用例声明问题 ' + S.problems.length + " 条：" +
        esc(S.problems.slice(0, 3).join("；")) + (S.problems.length > 3 ? " …" : "") + "</div>"
      : "";
    var groups = groupCases(items);
    wrap.innerHTML = problems + groups.map(function (group) {
      return '<section class="group" data-group="' + esc(group.key || UNCAT) + '">' +
        '<div class="group-head"><span class="gbar"></span><b>' + esc(group.label) + "</b>" +
        '<span class="gcount">' + group.items.length + " 例</span></div>" +
        group.items.map(renderCaseRow).join("") + "</section>";
    }).join("");

    Array.prototype.forEach.call(wrap.querySelectorAll(".case"), function (row) {
      var id = row.dataset.caseId;
      var pick = row.querySelector(".pick");
      if (pick) {
        pick.onchange = function () {
          if (pick.checked) S.sel[id] = true; else delete S.sel[id];
          persist();
          renderSelCount();
          renderRunButtons();
        };
      }
      var expand = row.querySelector(".expand");
      if (expand) {
        expand.onclick = function () {
          var steps = row.querySelector(".steps");
          if (!steps) return;
          steps.hidden = !steps.hidden;
          expand.textContent = steps.hidden ? "▸" : "▾";
        };
      }
      var run = row.querySelector(".run-one");
      if (run) run.onclick = function () { runJob({ case_ids: [id] }, "单例 " + id); };
    });
    renderSelCount();
    renderRunButtons();
  }

  function renderSelCount() {
    var el = $("sel-count");
    var count = selectedIds().length;
    if (el) el.textContent = count ? ("已勾选 " + count + " 例") : "未勾选";
    var clear = $("btn-clear-sel");
    if (clear) clear.disabled = !count;
    var save = $("btn-save-suite");
    if (save) save.disabled = !count;   // 同 Demo：未勾选时「存方案」置灰
  }

  // ---------- 渲染：方案（UI-3） ----------
  function renderSuites() {
    var select = $("suite-select");
    if (select) {
      select.innerHTML = '<option value="">（未选方案）</option>' + S.suites.map(function (suite) {
        return '<option value="' + esc(suite.id) + '">' + esc(suite.name || suite.id) +
          "（" + suite.case_ids.length + " 例）</option>";
      }).join("");
      select.value = S.suiteId;
    }
    var del = $("btn-del-suite");
    if (del) del.disabled = !S.suiteId;
  }

  // ---------- 运行闸门（UI-2）：台架不满足时置灰 + 逐项原因 ----------
  function gate() {
    if (!S.apiOk) return { ok: false, reason: "后端未就绪：" + S.apiError };
    if (S.benchMode === "blocked") return { ok: false, reason: "台架未就绪：" + S.benchReasons.join("；") };
    return { ok: true, reason: "" };
  }

  function renderRunButtons() {
    var decision = gate();
    var count = selectedIds().length;
    var runNow = $("btn-run-now");
    if (runNow) {
      runNow.disabled = !decision.ok || !count;
      runNow.title = !decision.ok ? decision.reason : (count ? "运行勾选的 " + count + " 例" : "勾选用例后运行");
    }
    var runSuite = $("btn-run-suite");
    if (runSuite) {
      runSuite.disabled = !decision.ok || !S.suiteId;
      runSuite.title = !decision.ok ? decision.reason : (S.suiteId ? "按方案 " + S.suiteId + " 运行" : "先选择方案");
    }
    Array.prototype.forEach.call(document.querySelectorAll(".run-one"), function (button) {
      button.disabled = !decision.ok;
      button.title = !decision.ok ? decision.reason : "立即运行此用例";
    });
  }

  // 运行参数（工位 E）：单用例地址入参入口已收敛进「默认参数」（config/case_defaults.json），
  // 页面不再平铺一个逐轮覆盖输入框；需要临时覆盖时仍可直接改默认参数。
  function collectParams() {
    return {};
  }

  function runJob(body, label) {
    var decision = gate();
    if (!decision.ok) { toast(decision.reason); return; }
    var payload = { params: collectParams(), base_url: window.location.origin };
    if (body.suite_id) payload.suite_id = body.suite_id;
    if (body.case_ids) payload.case_ids = body.case_ids;
    api("POST", "/api/cases/run", payload).then(function (data) {
      var res = obj(data);
      if (res.accepted === false) {
        // 预检未通过（不建 job，<1s 返回）：结果已按 run_id 落盘 → 直接进结果视图看逐行原因
        toast("预检未通过，本轮未运行（详见结果视图）");
        if (clean(res.run_id)) {
          loadRun(res.run_id);
        } else {
          showJobError(new Error("预检未通过（unmet_facts 见响应），且未返回 run_id"));
        }
        return;
      }
      var jobId = res.job_id || obj(res.job).job_id || "";
      if (!jobId) { toast("提交成功但未返回 job_id"); return; }
      toast("已提交（" + (label || "job") + "）：" + jobId);
      attachJob(jobId, label);
    }).catch(function (error) {
      toast("提交失败：" + error.message);
      showJobError(error);
    });
  }

  function showJobError(error) {
    var panel = $("job-panel");
    if (panel) panel.classList.add("show");
    S.jobTerminal = false;
    S.jobLabel = "任务提交失败";
    if ($("job-line")) $("job-line").textContent = S.jobLabel;
    var progressLine = $("job-progress-line");
    if (progressLine) { progressLine.textContent = ""; progressLine.hidden = true; }
    var doneLine = $("job-done-line");
    if (doneLine) { doneLine.hidden = true; doneLine.innerHTML = ""; }
    if ($("job-stage")) $("job-stage").textContent = "HTTP " + (error.status || "?");
    if ($("job-results")) {
      $("job-results").innerHTML = '<div class="degraded" data-hook="job-error">' + esc(error.message) + "</div>";
    }
    if ($("job-log")) $("job-log").textContent = "";
    setJobExpanded(true);   // 错误默认展开（需要用户看到原因），行不可点击收起
  }

  // ---------- job 面板单行/折叠（工位 E）----------
  // 运行中：单行 = 方案名 + 进度 n/m + 当前用例名（job-collapsed 行，collapsible 收起）。
  // 终态：自动收起为一行 = ✔/✘ + run_id + verdict + 耗时；点击该行才展开完整结果。
  function setJobExpanded(open) {
    S.jobExpanded = !!open;
    var wrap = $("job-collapsible");
    if (wrap) wrap.hidden = !S.jobExpanded;
    var line = $("job-collapsed");
    if (line && line.setAttribute) line.setAttribute("aria-expanded", S.jobExpanded ? "true" : "false");
    var expander = $("job-expander");
    if (expander) expander.textContent = S.jobExpanded ? "▾" : "▸";
  }

  function setJobClickable(clickable) {
    S.jobTerminal = !!clickable;
    var panel = $("job-panel");
    if (panel) {
      // 不用 classList.toggle（冒烟 DOM 桩只实现了 add/remove/contains）
      if (clickable) panel.classList.add("clickable");
      else panel.classList.remove("clickable");
    }
  }

  // 终态单行：✔（全通过）/ ✘（有未通过/说不清/台架不具备/任务失败）+ run_id + verdict + 耗时
  function jobDoneGlyph(job) {
    var counts = obj(job.case_counts);
    var total = num(counts.pass) + num(counts.fail) + num(counts.blocked) + num(counts.inconclusive);
    if (!total) {
      arr(job.results || job.cases).forEach(function (raw) {
        var verdict = normVerdict(obj(raw).verdict);
        if (!verdict) return;
        if (verdict === "pass") counts.pass = num(counts.pass) + 1;
        else counts[verdict] = num(counts[verdict]) + 1;
        total += 1;
      });
    }
    var failed = String(job.state || "").toLowerCase() === "failed"
      || num(counts.fail) + num(counts.blocked) + num(counts.inconclusive) > 0;
    if (!total && !failed) return "✔";
    return failed ? "✘" : "✔";
  }

  function jobDoneLineHtml(job) {
    var glyph = jobDoneGlyph(job);
    var glyphCls = glyph === "✔" ? "is-pass" : "is-fail";
    var verdictPhrase = clean(job.verdict) ? (VERDICT_HEAD[normVerdict(job.verdict)] || verdictText(job.verdict))
      : summaryText(job);
    var seconds = num(job.duration_seconds) || num(job.duration_s);
    var parts = [];
    if (clean(job.run_id)) parts.push('<span class="job-runid">' + esc(job.run_id) + "</span>");
    if (verdictPhrase) parts.push(esc(verdictPhrase));
    if (seconds) parts.push(esc(seconds) + "s");
    return '<span class="job-glyph ' + glyphCls + '">' + glyph + "</span>" + parts.join(" · ");
  }

  // 终态收起：单行换成 ✔/✘ + run_id + verdict + 耗时，展开区收起；「查看完整报表」
  // 按钮（有 run_id 时）保留结果视图入口——不再自动整页跳转（工位 E 口径）。
  function collapseJobDone(job) {
    setJobClickable(true);
    var progressLine = $("job-progress-line");
    if (progressLine) { progressLine.textContent = ""; progressLine.hidden = true; }
    var doneLine = $("job-done-line");
    if (doneLine) {
      doneLine.innerHTML = jobDoneLineHtml(job);
      doneLine.hidden = false;
    }
    var openReport = $("btn-open-report");
    if (openReport) {
      openReport.hidden = !clean(job.run_id);
      openReport.onclick = function () { loadRun(clean(job.run_id)); };
    }
    setJobExpanded(false);   // 完成后自动收起为一行；点击该行才展开完整结果
  }

  // ---------- job 轮询（UI-4） ----------
  function attachJob(jobId, label) {
    S.jobId = jobId;
    S.job = null;
    S.jobLabel = clean(label) || "任务";
    var panel = $("job-panel");
    if (panel) panel.classList.add("show");
    if ($("job-line")) $("job-line").textContent = S.jobLabel;
    var progressLine = $("job-progress-line");
    if (progressLine) { progressLine.textContent = ""; progressLine.hidden = false; }
    var doneLine = $("job-done-line");
    if (doneLine) { doneLine.hidden = true; doneLine.innerHTML = ""; }
    if ($("job-stage")) $("job-stage").textContent = "提交完成，等待进度…";
    if ($("job-current")) $("job-current").textContent = "";
    if ($("job-summary")) $("job-summary").textContent = "";
    if ($("job-results")) $("job-results").innerHTML = "";
    if ($("job-log")) $("job-log").textContent = "";
    var openReport = $("btn-open-report");
    if (openReport) openReport.hidden = true;
    setJobClickable(false);
    setJobExpanded(false);   // 运行中只显示单行进度（工位 E）
    setBar(0, "");
    if (S.jobTimer) { clearTimeout(S.jobTimer); S.jobTimer = null; }
    pollJob();
  }

  function setBar(percent, text) {
    var bar = $("job-bar");
    if (bar) bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
    if (text && $("job-current")) $("job-current").textContent = text;
  }

  function pollJob() {
    if (!S.jobId) return;
    var jobId = S.jobId;
    api("GET", "/api/cases/jobs/" + encodeURIComponent(jobId)).then(function (data) {
      if (S.jobId !== jobId) return;      // 期间又提了新任务：丢弃旧响应
      var job = obj(data);
      S.job = job;
      renderJob(job);
      var state = String(job.state || "").toLowerCase();
      if (state === "done" || state === "failed" || state === "cancelled" || state === "canceled") {
        S.jobTimer = null;
        var results = arr(job.results || job.cases);
        results.forEach(function (result) {
          var id = result.case_id || "";
          if (id) S.lastRun[id] = normVerdict(result.verdict);
        });
        persist();
        renderList();
        // 报表契约（REQS-0044 变更 3）：job 终态带 run.report → 记下 run_id 供「查看完整报表」
        // 与历史下拉；整页不再自动跳转结果视图，面板自动收起为一行（工位 E 口径）
        var reportRows = arr(obj(job.report).rows);
        if (reportRows.length) {
          S.lastRunId = clean(job.run_id) || S.lastRunId;
          persist();
          var runInput = $("run-id-input");
          if (runInput && S.lastRunId) runInput.value = S.lastRunId;
        }
        collapseJobDone(job);
        toast("任务结束：" + stateLabel(state) + " · " + summaryText(job));
        return;
      }
      S.jobTimer = setTimeout(pollJob, POLL_MS);
    }).catch(function (error) {
      if (S.jobId !== jobId) return;
      if ($("job-stage")) $("job-stage").textContent = "轮询失败：" + error.message;
      if (error.status === 404) {
        S.jobTimer = null;
        if ($("job-results")) {
          $("job-results").innerHTML = '<div class="degraded" data-hook="job-error">' +
            "GET /api/cases/jobs/" + esc(jobId) + " 不可用（后端未挂载或任务不存在）；已停止轮询。</div>";
        }
        return;
      }
      S.jobTimer = setTimeout(pollJob, 3000);
    });
  }

  // 顶部汇总：一句话（D11-1），如「本轮 2 例全部通过」「本轮 1 例通过、1 例未通过（组网超时）」
  function summaryText(job) {
    var results = arr(job.results || job.cases);
    var counts = obj(job.case_counts);
    var tally = { pass: 0, fail: 0, blocked: 0, inconclusive: 0 };
    var total = 0;
    ["pass", "fail", "blocked", "inconclusive"].forEach(function (key) {
      var value = num(counts[key]);
      tally[key] = value;
      total += value;
    });
    if (!total) {
      results.forEach(function (raw) {
        var verdict = normVerdict(obj(raw).verdict);
        if (!verdict) return;
        tally[verdict] += 1;
        total += 1;
      });
    }
    if (!total) return job.verdict ? ("结论 " + verdictText(job.verdict)) : "";
    if (tally.pass === total) return "本轮 " + total + " 例全部通过";
    var failReasons = [];
    if (tally.fail) {
      results.forEach(function (raw) {
        var result = obj(raw);
        if (normVerdict(result.verdict) !== "fail") return;
        var reasons = reasonList(result, "fail", normSummary(result));
        if (reasons.length && failReasons.indexOf(reasons[0]) < 0) failReasons.push(reasons[0]);
      });
    }
    var parts = [];
    if (tally.pass) parts.push(tally.pass + " 例通过");
    if (tally.fail) {
      parts.push(tally.fail + " 例未通过" +
        (failReasons.length ? "（" + clip(failReasons.slice(0, 2).join("；"), TITLE_REASON_MAX) + "）" : ""));
    }
    if (tally.blocked) parts.push(tally.blocked + " 例台架不具备");
    if (tally.inconclusive) parts.push(tally.inconclusive + " 例说不清");
    return "本轮 " + parts.join("、");
  }

  function renderJob(job) {
    var progress = obj(job.progress);
    var total = num(progress.total);
    var done = num(progress.done);
    var percent = typeof progress.percent === "number"
      ? progress.percent
      : (total ? Math.round(done * 100 / total) : 0);
    setBar(percent, "");
    // 单行进度（工位 E）：方案名 + 进度 n/m + 当前用例名，一行说完
    var current = progress.current_case_title || progress.current_case_id || "";
    var progressLine = $("job-progress-line");
    if (progressLine) {
      progressLine.textContent = (total ? ("进度 " + done + "/" + total + (percent ? " · " + percent + "%" : "")) : "")
        + (current ? "　当前：" + current : "");
    }
    if ($("job-stage")) $("job-stage").textContent = stateLabel(job.state);
    if ($("job-current")) {
      $("job-current").textContent = (total ? ("进度 " + done + "/" + total + " · " + percent + "%") : "")
        + (current ? "　当前：" + current : "");
    }
    if ($("job-summary")) $("job-summary").textContent = summaryText(job);
    var results = arr(job.results || job.cases);
    var resultsWrap = $("job-results");
    if (resultsWrap) {
      resultsWrap.innerHTML = results.map(function (result) {
        return renderCaseResult(result, job);
      }).join("");
      bindReportActions(resultsWrap);
    }
    var log = [];
    if (job.error) log.push("任务错误：" + job.error);
    if (job.result_path) log.push("证据落盘：" + job.result_path);
    if (job.run_id) log.push("run_id：" + job.run_id);
    results.forEach(function (result) {
      if (result.error) log.push(result.case_id + "：" + result.error);
      if (result.skipped_reason) log.push(result.case_id + " 跳过原因：" + result.skipped_reason);
    });
    if ($("job-log")) $("job-log").textContent = log.join("\n");
  }

  // ---------- 开发者详情（D11-2）：内部块名只在这里与「开发者详情」折叠内出现 ----------
  // 块名 → 业务动作词典（REQS-0042 v11 D11-2 口径）。默认展示区只消费 actionLabel() 的返回值，
  // 词典与逐步明细一律不进默认区（反向守卫见 apps/workbench/test_cases_page.py）。
  var BLOCK_ACTION = [
    ["fix.simcon_session", "台架自检"],
    ["fix.simeter_session", "台架自检"],
    ["reset.sta", "复位 STA"],
    ["archive.add", "档案下发"],
    ["nwk.topology", "组网"],
    ["meter.addr_probe", "绑表（取表地址）"],
    ["meter.read_645", "抄表"],
    ["nwk.nodes_online_min", "组网节点数"],
    ["nwk.onnet_count_min", "在网节点数"],
    ["nwk.route_running", "路由运行"],
    ["bench.sta_online", "STA 在线"],
    ["bench.simcon_online", "模拟集中器在线"],
    ["bench.simeter_online", "模拟电表在线"],
    ["sta.meter_addr_known", "表地址已知"],
    ["archive.contains", "档案存在"]
  ];

  function normBlockKey(value) {
    return String(value == null ? "" : value).toLowerCase().replace(/_/g, ".");
  }

  // 块名 → 人话（未知块名返回空串：宁可不显示，也不把内部 id 甩给用户）
  function actionLabel(blockId) {
    var key = normBlockKey(blockId);
    if (!key) return "";
    var best = "";
    var bestLabel = "";
    for (var i = 0; i < BLOCK_ACTION.length; i += 1) {
      var known = normBlockKey(BLOCK_ACTION[i][0]);   // 词典键同样归一化：a.b_c ≡ a.b.c
      if (key === known) return BLOCK_ACTION[i][1];
      if (key.indexOf(known) === 0 && known.length > best.length) {
        best = known;
        bestLabel = BLOCK_ACTION[i][1];
      }
    }
    return bestLabel;
  }

  function blockDetail(result, blockId) {
    var rows = arr(result.blocks || result.steps || []);
    for (var i = 0; i < rows.length; i += 1) {
      var row = obj(rows[i]);
      var id = row.block_id || row.block || row.id || "";
      if (id === blockId) return String(row.detail || row.message || row.reason || "");
    }
    return "";
  }

  function unmetFactsHtml(raw) {
    var entries = [];
    if (Array.isArray(raw)) {
      entries = raw.map(function (item) {
        if (typeof item === "string") return { id: item, state: "unknown", detail: "" };
        var fact = obj(item);
        return { id: String(fact.id || fact.fact_id || "?"), state: normFactState(fact.state), detail: String(fact.detail || "") };
      });
    } else {
      Object.keys(obj(raw)).forEach(function (key) {
        var fact = obj(obj(raw)[key]);
        var state = fact.state === undefined ? (obj(raw)[key] === false ? "no" : "unknown") : normFactState(fact.state);
        entries.push({ id: key, state: state, detail: String(fact.detail || "") });
      });
    }
    return entries.map(function (fact) {
      return '<div class="bp-item bp-fact"><span class="k">未满足事实</span><code>' + esc(fact.id) +
        "</code><span>" + esc(fact.state) + (fact.detail ? " — " + esc(fact.detail) : "") + "</span></div>";
    }).join("");
  }

  function selfHealedHtml(raw) {
    return arr(raw).map(function (item) {
      if (typeof item === "string") {
        return '<div class="bp-item bp-heal"><span class="k">自愈</span><span>' + esc(item) + "</span></div>";
      }
      var heal = obj(item);
      var target = heal.target || heal.block_id || "";
      var result = heal.result || "";
      return '<div class="bp-item bp-heal"><span class="k">自愈</span>' +
        (target ? "<code>" + esc(target) + "</code>" : "") +
        "<span>" + esc(heal.action || "retry") + (result ? " → " + esc(result) : "") +
        (heal.detail ? "（" + esc(heal.detail) + "）" : "") + "</span></div>";
    }).join("");
  }

  function breakpointHtml(result) {
    var items = [];
    if (result.failed_block) {
      items.push('<div class="bp-item bp-failed"><span class="k">失败块</span><code>' + esc(result.failed_block) +
        "</code><span class=\"v\">" + esc(blockDetail(result, result.failed_block) || "用例在此块判失败") + "</span></div>");
    }
    if (result.blocked_block) {
      items.push('<div class="bp-item bp-blocked"><span class="k">阻断块</span><code>' + esc(result.blocked_block) +
        "</code><span>" + esc(blockDetail(result, result.blocked_block) || "台架不具备条件，未进入后续动作") + "</span></div>");
    }
    items.push(unmetFactsHtml(result.unmet_facts));
    items.push(selfHealedHtml(result.self_healed));
    if (result.skipped_reason) {
      items.push('<div class="bp-item bp-blocked"><span class="k">跳过原因</span><span>' + esc(result.skipped_reason) + "</span></div>");
    }
    var body = items.join("");
    return body ? '<div class="bp" data-hook="breakpoint">' + body + "</div>" : "";
  }

  function stepState(row) {
    if (row.outcome) return normVerdict(row.outcome);
    var state = String(row.state || "").toLowerCase();
    if (state === "pass") return "pass";
    if (state === "fail") return "fail";
    if (state === "skipped") return "blocked";
    if (state === "unknown") return "inconclusive";
    return normVerdict(state) || "inconclusive";
  }

  function resultRowsHtml(result) {
    var rows = arr(result.blocks || result.steps || []);
    if (!rows.length) return "";
    var html = '<table class="run-steps"><thead><tr><th style="width:46px">步骤</th><th>块 / 动作</th>' +
      "<th style=\"width:84px\">块态</th><th>说明</th></tr></thead><tbody>";
    rows.forEach(function (raw, index) {
      var row = obj(raw);
      var id = row.block_id || row.block || row.id || ("step-" + (index + 1));
      var state = stepState(row);
      var cls = "st-" + state;
      var isProblem = (result.failed_block && id === result.failed_block)
        || (result.blocked_block && id === result.blocked_block);
      if (isProblem) cls += " is-problem";
      var label = row.no || index + 1;
      html += '<tr class="' + cls + '" data-block="' + esc(id) + '">' +
        "<td>" + esc(label) + "</td>" +
        '<td class="mono">' + esc(id) + "</td>" +
        '<td><span class="block-state ' + state + '">' + esc(verdictText(state)) + "</span></td>" +
        "<td>" + esc(row.detail || row.message || "") + "</td></tr>";
    });
    html += "</tbody></table>";
    return html;
  }

  // 逐步明细收进「开发者详情」折叠（默认 hidden）：排查用，块名只允许出现在这里
  function devDetailsHtml(result) {
    var inner = breakpointHtml(result) + resultRowsHtml(result);
    if (result.error) inner += '<div class="bp-item bp-failed"><span class="k">错误</span><span>' + esc(result.error) + "</span></div>";
    // 默认区最多显示 3 条原因；被折叠掉的（含块层原始 detail）都在这里看全量
    var verdict = normVerdict(result.verdict);
    var summary = normSummary(result);
    var allReasons = summary.reasons.length ? summary.reasons : derivedReasons(result, verdict);
    if (allReasons.length > 3) {
      inner += '<div class="bp-item bp-fact"><span class="k">全部原因</span><span>' +
        esc(allReasons.join("；")) + "</span></div>";
    }
    if (!inner) return "";
    return '<div class="rc-dev-head">' +
      '<button class="mini rc-dev-toggle" data-hook="dev-toggle" aria-expanded="false">开发者详情 ▸</button>' +
      "</div>" +
      '<div class="rc-dev" data-hook="dev-details" hidden>' + inner + "</div>";
  }

  /* ==== 报告卡默认区 BEGIN（D11-1 / D11-3）====
   * 反向守卫（apps/workbench/test_cases_page.py）：本区间只允许出现人话文案与证据路径，
   * 不得出现任何内部块 id 字面量；需要块名时一律经 actionLabel() 翻成业务动作。
   * ==== */
  var VERDICT_HEAD = {
    pass: "PASS",
    fail: "未通过",
    blocked: "台架不具备",
    inconclusive: "说不清",
    "": "未运行"
  };
  var DEV_HINT = "详情见开发者详情";
  var BLOCK_TOKEN = /[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+/g;

  // 人读文案上限：一条原因一句人话（40 字），标题括号里更短（30 字）
  var REASON_MAX = 40;
  var TITLE_REASON_MAX = 30;
  var BENCH_REASON_MAX = 40;
  // 内部词/地址/十六进制一律不进默认区（WORKBENCH_BASE 之类只留在开发者详情）
  var INTERNAL_TOKEN = /`{0,2}WORKBENCH_BASE`{0,2}|\binjected_base_url\b|https?:\/\/[^\s，。；)）]+|\b0x[0-9a-f]+\b/gi;

  function hasInternalToken(value) {
    return /WORKBENCH_BASE|injected_base_url/i.test(String(value == null ? "" : value));
  }

  function scrubInternal(value) {
    return String(value == null ? "" : value)
      .replace(INTERNAL_TOKEN, " ")
      .replace(/\s{2,}/g, " ")
      .trim();
  }

  function clip(value, max) {
    var text = String(value == null ? "" : value).trim();
    var limit = max || REASON_MAX;
    if (text.length <= limit) return text;
    // 截断处别留半截标点（如「…不足（」→「…不足」）
    return text.slice(0, limit).replace(/[（(：:；;、,，\s]+$/, "") + "…";
  }

  // 块名/事实名 → 人话（认不出来给个中性说法，绝不回显 id）
  function factLabel(value) {
    return actionLabel(value) || "关键事实";
  }

  // 人话护栏：把混进文案的块 id / 内部标识换成业务动作，认不出来就丢掉——绝不把内部 id 显示给用户
  function sanitizeHuman(value) {
    var text = String(value == null ? "" : value);
    if (!text) return "";
    text = text.replace(BLOCK_TOKEN, function (token) { return actionLabel(token) || " "; });
    text = scrubInternal(text);
    return text
      .replace(/\s{2,}/g, " ")
      .replace(/^[\s；;，,、:：·\-]+/, "")
      .replace(/[\s；;，,、:：·\-]+$/, "")
      .trim();
  }

  // 「组网：明确不足」→「组网未完成」：只收敛少数已知后缀，不瞎改语义
  function tightenReason(value) {
    var match = String(value || "").match(/^(.+?)[：:](明确不足|条件不足|不满足|不足|未完成|失败|超时|异常)$/);
    if (match) return match[1] + "未完成";
    return String(value || "");
  }

  // 降级路径专用：只保留一句主体短语——截到第一个「——」/「（」/「；」之前，再限长；
  // 完整 detail 一律进「开发者详情」。
  function oneLineReason(value, max) {
    var text = sanitizeHuman(value);
    if (!text) return "";
    var cut = text.length;
    ["——", "（", "(", "；", ";"].forEach(function (sep) {
      var index = text.indexOf(sep);
      if (index > 0 && index < cut) cut = index;
    });
    text = tightenReason(text.slice(0, cut).trim());
    return clip(text.replace(/[\s：:、,，。.]+$/, ""), max || REASON_MAX);
  }

  // 台架侧人话：内部口径 → 一句人话；技术细节只进开发者详情
  function oneLineBenchReason(value) {
    var raw = String(value == null ? "" : value);
    if (hasInternalToken(raw)) return "台架正忙（上一轮运行未结束）";
    if (/https?:\/\//i.test(raw) && /地址|base/i.test(raw)) return "台架地址异常（见开发者详情）";
    return oneLineReason(raw, BENCH_REASON_MAX);
  }

  function toTextList(raw) {
    if (raw === undefined || raw === null) return [];
    var items = Array.isArray(raw) ? raw : String(raw).split(/[\n;；]+/);
    var out = [];
    items.forEach(function (item) {
      if (item === undefined || item === null) return;
      if (typeof item === "string") {
        if (item.trim()) out.push(item);
        return;
      }
      var entry = obj(item);
      var text = clean(entry.text) || clean(entry.reason) || clean(entry.message)
        || clean(entry.detail) || clean(entry.label);
      if (text) { out.push(text); return; }
      // 只给了块名：先收下，交给 sanitizeHuman 翻成业务动作
      var block = clean(entry.block) || clean(entry.block_id) || clean(entry.target);
      if (block) out.push(block);
    });
    return out;
  }

  var EVIDENCE_KIND = {
    run: "运行目录",
    run_dir: "运行目录",
    dir: "运行目录",
    workdir: "运行目录",
    result: "result.json",
    result_json: "result.json",
    log: "日志",
    logs: "日志",
    cli_log: "日志",
    evidence: "证据"
  };

  // 纯字符串证据没有 kind：按后缀猜一个人话标签（猜不出就当运行目录）
  function evidenceLabel(path) {
    var text = String(path == null ? "" : path).trim();
    if (!text) return "";
    if (/\.json$/i.test(text)) return "result.json";
    if (/\.(log|txt|out)$/i.test(text)) return "日志";
    return "运行目录";
  }

  function toEvidence(raw) {
    if (raw === undefined || raw === null) return [];
    if (typeof raw === "string") return toEvidence([raw]);
    var list = [];
    arr(raw).forEach(function (item) {
      if (item === undefined || item === null) return;
      if (typeof item === "string") {
        String(item).split(/[\n;；]+/).forEach(function (chunk) {
          var text = chunk.trim();
          if (text) list.push({ path: text, label: evidenceLabel(text) });
        });
        return;
      }
      var entry = obj(item);
      var path = clean(entry.path) || clean(entry.dir) || clean(entry.file) || clean(entry.value);
      if (!path) return;
      var kind = String(entry.kind || entry.type || "").toLowerCase();
      list.push({ path: path, label: clean(entry.label) || EVIDENCE_KIND[kind] || evidenceLabel(path) });
    });
    return list;
  }

  // 兼容两种下发形状：result.summary{headline,reason(s),evidence_paths} 或平铺 headline/reasons/evidence_paths
  // （summary 万一给成纯字符串，就当成 headline 用——后端字段形状未定型前不赌）
  function normSummary(result) {
    var summary = obj(result.summary);
    var headline = clean(summary.headline) || clean(summary.title) || clean(summary.conclusion)
      || clean(result.headline) || clean(result.summary);
    var reasonsRaw = summary.reasons !== undefined ? summary.reasons
      : (summary.reason !== undefined ? summary.reason
        : (result.reasons !== undefined ? result.reasons : result.reason));
    var evidenceRaw = summary.evidence_paths !== undefined ? summary.evidence_paths
      : (summary.evidence !== undefined ? summary.evidence
        : (result.evidence_paths !== undefined ? result.evidence_paths : result.evidence));
    return { headline: headline, reasons: toTextList(reasonsRaw), evidence: toEvidence(evidenceRaw) };
  }

  function blockReason(result, blockId) {
    if (!clean(blockId)) return "";
    var detail = sanitizeHuman(blockDetail(result, blockId));
    if (detail) return detail;
    return actionLabel(blockId);
  }

  function problemRowReason(result) {
    var rows = arr(result.blocks || result.steps || []);
    for (var i = 0; i < rows.length; i += 1) {
      var row = obj(rows[i]);
      if (stepState(row) === "pass") continue;
      var text = sanitizeHuman(row.detail || row.message || row.reason || "");
      if (text) return text;
      var label = actionLabel(row.block_id || row.block || row.id || "");
      if (label) return label + "未通过";
    }
    return "";
  }

  // 未满足事实：优先用 detail（后端给的人话）；只有事实 id 时翻成「<业务动作>未满足」
  function unmetFactReasons(result) {
    var raw = result.unmet_facts;
    var entries = [];
    if (Array.isArray(raw)) {
      entries = raw.map(function (item) {
        if (typeof item === "string") return { id: item, state: "", detail: "" };
        var fact = obj(item);
        return {
          id: String(fact.id || fact.fact_id || ""),
          state: fact.state === undefined ? "" : normFactState(fact.state),
          detail: String(fact.detail || "")
        };
      });
    } else {
      var map = obj(raw);
      Object.keys(map).forEach(function (key) {
        var fact = obj(map[key]);
        return entries.push({
          id: key,
          state: fact.state === undefined ? (map[key] === false ? "no" : "") : normFactState(fact.state),
          detail: String(fact.detail || "")
        });
      });
    }
    return entries.map(function (fact) {
      var detail = sanitizeHuman(fact.detail);
      if (detail) return detail;
      var label = actionLabel(fact.id);
      if (!label) return "";
      return label + (fact.state === "yes" ? "已满足" : "未满足");
    }).filter(Boolean);
  }

  // 无 summary 时的保守降级：verdict + 断点字段拼人话，绝不白屏、绝不显示块 id
  function derivedReasons(result, verdict) {
    var list = [];
    if (verdict === "fail") {
      list.push(blockReason(result, result.failed_block) || problemRowReason(result));
    } else if (verdict === "blocked") {
      list.push(clean(result.skipped_reason));
      list.push(blockReason(result, result.blocked_block) || problemRowReason(result));
    } else if (verdict === "inconclusive") {
      list.push(blockReason(result, result.failed_block) || problemRowReason(result) || "抄读结果不确定");
    }
    list = list.concat(unmetFactReasons(result));
    if (verdict === "fail" && !list.filter(Boolean).length && result.error) list.push(String(result.error));
    return list.filter(Boolean);
  }

  function uniqueReasons(items) {
    var seen = {};
    var out = [];
    items.forEach(function (item) {
      var text = String(item == null ? "" : item).trim();
      if (!text || seen[text]) return;
      seen[text] = true;
      out.push(text);
    });
    return out;
  }

  // 原因行：API 的 summary.reasons 优先（后端已做人话映射，只做 id 护栏 + 限长）；
  // 没有 summary 时走降级——每条原因只保留一句人话，最多 3 条，其余归开发者详情。
  function reasonList(result, verdict, summary) {
    if (summary.reasons.length) {
      return uniqueReasons(summary.reasons.map(function (item) {
        return clip(sanitizeHuman(item), REASON_MAX);
      })).slice(0, 3);
    }
    return uniqueReasons(derivedReasons(result, verdict).map(function (item) {
      return oneLineReason(item);
    })).filter(Boolean).slice(0, 3);
  }

  var GENERIC_HEAD = ["通过", "未通过", "失败", "台架不具备", "说不清",
    "PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"];

  // 标题：默认只给「用例名（结论）」——PASS / 未通过：一句原因 / 台架不具备：原因 / 说不清：原因
  // 括号内原因总长限 30 字，超了加省略号——标题永远是一行。
  function reportHeadline(result, verdict, summary, reasons) {
    var title = clean(result.title) || clean(result.case_id) || "未命名用例";
    var shortReasons = clip(reasons.join("；"), TITLE_REASON_MAX);
    var head = sanitizeHuman(summary.headline);
    if (head) {
      // 后端只给了「未通过」这类空结论时，把原因补进标题（仍是人话，不含块名）
      if (reasons.length && GENERIC_HEAD.indexOf(head) >= 0 && head.indexOf("：") < 0) {
        head += "：" + shortReasons;
      }
      // 用例声明的 title 常带括号说明（「台架就绪自检（STA 控制台口 + …）」），
      // 与后端 headline（「台架就绪自检（PASS）」）按**全称**比较会重复，故按短标题比。
      var shortTitle = clip(String(title).split(/[（(：:]/)[0].trim() || title, 20);
      if (head.indexOf(shortTitle) === 0) return clip(head, shortTitle.length + TITLE_REASON_MAX + 4);
      return shortTitle + "（" + clip(head, TITLE_REASON_MAX) + "）";
    }
    var prefix = VERDICT_HEAD[verdict] || VERDICT_HEAD[""];
    if (!verdict || verdict === "pass") return title + "（" + prefix + "）";
    return title + "（" + prefix +
      (shortReasons ? "：" + shortReasons : "，" + DEV_HINT) + "）";
  }

  // 证据路径：summary 没给就退到 result/job 上的落盘字段；一条都没有也不留空白行
  function fallbackEvidence(result, job) {
    var owner = obj(job);
    var list = [];
    function push(path, label) {
      var text = clean(path);
      if (!text) return;
      for (var i = 0; i < list.length; i += 1) {
        if (list[i].path === text) return;
      }
      list.push({ path: text, label: label });
    }
    push(result.run_dir || owner.run_dir, "运行目录");
    push(result.result_path || owner.result_path, "result.json");
    push(result.log_path || result.cli_log || result.log_file || owner.log_path, "日志");
    push(result.evidence_dir, "证据");
    return list;
  }

  function reportEvidenceHtml(result, summary, job) {
    var items = summary.evidence.slice();
    if (!items.length) items = fallbackEvidence(result, job);
    var html = '<div class="rc-evidence" data-hook="report-evidence"><span class="rc-elabel">证据</span>';
    if (!items.length) {
      return html + '<span class="rc-empty">证据路径未提供（见任务日志）</span></div>';
    }
    html += items.map(function (item) {
      return '<span class="rc-ev">' +
        (item.label ? '<span class="rc-ekind">' + esc(item.label) + "</span>" : "") +
        '<code class="rc-path mono" data-hook="evidence-path" data-path="' + esc(item.path) +
        '" title="点击复制路径">' + esc(item.path) + "</code>" +
        '<button class="mini rc-copy" data-path="' + esc(item.path) + '" title="复制路径">复制</button>' +
        "</span>";
    }).join("");
    return html + "</div>";
  }

  // 每条用例一张报告卡：标题行 + 原因行 + 证据行；明细一律收进「开发者详情」
  function renderCaseResult(raw, job) {
    var result = obj(raw);
    var verdict = normVerdict(result.verdict);
    var summary = normSummary(result);
    var reasons = reasonList(result, verdict, summary);
    var headline = reportHeadline(result, verdict, summary, reasons);
    var html = '<article class="case-result report is-' + (verdict || "unknown") + '"' +
      ' data-hook="case-report" data-verdict="' + esc(verdict) + '"' +
      ' data-case-id="' + esc(result.case_id || "") + '">';
    html += '<div class="rc-head" data-hook="report-headline">' + badge(verdict) +
      '<b class="rc-title">' + esc(headline) + "</b>" +
      (result.case_id ? '<span class="rc-id mono">' + esc(result.case_id) + "</span>" : "") +
      "</div>";
    if (reasons.length) {
      html += '<div class="rc-reasons" data-hook="report-reasons">' + esc(reasons.join("；")) + "</div>";
    }
    html += reportEvidenceHtml(result, summary, job);
    html += devDetailsHtml(result);
    html += "</article>";
    return html;
  }

  /* ==== 报告卡默认区 END ==== */

  /* ==== 结果视图（REQS-0044 变更 3 报表契约：run.report.rows[]/summary）====
   * 数据源（全部真接口，零 mock）：GET /api/cases/runs/{run_id}（历史结果读模型）与
   * job 轮询终态的 RunRecordView.report。列固定：测试大类 | 测试子类 | 用例名称 |
   * 测试步骤 | 结果 | 失败原因/说明；null → 「—」；四态 ✔绿 / ✘红 / ⊘灰 / ？黄；
   * 证据路径在原因列可展开；块级明细（含 observation 观测）只进「开发者详情」折叠
   * （用户层零块名，REQS-0042 D11-2）。 ==== */
  var VD_GLYPH = { pass: "✔", fail: "✘", blocked: "⊘", inconclusive: "？", "": "？" };
  var VD_SHORT = { pass: "通过", fail: "失败", blocked: "阻断", inconclusive: "说不清", "": "未知" };

  function vdChip(value) {
    var key = normVerdict(value) || "";
    var meta = VERDICT[key] || VERDICT[""];
    var name = VD_SHORT[key] || "未知";
    return '<span class="verdict ' + meta.cls + '" data-verdict="' + esc(key) +
      '" title="' + esc(name) + '">' + (VD_GLYPH[key] || "？") + " " + esc(name) + "</span>";
  }

  function showResultView() {
    var result = $("view-result");
    var list = $("view-list");
    if (result) result.hidden = false;
    if (list) list.hidden = true;
    if (window.scrollTo) window.scrollTo(0, 0);
  }

  function showListView() {
    var result = $("view-result");
    var list = $("view-list");
    if (result) result.hidden = true;
    if (list) list.hidden = false;
  }

  // 证据路径折叠（原因列内）：展开后点击路径即复制
  function resultEvidenceHtml(paths) {
    var items = arr(paths).map(String).filter(function (p) { return !!clean(p); });
    if (!items.length) return "";
    return '<div><button class="mini ev-toggle" type="button" title="证据路径（点击展开/收起）：' +
      esc(items.join("  ")) + '">证据 ▸</button></div>' +
      '<div class="ev-paths" hidden>' + items.map(function (p) {
        return '<code class="ev-path mono" data-path="' + esc(p) + '" title="点击复制路径">' + esc(p) + "</code>";
      }).join("") + "</div>";
  }

  // 汇总条：产品通过率（分母不含 setup 行，直接用 summary.product_pass_rate）+ 四态 + 台架准备 n 项
  function renderRunSumbar(rows, summary) {
    var sum = obj(summary);
    var setupN = rows.filter(function (r) { return obj(r).kind === "setup"; }).length;
    function chip(cls, glyph, label, value) {
      return '<span class="sum-chip ' + cls + '">' + glyph + " " + label + " " + num(value) + "</span>";
    }
    var bar = $("sumbar");
    if (!bar) return;
    bar.innerHTML =
      '<div class="sum-rate"><b>' + esc(clean(sum.product_pass_rate) || "—") + "</b><span>产品通过率</span></div>" +
      chip("pass", "✔", "通过", sum.pass) +
      chip("fail", "✘", "失败", sum.fail) +
      chip("blocked", "⊘", "阻断", sum.blocked) +
      chip("inconclusive", "？", "说不清", sum.inconclusive) +
      '<span class="sum-setup">台架准备 ' + setupN + " 项（不计入分母）</span>";
  }

  // Excel 式结果表：六列固定，null → 「—」，行按四态着色
  function renderRunTable(rows) {
    var tbody = $("res-tbody");
    if (!tbody) return;
    var dash = '<span class="muted-dash">—</span>';
    var cell = function (value) {
      return (value === null || value === undefined || String(value) === "") ? dash : esc(String(value));
    };
    tbody.innerHTML = rows.map(function (raw) {
      var r = obj(raw);
      var kind = String(r.kind || "");
      var reasonHtml = clean(r.reason) ? '<span class="reason">' + esc(r.reason) + "</span>" : dash;
      reasonHtml += resultEvidenceHtml(r.evidence_paths);
      return '<tr class="r-' + esc(normVerdict(r.verdict) || "inconclusive") + (kind === "setup" ? " r-setup" : "") + '">' +
        "<td>" + cell(r.group) + "</td>" +
        "<td>" + cell(r.subgroup) + "</td>" +
        '<td class="c-name">' + cell(r.name) + "</td>" +
        '<td class="c-steps">' + cell(r.steps_text) + "</td>" +
        "<td>" + vdChip(r.verdict) + "</td>" +
        '<td class="c-reason">' + reasonHtml + "</td>" +
        "</tr>";
    }).join("");
    Array.prototype.forEach.call(tbody.querySelectorAll(".ev-toggle"), function (button) {
      button.onclick = function () {
        var box = button.parentNode.nextElementSibling;
        if (!box) return;
        var open = !!box.hidden;
        box.hidden = !open;
        button.textContent = open ? "证据 ▾" : "证据 ▸";
      };
    });
    Array.prototype.forEach.call(tbody.querySelectorAll(".ev-path"), function (code) {
      code.onclick = function () { copyText(code.getAttribute("data-path") || code.textContent); };
    });
  }

  // 开发者详情：块级观测明细（块 id / 块态 / 结论 / observation）只出现在这里
  function renderRunDev(run) {
    var body = $("dev-body");
    if (!body) return;
    var results = arr(run.results);
    if (!results.length) {
      body.innerHTML = '<p class="dev-note">该运行没有块级明细数据（run.results 为空）。</p>';
      return;
    }
    body.innerHTML =
      '<p class="dev-note">按 REQS-0042 D11-2：内部块 id 禁止出现在用户层文本，仅本折叠区与 --json 可见。' +
      "块只观测不判定（REQS-0044 §4.2）：判据归步骤期望与用例聚合。</p>" +
      results.map(function (raw) {
        var res = obj(raw);
        var blocks = arr(res.blocks).map(function (rawBlock) {
          var b = obj(rawBlock);
          var obs = obj(b.observation);
          var values = obj(obs.values);
          var valueText = Object.keys(values).map(function (key) {
            return key + "=" + String(values[key]);
          }).join("；");
          var obsText = clean(obs.detail) || clean(b.detail) || clean(b.message) || "";
          var obsHtml = esc(obsText) +
            (valueText ? '<div class="bk-obs">' + esc(valueText) +
              (obs.complete === false ? "（观测不完整）" : "") + "</div>" : "");
          return "<tr><td class='bkid'>" + esc(String(b.block_id || "")) + "</td><td>" +
            esc(String(b.state || "")) + "</td><td>" + esc(String(b.outcome || "")) + "</td><td>" +
            obsHtml + "</td></tr>";
        }).join("");
        var unmet = Object.keys(obj(res.unmet_facts)).map(function (key) {
          var fact = obj(obj(res.unmet_facts)[key]);
          return '<div class="bp-item bp-fact"><span class="k">未满足事实</span><code>' + esc(key) +
            "</code><span>" + esc(String(fact.state || "")) +
            (clean(fact.detail) ? " — " + esc(fact.detail) : "") + "</span></div>";
        }).join("");
        var notes = [];
        var summary = obj(res.summary);
        if (clean(summary.reason)) notes.push(String(summary.reason));
        arr(summary.reasons).forEach(function (item) { if (clean(item)) notes.push(String(item)); });
        if (clean(res.skipped_reason)) notes.push(String(res.skipped_reason));
        if (clean(res.error)) notes.push(String(res.error));
        return '<div class="dev-row"><b>' + esc(clean(res.title) || clean(res.case_id) || "未命名用例") + "</b> " +
          vdChip(res.verdict) +
          (notes.length ? '<div class="dev-note">' + esc(notes.join("；")) + "</div>" : "") +
          unmet +
          (blocks ? '<table class="bk"><thead><tr><th>块 id</th><th>块态</th><th>结论</th>' +
            "<th>观测（observation）</th></tr></thead><tbody>" + blocks + "</tbody></table>" : "") +
          "</div>";
      }).join("");
  }

  // 运行记录 → 结果视图（历史读回与 job 终态共用同一条路径）
  function renderRunReport(record) {
    var run = obj(record);
    S.currentRun = run;
    var report = obj(run.report);
    var rows = arr(report.rows);
    var title = $("res-title");
    if (title) title.textContent = "运行结果 " + String(run.run_id || "");
    var dir = $("res-dir");
    if (dir) {
      dir.textContent = clean(run.result_path) || clean(run.run_dir) ||
        ("case_runs/" + String(run.run_id || "") + "/");
      dir.title = "证据落盘位置";
    }
    var meta = $("res-meta");
    if (meta) {
      var seconds = num(run.duration_seconds) || num(run.duration_s);
      var setupN = rows.filter(function (r) { return obj(r).kind === "setup"; }).length;
      meta.textContent = (seconds ? "用时 " + seconds + "s · " : "") +
        (rows.length ? rows.length + " 行，其中台架准备 " + setupN + " 行不计入通过率" : "");
    }
    var empty = $("res-empty");
    var table = $("res-table");
    if (!rows.length) {
      if (table) table.hidden = true;
      if (empty) {
        empty.hidden = false;
        empty.textContent = "该运行没有报表数据；块级明细见下方开发者详情。";
      }
      renderRunSumbar([], report.summary);
    } else {
      if (table) table.hidden = false;
      if (empty) empty.hidden = true;
      renderRunSumbar(rows, report.summary);
      renderRunTable(rows);
    }
    renderRunDev(run);
    var details = $("dev-details-run");
    if (details) details.open = false;
    showResultView();
  }

  // 历史结果：GET /api/cases/runs/{run_id}（真接口；404/缺契约时显式降级，不白屏）
  function loadRun(runId) {
    var id = clean(runId);
    if (!id) { toast("请先输入 run_id"); return Promise.resolve(false); }
    return api("GET", "/api/cases/runs/" + encodeURIComponent(id)).then(function (record) {
      S.lastRunId = clean(obj(record).run_id) || id;
      persist();
      var input = $("run-id-input");
      if (input) input.value = S.lastRunId;
      renderRunReport(record);
      renderRunsSelect();   // 历史运行下拉同步回显当前 run_id
      toast("已载入历史结果 " + S.lastRunId);
      return true;
    }).catch(function (error) {
      toast("读取运行结果失败：" + (error && error.message ? error.message : String(error)));
      return false;
    });
  }
  // 供控制台 / Playwright 驱动（同 Demo 的 __demoRenderResult 口径，但只吃真实 run_id）
  window.__casesLoadRun = function (runId) { return loadRun(runId); };
  window.__casesShowList = function () { showListView(); };

  // ---------- 历史运行索引（GET /api/cases/runs?limit=10，REQS-0044 变更 4） ----------
  // 清单页一个小下拉：选中某轮即复用上面的 loadRun 载入渲染（同一条 run_id 载入路径）；
  // 后端未挂该端点（404）时下拉只留占位项，不影响页面其余功能。
  var RUNS_LIST_LIMIT = 10;

  function loadRunsIndex() {
    return api("GET", "/api/cases/runs?limit=" + RUNS_LIST_LIMIT).then(function (data) {
      S.runsIndex = arr(obj(data).runs);
    }).catch(function () {
      S.runsIndex = [];   // 端点缺失/失败：下拉置空但不白屏
    }).then(function () {
      renderRunsSelect();
    });
  }

  function runIndexLabel(run) {
    var verdict = normVerdict(run.verdict);
    var parts = [
      clean(run.run_id) || "?",
      VD_SHORT[verdict] || "未知",
      clean(run.finished_at).replace("T", " ")
    ];
    var names = arr(run.case_names);
    if (names.length) parts.push(names.length + " 例");
    var headline = clean(obj(run.summary).headline);
    if (headline) parts.push(clip(headline, 30));
    return parts.filter(Boolean).join(" · ");
  }

  function renderRunsSelect() {
    var select = $("runs-select");
    if (!select) return;
    var html = '<option value="">历史运行（最近 ' + S.runsIndex.length + ' 轮）…</option>';
    S.runsIndex.forEach(function (run) {
      html += '<option value="' + esc(clean(run.run_id)) + '">' + esc(runIndexLabel(run)) + "</option>";
    });
    select.innerHTML = html;
    // 当前结果视图里的 run_id 若在索引里则回显选中；不在就保持占位项
    select.value = S.runsIndex.some(function (run) { return clean(run.run_id) === S.lastRunId; })
      ? S.lastRunId : "";
  }

  // ---------- 默认参数（REQS-0044 工位 E）----------
  // GET /api/cases/defaults 下发白名单键 + 业务文案（"默认表地址" 等），表单只渲染下发的
  // items——页面**不造键**（用户提到但配置里没有对应键的项一律不出现）；保存走 PUT，
  // 后端做白名单校验 + 写前备份。404（后端未挂）→ 隐藏入口，不影响页面其余功能。
  function loadDefaults() {
    return api("GET", "/api/cases/defaults").then(function (data) {
      S.defaults = arr(obj(data).items);
    }).catch(function () {
      S.defaults = null;   // 端点缺失/失败：隐藏入口（不白屏）
    }).then(function () {
      renderDefaults();
    });
  }

  function renderDefaults() {
    var line = $("defaults-line");
    if (!line) return;
    var textEl = $("defaults-text");
    if (!S.defaults || !S.defaults.length) {
      line.hidden = true;   // 后端未提供或配置为空：不显示入口
      if (textEl) textEl.textContent = "";
      return;
    }
    line.hidden = false;
    if (textEl) {
      // 一行显示当前默认：第一项（表地址类）+ 其余项数；全部细节在「修改」小表单里
      var first = obj(S.defaults[0]);
      var value = String(first.value == null ? "" : first.value);
      var text = "默认参数：" + clean(first.label) + " " + value;
      if (S.defaults.length > 1) text += " 等 " + S.defaults.length + " 项";
      textEl.textContent = text;
    }
  }

  function openDefaultsModal() {
    if (!S.defaults || !S.defaults.length) {
      toast("默认参数不可用（后端未提供 GET /api/cases/defaults 或配置为空）");
      return;
    }
    var form = $("defaults-form");
    if (form) {
      form.innerHTML = S.defaults.map(function (item) {
        return '<div class="row"><label>' + esc(clean(item.label)) + '</label>' +
          '<input type="text" data-key="' + esc(item.key) + '" value="' +
          esc(String(item.value == null ? "" : item.value)) + '" title="' + esc(item.key) + '"></div>';
      }).join("");
    }
    var modal = $("defaults-modal");
    if (modal) modal.classList.add("show");
  }

  function closeDefaultsModal() {
    var modal = $("defaults-modal");
    if (modal) modal.classList.remove("show");
  }

  function saveDefaults() {
    var form = $("defaults-form");
    if (!form) return;
    var values = {};
    Array.prototype.forEach.call(form.querySelectorAll("input[data-key]"), function (input) {
      var key = input.getAttribute("data-key") || "";
      if (key) values[key] = String(input.value || "").trim();
    });
    api("PUT", "/api/cases/defaults", { values: values }).then(function (data) {
      S.defaults = arr(obj(data).items);
      renderDefaults();   // 保存成功后刷新页面默认值显示（工位 E 要求）
      closeDefaultsModal();
      toast("默认参数已保存" + (clean(obj(data).backup_path) ? "（原值已备份）" : ""));
    }).catch(function (error) {
      toast("保存失败：" + (error && error.message ? error.message : String(error)));
    });
  }

  // 自检（供控制台 / Playwright 调用）：行数一致、用户层零块名、四态类齐备、离线零外链
  window.__casesSelfcheck = function () {
    var checks = [];
    function add(name, ok, detail) { checks.push({ name: name, ok: !!ok, detail: String(detail) }); }

    // 1. 结果行数与 API rows 数一致
    var rows = arr(obj(obj(S.currentRun).report).rows);
    var tbody = document.getElementById("res-tbody");
    var rendered = tbody ? tbody.querySelectorAll("tr").length : 0;
    add("result-rows-match-api", rows.length > 0 && rendered === rows.length,
      "API rows=" + rows.length + "，DOM tr=" + rendered);

    // 2. 用户层零块名：非折叠区（剔除 script/style 与所有开发者详情折叠）逐文本节点扫描。
    //    折叠区（两个大折叠 + 每张报告卡里默认 hidden 的 dev-details）按 REQS-0042 D11-2
    //    本就允许出现块 id，整棵子树跳过（工位 E：补上报告卡内的折叠容器）。
    var hits = [];
    var LEAK_SKIP = { "dev-details-run": true, "bench-dev-details": true };
    function isSkipEl(el) {
      if (!el || el.nodeType !== 1) return false;
      var tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE") return true;
      if (LEAK_SKIP[el.id]) return true;
      var hook = el.getAttribute && el.getAttribute("data-hook");
      // case-steps（清单展开区，默认 hidden）同属折叠区：步骤 do/expect 原文里引用的
      // 块名按 D11-2 口径允许（2026-11-25 用户裁决：具体步骤预期收进展开详情）
      return hook === "bench-dev-details" || hook === "dev-details" || hook === "case-steps";
    }
    function walkText(el) {
      if (isSkipEl(el)) return;   // 折叠区（块 id / 事实 id 允许出现）整棵子树跳过
      for (var child = el.firstChild; child; child = child.nextSibling) {
        if (child.nodeType === 3) {
          var found = (child.nodeValue || "").match(/[a-z_]+\.[a-z_0-9]+/gi) || [];
          found.forEach(function (token) {
            if (/\.(json|log|txt)$/i.test(token)) return;   // 证据文件名（result.json / cli.log）不是块 id
            if (hits.indexOf(token) < 0) hits.push(token);
          });
        } else if (child.nodeType === 1) {
          walkText(child);
        }
      }
    }
    walkText(document.body);
    add("no-block-leak（用户层零块名，REQS-0042 D11-2）", hits.length === 0,
      hits.length ? "泄漏：" + hits.join(", ") : "非折叠区无 xxx.yyy 块 id（证据文件名在白名单）");

    // 3. 四态着色：数据里出现过的每个 verdict 都有对应徽标类
    var verdicts = {};
    rows.forEach(function (raw) {
      var key = normVerdict(obj(raw).verdict);
      if (key) verdicts[key] = true;
    });
    var missing = Object.keys(verdicts).filter(function (key) {
      return !tbody || !tbody.querySelector('span.verdict[data-verdict="' + key + '"]');
    });
    add("four-state-classes（✔✘⊘？ 四态徽标齐备）", missing.length === 0,
      missing.length ? "缺类：" + missing.join(",") : "数据出现的四态徽标均已着色渲染");

    // 4. 离线：零外链依赖
    var ext = document.documentElement.outerHTML.match(/(?:src|href)\s*=\s*["'](?:https?:)?\/\//g) || [];
    add("offline（零外部依赖）", ext.length === 0,
      ext.length ? "外部引用：" + ext.join(", ") : "无 http(s) 外链");

    return { ok: checks.every(function (c) { return c.ok; }), checks: checks };
  };


  // 证据路径：点击路径或「复制」按钮即复制（剪贴板不可用时提示手动选中）
  function copyText(value) {
    var text = String(value == null ? "" : value);
    if (!text) return;
    var nav = typeof navigator !== "undefined" ? navigator : null;
    if (nav && nav.clipboard && typeof nav.clipboard.writeText === "function") {
      nav.clipboard.writeText(text).then(function () {
        toast("已复制：" + text);
      }, function () {
        toast("复制失败，请手动选中路径");
      });
      return;
    }
    try {
      if (typeof document.createElement === "function" && document.body) {
        var area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
        toast("已复制：" + text);
        return;
      }
    } catch (error) { /* 落到下面的手动提示 */ }
    toast("请手动选中路径复制");
  }

  function bindReportActions(wrap) {
    Array.prototype.forEach.call(wrap.querySelectorAll(".rc-dev-toggle"), function (button) {
      button.onclick = function () {
        var head = button.parentNode;
        var panel = head && head.nextElementSibling;
        if (!panel) return;
        var open = !!panel.hidden;
        panel.hidden = !open;
        button.textContent = open ? "开发者详情 ▾" : "开发者详情 ▸";
        button.setAttribute("aria-expanded", open ? "true" : "false");
      };
    });
    Array.prototype.forEach.call(wrap.querySelectorAll(".rc-path"), function (node) {
      node.onclick = function () {
        copyText(node.getAttribute("data-path") || node.textContent);
      };
    });
    Array.prototype.forEach.call(wrap.querySelectorAll(".rc-copy"), function (node) {
      node.onclick = function () {
        copyText(node.getAttribute("data-path") || "");
      };
    });
  }

  // ---------- 存方案对话框（UI-3） ----------
  function openSuiteModal() {
    var ids = selectedIds();
    if (!ids.length) { toast("先勾选用例再存方案"); return; }
    var suite = S.suiteId ? suiteOf(S.suiteId) : null;
    if ($("suite-id")) $("suite-id").value = suite ? suite.id : "";
    if ($("suite-name")) $("suite-name").value = suite ? suite.name : "";
    if ($("suite-notes")) $("suite-notes").value = suite ? suite.notes : "";
    if ($("sw-stop")) $("sw-stop").checked = suite ? suite.stop_on_fail : true;
    if ($("suite-cover")) {
      $("suite-cover").textContent = "方案用例：" + ids.length + " 条（" + ids.join(" / ") + "）";
    }
    var modal = $("suite-modal");
    if (modal) modal.classList.add("show");
  }
  function closeSuiteModal() {
    var modal = $("suite-modal");
    if (modal) modal.classList.remove("show");
  }
  function saveSuite() {
    var ids = selectedIds();
    if (!ids.length) { toast("先勾选用例再存方案"); return; }
    var id = ($("suite-id") ? $("suite-id").value : "").trim();
    var name = ($("suite-name") ? $("suite-name").value : "").trim();
    if (!id) { toast("方案 ID 不能为空"); return; }
    var payload = {
      id: id,
      name: name || id,
      case_ids: ids,
      stop_on_fail: $("sw-stop") ? $("sw-stop").checked : true,
      notes: ($("suite-notes") ? $("suite-notes").value : "").trim()
    };
    api("POST", "/api/cases/suites", payload).then(function () {
      S.suiteId = id;
      persist();
      closeSuiteModal();
      toast("已保存方案 " + id + "（" + ids.length + " 例）");
      return loadSuites().then(function () { renderSuites(); renderRunButtons(); });
    }).catch(function (error) {
      toast("保存失败：" + error.message);
    });
  }
  function deleteSuite() {
    if (!S.suiteId) return;
    var id = S.suiteId;
    if (!window.confirm("删除方案 " + id + "？（只删方案，不删用例勾选）")) return;
    api("DELETE", "/api/cases/suites/" + encodeURIComponent(id)).then(function () {
      S.suiteId = "";
      persist();
      toast("已删除方案 " + id);
      return loadSuites().then(function () { renderSuites(); renderRunButtons(); });
    }).catch(function (error) {
      toast("删除失败：" + error.message);
    });
  }

  // ---------- 总渲染 ----------
  function renderAll() {
    renderApiPill();
    renderBench();
    renderFilters();
    renderRail();
    renderList();
    renderSuites();
    renderRunButtons();
  }

  // ---------- 事件 ----------
  function bind() {
    if ($("q")) {
      $("q").oninput = function () {
        S.filters.q = this.value;
        renderRail();
        renderList();
      };
    }
    ["usage", "domain", "scene", "region"].forEach(function (dim) {
      var select = $("f-" + dim);
      if (select) {
        select.onchange = function () {
          S.filters[dim] = this.value;
          renderRail();
          renderList();
        };
      }
    });
    if ($("f-protocol")) {
      $("f-protocol").onchange = function () { S.filters.protocol = this.value; renderList(); };
    }
    if ($("f-sort")) {
      $("f-sort").onchange = function () { S.sort = this.value; persist(); renderList(); };
    }
    if ($("group-by")) {
      $("group-by").onchange = function () {
        S.groupBy = this.value;
        S.filters[S.groupBy] = "";
        persist();
        renderRail();
        renderList();
      };
    }
    if ($("suite-select")) {
      $("suite-select").onchange = function () {
        var suite = this.value ? suiteOf(this.value) : null;
        S.suiteId = suite ? suite.id : "";
        if (suite) {
          var known = {};
          S.cases.forEach(function (item) { known[item.id] = true; });
          S.sel = {};
          var skipped = 0;
          suite.case_ids.forEach(function (id) {
            if (known[id]) S.sel[id] = true; else skipped += 1;
          });
          if (skipped) toast("方案内 " + skipped + " 条用例已不在用例库，已跳过");
        }
        persist();
        renderList();
        renderSuites();
        renderRunButtons();
      };
    }
    if ($("bench-dev-toggle")) {
      $("bench-dev-toggle").onclick = function () {
        var panel = $("bench-dev-details");
        if (!panel) return;
        var open = !!panel.hidden;
        panel.hidden = !open;
        this.textContent = open ? "开发者详情 ▾" : "开发者详情 ▸";
        this.setAttribute("aria-expanded", open ? "true" : "false");
      };
    }
    // 横幅「详情」折叠（工位 E）：手动展开/收起口状态与事实芯片；点过之后以用户为准
    if ($("bench-detail-toggle")) {
      $("bench-detail-toggle").onclick = function () {
        var box = $("bench-details");
        var open = !!(box && box.hidden);   // 当前收起 → 这次点开
        S.benchDetailsOpen = open;
        setBenchDetails(open);
      };
    }
    // job 单行（工位 E）：终态收起后点击该行才展开完整结果；运行中不响应
    if ($("job-collapsed")) {
      $("job-collapsed").onclick = function () {
        if (!S.jobTerminal) return;
        setJobExpanded(!S.jobExpanded);
      };
      $("job-collapsed").onkeydown = function (event) {
        if (!S.jobTerminal) return;
        if (event && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          setJobExpanded(!S.jobExpanded);
        }
      };
    }
    // 默认参数入口（工位 E）：修改 / 取消 / 保存
    if ($("btn-defaults-edit")) $("btn-defaults-edit").onclick = openDefaultsModal;
    if ($("defaults-cancel")) $("defaults-cancel").onclick = closeDefaultsModal;
    if ($("defaults-save")) $("defaults-save").onclick = saveDefaults;
    if ($("defaults-modal")) {
      $("defaults-modal").onclick = function (event) {
        if (event.target === $("defaults-modal")) closeDefaultsModal();
      };
    }
    if ($("btn-refresh")) $("btn-refresh").onclick = function () { loadAll().then(function () { toast("已刷新"); }); };
    if ($("btn-clear-filter")) {
      $("btn-clear-filter").onclick = function () {
        S.filters = { q: "", usage: "", domain: "", scene: "", region: "", protocol: "" };
        if ($("q")) $("q").value = "";
        renderFilters();
        renderRail();
        renderList();
        toast("已清除全部筛选");
      };
    }
    // ---------- 结果视图（REQS-0044）：返回 / 载入历史 run / 再跑一次 ----------
    if ($("btn-back-list")) $("btn-back-list").onclick = function () { showListView(); };
    if ($("runs-select")) {
      $("runs-select").onchange = function () {
        if (this.value) loadRun(this.value);   // 选中即载入（复用 run_id 载入路径）
      };
    }
    if ($("btn-load-run")) {
      $("btn-load-run").onclick = function () {
        loadRun($("run-id-input") ? $("run-id-input").value : "");
      };
    }
    if ($("run-id-input")) {
      $("run-id-input").onkeydown = function (event) {
        if (event && event.key === "Enter") loadRun(this.value);
      };
    }
    if ($("btn-rerun")) {
      $("btn-rerun").onclick = function () {
        var ids = arr(obj(S.currentRun).case_ids);
        if (!ids.length) { toast("该运行没有可重跑的用例清单"); return; }
        runJob({ case_ids: ids }, "再跑一次");
      };
    }
    if ($("btn-clear-sel")) {
      $("btn-clear-sel").onclick = function () {
        S.sel = {};
        persist();
        renderList();
      };
    }
    if ($("btn-run-now")) {
      $("btn-run-now").onclick = function () {
        var ids = selectedIds();
        if (ids.length) runJob({ case_ids: ids }, "勾选 " + ids.length + " 例");
      };
    }
    if ($("btn-run-suite")) {
      $("btn-run-suite").onclick = function () {
        if (S.suiteId) runJob({ suite_id: S.suiteId }, "方案 " + S.suiteId);
      };
    }
    if ($("btn-save-suite")) $("btn-save-suite").onclick = openSuiteModal;
    if ($("btn-del-suite")) $("btn-del-suite").onclick = deleteSuite;
    if ($("suite-cancel")) $("suite-cancel").onclick = closeSuiteModal;
    if ($("suite-ok")) $("suite-ok").onclick = saveSuite;
    if ($("suite-modal")) {
      $("suite-modal").onclick = function (event) {
        if (event.target === $("suite-modal")) closeSuiteModal();
      };
    }
  }

  // ---------- 启动 ----------
  bind();
  loadAll();
  // 横幅自动刷新（横幅慢修 2026-11-25）：每 30s 重取 /api/cases/bench 更新横幅——
  // 后台刷新写完缓存后，下一轮轮询自动把真结论补上（stale-while-revalidate）。
  // 页面隐藏（document.hidden）时跳过本轮，恢复可见后从下一轮继续（省后端探测压力）。
  setInterval(function () {
    if (document.hidden) return;
    loadBench().then(function () { renderRunButtons(); });
  }, 30000);
})();
