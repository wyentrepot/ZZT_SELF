/* 静态校验：语法 / 悬空 id / 导航↔页面映射 / 标签平衡 */
const fs = require('fs');
const p = 'D:/2-侦听台改造/ui-demo/workbench-ui-demo.html';
const src = fs.readFileSync(p, 'utf8');
let fail = 0;
const ok = (c, m) => { console.log((c ? '  PASS  ' : '  FAIL  ') + m); if (!c) fail++; };

/* 1. JS 语法 */
const m = src.match(/<script>([\s\S]*?)<\/script>/);
console.log('\n[1] JS 语法');
if (!m) { console.log('  FAIL  未找到 script'); fail++; }
else {
  try { new Function(m[1]); console.log('  PASS  new Function 解析通过（' + m[1].length + ' 字符）'); }
  catch (e) { console.log('  FAIL  ' + e.message); fail++; }
}

/* 2. 悬空 id：JS 里 $('#x') 必须在 HTML 存在 */
console.log('\n[2] 悬空 id 引用');
const htmlIds = new Set();
for (const mm of src.matchAll(/\sid="([A-Za-z][\w-]*)"/g)) htmlIds.add(mm[1]);
const jsIds = new Set();
for (const mm of m[1].matchAll(/\$\('#([A-Za-z][\w-]*)'/g)) jsIds.add(mm[1]);
const dangling = [...jsIds].filter(i => !htmlIds.has(i));
ok(dangling.length === 0, dangling.length ? '悬空: ' + dangling.join(', ') : jsIds.size + ' 个引用全部命中');

/* 2b. querySelector('#x') 形式 */
const jsIds2 = new Set();
for (const mm of m[1].matchAll(/querySelector\('#([A-Za-z][\w-]*)'/g)) jsIds2.add(mm[1]);
const dangling2 = [...jsIds2].filter(i => !htmlIds.has(i));
ok(dangling2.length === 0, dangling2.length ? '悬空(qs): ' + dangling2.join(', ') : jsIds2.size + ' 个 querySelector 引用全部命中');

/* 3. 导航 PAGES ↔ <section id="p-*"> */
console.log('\n[3] 导航 ↔ 页面映射');
const navDef = [...m[1].matchAll(/\{id:'([\w-]+)',\s*n:'/g)].map(x => x[1]);
const secIds = [...src.matchAll(/<section class="page[^"]*" id="p-([\w-]+)"/g)].map(x => x[1]);
console.log('  导航定义 (' + navDef.length + '): ' + navDef.join(', '));
console.log('  页面 section (' + secIds.length + '): ' + secIds.join(', '));
ok(navDef.length === 9, '导航 9 项');
ok(secIds.length === 9, '页面 section 9 个');
const onlyNav = navDef.filter(i => !secIds.includes(i));
const onlySec = secIds.filter(i => !navDef.includes(i));
ok(onlyNav.length === 0, onlyNav.length ? '仅导航有: ' + onlyNav.join(', ') : '无仅导航项');
ok(onlySec.length === 0, onlySec.length ? '仅页面有: ' + onlySec.join(', ') : '无仅页面项');

/* 4. 标签开闭平衡（主要容器） */
console.log('\n[4] 标签开闭平衡');
for (const tag of ['div', 'section', 'aside', 'table', 'tbody', 'thead', 'tr', 'td', 'th', 'dl', 'ol', 'ul', 'li', 'span', 'button']) {
  // 注意：JS 里存在 '<tr' + x + '>' 这类拼接，开标签后可能紧跟引号，需一并计入
  const o = (src.match(new RegExp('<' + tag + '(?=[\\s>\\\'"`])', 'g')) || []).length;
  const c = (src.match(new RegExp('</' + tag + '>', 'g')) || []).length;
  if (o !== c) { console.log('  FAIL  <' + tag + '> ' + o + ' 开 / ' + c + ' 闭'); fail++; }
}
if (!fail) console.log('  PASS  所有标签开闭平衡');

/* 5. 主题类 ↔ CSS 定义 */
console.log('\n[5] 主题类定义');
const themeCls = [...m[1].matchAll(/theme-(\w+)/g)].map(x => x[1]).filter((v, i, a) => a.indexOf(v) === i);
const cssThemes = [...src.matchAll(/^html\.theme-(\w+)\{/gm)].map(x => x[1]);
ok(['deepblue', 'emerald', 'charcoal', 'indigo'].every(t => cssThemes.includes(t)),
  'CSS 4 套主题: ' + cssThemes.join(', ') + '（JS 用到: ' + themeCls.join(', ') + '）');

/* 6. 关键功能点存在性 */
console.log('\n[6] 用户点名的三处补回');
ok(src.includes('id="msSendText"') && src.includes('msSend'), '模块日志 · 串口发送框');
ok(src.includes('id="lv-minute"') && src.includes('minuteQuery'), '侦听台 · 分钟采集分析视图');
ok(src.includes('id="nidFilter"') && src.includes('applyFilter'), '侦听台 · 全局 NID 筛选');

console.log('\n' + (fail ? '=== 发现 ' + fail + ' 处问题 ===' : '=== 全部通过 ==='));
process.exit(fail ? 1 : 0);
