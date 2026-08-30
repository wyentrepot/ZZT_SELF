/* 场景脚本页（reqs/0010 P3，只读浏览 + 试跑跳转）
 * 数据源：/api/scenarios（编排路由）+ /api/scenarios/{id}/task（激励任务原始 JSON）。
 */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };

  var state = { scenarios: [], cur: -1, task: null, curStep: -1 };

  function banner(msg) {
    var el = $("#banner");
    if (!msg) { el.classList.remove("show"); el.textContent = ""; return; }
    el.textContent = msg;
    el.classList.add("show");
  }

  function api(path) {
    return fetch(path).then(function (resp) {
      if (!resp.ok) return resp.json().catch(function () { return {}; }).then(function (body) {
        throw new Error(body.detail || ("HTTP " + resp.status));
      });
      return resp.json();
    });
  }

  function loadScenarios() {
    api("/api/scenarios").then(function (list) {
      state.scenarios = list || [];
      if (!state.scenarios.length) {
        $("#scList").innerHTML = '<div class="empty"><p>无场景</p></div>';
        return;
      }
      $("#scList").innerHTML = state.scenarios.map(function (s, i) {
        var flowN = (s.expected_flow || []).length;
        return '<button class="sc-card" data-s="' + i + '">' +
          '<span class="sc-r"><span class="dot" style="color:var(--color-fg-dim)"></span>' + esc(s.name || s.id) +
          '<span class="mono">' + esc(s.id) + '</span></span>' +
          '<span class="sc-meta">' +
          '<span class="chip" style="height:17px;font-size:10px">' + flowN + " 事件步</span>" +
          '<span class="chip" style="height:17px;font-size:10px">' + esc(s.module || "cco") + "</span>" +
          ((s.stimulus || {}).task_file ? '<span class="chip chip--ghost" style="height:17px;font-size:10px">' + esc(s.stimulus.task_file) + "</span>" : "") +
          "</span></button>";
      }).join("");
      Array.prototype.forEach.call(document.querySelectorAll("#scList .sc-card"), function (el) {
        el.addEventListener("click", function () { selectScenario(+el.dataset.s); });
      });
      selectScenario(0);
    }).catch(function (err) {
      $("#scList").innerHTML = '<div class="empty"><p>场景加载失败</p></div>';
      banner(err.message);
    });
  }

  function selectScenario(i) {
    state.cur = i;
    state.curStep = -1;
    state.task = null;
    var s = state.scenarios[i];
    Array.prototype.forEach.call(document.querySelectorAll("#scList .sc-card"), function (el) {
      var on = +el.dataset.s === i;
      el.classList.toggle("on", on);
      el.querySelector(".dot").style.color = on ? "var(--color-accent)" : "var(--color-fg-dim)";
    });
    api("/api/scenarios/" + encodeURIComponent(s.id) + "/task").then(function (task) {
      state.task = task;
      renderEdit();
    }).catch(function (err) {
      state.task = null;
      renderEdit();
      if (String(err.message).indexOf("未声明") < 0) banner(err.message);
    });
  }

  function renderEdit() {
    var s = state.scenarios[state.cur];
    var flow = s.expected_flow || [];
    var steps = (state.task && state.task.steps) || [];
    var responders = (state.task && state.task.responders) || [];
    var monitor = s.monitor || {};
    $("#edBody").innerHTML =
      '<div class="ed-head"><div class="ed-r1"><b>' + esc(s.name || s.id) + "</b>" +
      '<span class="chip chip--ac" style="margin-left:auto">' + esc(s.module || "cco") + "</span></div>" +
      ((state.task && state.task.description)
        ? '<div class="hint" style="line-height:1.6">' + esc(state.task.description) + "</div>" : "") +
      "</div>" +
      '<div class="ed-label">期望流程 · expected_flow（事件步）</div>' +
      (flow.length ? flow.map(function (f, i) {
        return '<div class="flow-it"><span class="flow-no">' + String(i + 1).padStart(2, "0") + "</span>" +
          '<span class="flow-nm">' + esc(f.step || "—") + "</span>" +
          '<span class="flow-ev">' + esc(f.event_type || "—") + "</span>" +
          '<span class="flow-ms">within ' + (f.within_ms != null ? (f.within_ms >= 1000 ? (f.within_ms / 1000) + "s" : f.within_ms + "ms") : "—") + "</span></div>";
      }).join("") : '<div class="hint" style="padding:2px 12px 8px">未声明期望流程</div>') +
      '<div class="ed-label">激励任务 · stimulus' + (state.task ? "（" + steps.length + " 步）" : "") + "</div>" +
      (steps.length ? steps.map(function (st, i) {
        var send = st.send || {};
        var tag = st.recv_only ? "recv_only" : (st.expect_no_reply ? "expect_no_reply" : "send");
        var afnFn = send.afn != null ? afnTag(send.afn, send.fn) : (st.name || "");
        return '<button class="step" data-st="' + i + '">' +
          '<span class="step-h"><span class="step-no">' + (i + 1) + "</span>" +
          '<span class="step-nm">' + esc(st.name || afnFn) + "</span>" +
          '<span class="chip chip--' + (tag === "send" ? "tx" : "rx") + '" style="height:18px;font-size:10px">' + tag + "</span>" +
          (send.afn != null ? '<span class="chip chip--ghost" style="height:18px;font-size:10px">' + esc(afnFn) + "</span>" : "") +
          "</span></button>";
      }).join("") : '<div class="hint" style="padding:2px 12px 8px">' + (state.task ? "任务无步骤" : "激励任务未加载（场景未声明 task_file）") + "</div>") +
      '<div class="ed-label">内置应答 · responders</div>' +
      (responders.length ? responders.map(function (r) {
        var m = r.match || {}, rp = r.reply || {};
        return '<div class="resp-row"><span class="mono">' + esc(r.id || "—") + '</span><span style="color:var(--color-status-special)">match</span>' +
          '<span class="chip chip--rx" style="height:18px;font-size:10px">' + esc(afnTag(m.afn, m.fn)) + '</span>' +
          '<span style="color:var(--color-status-special)">→ reply</span>' +
          '<span class="chip chip--tx" style="height:18px;font-size:10px">' + esc(afnTag(rp.afn, rp.fn)) + "</span>" +
          (rp.desc ? '<span class="hint" style="margin-left:auto">' + esc(rp.desc) + "</span>" : "") + "</div>";
      }).join("") : '<div class="resp-row" style="color:var(--color-fg-dim)">无任务级应答规则，使用全局内置应答表</div>') +
      '<div class="ed-label">监控 · monitor</div>' +
      '<div class="moni">' +
      (monitor.rules || []).map(function (r) { return '<span class="chip chip--ac">' + esc(r) + "</span>"; }).join("") +
      '<span class="chip">sources: ' + esc((monitor.sources || []).join(", ") || "—") + "</span></div>";
    Array.prototype.forEach.call(document.querySelectorAll("#edBody .step"), function (el) {
      el.addEventListener("click", function () {
        state.curStep = +el.dataset.st;
        Array.prototype.forEach.call(document.querySelectorAll("#edBody .step"), function (x) { x.classList.remove("on"); });
        el.classList.add("on");
        renderStep();
      });
    });
    if (steps.length) {
      var first = document.querySelector("#edBody .step");
      if (first) first.click();
    } else {
      $("#parHead").innerHTML = '<div class="par-t2" style="padding:0">该场景无激励步骤详情。</div>';
      $("#parBody").innerHTML = '<div class="empty"><p>场景 JSON 与期望流程见中间列</p></div>';
    }
  }

  function afnTag(afn, fn) {
    var a = afn == null ? "—" : (typeof afn === "number" ? afn.toString(16).toUpperCase().padStart(2, "0") + "H" : String(afn));
    var f = fn == null ? "" : (typeof fn === "number" ? "F" + fn : String(fn));
    return a + (f ? " " + (f.toUpperCase().indexOf("F") === 0 ? f.toUpperCase() : "F" + f) : "");
  }

  function renderStep() {
    var st = (state.task && state.task.steps)[state.curStep];
    if (!st) return;
    var send = st.send || {};
    var expect = st.expect || {};
    $("#parHead").innerHTML = '<div class="par-t1"><b>' + esc(st.name || "步骤 " + (state.curStep + 1)) + "</b>" +
      (send.afn != null ? "<span class='chip chip--ac'>" + esc(afnTag(send.afn, send.fn)) + "</span>" : "") + "</div>" +
      '<div class="par-t2">语义化步骤（ADR-5）：send 只写 afn/fn + 业务参数，构帧由 scenario_codec 完成；本页只读，执行请到验证工作台。</div>';
    var rows = [];
    Object.keys(send.params || {}).forEach(function (k) {
      rows.push("<tr><td><div class='nm mono' style='color:var(--color-fg-muted)'>" + esc(k) + "</div></td>" +
        "<td><span class='mono'>" + esc(JSON.stringify(send.params[k])) + "</span></td></tr>");
    });
    var expectDesc = [];
    if (expect.afn != null) expectDesc.push("期望应答 " + afnTag(expect.afn, expect.fn));
    if (expect.format) expectDesc.push("format=" + expect.format);
    if (st.expect_timeout != null) expectDesc.push("超时 " + st.expect_timeout + "s");
    $("#parBody").innerHTML =
      (rows.length || send.afn != null
        ? '<div class="card"><div class="card-h">send 参数<span class="hint">' + rows.length + " 项</span></div>" +
          '<div class="card-in"><table class="ft"><thead><tr><th style="width:36%">参数</th><th>取值</th></tr></thead><tbody>' +
          (send.afn != null ? "<tr><td><div class='nm'>afn / fn</div></td><td><span class='mono'>" + esc(afnTag(send.afn, send.fn)) + "</span></td></tr>" : "") +
          rows.join("") + "</tbody></table></div></div>"
        : '<div class="card"><div class="card-h">send</div><div class="card-in"><span class="hint">纯监听步（recv_only / expect_no_reply），不下发报文。</span></div></div>') +
      '<div class="card" style="margin-top:14px"><div class="card-h">expect / 判定<span class="hint">' + esc(expectDesc.join(" · ") || "—") + "</span></div>" +
      '<div class="card-in"><div style="font-size:11.5px;color:var(--color-fg-muted);line-height:1.7">' +
      (expectDesc.length ? "期望：" + esc(expectDesc.join("；")) : "无显式期望——结果由场景执行器按监控规则判定。") +
      "</div></div></div>" +
      '<div class="card" style="margin-top:14px"><div class="card-h">步骤原始 JSON</div>' +
      '<div class="card-in"><div class="jsonwrap">' + esc(JSON.stringify(st, null, 2)) + "</div></div></div>";
  }

  loadScenarios();
})();
