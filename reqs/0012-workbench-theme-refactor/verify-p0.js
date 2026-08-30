/* P0 改动静态校验：提取所有内联 script 验语法 + 核对 color-scheme / 防闪跳覆盖率 */
const fs = require('fs');
const path = require('path');

const ROOT = 'apps/workbench/static';

// Keep the production gate explicit. Local prototypes under static/preview are
// intentionally excluded from delivery and must not affect the P0 result.
const PAGES = [
  'index.html',
  'pages/dict/dict.html',
  'pages/listener/index.html',
  'pages/maintenance/maintenance.html',
  'pages/module-serial/module-serial.html',
  'pages/scenario/scenario.html',
  'pages/serial-profile/serial-profile.html',
  'pages/simcon/simcon.html',
  'pages/trace/trace.html',
  'workbench.html',
].map((file) => path.join(ROOT, file));

const pages = PAGES;
let fail = 0;
const rows = [];

for (const file of pages) {
  const src = fs.readFileSync(file, 'utf8');

  // 1. 内联 script 语法校验（跳过带 src 的外链）
  const re = /<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m, idx = 0, syntaxBad = 0;
  while ((m = re.exec(src)) !== null) {
    idx += 1;
    try {
      new Function(m[1]);
    } catch (e) {
      syntaxBad += 1;
      fail += 1;
      console.log(`  [语法错误] ${file} 第 ${idx} 个 script: ${e.message}`);
    }
  }

  // 2. 防闪跳脚本覆盖率
  const antiFouc = src.includes('localStorage.getItem("wb-theme")');

  // 3. color-scheme 覆盖：本页声明，或所引用的样式表声明
  const ownScheme = /color-scheme\s*:\s*dark/.test(src);
  let inherited = false;
  const linkRe = /<link[^>]+href="([^"]+\.css)[^"]*"/gi;
  let lm;
  while ((lm = linkRe.exec(src)) !== null) {
    const cssPath = lm[1].replace(/^\/static\//, 'apps/workbench/static/').split('?')[0];
    if (fs.existsSync(cssPath) && /color-scheme\s*:\s*dark/.test(fs.readFileSync(cssPath, 'utf8'))) {
      inherited = true;
    }
  }
  const hasScheme = ownScheme || inherited;

  // 4. 渐变引用残留
  const gradientRef = /var\(--bg-gradient\)/.test(src);

  if (!antiFouc) fail += 1;
  if (!hasScheme) fail += 1;
  if (gradientRef) fail += 1;

  rows.push({
    file: file.replace('apps/workbench/static/', ''),
    script: idx,
    语法: syntaxBad === 0 ? 'OK' : `${syntaxBad} 错`,
    防闪跳: antiFouc ? 'Y' : '缺失',
    colorScheme: hasScheme ? 'Y' : '缺失',
    渐变残留: gradientRef ? '有' : '-',
  });
}

console.log('\nP0 静态校验结果\n');
console.log(['文件'.padEnd(46), 'script', '语法', '防闪跳', 'scheme', '渐变'].join('  '));
console.log('-'.repeat(88));
for (const r of rows) {
  console.log([
    r.file.padEnd(46),
    String(r.script).padStart(6),
    r.语法.padStart(4),
    r.防闪跳.padStart(6),
    r.colorScheme.padStart(6),
    r.渐变残留.padStart(4),
  ].join('  '));
}
console.log('-'.repeat(88));
console.log(`共 ${rows.length} 个页面，问题 ${fail} 项`);

// tokens.css 单独核对
const tokens = fs.readFileSync('apps/workbench/static/tokens.css', 'utf8');
const inc = /--st-inconclusive:\s*#a371f7/i.test(tokens);
const frameBg = /--frame-bg:\s*var\(--bg-page\)/.test(tokens);
console.log(`\ntokens.css: inconclusive 独立紫 ${inc ? 'Y' : '缺失'} / frame-bg 实心 ${frameBg ? 'Y' : '缺失'}`);
if (!inc || !frameBg) fail += 1;

process.exit(fail === 0 ? 0 : 1);
