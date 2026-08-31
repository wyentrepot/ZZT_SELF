/* 模拟集中器页（reqs/0010 P4）
 * 数据源：/api/dict/afn-fn（AFN/Fn 参考字典）+ /api/simcon/*（status/ports/open/close/
 * build/step/frames/responders，经 workbench 直挂 /api/simcon 透传 module_log 子应用）。
 * 构帧预览走 /api/simcon/build（只算不发）；下发走 /api/simcon/step（串口未开会自动打开）。
 */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };

  var state = {
    afnList: [], curAfn: -1, curFn: -1,
    filter: "", lastSeq: 0, pollTimer: null, statusTimer: null, sending: false,
    resp: null, mode: "auto",           // REQS-0013：当前 Fn 响应契约 + 查询模式
    snapshots: [], curSnapshot: null,
  };

  function banner(msg) {
    var el = $("#banner");
    if (!msg) { el.classList.remove("show"); el.textContent = ""; return; }
    el.textContent = msg;
    el.classList.add("show");
  }

  function api(path, options) {
    return fetch(path, options).then(function (resp) {
      if (resp.status === 404) return resp.json().catch(function () { return {}; }).then(function (body) {
        throw new Error(body.detail || "资源不存在（会话可能未建立）");
      });
      if (!resp.ok) return resp.json().catch(function () { return {}; }).then(function (body) {
        throw new Error(body.detail || ("HTTP " + resp.status));
      });
      return resp.json();
    });
  }

  /* ================= 串口会话 ================= */

  function loadPorts() {
    api("/api/simcon/ports").then(function (data) {
      var sel = $("#portSel");
      var ports = data.ports || [];
      sel.innerHTML = ports.length
        ? ports.map(function (p) { return "<option>" + esc(p) + "</option>"; }).join("")
        : "<option value=''>无可用串口</option>";
      if (data.mapping_error) banner("串口映射提示：" + data.mapping_error);
    }).catch(function (err) { banner(err.message); });
    refreshStatus();
    if (state.statusTimer) clearInterval(state.statusTimer);
    state.statusTimer = setInterval(refreshStatus, 3000);
  }

  function refreshStatus() {
    api("/api/simcon/status").then(function (st) {
      var chip = $("#stChip");
      if (st.open) {
        chip.innerHTML = "<span class='dot' style='color:var(--color-status-pass)'></span>" +
          esc(st.port || "") + " · 待发帧 " + (st.pending_frames || 0);
        $("#btnOpen").textContent = "重新打开";
      } else {
        chip.innerHTML = "<span class='dot' style='color:var(--color-fg-dim)'></span>未连接";
        $("#btnOpen").textContent = "打开串口";
      }
      if (st.open && !state.pollTimer) startPolling();
    }).catch(function () {
      $("#stChip").innerHTML = "<span class='dot' style='color:var(--color-status-fail)'></span>simcon 不可用";
    });
  }

  $("#btnOpen").addEventListener("click", function () {
    var body = {
      port: $("#portSel").value || null,
      baudrate: +$("#baudSel").value,
      parity: $("#paritySel").value,
    };
    api("/api/simcon/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function () { banner(null); refreshStatus(); startPolling(); })
      .catch(function (err) { banner(err.message); });
  });
  $("#btnClose").addEventListener("click", function () {
    api("/api/simcon/close", { method: "POST" })
      .then(function () { refreshStatus(); })
      .catch(function (err) { banner(err.message); });
  });

  /* ================= AFN / Fn 列 ================= */

  function loadDict() {
    api("/api/dict/afn-fn").then(function (data) {
      state.afnList = data.items || [];
      $("#afnCnt").textContent = state.afnList.length + " 类 AFN";
      renderAfn();
    }).catch(function (err) {
      $("#afnCol").innerHTML = '<div class="empty"><p>字典加载失败</p></div>';
      banner(err.message);
    });
  }

  function renderAfn() {
    $("#afnCol").innerHTML = state.afnList.map(function (a, i) {
      return '<div class="afn-item" data-i="' + i + '"><button class="afn-h">' +
        "<span class='afn-code'>" + esc(a.code) + "</span>" +
        "<span class='afn-name'>" + esc(a.name) + "</span>" +
        "<span class='afn-meta'>" + (a.fns || []).length + "F</span></button></div>";
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll("#afnCol .afn-item"), function (el) {
      el.addEventListener("click", function () { selectAfn(+el.dataset.i); });
    });
  }

  function selectAfn(i) {
    state.curAfn = i;
    state.curFn = -1;
    Array.prototype.forEach.call(document.querySelectorAll("#afnCol .afn-item"), function (el) {
      el.classList.toggle("sel", +el.dataset.i === i);
    });
    var a = state.afnList[i];
    $("#fnCnt").textContent = (a.fns || []).length + " Fn";
    $("#fnCol").innerHTML = (a.fns || []).map(function (f, j) {
      return '<button class="fn-item" data-j="' + j + '"><span class="fn-no">' + esc(f.no) + "</span>" +
        '<span class="fn-nm">' + esc(f.name) + (f.todo ? "<br><span class='mono' style='font-size:10px;color:var(--color-fg-dim)'>字段待补</span>" : "") + "</span></button>";
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll("#fnCol .fn-item"), function (el) {
      el.addEventListener("click", function () { selectFn(+el.dataset.j); });
    });
    $("#parHead").innerHTML = '<div class="par-t1"><b style="font-family:var(--font-mono);color:var(--color-accent)">AFN ' + esc(a.code) + "</b><b>" + esc(a.name) + "</b>" +
      "<span class='chip chip--ghost'>" + esc(a.dir || "—") + "</span></div>" +
      '<div class="par-t2">' + esc(a.sem || "") + "</div>";
    $("#parBody").innerHTML = '<div class="empty"><p>选择具体 Fn 载入参数</p></div>';
    $("#frameMeta").textContent = "—";
  }

  function selectFn(j) {
    state.curFn = j;
    Array.prototype.forEach.call(document.querySelectorAll("#fnCol .fn-item"), function (el) {
      el.classList.toggle("on", +el.dataset.j === j);
    });
    var a = state.afnList[state.curAfn];
    var f = a.fns[j];
    $("#parHead").innerHTML = '<div class="par-t1"><b style="font-family:var(--font-mono);color:var(--color-accent)">' + esc(a.code + " " + f.no) + "</b><b>" + esc(f.name) + "</b>" +
      "<span class='chip chip--" + (f.dir === "下行" ? "tx" : "rx") + "'>" + esc(f.dir || "—") + "</span></div>" +
      '<div class="par-t2">' + esc(f.sem || f.d || a.sem || "") + "</div>";

    // REQS-0016：上行 Fn 只读化——主动上报/应答无需下发，从收发库读取记录
    if (f.dir === "上行") {
      $("#btnSend").style.display = "none";
      var isReport = a.code === "06H";
      $("#parBody").innerHTML =
        '<div class="card"><div class="card-h">上行报文<span class="hint">' + (isReport ? "主动上报" : "应答") + "</span></div>" +
        '<div class="card-in"><span class="hint">' +
        (isReport
          ? "该命令为主动上报（上行），由模块/路由主动推送，无需下发；数据从收发库读取。" +
            "点「上报历史」回查持久化记录，实时帧见下方「收发记录」。"
          : "该命令为上行应答，由对端主动回送，无需下发；数据从收发库（frame_log）读取。") +
        "</span></div></div>";
      $("#frameMeta").textContent = "—";
      setupRespGrid(f);
      if (isReport) loadEvents();
      return;
    }
    $("#btnSend").style.display = "";

    var fields = ((f.req && f.req.fields) || f.fields || []);
    var formable = fields.filter(function (x) { return x.key !== null && x.key !== undefined; });

    // 无数据单元（无字段 或 全为自动计算字段）：隐藏业务参数栏，直接下发
    if (!formable.length) {
      $("#parBody").innerHTML =
        '<div class="card"><div class="card-h">下发</div><div class="card-in">' +
        '<span class="hint">该命令无数据单元，直接下发即可。</span></div></div>' +
        '<div class="card" style="margin-top:14px"><div class="card-h">帧预览 · 68H|L|C|R|A|AFN|DT|数据|CS|16H<span class="hint">只算不发</span></div>' +
        '<div class="card-in"><div class="hexwrap"><div class="hex" id="prevHex">点击「刷新预览」或下发后自动构建</div></div></div></div>';
      buildPreview();
      setupRespGrid(f);
      return;
    }

    // 有字段：渲染表单输入框
    var rows = fields.map(function (x, i) {
      if (x.key === null || x.key === undefined) return "";
      var unit = (typeof x.o === "string" && x.o) ? x.o : "";
      var label = esc(x.n);
      var desc = x.d ? '<div class="ff-desc">' + esc(x.d) + "</div>" : "";
      var ctrl = "";
      if (x.c === "select" && Array.isArray(x.o)) {
        var opts = x.o.map(function (o) {
          var v = Array.isArray(o) ? o[0] : o;
          var t = Array.isArray(o) ? o[1] : o;
          return '<option value="' + esc(v) + '">' + esc(t) + "</option>";
        }).join("");
        ctrl = '<select class="fld ff-in" data-k="' + esc(x.key) + '" data-t="' + esc(x.c || "") + '">' + opts + "</select>";
      } else if (x.key === "_time") {
        ctrl = '<input class="fld ff-in" type="datetime-local" step="1" data-k="_time" data-t="time">';
      } else if (x.key === "payload" || x.key === "data" || x.key === "content") {
        ctrl = '<textarea class="fld ff-in ff-hex" rows="2" spellcheck="false" data-k="' + esc(x.key) + '" data-t="hex" placeholder="hex 报文"></textarea>';
      } else if (x.key === "nodes" || x.key === "meters" || x.key === "relays" || x.key === "subs") {
        ctrl = '<input class="fld ff-in" data-k="' + esc(x.key) + '" data-t="list" placeholder="地址列表（逗号分隔）">';
      } else if (x.key === "addr" || x.c === "bcd") {
        ctrl = '<input class="fld ff-in mono" data-k="' + esc(x.key) + '" data-t="addr" placeholder="12 位地址">';
      } else {
        ctrl = '<input class="fld ff-in" data-k="' + esc(x.key) + '" data-t="' + esc(x.c || "text") + '" placeholder="' + esc(x.f || "") + (x.b ? " × " + x.b + "B" : "") + '">';
      }
      var unitSuffix = unit ? '<span class="ff-unit">' + esc(unit) + "</span>" : "";
      return '<div class="ff-row"><div class="ff-label">' + label + "</div>" +
        '<div class="ff-ctrl">' + ctrl + unitSuffix + "</div>" + desc + "</div>";
    }).join("");

    $("#parBody").innerHTML =
      '<div class="card"><div class="card-h">业务参数<span class="hint">构帧自动完成</span></div>' +
      '<div class="card-in"><div class="ff-grid">' + rows + "</div></div></div>" +
      '<div class="card" style="margin-top:14px"><div class="card-h">帧预览 · 68H|L|C|R|A|AFN|DT|数据|CS|16H<span class="hint">只算不发</span></div>' +
      '<div class="card-in"><div class="hexwrap"><div class="hex" id="prevHex">点击「刷新预览」或修改参数后自动构建</div></div></div></div>';

    Array.prototype.forEach.call(document.querySelectorAll("#parBody .ff-in"), function (el) {
      el.addEventListener("input", debounceBuild);
      el.addEventListener("change", debounceBuild);
    });
    buildPreview();
    setupRespGrid(f);
  }

  /* ================= REQS-0013：响应表格区 ================= */

  function setupRespGrid(f) {
    var resp = (f && f.resp) || null;
    state.resp = resp;
    state.curSnapshot = null;
    var grid = $("#respGrid");
    // 06H 主动上报：显示「上报历史」按钮，实时上报走收发记录 + resp 契约
    var isReport = state.afnList[state.curAfn] && state.afnList[state.curAfn].code === "06H";
    $("#btnEvents").style.display = isReport ? "inline-flex" : "none";
    if (!resp) { grid.style.display = "none"; return; }
    grid.style.display = "flex";
    var lst = resp.list;
    var isList = !!(lst && lst.record && lst.record.length);
    var pm = f.pageMode || "none";
    // 分页双模式切换：列表型才显示模式按钮与查询参数
    var segMode = $("#segMode");
    segMode.style.display = (pm === "both" || pm === "manual" || pm === "auto") ? "flex" : "none";
    if (pm === "auto") state.mode = "auto";
    if (pm === "manual") state.mode = "manual";
    $("#btnQuery").style.display = isList ? "inline-flex" : "none";
    $("#rgStart").style.display = isList ? "inline-flex" : "none";
    $("#rgCount").style.display = isList ? "inline-flex" : "none";
    $("#respMeta").textContent = isReport ? "主动上报 · 已落库" : (lst ? "总数量 → 自动遍历" : "标量响应");
    $("#respGridBody").innerHTML = '<div class="empty" style="height:100px"><p>' + (isReport ? "上报帧到达后自动解析，点「上报历史」回查库" : "查询后响应记录将显示在这里") + "</p></div>";
  }

  // 位域字段的解释器：把 BS/BIN 位字段转可读文本（仅做展示增强）
  function fmtCell(field, val, row, key) {
    if (val == null) return "—";
    if (typeof val === "object") { // BS 位域 → 显示 hex
      return val.hex || String(val);
    }
    // 地址类（BIN 6B 大整数）→ 优先展示 hex；BCD 已是字符串
    var hex = row && row[key + "__hex"];
    var isAddr = /地址|节点地址/.test(field.n) && /BIN/.test(field.f || "");
    if (isAddr && hex) return hex;
    return String(val);
  }

  function renderRespTable(respData) {
    var body = $("#respGridBody");
    var resp = state.resp;
    if (!resp) return;
    var head = respData.head || {};
    var records = respData.records || [];
    var lst = resp.list;
    var html = "";
    // 头部标量（总数/本次数量/起始序号）
    var headKvs = [];
    (resp.fields || []).forEach(function (f) {
      var k = f.n, v = head[k];
      if (v != null) headKvs.push('<div class="kv"><div class="k">' + esc(f.n) + "</div><div class='v'>" + esc(fmtCell(f, v, head, k)) + "</div></div>");
    });
    if (headKvs.length) html += '<div class="resp-scalar">' + headKvs.join("") + "</div>";
    if (lst && lst.record && lst.record.length) {
      var cols = lst.record.filter(function (f) { return !String(f.b || "").startsWith("len_ref") || f.f === "BIN" ? true : false; });
      // 列：排除 list_ref 嵌套（扁平展示主字段）
      cols = lst.record.filter(function (f) { return !String(f.f || "").startsWith("list_ref:"); });
      html += '<table class="rtab"><thead><tr><th>#</th>' + cols.map(function (f) {
        return "<th>" + esc(f.n) + "</th>";
      }).join("") + "</tr></thead><tbody>";
      records.forEach(function (r, i) {
        html += "<tr><td class='num'>" + (r._seq_index != null ? r._seq_index + 1 : i + 1) + "</td>" + cols.map(function (f) {
          return "<td>" + esc(fmtCell(f, r[f.n], r, f.n)) + "</td>";
        }).join("") + "</tr>";
      });
      html += "</tbody></table>";
      if (!records.length) html += '<div class="empty" style="height:80px"><p>本帧无记录</p></div>';
    } else if (!headKvs.length) {
      html = '<div class="empty" style="height:80px"><p>无结构化响应</p></div>';
    }
    body.innerHTML = html;
  }

  function loadSnapshots() {
    api("/api/simcon/store/snapshots?limit=20").then(function (d) {
      state.snapshots = d.items || [];
      renderSnapshotList();
    }).catch(function () { state.snapshots = []; });
  }

  function renderSnapshotList() {
    var items = state.snapshots;
    $("#respMeta").textContent = items.length ? ("快照 × " + items.length + "（点快照回看）") : "快照 0";
  }

  function querySnapshotsByAfnFn() {
    var a = state.afnList[state.curAfn];
    var f = a.fns[state.curFn];
    api("/api/simcon/store/snapshots?afn=" + encodeURIComponent(a.code) + "&fn=" + encodeURIComponent(f.no) + "&limit=5")
      .then(function (d) { state.snapshots = d.items || []; renderSnapshotList(); })
      .catch(function () {});
  }

  function doQuery() {
    var a = state.afnList[state.curAfn];
    var f = a.fns[state.curFn];
    if (!f || !f.resp || !f.resp.list) return;
    var start = parseInt($("#rgStart").value || "0", 10) || 0;
    var count = parseInt($("#rgCount").value || "16", 10) || 16;
    // 把范围写入表单的 start/count 字段（若有），供下发
    var startEl = document.querySelector('#parBody .ff-in[data-k="start"]');
    var countEl = document.querySelector('#parBody .ff-in[data-k="count"]');
    if (startEl) startEl.value = start;
    if (countEl) countEl.value = count;
    buildPreview();
    banner("已填查询参数（起始=" + start + "，条数=" + count + "），点「下发」发送");
  }

  $("#btnQuery").addEventListener("click", doQuery);
  $("#btnSnapshot").addEventListener("click", function () {
    querySnapshotsByAfnFn();
    banner("快照列表已刷新到「响应表格」标题栏");
  });
  $("#btnEvents").addEventListener("click", loadEvents);

  /* ================= REQS-0013：06H 上报历史回查 ================= */
  function loadEvents() {
    api("/api/simcon/store/events?limit=50").then(function (d) {
      var items = d.items || [];
      renderEvents(items);
    }).catch(function (err) { banner("上报历史读取失败：" + err.message); });
  }

  function renderEvents(items) {
    var body = $("#respGridBody");
    if (!items.length) {
      body.innerHTML = '<div class="empty" style="height:100px"><p>暂无上报记录（库为空）</p></div>';
      return;
    }
    var html = '<table class="rtab"><thead><tr><th>时间</th><th>AFN/Fn</th><th>事件类型</th><th>摘要</th></tr></thead><tbody>';
    items.forEach(function (e) {
      var pl = {};
      try { pl = JSON.parse(e.payload_json || "{}"); } catch (err) {}
      var n = (pl.head && pl.head["上报从节点的数量n"]) != null ? pl.head["上报从节点的数量n"] : "";
      var recs = pl.records || [];
      var summary = recs.length ? ("记录 × " + recs.length + (n !== "" ? " / 总数 " + n : "")) : (n !== "" ? "数量 " + n : "—");
      html += "<tr><td>" + esc((e.ts || "").replace("T", " ").slice(0, 16)) + "</td>" +
        "<td class='mono'>" + esc(e.afn) + " " + esc(e.fn) + "</td>" +
        "<td>" + esc(e.event_type || "—") + "</td><td>" + esc(summary) + "</td></tr>";
    });
    html += "</tbody></table>";
    body.innerHTML = html;
    $("#respMeta").textContent = "上报历史 × " + items.length + "（持久层）";
  }

  $("#segMode").addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    Array.prototype.forEach.call(document.querySelectorAll("#segMode button"), function (x) { x.classList.remove("on"); });
    btn.classList.add("on");
    state.mode = btn.dataset.m;
  });

  var debounceTimer = null;
  function debounceBuild() { clearTimeout(debounceTimer); debounceTimer = setTimeout(buildPreview, 500); }

  function currentSend() {
    var a = state.afnList[state.curAfn];
    var f = a.fns[state.curFn];
    var params = collectFormParams();
    return {
      afn: parseInt(a.code, 16),
      fn: parseInt(String(f.no).replace(/^F/i, ""), 10),
      params: params,
    };
  }

  /* 从表单字段（data-k）组装业务参数；数值字段转数字，时间拆 BCD 六元组 */
  function collectFormParams() {
    var params = {};
    var els = document.querySelectorAll("#parBody .ff-in");
    els.forEach(function (el) {
      var k = el.dataset.k;
      var t = el.dataset.t;
      var v = el.value;
      if (v === "" || v == null) return;
      if (t === "_time" || t === "time") {
        // datetime-local → 秒分时日月年（BCD 六元组，adapter _time6_bytes 消费）
        if (v) {
          var d = new Date(v);
          params.sec = d.getSeconds(); params.min = d.getMinutes();
          params.hour = d.getHours(); params.day = d.getDate();
          params.month = d.getMonth() + 1; params.year = (d.getFullYear() % 100);
        }
        return;
      }
      if (t === "list") {
        // 地址列表：逗号/空白分隔，输出数组（adapter nodes/meters/relays/subs 消费）
        params[k] = v.split(/[,，\s]+/).filter(Boolean);
        return;
      }
      if (t === "num" || t === "bcd" || t === "addr") {
        var num = v.trim();
        if (/^-?\d+$/.test(num)) { params[k] = parseInt(num, 10); return; }
        params[k] = num;
        return;
      }
      params[k] = v;
    });
    return params;
  }

  function buildPreview() {
    if (state.curAfn < 0 || state.curFn < 0) return;
    var send;
    try { send = currentSend(); }
    catch (e) {
      $("#prevHex").innerHTML = "<span style='color:var(--color-status-fail)'>" + esc(e.message) + "</span>";
      $("#frameMeta").textContent = "—";
      return;
    }
    api("/api/simcon/build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ afn: send.afn, fn: send.fn, params: send.params, profile: $("#profileSel").value }),
    }).then(function (r) {
      $("#prevHex").innerHTML = colorHex(r.hex);
      $("#frameMeta").textContent = "帧长 " + r.length + " 字节";
    }).catch(function (err) {
      $("#prevHex").innerHTML = "<span style='color:var(--color-status-fail)'>" + esc(err.message) + "</span>";
      $("#frameMeta").textContent = "构帧失败";
    });
  }
  $("#btnBuild").addEventListener("click", buildPreview);

  $("#btnSend").addEventListener("click", function () {
    if (state.sending) return;
    var send;
    try { send = currentSend(); }
    catch (e) { banner(e.message); return; }
    state.sending = true;
    var btn = $("#btnSend");
    btn.disabled = true;
    api("/api/simcon/step", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ send: send, profile: $("#profileSel").value, enable_responder: true, name: "page-send" }),
    }).then(function (r) {
      banner(null);
      $("#frameMeta").textContent = "已下发 · " + new Date().toLocaleTimeString();
      refreshFrames();
      refreshStatus();
      btn.disabled = false;
      state.sending = false;
    }).catch(function (err) {
      banner("下发失败：" + err.message);
      btn.disabled = false;
      state.sending = false;
    });
  });

  /* ================= 帧着色（与设计稿同规则） ================= */

  function colorHex(raw) {
    if (!raw) return "";
    var p = String(raw).trim().split(/\s+/);
    if (p.length < 8) return p.map(function (x) { return "<span class='hx s-DU'>" + esc(x) + "</span>"; }).join(" ");
    var c = new Array(p.length).fill("s-DU");
    c[0] = "s-68"; c[p.length - 1] = "s-16"; c[p.length - 2] = "s-CS";
    c[1] = "s-L"; c[2] = "s-L"; c[3] = "s-C";
    for (var i = 4; i < Math.min(10, p.length - 2); i++) c[i] = "s-R";
    if (p.length > 10) { c[10] = "s-AFN"; if (p.length > 11) c[11] = "s-DT"; if (p.length > 12) c[12] = "s-DT"; }
    return p.map(function (x, i) { return "<span class='hx " + c[i] + "'>" + esc(x) + "</span>"; }).join(" ");
  }

  /* ================= 收发记录 ================= */

  function refreshFrames() {
    var dir = $("#segF button.on").dataset.f || "";
    api("/api/simcon/frames?limit=200" + (dir ? "&direction=" + dir : "") + (state.lastSeq ? "&after_seq=" + state.lastSeq : ""))
      .then(function (data) {
        var frames = data.entries || [];
        var body = $("#trBody");
        if (data.counts) {
          $("#cTx").textContent = data.counts.tx || 0;
          $("#cRx").textContent = data.counts.rx || 0;
        }
        if (!frames.length) {
          var pending = body.querySelector(".empty");
          if (pending) pending.querySelector("p").textContent = "暂无帧记录";
          return;
        }
        state.lastSeq = frames[frames.length - 1].seq || state.lastSeq;
        var empty = body.querySelector(".empty");
        if (empty) empty.remove();
        frames.reverse().forEach(function (f) {
          var row = document.createElement("div");
          row.className = "tr-row";
          row.innerHTML =
            '<button class="tr-main"><span class="tm">' + esc((f.ts || "").replace("T", " ").slice(0, 12)) + "</span>" +
            '<span class="dir ' + f.dir + '">' + (f.dir === "tx" ? "TX" : "RX") + "</span>" +
            '<span class="afn-tag2">' + esc(f.afn || "—") + " " + esc(f.fn || "") + "</span>" +
            (f.kind ? '<span class="kind-tag">' + esc(f.kind) + "</span>" : "") +
            '<span class="sum mono">' + esc(f.frame_hex || "") + "</span></button>" +
            '<div class="tr-det"><div class="hexwrap"><div class="hex">' + colorHex(f.frame_hex) + "</div></div>" +
            (f.updown ? '<div class="hint" style="margin-top:6px">CCO 主动上报（updown=' + esc(f.updown) + "）</div>" : "") + "</div>";
          row.querySelector(".tr-main").addEventListener("click", function () { row.classList.toggle("open"); });
          body.insertBefore(row, body.firstChild);
          // REQS-0013：上行响应帧 → 刷新响应表格（匹配当前选中 Fn）
          if (f.dir === "rx" && f.updown === "up" && f.resp && state.resp && f.afn === (state.afnList[state.curAfn] || {}).code && f.fn === (state.afnList[state.curAfn] || {}).fns[state.curFn].no) {
            renderRespTable(f.resp);
          }
        });
        while (body.children.length > 300) body.removeChild(body.lastChild);
      }).catch(function () { /* 会话未建立时静默，状态条已提示 */ });
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(refreshFrames, 2500);
    refreshFrames();
  }

  $("#segF").addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    Array.prototype.forEach.call(document.querySelectorAll("#segF button"), function (x) { x.classList.remove("on"); });
    btn.classList.add("on");
    state.lastSeq = 0;
    $("#trBody").innerHTML = '<div class="empty" style="height:120px"><p>加载中…</p></div>';
    refreshFrames();
  });
  $("#trH").addEventListener("click", function (e) {
    if (e.target.closest(".seg") || e.target.closest(".chip")) return;
    $("#traffic").classList.toggle("fold");
  });

  /* ================= 内置应答 ================= */

  function loadResponders() {
    api("/api/simcon/responders").then(function (data) {
      var rules = data.rules || [];
      var strip = $("#respStrip");
      if (!rules.length) {
        strip.innerHTML = '<span class="lbl">内置应答</span><span class="hint" style="padding-top:2px">无规则</span>';
        return;
      }
      strip.innerHTML = '<span class="lbl">内置应答 · ' + rules.length + "</span>" + rules.map(function (r) {
        var m = r.match || {}, rp = r.reply || {};
        return "<span class='chip'><span class='mono' style='color:var(--color-accent)'>" + esc(r.id || "—") + "</span>" +
          (m.afn != null ? esc(afnTxt(m.afn, m.fn)) : "") +
          (rp.afn != null ? " → " + esc(afnTxt(rp.afn, rp.fn)) : "") +
          (r.desc ? "<span class='hint'>" + esc(r.desc) + "</span>" : "") + "</span>";
      }).join("");
    }).catch(function () {
      $("#respStrip").innerHTML = '<span class="lbl">内置应答</span><span class="hint" style="padding-top:2px">不可用</span>';
    });
  }

  function afnTxt(afn, fn) {
    var a = typeof afn === "number" ? afn.toString(16).toUpperCase().padStart(2, "0") + "H" : String(afn || "");
    var f = fn == null ? "" : (typeof fn === "number" ? "F" + fn : String(fn));
    return " " + a + f;
  }

  /* ================= 初始化 ================= */
  loadDict();
  loadPorts();
  loadResponders();
})();
