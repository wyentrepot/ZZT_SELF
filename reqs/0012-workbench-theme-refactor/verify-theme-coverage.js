/* REQS-0012 P2/P3 静态主题覆盖率门禁。
 * P5 已拍板：RX=青绿(--color-dir-rx)，TX=琥珀(--color-dir-tx)。
 * preview/ 是本地忽略 demo，不属于生产页面。
 *
 * 检查项：
 *   (默认)        遗留方言 / 裸色值 / 防闪脚本白名单 / P5 收发方向契约
 *   body-theme    切换 data-theme 后画布底色必须变化
 * 单独跑某一条：--only=body-theme 或 THEME_CHECK_ONLY=body-theme
 */
const fs = require("fs");
const path = require("path");

const ARGS = process.argv.slice(2);
const ONLY = (() => {
  const flag = ARGS.find((arg) => arg.startsWith("--only="));
  if (flag) return flag.slice("--only=".length).trim();
  return (process.env.THEME_CHECK_ONLY || "").trim();
})();
const KNOWN_CHECKS = ["body-theme"];
if (ONLY && !KNOWN_CHECKS.includes(ONLY)) {
  process.stdout.write(`未知的检查项：${ONLY}（可用：${KNOWN_CHECKS.join(", ")}）\n`);
  process.exit(2);
}
const runLegacy = !ONLY;
const runBodyTheme = !ONLY || ONLY === "body-theme";

const fixtureArg = ARGS.find((arg) => !arg.startsWith("--"));
const fixtureRoot = fixtureArg ? path.resolve(fixtureArg) : path.resolve(__dirname, "../..");
const ROOT = path.join(fixtureRoot, "apps/workbench/static");
const LEGACY_VARS = [
  "bg-", "fg-", "tx-", "ac", "am", "canvas", "panel", "ink",
  "muted", "faint", "cyan",
];
function read(file) { return fs.readFileSync(path.join(ROOT, file), "utf8"); }
function productionPages() {
  const app = read("app.js");
  const pagesArray = app.match(/\bconst\s+PAGES\s*=\s*\[([\s\S]*?)\]/);
  if (!pagesArray) throw new Error("未找到 const PAGES 数组");
  const children = [...pagesArray[1].matchAll(/\bsrc\s*:\s*["']\/static\/([^"']+\.html)["']/g)]
    .map((match) => match[1]);
  return ["index.html", ...children.filter((file, index) => children.indexOf(file) === index)];
}
function cssFiles(html, pageFile) {
  const files = [];
  const re = /<link\b[^>]*\bhref=["']([^"']+\.css)(?:\?[^"']*)?["'][^>]*>/gi;
  let match;
  while ((match = re.exec(html))) {
    const href = match[1];
    if (href.includes("tokens-v2.css")) continue; // token definitions are the source of truth.
    const relative = href.startsWith("/static/")
      ? href.slice("/static/".length)
      : path.relative("", path.join(path.dirname(pageFile), href));
    const candidate = path.normalize(relative);
    if (fs.existsSync(path.join(ROOT, candidate))) files.push(candidate);
  }
  return files;
}

function rawColors(text) {
  const found = new Set();
  const hex = /#[0-9a-f]{3,8}\b/gi;
  const func = /\b(?:rgb|rgba|hsl|hsla)\s*\([^)]*\)/gi;
  for (const re of [hex, func]) {
    let match;
    while ((match = re.exec(text))) found.add(match[0]);
  }
  return [...found];
}
function directionRules(css, callback) {
  let cursor = 0;
  while (cursor < css.length) {
    const open = css.indexOf("{", cursor);
    if (open < 0) break;
    const selector = css.slice(cursor, open).trim();
    let depth = 1;
    let close = open + 1;
    while (close < css.length && depth) {
      if (css[close] === "{") depth += 1;
      else if (css[close] === "}") depth -= 1;
      close += 1;
    }
    const body = css.slice(open + 1, close - 1);
    if (selector.startsWith("@")) directionRules(body, callback);
    else callback(selector, body.replace(/[^{}]*\{[\s\S]*\}/g, ""));
    cursor = close;
  }
}
function hasControlledThemeAssignment(boot) {
  const savedMatch = boot.match(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*localStorage\.getItem\(["']wb-theme["']\)/i);
  if (!savedMatch) return false;
  const saved = savedMatch[1];
  const condition = /if\s*\(([\s\S]*?)\)\s*\{/gi;
  const protectedRanges = [];
  let match;
  while ((match = condition.exec(boot)) !== null) {
    const expression = match[1];
    const includes = new RegExp(`includes\\s*\\(\\s*${saved}\\b`, "i").test(expression);
    const indexOf = new RegExp(`indexOf\\s*\\(\\s*${saved}\\s*\\)\\s*(?:>=\\s*0|!==?\\s*-1|>\\s*-1)`, "i").test(expression);
    if (!includes && !indexOf) continue;
    let depth = 1;
    let cursor = match.index + match[0].length;
    while (cursor < boot.length && depth) {
      if (boot[cursor] === "{") depth += 1;
      else if (boot[cursor] === "}") depth -= 1;
      cursor += 1;
    }
    protectedRanges.push([match.index, cursor]);
  }
  if (!protectedRanges.length) return false;
  const selected = new Set();
  for (const [start, end] of protectedRanges) {
    const block = boot.slice(start, end);
    for (const found of block.matchAll(new RegExp(`(?:var|let|const)?\\s*([A-Za-z_$][\\w$]*)\\s*=\\s*${saved}\\b`, "gi"))) selected.add(found[1]);
  }
  const assignment = /(?:setAttribute\(\s*["']data-theme["']\s*,\s*|dataset\.theme\s*=\s*)([A-Za-z_$][\w$]*)\b/gi;
  let assignmentMatch;
  let found = false;
  while ((assignmentMatch = assignment.exec(boot)) !== null) {
    found = true;
    const value = assignmentMatch[1];
    const protectedDirect = protectedRanges.some(([start, end]) => assignmentMatch.index >= start && assignmentMatch.index < end);
    if (value === saved ? !protectedDirect : !selected.has(value)) return false;
  }
  return found;
}

const issues = [];
let PAGES;
try {
  PAGES = productionPages();
} catch (error) {
  process.stdout.write(`主题覆盖率：0 个生产页面 / 1 issues\n- 无法读取 app.js PAGES: ${error.message}\n`);
  process.exit(1);
}
if (runLegacy && PAGES.length !== 10) issues.push(`app.js PAGES 应注册 9 个子页面，实际 ${PAGES.length - 1}`);

const tokens = read("tokens-v2.css");
function checkDirectionContract() {
  for (const primitive of ["--p-teal-", "--p-amber-"]) {
    if (!new RegExp(`${primitive.replace("-", "\\-")}\\d+\\s*:`).test(tokens)) {
      issues.push(`P5: tokens-v2 缺少 ${primitive} primitive`);
    }
  }
  for (const theme of ["midnight", "daylight"]) {
    const match = tokens.match(new RegExp(`html\\[data-theme=["']${theme}["']\\][^{]*\\{([\\s\\S]*?)\\}`));
    const block = match ? match[1] : "";
    if (!/--color-dir-rx\s*:\s*var\(--p-teal-\w+\)/.test(block)) issues.push(`P5: ${theme} RX 未映射青绿 primitive`);
    if (!/--color-dir-tx\s*:\s*var\(--p-amber-\w+\)/.test(block)) issues.push(`P5: ${theme} TX 未映射琥珀 primitive`);
  }
}
if (runLegacy) checkDirectionContract();

/* ============================================================
   断言：切换 data-theme 后画布底色必须变化（body 背景色主题差分）
   ------------------------------------------------------------
   纯静态分析 —— 只用 Node 内置 fs/path，不引入 jsdom / puppeteer / postcss。

   判定思路：
     1) 把页面真正加载的样式表按文档顺序收集起来：内联 <style> 与 <link>
        外部 CSS 交错排列（含 tokens-v2.css 自身）。这样「页面私有 :root」
        与 tokens-v2.css 落在同一条层叠里，跨文件定义的作用域自然合并。
     2) 按主题分别建立自定义属性作用域：
          · :root / html / html[data-theme]  → 所有主题生效（兜底层）
          · html[data-theme="X"]             → 仅主题 X 生效
        同特异性按文档顺序后者胜出，因此 midnight 挂在 :root 上的兜底值会被
        daylight 的 html[data-theme="daylight"]（特异性更高）正确覆盖。
        主题清单取自 tokens-v2.css 的 --theme-registry，新增主题自动纳入。
     3) 取层叠中最后一条命中 body 的 background / background-color 声明；
        background 简写按顶层逗号切层，背景色只可能出现在最后一层。
     4) 沿 var() 链递归求解到具体色值
        （--frame-bg → --color-bg-canvas → --p-slate-900 → #0a1628）。
     5) body 没有背景色时回退到 html —— 根元素背景会传播到画布。
     6) color-mix() 等无法静态求值的函数不会伪装成通过，单列「需人工确认」。
   ============================================================ */
const MANUAL_REVIEW = [];

function stripComments(text) { return text.replace(/\/\*[\s\S]*?\*\//g, " "); }

/* 逐条输出 (selector, body)，并下钻进 @media / @supports / @layer / @scope。
   var() 与自定义属性的解析与「使用方规则出现的位置」无关，只看 --x 声明
   自身的层叠，所以这里不需要保留 at-rule 上下文。 */
function walkRules(css, onRule) {
  const text = stripComments(css);
  let cursor = 0;
  while (cursor < text.length) {
    const open = text.indexOf("{", cursor);
    if (open < 0) break;
    const selector = text.slice(cursor, open).trim();
    let depth = 1;
    let close = open + 1;
    while (close < text.length && depth) {
      if (text[close] === "{") depth += 1;
      else if (text[close] === "}") depth -= 1;
      close += 1;
    }
    const body = text.slice(open + 1, close - 1);
    cursor = close;
    if (selector.startsWith("@")) {
      if (/^@(?:media|supports|layer|scope|container)\b/i.test(selector)) walkRules(body, onRule);
      continue;
    }
    onRule(selector, body.replace(/@[a-z-]+[^{]*\{(?:[^{}]|\{[^{}]*\})*\}/gi, ""));
  }
}

function declarations(block) {
  const out = [];
  for (const raw of block.split(";")) {
    const idx = raw.indexOf(":");
    if (idx < 0) continue;
    const prop = raw.slice(0, idx).trim();
    const value = raw.slice(idx + 1).trim();
    if (!prop || !value) continue;
    out.push([prop, value]);
  }
  return out;
}

/* 近似特异性：id*10000 + (类/属性/伪类)*100 + (元素/伪元素)。
   本仓变量块只有 :root / html[data-theme] / html[data-theme="x"] 三种形态，
   这个近似足以正确表达「daylight 覆盖 :root 上的 midnight 兜底值」。 */
function specificityOf(part) {
  const text = part.trim();
  const ids = (text.match(/#[\w-]+/g) || []).length;
  const cls = (text.match(/\.[\w-]+|\[[^\]]*\]|::?[a-zA-Z-]+/g) || []).length;
  const els = (text
    .replace(/::?[a-zA-Z-]+(?:\([^()]*\))?/g, " ")
    .replace(/[#.][\w-]+/g, " ")
    .replace(/\[[^\]]*\]/g, " ")
    .match(/(?:^|[\s>+~])[a-zA-Z][\w-]*/g) || []).length;
  return ids * 10000 + cls * 100 + els;
}

/* 变量定义块是否在该主题下生效；返回值 = 生效部分的特异性，null = 不生效。 */
function themeScopeSpec(selector, theme) {
  let best = null;
  for (const raw of selector.split(",")) {
    const part = raw.trim();
    if (!part) continue;
    let spec;
    if (/^(:root|html)$/i.test(part)) {
      spec = specificityOf(part);
    } else {
      const exact = part.match(/^html\[data-theme\s*=\s*["']?([\w-]+)["']?\]$/i);
      if (exact) {
        if (exact[1] !== theme) continue;
        spec = specificityOf(part);
      } else if (/^html\[data-theme\]$/i.test(part)) {
        spec = specificityOf(part);
      } else {
        continue;
      }
    }
    if (best === null || spec > best) best = spec;
  }
  return best;
}

function buildThemeScopes(sheets, themes) {
  const scopes = {};
  for (const theme of themes) scopes[theme] = new Map();
  for (const sheet of sheets) {
    walkRules(sheet.text, (selector, body) => {
      for (const theme of themes) {
        const spec = themeScopeSpec(selector, theme);
        if (spec === null) continue;
        for (const [prop, value] of declarations(body)) {
          if (!prop.startsWith("--")) continue;
          const current = scopes[theme].get(prop);
          if (!current || spec >= current.spec) scopes[theme].set(prop, { value, spec });
        }
      }
    });
  }
  return scopes;
}

function normalizeColor(value) {
  let text = value.trim().toLowerCase().replace(/\s+/g, " ").replace(/\s*([/,])\s*/g, "$1");
  const short = text.match(/^#([0-9a-f]{3}|[0-9a-f]{4})$/);
  if (short) text = `#${short[1].split("").map((ch) => ch + ch).join("")}`;
  return text;
}

function resolveValue(raw, scope, trail) {
  const value = String(raw).trim();
  if (!value) return { kind: "empty" };
  if (/color-mix\s*\(/i.test(value)) return { kind: "unevaluable", reason: "color-mix() 无法静态求值" };
  if (/(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(/i.test(value)) return { kind: "image" };
  const ref = value.match(/^var\(\s*(--[\w-]+)\s*(?:,([\s\S]*))?\)$/);
  if (ref) {
    const name = ref[1];
    if (trail.includes(name)) {
      return { kind: "unevaluable", reason: `变量循环引用 ${trail.concat(name).join(" → ")}` };
    }
    const entry = scope.get(name);
    if (entry) return resolveValue(entry.value, scope, trail.concat(name));
    const fallback = ref[2] ? ref[2].trim() : null;
    if (fallback) return resolveValue(fallback, scope, trail);
    return { kind: "undefined", name, chain: trail.concat(name) };
  }
  if (/var\(\s*--/.test(value)) {
    return { kind: "unevaluable", reason: `复合值中的 var() 无法整体求值：${value}` };
  }
  if (/^(?:#[0-9a-f]{3,8}|(?:rgba?|hsla?)\([^()]*\)|[a-z]+)$/i.test(value)) {
    return { kind: "color", value: normalizeColor(value) };
  }
  return { kind: "unevaluable", reason: `无法识别的颜色值：${value}` };
}

function splitTopLevel(value) {
  const layers = [];
  let depth = 0;
  let current = "";
  for (const ch of value) {
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) {
      layers.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  layers.push(current);
  return layers.map((layer) => layer.trim());
}

const TRAILING_COLOR = /((?:#[0-9a-fA-F]{3,8})|(?:(?:rgba?|hsla?)\([^()]*\))|(?:var\(\s*--[\w-]+[^()]*\))|transparent)\s*$/;

/* background 简写中背景色只能出现在最后一层；最后一层是渐变即无背景色。 */
function backgroundColorOf(value, scope) {
  const layers = splitTopLevel(value);
  const last = layers[layers.length - 1];
  if (!last) return { kind: "empty" };
  const matched = last.match(TRAILING_COLOR);
  if (!matched) {
    if (/(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(/i.test(last)) return { kind: "none" };
    return { kind: "unevaluable", reason: `无法从 background 值中识别背景色：${value}` };
  }
  return resolveValue(matched[1], scope, []);
}

/* 选择器是否作用于 tag 本身（排除伪元素：它们不承载元素自身的背景）。 */
function elementSpec(selector, tag) {
  let best = null;
  for (const raw of selector.split(",")) {
    const part = raw.trim();
    if (!part) continue;
    if (/::?[a-zA-Z-]+/.test(part.replace(/::?[a-zA-Z-]+\([^()]*\)/g, ""))) continue;
    if (new RegExp(`(?:^|[\\s>+~])${tag}$`, "i").test(part)) {
      const spec = specificityOf(part);
      if (best === null || spec > best) best = spec;
    }
  }
  return best;
}

function elementBackground(sheets, tag) {
  let winner = null;
  for (const sheet of sheets) {
    walkRules(sheet.text, (selector, body) => {
      const spec = elementSpec(selector, tag);
      if (spec === null) return;
      for (const [prop, value] of declarations(body)) {
        if (prop !== "background" && prop !== "background-color") continue;
        if (!winner || spec >= winner.spec) winner = { prop, value, spec, source: sheet.name };
      }
    });
  }
  return winner;
}

/* 页面实际加载的样式表序列，内联 <style> 与 <link> 按文档顺序交错。 */
function pageStylesheets(html, pageFile) {
  const sheets = [];
  const re = /<link\b[^>]*\bhref=["']([^"']+\.css)(?:\?[^"']*)?["'][^>]*>|<style\b[^>]*>([\s\S]*?)<\/style>/gi;
  let match;
  while ((match = re.exec(html))) {
    if (match[2] !== undefined) {
      sheets.push({ name: "内联 <style>", text: match[2] });
      continue;
    }
    const href = match[1];
    const relative = href.startsWith("/static/")
      ? href.slice("/static/".length)
      : path.relative("", path.join(path.dirname(pageFile), href));
    const candidate = path.normalize(relative);
    const absolute = path.join(ROOT, candidate);
    if (fs.existsSync(absolute)) {
      sheets.push({ name: candidate.replaceAll("\\", "/"), text: fs.readFileSync(absolute, "utf8") });
    }
  }
  return sheets;
}

/* 主题清单单一数据源：tokens-v2.css 的 --theme-registry。 */
function themeRegistry() {
  const matched = tokens.match(/--theme-registry\s*:\s*([^;]+);/);
  if (!matched) return [];
  return [...matched[1].matchAll(/([\w-]+)\s*\|/g)].map((entry) => entry[1]);
}

function describeResolved(result) {
  if (result.kind === "color") return result.value;
  if (result.kind === "undefined") return `未定义变量 ${result.name}`;
  if (result.kind === "unevaluable") return result.reason;
  if (result.kind === "image") return "渐变（非背景色）";
  return "无背景色";
}

function checkBodyThemeCoverage(pageFile) {
  const label = pageFile.replaceAll("\\", "/");
  const html = read(pageFile);
  const themes = themeRegistry();
  if (themes.length < 2) {
    issues.push(`${label}: 主题覆盖率断言失效 —— --theme-registry 至少需要两套主题，实际 ${themes.length}`);
    return;
  }
  const sheets = pageStylesheets(html, pageFile);
  const linksTokensV2 = sheets.some((sheet) => /(?:^|\/)tokens-v2\.css$/.test(sheet.name));
  const scopes = buildThemeScopes(sheets, themes);

  const bodyRule = elementBackground(sheets, "body");
  const htmlRule = elementBackground(sheets, "html");
  const picked = bodyRule || htmlRule;
  if (!picked) {
    issues.push(`${label}: 主题覆盖率 —— body 与 html 均无 background 声明，切换 data-theme 不会改变画布底色`
      + "（若背景由 .app-shell 之类的子元素承载，请把底色声明上移到 body）");
    return;
  }
  const target = bodyRule ? "body" : "html";
  const where = `${target} ${picked.prop}（${picked.source}）`;
  if (!bodyRule) {
    MANUAL_REVIEW.push(`${label}: body 无 background 声明，画布底色由 html 承载（${where}）—— 已按 html 判定`);
  }

  const resolved = {};
  for (const theme of themes) {
    resolved[theme] = picked.prop === "background-color"
      ? resolveValue(picked.value, scopes[theme], [])
      : backgroundColorOf(picked.value, scopes[theme]);
  }

  if (/color-mix\s*\(/i.test(picked.value)) {
    const known = themes.filter((t) => resolved[t].kind === "color").map((t) => `${t}=${resolved[t].value}`);
    MANUAL_REVIEW.push(`${label}: ${where} 含 color-mix() 无法静态求值；`
      + `可判定的背景色层为 ${known.length ? known.join(" / ") : "无"}，整体观感需人工确认`);
  }

  for (const theme of themes) {
    if (resolved[theme].kind !== "unevaluable") continue;
    MANUAL_REVIEW.push(`${label}: [${theme}] ${where} —— ${resolved[theme].reason}，无法静态判定背景色是否随主题变化`);
  }

  const undefinedThemes = themes.filter((theme) => resolved[theme].kind === "undefined");
  if (undefinedThemes.length) {
    const chain = resolved[undefinedThemes[0]].chain.join(" → ");
    const reason = linksTokensV2
      ? `变量 ${chain} 在 ${undefinedThemes.join("/")} 下无定义`
      : `页面未引入 tokens-v2.css，${chain} 运行时取不到值 —— 看起来接入了 token，实际切换主题背景不会变`;
    issues.push(`${label}: 主题覆盖率 —— ${where} 未解析到具体色值：${reason}`);
    return;
  }

  const colorThemes = themes.filter((theme) => resolved[theme].kind === "color");
  const unevaluable = themes.filter((theme) => resolved[theme].kind === "unevaluable");
  if (colorThemes.length < 2) {
    if (!unevaluable.length) {
      const sample = resolved[themes.find((theme) => resolved[theme].kind !== "color")];
      issues.push(`${label}: 主题覆盖率 —— ${where} 没有可求值的背景色（${describeResolved(sample)}），切换 data-theme 不会改变画布底色`);
    }
    return;
  }
  const seen = new Map();
  for (const theme of colorThemes) {
    const value = resolved[theme].value;
    if (seen.has(value)) {
      issues.push(`${label}: 主题覆盖率 —— ${where} 在 ${seen.get(value)} 与 ${theme} 下同为 ${value}，切换 data-theme 背景不变`);
    } else {
      seen.set(value, theme);
    }
  }
}

for (const file of PAGES) {
  if (runLegacy) checkLegacyPage(file);
  if (runBodyTheme) checkBodyThemeCoverage(file);
}

function checkLegacyPage(file) {
  const html = read(file);
  const label = file.replaceAll("\\", "/");
  const links = [...html.matchAll(/<link\b[^>]*\bhref=["']([^"']+)["']/gi)].map((m) => m[1]);
  if (!links.some((href) => /(?:^|\/)tokens-v2\.css(?:\?|$)/.test(href))) {
    issues.push(`${label}: 缺少直接 tokens-v2.css 引入`);
  }
  const head = (html.match(/<head\b[^>]*>([\s\S]*?)<\/head>/i) || ["", ""])[1];
  const boot = (head.match(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/i) || ["", ""])[1];
  if (!hasControlledThemeAssignment(boot)) {
    issues.push(`${label}: head 防闪脚本每次 data-theme 赋值都必须受主题白名单校验控制`);
  }
  if (links.some((href) => /(?:^|\/)tokens\.css(?:\?|$)/.test(href))) issues.push(`${label}: 禁止引入 tokens.css`);
  if (links.some((href) => /(?:^|\/)compat-dialects\.css(?:\?|$)/.test(href))) issues.push(`${label}: 禁止引入 compat-dialects.css`);
  if (/\.theme-(?!dot\b)[\w-]+\b|\btheme-(?:deepblue|midnight|daylight|light|dark)\b/i.test(html)) {
    issues.push(`${label}: 使用遗留 theme selector`);
  }

  const inlineCss = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)]
    .map((match) => match[1]).join("\n");
  const cssText = `${inlineCss}\n${cssFiles(html, file).map((css) => read(css)).join("\n")}`;
  const scanned = `${html}\n${cssText}`;
  for (const legacy of LEGACY_VARS) {
    const stem = legacy.endsWith("-")
      ? `--${legacy}[\\w-]+`
      : `--${legacy}(?:-[\\w-]+)?`;
    const re = new RegExp(`var\\(${stem}\\b`, "i");
    if (re.test(scanned)) issues.push(`${label}: 遗留变量 --${legacy}*`);
  }
  for (const color of rawColors(scanned)) issues.push(`${label}: CSS raw color literal ${color}`);
  directionRules(cssText, (rawSelector, body) => {
    const selector = rawSelector.toLowerCase();
    if (/(?:\brx\b|receiver|receiving)/.test(selector) && !/var\(--color-dir-rx(?:-soft)?\)/.test(body)) {
      issues.push(`${label}: P5 RX selector 未使用 --color-dir-rx`);
    }
    if (/(?:\btx\b|sender|sending)/.test(selector) && !/var\(--color-dir-tx(?:-soft)?\)/.test(body)) {
      issues.push(`${label}: P5 TX selector 未使用 --color-dir-tx`);
    }
  });
}

const header = ONLY ? `主题覆盖率[仅 ${ONLY}]` : "主题覆盖率";
if (issues.length === 0 && MANUAL_REVIEW.length === 0) {
  process.stdout.write(`${header}：${PAGES.length} 个生产页面 / 0 issues\n`);
  process.exit(0);
}
process.stdout.write(`${header}：${PAGES.length} 个生产页面 / ${issues.length} issues`);
if (MANUAL_REVIEW.length) process.stdout.write(`（另有 ${MANUAL_REVIEW.length} 项需人工确认）`);
process.stdout.write("\n");
for (const issue of issues) process.stdout.write(`- ${issue}\n`);
if (MANUAL_REVIEW.length) {
  process.stdout.write("\n以下项目前无法静态判定，需人工确认：\n");
  for (const item of MANUAL_REVIEW) process.stdout.write(`? ${item}\n`);
}
process.exit(issues.length ? 1 : 0);
