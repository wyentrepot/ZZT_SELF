# tools/skills-eval —— 路由触发 eval

评测「用户话语 → 应加载哪个技能/走哪条组合链」的路由判定质量。候选全集：

- 技能：`wb-base`、`wb-flash`、`wb-listener`、`wb-simeter`、`wb-simcon`、`wb-appframe`、`wb-serial`、`wb-casetest`、`mclt-collect-analysis`
- 组合链：`烧录验证链` / `采集排障链` / `用例回归链`（触发与步骤取自 `ai-control-plane/SKILL.md` §2；读不到该文件时 `run_eval.py` 使用内置兜底文本）

## 文件

| 文件 | 作用 |
| --- | --- |
| `cases.json` | 用例集，每条 `{"id","utterance","expected":{"skill":名}或{"chain":链名},"note"}`，现 15 条 |
| `run_eval.py` | 两种模式：`--prompt` 生成评审 prompt；`--grade answers.json` 批改 |
| `README.md` | 本文件 |

## 两步流程

### 第 1 步：生成 prompt 并交给评审子代理

```bash
cd <仓库根>
python3 tools/skills-eval/run_eval.py --prompt > /tmp/route-prompt.txt
```

`--prompt` 会从技能根（`--skills-root`，默认
`/home/02-skill-fc/skills/shared/hardware-in-the-loop/`；`mclt-collect-analysis`
按 `--extra-root`/上级目录/`/root/.agents/skills`/仓库 `skills/` 顺序自动查找）读取各
SKILL.md 的 frontmatter `description`，打印一份含「候选清单 + 全部用例 + 判定规则 +
输出格式」的评审 prompt。

把 `/tmp/route-prompt.txt` 原文交给一个评审子代理（或任意被评模型），要求它**只返回
JSON 数组**；把它的返回存成 `answers.json`：

```json
[
  {"id": "parse-single-frame", "choice": "wb-appframe"},
  {"id": "flash-and-confirm", "choice": "烧录验证链"}
]
```

`choice` 允许是裸技能名或链名；也接受 `{"skill": ...}` / `{"chain": ...}` 对象形式；
整体被 ```json 围栏包裹也能解析。

### 第 2 步：批改

```bash
python3 tools/skills-eval/run_eval.py --grade answers.json
```

输出逐条 `[PASS]/[FAIL]`（含 expected 与 got）和总通过率，例如：

```
[PASS] parse-single-frame         expected=wb-appframe(skill)  got=wb-appframe
[FAIL] flash-and-confirm          expected=烧录验证链(chain)  got=wb-flash
...
通过 14/15（93.3%）—— FAIL
```

- 退出码：全部通过 `0`，否则 `1`（可直接挂 CI）。
- 缺答案、重复 id、候选之外的 id 均会逐条提示；`choice` 不在候选清单按 FAIL 计。

## 维护

- **加用例**：往 `cases.json` 追加对象即可。`note` 只给维护者/批改者看，**不会**进入
  `--prompt` 输出（避免泄露答案）。覆盖面要求见各条 `note`，至少覆盖：解析单帧报文、
  烧录并确认、查已解析帧、分钟漏点、开串口、模拟电表 645、并发抄表、用例回归（单技能
  与链各一条）、token/401、采集排障链、构 645 帧。
- **改候选/链**：改 `run_eval.py` 顶部的 `SKILL_CANDIDATES` / `CHAINS` /
  `FALLBACK_CHAINS`；链的权威描述以 `ai-control-plane/SKILL.md` §2 为准，由
  `tools/scripts/verify_skills.py` 把关技能组一致性。
- **换技能根**：`--skills-root DIR`（wb-* 技能组），`--extra-root DIR` 可多次指定。
- 全部标准库实现，`python3 run_eval.py --help` 查看参数。
