// P6 串口配置页：四槽运行 Profile 保存 / 一键应用 / 刷新状态（纯静态，零构建）
(function () {
  "use strict";

  var API_BASE = document.body.dataset.apiBase || "/api";
  function api(suffix) { return API_BASE + suffix; }

  // 四槽定义：slot id / 显示名 / 说明
  var SLOTS = [
    { slot: "module_log.cco", label: "CCO 日志槽", hint: "CCO 主模块日志口" },
    { slot: "module_log.sta", label: "STA 日志槽", hint: "STA 主模块日志口" },
    { slot: "listener.main", label: "侦听台槽", hint: "侦听台采集口" },
    { slot: "simcon.main", label: "模拟集中器槽", hint: "模拟集中器交互口" },
  ];

  var profiles = null;   // GET /api/serial-profile -> {profiles:{slot:{...}}}
  var portDetails = [];  // GET /api/module-serial/ports -> port_details
  var applyResult = {};  // 上次 apply 逐槽结果
  var busy = false;

  var gridEl = document.getElementById("slotGrid");
  var summaryEl = document.getElementById("errorSummary");
  var hintEl = document.getElementById("saveHint");

  function $(id) { return document.getElementById(id); }

  async function request(url, options) {
    var response = await fetch(url, options);
    var body = {};
    try { body = await response.json(); } catch (err) {}
    if (!response.ok) {
      var detail = (body && (body.detail || body.message)) || response.statusText || "请求失败";
      throw new Error(detail);
    }
    return body;
  }

  function escapeHtml(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function showSummary(text) {
    if (!summaryEl) return;
    summaryEl.textContent = text;
    summaryEl.hidden = !text;
    if (text) {
      try { summaryEl.focus(); } catch (err) {}
    }
  }

  function inlineError(container, message) {
    var err = container && container.querySelector(".error");
    if (err) err.textContent = message || "";
  }

  // ---------- 数据加载 ----------
  async function loadProfiles() {
    profiles = await request(api("/serial-profile"));
    return profiles;
  }

  async function loadPorts() {
    try {
      var data = await request(api("/module-serial/ports"));
      portDetails = data.port_details || data.ports || [];
    } catch (err) {
      portDetails = [];
    }
    return portDetails;
  }

  // 从 port_details 提取可选项（映射 + 未映射设备）
  function mappingOptions() {
    var seen = new Set();
    var options = [];
    portDetails.forEach(function (item) {
      var mid = item.mapping_id || "";
      var device = item.device || item.com || item.port || "";
      var label = item.label || mid || device;
      if (mid && !seen.has(mid)) {
        seen.add(mid);
        options.push({ value: mid, text: mid + " · " + label + (item.online ? "（在线）" : "（离线）") });
      }
    });
    return options;
  }

  // ---------- 渲染 ----------
  function renderSlots() {
    if (!gridEl) return;
    gridEl.innerHTML = "";
    SLOTS.forEach(function (def) {
      var entry = profiles && profiles.profiles ? (profiles.profiles[def.slot] || {}) : {};
      var res = applyResult[def.slot] || {};

      var card = document.createElement("section");
      card.className = "slot-card";
      card.dataset.slot = def.slot;

      var head = document.createElement("h2");
      head.textContent = def.label;
      var hint = document.createElement("span");
      hint.className = "slot-id";
      hint.textContent = def.hint + " · " + def.slot;
      head.appendChild(hint);
      card.appendChild(head);

      // 启用
      var enabledRow = document.createElement("div");
      enabledRow.className = "enabled-row";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = "enabled-" + def.slot;
      cb.checked = !!entry.enabled;
      cb.dataset.slot = def.slot;
      var cbLabel = document.createElement("label");
      cbLabel.htmlFor = cb.id;
      cbLabel.textContent = "启用该槽";
      enabledRow.appendChild(cb);
      enabledRow.appendChild(cbLabel);
      card.appendChild(enabledRow);

      // 映射下拉
      var mapField = field("映射串口");
      var sel = document.createElement("select");
      sel.id = "mapping-" + def.slot;
      sel.dataset.slot = def.slot;
      var optNone = document.createElement("option");
      optNone.value = "";
      optNone.textContent = def.slot === "simcon.main" ? "（自动选择可用串口）" : "（未选择）";
      sel.appendChild(optNone);
      mappingOptions().forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.text;
        if (opt.value === entry.mapping_id) o.selected = true;
        sel.appendChild(o);
      });
      mapField.appendChild(sel);
      mapField.appendChild(errorSpan());
      card.appendChild(mapField);

      // 串口参数
      var baudField = field("波特率");
      var baud = document.createElement("input");
      baud.type = "number";
      baud.value = entry.baudrate != null ? entry.baudrate : "";
      baud.dataset.slot = def.slot;
      baud.id = "baud-" + def.slot;
      baudField.appendChild(baud);
      card.appendChild(baudField);

      var parField = field("校验位");
      var parity = document.createElement("select");
      parity.id = "parity-" + def.slot;
      parity.dataset.slot = def.slot;
      ["N", "E", "O", "M", "S"].forEach(function (p) {
        var o = document.createElement("option");
        o.value = p; o.textContent = p;
        if (p === (entry.parity || "N")) o.selected = true;
        parity.appendChild(o);
      });
      parField.appendChild(parity);
      card.appendChild(parField);

      // 状态
      var status = document.createElement("div");
      status.className = "slot-status";
      status.id = "status-" + def.slot;
      status.innerHTML =
        "<dl>" +
        "<dt>状态</dt><dd id='st-" + def.slot + "'>…</dd>" +
        "<dt>占用</dt><dd id='own-" + def.slot + "'>…</dd>" +
        "</dl>";
      card.appendChild(status);

      // 应用结果
      var result = document.createElement("div");
      result.className = "result";
      result.id = "result-" + def.slot;
      card.appendChild(result);

      gridEl.appendChild(card);

      cb.addEventListener("change", function () { onSlotChange(def.slot); });
      sel.addEventListener("change", function () { onSlotChange(def.slot); });
    });
  }

  function field(labelText) {
    var wrapper = document.createElement("div");
    wrapper.className = "field";
    var label = document.createElement("label");
    label.textContent = labelText;
    wrapper.appendChild(label);
    return wrapper;
  }

  function errorSpan() {
    var span = document.createElement("span");
    span.className = "error";
    return span;
  }

  function onSlotChange(slot) {
    // 清除该槽错误
    var card = gridEl.querySelector('[data-slot="' + slot + '"]');
    if (card) inlineError(card, "");
    showSummary("");
  }

  // ---------- 校验 ----------
  function collectProfiles() {
    var out = {};
    var errors = [];
    var usedMapping = new Set();
    SLOTS.forEach(function (def) {
      var cb = $("enabled-" + def.slot);
      var sel = $("mapping-" + def.slot);
      var baud = $("baud-" + def.slot);
      var parity = $("parity-" + def.slot);
      var enabled = !!(cb && cb.checked);
      var mappingId = (sel && sel.value) || "";
      var card = gridEl.querySelector('[data-slot="' + def.slot + '"]');

      if (enabled && !mappingId) {
        errors.push(def.label + "：启用但未选择串口");
        if (card) inlineError(card, "启用该槽必须选择串口");
        return;
      }
      if (enabled && mappingId) {
        if (usedMapping.has(mappingId)) {
          errors.push(def.label + "：映射 " + mappingId + " 与其他槽重复");
          if (card) inlineError(card, "映射与其他槽重复");
          return;
        }
        usedMapping.add(mappingId);
      }
      out[def.slot] = {
        mapping_id: mappingId,
        enabled: enabled,
        baudrate: baud && baud.value ? Number(baud.value) : null,
        parity: parity ? parity.value : "N",
      };
    });
    return { profiles: out, errors: errors };
  }

  // ---------- 动作 ----------
  async function doSave() {
    if (busy) return;
    var collected = collectProfiles();
    if (collected.errors.length) {
      showSummary("无法保存：" + collected.errors.join("；"));
      return;
    }
    busy = true; setBusy(true);
    try {
      var body = await request(api("/serial-profile"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collected),
      });
      profiles = body;
      if (hintEl) hintEl.textContent = "已保存 ✓";
      showSummary("");
      renderSlots();
    } catch (err) {
      showSummary("保存失败：" + err.message);
    } finally {
      busy = false; setBusy(false);
    }
  }

  async function doApply() {
    if (busy) return;
    var collected = collectProfiles();
    if (collected.errors.length) {
      showSummary("请先修正：" + collected.errors.join("；"));
      return;
    }
    busy = true; setBusy(true);
    try {
      var body = await request(api("/serial-profile/apply"), { method: "POST" });
      applyResult = {};
      (body.slots || []).forEach(function (s) {
        applyResult[s.slot] = s;
        var card = gridEl.querySelector('[data-slot="' + s.slot + '"]');
        var resEl = card && card.querySelector(".result");
        if (resEl) {
          resEl.className = "result " + (s.status === "failed" ? "fail" : "ok");
          resEl.textContent = s.status + (s.reason ? "：" + s.reason : "");
        }
      });
      if (hintEl) hintEl.textContent = "一键应用完成 ✓";
      if (body.overall === "ok") showSummary("");
      else showSummary("部分槽未成功：详见各槽应用结果");
      renderSlots();
    } catch (err) {
      showSummary("一键应用失败：" + err.message);
    } finally {
      busy = false; setBusy(false);
    }
  }

  async function doRefresh() {
    busy = true; setBusy(true);
    try {
      await Promise.all([loadProfiles(), loadPorts()]);
      renderSlots();
      if (hintEl) hintEl.textContent = "已刷新状态 ✓";
      showSummary("");
    } catch (err) {
      showSummary("刷新失败：" + err.message);
    } finally {
      busy = false; setBusy(false);
    }
  }

  function setBusy(value) {
    ["btnSave", "btnApply", "btnRefresh"].forEach(function (id) {
      var btn = $(id);
      if (btn) btn.disabled = value;
    });
  }

  // ---------- 绑定 ----------
  var saveBtn = $("btnSave");
  var applyBtn = $("btnApply");
  var refreshBtn = $("btnRefresh");
  if (saveBtn) saveBtn.addEventListener("click", doSave);
  if (applyBtn) applyBtn.addEventListener("click", doApply);
  if (refreshBtn) refreshBtn.addEventListener("click", doRefresh);

  // 初始加载
  (async function init() {
    try {
      await Promise.all([loadProfiles(), loadPorts()]);
      renderSlots();
    } catch (err) {
      showSummary("加载失败：" + err.message);
    }
  })();
})();
