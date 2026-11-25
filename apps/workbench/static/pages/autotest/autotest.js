/* 自动测试页面逻辑：用例清单 / 链路一键运行 / 选择运行 / job 进度轮询 / 结果与文档。
   全部走 /api/autotest/*（subprocess 同源 autotest/runner.py，与 AI 脚本同一执行路径）。 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var specs = { available: false, cases: [], chains: [] };
  var selected = {};          // case_id -> true
  var tagFilter = null;       // 单选 tag（再点取消）
  var q = "";
  var supportedFilter = "";
  var currentJobId = null;
  var pollTimer = null;
  var drawerCase = null;

  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(function () { t.classList.remove("show"); }, 2400);
  }

  function api(method, path, body) {
    return fetch(path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.detail || (method + " " + path + " -> " + r.status));
        return data;
      });
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // -- 清单 -------------------------------------------------------------
  function loadList() {
    return api("GET", "/api/autotest/list").then(function (data) {
      specs = data;
      renderPill();
      renderChainBar();
      renderTagBar();
      renderTable();
    }).catch(function (e) {
      specs = { available: false, cases: [], chains: [] };
      $("list-wrap").innerHTML = '<div class="empty">清单加载失败：' + esc(e.message) + "</div>";
      renderPill();
    });
  }

  function renderPill() {
    var pill = $("pill"), txt = $("pill-text");
    if (!specs.available) {
      pill.className = "pill";
      txt.textContent = "框架不可用";
      return;
    }
    pill.className = "pill on";
    txt.textContent = specs.cases.length + " 用例 · " + specs.chains.length + " 链路";
  }

  function renderChainBar() {
    var bar = $("chain-bar");
    var html = '<span class="hint" style="align-self:center">链路一键跑：</span>';
    specs.chains.forEach(function (ch) {
      html += '<button class="mini chain-btn" data-chain="' + esc(ch.name) + '" title="' +
        esc(ch.desc) + '">▶ ' + esc(ch.name) + " (" + ch.cases.length + ")</button>";
    });
    if (!specs.chains.length) html += '<span class="hint">无链路定义</span>';
    bar.innerHTML = html;
    Array.prototype.forEach.call(bar.querySelectorAll(".chain-btn"), function (btn) {
      btn.onclick = function () { runJob("chain", null, btn.dataset.chain); };
    });
  }

  function renderTagBar() {
    var tags = {};
    specs.cases.forEach(function (c) { (c.tags || []).forEach(function (t) { tags[t] = 1; }); });
    var bar = $("tag-bar");
    var html = Object.keys(tags).sort().map(function (t) {
      return '<span class="chip' + (tagFilter === t ? " on" : "") + '" data-tag="' + esc(t) + '">' + esc(t) + "</span>";
    }).join("");
    // 选中 tag 后给"跑该组"（对齐 runner --group 能力）
    if (tagFilter) {
      var n = filtered().length;
      html += ' <button class="mini primary" id="btn-run-group">▶ 跑该组(' + n + ")</button>";
    }
    bar.innerHTML = html;
    Array.prototype.forEach.call(bar.querySelectorAll(".chip"), function (chip) {
      chip.onclick = function () {
        tagFilter = (tagFilter === chip.dataset.tag) ? null : chip.dataset.tag;
        renderTagBar();
        renderTable();
      };
    });
    var rg = $("btn-run-group");
    if (rg) rg.onclick = function () { runJob("group", null, tagFilter); };
  }

  function filtered() {
    return specs.cases.filter(function (c) {
      if (supportedFilter && (c.supported || "?") !== supportedFilter) return false;
      if (tagFilter && (c.tags || []).indexOf(tagFilter) < 0) return false;
      if (q) {
        var s = (c.id + " " + c.title).toLowerCase();
        if (s.indexOf(q.toLowerCase()) < 0) return false;
      }
      return true;
    });
  }

  function lastBadge(c) {
    if (!c.last) return '<span class="badge">未运行</span>';
    return '<span class="badge ' + (c.last.passed ? "pass" : "fail") + '">' +
      (c.last.passed ? "PASS" : "FAIL") + "</span> <span class='hint'>" + esc(c.last.ts) + "</span>";
  }

  function supportedBadge(c) {
    var s = c.supported || "?";
    var cls = { yes: "yes", partial: "partial", no: "no", error: "err" }[s] || "no";
    return '<span class="badge ' + cls + '">' + esc(s) + "</span>";
  }

  function renderTable() {
    var rows = filtered();
    if (!specs.available) {
      $("list-wrap").innerHTML = '<div class="empty">' + esc(specs.reason || "autotest 框架不可用") +
        "<br><span class='hint'>设置 WORKBENCH_AUTOTEST_ROOT 或 ~/.config/workbench/autotest-root 后重启工作台</span></div>";
      return;
    }
    if (!rows.length) {
      $("list-wrap").innerHTML = '<div class="empty">无匹配用例</div>';
      return;
    }
    var html = "<table><thead><tr>" +
      '<th style="width:28px"><input type="checkbox" id="sel-all"></th>' +
      "<th style=\"width:70px\">操作</th><th>ID</th><th>标题</th><th>支持</th><th>tags</th><th>最近结果</th>" +
      "</tr></thead><tbody>";
    rows.forEach(function (c) {
      var disabled = c.runnable ? "" : " disabled";
      html += "<tr data-id=\"" + esc(c.id) + "\">" +
        '<td><input type="checkbox" class="sel-row" data-id="' + esc(c.id) + '"' +
        (selected[c.id] ? " checked" : "") + "></td>" +
        '<td><button class="mini run-one' + disabled + '" data-id="' + esc(c.id) + '"' + disabled + ">▶</button></td>" +
        "<td class='mono'>" + esc(c.id) + "</td>" +
        "<td>" + esc(c.title) + (c.load_error ? ' <span class="badge err">加载失败</span>' : "") + "</td>" +
        "<td>" + supportedBadge(c) + "</td>" +
        "<td>" + (c.tags || []).map(function (t) { return '<span class="tag">' + esc(t) + "</span>"; }).join("") + "</td>" +
        "<td>" + lastBadge(c) + "</td></tr>";
    });
    html += "</tbody></table>";
    $("list-wrap").innerHTML = html;

    $("sel-all").onchange = function () {
      var on = $("sel-all").checked;
      rows.forEach(function (c) { if (c.runnable) selected[c.id] = on; });
      renderTable();
      renderSelCount();
    };
    Array.prototype.forEach.call(document.querySelectorAll(".sel-row"), function (cb) {
      cb.onclick = function (ev) { ev.stopPropagation(); };
      cb.onchange = function () {
        if (cb.checked) selected[cb.dataset.id] = true;
        else delete selected[cb.dataset.id];
        renderSelCount();
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll(".run-one"), function (btn) {
      btn.onclick = function (ev) { ev.stopPropagation(); runJob("case", [btn.dataset.id]); };
    });
    Array.prototype.forEach.call($("list-wrap").querySelectorAll("tbody tr"), function (tr) {
      tr.onclick = function () { openDrawer(tr.dataset.id); };
    });
    renderSelCount();
  }

  function renderSelCount() {
    var n = Object.keys(selected).filter(function (k) { return selected[k]; }).length;
    $("sel-count").textContent = n ? "已选 " + n + " 例" : "";
    $("btn-run-sel").disabled = !n;
    $("btn-clear-sel").disabled = !n;
  }

  // -- 运行 -------------------------------------------------------------
  function runJob(mode, ids, name) {
    var body = { mode: mode };
    if (ids) body.ids = ids;
    if (name) body.name = name;
    api("POST", "/api/autotest/run", body).then(function (data) {
      toast("已提交：" + data.target);
      attachJob(data.job_id);
    }).catch(function (e) { toast("提交失败：" + e.message); });
  }

  function attachJob(jobId) {
    currentJobId = jobId;
    $("job-panel").classList.add("show");
    $("job-line").textContent = "任务 " + jobId;
    $("job-summary").textContent = "运行中…";
    $("job-chips").innerHTML = "";
    $("job-log").textContent = "";
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollJob, 1500);
    pollJob();
  }

  function pollJob() {
    if (!currentJobId) return;
    api("GET", "/api/autotest/job/" + currentJobId).then(function (job) {
      $("job-chips").innerHTML = (job.results || []).map(function (r) {
        return '<span class="badge ' + (r.passed ? "pass" : "fail") + '">' +
          (r.passed ? "PASS" : "FAIL") + " " + esc(r.case_id) + "</span>";
      }).join("");
      $("job-log").textContent = (job.log_tail || []).join("\n");
      $("job-log").scrollTop = $("job-log").scrollHeight;
      if (job.status !== "running") {
        clearInterval(pollTimer);
        pollTimer = null;
        var s = job.summary;
        $("job-summary").textContent = s
          ? ("汇总 " + s.pass + "/" + s.total + " PASS · exit=" + job.exit_code)
          : ("结束 · exit=" + job.exit_code);
        toast("任务结束：" + (s ? s.pass + "/" + s.total + " PASS" : "exit=" + job.exit_code));
        loadList();
      }
    }).catch(function () { /* 瞬时错误下次轮询再取 */ });
  }

  // -- 详情抽屉 -----------------------------------------------------------
  function openDrawer(caseId) {
    var c = null;
    specs.cases.forEach(function (x) { if (x.id === caseId) c = x; });
    if (!c) return;
    drawerCase = c;
    $("d-title").textContent = c.id;
    $("d-body").innerHTML =
      '<div class="kv"><b>标题</b>' + esc(c.title) + "</div>" +
      '<div class="kv"><b>支持</b>' + supportedBadge(c) +
      (c.supported_reason ? ' <span class="hint">' + esc(c.supported_reason) + "</span>" : "") + "</div>" +
      '<div class="kv"><b>tags</b>' + (c.tags || []).map(function (t) { return '<span class="tag">' + esc(t) + "</span>"; }).join("") + "</div>" +
      '<div class="kv"><b>内容</b>' + esc(c.desc || "—") + "</div>" +
      '<div class="kv"><b>执行方式</b>' + esc(c.how || "—") + "</div>" +
      '<div class="kv"><b>最近结果</b>' + lastBadge(c) +
      (c.last ? '<pre>' + esc(c.last.detail) + "</pre>" : "") + "</div>" +
      '<div id="d-extra"></div>';
    $("d-run").disabled = !c.runnable;
    $("drawer").classList.add("open");
  }

  function dExtra(preText, title) {
    $("d-extra").innerHTML = "<div class='kv'><b>" + esc(title) + "</b><pre>" + esc(preText) + "</pre></div>";
  }

  // -- 事件 -------------------------------------------------------------
  $("q").oninput = function () { q = this.value; renderTable(); };
  $("f-supported").onchange = function () { supportedFilter = this.value; renderTable(); };
  $("btn-refresh").onclick = function () { loadList().then(function () { toast("已刷新"); }); };
  $("btn-run-all").onclick = function () { runJob("all"); };
  $("btn-run-sel").onclick = function () {
    var ids = Object.keys(selected).filter(function (k) { return selected[k]; });
    if (ids.length) runJob("case", ids);
  };
  $("btn-clear-sel").onclick = function () { selected = {}; renderTable(); renderSelCount(); };
  $("d-close").onclick = function () { $("drawer").classList.remove("open"); };
  $("d-run").onclick = function () {
    if (drawerCase) { runJob("case", [drawerCase.id]); $("drawer").classList.remove("open"); }
  };
  $("d-result").onclick = function () {
    if (!drawerCase) return;
    api("GET", "/api/autotest/result/" + drawerCase.id + "/latest").then(function (r) {
      dExtra(JSON.stringify(r, null, 2), "最近完整结果");
    }).catch(function (e) { toast(e.message); });
  };
  $("d-doc").onclick = function () {
    if (!drawerCase) return;
    api("GET", "/api/autotest/doc/" + drawerCase.id).then(function (r) {
      dExtra(r.doc, "用例文档 docs/" + drawerCase.id + ".md");
    }).catch(function (e) { toast(e.message); });
  };

  // -- 启动：恢复进行中的任务 ---------------------------------------------
  loadList().then(function () {
    api("GET", "/api/autotest/status").then(function (st) {
      if (st.busy && st.current_job) attachJob(st.current_job.job_id);
      renderPill();
    });
  });
})();
