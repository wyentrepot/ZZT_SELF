/* 运行时冒烟：jsdom 加载 Demo，遍历 9 页 + 触发关键交互，捕获 console 错误 */
const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const P = 'D:/2-侦听台改造/ui-demo/workbench-ui-demo.html';
const html = fs.readFileSync(P, 'utf8');

const errors = [];
const cssWarn = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => {
  const m = e.message || String(e);
  if (/Could not parse CSS/i.test(m)) cssWarn.push(m);
  else errors.push('jsdomError: ' + m + (e.detail ? '\n   ' + String(e.detail).split('\n')[0] : ''));
});
vc.on('error', (...a) => errors.push('console.error: ' + a.map(String).join(' ')));
['warn', 'log', 'info', 'debug'].forEach(k => vc.on(k, () => {}));

/* canvas 2d 上下文桩（jsdom 不实现 canvas） */
const stubCtx = new Proxy({}, {
  get(t, p) { return typeof p === 'string' ? (() => {}) : undefined; },
  set() { return true; }
});

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  url: 'http://localhost/',
  pretendToBeVisual: true,
  virtualConsole: vc,
  beforeParse(w) { w.HTMLCanvasElement.prototype.getContext = function () { return stubCtx; }; }
});
const { window } = dom;
const doc = window.document;
const $ = s => doc.querySelector(s);
const $$ = s => Array.from(doc.querySelectorAll(s));
const sleep = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
const results = [];
function assert(cond, msg) {
  if (cond) { pass++; results.push('  PASS  ' + msg); }
  else { fail++; results.push('  FAIL  ' + msg); }
}
function click(sel, label) {
  const el = typeof sel === 'string' ? $(sel) : sel;
  const nm = label || (typeof sel === 'string' ? sel : 'element');
  if (!el) { fail++; results.push('  FAIL  点击目标不存在: ' + nm); return false; }
  try { el.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); return true; }
  catch (e) { fail++; results.push('  FAIL  点击异常 ' + nm + ': ' + e.message); return false; }
}
function setVal(sel, v, ev) {
  const el = $(sel);
  if (!el) { fail++; results.push('  FAIL  输入目标不存在: ' + sel); return; }
  el.value = v;
  el.dispatchEvent(new window.Event(ev || 'input', { bubbles: true }));
}

(async () => {
  await sleep(300);
  const sec = t => { results.push('\n[' + t + ']'); };

  /* ---- 外壳 ---- */
  sec('0 外壳');
  assert($$('.nav-it').length === 9, '导航渲染 9 项（实际 ' + $$('.nav-it').length + '）');
  assert($$('.nav-gt').length === 4, '导航 4 个分组');
  assert($('#nav').textContent.includes('验证工作台'), '导航含「验证工作台」');
  assert($('#p-workbench').classList.contains('on'), '默认页为验证工作台');

  for (const id of ['serial-profile', 'module', 'listener', 'simcon', 'trace', 'dict', 'scenario', 'maintenance']) {
    click('.nav-it[data-page="' + id + '"]', 'nav→' + id);
    assert($('#p-' + id).classList.contains('on'), '切换到 ' + id);
  }
  click('.nav-it[data-page="workbench"]');
  assert(window.location.hash === '#workbench', 'hash 路由写入（' + window.location.hash + '）');

  /* ---- 1 验证工作台 ---- */
  sec('1 验证工作台');
  assert($('#scnSel').options.length === 5, '场景下拉 5 项');
  assert($('#scnDesc').textContent.includes('期望流程'), '场景描述渲染');
  click('#btnRun');
  assert($('#wbBadge').textContent === '运行中', '点执行后进入 RUNNING');
  assert($('#btnCancelRun').hidden === false, '取消按钮出现');
  let waited = 0;
  while (waited < 9000 && $('#wbBadge').textContent === '运行中') { await sleep(400); waited += 400; }
  assert($('#wbBadge').textContent !== '运行中', 'Run 到达终态（' + $('#wbBadge').textContent + '）');
  assert($('#runResult').querySelectorAll('.step-item').length > 0, 'Run 结果渲染步骤');
  assert($('#btnCancelRun').hidden === true, '终态后取消按钮隐藏');
  assert(parseInt(($('#evTotal').textContent.match(/\d+/) || [0])[0], 10) > 0, '证据下钻有条目');
  assert($('#histBody').querySelectorAll('tr').length > 0
      && !$('#histBody').textContent.includes('暂无历史'), '历史 Runs 有记录');

  /* ---- 2 串口配置 ---- */
  sec('2 串口配置');
  click('.nav-it[data-page="serial-profile"]');
  assert($$('#slotGrid .slot').length === 4, '四槽渲染');
  const cb = $('#slotGrid .slot .chk input');
  const cb3 = () => $('#slotGrid .slot[data-i="2"] .chk input');
  cb3().checked = true;
  cb3().dispatchEvent(new window.Event('change', { bubbles: true }));
  click('#btnSave');
  assert($('#spError').classList.contains('show'), '槽3 无串口 → 错误汇总条展开');
  cb3().checked = false;
  cb3().dispatchEvent(new window.Event('change', { bubbles: true }));
  assert($('#slotGrid .slot[data-i="2"]').classList.contains('off'), '取消启用后卡片置灰');
  click('#btnSave');
  assert(!$('#spError').classList.contains('show'), '改回后保存校验通过');
  assert($('#saveHint').textContent.includes('已保存'), '保存提示出现');
  click('#btnApply');
  assert($$('#slotGrid .slot-res').some(e => e.textContent), '一键应用给出每槽结果');
  click('#btnSpRefresh');
  assert($$('#slotGrid .slot-res').some(e => e.textContent.includes('在役')), '刷新状态写入槽结果');

  /* ---- 3 模块日志 ---- */
  sec('3 模块日志');
  click('.nav-it[data-page="module"]');
  assert($$('#msSessTabs .sess-tab').length === 2, '2 个会话页签');
  click('#msAddSess');
  assert($$('#msSessTabs .sess-tab').length === 3, '新增会话页');
  click('#msToggle');
  assert($('#msSessStatus').textContent === 'running', '串口启动（running）');
  assert($('#msToggle').textContent.includes('停止'), '按钮切为「停止」');
  assert($$('#msSessTabs .sess-tab.running').length === 1, '页签显示 running 发光点');
  setVal('#msSendText', 'AT+VER');
  click('#msSendBtn');
  assert($('#msLog').textContent.includes('TX> AT+VER'), '串口发送框发出内容');
  assert($('#msSendText').value === '', '发送后清空输入框');
  await sleep(600);
  assert($('#msLog').textContent.includes('RX<'), '收到模拟应答');
  click('#msToggle');
  assert($('#msSessStatus').textContent === 'idle', '串口停止');
  click('#msCloseSess');
  assert($$('#msSessTabs .sess-tab').length === 2, '关闭会话页');
  /* 对照解析 */
  click('.srccard[data-src="file"]');
  assert($('#cmpCfgFile').style.display === 'flex', '切到文件来源');
  setVal('#cmpFile', 'D:\\日志\\cco_20260830.log');
  click('#cmpRunFile');
  assert(parseInt($('#cmpStatLines').textContent, 10) > 0, '对照解析出日志行（' + $('#cmpStatLines').textContent + '）');
  assert($$('#cmpEvList .ev').length > 0, '事件卡渲染');
  assert($('#cmpStatDrift').textContent !== '0', '行号漂移统计非零');
  click($('#cmpEvList .ev'), '选中首个事件卡');
  assert($('#cmpEvList .ev.sel') !== null, '事件卡选中');
  assert($('#cmpLogList .log-line.sel') !== null, '双向绑定：日志行联动高亮');
  const evLn = $('#cmpLogList .log-line.sel').getAttribute('data-ln');
  click($('#cmpLogList .log-line[data-ln="' + evLn + '"]'), '点日志行(行 ' + (+evLn + 1) + ')');
  assert($('#cmpLogList .log-line.sel') !== null, '点事件行仍可高亮（反向联动）');
  click($('#cmpLogList .log-line[data-ln="0"]'), '点无事件的首行');
  assert($('#cmpEvList .ev.sel') === null, '点无事件行清除事件卡选中（符合设计）');
  /* 内嵌 simcon */
  click($$('#msTabs .tab')[2]);
  assert($('#tp-simcon').classList.contains('on'), '切到内嵌模拟集中器页签');
  assert($$('#msRuleList .li').length === 6, '应答规则 6 条');
  click($('#msRuleList .li'));
  assert($('#msRuleCnt').textContent.includes('启用'), '规则开关生效（' + $('#msRuleCnt').textContent + '）');
  click('#msRunTask');
  assert($('#msTaskResult').textContent.includes('verify.demo'), '执行验证任务返回结果');

  /* ---- 4 侦听台 ---- */
  sec('4 侦听台');
  click('.nav-it[data-page="listener"]');
  setVal('#nidFilter', '000');
  click('#btnApplyFilter');
  assert($('#filterSummary').textContent.includes('NID=000'), '全局 NID 筛选生效');
  assert($$('#filterChips .fchip').length >= 1, '筛选 chips 渲染');
  ['frames', 'minute', 'task', 'net', 'pro'].forEach(v => {
    click($$('#lsnTabs .tab').find(t => t.getAttribute('data-tp') === v), '视图→' + v);
    assert($('#lv-' + v).classList.contains('on'), '切换视图 ' + v);
  });
  click($$('#lsnTabs .tab').find(t => t.getAttribute('data-tp') === 'frames'));
  click('#btnLoadLog');
  waited = 0;
  while (waited < 6000 && $('#jobMessage').textContent !== '索引建立完成') { await sleep(300); waited += 300; }
  assert(parseInt($('#frameCount').textContent, 10) > 0, '建立解析索引（' + $('#frameCount').textContent + ' 帧）');
  assert($$('#frameRows tr').length > 0, '帧索引表格有行');
  assert($('#frameRows').innerHTML.includes('data-gi'), '帧行可点击（data-gi）');
  click($('#frameRows tr[data-gi]'), '选中首帧');
  assert($('#detailTitle').textContent.startsWith('帧 #'), '深度解析标题（' + $('#detailTitle').textContent + '）');
  assert($$('#parseStages .stage').length === 4, '解析阶段 4 层');
  assert($('#detailJson').textContent.length > 20, '完整 JSON 输出');
  assert($('#detailRaw').textContent.includes(' '), '原始十六进制输出');
  click($('.tab[data-dt="app"]'), '应用层展开');
  assert($('#detailApp').style.display !== 'none', '应用层展开页签');
  click($('.tab[data-dt="base"]'), '回到基础解析');
  /* 分钟采集（用户点名） */
  click($$('#lsnTabs .tab').find(t => t.getAttribute('data-tp') === 'minute'));
  setVal('#minutePeriod', '0');
  click('#minuteQuery');
  assert($('#minuteErr').classList.contains('show'), '周期越界 → 错误横幅展开');
  setVal('#minutePeriod', '15');
  click('#minuteQuery');
  assert(!$('#minuteErr').classList.contains('show'), '改回合法值 → 错误横幅收起');
  assert($$('#minuteTable tr[data-mi]').length > 0, '分钟采集周期统计有行');
  click($('#minuteTable tr[data-mi]'), '选中周期');
  assert($('#minuteDetail').textContent.includes('条上报'), '周期上报详情渲染');
  assert($('#minuteNidHint').style.display !== 'none', 'NID 提示因锁定显示');
  /* 任务配置 */
  click($$('#lsnTabs .tab').find(t => t.getAttribute('data-tp') === 'task'));
  click('#taskRefresh');
  assert($('#taskSel').options.length > 1 && !$('#taskSel').disabled, '任务号下拉已填充');
  click('#taskQuery');
  assert(parseInt($('#taskSent').textContent, 10) > 0, '6 项统计有值（下发 ' + $('#taskSent').textContent + '）');
  assert($$('#taskStaTable tr').length > 0, 'STA 汇总表有行');
  const before = $('#taskStaTable').textContent;
  click('#taskSortMac');
  assert($('#taskStaTable').textContent !== before, 'STA MAC 排序切换生效');
  assert($('#taskSortMac').textContent.includes('↓'), '排序箭头更新（' + $('#taskSortMac').textContent + '）');
  assert($('#cycleAnalysis').textContent.includes('生命周期'), '轮次分析渲染');
  /* 网络承载 */
  click($$('#lsnTabs .tab').find(t => t.getAttribute('data-tp') === 'net'));
  click('#netAssess');
  assert($('#netBeacon').textContent !== '—', '快照四指标有值（信标 ' + $('#netBeacon').textContent + '）');
  assert($$('#netMetrics .st').length > 0, '网络指标块渲染');
  assert($$('#netCycleRows tr').length > 0, '周期明细表有行');

  /* ---- 5 模拟集中器 ---- */
  sec('5 模拟集中器');
  click('.nav-it[data-page="simcon"]');
  assert($$('#afnList .li').length === 15, 'AFN 15 项');
  click($('#afnList .li'), '选首个 AFN');
  assert($$('#fnList .li').length > 0, 'Fn 联动渲染（' + $$('#fnList .li').length + ' 项）');
  click($('#fnList .li'), '选首个 Fn');
  assert($('#simParamBox').querySelectorAll('input').length > 0, '语义化参数表单渲染');
  assert($('#simFrameMeta').textContent.length > 0, '构帧预览元数据');
  assert($('#simSend').disabled === true, '串口未打开时下发禁用');
  click('#simOpen');
  assert($('#simStatusTxt').textContent.includes('已打开'), '串口打开');
  assert($('#simRespTxt').textContent.includes('已启用'), '内置应答条更新');
  assert($('#simSend').disabled === false, '打开后下发可用');
  click('#simSend');
  assert($('#simTxCnt').textContent === '发 1', '下发后发送计数（' + $('#simTxCnt').textContent + '）');
  await sleep(600);
  assert($('#simRxCnt').textContent === '收 1', '收到应答（' + $('#simRxCnt').textContent + '）');
  assert($$('#simLogList .frlog').length === 2, '收发记录 2 条');
  click($$('#simLogSeg button')[1], '切「发送」分段');
  assert($$('#simLogList .frlog').length === 1, '分段筛选生效');

  /* ---- 6 报文追踪 ---- */
  sec('6 报文追踪');
  click('.nav-it[data-page="trace"]');
  setVal('#trName', '冒烟追踪');
  setVal('#trValue', '00000123');
  click('#trSave');
  assert($('#traceCount').textContent === '1 条', '追踪任务保存（' + $('#traceCount').textContent + '）');
  assert($$('#traceEvid .evid').length === 3, '三段证据链 3 段');
  assert($('#traceEvid').textContent.includes('① 触发帧'), '证据链段名正确');
  click($$('#traceModeSeg button')[1], '切 Live');
  assert($('#traceModeTag').textContent === 'Live', '模式分段切换');

  /* ---- 7 协议字典 ---- */
  sec('7 协议字典');
  click('.nav-it[data-page="dict"]');
  assert($$('#dictList .li').length === 4, '4 本字典');
  assert($('#dictTotalTag').textContent.includes('条目'), '字典/条目计数（' + $('#dictTotalTag').textContent + '）');
  click($('#dictList .li'), '选首本字典');
  assert($$('#dictItems .li').length > 0, '条目渲染');
  click($('#dictItems .li'), '选首条目');
  assert($('#dictDetail').textContent.includes('原始 JSON'), '条目详情渲染');
  setVal('#dictSearch', '采集');
  assert($('#dictTotalTag').textContent !== '4 字典 / 20 条目', '搜索过滤生效（' + $('#dictTotalTag').textContent + '）');
  setVal('#dictSearch', '');

  /* ---- 8 场景脚本 ---- */
  sec('8 场景脚本');
  click('.nav-it[data-page="scenario"]');
  assert($$('#scList .sc-card').length === 5, '场景 5 个');
  click($('#scList .sc-card'), '选首个场景');
  assert($$('#scFlow .flow-it').length === 6, 'expected_flow 时间线 6 步');
  click($('#scFlow .flow-it'), '选首步骤');
  assert($('#scStep').querySelectorAll('.par-tbl').length > 0, '步骤详情语义化参数');
  assert($('#scStep').textContent.includes('步骤 JSON 原文'), '步骤 JSON 原文');
  click('#scnGoWorkbench');
  assert($('#p-workbench').classList.contains('on'), '跳转验证工作台');

  /* ---- 9 工作台状态 + 主题 ---- */
  sec('9 工作台状态 / 主题');
  click('.nav-it[data-page="maintenance"]');
  click('#mntReload');
  assert($('#mVer').textContent.includes('v2.3.2'), '平台信息版本');
  assert($('#mMod').textContent.includes('✓') && $('#mLsn').textContent.includes('✓'), '两个挂载状态 ✓');
  click('.theme-dot[data-t="emerald"]');
  assert(doc.documentElement.className === 'theme-emerald', '主题切换（' + doc.documentElement.className + '）');
  assert($('#sbTheme').textContent === '暗色翡翠', '状态栏主题同步');
  assert($('#mThemeName').textContent === '暗色翡翠', '工作台状态页主题同步');
  click('#btnDensity');
  assert(doc.documentElement.getAttribute('data-density') === 'comfortable', '密度切换');
  click('#btnSide');
  assert($('#app').getAttribute('data-side') === 'mini', '侧栏折叠');
  click('#btnNotes');
  assert($('#drawer').classList.contains('on'), '设计说明抽屉打开');

  /* ---- 汇总 ---- */
  console.log(results.join('\n'));
  console.log('\n' + '='.repeat(56));
  if (cssWarn.length) console.log('  （CSS 解析警告 ' + cssWarn.length + ' 条，jsdom 不支持现代选择器，非代码问题）');
  if (errors.length) {
    console.log('  运行时错误 ' + errors.length + ' 条：');
    errors.slice(0, 30).forEach(e => console.log('   - ' + e));
  } else {
    console.log('  运行时错误：0');
  }
  console.log('  断言：' + pass + ' 通过 / ' + fail + ' 失败');
  console.log('='.repeat(56));
  window.close();
  process.exit(fail || errors.length ? 1 : 0);
})().catch(e => {
  console.log(results.join('\n'));
  console.log('\n!! 冒烟脚本异常: ' + e.message + '\n' + e.stack);
  process.exit(2);
});
