/* 新版帧浏览（reqs/0010 P2）：三列布局 + feature_hint + 报文追踪联动。
 * 数据源与经典视图共用同一索引库：/api/logs/frames（列表）、/api/logs/frames/{id}（详情+feature_hint）。
 * 串口采集/日志导入仍在经典视图 01 数据源面板；本视图聚焦 浏览→解析→追踪。
 */
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const state = { offset: 0, limit: 60, total: 0, frames: [], curId: null, loading: false };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function request(url) {
    return fetch(url).then((resp) => {
      if (!resp.ok) return resp.json().catch(() => ({})).then((body) => {
        throw new Error(body.detail || `HTTP ${resp.status}`);
      });
      return resp.json();
    });
  }

  function loadFrames() {
    if (state.loading) return;
    state.loading = true;
    const params = new URLSearchParams({
      query: $("#proQuery").value.trim(),
      nid: $("#proNid").value.trim(),
      offset: state.offset,
      limit: state.limit,
    });
    request(`/api/logs/frames?${params}`).then((page) => {
      state.frames = page.frames || page.items || [];
      state.total = page.total || state.frames.length;
      $("#proTotal").textContent = `${state.total} 帧`;
      $("#proPage").textContent = `第 ${Math.floor(state.offset / state.limit) + 1} 页`;
      $("#proPageInfo").textContent = `${state.offset + 1}~${state.offset + state.frames.length} / ${state.total}`;
      renderList();
    }).catch((err) => {
      $("#proList").innerHTML = `<div class="pro-empty">${esc(err.message)}<br>请先在经典视图加载数据源</div>`;
    }).finally(() => { state.loading = false; });
  }

  function rowTitle(f) {
    return f.frm_type || f.FrmType || f.summary?.FrmType || f.summary_json?.FrmType || `frame ${f.id}`;
  }

  function renderList() {
    if (!state.frames.length) {
      $("#proList").innerHTML = '<div class="pro-empty">无帧<br>先在「帧浏览（经典）」导入日志或启动串口采集</div>';
      return;
    }
    $("#proList").innerHTML = state.frames.map((f) => `
      <div class="pro-fr${f.id === state.curId ? " sel" : ""}" data-id="${f.id}">
        <div class="pro-fr-in">
          <div class="pro-fr-r"><span class="pro-fr-t">${esc(f.log_time || "")}</span>
            <span class="pro-fr-n">${esc(rowTitle(f))}</span>
            <span class="pro-fr-l">${f.byte_length ?? "?"}B</span></div>
          <div class="pro-fr-r"><span class="pro-fr-t">#${f.id}</span>
            ${f.parse_error ? '<span class="pro-fr-t" style="color:var(--perr)">坏帧</span>' : ""}</div>
        </div>
      </div>`).join("");
    document.querySelectorAll("#proList .pro-fr").forEach((el) => {
      el.addEventListener("click", () => selectFrame(+el.dataset.id));
    });
  }

  function kv(rows) {
    return `<div class="pro-kv">${rows.map((r) =>
      `<div><div class="k">${r[0]}</div><div class="v">${esc(String(r[1] ?? "—"))}</div></div>`).join("")}</div>`;
  }

  function selectFrame(id) {
    state.curId = id;
    document.querySelectorAll("#proList .pro-fr").forEach((el) => {
      el.classList.toggle("sel", +el.dataset.id === id);
    });
    const detail = $("#proDetail");
    detail.innerHTML = '<div class="pro-empty">加载中…</div>';
    request(`/api/logs/frames/${id}`).then((f) => {
      let summary = f.summary_json ?? f.summary;
      if (typeof summary === "string") {
        try { summary = JSON.parse(summary); } catch (e) { /* 保持字符串 */ }
      }
      const hint = f.feature_hint;
      const sumText = summary && typeof summary === "object" ? JSON.stringify(summary, null, 2) : String(summary || "—");
      const hintFeature = hint && hint.feature ? hint.feature : null;
      detail.innerHTML = `
        <div class="pro-card"><div class="pro-card-h">解析摘要<span class="sp">frame ${f.id} · ${esc(f.log_time || "")} · ${f.byte_length ?? "?"}B</span></div>
          <div class="pro-card-in">
            ${kv([["frm_type", (summary && summary.FrmType) || rowTitle(f)], ["frame_id", f.id],
                  ["log_time", f.log_time], ["byte_length", f.byte_length]])}
            ${f.parse_error ? `<div style="margin-top:8px;color:var(--perr);font-size:11.5px">⚠ ${esc(f.parse_error)}</div>` : ""}
          </div></div>
        ${hint ? `
        <div class="pro-card pro-hintcard"><div class="pro-card-h">feature_hint · 特征草稿<span class="sp">单帧反推（ADR-9 §5.3）</span></div>
          <div class="pro-card-in">
            ${kv([["frm_type", hintFeature.frm_type], ["app_id", hintFeature.app_id],
                  ["msg_seq", hintFeature.msg_seq || "（留空可升级 campaign）"], ["scope", hint.scope]])}
            ${(hint.tips || []).map((t) => `<div style="margin-top:7px;font-size:11px;color:var(--pam)">◆ ${esc(t)}</div>`).join("")}
            <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
              <a class="pro-btn pro-btn--primary" href="/#trace" target="_top">送报文追踪 →</a>
              <button class="pro-btn" id="proCopyHint">复制特征 JSON</button>
            </div>
          </div></div>` : ""}
        <div class="pro-card"><div class="pro-card-h">原始帧<span class="sp">${esc(f.raw_hex ? `${f.raw_hex.split(" ").length} 字节` : "")}</span></div>
          <div class="pro-card-in"><div class="pro-hex">${esc(f.raw_hex || "（无原始 hex）")}</div></div></div>
        <div class="pro-card"><div class="pro-card-h">summary_json</div>
          <div class="pro-card-in"><div class="pro-json">${esc(sumText)}</div></div></div>`;
      const copyBtn = detail.querySelector("#proCopyHint");
      if (copyBtn) copyBtn.addEventListener("click", () => {
        navigator.clipboard?.writeText(JSON.stringify(hint, null, 2));
        copyBtn.textContent = "已复制";
        setTimeout(() => { copyBtn.textContent = "复制特征 JSON"; }, 1200);
      });
      detail.scrollTop = 0;
    }).catch((err) => {
      detail.innerHTML = `<div class="pro-empty">${esc(err.message)}</div>`;
    });
  }

  /* 首次切入该视图时懒加载 */
  let loaded = false;
  document.querySelector('.view-tab[data-view="frames-pro"]')?.addEventListener("click", () => {
    if (!loaded) { loaded = true; loadFrames(); }
  });
  /* 默认视图即本视图：页面就绪后加载 */
  if (document.querySelector('.view-tab[data-view="frames-pro"]')?.classList.contains("active")) {
    loaded = true;
    loadFrames();
  }

  $("#proReload").addEventListener("click", () => { state.offset = 0; loadFrames(); });
  $("#proPrev").addEventListener("click", () => {
    if (state.offset <= 0) return;
    state.offset = Math.max(0, state.offset - state.limit);
    loadFrames();
  });
  $("#proNext").addEventListener("click", () => {
    if (state.offset + state.limit >= state.total) return;
    state.offset += state.limit;
    loadFrames();
  });
  let debounce = null;
  $("#proQuery").addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.offset = 0; loadFrames(); }, 300);
  });
  $("#proNid").addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.offset = 0; loadFrames(); }, 300);
  });
})();
