#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_skills.py —— 硬件在环技能组一致性校验器。

校验范围：硬件在环技能组，规范源默认
/home/02-skill-fc/skills/shared/hardware-in-the-loop/（可用 --skills-root 覆盖），
成员 = ai-control-plane + 全部 wb-* 同级目录。

检查项（每条违规输出 `文件:行号 消息`，任一 error 级违规退出码 1）：
  a) frontmatter：name == 目录名；description 非空；有 version；有 argument-hint；
  b) 硬编码路径：组内任何 SKILL.md 不得含 /root/.dsh/skills 或 /root/.agents/skills
     （应使用裸技能名引用，如 wb-base）；
  c) 悬空本地引用：正文中形如 references/xxx、scripts/xxx、使用经验/xxx 的相对引用，
     必须在该技能目录内存在（只查这三类 token，不查仓库路径）；
  d) 路由↔目录一致性：ai-control-plane/SKILL.md 的「§1 子技能目录」表必须恰好覆盖
     全部 wb-* 目录（每个目录名在表中出现一次，不得多列不存在的）；
  e) 重复红线：ai-control-plane 不得自带编号红线列表（出现形如 `1. ` 且含
     红线/用完即止/串口独占/观察先建后造 的行即违规——红线唯一来源是 wb-base §6.2）；
  f) 索引可见性：每个成员目录在 /root/.agents/skills/ 下有同名条目
     （缺 → warning 级；--strict 时升级为 error）。

用法：
  python3 verify_skills.py [--skills-root DIR] [--index-root DIR] [--strict] [--json]

退出码：0 = 无 error 级违规；1 = 存在 error 级违规；2 = 参数/环境错误。
仅用 Python 标准库。
"""

import argparse
import json
import os
import re
import sys
from collections import Counter

DEFAULT_SKILLS_ROOT = "/home/02-skill-fc/skills/shared/hardware-in-the-loop"
DEFAULT_INDEX_ROOT = "/root/.agents/skills"
ENTRY_SKILL = "ai-control-plane"
WB_PREFIX = "wb-"

# b) 禁止的硬编码技能索引绝对路径
HARDCODED_INDEX_PATHS = ("/root/.dsh/skills", "/root/.agents/skills")

# c) 本地相对引用前缀：只查这三类 token。
#    仓库路径引用（apps/ tools/ libs/ 开头）相对 <WORKBENCH_ROOT> 解析而非技能目录，
#    本工具不做存在性校验（见 REPO_PATH_PREFIXES）。
# TODO: 后续加 --repo-root 参数后，再对 apps/ tools/ libs/ 开头的仓库路径引用做存在性校验。
REPO_PATH_PREFIXES = ("apps/", "tools/", "libs/")
# 前向否定上下文：token 前不能是路径的一部分（排除 /root/.dsh/skills/xxx/references/ 这类
# 绝对路径里的同名段，以及 tools/scripts/ 这类仓库路径里的同名段）。
LOCAL_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_./\-])(references|scripts|使用经验)"
    r"/([A-Za-z0-9_\-\u4e00-\u9fff.]+)"
)

# e) 红线唯一来源是 wb-base §6.2，ai-control-plane 不得自带编号红线列表
REDLINE_KEYWORDS = ("红线", "用完即止", "串口独占", "观察先建后造")
NUMBERED_LINE_RE = re.compile(r"^\s*\d+\.\s")

SEV_ERROR = "error"
SEV_WARNING = "warning"


def read_lines(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().splitlines()


def split_frontmatter(lines):
    """返回 (frontmatter 行列表, 正文起始 1-based 行号)；无 frontmatter 返回 (None, 1)。"""
    if not lines or lines[0].strip() != "---":
        return None, 1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], i + 2
    return None, 1


def unquote(value):
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].strip()
    return v


def fm_get(fm_lines, key, top_level_only=False):
    """在 frontmatter 里找 key，返回 (value, 1-based 行号)；找不到返回 (None, None)。

    version / argument-hint 允许嵌在 metadata: 下（缩进行），name / description 只认顶层。
    """
    pat = re.compile(
        (r"^" if top_level_only else r"^\s*")
        + re.escape(key)
        + r"\s*:\s*(.*)$"
    )
    for idx, line in enumerate(fm_lines):
        m = pat.match(line)
        if m:
            return m.group(1), idx + 2  # frontmatter 从文件第 2 行开始
    return None, None


class Checker:
    def __init__(self, skills_root, index_root, strict):
        self.skills_root = skills_root
        self.index_root = index_root
        self.strict = strict
        self.findings = []

    def add(self, severity, check, file_label, line, message):
        self.findings.append(
            {
                "severity": severity,
                "check": check,
                "file": file_label,
                "line": line,
                "message": message,
            }
        )

    def rel(self, path):
        rel = os.path.relpath(path, self.skills_root)
        return rel if not rel.startswith("..") else path

    # a) frontmatter 完整性与 name==目录名
    def check_frontmatter(self, skill_name, skill_dir):
        """返回 (lines, body_start)；SKILL.md 缺失时返回 (None, None)。"""
        path = os.path.join(skill_dir, "SKILL.md")
        label = self.rel(path)
        if not os.path.isfile(path):
            self.add(SEV_ERROR, "a-frontmatter", label, 0, "SKILL.md 不存在")
            return None, None
        lines = read_lines(path)
        fm, body_start = split_frontmatter(lines)
        if fm is None:
            self.add(SEV_ERROR, "a-frontmatter", label, 1, "缺少 frontmatter（首行应为 ---）")
            return lines, 1

        name, name_line = fm_get(fm, "name", top_level_only=True)
        if name is None:
            self.add(SEV_ERROR, "a-frontmatter", label, 1, "frontmatter 缺少 name")
        elif unquote(name) != skill_name:
            self.add(
                SEV_ERROR, "a-frontmatter", label, name_line,
                "frontmatter name `%s` 与目录名 `%s` 不一致" % (unquote(name), skill_name),
            )

        desc, _ = fm_get(fm, "description", top_level_only=True)
        if desc is None:
            self.add(SEV_ERROR, "a-frontmatter", label, 1, "frontmatter 缺少 description")
        elif not unquote(desc):
            self.add(SEV_ERROR, "a-frontmatter", label, 1, "description 为空")

        version, _ = fm_get(fm, "version")
        if version is None or not unquote(version):
            self.add(SEV_ERROR, "a-frontmatter", label, 1, "frontmatter 缺少 version（含 metadata.version）")

        hint, _ = fm_get(fm, "argument-hint")
        if hint is None or not unquote(hint):
            self.add(SEV_ERROR, "a-frontmatter", label, 1, "frontmatter 缺少 argument-hint")

        return lines, body_start

    # b) 硬编码技能索引绝对路径
    def check_hardcoded_paths(self, label, lines):
        for lineno, line in enumerate(lines, 1):
            for forbidden in HARDCODED_INDEX_PATHS:
                if forbidden in line:
                    self.add(
                        SEV_ERROR, "b-hardcoded-path", label, lineno,
                        "硬编码索引路径 `%s`（应改为裸技能名引用，如 wb-base）" % forbidden,
                    )

    # c) 悬空本地引用（references/xxx、scripts/xxx、使用经验/xxx）
    def check_local_refs(self, skill_dir, label, lines, body_start):
        # ai-control-plane/references/ 是本技能组的共享参考库（2026-11-05 恢复共享引用时确立），
        # 以 ai-control-plane/ 开头的 token 相对技能组根解析而非当前技能目录。
        shared_prefixes = ("ai-control-plane/",)
        for lineno in range(body_start, len(lines) + 1):
            line = lines[lineno - 1]
            for m in LOCAL_REF_RE.finditer(line):
                token = "%s/%s" % (m.group(1), m.group(2).rstrip("."))
                # 仓库路径引用不做存在性校验（apps/ tools/ libs/ 开头，TODO 见文件头）
                if token.startswith(REPO_PATH_PREFIXES):
                    continue
                if token.startswith(shared_prefixes):
                    target = os.path.join(self.skills_root, token)
                else:
                    target = os.path.join(skill_dir, token)
                if not os.path.exists(target):
                    self.add(
                        SEV_ERROR, "c-dangling-ref", label, lineno,
                        "悬空本地引用 `%s`（技能目录内不存在）" % token,
                    )

    # d) ai-control-plane §1 子技能目录表 ↔ wb-* 目录一致性
    def check_routing_table(self, acp_label, lines, wb_members):
        heading_idx = None
        for i, line in enumerate(lines):
            # 只认 Markdown 标题行：frontmatter 正文里也可能出现 "§1"/"子技能" 字样
            if line.lstrip().startswith("#") and "§1" in line and "子技能目录" in line:
                heading_idx = i
                break
        if heading_idx is None:
            self.add(
                SEV_ERROR, "d-routing-table", acp_label, 0,
                "未找到「§1 子技能目录」标题，无法校验路由表",
            )
            return

        rows = []
        in_table = False
        for i in range(heading_idx + 1, len(lines)):
            stripped = lines[i].lstrip()
            if stripped.startswith("|"):
                in_table = True
                rows.append((i + 1, stripped.rstrip()))
            elif in_table:
                break

        entries = []  # (行号, 首列名)
        for lineno, row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if not cells or not any(cells):
                continue
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue  # 分隔行
            first = cells[0]
            if re.fullmatch(r"[a-z][a-z0-9\-]*", first):
                entries.append((lineno, first))

        counts = Counter(name for _, name in entries)
        member_set = set(wb_members)
        for name in wb_members:
            seen = counts.get(name, 0)
            if seen == 0:
                self.add(
                    SEV_ERROR, "d-routing-table", acp_label, heading_idx + 1,
                    "§1 表缺少子技能 `%s`（目录存在但未入路由表）" % name,
                )
            elif seen > 1:
                self.add(
                    SEV_ERROR, "d-routing-table", acp_label, heading_idx + 1,
                    "§1 表重复列出 `%s`（%d 次）" % (name, seen),
                )
        for lineno, name in entries:
            if name not in member_set:
                self.add(
                    SEV_ERROR, "d-routing-table", acp_label, lineno,
                    "§1 表列出了不存在的子技能目录 `%s`" % name,
                )

    # e) ai-control-plane 不得自带编号红线列表
    def check_redlines(self, acp_label, lines, body_start):
        for lineno in range(body_start, len(lines) + 1):
            line = lines[lineno - 1]
            if NUMBERED_LINE_RE.match(line) and any(k in line for k in REDLINE_KEYWORDS):
                self.add(
                    SEV_ERROR, "e-redline-dup", acp_label, lineno,
                    "ai-control-plane 自带编号红线（红线唯一来源是 wb-base §6.2，此处不得复制）",
                )

    # f) 技能索引可见性（/root/.agents/skills/ 下同名条目）
    def check_index_visibility(self, skill_name):
        if os.path.isdir(os.path.join(self.index_root, skill_name)):
            return
        severity = SEV_ERROR if self.strict else SEV_WARNING
        self.add(
            severity, "f-index-visibility", skill_name, 0,
            "技能索引 %s/ 下缺少同名条目 `%s`" % (self.index_root, skill_name),
        )


def discover_members(skills_root):
    """返回成员目录名列表：ai-control-plane 优先，其余 wb-* 按名排序。"""
    names = [
        n for n in sorted(os.listdir(skills_root))
        if os.path.isdir(os.path.join(skills_root, n))
        and (n == ENTRY_SKILL or n.startswith(WB_PREFIX))
    ]
    if ENTRY_SKILL in names:
        names.remove(ENTRY_SKILL)
        names.insert(0, ENTRY_SKILL)
    return names


def run(skills_root, index_root, strict):
    checker = Checker(skills_root, index_root, strict)
    members = discover_members(skills_root)
    wb_members = [n for n in members if n.startswith(WB_PREFIX)]

    if ENTRY_SKILL not in members:
        checker.add(
            SEV_ERROR, "d-routing-table", "skills-root", 0,
            "技能组缺少入口技能目录 `%s`" % ENTRY_SKILL,
        )
    if not wb_members:
        checker.add(
            SEV_WARNING, "d-routing-table", "skills-root", 0,
            "技能组内未发现任何 %s* 子技能目录" % WB_PREFIX,
        )

    acp_lines, acp_body_start = None, None
    for name in members:
        skill_dir = os.path.join(skills_root, name)
        label = checker.rel(os.path.join(skill_dir, "SKILL.md"))
        lines, body_start = checker.check_frontmatter(name, skill_dir)
        if lines is None:
            checker.check_index_visibility(name)
            continue
        checker.check_hardcoded_paths(label, lines)
        checker.check_local_refs(skill_dir, label, lines, body_start)
        checker.check_index_visibility(name)
        if name == ENTRY_SKILL:
            acp_lines, acp_body_start = lines, body_start

    if acp_lines is not None:
        acp_label = checker.rel(os.path.join(skills_root, ENTRY_SKILL, "SKILL.md"))
        checker.check_routing_table(acp_label, acp_lines, wb_members)
        checker.check_redlines(acp_label, acp_lines, acp_body_start)

    checker.findings.sort(key=lambda f: (f["file"], f["line"], f["check"]))
    errors = sum(1 for f in checker.findings if f["severity"] == SEV_ERROR)
    warnings = sum(1 for f in checker.findings if f["severity"] == SEV_WARNING)
    return {
        "skills_root": os.path.abspath(skills_root),
        "index_root": os.path.abspath(index_root),
        "members": members,
        "findings": checker.findings,
        "summary": {"errors": errors, "warnings": warnings, "passed": errors == 0},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="verify_skills.py",
        description="硬件在环技能组一致性校验器（ai-control-plane + wb-*）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "检查项：\n"
            "  a) frontmatter（name==目录名、description、version、argument-hint）\n"
            "  b) 禁止硬编码 /root/.dsh/skills 或 /root/.agents/skills\n"
            "  c) references/ scripts/ 使用经验/ 本地引用不得悬空\n"
            "  d) ai-control-plane §1 路由表 ↔ wb-* 目录一致\n"
            "  e) ai-control-plane 不得自带编号红线（唯一来源 wb-base §6.2）\n"
            "  f) /root/.agents/skills/ 下有同名条目（warning，--strict 升级）\n"
            "退出码：0=通过；1=存在 error 级违规；2=参数/环境错误。"
        ),
    )
    parser.add_argument(
        "--skills-root", default=DEFAULT_SKILLS_ROOT,
        help="技能组规范源目录（默认 %(default)s）",
    )
    parser.add_argument(
        "--index-root", default=DEFAULT_INDEX_ROOT,
        help="技能索引目录（默认 %(default)s）",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="把 warning 级问题（索引缺失）升级为 error",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="以 JSON 输出结构化结果",
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.skills_root):
        print("error: --skills-root 不存在: %s" % args.skills_root, file=sys.stderr)
        return 2

    result = run(args.skills_root, args.index_root, args.strict)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for f in result["findings"]:
            print("%s:%d [%s] (%s) %s" % (f["file"], f["line"], f["severity"], f["check"], f["message"]))
        summary = result["summary"]
        print(
            "\n成员 %d 个（%s）；错误 %d，警告 %d。结果：%s"
            % (
                len(result["members"]),
                "、".join(result["members"]) or "无",
                summary["errors"],
                summary["warnings"],
                "PASS" if summary["passed"] else "FAIL",
            )
        )

    return 0 if result["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
