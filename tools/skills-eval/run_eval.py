#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_eval.py —— 硬件在环技能路由触发 eval。

候选技能：wb-base、wb-flash、wb-listener、wb-simeter、wb-simcon、wb-appframe、
wb-serial、wb-casetest、mclt-collect-analysis；组合链：烧录验证链 / 采集排障链 /
用例回归链（链的触发与步骤取自 ai-control-plane/SKILL.md §2，找不到该文件时用内置兜底）。

两种模式（二选一）：
  --prompt             从技能根读取各 SKILL.md 的 frontmatter description，
                       打印一份评审 prompt（候选清单 + 全部用例）到 stdout，
                       交给被评模型/子代理，要求其对每条用例输出 {"id","choice"} JSON；
  --grade ANSWERS.json 与 cases.json 的 expected 比对，输出通过率与逐条对错。
                       全部通过退出码 0，否则 1。

用法示例：
  python3 run_eval.py --prompt > prompt.txt
  python3 run_eval.py --grade answers.json

仅用 Python 标准库。
"""

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CASES = os.path.join(SCRIPT_DIR, "cases.json")
DEFAULT_SKILLS_ROOT = "/home/02-skill-fc/skills/shared/hardware-in-the-loop"

SKILL_CANDIDATES = [
    "wb-base",
    "wb-flash",
    "wb-listener",
    "wb-simeter",
    "wb-simcon",
    "wb-appframe",
    "wb-serial",
    "wb-casetest",
    "mclt-collect-analysis",
]
CHAINS = ["烧录验证链", "采集排障链", "用例回归链"]

# 兜底组合链描述（与 ai-control-plane/SKILL.md §2 保持一致；能读到 §2 时以 §2 为准）
FALLBACK_CHAINS = {
    "烧录验证链": {
        "trigger": "\"烧一版固件\"、\"烧完确认入网/真烧成\"",
        "steps": "wb-base → wb-serial（preflight+会话）→ wb-flash（烧录判定）→ wb-listener（入网/帧证据）",
    },
    "采集排障链": {
        "trigger": "\"漏点/不入网/这轮抄表断在哪跳\"、\"解释某帧\"",
        "steps": "wb-base → wb-listener（查帧定位）→ wb-simcon/wb-simeter（复现或注入）→ wb-appframe 或 mclt-collect-analysis（离线深解析）",
    },
    "用例回归链": {
        "trigger": "\"跑一遍验证用例\"、\"搭回归\"、\"端到端链路\"",
        "steps": "wb-base → wb-casetest（编排+执行）→ wb-simcon/wb-listener（过程与帧证据）",
    },
}


# ---------- 用例与答案 ----------

def load_cases(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SystemExit("error: 无法读取用例文件 %s：%s" % (path, exc))
    if not isinstance(data, list) or not data:
        raise SystemExit("error: %s 应为非空 JSON 数组" % path)
    problems = []
    for i, case in enumerate(data, 1):
        if not isinstance(case, dict):
            problems.append("第 %d 项不是对象" % i)
            continue
        for key in ("id", "utterance", "expected", "note"):
            if key not in case:
                problems.append("第 %d 项（%s）缺少字段 `%s`" % (i, case.get("id", "?"), key))
        expected = case.get("expected")
        if isinstance(expected, dict):
            kinds = [k for k in ("skill", "chain") if k in expected]
            if len(kinds) != 1:
                problems.append("第 %d 项 expected 必须恰含 skill/chain 之一" % i)
        elif "expected" in case:
            problems.append("第 %d 项 expected 应为对象" % i)
    if problems:
        raise SystemExit("error: %s 校验失败：\n  " % path + "\n  ".join(problems))
    return data


def expected_of(case):
    """返回 (kind, name)，kind 为 'skill' 或 'chain'。"""
    expected = case["expected"]
    if "skill" in expected:
        return "skill", expected["skill"]
    return "chain", expected["chain"]


def strip_code_fence(text):
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def load_answers(path):
    """读取答案文件：JSON 数组 [{"id","choice"},...]，或 {"answers":[...]}；
    允许整体被 ```json ... ``` 包裹。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise SystemExit("error: 无法读取答案文件 %s：%s" % (path, exc))
    try:
        data = json.loads(strip_code_fence(raw))
    except ValueError as exc:
        raise SystemExit("error: %s 不是合法 JSON：%s" % (path, exc))
    if isinstance(data, dict):
        data = data.get("answers", data.get("results"))
    if not isinstance(data, list):
        raise SystemExit("error: %s 应为 JSON 数组（或含 answers 数组的对象）" % path)
    answers = []
    for item in data:
        if isinstance(item, dict) and "id" in item:
            answers.append({"id": str(item["id"]), "choice": item.get("choice")})
    return answers


def normalize_choice(choice):
    """把 choice 归一成裸名：支持 {"skill":..}/{"chain":..}、反引号、引号、空白。"""
    if choice is None:
        return None
    if isinstance(choice, dict):
        choice = choice.get("skill", choice.get("chain"))
    if not isinstance(choice, str):
        return None
    return choice.strip().strip("`\"'").strip()


# ---------- 技能描述与链的装载 ----------

def build_search_roots(skills_root, extra_roots):
    roots = [skills_root]
    roots.extend(extra_roots or [])
    parent = os.path.dirname(os.path.abspath(skills_root))
    roots.extend(
        [
            parent,
            os.path.dirname(parent),
            "/root/.agents/skills",
            os.path.join(os.getcwd(), "skills"),
        ]
    )
    seen, ordered = set(), []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return ordered


def find_skill_md(name, roots):
    for root in roots:
        candidate = os.path.join(root, name, "SKILL.md")
        if os.path.isfile(candidate):
            return candidate
    return None


def read_description(path):
    """读取 SKILL.md frontmatter 的 description；读不到返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = lines[1:i]
            break
    else:
        return None
    for line in fm:
        m = re.match(r"^description\s*:\s*(.*)$", line)
        if m:
            value = m.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
            return value
    return None


def load_chain_table(acp_path):
    """从 ai-control-plane/SKILL.md §2 表格读链（触发、步骤）；读不到用兜底。"""
    chains = {name: dict(FALLBACK_CHAINS[name]) for name in CHAINS}
    if not acp_path:
        return chains, False
    try:
        with open(acp_path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return chains, False
    heading = None
    for i, line in enumerate(lines):
        # 只认 Markdown 标题行：frontmatter description 里也会出现 "§2" 字样
        if line.lstrip().startswith("#") and "§2" in line and ("组合链" in line or "任务旅程" in line):
            heading = i
            break
    if heading is None:
        return chains, False
    found = False
    in_table = False
    for i in range(heading + 1, len(lines)):
        stripped = lines[i].lstrip()
        if stripped.startswith("|"):
            in_table = True
            cells = [c.strip() for c in stripped.rstrip().strip("|").split("|")]
            if len(cells) >= 3 and re.fullmatch(r"[^|]*", cells[0]) and cells[0] in chains:
                if not re.fullmatch(r":?-{2,}:?", cells[0]):
                    chains[cells[0]] = {"trigger": cells[1], "steps": cells[2]}
                    found = True
        elif in_table:
            break
    return chains, found


# ---------- --prompt ----------

def build_prompt(cases, desc_map, chains, chains_from_source):
    out = []
    out.append("# 技能路由评审（hardware-in-the-loop）")
    out.append("")
    out.append("你是路由评审员。下面给出候选技能与组合链清单，以及一组用户话语（utterance）。")
    out.append("请对每条话语判断：应加载哪个技能（单域任务），或应按哪条组合链按序加载（跨域任务）。")
    out.append("")
    out.append("## 一、候选技能（choice 只能取下列裸技能名）")
    out.append("")
    for name in SKILL_CANDIDATES:
        desc = desc_map.get(name)
        if not desc:
            desc = "（未找到 SKILL.md，请人工补充描述）"
        out.append("- %s：%s" % (name, desc))
    out.append("")
    out.append("## 二、组合链（跨域任务按链按序加载，每步用完即止）")
    out.append("")
    for name in CHAINS:
        chain = chains[name]
        out.append("- %s：触发——%s；步骤——%s" % (name, chain["trigger"], chain["steps"]))
    if not chains_from_source:
        out.append("")
        out.append("（链的触发/步骤为内置兜底文本，来源：ai-control-plane §2）")
    out.append("")
    out.append("## 三、判定规则")
    out.append("")
    out.append("1. choice 必须是上面候选清单里的裸技能名或链名，一字不差，不要自造名字。")
    out.append("2. 单域任务只选一个最贴合的技能；任务跨多个域、需要按序配合时选组合链。")
    out.append("3. 手头只有帧/日志/文件、不需要驱动工作台的离线分析，优先选离线专项技能。")
    out.append("4. 拿不准时按话语中最核心的意图判定，不要被次要修饰词带偏。")
    out.append("")
    out.append("## 四、用例（共 %d 条）" % len(cases))
    out.append("")
    for case in cases:
        out.append(json.dumps({"id": case["id"], "utterance": case["utterance"]}, ensure_ascii=False))
    out.append("")
    out.append("## 五、输出格式")
    out.append("")
    out.append("只输出如下形式的 JSON 数组（每条用例一个元素，不要输出任何解释性文字）：")
    out.append("")
    out.append("```json")
    out.append("[")
    out.append('  {"id": "<用例id>", "choice": "<技能名或链名>"},')
    out.append("  ...")
    out.append("]")
    out.append("```")
    return "\n".join(out)


def cmd_prompt(args):
    cases = load_cases(args.cases)
    roots = build_search_roots(args.skills_root, args.extra_root)
    desc_map = {}
    for name in SKILL_CANDIDATES:
        path = find_skill_md(name, roots)
        desc_map[name] = read_description(path) if path else None
    acp_path = find_skill_md("ai-control-plane", roots)
    chains, chains_from_source = load_chain_table(acp_path)
    print(build_prompt(cases, desc_map, chains, chains_from_source))
    return 0


# ---------- --grade ----------

def cmd_grade(args):
    cases = load_cases(args.cases)
    answers = load_answers(args.grade)
    candidates = set(SKILL_CANDIDATES) | set(CHAINS)

    by_id, duplicated = {}, set()
    for ans in answers:
        if ans["id"] in by_id:
            duplicated.add(ans["id"])
        else:
            by_id[ans["id"]] = ans["choice"]

    total = len(cases)
    passed = 0
    lines = []
    for case in cases:
        kind, expected = expected_of(case)
        cid = case["id"]
        got = normalize_choice(by_id.get(cid))
        if cid not in by_id:
            lines.append("[FAIL] %-26s expected=%s(%s)  got=<缺答案>" % (cid, expected, kind))
        elif got == expected:
            passed += 1
            lines.append("[PASS] %-26s expected=%s(%s)  got=%s" % (cid, expected, kind, got))
        elif got not in candidates and got is not None:
            lines.append(
                "[FAIL] %-26s expected=%s(%s)  got=%s（不在候选清单）" % (cid, expected, kind, got)
            )
        else:
            lines.append("[FAIL] %-26s expected=%s(%s)  got=%s" % (cid, expected, kind, got))
    for cid in duplicated:
        lines.append("[WARN] 答案中 id `%s` 出现多次，取第一条" % cid)
    extra = [cid for cid in by_id if cid not in {c["id"] for c in cases}]
    for cid in sorted(extra):
        lines.append("[WARN] 答案中存在用例外的 id `%s`，已忽略" % cid)

    for line in lines:
        print(line)
    pct = (100.0 * passed / total) if total else 0.0
    print("\n通过 %d/%d（%.1f%%）—— %s" % (passed, total, pct, "PASS" if passed == total else "FAIL"))
    return 0 if passed == total else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_eval.py",
        description="硬件在环技能路由触发 eval：--prompt 生成评审 prompt，--grade 批改答案。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "两步流程：\n"
            "  1) python3 run_eval.py --prompt > prompt.txt   # 交给评审子代理\n"
            "  2) 评审子代理返回的 JSON 数组存为 answers.json\n"
            "     python3 run_eval.py --grade answers.json    # 输出通过率与逐条对错\n"
            "退出码：--grade 全部通过为 0，否则 1。"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--prompt", action="store_true",
        help="打印评审 prompt（候选清单 + 全部用例）到 stdout",
    )
    mode.add_argument(
        "--grade", metavar="ANSWERS_JSON",
        help="与 expected 比对，输出通过率与逐条对错",
    )
    parser.add_argument(
        "--cases", default=DEFAULT_CASES,
        help="用例文件路径（默认 %(default)s）",
    )
    parser.add_argument(
        "--skills-root", default=DEFAULT_SKILLS_ROOT,
        help="技能根目录，用于读各 SKILL.md 的 description（默认 %(default)s）",
    )
    parser.add_argument(
        "--extra-root", action="append", default=[], metavar="DIR",
        help="额外技能查找根目录（可多次指定；mclt-collect-analysis 等不在 skills-root 下的技能从这里找）",
    )
    args = parser.parse_args(argv)

    if args.prompt:
        return cmd_prompt(args)
    if args.grade:
        return cmd_grade(args)
    parser.error("请指定 --prompt 或 --grade ANSWERS_JSON（二选一）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
