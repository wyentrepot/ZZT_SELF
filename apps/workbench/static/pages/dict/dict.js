/* 协议字典页（reqs/0010 P1）
 * 数据源：/api/dict*（workbench 本地端点，直读 libs/ 下 metadata 与 loghooks rules 文件）。
 */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };

  var state = { dicts: [], cur: null, items: [], curEntry: -1, raw: null };

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

  /* desc 含纠错/实证关键词的条目打琥珀点（与设计稿注记语义一致） */
  var NOTE_RE = /(纠错|错位|实证|疑为|厂商扩展|设备自定义|无协议出处|待真机|真机帧|待裁决|兜底|与.*不一致)/;

  function entryText(item, dictId) {
    if (dictId === "afn-fn") {
      return [item.code, item.name, item.sem].join(" ");
    }
    if (dictId === "rules") {
      return [item.file, JSON.stringify(item.entries)].join(" ");
    }
    return [item.key, item.name, item.desc, item.data_type].join(" ");
  }

  /* ================= 列1：字典清单 ================= */

  function loadDicts() {
    return api("/api/dict").then(function (list) {
      state.dicts = list;
      $("#dctCnt").textContent = list.length + " 本";
      $("#dctList").innerHTML = list.map(function (d) {
        return '<button class="dct-card" data-d="' + d.id + '">' +
          '<span class="dct-r"><span class="dot" style="color:var(--tx-4)"></span>' + esc(d.name) +
          "<span class='mono'>" + d.count + (d.fn_count ? " AFN · " + d.fn_count + " Fn" : " 条") + "</span></span>" +
          '<span class="dct-s"><code>' + esc(d.path) + "</code></span></button>";
      }).join("");
      Array.prototype.forEach.call(document.querySelectorAll("#dctList .dct-card"), function (el) {
        el.addEventListener("click", function () { selectDict(el.dataset.d); });
      });
      if (list.length) selectDict(list[0].id);
    }).catch(function (err) {
      $("#dctList").innerHTML = '<div class="empty"><p>字典清单加载失败</p></div>';
      banner(err.message);
    });
  }

  function selectDict(id) {
    state.cur = state.dicts.filter(function (d) { return d.id === id; })[0];
    Array.prototype.forEach.call(document.querySelectorAll("#dctList .dct-card"), function (el) {
      var on = el.dataset.d === id;
      el.classList.toggle("on", on);
      el.querySelector(".dot").style.color = on ? "var(--ac)" : "var(--tx-4)";
    });
    $("#enTitle").textContent = state.cur.name + " · 条目";
    $("#parHead").innerHTML = '<div class="par-t1"><b>' + esc(state.cur.name) + "</b></div>" +
      '<div class="par-t2">' + esc(state.cur.desc || "") + '</div>';
    $("#parBody").innerHTML =
      '<div class="card"><div class="card-h">来源文件<span class="hint">' + esc(state.cur.path) + "</span></div>" +
      '<div class="card-in"><div style="font-size:12px;color:var(--tx-2);line-height:1.7">从左侧选择一个条目查看属性定义、desc 注记与原始 JSON。<br>' +
      '<span class="hint">simple JSON 键即事实契约：改键名/删条目 = 契约变更，需同步 adapter 与 UI。</span></div></div></div>';
    loadEntries();
  }

  /* ================= 列2：条目列表 ================= */

  function loadEntries() {
    var id = state.cur.id;
    var q = $("#q").value.trim();
    var url = "/api/dict/" + id + (q ? "?q=" + encodeURIComponent(q) : "");
    api(url).then(function (data) {
      state.raw = data;
      if (id === "rules") {
        state.items = (data.files || []).map(function (f) {
          return { file: f.file, count: f.count, entries: f.entries };
        });
      } else {
        state.items = data.items || [];
      }
      renderEntries();
    }).catch(function (err) {
      state.items = [];
      renderEntries();
      banner(err.message);
    });
  }

  function renderEntries() {
    var id = state.cur.id;
    if (!state.items.length) {
      $("#enList").innerHTML = '<div class="empty"><p>无匹配条目</p></div>';
      return;
    }
    var html;
    if (id === "rules") {
      html = state.items.map(function (f, i) {
        return '<div class="en-item" data-i="' + i + '"><div class="en-in">' +
          '<div class="en-r"><span class="en-key" style="min-width:0">' + esc(f.file) + '</span><span class="sub-count">' + f.count + " 条</span></div>" +
          '<div class="en-s"><span class="ds">事件识别规则文件（loghooks）</span></div></div></div>';
      }).join("");
    } else if (id === "afn-fn") {
      html = state.items.map(function (a, i) {
        return '<div class="en-item" data-i="' + i + '"><div class="en-in">' +
          '<div class="en-r"><span class="en-key">' + esc(a.code) + '</span><span class="en-nm">' + esc(a.name) + "</span>" +
          '<span class="sub-count">' + (a.fns || []).length + " Fn</span></div>" +
          '<div class="en-s"><span class="ds">' + esc(a.dir || "") + (a.route && a.route !== "—" ? " · " + esc(a.route) : "") + "</span></div></div></div>";
      }).join("");
    } else {
      html = state.items.map(function (it, i) {
        var note = NOTE_RE.test(it.desc || "");
        return '<div class="en-item" data-i="' + i + '"><div class="en-in">' +
          '<div class="en-r"><span class="en-key">' + esc(it.key) + '</span><span class="en-nm">' + esc(it.name) + "</span>" +
          (note ? '<span class="note-dot" title="desc 含纠错/实证注记"></span>' : "") + "</div>" +
          '<div class="en-s"><span>' + esc(it.data_type || "") + "</span>" +
          (it.unit ? "<span>· " + esc(it.unit) + "</span>" : "") +
          (it.scale ? "<span>· ×10<sup>" + esc(it.scale) + "</sup></span>" : "") + "</div></div></div>";
      }).join("");
    }
    $("#enList").innerHTML = html;
    Array.prototype.forEach.call(document.querySelectorAll("#enList .en-item"), function (el) {
      el.addEventListener("click", function () {
        state.curEntry = +el.dataset.i;
        Array.prototype.forEach.call(document.querySelectorAll("#enList .en-item"), function (x) { x.classList.remove("sel"); });
        el.classList.add("sel");
        renderDetail();
      });
    });
    if (id !== "rules" && state.items.length) {
      var first = document.querySelector("#enList .en-item");
      if (first) first.click();
    }
  }

  /* ================= 列3：详情 ================= */

  function kvTable(rows) {
    return '<table class="ft"><thead><tr><th style="width:32%">项</th><th>值</th></tr></thead><tbody>' +
      rows.map(function (r) {
        return "<tr><td><div class='nm'>" + r[0] + "</div></td><td>" + r[1] + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function renderDetail() {
    var id = state.cur.id, item = state.items[state.curEntry];
    if (!item) return;
    var body = "";
    if (id === "rules") {
      $("#parHead").innerHTML = '<div class="par-t1"><b class="mono">' + esc(item.file) + "</b>" +
        "<span class='chip'>" + item.count + " 条规则</span></div>" +
        '<div class="par-t2">事件识别规则（loghooks）：event.type 即场景脚本 expected_flow 的 event_type。</div>';
      body = item.entries.map(function (e) {
        var ev = e.event || {};
        return '<div class="card"><div class="card-h">' + esc(ev.label || e.id || "规则") +
          '<span class="hint">' + esc(e.id || "") + "</span></div>" +
          '<div class="card-in"><div style="font-size:11.5px;color:var(--tx-2);line-height:1.7">' +
          "事件 <span class='mono' style='color:var(--ac)'>" + esc(ev.type || "—") + "</span>" +
          (e.level ? " · 级别 <span class='mono'>" + esc(e.level) + "</span>" : "") +
          (e.match && e.match.pattern ? " · 匹配 <span class='mono' style='color:var(--am)'>" + esc(e.match.pattern) + "</span>" : "") +
          "</div></div></div>";
      }).join("") || '<div class="empty" style="height:120px"><p>该文件无匹配规则</p></div>';
    } else if (id === "afn-fn") {
      $("#parHead").innerHTML = '<div class="par-t1"><b class="mono">' + esc(item.code) + "</b><b>" + esc(item.name) + "</b>" +
        "<span class='chip chip--ac'>AFN</span>" +
        (item.route && item.route !== "—" ? "<span class='chip'>" + esc(item.route) + "</span>" : "") + "</div>" +
        '<div class="par-t2">' + esc(item.sem || "") + "</div>";
      var fnRows = (item.fns || []).map(function (f) {
        return "<tr><td><div class='nm'><span class='mono' style='color:var(--ac)'>" + esc(f.no) + "</span> " + esc(f.name) +
          (f.todo ? " <span class='chip chip--am' style='height:16px;font-size:10px'>字段待补</span>" : "") + "</div>" +
          (f.sem ? "<div class='ds'>" + esc(f.sem) + "</div>" : "") + "</td>" +
          "<td><span class='chip chip--" + (f.dir === "下行" ? "am" : "ghost") + "' style='height:18px;font-size:10px'>" + esc(f.dir || "—") + "</span></td>" +
          "<td><span class='mono'>" + (f.fields && f.fields.length ? f.fields.length + " 字段" : "—") + "</span></td></tr>";
      }).join("");
      body = '<div class="card"><div class="card-h">Fn 命令表<span class="hint">' + (item.fns || []).length + " 个</span></div>" +
        '<div class="card-in"><table class="ft"><thead><tr><th style="width:56%">命令</th><th style="width:20%">方向</th><th>字段</th></tr></thead><tbody>' +
        fnRows + "</tbody></table></div></div>" +
        (state.raw.note ? '<div class="card fixcard"><div class="card-h">字典说明</div><div class="card-in">' +
          '<div style="font-size:11.5px;color:var(--tx-2);line-height:1.7">' + esc(state.raw.note) + "</div></div></div>" : "");
    } else {
      var note = NOTE_RE.test(item.desc || "");
      $("#parHead").innerHTML = '<div class="par-t1"><b class="mono">' + esc(item.key) + "</b><b>" + esc(item.name) + "</b>" +
        "<span class='chip chip--ac'>" + esc(state.cur.name) + "</span>" +
        (note ? "<span class='chip chip--am'>含注记</span>" : "") + "</div>" +
        '<div class="par-t2">' + esc(item.desc || "") + "</div>";
      var rows = [
        ["数据类型", "<span class='mono'>" + esc(item.data_type || "—") + "</span>"],
        ["单位 / 换算", "<span class='mono'>" + esc(item.unit || "—") + "</span>" +
          (item.scale ? " · ×10<sup>" + esc(item.scale) + "</sup>" : "")],
        ["desc", "<span style='font-size:11.5px;color:var(--tx-2)'>" + esc(item.desc || "—") + "</span>"],
      ];
      if (item.length != null) rows.splice(2, 0, ["字节数", "<span class='mono'>" + esc(item.length) + "</span>"]);
      body = kvTable(rows) +
        (note ? '<div class="card fixcard" style="margin-top:14px"><div class="card-h">纠错 / 实证注记</div>' +
          '<div class="card-in"><div style="font-size:11.5px;color:var(--tx-2);line-height:1.7">' + esc(item.desc) + "</div></div></div>" : "");
    }
    body += '<div class="card" style="margin-top:14px"><div class="card-h">原始 JSON<span class="hint">' + esc(state.cur.path) + "</span></div>" +
      '<div class="card-in"><div class="jsonwrap">' + esc(JSON.stringify(item, null, 2)) + "</div></div></div>";
    $("#parBody").innerHTML = body;
  }

  /* ================= 交互 ================= */

  var debounce = null;
  $("#q").addEventListener("input", function () {
    clearTimeout(debounce);
    debounce = setTimeout(loadEntries, 250);
  });

  loadDicts();
})();
