// 验证修复后 module-serial.js 的 bind() 不会因 $("id") 为 null 而中断
// 方法：提取 JS 中 bind() 函数体里所有 $("id").addEventListener，检查 id 是否在 HTML 存在
const fs = require("fs");
const path = require("path");

function validateBind(htmlPath, jsPath, label) {
  const html = fs.readFileSync(htmlPath, "utf-8");
  const js = fs.readFileSync(jsPath, "utf-8");
  const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
  // 提取 bind() 函数体
  const bindMatch = js.match(/function bind\(\) \{([\s\S]*?)\n  \}/);
  if (!bindMatch) throw new Error("未找到 bind()");
  const bindBody = bindMatch[1];
  // bind() 内所有 $("id") 直接调用 .addEventListener 的
  const refs = [...bindBody.matchAll(/\$\("([A-Za-z0-9_-]+)"\)\.addEventListener/g)].map((m) => m[1]);
  const missing = refs.filter((r) => !htmlIds.has(r));
  console.log(`[${label}] bind() 内 addEventListener 目标 ${refs.length} 个，缺失: ${missing.length ? missing.join(", ") : "无 ✓"}`);
  return missing.length;
}

let bad = 0;
bad += validateBind(
  path.join("apps/module_log/static/module-serial.html"),
  path.join("apps/module_log/static/module-serial.js"),
  "独立版"
);
bad += validateBind(
  path.join("apps/workbench/static/pages/module-serial/module-serial.html"),
  path.join("apps/workbench/static/pages/module-serial/module-serial.js"),
  "workbench 副本"
);
console.log(bad ? "存在缺失 → FAIL" : "全部元素齐全 → PASS");
process.exit(bad ? 1 : 0);
