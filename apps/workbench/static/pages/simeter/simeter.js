/* 模拟电表页面逻辑：状态轮询 / 打开关闭 / 配置 / 帧流 / handle_hex 调测台。
   全部走工作台挂载的 /api/simeter/*（REQS-0035）。 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var lastSeq = 0;
  var timer = null;

  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(function () { t.classList.remove("show"); }, 2200);
  }

  function banner(msg) {
    var b = $("banner");
    if (!msg) { b.style.display = "none"; return; }
    b.textContent = msg;
    b.style.display = "block";
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

  // -- 状态 -------------------------------------------------------------
  function renderStatus(st) {
    var open = !!(st.serial && st.serial.open);
    var pill = $("pill");
    pill.classList.toggle("on", open);
    $("pillText").textContent = open
      ? ("已打开 · " + st.serial.port + " @" + st.serial.baudrate)
      : "未打开";
    $("btnOpen").disabled = open;
    $("btnClose").disabled = !open;
    $("btnSendHex").disabled = !open;
    var kv = $("statusKv");
    var rows = [];
    rows.push(["表地址", st.brain.addr]);
    rows.push(["645版本", st.brain.version === "2007" ? "2007（07）" : "1997（97）"]);
    rows.push(["收帧/应答", st.brain.rx_count + " / " + st.brain.tx_count]);
    if (open) {
      rows.push(["串口", st.serial.port + " · " + st.serial.baudrate + " "
        + st.serial.parity + st.serial.bytesize + st.serial.stopbits]);
      if (st.serial.last_error) rows.push(["最近错误", st.serial.last_error]);
    }
    if (st.session && st.session.session_id) {
      rows.push(["会话", st.session.session_id + "（" + (st.session.frames || 0) + " 帧）"]);
    }
    kv.innerHTML = rows.map(function (r) {
      return '<span class="k">' + r[0] + '</span><span class="mono">' + r[1] + "</span>";
    }).join("");
    if (!$("cfgAddr").value) {
      $("cfgAddr").value = st.brain.addr || "";
      $("cfgVersion").value = st.brain.version === "1997" ? "97" : "07";
    }
  }

  function refresh() {
    return api("GET", "/api/simeter/status").then(renderStatus).catch(function (e) {
      banner("状态获取失败：" + e.message);
    });
  }

  // -- 帧流 --------------------------------------------------------------
  function frameRow(e) {
    var div = document.createElement("div");
    var cls = e.dir === "rx" ? "rx" : e.dir === "tx" ? "tx" : "ev";
    div.className = "fr " + cls;
    var dirText = e.dir === "rx" ? "→表" : e.dir === "tx" ? "表→" : "事件";
    var hex = "";
    if (e.hex) hex = e.hex;
    else if (e.data) hex = String(e.data);
    else if (e.note && e.note.rx_hex) hex = e.note.rx_hex;
    else if (e.note && e.note.reply_hex) hex = e.note.reply_hex;
    div.innerHTML = '<span class="seq">' + e.seq + '</span>'
      + '<span class="dir">' + dirText + "</span>"
      + '<span class="mono">' + (hex || (e.note ? JSON.stringify(e.note).slice(0, 120) : "")) + "</span>";
    return div;
  }

  function pollFrames() {
    var dir = $("dirFilter").value;
    var qs = "?after_seq=" + lastSeq + "&limit=200" + (dir ? "&dir=" + dir : "");
    api("GET", "/api/simeter/frames" + qs).then(function (d) {
      if (!d.session) { $("framesInfo").textContent = "无活动会话（串口未打开）"; return; }
      $("framesInfo").textContent = "会话 " + d.session.session_id + " · 已采 "
        + (d.session.frames || 0) + " 帧 · 视图至 seq " + lastSeq;
      if (!d.entries.length) return;
      var box = $("frames");
      var stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
      d.entries.forEach(function (e) {
        box.appendChild(frameRow(e));
        if (e.seq > lastSeq) lastSeq = e.seq;
      });
      while (box.childNodes.length > 600) box.removeChild(box.firstChild);
      if (stick) box.scrollTop = box.scrollHeight;
    }).catch(function () { /* 轮询失败静默，下轮重试 */ });
  }

  function resetView() {
    lastSeq = 0;
    $("frames").innerHTML = "";
    pollFrames();
  }

  // -- 事件绑定 -----------------------------------------------------------
  $("mapping").addEventListener("change", function () {
    $("port").style.display = $("mapping").value ? "none" : "inline-block";
  });

  $("btnOpen").addEventListener("click", function () {
    var mapping = $("mapping").value;
    var body = { parity: "N" };   // WSL pts 不接受 PARENB；线参由对端决定
    if (mapping) body.mapping_id = mapping;
    else body.port = $("port").value.trim();
    api("POST", "/api/simeter/open", body).then(function (st) {
      renderStatus(st);
      resetView();
      banner("");
      toast("串口已打开");
    }).catch(function (e) { banner("打开失败：" + e.message); });
  });

  $("btnClose").addEventListener("click", function () {
    api("POST", "/api/simeter/close").then(function (st) {
      renderStatus(st);
      $("framesInfo").textContent = "—";
      toast("串口已关闭");
    }).catch(function (e) { banner("关闭失败：" + e.message); });
  });

  $("btnRefresh").addEventListener("click", refresh);

  $("btnSaveCfg").addEventListener("click", function () {
    var addr = $("cfgAddr").value.trim();
    var body = { version: $("cfgVersion").value };
    if (addr) body.addr = addr;
    api("POST", "/api/simeter/config", body).then(function (st) {
      renderStatus(st);
      toast("配置已保存");
    }).catch(function (e) { banner("保存失败：" + e.message); });
  });

  $("btnSetData").addEventListener("click", function () {
    api("POST", "/api/simeter/data", {
      di: $("dataDi").value.trim(), value: $("dataVal").value.trim(),
      scale: parseInt($("dataScale").value, 10) || 0,
    }).then(function () { toast("数据已写入 " + $("dataDi").value); })
      .catch(function (e) { banner("写数据失败：" + e.message); });
  });

  $("btnGetData").addEventListener("click", function () {
    api("GET", "/api/simeter/data").then(function (d) {
      var items = d.registers || {};
      $("dataOut").textContent = Object.keys(items).slice(0, 12).map(function (k) {
        return k + " = " + items[k].value;
      }).join("  |  ");
    }).catch(function (e) { banner("读数据失败：" + e.message); });
  });

  $("btnHandle").addEventListener("click", function () {
    var hex = $("hexIn").value.trim();
    if (!hex) { toast("请输入请求帧 hex"); return; }
    api("POST", "/api/simeter/handle_hex", { hex: hex }).then(function (d) {
      $("hexOut").textContent = d.reply_hex || d.reply_wire_hex || "（不应答）";
      $("hexNote").textContent = d.note ? JSON.stringify(d.note) : "";
    }).catch(function (e) { banner("调测失败：" + e.message); });
  });

  $("btnSendHex").addEventListener("click", function () {
    api("POST", "/api/simeter/send_hex", { hex: $("hexIn").value.trim() })
      .then(function (d) { toast("已注入 " + d.sent_bytes + " 字节"); })
      .catch(function (e) { banner("注入失败：" + e.message); });
  });

  $("btnClearView").addEventListener("click", function () {
    $("frames").innerHTML = "";
  });

  // 轮询：状态 2s / 帧流 1s
  refresh();
  pollFrames();
  timer = setInterval(function () {
    if ($("autoRefresh").checked) pollFrames();
  }, 1000);
  setInterval(function () { if ($("autoRefresh").checked) refresh(); }, 2000);
})();
