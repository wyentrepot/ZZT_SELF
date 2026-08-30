/* 报文追踪页（reqs/0010 P1 / ADR-9）
 * 数据源：/api/listener/traces*（经 workbench 前缀代理转发侦听台子应用）。
 * 回放与 live 同一引擎同一 schema：回放 POST 同步返回报告；live 注册后轮询快照。
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
    mode: "replay",          // replay | live（表单窗口模式）
    session: [],             // 本次会话的回放报告
    liveList: [],            // 服务端 live 句柄
    selected: null,          // {kind:"replay",report} | {kind:"live",meta,report}
    selectedFlowKey: null,
    pollTimer: null,
  };

  function banner(msg) {
    var el = $("#banner");
    if (!msg) { el.classList.remove("show"); el.textContent = ""; return; }
    el.textContent = msg;
    el.classList.add("show");
  }

  /* workbench 前缀代理把 /api/listener 剥成 /api，而侦听台内部路由自带
   * /api/listener 前缀，故此处用双前缀（与内嵌侦听台页 /api/listener/listener/indexes 同惯例）。*/
  var TRACE_API = "/api/listener/listener/traces";
  var FRAME_API = "/api/listener/logs/frames/";

  function api(path, options) {
    return fetch(path, options).then(function (resp) {
      if (resp.status === 503) throw new Error("侦听台子应用未启用（降级运行）");
      if (resp.status === 404) throw new Error("追踪不存在或已被清理");
      if (!resp.ok) return resp.json().catch(function () { return {}; }).then(function (body) {
        throw new Error(body.detail || ("HTTP " + resp.status));
      });
      return resp.json();
    });
  }

  /* ================= 列1：追踪列表 ================= */

  function refreshList() {
    return api(TRACE_API).then(function (data) {
      state.liveList = data.traces || [];
      renderList();
    }).catch(function (err) { banner(err.message); });
  }

  function renderList() {
    var rows = [];
    state.liveList.forEach(function (t) {
      rows.push({ kind: "live", id: t.trace_id, meta: t });
    });
    state.session.forEach(function (r) {
      rows.push({ kind: "replay", id: r.trace_id, report: r });
    });
    $("#lstCnt").textContent = rows.length ? "live " + state.liveList.length + " · 回放 " + state.session.length : "";
    if (!rows.length) {
      $("#traceList").innerHTML = '<div class="empty"><p>暂无追踪。<br>点右上角「新建追踪」以一次发送的特征锁定一条通信流。</p></div>';
      return;
    }
    $("#traceList").innerHTML = rows.map(function (row, i) {
      var sel = state.selected && state.selected.kind === row.kind &&
        (row.kind === "live" ? state.selected.meta.trace_id : state.selected.report.trace_id) === row.id;
      var head =
        '<div class="tr-r1"><span class="tr-id">' + esc(row.id) + "</span>" +
        '<span class="chip chip--' + (row.kind === "live" ? "rx" : "ghost") + '">' + (row.kind === "live" ? "LIVE" : "回放") + "</span>" +
        '<span class="chip" style="margin-left:auto">' + esc(row.kind === "live" ? (featureScope(row.meta.feature)) : esc(row.report.scope)) + "</span></div>";
      var sub = "";
      if (row.kind === "live") {
        sub = '<div class="tr-sum"><span>起始帧 <b>' + esc(row.meta.start_frame_id) + "</b></span>" +
          '<span>最新 <b>' + esc(row.meta.last_frame_id) + "</b></span>" +
          '<span class="bad">' + esc(row.meta.status) + "</span></div>" +
          '<div class="tr-sum" style="color:var(--color-fg-dim)">' + esc(row.meta.created_at || "") + "</div>";
      } else {
        var s = row.report.summary || {};
        sub = '<div class="tr-sum"><span>轮 <b>' + (s.rounds || 0) + "</b></span><span>流 <b>" + (s.flows || 0) + "</b></span>" +
          "<span>全链 <b>" + (s.full_chain || 0) + "</b></span>" +
          '<span class="bad">无ACK <b>' + (s.no_ack || 0) + "</b></span>" +
          '<span class="bad">无响应 <b>' + (s.no_response || 0) + "</b></span></div>";
      }
      return '<div class="tr-item' + (sel ? " sel" : "") + '" data-i="' + i + '"><div class="tr-in">' + head + sub + "</div></div>";
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll("#traceList .tr-item"), function (el) {
      el.addEventListener("click", function () { selectRow(rows[+el.dataset.i]); });
    });
  }

  function featureScope(feature) {
    return (feature && feature.scope) || "round";
  }

  function selectRow(row) {
    stopPoll();
    if (row.kind === "replay") {
      state.selected = { kind: "replay", report: row.report };
      renderReport(row.report);
    } else {
      state.selected = { kind: "live", meta: row.meta, report: null };
      pollLive(row.meta.trace_id);
    }
    renderList();
  }

  function stopPoll() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  function pollLive(traceId) {
    var tick = function () {
      api(TRACE_API + "/" + encodeURIComponent(traceId)).then(function (report) {
        if (!state.selected || state.selected.kind !== "live") return;
        state.selected.report = report;
        renderReport(report);
        renderList();
      }).catch(function (err) { banner(err.message); stopPoll(); });
    };
    tick();
    state.pollTimer = setInterval(tick, 4000);
  }

  /* ================= 列2：表单 / 报告 ================= */

  function showForm() {
    stopPoll();
    state.selected = null;
    state.selectedFlowKey = null;
    renderList();
    $("#midTitle").textContent = "特征 · 新建追踪";
    $("#midHint").textContent = "";
    $("#parHead").innerHTML = '<div class="par-t2" style="padding:0">提交特征后，这里将渲染三段证据链。</div>';
    $("#parBody").innerHTML = '<div class="empty"><p>未选择流</p></div>';
    $("#pxStrip").innerHTML = '<span class="lbl">代理关系观测</span><span class="hint" style="padding-top:3px">追踪结果的 (表地址→应答STA) 副产物将显示在这里</span>';
    var live = state.mode === "live";
    $("#midCol").innerHTML =
      '<div class="form">' +
      '<div class="form-err" id="formErr"></div>' +
      '<div class="row"><label>scope 粒度（flow 需填报文序号）</label>' +
      '<select class="fld" id="fScope"><option value="round">round · 时间簇（缺省 60s 空闲切簇）</option>' +
      '<option value="flow">flow · 单流（业务ID+序号）</option>' +
      '<option value="campaign">campaign · 业务×时间窗</option></select></div>' +
      '<div class="two">' +
      '<div class="row"><label>app_id 报文ID <b>*必填</b>（如 0003 并发抄表 / 0008 事件上报）</label><input class="fld mono" id="fApp" value="0003" placeholder="0003"></div>' +
      '<div class="row"><label>msg_seq 报文序号（APP_RAW[8:10]，hex）</label><input class="fld mono" id="fSeq" placeholder="04D2"></div></div>' +
      '<div class="two">' +
      '<div class="row"><label>frm_type 业务名（可留空通配）</label><input class="fld" id="fFrm" placeholder="终端主动并发抄表"></div>' +
      '<div class="row"><label>dst_tei 对端 TEI（留空=全部，FFF 广播）</label><input class="fld mono" id="fDst" placeholder="087"></div></div>' +
      '<div class="three">' +
      '<div class="row"><label>app_port</label><input class="fld mono" id="fPort" placeholder="11"></div>' +
      '<div class="row"><label>nid</label><input class="fld mono" id="fNid" placeholder=""></div>' +
      '<div class="row"><label>channel</label><input class="fld" id="fCh" placeholder="PLC/RF"></div></div>' +
      '<div class="row"><label>app_raw_contains 载荷 hex 片段</label><input class="fld mono" id="fRaw" placeholder="11030000"></div>' +
      (live
        ? '<div class="row"><label>Live 模式：只匹配注册之后入库的帧</label><div class="hint">注册后自动每 4s 轮询快照，可随时停止。</div></div>'
        : '<div class="two">' +
          '<div class="row"><label>window.start_time（可留空=全库）</label><input class="fld mono" id="fT0" placeholder="2026-08-29 01:22:00"></div>' +
          '<div class="row"><label>window.end_time</label><input class="fld mono" id="fT1" placeholder="2026-08-29 01:23:00"></div></div>') +
      '<div class="row"><label>响应策略</label><div class="ckline">' +
      '<label><input type="checkbox" id="fAck" checked>use_ack_evidence 链路ACK证据</label>' +
      '<label><input type="checkbox" id="fConfirm" checked>confirm_via_0x0020 显式确认</label></div></div>' +
      '<div class="two">' +
      '<div class="row"><label>cluster_gap_seconds 切簇空闲（秒）</label><input class="fld mono" id="fGap" value="60"></div>' +
      '<div class="row"><label>&nbsp;</label><button class="btn btn--primary" id="btnSubmit" style="width:100%">' +
      (live ? "注册 Live 追踪" : "运行回放追踪") + "</button></div></div>" +
      '<div class="hint">提示：真序号在 APP_RAW[8:10]（全局递增、重发不增）；业务头里的 0x0201 是静态字段，不是序号。</div>' +
      "</div>";
    $("#btnSubmit").addEventListener("click", submitTrace);
  }

  function submitTrace() {
    var errEl = $("#formErr");
    errEl.classList.remove("show");
    var scope = $("#fScope").value;
    var msgSeq = $("#fSeq").value.trim();
    var appId = $("#fApp").value.trim();
    if (!appId) { return formError(errEl, "feature.app_id 必填（如 0003）"); }
    if (scope === "flow" && !msgSeq) { return formError(errEl, "flow 粒度必须提供 msg_seq（配对键）"); }
    var feat = { scope: scope, feature: { app_id: appId }, window: {}, response_policy: {} };
    if (msgSeq) feat.feature.msg_seq = msgSeq;
    if ($("#fFrm").value.trim()) feat.feature.frm_type = $("#fFrm").value.trim();
    if ($("#fDst").value.trim()) feat.feature.dst_tei = $("#fDst").value.trim();
    if ($("#fRaw").value.trim()) feat.feature.app_raw_contains = $("#fRaw").value.trim();
    if ($("#fNid").value.trim()) feat.feature.nid = $("#fNid").value.trim();
    if ($("#fCh").value.trim()) feat.feature.channel = $("#fCh").value.trim();
    var useAck = $("#fAck").checked, useConfirm = $("#fConfirm").checked;
    feat.response_policy.use_ack_evidence = useAck;
    feat.response_policy.confirm_via_0x0020 = useConfirm;
    if (+$("#fGap").value > 0) feat.response_policy.cluster_gap_seconds = +$("#fGap").value;
    if (state.mode === "live") {
      feat.window.mode = "live";
    } else {
      feat.window.mode = "time_range";
      if ($("#fT0").value.trim()) feat.window.start_time = $("#fT0").value.trim();
      if ($("#fT1").value.trim()) feat.window.end_time = $("#fT1").value.trim();
    }
    var btn = $("#btnSubmit");
    btn.disabled = true;
    api(TRACE_API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(feat),
    }).then(function (report) {
      btn.disabled = false;
      if (state.mode === "live") {
        banner(null);
        return refreshList().then(function () {
          var row = state.liveList.filter(function (t) { return t.trace_id === report.trace_id; })[0];
          if (row) selectRow({ kind: "live", id: row.trace_id, meta: row });
        });
      }
      state.session.unshift(report);
      selectRow({ kind: "replay", id: report.trace_id, report: report });
    }).catch(function (err) {
      btn.disabled = false;
      formError(errEl, err.message);
    });
  }

  function formError(el, msg) {
    el.textContent = msg;
    el.classList.add("show");
  }

  var STG = {
    confirmed: ["st-confirmed", "已确认"], responded: ["st-responded", "已响应"],
    acked: ["st-acked", "已ACK"], sent: ["st-sent", "已发出"],
    denied: ["st-denied", "否认"], armed: ["st-armed", "armed"], timeout: ["st-timeout", "超时"],
  };
  var EK = {
    explicit_ack: "铁证 · 0x0020显式确认", no_retransmit_inference: "推断 · 簇内无重传",
    retransmitted: "未确认 · 重传反证", denied: "否认", none: "—",
  };

  function stageChip(stage) {
    var def = STG[stage] || STG.armed;
    return '<span class="st ' + def[0] + '">' + def[1] + "</span>";
  }

  function renderReport(report) {
    var f = report.feature || {};
    var feat = f.feature || {};
    var s = report.summary || {};
    $("#midTitle").textContent = "追踪 " + report.trace_id;
    $("#midHint").textContent = report.mode === "live" ? "live · 每 4s 轮询" : "回放";
    var liveStop = "";
    if (report.mode === "live" && report.live && report.live.status === "live") {
      liveStop = '<button class="btn btn--sm btn--ghost btn--danger" id="btnStop">停止</button>';
    }
    $("#midCol").innerHTML =
      '<div class="ft-head">' +
      '<div class="ft-grid">' +
      "<span class='k'>scope</span><span class='v'>" + esc(report.scope) + "</span>" +
      "<span class='k'>app_id</span><span class='v'>" + esc(feat.app_id || "—") + "</span>" +
      "<span class='k'>msg_seq</span><span class='v'>" + esc(feat.msg_seq || "（全部序号）") + "</span>" +
      "<span class='k'>frm_type</span><span class='v'>" + esc(feat.frm_type || "（通配）") + "</span>" +
      "<span class='k'>dst_tei</span><span class='v'>" + esc(feat.dst_tei || "（全部对端）") + "</span>" +
      "<span class='k'>窗口</span><span class='v'>" + esc(windowText(f)) + "</span>" +
      (report.live ? "<span class='k'>live</span><span class='v'>起始帧 " + esc(report.live.start_frame_id) +
        " · 最新 " + esc(report.live.last_frame_id) + " · " + esc(report.live.status) + "</span>" : "") +
      "</div>" +
      '<div class="sumbar">' +
      "<span class='chip'>轮 <b class='mono'>" + (s.rounds || 0) + "</b></span>" +
      "<span class='chip'>流 <b class='mono'>" + (s.flows || 0) + "</b></span>" +
      "<span class='chip'>表 <b class='mono'>" + (s.meters || 0) + "</b></span>" +
      "<span class='chip chip--ok'>全链 <b class='mono'>" + (s.full_chain || 0) + "</b></span>" +
      "<span class='chip chip--tx'>无ACK <b class='mono'>" + (s.no_ack || 0) + "</b></span>" +
      "<span class='chip chip--am'>无响应 <b class='mono'>" + (s.no_response || 0) + "</b></span>" +
      "<span class='chip chip--mg'>否认 <b class='mono'>" + (s.denied || 0) + "</b></span>" +
      "<span class='chip chip--err'>未确认 <b class='mono'>" + (s.no_confirm || 0) + "</b></span>" +
      (s.bad_frames ? "<span class='chip chip--am'>坏帧 <b class='mono'>" + s.bad_frames + "</b></span>" : "") +
      "</div>" +
      '<div style="display:flex;gap:6px">' + liveStop +
      '<button class="btn btn--sm btn--ghost" id="btnCopy">复制特征 JSON</button></div>' +
      "</div>" +
      '<div id="roundsWrap"></div>';
    renderRounds(report);
    renderProxy(report);
    var copyBtn = $("#btnCopy");
    if (copyBtn) copyBtn.addEventListener("click", function () {
      navigator.clipboard && navigator.clipboard.writeText(JSON.stringify(report.feature, null, 2));
      copyBtn.textContent = "已复制";
      setTimeout(function () { copyBtn.textContent = "复制特征 JSON"; }, 1200);
    });
    var stopBtn = $("#btnStop");
    if (stopBtn) stopBtn.addEventListener("click", function () {
      api(TRACE_API + "/" + encodeURIComponent(report.trace_id), { method: "DELETE" })
        .then(function () { stopPoll(); refreshList(); })
        .catch(function (err) { banner(err.message); });
    });
  }

  function windowText(feature) {
    var w = feature.window || {};
    if (w.mode === "live") return "live（注册后增量）";
    if (w.mode === "cursor_range") return "cursor_range " + (w.start_id != null ? "≥" + w.start_id : "");
    if (w.start_time || w.end_time) return (w.start_time || "…") + " ~ " + (w.end_time || "…");
    return "全库（time_range 未限定）";
  }

  function renderRounds(report) {
    var wrap = $("#roundsWrap");
    var rounds = report.rounds || [];
    if (!rounds.length) {
      wrap.innerHTML = '<div class="empty" style="height:140px"><p>窗口内未匹配到帧。<br>检查 app_id / 序号 / 时间窗，或确认侦听台索引含物化列数据。</p></div>';
      return;
    }
    wrap.innerHTML = rounds.map(function (r, ri) {
      var flows = r.flows || [];
      var head =
        '<button class="round-h" data-r="' + ri + '">' +
        '<svg class="car" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>' +
        "<span class='rnd-seq'>" + esc((r.msg_seqs || []).join(",") || "—") + "</span>" +
        "<span class='rnd-cluster'>" + esc(r.start_t || "") + " ~ " + esc(r.end_t || "") +
        (r.duration_ms != null ? " · " + (r.duration_ms / 1000).toFixed(1) + "s" : "") + "</span>" +
        "<span class='rnd-cnt'>" + (r.meters ? r.meters.responded + "/" + r.meters.targets : flows.length + " 流") + " 回</span></button>";
      var meterRows = (r.meter_table || []).map(function (m) {
        var cls = m.status === "ok" ? "rs-ok" : (m.status === "denied" ? "rs-denied" : "rs-missing");
        var zh = m.status === "ok" ? "正常" : (m.status === "denied" ? "否认" : "没回");
        return "<tr><td>" + esc(m.meter_addr) + "</td><td>" + esc(m.via_tei || "—") + "</td>" +
          '<td class="' + cls + '">' + zh + "</td><td>" + esc(m.flow_key || "—") + "</td></tr>";
      }).join("");
      var flowRows = flows.map(function (fl) {
        var sel = fl.flow_key === state.selectedFlowKey ? " sel" : "";
        var latency = fl.response && fl.response.latency_ms != null ? fl.response.latency_ms + "ms" : "—";
        return '<button class="flow-row' + sel + '" data-fk="' + esc(fl.flow_key) + '" data-r="' + ri + '">' +
          "<span class='flow-seq'>" + esc(fl.msg_seq) + "</span>" +
          "<span class='flow-tei'>TEI " + esc(fl.via_tei || "—") + "</span>" +
          "<span class='flow-lat'>" + latency + "</span>" +
          "<span style='margin-left:auto'></span>" + stageChip(fl.stage) + "</button>";
      }).join("");
      return '<div class="round' + (ri === 0 ? " open" : "") + '" data-ri="' + ri + '">' + head +
        '<div class="round-b"><div>' +
        (meterRows ? '<div class="recon"><table class="recon-t"><thead><tr><th>表地址</th><th>经TEI</th><th>对账</th><th>flow</th></tr></thead><tbody>' +
          meterRows + "</tbody></table></div>" : "") +
        flowRows + "</div></div></div>";
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll("#roundsWrap .round-h"), function (el) {
      el.addEventListener("click", function () { el.parentElement.classList.toggle("open"); });
    });
    Array.prototype.forEach.call(document.querySelectorAll("#roundsWrap .flow-row"), function (el) {
      el.addEventListener("click", function () {
        state.selectedFlowKey = el.dataset.fk;
        var round = report.rounds[+el.dataset.r];
        var flow = (round.flows || []).filter(function (f) { return f.flow_key === state.selectedFlowKey; })[0];
        Array.prototype.forEach.call(document.querySelectorAll("#roundsWrap .flow-row"), function (x) { x.classList.remove("sel"); });
        el.classList.add("sel");
        renderChain(flow);
      });
    });
    /* 默认选中第一条流 */
    if (!state.selectedFlowKey && rounds.length && (rounds[0].flows || []).length) {
      var first = document.querySelector("#roundsWrap .flow-row");
      if (first) first.click();
    }
  }

  function renderProxy(report) {
    var px = report.proxy_graph || [];
    var strip = $("#pxStrip");
    if (!px.length) {
      strip.innerHTML = '<span class="lbl">代理关系观测</span><span class="hint" style="padding-top:3px">本轮无 (表地址→应答STA) 观测</span>';
      return;
    }
    strip.innerHTML = '<span class="lbl">代理关系观测 · ' + px.length + "</span>" + px.slice(0, 24).map(function (p) {
      return '<span class="px-item"><span class="mono">' + esc(p.meter_addr) + '</span><span class="arr">→</span>' +
        "STA <span class='mono'>" + esc(p.sta_tei || "—") + "</span><span style='color:var(--color-fg-dim)'>×" + p.observations + "</span></span>";
    }).join("");
  }

  /* ================= 列3：三段证据链 ================= */

  function frameBtn(label, fid) {
    if (!fid) return "";
    return '<span class="ev-frame" data-fid="' + fid + '">钻取 <span class="mono">frame ' + esc(fid) + "</span> · " + esc(label) + "</span>";
  }

  function renderChain(flow) {
    if (!flow) {
      $("#parHead").innerHTML = '<div class="par-t2" style="padding:0">该轮无流。</div>';
      $("#parBody").innerHTML = '<div class="empty"><p>未选择流</p></div>';
      return;
    }
    var sent = flow.sent, ack = flow.ack, resp = flow.response, confirm = flow.confirm;
    var s3 = flow.s3 || {};
    $("#parHead").innerHTML =
      '<div class="par-t1"><b>' + esc(flow.flow_key) + "</b>" + stageChip(flow.stage) +
      "<span class='chip chip--ghost'>经 TEI " + esc(flow.via_tei || "—") + "</span>" +
      (s3.verdict && s3.verdict !== "none"
        ? "<span class='chip " + (s3.verdict === "confirmed" ? "chip--ok" : (s3.verdict === "denied" ? "chip--mg" : "chip--am")) + "'>" +
          esc(s3.verdict) + " · " + esc(EK[s3.evidence_kind] || s3.evidence_kind || "") + "</span>" : "") +
      "</div>" +
      '<div class="par-t2">状态机 armed → sent → acked → responded → confirmed；判定全部来自侦听台空口自证（ADR-9）。</div>';
    var items = [
      { cls: "part", name: "S1 发出 · CCO 下行帧被捕获", ok: !!sent,
        ds: sent ? "下行帧 " + esc(sent.t) + "，重传 " + (sent.retries || 0) + " 次" : "窗口内未见匹配的下行帧。",
        frame: sent ? frameBtn("S1 下行帧", sent.frame_id) : "" },
      { cls: "part", name: "S2a 收到 · 链路层选择确认", ok: !!ack,
        ds: ack ? "ACK 对端 " + esc(ack.ack_peer || "—") + " 命中被确认帧 STA 端 TEI（MAC 头 [27..28]）· " + esc(ack.t || "") : ack === null ? "无链路 ACK——信道层丢帧或对端离线。" : "未启用 ACK 证据。",
        frame: ack ? frameBtn("S2a ACK", ack.frame_id) : "" },
      { cls: "part", name: "S2b 响应 · 同序号上行帧", ok: !!resp,
        ds: resp ? "上行帧 " + esc(resp.t || "") + " · 延时 " + (resp.latency_ms != null ? resp.latency_ms + " ms" : "—") +
          " · 应答 STA " + esc(resp.responded_sta || "—") : "有 ACK 无响应——STA 业务层卡滞。",
        frame: resp ? frameBtn("S2b 上行帧", resp.frame_id) : "" },
      { cls: s3.verdict === "denied" ? "bad" : "part", name: "S3 已接收 · 0x0020 确认/重传反证", ok: s3.verdict === "confirmed",
        ds: s3.verdict === "confirmed" ? "判定：" + esc(EK[s3.evidence_kind] || s3.evidence_kind) :
            s3.verdict === "denied" ? "应答全否认（否认是一等结果）。" :
            s3.verdict === "not_confirmed" ? "窗口内出现重传——CCO 接收侧异常。" : "无 S3 证据。",
        frame: confirm ? frameBtn("S3 确认帧" + (confirm.denied ? "（否认）" : ""), confirm.frame_id) : "" },
    ];
    $("#parBody").innerHTML =
      '<div class="card"><div class="card-h">证据链<span class="hint">' + esc(flow.flow_key) + "</span></div>" +
      '<div class="card-in"><div class="ev">' + items.map(function (it) {
        return '<div class="ev-it ' + (it.ok ? "done" : it.cls) + '"><div class="ev-r1"><b>' + it.name + "</b>" +
          (it.ok ? "<span class='chip chip--ok'>通过</span>" : "<span class='chip chip--am'>未达</span>") + "</div>" +
          '<div class="ev-ds">' + it.ds + "</div>" + it.frame + "</div>";
      }).join("") + "</div></div></div>" +
      '<div class="card"><div class="card-h">重传序列<span class="hint">' + ((flow.retransmissions || []).length) + " 次</span></div>" +
      '<div class="card-in" id="retxWrap">' +
      ((flow.retransmissions || []).length
        ? flow.retransmissions.map(function (r) {
            return frameBtn("重传 @" + esc(r.t || "") + (r.interval_ms != null ? " +" + r.interval_ms + "ms" : ""), r.frame_id) + "<br>";
          }).join("")
        : '<span class="hint">窗口内同序号未重发（S3 推断的前提之一）。</span>') + "</div></div>" +
      '<div class="card" id="drillCard" style="display:none"><div class="card-h">帧钻取<span class="hint" id="drillMeta"></span></div>' +
      '<div class="card-in"><div class="hexwrap" id="drillHex"></div></div></div>';
    Array.prototype.forEach.call(document.querySelectorAll("#parBody .ev-frame"), function (el) {
      el.addEventListener("click", function () { drill(+el.dataset.fid); });
    });
  }

  function drill(frameId) {
    var card = $("#drillCard");
    card.style.display = "";
    $("#drillMeta").textContent = "frame " + frameId + " · 加载中…";
    $("#drillHex").textContent = "…";
    fetch(FRAME_API + frameId).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (frame) {
      $("#drillMeta").textContent = "frame " + frameId + " · " + esc(frame.log_time || "") + " · " + (frame.byte_length || "?") + "B";
      $("#drillHex").textContent = frame.raw_hex || "（无原始 hex）" +
        (frame.parse_error ? "\n⚠ " + frame.parse_error : "");
    }).catch(function (err) {
      $("#drillMeta").textContent = "frame " + frameId + " · 失败";
      $("#drillHex").textContent = err.message;
    });
  }

  /* ================= 交互 ================= */

  $("#segMode").addEventListener("click", function (e) {
    var btn = e.target.closest("button");
    if (!btn) return;
    Array.prototype.forEach.call(document.querySelectorAll("#segMode button"), function (x) { x.classList.remove("on"); });
    btn.classList.add("on");
    state.mode = btn.dataset.m;
    showForm();
  });
  $("#btnNew").addEventListener("click", showForm);
  $("#btnRefresh").addEventListener("click", function () { banner(null); refreshList(); });

  refreshList();
  showForm();
})();
