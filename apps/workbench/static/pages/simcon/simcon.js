/* 模拟集中器页（reqs/0010 P4）
 * 数据源：/api/dict/afn-fn（AFN/Fn 参考字典）+ /api/simcon/*（status/ports/open/close/
 * build/step/frames/responders，经 workbench 直挂 /api/simcon 透传 module_log 子应用）。
 * 构帧预览走 /api/simcon/build（只算不发）；下发走 /api/simcon/step（串口未开会自动打开）。
 * REQS-0037「分钟采集」页签：F231/F232 配任务 + 11E3 构帧预览 + 0037 场景入口
 * + 06H-F230 存数展示（/report_buckets buckets.F230 与 /store/mclt）。
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
      var details = data.port_details || [];
      // 用 port_details 渲染：显示角色标签（如 “CCO 日志口 · COM8”）
      var opts = details.length
        ? details.map(function (item) {
            var tag = item.role_label || item.label || "";
            var text = tag ? tag + " · " : "";
            text += item.device;
            if (item.online === false) text += "（离线）";
            return '<option value="' + esc(item.device) + '">' + esc(text) + "</option>";
          }).join("")
        : "<option value=''>无可用串口</option>";
      sel.innerHTML = opts;
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
    var sendDesc = null;
    try {
      var a0 = state.afnList[state.curAfn], f0 = a0.fns[state.curFn];
      sendDesc = { afn: send.afn, fn: send.fn, name: f0.name,
                   afnHex: a0.code, fnNo: f0.no };
    } catch (e) {}
    if (sendDesc) panelPending(sendDesc);   // REQS-0027：等待配对（倒计时）
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
      if (sendDesc) showPairing(r, sendDesc);   // REQS-0027：配对卡片
    }).catch(function (err) {
      banner("下发失败：" + err.message);
      btn.disabled = false;
      state.sending = false;
      panelAbort(err.message);
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

  /* REQS-0031 P2：帧推送至外壳全局 Dock（跨模块"示波器"）。
     postMessage 交给壳层（app.js 监听 wb-dock-push），本页不依赖壳存在。 */
  function pushDockFrame(f) {
    try {
      if (!window.parent || window.parent === window) return;
      window.parent.postMessage({
        type: "wb-dock-push",
        view: "frame",
        dir: f.dir === "tx" ? "tx" : "rx",
        source: "模拟集中器",
        tag: (f.afn || "—") + " " + (f.fn || ""),
        text: f.frame_hex || ""
      }, "*");
    } catch (e) { /* 非嵌套环境静默 */ }
  }

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
        var firstBatch = !state.lastSeq;   // 首批为历史帧，不重复推送 Dock
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
          if (!firstBatch) pushDockFrame(f);   // REQS-0031 P2：增量帧 → 外壳全局 Dock
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

  /* ================= REQS-0027：结果面板 ================= */

  var ER = null;               // expect_rules 文档（/api/simcon/expect_rules）
  var pairTimer = null;        // 等待应答倒计时
  var panelTab = "pair";
  var readTimer = null, rptTimer = null, batchTimer = null;
  var batchJobId = null;
  var readFilter = { source: "", result: "" };

  function loadExpectRules() {
    api("/api/simcon/expect_rules").then(function (d) { ER = d; }).catch(function () {});
  }

  /* 与后端 expect_rules.rule_for 同语义：Fn 级规则优先于 AFN 级 */
  function ruleFor(afnInt, fnInt) {
    if (!ER) return null;
    var fallback = null;
    for (var i = 0; i < (ER.rules || []).length; i++) {
      var r = ER.rules[i], m = r.match || {};
      var ma = parseInt(m.afn, 16), mf = m.fn == null ? null : parseInt(m.fn, 10);
      if (ma !== afnInt) continue;
      if (mf != null && mf === fnInt) return r;
      if (mf == null && !fallback) fallback = r;
    }
    return fallback;
  }

  function tierFor(afnInt, fnInt) {
    var r = ruleFor(afnInt, fnInt);
    var id = (r && r.timeout_tier) || "default";
    var t = ER && ER.timeout_tiers ? ER.timeout_tiers[id] : null;
    return t ? { seconds: t.seconds, tier: id, note: t.note || "" }
             : { seconds: 5, tier: "default", note: "规则未加载，按默认 5s" };
  }

  function afnHexOf(n) { return (n == null ? "—?" : Number(n).toString(16).toUpperCase().padStart(2, "0") + "H"); }
  function fnNoOf(n) { return n == null ? "" : "F" + n; }

  /* ---- 收发配对 ---- */
  function setPairBody(html) { $("#pBodyPair").innerHTML = html; }

  function panelPending(desc) {
    switchPanel("pair", true);
    var tier = tierFor(desc.afn, desc.fn);
    stopPairTimer();
    var t0 = Date.now(), total = tier.seconds * 1000;
    setPairBody(
      '<div class="pairwrap">' +
        '<div class="pcard"><div class="pcard-h"><span class="chip chip--tx">下发</span><b class="mono">' + esc(desc.afnHex + " " + desc.fnNo) + "</b>" + esc(desc.name) + "</div>" +
        '<div class="pcard-in"><div class="pkv"><span class="k">状态</span><span class="v"><span class="badge badge--wait">等待应答…</span></span></div>' +
        '<div class="pkv"><span class="k">超时档位</span><span class="v mono" title="' + esc(tier.note) + '">' + esc(tier.tier) + " · " + tier.seconds + "s</span></div>" +
        '<div class="cd-bar"><i id="cdBar" style="width:0%"></i></div></div></div>' +
        '<div class="pcard"><div class="pcard-h"><span class="chip chip--rx">应答</span></div>' +
        '<div class="pcard-in"><span class="hint">窗口内持续等待应答帧（跳过旁听帧）…</span></div></div>' +
      "</div>");
    var meta = $("#panelMeta");
    if (tier.note) meta.textContent = "档位 " + tier.tier + "（" + tier.note + "）";
    pairTimer = setInterval(function () {
      var p = Math.min(100, (Date.now() - t0) / total * 100);
      var bar = $("#cdBar");
      if (bar) bar.style.width = p + "%";
      if (p >= 100) stopPairTimer();
    }, 200);
  }

  function stopPairTimer() { if (pairTimer) { clearInterval(pairTimer); pairTimer = null; } }
  function panelAbort(msg) {
    stopPairTimer();
    setPairBody('<div class="empty" style="height:80px"><p>下发失败：' + esc(msg) + "</p></div>");
  }

  function decodedSummary(parsed) {
    if (!parsed) return "";
    var rows = (parsed.items || []).map(function (it) {
      return "<tr><td>" + esc(it.name) + "</td><td class='mono'>" + esc(it.value != null ? it.value : it.hex) + "</td></tr>";
    }).join("");
    var nested = parsed.nested || [];
    var html = rows ? '<table class="rtab"><thead><tr><th>数据项</th><th>值</th></tr></thead><tbody>' + rows + "</tbody></table>" : "";
    if (nested.length) {
      html += '<div class="hint" style="margin-top:6px">嵌套帧 × ' + nested.length + "（645/698 明细见收发记录展开）</div>";
    }
    return html;
  }

  function showPairing(r, desc) {
    stopPairTimer();
    switchPanel("pair", true);
    var s = (r && r.step) || {};
    var tier = tierFor(desc.afn, desc.fn);
    var meta = s.pairing || {};
    var srcNote = meta.expect_source === "auto"
      ? "规则 " + esc(meta.rule_id || "—") + " 自动生成 expect"
      : "显式 expect（覆盖规则）";
    // 左卡：下发
    var tx = '<div class="pcard"><div class="pcard-h"><span class="chip chip--tx">下发</span>' +
      '<b class="mono">' + esc(desc.afnHex + " " + desc.fnNo) + "</b><span>" + esc(desc.name) + "</span></div>" +
      '<div class="pcard-in"><div class="pkv"><span class="k">构帧</span><span class="v mono">' + colorHex(s.sent_hex) + "</span></div>" +
      '<div class="pkv"><span class="k">预期</span><span class="v"><span class="badge badge--idle">' + esc(meta.desc || "—") + "</span></span></div>" +
      '<div class="pkv"><span class="k">超时</span><span class="v mono" title="' + esc((meta.timeout || tier).note || "") + '">' +
      esc(((meta.timeout || tier).tier || "?") + " · " + ((meta.timeout || tier).seconds + "s")) + "</span></div></div></div>";
    // 右卡：应答（按形态分格式）
    var rxIn = "";
    if (s.deny) {
      rxIn = '<div class="pkv"><span class="k">判定</span><span class="v"><span class="badge badge--deny">否认 ' + esc(s.deny.code_hex) + "</span></span></div>" +
        '<div class="pkv"><span class="k">语义</span><span class="v"><b>' + esc(s.deny.text) + "</b></span></div>";
    } else if (s.result === "pass") {
      var confirmish = s.parsed && (s.parsed.fields || {}).AFN && /^0x00/.test(String(s.parsed.fields.AFN.value || ""));
      rxIn = '<div class="pkv"><span class="k">判定</span><span class="v"><span class="badge badge--ok">' +
        (confirmish ? "确认 00H-F1" : "应答匹配 " + esc(s.parsed && s.parsed.fields && s.parsed.fields.AFN ? String(s.parsed.fields.AFN.value).split(" ")[0] : "")) + "</span></span></div>";
    } else {
      rxIn = '<div class="pkv"><span class="k">判定</span><span class="v"><span class="badge badge--idle">预期应答未到（超时）</span></span></div>' +
        '<div class="pkv"><span class="k">原因</span><span class="v">' + esc(s.reason || "") + "</span></div>";
    }
    var rxBody = rxIn + (s.result === "pass" || s.deny ? decodedSummary(s.parsed) : "");
    var bl = s.bystanders || [];
    if (bl.length) {
      rxBody += '<div class="pkv"><span class="k">旁听</span><span class="v"><div class="byst">' +
        bl.slice(-6).map(function (b) {
          return '<div class="b-row"><span class="afn-tag2">' + esc(b.afn ? b.afn + "H" : "—") + " " + esc(b.fn != null ? "F" + b.fn : "") + '</span><span class="mono">' + esc(String(b.hex).slice(0, 40)) + "…</span></div>";
        }).join("") + "</div><div class='hint'>窗口内收到 " + bl.length + " 帧非预期帧（主动上报插播），不占匹配窗口</div></span></div>";
    }
    var rx = '<div class="pcard"><div class="pcard-h"><span class="chip chip--rx">应答</span>' +
      (s.result === "pass" ? '<span class="badge badge--ok">匹配成功</span>' : s.deny ? '<span class="badge badge--deny">业务失败</span>' : '<span class="badge badge--idle">未应答</span>') + "</div>" +
      '<div class="pcard-in">' + rxBody + "</div></div>";
    setPairBody('<div class="pairwrap">' + tx + rx + "</div>" +
      '<div class="hint" style="margin-top:6px">' + srcNote + " · 档位出处见超时 tooltip</div>");
    $("#panelMeta").textContent = "最近配对 · " + new Date().toLocaleTimeString();
  }

  /* ---- 抄读汇总（G4） ---- */
  function renderReadings(d) {
    var s = d.stats || {};
    var denyChips = Object.keys(s.deny_breakdown || {}).map(function (k) {
      return '<span class="chip">' + esc(k) + " × " + s.deny_breakdown[k] + "</span>";
    }).join("");
    var head = '<div class="statrow" style="margin-bottom:9px">' +
      '<div class="statkv"><div class="k">下发</div><div class="v">' + (s.sent || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">应答</div><div class="v">' + (s.replied || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">成功</div><div class="v v-ok">' + (s.success || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">失败</div><div class="v v-fail">' + (s.failed || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">成功率</div><div class="v ' + ((s.success_rate || 0) >= 0.8 ? "v-ok" : "v-fail") + '">' +
      (s.success_rate == null ? "—" : Math.round(s.success_rate * 100) + "%") + "</div></div>" + denyChips +
      '<span class="sp" style="margin-left:auto"></span>' +
      '<select class="mini-in" id="rfSource"><option value="">全部来源</option><option value="batch">并发任务</option><option value="snapshot">查询快照</option><option value="report">上报抄读</option></select>' +
      '<select class="mini-in" id="rfResult"><option value="">全部结果</option><option value="success">成功</option><option value="deny">否认</option><option value="timeout">超时</option><option value="error">错误</option></select>' +
      "</div>";
    var rows = d.rows || [];
    var tbl = rows.length
      ? '<table class="rtab"><thead><tr><th>时间</th><th>来源</th><th>表地址</th><th>AFN/Fn</th><th>数据项</th><th>值</th><th class="num">耗时ms</th><th>结果</th></tr></thead><tbody>' +
        rows.map(function (x) {
          var st = x.status === "success" ? '<span class="badge badge--ok">成功</span>'
            : x.status === "deny" ? '<span class="badge badge--deny">' + esc(x.deny_code || "否认") + " " + esc(x.deny_text || "") + "</span>"
            : x.status === "timeout" ? '<span class="badge badge--idle">超时</span>'
            : '<span class="badge badge--idle">' + esc(x.status) + "</span>";
          return "<tr><td class='mono'>" + esc((x.ts || "").replace("T", " ").slice(0, 19)) + "</td><td>" + esc(x.source_label || x.source || "") + "</td>" +
            "<td class='mono'>" + esc(x.meter || "") + "</td><td class='mono'>" + esc(x.afn_fn || "") + "</td><td>" + esc(x.item || "") + "</td>" +
            "<td class='mono' style='max-width:220px;overflow:hidden;text-overflow:ellipsis'>" + esc(x.value || "") + "</td>" +
            "<td class='num'>" + (x.elapsed_ms != null ? x.elapsed_ms : "—") + "</td><td>" + st + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<div class="empty" style="height:80px"><p>暂无抄读记录（下发单抄/并抄后自动汇总）</p></div>';
    $("#pBodyReadings").innerHTML = head + tbl;
    var src = $("#rfSource"), rst = $("#rfResult");
    if (src) {
      src.value = readFilter.source; rst.value = readFilter.result;
      src.addEventListener("change", function () { readFilter.source = src.value; loadReadings(); });
      rst.addEventListener("change", function () { readFilter.result = rst.value; loadReadings(); });
    }
  }

  function loadReadings() {
    var q = "?limit=300" + (readFilter.source ? "&source=" + readFilter.source : "") +
      (readFilter.result ? "&result=" + readFilter.result : "");
    api("/api/simcon/readings" + q).then(renderReadings).catch(function (e) {
      $("#pBodyReadings").innerHTML = '<div class="empty" style="height:80px"><p>读取失败：' + esc(e.message) + "</p></div>";
    });
  }

  /* ---- 并发抄表（G5） ---- */
  function renderBatchForm() {
    $("#pBodyBatch").innerHTML =
      '<div class="pbody-row">' +
        '<span class="hint">表地址（每行一个或逗号分隔）</span>' +
        '<span class="hint">最大并发</span><input class="mini-in" id="bcMax" value="5" style="width:52px" title="在途数=最大并发；6D=超最大并发">' +
        '<select class="mini-in" id="bcMode"><option value="single">单抄 02H-F1（59s 档）</option><option value="batch">并发抄表 F1H-F1（99s 档）</option></select>' +
        '<select class="mini-in" id="bcProto"><option value="2">645-2007</option><option value="3">698.45</option><option value="0">透明</option></select>' +
        '<button class="btn btn--sm btn--primary" id="bcStart">启动任务</button>' +
        '<button class="btn btn--sm btn--ghost" id="bcStop">停止</button>' +
        '<span class="chip chip--ghost" id="bcMeta">滑窗：回一帧补一发</span>' +
      "</div>" +
      '<textarea class="meter-ta" id="bcMeters" placeholder="000000000001&#10;000000000002&#10;000000000003"></textarea>' +
      '<div id="bcStatus"><div class="empty" style="height:60px"><p>启动后此处显示在途/完成/排队计数与逐表明细</p></div></div>';
    $("#bcStart").addEventListener("click", startBatch);
    $("#bcStop").addEventListener("click", function () {
      if (batchJobId) api("/api/simcon/batch_read/" + batchJobId + "/stop", { method: "POST" }).then(pollBatch).catch(function (e) { banner(e.message); });
    });
  }

  function startBatch() {
    var meters = $("#bcMeters").value.split(/[^0-9A-Za-z]+/).filter(function (x) { return x.length === 12; });
    if (!meters.length) { banner("请先填入 12 位表地址"); return; }
    var body = {
      meters: meters,
      max_concurrent: parseInt($("#bcMax").value, 10) || 5,
      mode: $("#bcMode").value,
      protocol_type: parseInt($("#bcProto").value, 10) || 2,
      profile: $("#profileSel").value,
    };
    api("/api/simcon/batch_read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (j) {
        banner(null);
        batchJobId = j.job_id;
        renderBatchStatus(j);
        if (!batchTimer) batchTimer = setInterval(pollBatch, 1000);
      }).catch(function (e) { banner("任务创建失败：" + e.message); });
  }

  function pollBatch() {
    if (!batchJobId) return;
    api("/api/simcon/batch_read/" + batchJobId).then(function (j) {
      renderBatchStatus(j);
      if (j.finished && batchTimer) { clearInterval(batchTimer); batchTimer = null; loadReadings(); }
    }).catch(function () {});
  }

  function renderBatchStatus(j) {
    var rate = j.done ? Math.round(j.success / j.done * 100) + "%" : "—";
    var head = '<div class="statrow" style="margin-bottom:9px">' +
      '<div class="statkv"><div class="k">在途</div><div class="v">' + (j.in_flight || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">排队</div><div class="v">' + (j.queued || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">完成</div><div class="v">' + (j.done || 0) + "/" + j.meters_total + "</div></div>" +
      '<div class="statkv"><div class="k">成功</div><div class="v v-ok">' + (j.success || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">失败</div><div class="v v-fail">' + (j.failed || 0) + "</div></div>" +
      '<div class="statkv"><div class="k">成功率</div><div class="v">' + rate + "</div></div>" +
      '<span class="chip chip--ghost mono">' + esc(j.afn_fn || "") + " · 并发 " + j.max_concurrent + "</span>" +
      (j.finished ? '<span class="badge badge--idle">已结束</span>' : '<span class="badge badge--wait">运行中</span>') +
      "</div>";
    var rows = (j.rows || []).slice().reverse();
    var tbl = rows.length
      ? '<table class="rtab"><thead><tr><th>时间</th><th>表地址</th><th>AFN/Fn</th><th class="num">耗时ms</th><th>结果</th></tr></thead><tbody>' +
        rows.map(function (x) {
          var st = x.status === "success" ? '<span class="badge badge--ok">成功</span>'
            : x.status === "deny" ? '<span class="badge badge--deny">' + esc(x.deny_code || "否认") + " " + esc(x.deny_text || "") + "</span>"
            : x.status === "timeout" ? '<span class="badge badge--idle">超时</span>'
            : '<span class="badge badge--idle">' + esc(x.status) + (x.note ? " · " + esc(x.note) : "") + "</span>";
          return "<tr><td class='mono'>" + esc((x.ts || "").replace("T", " ").slice(0, 19)) + "</td><td class='mono'>" + esc(x.meter) + "</td>" +
            "<td class='mono'>" + esc(x.afn_fn) + "</td><td class='num'>" + x.elapsed_ms + "</td><td>" + st + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<div class="empty" style="height:60px"><p>等待第一块表的应答…</p></div>';
    $("#bcStatus").innerHTML = head + tbl;
  }

  /* ---- 主动上报分类型（G6） ---- */
  function renderReports(d) {
    var b = d.buckets || {};
    var power = b.F5_power || { name: "停复电事件", count: 0, items: [] };
    var mclt = b.F230 || { name: "分钟采集数据上报", count: 0, items: [] };   // REQS-0037
    var html = '<div class="statrow" style="margin-bottom:9px">' +
      '<div class="statkv"><div class="k">上报总数</div><div class="v">' + (d.total || 0) + "</div></div>" +
      ["F1", "F2", "F3", "F4", "F5"].map(function (k) {
        return '<span class="chip"><span class="mono">' + k + "</span> " + esc((b[k] || {}).name || "") + " × " + ((b[k] || {}).count || 0) + "</span>";
      }).join("") +
      '<span class="chip chip--ac">停复电 × ' + power.count + "</span>" +
      '<span class="chip chip--ac" id="mcltF230Chip">F230 分钟采集 × ' + mclt.count + "</span>" +
      "</div>";
    // 停复电单独一表
    html += '<div class="bkt-card"><div class="bkt-h">停复电事件（06H-F5 · 协议类型 04H）<span class="cnt">' + power.count + "</span></div>";
    html += power.items.length
      ? '<table class="rtab"><thead><tr><th>时间</th><th>表地址</th><th>事件</th><th>摘要</th></tr></thead><tbody>' +
        power.items.map(function (x) {
          var ev = x.event === "停电" ? '<span class="badge badge--deny">停电</span>'
            : x.event === "复电" ? '<span class="badge badge--ok">复电</span>'
            : '<span class="badge badge--idle">' + esc(x.event || "—") + "</span>";
          return "<tr><td class='mono'>" + esc((x.ts || "").replace("T", " ").slice(0, 19)) + "</td><td class='mono'>" + esc(x.meter) + "</td><td>" + ev + "</td><td class='mono' style='max-width:280px;overflow:hidden;text-overflow:ellipsis'>" + esc(x.summary) + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<div class="empty" style="height:52px"><p>暂无停复电事件（真机停电/复电后到达）</p></div>';
    html += "</div>";
    // REQS-0037：分钟采集（06H-F230）单独一表（仿停复电特例：任务号/协议/采集时间/从节点/长度，点行看详情）
    html += '<div class="bkt-card" id="mcltF230Card"><div class="bkt-h">分钟采集数据上报（06H-F230）<span class="cnt">' + mclt.count + "</span></div>";
    html += mclt.items.length
      ? '<table class="rtab" id="mcltF230Table"><thead><tr><th>时间</th><th>任务号</th><th>协议</th><th>采集时间</th><th>从节点</th><th class="num">长度</th></tr></thead><tbody>' +
        mclt.items.map(function (x, i) {
          return "<tr class='mclt-f230-row' data-i='" + i + "'><td class='mono'>" + esc((x.ts || "").replace("T", " ").slice(0, 19)) + "</td>" +
            "<td class='mono'>" + esc(x.task_id) + "</td><td>" + esc(x.proto) + "</td>" +
            "<td class='mono'>" + esc(x.collect_time || "—") + "</td><td class='mono'>" + esc(x.meter || "—") + "</td>" +
            "<td class='num'>" + esc(x.data_len == null ? "—" : x.data_len) + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<div class="empty" style="height:52px"><p>暂无 F230 存数（配任务后按周期上报；点「分钟采集」页签配任务）</p></div>';
    html += "</div>";
    // 其余类型各一表
    ["F1", "F2", "F3", "F4", "F5"].forEach(function (k) {
      var bk = b[k] || { count: 0, items: [] };
      html += '<div class="bkt-card"><div class="bkt-h">06H-' + k + " " + esc(bk.name || "") + '<span class="cnt">' + bk.count + "</span></div>";
      html += bk.items.length
        ? '<table class="rtab"><thead><tr><th>时间</th><th>地址</th><th>摘要</th></tr></thead><tbody>' +
          bk.items.slice(0, 30).map(function (x) {
            return "<tr><td class='mono'>" + esc((x.ts || "").replace("T", " ").slice(0, 19)) + "</td><td class='mono'>" + esc(x.meter) + "</td><td class='mono' style='max-width:320px;overflow:hidden;text-overflow:ellipsis'>" + esc(x.summary) + "</td></tr>";
          }).join("") + "</tbody></table>"
        : '<div class="empty" style="height:44px"><p>暂无</p></div>';
      html += "</div>";
    });
    $("#pBodyReports").innerHTML = html;
    $("#panelMeta").textContent = "上报分桶 · 总数 " + (d.total || 0);
    // F230 行点击 → /store/mclt 明细（含 frame_id 证据链 + 内嵌帧数）
    Array.prototype.forEach.call(document.querySelectorAll("#mcltF230Table .mclt-f230-row"), function (tr) {
      tr.addEventListener("click", function () {
        var item = (b.F230 || {}).items ? b.F230.items[+tr.dataset.i] : null;
        if (item) showMcltDetail(item);
      });
    });
  }

  function loadReports() {
    api("/api/simcon/report_buckets?limit=300").then(renderReports).catch(function (e) {
      $("#pBodyReports").innerHTML = '<div class="empty" style="height:80px"><p>读取失败：' + esc(e.message) + "</p></div>";
    });
  }


  /* ---- 深化应用（REQS-0030）：档案→在网→勾选→并发下发→周期统计 ---- */
  var deepTimer = null, deepStatsPeriod = "15m";

  function renderDeepPanel() {
    $("#dpBody").innerHTML =
      '<div class="pbody-row">' +
        '<button class="btn btn--sm btn--primary" id="dpQueryArchive">① 查档案</button>' +
        '<button class="btn btn--sm btn--ghost" id="dpQueryOnline">查在网</button>' +
        '<button class="btn btn--sm btn--ghost" id="dpExport">导出 Excel</button>' +
        '<span class="chip chip--ghost" id="dpArchiveMeta">临时档案 · 每次从模块实时获取，复位不保存</span>' +
      "</div>" +
      '<div class="pbody-row">' +
        '<span class="hint">勾选过滤</span>' +
        '<select class="mini-in" id="dpFOnline"><option value="">在网：全部</option><option value="1">仅在线</option><option value="0">仅离线</option></select>' +
        '<span class="hint">并发数</span><input class="mini-in" id="dpMax" value="5" style="width:52px" title="1~20；CCO 上限 20，超限否认 109（前端不拦截，后置校验）">' +
        '<button class="btn btn--sm btn--primary" id="dpStart">② 下发并发抄表（勾选表）</button>' +
        '<span class="chip chip--ghost" id="dpSelMeta">已选 0</span>' +
      "</div>" +
      '<div id="dpTable"><div class="empty" style="height:80px"><p>点击「① 查档案」后此处显示模块表格（地址/信号品质/中继级别/在网）</p></div></div>' +
      '<div class="pbody-row" style="margin-top:9px">' +
        '<span class="hint">统计周期</span>' +
        '<select class="mini-in" id="dpPeriod"><option value="15m">15 分钟</option><option value="30s">30 秒</option><option value="5m">5 分钟</option><option value="1h">1 小时</option></select>' +
        '<button class="btn btn--sm btn--ghost" id="dpStatsBtn">③ 刷新统计</button>' +
        '<span class="chip chip--ghost">AI 接口：/api/simcon/batch/stats?period=…</span>' +
      "</div>" +
      '<div id="dpStats"><div class="empty" style="height:60px"><p>按周期聚合：最大并发数 / 成功数成功率 / 平均耗时 / 重复下发</p></div></div>';
    $("#dpQueryArchive").addEventListener("click", queryDeepArchive);
    $("#dpQueryOnline").addEventListener("click", queryDeepOnline);
    $("#dpExport").addEventListener("click", exportDeepArchive);
    $("#dpStart").addEventListener("click", startDeepBatch);
    $("#dpStatsBtn").addEventListener("click", loadDeepStats);
    $("#dpPeriod").addEventListener("change", function () {
      deepStatsPeriod = this.value; loadDeepStats();
    });
  }

  function deepNodes() { return state.deepNodes || []; }

  function deepFiltered() {
    var f = $("#dpFOnline") ? $("#dpFOnline").value : "";
    return deepNodes().filter(function (n) {
      return f === "" || String(n.online ? 1 : 0) === f;
    });
  }

  function renderDeepTable() {
    var nodes = deepFiltered();
    var tbl = nodes.length
      ? '<table class="rtab"><thead><tr><th><input type="checkbox" id="dpAll"></th><th>#</th><th>模块地址</th><th class="num">信号品质</th><th class="num">中继级别</th><th>在网</th></tr></thead><tbody>' +
        nodes.map(function (n, i) {
          return "<tr><td><input type='checkbox' class='dpChk' value='" + esc(n.addr) + "'></td>" +
            "<td class='num'>" + (i + 1) + "</td><td class='mono'>" + esc(n.addr) + "</td>" +
            "<td class='num'>" + n.signal + "</td><td class='num'>" + n.relay + "</td>" +
            "<td>" + (n.online ? '<span class="badge badge--ok">在线</span>' : '<span class="badge badge--idle">离线</span>') + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<div class="empty" style="height:60px"><p>无模块（先查档案，或调整在网过滤）</p></div>';
    $("#dpTable").innerHTML = tbl;
    updateDeepSel();
    var all = $("#dpAll");
    if (all) all.addEventListener("change", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".dpChk"), function (c) { c.checked = all.checked; });
      updateDeepSel();
    });
    Array.prototype.forEach.call(document.querySelectorAll(".dpChk"), function (c) {
      c.addEventListener("change", updateDeepSel);
    });
  }

  function updateDeepSel() {
    var m = $("#dpSelMeta");
    if (m) m.textContent = "已选 " + document.querySelectorAll(".dpChk:checked").length + " / 共 " + deepFiltered().length;
  }

  function queryDeepArchive() {
    $("#dpArchiveMeta").textContent = "查档案中…（10H-F2 构帧下发）";
    api("/api/simcon/archive/query?count=200").then(function (d) {
      state.deepNodes = d.nodes || [];
      $("#dpArchiveMeta").textContent = "档案 " + (d.session ? d.session.total : state.deepNodes.length) +
        " 个 · 获取于 " + (d.session && d.session.fetched_at ? d.session.fetched_at.slice(0, 19).replace("T", " ") : "—") + " · 临时存储";
      renderDeepTable();
    }).catch(function (e) {
      $("#dpArchiveMeta").textContent = "查档案失败：" + e.message;
    });
  }

  function queryDeepOnline() {
    $("#dpArchiveMeta").textContent = "查在网中…（10H-F1）";
    api("/api/simcon/online").then(function (d) {
      $("#dpArchiveMeta").textContent = "在网规模 " + d.total + " / 容量 " + d.capacity + "（10H-F1 网络口径）";
    }).catch(function (e) {
      $("#dpArchiveMeta").textContent = "查在网失败：" + e.message;
    });
  }

  function exportDeepArchive() {
    window.open("/api/simcon/archive/export.xlsx", "_blank");
    $("#dpArchiveMeta").textContent = "已触发导出（文件在 data/runtime/，浏览器直接下载）";
  }

  function startDeepBatch() {
    var meters = Array.prototype.map.call(document.querySelectorAll(".dpChk:checked"), function (c) { return c.value; });
    if (!meters.length) { banner("请先勾选模块（可按在网状态过滤）"); return; }
    var maxc = parseInt($("#dpMax").value, 10) || 5;
    var body = {
      meters: meters, max_concurrent: maxc, mode: "batch",
      protocol_type: 2, profile: $("#profileSel").value,
    };
    api("/api/simcon/batch_read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (j) {
        banner(null);
        batchJobId = j.job_id;
        renderBatchStatus(j);
        switchPanel("batch", true);
        if (!batchTimer) batchTimer = setInterval(pollBatch, 1000);
        loadDeepStats();
      }).catch(function (e) { banner("下发失败：" + e.message); });
  }

  function renderDeepStats(d) {
    var rows = (d.buckets || []).slice(-12).reverse();
    var tbl = rows.length
      ? '<table class="rtab"><thead><tr><th>周期起点</th><th class="num">下发</th><th class="num">成功</th><th class="num">成功率</th><th class="num">最大并发</th><th class="num">平均耗时ms</th><th class="num">重复下发</th></tr></thead><tbody>' +
        rows.map(function (b) {
          var rate = b.dispatch_count ? Math.round(b.success_count / b.dispatch_count * 100) + "%" : "—";
          return "<tr><td class='mono'>" + esc(b.period_start.replace("T", " ").slice(0, 19)) + "</td>" +
            "<td class='num'>" + b.dispatch_count + "</td><td class='num'>" + b.success_count + "</td>" +
            "<td class='num'>" + rate + "</td><td class='num'>" + b.max_concurrent + "</td>" +
            "<td class='num'>" + (b.avg_duration_ms != null ? b.avg_duration_ms : "—") + "</td>" +
            "<td class='num'>" + b.duplicate_count + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<div class="empty" style="height:52px"><p>暂无统计（下发并发抄表后按周期聚合）</p></div>';
    $("#dpStats").innerHTML = '<span class="hint" style="display:block;margin:4px 0">任务数 ' + (d.jobs_count || 0) + " · 尝试 " + (d.attempts_total || 0) + " · 周期 " + (d.period_seconds || 900) + "s</span>" + tbl;
  }

  function loadDeepStats() {
    api("/api/simcon/batch/stats?period=" + encodeURIComponent(deepStatsPeriod)).then(renderDeepStats).catch(function (e) {
      $("#dpStats").innerHTML = '<div class="empty" style="height:52px"><p>统计读取失败：' + esc(e.message) + "</p></div>";
    });
  }

  /* ---- 面板页签 ---- */
  function stopPanelTimers() {
    if (readTimer) { clearInterval(readTimer); readTimer = null; }
    if (rptTimer) { clearInterval(rptTimer); rptTimer = null; }
    if (batchTimer) { clearInterval(batchTimer); batchTimer = null; }
    if (deepTimer) { clearInterval(deepTimer); deepTimer = null; }
  }

  function switchPanel(tab, force) {
    if (panelTab === tab && !force) return;
    panelTab = tab;
    Array.prototype.forEach.call(document.querySelectorAll("#segPanel button"), function (x) {
      x.classList.toggle("on", x.dataset.p === tab);
    });
    $("#pBodyPair").style.display = tab === "pair" ? "" : "none";
    $("#pBodyReadings").style.display = tab === "readings" ? "" : "none";
    $("#pBodyBatch").style.display = tab === "batch" ? "" : "none";
    $("#pBodyReports").style.display = tab === "reports" ? "" : "none";
    stopPanelTimers();
    if (tab === "readings") { loadReadings(); readTimer = setInterval(loadReadings, 3000); }
    if (tab === "reports") { loadReports(); rptTimer = setInterval(loadReports, 5000); }
    if (tab === "batch") {
      if (!$("#bcMeters")) renderBatchForm();
      if (batchJobId && !batchTimer) { pollBatch(); batchTimer = setInterval(pollBatch, 1000); }
    }
  }

  $("#segPanel").addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (btn) switchPanel(btn.dataset.p);
  });
  $("#btnPanelToggle").addEventListener("click", function () {
    $("#panel").classList.toggle("fold");
    var folded = $("#panel").classList.contains("fold");
    $("#btnPanelToggle").textContent = folded ? "▸" : "▾";
    if (!folded) switchPanel(panelTab, true);
    else stopPanelTimers();
  });

  /* ================= REQS-0037：分钟采集页签 =================
   * 配任务（11H-F231/F232）→ 等存数（wait_mclt_stored）→ 读数（read_mclt_11e3）。
   * 构帧全部走后端（/build 走 scenario_codec；11E3 走 /mclt/11e3_preview 走
   * mclt_e3），前端不手拼字节；场景执行走 POST /api/simcon/verify（verify 任务面）。
   */
  var MC_SCENARIO_ID = "chongqing_mclt_recheck_0037";

  /* OAD 列表 textarea → [{item, reply_len}]（8 位 hex OAD + 回复长度字节） */
  function mcParseOads(text) {
    return String(text || "").split(/[\n,，;；]+/).map(function (line) {
      var seg = line.trim().split(/[\s:：=]+/).filter(Boolean);
      if (!seg.length) return null;
      var oad = seg[0].replace(/[^0-9a-fA-F]/g, "");
      if (!oad) return null;
      return { item: oad, reply_len: seg[1] ? (parseInt(seg[1], 10) || 0) : 0, meter_type: 0 };
    }).filter(Boolean);
  }

  /* 组 F231/F232 send 描述（字段语义 = scenario_codec 示例场景 / adapter_10376 编码器） */
  function mcTaskSend(fnNo) {
    var taskNo = parseInt($("#mcTaskNo").value, 10);
    if (isNaN(taskNo) || taskNo < 0 || taskNo > 15) throw new Error("任务号需 0~15");
    if (fnNo === 231) {
      var cycle = parseInt($("#mcCycle").value, 10);
      if (isNaN(cycle) || cycle < 1 || cycle > 1440) throw new Error("周期分钟需 1~1440");
      var items = mcParseOads($("#mcOads").value);
      if (!items.length) throw new Error("请填写至少一条 OAD（每行：OAD hex + 回复长度）");
      return {
        afn: "11", fn: "F231",
        params: { task_no: taskNo, action: "enable",
                  protocol: parseInt($("#mcProto").value, 10),
                  cycle_min: cycle, items: items },
      };
    }
    var meters = $("#mcMeters").value.split(/[^0-9]+/).filter(function (x) { return x.length === 12; });
    if (!meters.length) throw new Error("F232 需至少一个 12 位 BCD 档案地址");
    return { afn: "11", fn: "F232", params: { task_no: taskNo, meters: meters } };
  }

  /* 构帧预览（POST /api/simcon/build，scenario_codec 只算不发） */
  function mcPreviewBuild(send) {
    return api("/api/simcon/build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ afn: send.afn, fn: send.fn, params: send.params, profile: $("#profileSel").value }),
    });
  }

  /* 经单步路径下发（POST /api/simcon/step：串口未开自动打开；F231/F232 为 afn/fn 帧） */
  function mcSendStep(send, label) {
    return api("/api/simcon/step", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        send: send, profile: $("#profileSel").value,
        expect: { format: "local", afn: 17, fn: send.fn === "F231" ? 231 : 232 },
        enable_responder: true, name: label,
      }),
    });
  }

  function mcShowHex(obj, target) {
    $(target).innerHTML = colorHex(obj.hex);
    return obj;
  }

  /* 渲染步骤列表：步骤 dict 逐个展示（wait_mclt_stored 超时/轮询、read_mclt_11e3 auto/显式冻结时刻） */
  function mcRenderSteps(steps) {
    var box = $("#mcSteps");
    if (!steps || !steps.length) {
      box.innerHTML = '<div class="empty" style="height:70px"><p>无步骤</p></div>';
      return;
    }
    box.innerHTML = steps.map(function (s, i) {
      var name = s.name || s.send && ("发送 " + (s.send.afn || "") + " " + (s.send.fn || "")) || ("步骤 " + (i + 1));
      var kind = s.wait_mclt_stored ? "wait_mclt_stored"
        : s.read_mclt_11e3 ? "read_mclt_11e3"
        : s.send ? ("send " + (s.send.afn || "") + "-" + (s.send.fn || ""))
        : Object.keys(s).filter(function (k) { return k !== "name"; })[0] || "step";
      var kvs = [];
      if (s.wait_mclt_stored) {
        var w = s.wait_mclt_stored;
        kvs.push("task_id=" + w.task_id, "timeout_s=" + (w.timeout_s != null ? w.timeout_s : 900), "poll_s=" + (w.poll_s != null ? w.poll_s : 5));
        if (w.proto != null) kvs.push("proto=" + w.proto);
      }
      if (s.read_mclt_11e3) {
        var r = s.read_mclt_11e3;
        kvs.push("task_id=" + r.task_id, "freeze_time=" + (r.freeze_time || "auto"), "protocol=" + (r.protocol || 3));
      }
      if (s.expect_timeout != null) kvs.push("expect_timeout=" + s.expect_timeout);
      if (s.expect && s.expect.afn != null) kvs.push("expect afn=" + s.expect.afn + (s.expect.task_id != null ? "/task_id=" + s.expect.task_id : ""));
      if (s.send && s.send.params) {
        kvs.push("params " + JSON.stringify(s.send.params));
      }
      return '<div class="mc-step"><span class="no">' + (i + 1) + '</span><div class="body">' +
        '<div class="nm">' + esc(name) + '</div><div class="kv">' + esc(kind) + (kvs.length ? " · " + esc(kvs.join(" · ")) : "") + "</div></div></div>";
    }).join("");
  }

  /* 用户自选 task 组合 → wait+read 步骤对（语义对齐 scenario_codec 构建器） */
  function mcBuildTaskSeqSteps() {
    var ids = ($("#mcTaskSeq").value || "").split(/[^0-9]+/).filter(Boolean).map(function (x) { return parseInt(x, 10); });
    if (!ids.length) throw new Error("请填任务号列表，如 0,1,15");
    var proto = parseInt($("#mcSeqProto").value, 10);
    var steps = [];
    ids.forEach(function (tid) {
      steps.push({
        name: "等待 task " + tid + " 存数 (06H-F230)",
        wait_mclt_stored: { task_id: tid, proto: proto, timeout_s: 900, poll_s: 5 },
      });
      steps.push({
        name: "按冻结时刻读数 (11E3, task " + tid + ", auto)",
        read_mclt_11e3: { task_id: tid, freeze_time: "auto", protocol: proto },
        expect: { afn: "11E3", task_id: tid },
        expect_timeout: 60,
      });
    });
    mcRenderSteps(steps);
    $("#mcScenarioMeta").textContent = "已生成 " + steps.length + " 步（" + ids.length + " 组 wait+read）· 点「运行场景」执行";
    return steps;
  }

  /* POST /api/simcon/verify：verify 任务面执行 wait/read 语义步骤序列 */
  function mcRunSteps(steps) {
    var body = {
      id: "ui.mclt." + Date.now().toString(36),
      profile: $("#profileSel").value,
      enable_responder: true,
      fail_fast: false,
      steps: steps,
    };
    $("#mcScenarioMeta").textContent = "执行中…（wait 步骤最长等 timeout_s）";
    return api("/api/simcon/verify", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(function (r) {
      var s = r.summary || {};
      $("#mcScenarioMeta").textContent = "结论 " + (s.verdict || "—") + " · 通过 " + (s.pass || 0) + "/" + (s.total || 0) +
        (r.run_id ? " · run " + r.run_id : "");
      mcRenderResults(r);
      loadReports();
      return r;
    });
  }

  /* verify 结果按步骤渲染（含失败原因） */
  function mcRenderResults(r) {
    var steps = r.steps || [];
    if (!steps.length) return;
    var box = $("#mcSteps");
    box.insertAdjacentHTML("beforeend",
      '<div class="mc-step"><span class="no">✓</span><div class="body"><div class="nm">执行结果</div>' +
      steps.map(function (s) {
        var badge = s.result === "pass" ? '<span class="badge badge--ok">通过</span>'
          : '<span class="badge badge--deny">失败</span>';
        return '<div class="kv" style="margin-top:4px">' + badge + " " + esc(s.name || "") +
          '<span class="hint"> ' + esc(s.reason || "") + "</span></div>";
      }).join("") + "</div></div>");
  }

  /* 11E3 构帧预览（POST /api/simcon/mclt/11e3_preview → mclt_e3.build_11e3_down） */
  function mcPreview11E3() {
    var taskNo = parseInt($("#mcReadTaskNo").value, 10);
    if (isNaN(taskNo) || taskNo < 0 || taskNo > 15) return Promise.reject(new Error("任务号需 0~15"));
    var fz = $("#mcFreeze").value.trim();
    if (!fz) return Promise.reject(new Error("请填冻结时刻（或点「取最近存数」）"));
    return api("/api/simcon/mclt/11e3_preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: taskNo, freeze_time: fz,
                             protocol: parseInt($("#mcReadProto").value, 10),
                             ssn: parseInt($("#mcSsn").value, 10) || 1 }),
    });
  }

  /* F230 明细弹窗：report_buckets item + /store/mclt 行（frame_id 证据链 + 内嵌帧数） */
  function showMcltDetail(item) {
    var mask = document.createElement("div");
    mask.className = "mclt-mask";
    var kv = function (k, v) {
      return '<div class="pkv"><span class="k">' + esc(k) + '</span><span class="v mono">' + esc(v == null ? "—" : v) + "</span></div>";
    };
    var payload = item.payload || {};
    var items = payload.items || [];
    mask.innerHTML =
      '<div class="mclt-dlg"><div class="d-h">06H-F230 明细 · 任务 ' + esc(item.task_id) +
      '<span class="sp"></span><button class="btn btn--sm btn--ghost" data-close>关闭</button></div>' +
      '<div class="d-body">' +
      kv("上报时间", (item.ts || "").replace("T", " ")) + kv("任务号", item.task_id) + kv("协议", item.proto) +
      kv("采集（冻结）时间", item.collect_time) + kv("从节点地址", item.meter) +
      kv("内嵌帧数", item.nested_count) + kv("报文长度", item.data_len) +
      '<div><div class="hint" style="margin-bottom:4px">报文 hex（内层 645/698）</div><div class="hexwrap"><div class="hex mono">' + esc(item.summary || "") + "</div></div></div>" +
      (items.length
        ? '<div><div class="hint" style="margin-bottom:4px">内嵌帧 × ' + items.length + "</div>" +
          '<table class="rtab"><thead><tr><th>#</th><th>地址</th><th>摘要</th></tr></thead><tbody>' +
          items.map(function (x, i) {
            return "<tr><td class='num'>" + (i + 1) + "</td><td class='mono'>" + esc(x.meter || x.addr || "—") + "</td><td class='mono'>" + esc((x.summary || x.payload_hex || "").slice(0, 48)) + "</td></tr>";
          }).join("") + "</tbody></table></div>"
        : "") +
      '<div class="hint">完整证据链：frame_id 回溯 frame_log（GET /api/simcon/store/mclt 行内 frame_id）</div>' +
      "</div></div>";
    mask.addEventListener("click", function (e) {
      if (e.target === mask || e.target.closest("[data-close]")) mask.remove();
    });
    document.body.appendChild(mask);
  }

  /* F230 存数明细表（分钟采集页签内，/store/mclt） */
  function loadMcltStore() {
    api("/api/simcon/store/mclt?limit=100").then(function (d) {
      var rows = d.items || [];
      var body = $("#mcF230Body");
      if (!rows.length) {
        body.innerHTML = '<div class="empty" style="height:70px"><p>暂无 F230 存数记录</p></div>';
        return;
      }
      body.innerHTML = '<table class="rtab"><thead><tr><th>时间</th><th>任务号</th><th>协议</th><th>采集时间</th><th>从节点</th><th class="num">长度</th><th class="num">帧数</th></tr></thead><tbody>' +
        rows.map(function (x) {
          return "<tr><td class='mono'>" + esc((x.ts || "").replace("T", " ").slice(0, 19)) + "</td>" +
            "<td class='mono'>" + esc(x.task_id || "—") + "</td><td class='mono'>" + esc(x.proto_type || "—") + "</td>" +
            "<td class='mono'>" + esc(x.collect_time || "—") + "</td><td class='mono'>" + esc(x.node_addr || "—") + "</td>" +
            "<td class='num'>" + esc(x.data_len == null ? "—" : x.data_len) + "</td>" +
            "<td class='num'>" + esc(x.nested_count == null ? "—" : x.nested_count) + "</td></tr>";
        }).join("") + "</tbody></table>";
    }).catch(function (e) {
      $("#mcF230Body").innerHTML = '<div class="empty" style="height:70px"><p>读取失败：' + esc(e.message) + "</p></div>";
    });
  }

  /* 场景列表：默认选中 0037 复测场景 */
  function loadMcltScenarios() {
    api("/api/scenarios").then(function (list) {
      var sel = $("#mcScenarioSel");
      var arr = (list || []).filter(function (s) { return s && s.id; });
      sel.innerHTML = arr.map(function (s) {
        return '<option value="' + esc(s.id) + '">' + esc(s.name || s.id) + "</option>";
      }).join("") || '<option value="">无场景</option>';
      if (arr.some(function (s) { return s.id === MC_SCENARIO_ID; })) sel.value = MC_SCENARIO_ID;
      $("#mcScenarioMeta").textContent = arr.length + " 个场景";
    }).catch(function (e) {
      $("#mcScenarioMeta").textContent = "场景加载失败：" + e.message;
    });
  }

  $("#mcBuildCfg").addEventListener("click", function () {
    try {
      var send = mcTaskSend(231);
      mcPreviewBuild(send).then(function (r) { mcShowHex(r, "#mcPrevHex"); })
        .catch(function (e) { banner("构帧失败：" + e.message); });
    } catch (e) { banner(e.message); }
  });

  $("#mcSendCfg").addEventListener("click", function () {
    var btn = $("#mcSendCfg");
    try {
      var send = mcTaskSend(231);
      btn.disabled = true;
      mcPreviewBuild(send).then(function (r) {
        mcShowHex(r, "#mcPrevHex");
        return mcSendStep(send, "分钟采集-F231");
      }).then(function () {
        banner(null);
        banner("F231 已下发（任务 " + $("#mcTaskNo").value + "）");
        btn.disabled = false;
      }).catch(function (e) { banner("下发失败：" + e.message); btn.disabled = false; });
    } catch (e) { banner(e.message); }
  });

  $("#mcSendF232").addEventListener("click", function () {
    var btn = $("#mcSendF232");
    try {
      var send = mcTaskSend(232);
      btn.disabled = true;
      mcPreviewBuild(send).then(function (r) {
        mcShowHex(r, "#mcPrevHex");
        return mcSendStep(send, "分钟采集-F232");
      }).then(function () {
        banner(null);
        banner("F232 已下发（任务 " + $("#mcTaskNo").value + "）");
        btn.disabled = false;
      }).catch(function (e) { banner("下发失败：" + e.message); btn.disabled = false; });
    } catch (e) { banner(e.message); }
  });

  /* 「取最近存数」：/store/mclt 最新一条该任务的 collect_time 填入冻结时刻 */
  $("#mcFreezeAuto").addEventListener("click", function () {
    var tid = String(parseInt($("#mcReadTaskNo").value, 10) || "");
    api("/api/simcon/store/mclt?limit=100").then(function (d) {
      var rows = (d.items || []).filter(function (x) { return String(x.task_id) === tid && x.collect_time; });
      if (!rows.length) { banner("任务 " + tid + " 暂无 F230 存数（可先运行场景等存数）"); return; }
      $("#mcFreeze").value = rows[0].collect_time;
      banner(null);
    }).catch(function (e) { banner(e.message); });
  });

  $("#mcBuild11E3").addEventListener("click", function () {
    mcPreview11E3().then(function (r) {
      mcShowHex(r, "#mc11E3Hex");
    }).catch(function (e) {
      $("#mc11E3Hex").innerHTML = "<span style='color:var(--color-status-fail)'>" + esc(e.message) + "</span>";
    });
  });

  $("#mcCopy11E3").addEventListener("click", function () {
    var hex = $("#mc11E3Hex").textContent || "";
    if (!hex.trim()) { banner("请先构帧预览"); return; }
    var text = hex.replace(/\s+/g, "");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { banner("11E3 hex 已复制（" + (text.length / 2) + " 字节）"); })
        .catch(function () { banner("复制失败，请手动选择文本"); });
    } else {
      banner("剪贴板不可用，请手动选择文本复制");
    }
  });

  $("#mcScenarioLoad").addEventListener("click", function () {
    var id = $("#mcScenarioSel").value;
    if (!id) { banner("请选择场景"); return; }
    api("/api/scenarios/" + encodeURIComponent(id)).then(function (s) {
      var steps = s.steps || (((s.steps_template || {}).task_1) || []);
      mcRenderSteps(steps);
      $("#mcScenarioMeta").textContent = (s.name || s.id) + " · " + steps.length + " 步（含 steps_template 可换 task 组合）";
    }).catch(function (e) { banner("场景加载失败：" + e.message); });
  });

  $("#mcSeqBuild").addEventListener("click", function () {
    try { mcBuildTaskSeqSteps(); } catch (e) { banner(e.message); }
  });

  $("#mcScenarioRun").addEventListener("click", function () {
    var id = $("#mcScenarioSel").value;
    if (!id) { banner("请选择场景"); return; }
    api("/api/scenarios/" + encodeURIComponent(id)).then(function (s) {
      var steps = s.steps || (((s.steps_template || {}).task_1) || []);
      if (!steps.length) throw new Error("场景无 steps");
      return mcRunSteps(steps);
    }).catch(function (e) { banner("场景执行失败：" + e.message); });
  });

  $("#mcF230Refresh").addEventListener("click", loadMcltStore);

  /* ================= 初始化 ================= */
  loadDict();
  loadPorts();
  loadResponders();
  loadExpectRules();
  renderBatchForm();
  renderDeepPanel();
  loadDeepStats();
  deepTimer = setInterval(loadDeepStats, 10000);
  loadMcltScenarios();
  loadMcltStore();
})();
