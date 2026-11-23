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
        return { id: String(port.id || port.name || ""), usable: port.usable === true, errors: arr(port.errors).map(String) };
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
    benchReasons: [],
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
      S.benchReasons = [error && error.status === 404
        ? "后端未就绪：GET /api/cases/bench 尚未提供，无法预检台架（运行按钮保持可用，整轮可能判 blocked）"
        : ("台架快照获取失败：" + (error && error.message ? error.message : String(error)))];
    });
  }

  function computeBench() {
    var bench = S.bench || {};
    S.benchPorts = normPorts(bench);
    S.benchFacts = normFacts(bench);
    // 运行闸门只看**必需事实**（后端 unmet_required = 用例库 needs 并集里不成立的）；
    // 信息性事实（如学习态的 nwk.* / 未注入能力的 sta.meter_addr_known）恒 unknown，
    // 若参与闸门会让按钮无谓置灰（2026-11-23 真机回归）。后端未给该字段时退化为全量口径。
    var requiredUnmet = (bench.unmet_required && typeof bench.unmet_required === "object")
      ? bench.unmet_required : null;
    var reasons = [];
    S.benchPorts.forEach(function (port) {
      if (!port.usable) {
        reasons.push("串口 " + port.id + " 不可用" + (port.errors.length ? "：" + port.errors.join("；") : ""));
      }
    });
    S.benchFacts.forEach(function (fact) {
      if (fact.state === "yes") return;
      var line = "事实 " + fact.id + (fact.state === "no" ? " 未成立" : " 状态未知") +
        (fact.detail ? "：" + fact.detail : "");
      var required = requiredUnmet ? Object.prototype.hasOwnProperty.call(requiredUnmet, fact.id) : true;
      if (required) reasons.push(line); else S.benchInfo.push(line);
    });
    if (bench.ready === false && !reasons.length) {
      reasons.push("后端判定台架未就绪（ready=false，未给出明细）");
    }
    if (!S.benchPorts.length && !S.benchFacts.length) {
      S.benchMode = "unknown";
      S.benchReasons = ["台架快照为空（既无端口也无事实），无法预检"];
      return;
    }
    S.benchReasons = reasons;
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
        ? (S.benchPorts.length + " 口全通 · " + S.benchFacts.filter(function (f) { return f.state === "yes"; }).length + " 条关键事实成立")
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
      factsBox.innerHTML = '<span class="glabel">事实</span>' + (S.benchFacts.length
        ? S.benchFacts.map(function (fact) {
          var cls = fact.state === "yes" ? "yes" : (fact.state === "no" ? "no" : "unknown");
          var label = fact.state === "yes" ? "yes" : (fact.state === "no" ? "no" : "unknown");
          return '<span class="fact ' + cls + '" title="' + esc(fact.detail || label) + '">' +
            '<span class="dot"></span>' + esc(fact.id) + " · " + esc(label) + "</span>";
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

  function stepSummary(item) {
    var parts = item.steps.slice(0, 3).map(function (step) {
      return step.no + ". " + (step.do || "—");
    });
    if (item.steps.length > 3) parts.push("…（共 " + item.steps.length + " 步）");
    return parts.join("　") || "（声明无步骤）";
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
    html += '<span class="case-steps">' + item.step_count + " 步</span>";
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

  function summaryText(job) {
    var counts = obj(job.case_counts);
    var parts = [];
    ["pass", "fail", "blocked", "inconclusive"].forEach(function (key) {
      var value = num(counts[key]);
      if (value) parts.push(VERDICT[key].label + " " + value);
    });
    if (parts.length) return parts.join(" · ");
    if (job.verdict) return "结论 " + verdictText(job.verdict);
    return "";
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
    if ($("job-results")) $("job-results").innerHTML = results.map(renderCaseResult).join("");
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

  // ---------- 断点可视化（UI-5） ----------
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

  function renderCaseResult(raw) {
    var result = obj(raw);
    var verdict = normVerdict(result.verdict);
    var cls = "case-result is-" + (verdict || "unknown");
    var html = '<div class="' + cls + '" data-hook="case-result" data-verdict="' + esc(verdict) + '">';
    html += '<div class="cr-head">' + badge(verdict) +
      "<b>" + esc(result.case_id || "") + "</b>" +
      (result.title ? '<span class="cr-title">' + esc(result.title) + "</span>" : "") +
      "</div>";
    html += '<div class="cr-body">' + breakpointHtml(result) + resultRowsHtml(result) + "</div>";
    html += "</div>";
    return html;
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
