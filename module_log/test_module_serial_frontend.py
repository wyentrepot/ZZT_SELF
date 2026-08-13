"""前端页面单测：用 py_mini_racer(V8) 执行真实 module-serial.js，
注入基于真实 HTML 的 DOM stub + fetch stub，验证前端交互逻辑。

覆盖：串口获取（用户痛点）、双通道独立、发送框回车即发送、隐藏/显示。
依赖：py_mini_racer（含 V8，纯 wheel 无需编译）。
运行：python -m unittest module_log.test_module_serial_frontend
"""
import json
import re
import unittest
from pathlib import Path

from py_mini_racer import MiniRacer

BASE_DIR = Path(__file__).resolve().parent
JS_PATH = BASE_DIR / "static" / "module-serial.js"
HTML_PATH = BASE_DIR / "static" / "module-serial.html"


# ---------- 从真实 HTML 解析元素（id/class/channel），用于构建 DOM stub ----------
def _parse_elements():
    html = HTML_PATH.read_text(encoding="utf-8")
    elements = []
    for m in re.finditer(r"<([a-zA-Z0-9]+)([^>]*)>", html):
        tag = m.group(1).lower()
        attrs = m.group(2)
        if tag in ("html", "head", "meta", "link", "title", "style", "script", "br"):
            continue
        idm = re.search(r'id="([^"]*)"', attrs)
        cm = re.search(r'class="([^"]*)"', attrs)
        dm = re.search(r'data-channel="([^"]*)"', attrs)
        el = {
            "id": idm.group(1) if idm else None,
            "class": cm.group(1).split() if cm else [],
            "channel": dm.group(1) if dm else None,
            "tag": tag,
        }
        if el["id"] or el["channel"]:
            elements.append(el)
    return elements


def _build_dom_stub_js(elements):
    """生成注入 V8 的 DOM stub：makeEl 创建元素，qsa 基于 class+channel 匹配。"""
    def _js_str(s):
        return json.dumps(s)

    ids_json = json.dumps([e["id"] for e in elements if e["id"]])
    creates = []
    for e in elements:
        if e["id"]:
            creates.append(
                "makeEl(%s, %s, %s);" % (
                    _js_str(e["id"]), _js_str(e["channel"]), json.dumps(e["class"]),
                )
            )
        elif e["channel"]:
            creates.append(
                "makeEl(null, %s, %s);" % (_js_str(e["channel"]), json.dumps(e["class"]))
            )
    # HTML 里带 checked 属性的 checkbox 默认勾选
    for _cid in ("ms-send-append-nl", "ms-autoscroll"):
        if any(e["id"] == _cid for e in elements):
            creates.append("__byId[%s].checked = true;" % _js_str(_cid))
    creates_js = "\n".join(creates)
    return r"""
var __byId = {};
var __elements = [];
function makeEl(id, channel, cls) {
  var el = {
    id: id || null, tagName: null, value: "", textContent: "", innerHTML: "",
    hidden: false, disabled: false, checked: false,
    style: { width: "" },
    classList: { toggle: function(c,f){}, add: function(){}, remove: function(){} },
    children: [], options: [], dataset: {}, scrollTop: 0, scrollHeight: 0, childElementCount: 0,
    replaceChildren: function(){ this.children=[]; this.options=[]; this.childElementCount=0; },
    appendChild: function(o){ this.children.push(o); this.childElementCount=this.children.length;
      if(o){ if(o.tagName==='OPTION'||o.__option){ this.options.push(o); } } },
    addEventListener: function(t,f){ (this.__events=this.__events||{})[t]=f; },
    querySelectorAll: function(){ return []; },
  };
  if (channel) el.dataset.channel = channel;
  el.__cls = cls || [];
  if (id) __byId[id] = el;
  __elements.push(el);
  return el;
}
var window = { __ids: __IDS__ };
__CREATES__
function _match(el, cls, channel) {
  if (cls) { if (el.__cls.indexOf(cls) < 0) { return false; } }
  if (channel) { if (el.dataset.channel !== channel) { return false; } }
  return true;
}
function qsa(sel) {
  var out = [];
  var m = sel.match(/^#(.+)$/);
  if (m) { var e = __byId[m[1]]; if (e) out.push(e); return out; }
  m = sel.match(/^\.([a-zA-Z-]+)\[data-channel="(.+)"\]$/);
  if (m) { __elements.forEach(function(e){ if(_match(e, m[1], m[2])) out.push(e); }); return out; }
  m = sel.match(/^\.([a-zA-Z-]+)$/);
  if (m) { __elements.forEach(function(e){ if(_match(e, m[1], null)) out.push(e); }); return out; }
  m = sel.match(/^#([a-zA-Z-]+)$/);
  if (m) { var e2 = __byId[m[1]]; if (e2) out.push(e2); return out; }
  return out;
}
var document = {
  getElementById: function(id){ return __byId[id] || null; },
  querySelector: function(sel){ var r=qsa(sel); return r.length?r[0]:null; },
  querySelectorAll: function(sel){ return qsa(sel); },
  createElement: function(tag){ var e=makeEl(null,null,[]); if(tag){ if(tag.toLowerCase()==='option'){ e.__option=true; e.tagName='OPTION'; } } return e; },
  readyState: "complete",
  addEventListener: function(){},
};
""".replace("__IDS__", ids_json).replace("__CREATES__", creates_js)


def _fetch_stub_js(ports, running_channels=None):
    ports_json = json.dumps(ports)
    running_json = json.dumps(running_channels or [])
    return r"""
function __makeChannels() {
  var chs = {
    cco:{state:"idle",port:"",baudrate:115200,flash:{flashing:false}},
    sta:{state:"idle",port:"",baudrate:115200,flash:{flashing:false}}
  };
  var run = __RUNNING__;
  for (var i=0;i<run.length;i++){
    chs[run[i]] = {state:"running",port:"COM4",baudrate:115200,flash:{flashing:false}};
  }
  return chs;
}
function __fetchStub(url, options) {
  window.__requests = window.__requests || [];
  window.__requests.push({ url: url, options: options || {} });
  var body;
  if (url.indexOf("/api/module-serial/ports") >= 0) {
    body = { ports: __PORTS__ };
  } else if (url.indexOf("/api/module-serial/status") >= 0) {
    body = { state:"idle", channels: __makeChannels() };
  } else if (url.indexOf("/api/module-serial/logs") >= 0) {
    body = { lines: [], last_seq: -1 };
  } else if (url.indexOf("/api/simcon/ports") >= 0) {
    body = { ports: __PORTS__ };
  } else if (url.indexOf("/api/simcon/status") >= 0) {
    body = { open: false, port: null, pending_frames: 0 };
  } else if (url.indexOf("/api/simcon/responders") >= 0) {
    body = { rules: [
      { id: "builtin.01xx_init", builtin: true, match: { afn: 1 }, reply: { afn: 129 } }
    ] };
  } else if (url.indexOf("/api/simcon/verify") >= 0) {
    body = { task_id: "t", port: "COM4", baudrate: 115200,
      steps: [{ name: "s1", result: "pass", sent_hex: "68..", matched: "recv", reason: "" }],
      summary: { total: 1, pass: 1, fail: 0, verdict: "pass" } };
  } else {
    body = {};
  }
  return Promise.resolve({ ok: true, status: 200, statusText: "OK",
    json: function(){ return Promise.resolve(body); } });
}
var fetch = __fetchStub;
var alert = function(m){ window.__alert = window.__alert||[]; window.__alert.push(m); };
var confirm = function(){ return true; };
var setInterval = function(fn, ms){ window.__interval = {fn:fn, ms:ms}; return 1; };
var setTimeout = function(fn, ms){ return 1; };
var clearInterval = function(){};
var console = { log: function(){}, error: function(){}, warn: function(){} };
""".replace("__PORTS__", ports_json).replace("__RUNNING__", running_json)


class FrontendHarness:
    """加载真实 module-serial.js 到 V8，提供 DOM/fetch stub，暴露断言接口。"""

    def __init__(self, ports=None, running_channels=None):
        self.ctx = MiniRacer()
        self.ports = ports if ports is not None else ["COM4", "COM23", "COM3"]
        self.running_channels = running_channels
        self._load()

    def _load(self):
        elements = _parse_elements()
        stub = _build_dom_stub_js(elements)
        self.ctx.eval(stub)
        self.ctx.eval(_fetch_stub_js(self.ports, self.running_channels))
        js = JS_PATH.read_text(encoding="utf-8")
        self.ctx.eval(js)
        self.flush()

    def flush(self, n=30):
        """多次 eval 触发 V8 microtask 队列，让异步 refreshPorts/status 完成。"""
        for _ in range(n):
            self.ctx.eval("void 0")

    # ---- 断言辅助 ----
    def _eval_str(self, js_expr):
        return self.ctx.eval(js_expr)

    def port_options(self, channel):
        raw = self.ctx.eval(
            "JSON.stringify(__byId['ms-port-%s'].options.map(function(o){return o.value;}))" % channel
        )
        return json.loads(raw)

    def text(self, element_id):
        return self.ctx.eval("__byId['%s'].textContent" % element_id)

    def requests(self):
        raw = self.ctx.eval("JSON.stringify(window.__requests ? window.__requests : [])")
        return json.loads(raw)

    def port_select_value(self, channel):
        return self.ctx.eval("__byId['ms-port-%s'].value" % channel)

    def port_disabled(self, channel):
        return bool(self.ctx.eval("__byId['ms-port-%s'].disabled" % channel))

    def baud_disabled(self, channel):
        return bool(self.ctx.eval("__byId['ms-baud-%s'].disabled" % channel))

    def alerts(self):
        raw = self.ctx.eval("JSON.stringify(window.__alert ? window.__alert : [])")
        return json.loads(raw)

    def click(self, selector):
        self.ctx.eval(
            "var _b = document.querySelector(%s); if(_b){ _b.__events.click(); }" % json.dumps(selector)
        )



class ModuleSerialFrontendTest(unittest.TestCase):
    """模拟前端页面功能（真实执行 module-serial.js）。"""

    def test_refresh_ports_populates_both_channel_dropdowns(self):
        """串口获取：/api/module-serial/ports 返回的串口应填入 cco 与 sta 两个下拉框。"""
        ports = ["COM4", "COM23", "COM3", "COM16"]
        h = FrontendHarness(ports=ports)
        h.flush()
        self.assertEqual(h.port_options("cco"), ports)
        self.assertEqual(h.port_options("sta"), ports)

    def test_refresh_ports_shows_placeholder_when_empty(self):
        """无串口时，下拉框应显示“（未发现串口）”占位选项。"""
        h = FrontendHarness(ports=[])
        h.flush()
        self.assertEqual(h.port_options("cco"), [""])
        self.assertEqual(h.text("ms-server-state"), "已连接")

    def test_ports_request_issued(self):
        """应发起 /api/module-serial/ports 请求。"""
        h = FrontendHarness(ports=["COM7"])
        h.flush()
        urls = [r["url"] for r in h.requests()]
        self.assertTrue(any("/api/module-serial/ports" in u for u in urls))

    def test_start_sends_channel_specific_request(self):
        """点击“启动”应向 /api/module-serial/start 发送带 channel=cco 的请求。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.ctx.eval("__byId['ms-port-cco'].value = 'COM4';")
        h.ctx.eval("__byId['ms-baud-cco'].value = '115200';")
        h.click('.ms-toggle[data-channel="cco"]')
        h.flush()
        reqs = h.requests()
        start_reqs = [r for r in reqs if "module-serial/start" in r["url"]]
        self.assertTrue(start_reqs, "应发起 start 请求")
        body = start_reqs[-1]["options"].get("body", "")
        self.assertIn("COM4", body)
        self.assertIn('"channel":"cco"', body)

    def test_send_text_to_channel(self):
        """底部发送框：sendText 应向 /api/module-serial/write_text 发目标通道，默认携带换行。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.ctx.eval("__byId['ms-send-channel'].value = 'sta';")
        h.ctx.eval("__byId['ms-send-text'].value = 'reboot';")
        h.click("#ms-send-btn")
        h.flush()
        reqs = h.requests()
        wt = [r for r in reqs if "write_text" in r["url"]]
        self.assertTrue(wt, "应发起 write_text 请求")
        body = wt[-1]["options"]["body"]
        self.assertIn("reboot", body)
        self.assertIn('"channel":"sta"', body)
        # 默认自动补换行 = true
        self.assertIn('"append_newline":true', body)

    def test_send_text_blank_sends_newline(self):
        """空输入按发送：也应发起 write_text（空内容 + 自动补换行 = 发送一个换行）。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.ctx.eval("__byId['ms-send-text'].value = '';")
        h.click("#ms-send-btn")
        h.flush()
        reqs = h.requests()
        wt = [r for r in reqs if "write_text" in r["url"]]
        self.assertTrue(wt, "空输入也应发起 write_text 请求（发送换行）")
        body = wt[-1]["options"]["body"]
        self.assertIn('"text":""', body)
        self.assertIn('"append_newline":true', body)

    def test_send_newline_button_sends_newline(self):
        """“换行”按钮：只发送一个换行（空文本 + append_newline=true）。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.click("#ms-send-newline")
        h.flush()
        reqs = h.requests()
        wt = [r for r in reqs if "write_text" in r["url"]]
        self.assertTrue(wt, "应发起 write_text 请求")
        body = wt[-1]["options"]["body"]
        self.assertIn('"text":""', body)
        self.assertIn('"append_newline":true', body)

    def test_send_text_append_newline_off(self):
        """关闭“自动补换行”后，发送应携带 append_newline=false。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.ctx.eval("__byId['ms-send-append-nl'].checked = false;")
        h.ctx.eval("__byId['ms-send-text'].value = 'reboot';")
        h.click("#ms-send-btn")
        h.flush()
        reqs = h.requests()
        wt = [r for r in reqs if "write_text" in r["url"]]
        self.assertTrue(wt)
        body = wt[-1]["options"]["body"]
        self.assertIn('"append_newline":false', body)

    def test_refresh_speed_default_medium(self):
        """刷新速度默认中档 500ms。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        self.assertEqual(h._eval_str("window.__pollIntervalMs"), 500)

    def test_refresh_speed_switch_fast_medium_slow(self):
        """切换快/中/慢应分别重建为 100/500/800ms 定时器。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        # 切快
        h.ctx.eval(
            "var _s=__byId['ms-refresh-speed']; _s.value='fast'; _s.__events.change({target:_s});"
        )
        self.assertEqual(h._eval_str("window.__pollIntervalMs"), 100)
        # 切慢
        h.ctx.eval(
            "var _s=__byId['ms-refresh-speed']; _s.value='slow'; _s.__events.change({target:_s});"
        )
        self.assertEqual(h._eval_str("window.__pollIntervalMs"), 800)
        # 切回中
        h.ctx.eval(
            "var _s=__byId['ms-refresh-speed']; _s.value='medium'; _s.__events.change({target:_s});"
        )
        self.assertEqual(h._eval_str("window.__pollIntervalMs"), 500)

    def test_sender_hide_show_toggles(self):
        """发送框隐藏/显示按钮切换。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        self.assertFalse(h._eval_str("__byId['ms-sender'].hidden"))
        h.click("#ms-sender-hide")
        self.assertTrue(h._eval_str("__byId['ms-sender'].hidden"))
        self.assertFalse(h._eval_str("__byId['ms-sender-showbar'].hidden"))
        h.click("#ms-sender-show")
        self.assertFalse(h._eval_str("__byId['ms-sender'].hidden"))

    def test_port_baud_disabled_while_running(self):
        """串口运行时，端口与波特率下拉框应禁用；停止后恢复可用。"""
        h = FrontendHarness(ports=["COM4"], running_channels=["cco"])
        h.flush()
        # cco 处于 running：端口与波特率应被禁用
        self.assertTrue(h.port_disabled("cco"), "串口运行时端口下拉框应禁用")
        self.assertTrue(h.baud_disabled("cco"), "串口运行时波特率下拉框应禁用")
        # sta 处于 idle：不应被禁用
        self.assertFalse(h.port_disabled("sta"), "未运行时端口下拉框应可用")
        self.assertFalse(h.baud_disabled("sta"), "未运行时波特率下拉框应可用")

    def test_port_baud_enabled_when_idle(self):
        """串口空闲时，端口与波特率下拉框应为可用状态。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        self.assertFalse(h.port_disabled("cco"))
        self.assertFalse(h.baud_disabled("cco"))
        self.assertFalse(h.port_disabled("sta"))
        self.assertFalse(h.baud_disabled("sta"))

    # ---------- 模拟集中器（第三页签） ----------
    def test_simcon_tab_button_present(self):
        """应有「模拟集中器」页签按钮，且第三页签面板存在。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        # 第三页签面板存在（有 id，可被 DOM stub 解析）
        self.assertEqual(h._eval_str("!!__byId['ms-tab-simcon']"), True)
        # 页签按钮 data-tab="simcon" 在真实 HTML 中声明
        html = HTML_PATH.read_text(encoding="utf-8")
        self.assertIn('data-tab="simcon"', html)
        self.assertIn("模拟集中器", html)

    def test_simcon_ports_populated_and_requests_issued(self):
        """模拟集中器页：应填充 simcon-port 下拉框并发出 status/ports/responders 请求。"""
        ports = ["COM4", "COM23"]
        h = FrontendHarness(ports=ports)
        h.flush()
        # simcon-port 下拉被填充
        options = json.loads(
            h._eval_str("JSON.stringify(__byId['simcon-port'].options.map(function(o){return o.value;}))")
        )
        self.assertEqual(options, ports)
        # 已发出 simcon 相关请求
        urls = [r["url"] for r in h.requests()]
        self.assertTrue(any("/api/simcon/status" in u for u in urls))
        self.assertTrue(any("/api/simcon/ports" in u for u in urls))
        self.assertTrue(any("/api/simcon/responders" in u for u in urls))
        # 未连接时打开按钮可用、关闭按钮禁用
        self.assertFalse(h._eval_str("__byId['simcon-open'].disabled"))
        self.assertTrue(h._eval_str("__byId['simcon-close'].disabled"))

    def test_simcon_open_sends_open_request(self):
        """点击「打开串口」应向 /api/simcon/open POST 串口与波特率。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.ctx.eval("__byId['simcon-port'].value = 'COM4';")
        h.ctx.eval("__byId['simcon-baud'].value = '115200';")
        h.click("#simcon-open")
        h.flush()
        reqs = h.requests()
        op = [r for r in reqs if "simcon/open" in r["url"]]
        self.assertTrue(op, "应发起 /api/simcon/open 请求")
        body = op[-1]["options"]["body"]
        self.assertIn("COM4", body)
        self.assertIn("115200", body)

    def test_simcon_run_task_renders_verdict(self):
        """执行验证任务后应渲染结论（通过 + 步骤）。"""
        h = FrontendHarness(ports=["COM4"])
        h.flush()
        h.ctx.eval(
            "__byId['simcon-task-input'].value = JSON.stringify({id:'t', port:'COM4', steps:[{name:'s1', send:{afn:0}}]});"
        )
        h.click("#simcon-run-task")
        h.flush()
        # 结论区应显示（fetch stub 返回 verdict=pass）
        self.assertFalse(h._eval_str("__byId['simcon-result'].hidden"))
        verdict_html = h._eval_str("__byId['simcon-result'].innerHTML")
        self.assertIn("通过", verdict_html)
        self.assertIn("1 通过", verdict_html)


if __name__ == "__main__":
    unittest.main()
