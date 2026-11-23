/* 用例库页面逻辑（REQS-0042 §3.7 UI-1..7）。
 *
 * 数据源：/api/cases/*（apps/workbench/cases_api.py）。后端可能尚未挂载（404）——
 * 本页对所有端点做「字段缺失容忍 + 404 降级」：能读到什么就画什么，
 * 读不到就显式说「后端未就绪」，而不是白屏。
 *
 * 四态结论（UI-6）：pass 绿 / fail 红 / blocked 灰（台架问题，不是产品问题）/ inconclusive 黄。
 * 配色一律走 tokens-v2 的 var(--color-*)，页面零裸色值。
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
      steps: steps,
      step_count: num(item.step_count) || steps.length,
      block_count: num(item.block_count) || arr(item.blocks).length,
      blocks: arr(item.blocks).map(String),
      needs: arr(item.needs).map(String),
      tags: arr(item.tags).map(String),
      protocols: arr(item.protocols).map(String),
      category: obj(item.category),
      hasCategory: !!(item.category && typeof item.category === "object")
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
          errors: arr(port.errors).map(String)
        };
      });
    } else {
      Object.keys(obj(source)).forEach(function (key) {
        var port = obj(source[key]);
        list.push({ id: key, usable: port.usable === true, errors: arr(port.errors).map(String) });
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
    benchReasons: [],            // 默认区：每条一句人话
    benchRaw: [],                // 开发者详情：端口/事实 id、原始 errors/detail
    benchInfo: [],               // 信息性事实（不参与运行闸门，仅展示）
    benchPorts: [],
    benchFacts: [],
    groupBy: "usage",
    sort: "default",
    filters: { q: "", usage: "", domain: "", scene: "", region: "", protocol: "" },
    sel: {},
    lastRun: {},
    resumeJobId: "",
    job: null,
    jobId: "",
    jobTimer: null
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
    }).catch(function (error) {
      S.bench = null;
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
    });
  }

  function computeBench() {
    var bench = S.bench || {};
    S.benchPorts = normPorts(bench);
    S.benchFacts = normFacts(bench);
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
    return Promise.all([loadCases(), loadSuites(), loadBench()]).then(function () {
      restoreState();
      renderAll();
      // 刷新/重开后把上一轮 job 的进度接回来（终态则恢复结论着色，UI-4）
      if (S.resumeJobId) {
        var jobId = S.resumeJobId;
        S.resumeJobId = "";
        attachJob(jobId);
      }
    });
  }

  function restoreState() {
    var saved = readStore();
    S.lastRun = obj(saved.last_run);
    S.resumeJobId = clean(saved.last_job_id);
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

  function renderBench() {
    renderBenchPill();
    var banner = $("bench-banner");
    var stateEl = $("bench-state");
    var hintEl = $("bench-hint");
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
    if (hintEl) {
      hintEl.textContent = S.benchMode === "ready"
        ? (S.benchPorts.filter(function (p) { return p.enabled !== false; }).length + " 口全通 · " + S.benchFacts.filter(function (f) { return f.state === "yes"; }).length + " 条关键事实成立")
        : "";
    }

    var portsBox = document.querySelector('[data-hook="bench-ports"]');
    if (portsBox) {
      portsBox.innerHTML = '<span class="glabel">串口</span>' + (S.benchPorts.length
        ? S.benchPorts.map(function (port) {
          return '<span class="port ' + (port.usable ? "on" : "off") + '" title="' +
            esc(port.errors.length ? port.errors.join("；") : (port.usable ? "可用" : "不可用")) + '">' +
            '<span class="dot"></span>' + esc(port.id) + "</span>";
        }).join("")
        : '<span class="hint">—</span>');
    }
    var factsBox = document.querySelector('[data-hook="bench-facts"]');
    if (factsBox) {
      var stateName = { yes: "成立", no: "未成立", unknown: "未知" };
      factsBox.innerHTML = '<span class="glabel">事实</span>' + (S.benchFacts.length
        ? S.benchFacts.map(function (fact) {
          var cls = fact.state === "yes" ? "yes" : (fact.state === "no" ? "no" : "unknown");
          // 只显示人话（事实 id 放 data-fact 供排查，不显示给用户）
          return '<span class="fact ' + cls + '" data-fact="' + esc(fact.id) + '" title="' +
            esc(factLabel(fact.id) + " · " + (stateName[fact.state] || "未知")) + '">' +
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

  function renderRail() {
    var body = $("rail-body");
    if (!body) return;
    var counts = {};
    S.cases.filter(function (item) {
      var saved = S.filters[S.groupBy];
      S.filters[S.groupBy] = "";
      var ok = matches(item);
      S.filters[S.groupBy] = saved;
      return ok;
    }).forEach(function (item) {
      var key = catOf(item)[S.groupBy] || "";
      counts[key] = (counts[key] || 0) + 1;
    });

    var keys = Object.keys(counts).sort(function (a, b) {
      if (!a !== !b) return a ? -1 : 1;
      return (rankOf(ORDER[S.groupBy], a) - rankOf(ORDER[S.groupBy], b)) || a.localeCompare(b, "zh");
    });
    var total = keys.reduce(function (sum, key) { return sum + counts[key]; }, 0);
    var active = S.filters[S.groupBy];

    var html = '<div class="rail-item' + (active === "" ? " on" : "") + '" data-key="">' +
      '<span class="swatch"></span>全部<span class="rcount">' + total + "</span></div>";
    html += keys.map(function (key) {
      return '<div class="rail-item' + ((active === key || (active === UNCAT && !key)) ? " on" : "") +
        '" data-key="' + esc(key || UNCAT) + '">' +
        '<span class="swatch"></span>' + esc(dimLabel(S.groupBy, key)) +
        '<span class="rcount">' + counts[key] + "</span></div>";
    }).join("");
    body.innerHTML = html;
    Array.prototype.forEach.call(body.querySelectorAll(".rail-item"), function (row) {
      row.onclick = function () {
        S.filters[S.groupBy] = row.dataset.key;
        persist();
        renderRail();
        renderList();
      };
    });
  }

  // ---------- 渲染：用例列表（UI-1） ----------
  function categoryTags(item) {
    var category = catOf(item);
    var tags = [];
    ["usage", "domain", "scene", "region"].forEach(function (dim) {
      if (category[dim]) {
        tags.push('<span class="tag">' + esc(dimLabel(dim, category[dim])) + "</span>");
      }
    });
    if (!category.usage && !category.domain && !category.scene && !category.region) {
      tags.push('<span class="tag uncat" data-hook="uncategorized">' + UNCAT_LABEL + "</span>");
    }
    item.protocols.forEach(function (protocol) {
      tags.push('<span class="tag">' + esc(protocol) + "</span>");
    });
    item.tags.slice(0, 4).forEach(function (tag) {
      tags.push('<span class="tag">#' + esc(tag) + "</span>");
    });
    return tags.join("");
  }

  // 列表行摘要：业务动作链（档案 → 组网 → 绑表 → 抄表），不再把 do 全文/帧标识怼给用户；
  // 完整 do/expect 留给「展开」（D11-1 的列表侧口径）。
  var STEP_ACTION = [
    [/(复位|重启|reboot|reset)/i, "复位"],
    [/(档案|建档|archive)/i, "档案"],
    [/(组网|拓扑|入网|onnet|topolog|nwk)/i, "组网"],
    [/(绑表|建档表|表地址|addr_probe)/i, "绑表"],
    [/(抄表|抄读|电能|read_645|645)/i, "抄表"],
    [/(自检|预检|诊断|preflight|自愈|simcon|simeter|串口)/i, "台架自检"]
  ];
  // 帧标识/协议指令号（11H-F1、10H-F21、645 DI…）：行内摘要一律剥掉
  var FRAME_TOKEN = /\b\d{1,3}H[-\s]?F\d+\b|\b(?:645|698|376\.2|1376\.2)\s*(?:DI|AFN|FN)\b/gi;

  function stepAction(step) {
    // 「不复位 STA」这类否定表述先剥掉，否则会被 /复位/ 误判成复位动作
    var text = String(step.do || "").replace(/不\s*(复位|重启|reboot|reset|改动|影响)/gi, "");
    for (var i = 0; i < STEP_ACTION.length; i += 1) {
      if (STEP_ACTION[i][0].test(text)) return STEP_ACTION[i][1];
    }
    var plain = text.replace(FRAME_TOKEN, " ").replace(/\s{2,}/g, " ").trim();
    return clip(plain || "动作", 10);
  }

  function blockChain(item) {
    var blocks = arr(item.blocks);
    var labels = [];
    blocks.forEach(function (id) {
      // 复用文件后部唯一的 actionLabel 词典；行内摘要去掉「（取表地址）」这类补充说明
      var label = String(actionLabel(String(id || "")) || "").split("（")[0].trim();
      if (!label) return;
      if (labels[labels.length - 1] === label) return;   // 连续重复动作只留一个
      labels.push(label);
    });
    return labels;
  }

  function stepChain(item) {
    var fromBlocks = blockChain(item);
    if (fromBlocks.length) return fromBlocks;   // 优先用**可执行块链**（plan 用例也准）
    var labels = [];
    item.steps.forEach(function (step) {
      var label = stepAction(step);
      if (!label) return;
      if (labels[labels.length - 1] === label) return;   // 连续重复动作只留一个
      labels.push(label);
    });
    return labels;
  }

  function stepSummary(item) {
    // 步数取**可执行步数**（block_count/blocks），不是人读说明条数
    var count = num(item.block_count) || arr(item.blocks).length || item.step_count || item.steps.length || 0;
    var chain = stepChain(item);
    if (!chain.length) return "（声明无步骤）";
    var shown = chain.slice(0, 5);
    return count + " 步：" + shown.join(" → ") + (chain.length > shown.length ? " → …" : "");
  }

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
    html += "<b class=\"case-title\">" + esc(item.title) + "</b>";
    html += '<span class="case-id mono">' + esc(item.id) + "</span>";
    var execSteps = num(item.block_count) || arr(item.blocks).length || item.step_count;
    html += '<span class="case-steps">' + execSteps + " 步</span>";
    if (last) html += badge(last);
    html += '<span class="spacer"></span>';
    html += '<button class="mini run-one" data-id="' + esc(item.id) + '" title="立即运行此用例">▶</button>';
    html += '<button class="mini expand" data-id="' + esc(item.id) + '">展开</button>';
    html += "</div>";
    html += '<div class="case-sum" title="' + esc(stepSummary(item)) + '">' + esc(stepSummary(item)) + "</div>";
    html += '<div class="case-cat">' + categoryTags(item) + "</div>";
    html += renderSteps(item);
    html += "</article>";
    return html;
  }

  function renderList() {
    var wrap = $("list-wrap");
    if (!wrap) return;

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
          expand.textContent = steps.hidden ? "展开" : "收起";
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

  function collectParams() {
    var params = {};
    var input = $("p-meter-addr");
    if (input && input.value.trim()) params.meter_addr = input.value.trim();
    return params;
  }

  function runJob(body, label) {
    var decision = gate();
    if (!decision.ok) { toast(decision.reason); return; }
    var payload = { params: collectParams(), base_url: window.location.origin };
    if (body.suite_id) payload.suite_id = body.suite_id;
    if (body.case_ids) payload.case_ids = body.case_ids;
    api("POST", "/api/cases/run", payload).then(function (data) {
      var jobId = (data && (data.job_id || (data.job && data.job.job_id))) || "";
      if (!jobId) { toast("提交成功但未返回 job_id"); return; }
      toast("已提交（" + (label || "job") + "）：" + jobId);
      attachJob(jobId);
    }).catch(function (error) {
      toast("提交失败：" + error.message);
      showJobError(error);
    });
  }

  function showJobError(error) {
    var panel = $("job-panel");
    if (panel) panel.classList.add("show");
    if ($("job-line")) $("job-line").textContent = "任务提交失败";
    if ($("job-stage")) $("job-stage").textContent = "HTTP " + (error.status || "?");
    if ($("job-results")) {
      $("job-results").innerHTML = '<div class="degraded" data-hook="job-error">' + esc(error.message) + "</div>";
    }
    if ($("job-log")) $("job-log").textContent = "";
  }

  // ---------- job 轮询（UI-4） ----------
  function attachJob(jobId) {
    S.jobId = jobId;
    S.job = null;
    var panel = $("job-panel");
    if (panel) panel.classList.add("show");
    if ($("job-line")) $("job-line").textContent = "任务 " + jobId;
    if ($("job-stage")) $("job-stage").textContent = "提交完成，等待进度…";
    if ($("job-current")) $("job-current").textContent = "";
    if ($("job-summary")) $("job-summary").textContent = "";
    if ($("job-results")) $("job-results").innerHTML = "";
    if ($("job-log")) $("job-log").textContent = "";
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
    setBar(percent, "进度 " + done + "/" + total + " 用例");
    if ($("job-stage")) $("job-stage").textContent = stateLabel(job.state);
    var current = progress.current_case_title || progress.current_case_id || "";
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
    if ($("btn-refresh")) $("btn-refresh").onclick = function () { loadAll().then(function () { toast("已刷新"); }); };
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
})();
