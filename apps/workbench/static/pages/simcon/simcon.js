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
  };

  /* 常用 Fn 的参数模板（业务键名对齐 scenario_codec / adapter_10376 模板） */
  var PARAM_TPL = {
    "11H-F1": { action: "add", addr: "080000000000", protocol: 3 },
    "11H-F2": { addr: "080000000000" },
    "11H-F231": { task_no: 1, action: "enable", protocol: 3, cycle_min: 1,
      items: [{ meter_type: 0, item: "20000201", reply_len: 1 }] },
    "11H-F100": { network_scale: 64 },
    "05H-F1": { addr: "000000000001" },
    "03H-F3": { start: 0, count: 16 },
    "03H-F11": { afn: "11" },
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
    var key = a.code + "-" + f.no;
    var tpl = PARAM_TPL[key];
    var fieldRows = (f.fields || []).map(function (x) {
      return "<tr><td><div class='nm'>" + esc(x.n) + "</div><div class='ds'>" + esc(x.d || "") + "</div></td>" +
        "<td><span class='fmt'>" + esc(x.f || "—") + "</span></td>" +
        "<td><span class='fmt'>" + esc(String(x.b == null ? "—" : x.b)) + "</span></td></tr>";
    }).join("");
    $("#parBody").innerHTML =
      '<div class="card"><div class="card-h">send 业务参数（JSON）<span class="hint">' +
      (tpl ? "已填常用模板" : "按 adapter_10376 模板填写") + "</span></div>" +
      '<div class="card-in"><textarea class="params-ta" id="paramsTa" spellcheck="false">' +
      esc(JSON.stringify(tpl || {}, null, 2)) + "</textarea>" +
      '<div class="hint" style="margin-top:7px">业务键名以 scenario_codec / adapter_10376 模板为准；构帧报错信息会指出缺失参数。</div></div></div>' +
      (fieldRows
        ? '<div class="card" style="margin-top:14px"><div class="card-h">数据单元字段参考<span class="hint">来自协议字典</span></div>' +
          '<div class="card-in"><table class="ft"><thead><tr><th style="width:52%">字段 / 语义</th><th style="width:20%">格式</th><th>字节</th></tr></thead><tbody>' +
          fieldRows + "</tbody></table></div></div>"
        : '<div class="card" style="margin-top:14px"><div class="card-h">数据单元字段</div><div class="card-in"><span class="hint">' +
          (f.todo ? "字段待补：该 Fn 尚无字段表，参数键名请对照 adapter_10376 代码。" : "该命令无数据单元，直接下发即可。") + "</span></div></div>") +
      '<div class="card" style="margin-top:14px"><div class="card-h">帧预览 · 68H|L|C|R|A|AFN|DT|数据|CS|16H<span class="hint">只算不发</span></div>' +
      '<div class="card-in"><div class="hexwrap"><div class="hex" id="prevHex">点击「刷新预览」或修改参数后自动构建</div></div></div></div>';
    $("#paramsTa").addEventListener("input", debounceBuild);
    buildPreview();
  }

  var debounceTimer = null;
  function debounceBuild() { clearTimeout(debounceTimer); debounceTimer = setTimeout(buildPreview, 500); }

  function currentSend() {
    var a = state.afnList[state.curAfn];
    var f = a.fns[state.curFn];
    var params = {};
    try { params = JSON.parse($("#paramsTa").value || "{}"); }
    catch (e) { throw new Error("参数 JSON 非法：" + e.message); }
    return {
      afn: parseInt(a.code, 16),
      fn: parseInt(String(f.no).replace(/^F/i, ""), 10),
      params: params,
    };
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
        var body = $("#trBody");
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
