// 校验 module-serial.js 中所有 $("id") 引用的元素在 HTML 里是否存在
const fs = require("fs");
const path = require("path");

function check(htmlPath, jsPath, label) {
  const html = fs.readFileSync(htmlPath, "utf-8");
  const js = fs.readFileSync(jsPath, "utf-8");
  // 提取 HTML 中所有 id="..."
  const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  // 提取 JS 中 $("literal")
  const refs = [...js.matchAll(/\$\("([A-Za-z0-9_-]+)"\)/g)].map((m) => m[1]);
  const uniqueRefs = [...new Set(refs)];
  const missing = uniqueRefs.filter((r) => !htmlIds.has(r));
  // 排除动态创建/属性选择器误报
  const knownDynamic = ["ms-session-tabs", "cmp-events", "cmp-lines", "ms-live-output"];
  const realMissing = missing.filter((r) => !knownDynamic.includes(r));
  console.log(`\n[${label}]`);
  console.log(`  JS 引用 ${uniqueRefs.length} 个 id，HTML 有 ${htmlIds.size} 个`);
  console.log(`  缺失元素: ${realMissing.length ? realMissing.join(", ") : "无 ✓"}`);
  return realMissing;
}

let bad = 0;
bad += check(
  path.join("apps/module_log/static/module-serial.html"),
  path.join("apps/module_log/static/module-serial.js"),
  "独立版 module_log"
).length;
bad += check(
  path.join("apps/workbench/static/pages/module-serial/module-serial.html"),
  path.join("apps/workbench/static/pages/module-serial/module-serial.js"),
  "workbench 副本"
).length;
process.exit(bad ? 1 : 0);
