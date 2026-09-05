"""检测用例库生成器（REQS-0025 G1/B1）。

从蒸馏库 `06_测试用例.md` 半自动转换为结构化 JSON：
  - md 表格/小节标题/要点字段 → 条目（entry_type=case / param_table）
  - 条目 id / 分类 / 分组映射由本文件的 MANIFEST 声明（人工核对点）
  - 生成的 data/cases.json 随库分发，运行时只读 cases.json，不依赖蒸馏库路径

用法：
  python -m libs.case_library.generate            # 用默认蒸馏路径重新生成
  python -m libs.case_library.generate --check    # 只校验 data/cases.json 与来源 md 的分类框架一致性

红线：只用蒸馏库国网部分；`南网/` 整目录禁止引用（06_测试用例.md 为国网计量中心蒸馏）。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

DEFAULT_SRC = Path(r"D:\3-obsidian-data\蒸馏\06_测试用例.md")
OUT_PATH = Path(__file__).resolve().parent / "data" / "cases.json"

SOURCE_DOC = "双模通信互联互通测试用例（国网计量中心 2022-01）"
DISTILL = "蒸馏/06_测试用例.md"

CATEGORIES = [
    {"id": "hplc-perf", "name": "HPLC 性能测试", "declared": 7},
    {"id": "wireless-perf", "name": "无线通信性能测试", "declared": 11},
    {"id": "hplc-consistency", "name": "HPLC 协议一致性", "declared": "~120"},
    {"id": "wireless-consistency", "name": "无线协议一致性", "declared": "~80"},
    {"id": "interop", "name": "互操作性测试", "declared": 11},
    {"id": "tester-protocol", "name": "检测线抄控器协议", "declared": None},
    {"id": "henan-pipeline", "name": "河南流水线检测方案", "declared": None},
    {"id": "params", "name": "测试模式/扩展命令参数表", "declared": None},
]

# ---------------------------------------------------------------------------
# markdown 解析基元
# ---------------------------------------------------------------------------

_H_RE = re.compile(r"^(#{2,6})\s+(.*)$")


def _split_sections(text: str) -> dict[str, list[str]]:
    """按 `## N. 标题` 二级小节切分，返回 {小节号: 行列表}。"""
    sections: dict[str, list[str]] = {}
    cur = ""
    for line in text.splitlines():
        m = _H_RE.match(line)
        if m and len(m.group(1)) == 2:
            num = m.group(2).split(".")[0].split(" ")[0]
            cur = num if num.isdigit() else ""
            sections.setdefault(cur, [])
        sections.setdefault(cur, []).append(line)
    return sections


def _find_table(lines: list[str], header_keys: list[str]) -> list[dict[str, str]]:
    """在行区间内找表头包含全部关键词的第一张表，返回 dict 行列表。"""
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        joined = "".join(cells)
        if all(k in joined for k in header_keys):
            header = cells
            rows = []
            for row_line in lines[i + 2:]:
                s = row_line.strip()
                if not s.startswith("|"):
                    break
                vals = [c.strip() for c in s.strip("|").split("|")]
                vals += [""] * (len(header) - len(vals))
                rows.append(dict(zip(header, vals)))
            return rows
    return []


_BOLD_SEC_RE = re.compile(r"^\*\*(\d+(?:\.\d+)*)[\s（(]")


def _sec_token(line: str) -> Optional[str]:
    """标题/加粗行的条款号 token：`#### 2.6 互操作测试`→2.6；`**4.2.1.1 CCO发送…**`→4.2.1.1。"""
    m = _H_RE.match(line)
    if m:
        return m.group(2).split()[0].strip()
    m = _BOLD_SEC_RE.match(line.strip())
    if m:
        return m.group(1)
    return None


def _find_subsection(lines: list[str], sec_no: str) -> list[str]:
    """取条款号恰好等于 sec_no 的标题/加粗段起，到下一个同级标题（或加粗段，仅当起点也是加粗段）之间的行。"""
    start = None
    level = 0
    start_is_bold = False
    for i, line in enumerate(lines):
        token = _sec_token(line)
        if token is None:
            continue
        if start is None:
            if token == sec_no:
                start = i
                m = _H_RE.match(line)
                start_is_bold = m is None
                level = len(m.group(1)) if m else 5
        else:
            is_heading = _H_RE.match(line) is not None
            if is_heading and len(_H_RE.match(line).group(1)) <= level:
                return lines[start:i]
            if not is_heading and start_is_bold:
                return lines[start:i]
    return lines[start:] if start is not None else []


def _find_bullet_block(lines: list[str], title_contains: str) -> list[str]:
    """取 `**N) 标题**` 加粗小节（如 3.1.2.1 的三条消息）之间的行。"""
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r"^\*\*\d+[\)）]", s):
            if start is None:
                if title_contains in s:
                    start = i
            elif title_contains not in s:
                return lines[start:i]
            else:
                return lines[start:i]
    return lines[start:] if start is not None else []


_FIELD_RE = re.compile(r"^-\s*\*\*(.+?)\*\*[：:]\s*(.*)$")


def _parse_fields(lines: list[str]) -> dict[str, Any]:
    """解析 `- **字段**：值` 要点；值跨行合并；`1.` 编号行拆成步骤数组。"""
    fields: dict[str, Any] = {}
    cur: Optional[str] = None
    for line in lines:
        s = line.strip()
        m = _FIELD_RE.match(s)
        if m:
            cur = m.group(1)
            fields[cur] = m.group(2).strip()
            continue
        if not s or s.startswith("#") or s.startswith("---") or s.startswith("**") or s.startswith("|"):
            if s.startswith("|") and cur:
                fields[cur] = (fields[cur] + "\n" + s) if fields.get(cur) else s
            continue
        if cur:
            numbered = re.match(r"^(\d+)[.、]\s*(.*)$", s)
            if numbered:
                if not isinstance(fields[cur], list):
                    fields[cur] = [v for v in [fields[cur]] if v]
                fields[cur].append(numbered.group(2).strip())
            elif fields[cur]:
                if isinstance(fields[cur], list):
                    fields[cur][-1] += s
                else:
                    fields[cur] += s
    return fields


def _num(value: str) -> Optional[int]:
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else None


class Builder:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._seq: dict[str, int] = {}

    def add(self, prefix: str, *, category: str, group: str, name: str,
            protocol: str, entry_type: str = "case", purpose: str = "",
            frames: Optional[list[str]] = None, steps: Optional[list[str]] = None,
            criteria: Optional[list[str]] = None, clause: str = "",
            detail_level: str = "detailed", section: str = "", fields: Optional[dict] = None) -> None:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        entry: dict[str, Any] = {
            "id": f"{prefix}-{self._seq[prefix]:02d}",
            "entry_type": entry_type,
            "category": category,
            "group": group,
            "name": name,
            "protocol": protocol,
            "detail_level": detail_level,
            "source": {"doc": SOURCE_DOC, "distill": DISTILL, "section": section},
        }
        if clause:
            entry["clause"] = clause
        for key, val in (("purpose", purpose), ("frames", frames), ("steps", steps),
                         ("criteria", criteria), ("fields", fields)):
            if val:
                entry[key] = val
        self.entries.append(entry)

    def count(self, prefix: str) -> int:
        return self._seq.get(prefix, 0)


def _split_frames(value: str) -> list[str]:
    parts = re.split(r"[、，,;；/]", value or "")
    return [p.strip() for p in parts if p.strip() and p.strip() != "—"]


# ---------------------------------------------------------------------------
# 各小节 → 条目
# ---------------------------------------------------------------------------

def build(sec: dict[str, list[str]]) -> Builder:
    b = Builder()

    # ---- §2.2 检测条目清单 → HPLC 性能 7 项名称来自清单行 ----
    perf_rows = _find_table(sec["2"], ["类别", "检测条目"])
    perf_names = []
    for row in perf_rows:
        if "HPLC性能" in row.get("类别", ""):
            perf_names = [n.strip() for n in row["检测条目"].split("、")]
            break
    detail_23 = {
        "工作频段及功率谱密度": "2.3.1", "抗白噪声": "2.3.2", "通信速率": "2.3.4",
    }
    for name in perf_names:
        sub = detail_23.get(name, "2.3.3")
        f = _parse_fields(_find_subsection(sec["2"], sub))
        b.add("HPLC-PERF", category="hplc-perf", group="性能测试", name=name, protocol="HPLC",
              purpose=f.get("测试目的", "验证 DUT 在干扰环境下的接收性能（与同类性能测试方法一致）"),
              frames=f.get("涉及帧类型", "") and _split_frames(f["涉及帧类型"]),
              criteria=_as_list(f.get("检查项目") or f.get("判定标准")),
              section=f"§2.2/§{sub}", detail_level="detailed" if f else "framework")

    # ---- §2.4 无线性能 11 项表 ----
    for row in _find_table(_find_subsection(sec["2"], "2.4"), ["测试项", "测试目的", "涉及帧类型"]):
        b.add("RF-PERF", category="wireless-perf", group="性能测试", name=row["测试项"],
              protocol="无线", purpose=row.get("测试目的", ""),
              frames=_split_frames(row.get("涉及帧类型", "")), section="§2.4")

    # ---- §2.5.1 物理层一致性 ----
    f = _parse_fields(_find_subsection(sec["2"], "2.5.1.1"))
    b.add("HPLC-PHY", category="hplc-consistency", group="物理层", name="HPLC TMI 模式遍历测试",
          protocol="HPLC", purpose=f.get("测试目的", ""), frames=_split_frames(f.get("涉及帧类型", "")),
          steps=f.get("测试步骤"), criteria=_as_list(f.get("判定标准")), clause="4.1.1.1", section="§2.5.1.1")
    f = _parse_fields(_find_subsection(sec["2"], "2.5.1.2"))
    b.add("HPLC-PHY", category="hplc-consistency", group="物理层", name="HPLC ToneMask 功能测试",
          protocol="HPLC", purpose=f.get("测试目的", ""), frames=_split_frames(f.get("涉及帧类型", "")),
          steps=_as_list(f.get("测试方法")), clause="4.1.1.2", section="§2.5.1.2")
    f = _parse_fields(_find_subsection(sec["2"], "2.5.1.3"))
    b.add("RF-PHY", category="wireless-consistency", group="物理层", name="无线 Option 和 MCS 模式遍历测试",
          protocol="无线", purpose=f.get("测试目的", ""), frames=_split_frames(f.get("涉及帧类型", "")),
          criteria=_as_list(f.get("测试参数")), clause="4.3.1.1", section="§2.5.1.3")

    # ---- §2.5.2.1 信标机制：4.2.1.1 详例 + 4.2.1.2~4.2.1.6 表 ----
    f = _parse_fields(_find_subsection(sec["2"], "4.2.1.1"))
    b.add("HPLC-DLL-BCN", category="hplc-consistency", group="信标机制",
          name="CCO 发送中央信标的周期性与合法性测试", protocol="HPLC",
          purpose=f.get("测试目的", ""), frames=_split_frames(f.get("涉及帧类型", "")),
          steps=f.get("测试步骤"), criteria=_as_list(f.get("检查项目")),
          clause="4.2.1.1", section="§2.5.2.1")
    for row in _find_table(_find_subsection(sec["2"], "2.5.2.1"), ["测试项", "测试目的", "涉及帧类型"]):
        b.add("HPLC-DLL-BCN", category="hplc-consistency", group="信标机制", name=row["测试项"],
              protocol="HPLC", purpose=row.get("测试目的", ""),
              frames=_split_frames(row.get("涉及帧类型", "")), section="§2.5.2.1")

    # ---- 时隙 / 信道访问 / 选择确认 / 报文过滤 / 时钟同步 / 多网 / 组网 / 网络维护 / 过零 ----
    for sub, prefix, group in (
        ("2.5.2.2", "HPLC-DLL-SLOT", "时隙管理"), ("2.5.2.3", "HPLC-DLL-CSMA", "信道访问"),
        ("2.5.2.5", "HPLC-DLL-SACK", "选择确认重传"), ("2.5.2.6", "HPLC-DLL-FILTER", "报文过滤"),
        ("2.5.2.8", "HPLC-DLL-CLK", "时钟同步"), ("2.5.2.9", "HPLC-DLL-MNET", "多网共存及协调"),
        ("2.5.2.10", "HPLC-DLL-NET", "单网络组网"), ("2.5.2.11", "HPLC-DLL-MAINT", "网络维护"),
    ):
        for row in _find_table(_find_subsection(sec["2"], sub), ["测试项", "测试目的"]):
            frames_col = next((k for k in row if "帧" in k and "类型" in k), None)
            b.add(prefix, category="hplc-consistency", group=group, name=row["测试项"],
                  protocol="HPLC", purpose=row.get("测试目的", ""),
                  frames=_split_frames(row.get(frames_col, "") if frames_col else ""),
                  section=f"§{sub}")

    # ---- §2.5.2.4 数据处理 16 格矩阵 + 2 错误报文 ----
    matrix = _find_table(_find_subsection(sec["2"], "2.5.2.4"), ["MPDU帧载荷长度", "长MAC帧头"])
    header_names = ["长MAC帧头", "短MAC帧头", "分多包MPDU(长MAC)", "分多包MPDU(短MAC)"]
    for row in matrix:
        length = row.get("MPDU帧载荷长度", "")
        for col in header_names:
            clause = row.get(col, "")
            m = re.search(r"4\.2\.4\.\d+", clause)
            if m:
                b.add("HPLC-DLL-DATA", category="hplc-consistency", group="数据处理",
                      name=f"MPDU 载荷 {length} · {col} 处理测试", protocol="HPLC",
                      purpose="验证 DUT 对不同载荷长度和 MAC 帧头格式的 SOF 帧处理能力",
                      frames=["SOF帧", "SACK帧"], clause=m.group(), section="§2.5.2.4")
    b.add("HPLC-DLL-DATA", category="hplc-consistency", group="数据处理",
          name="错误报文处理测试（CCO 侧）", protocol="HPLC",
          purpose="验证 DUT 对错误报文的处理不会造成异常", frames=["错误报文"],
          clause="4.2.4.17", section="§2.5.2.4")
    b.add("HPLC-DLL-DATA", category="hplc-consistency", group="数据处理",
          name="错误报文处理测试（STA 侧）", protocol="HPLC",
          purpose="验证 DUT 对错误报文的处理不会造成异常", frames=["错误报文"],
          clause="4.2.4.18", section="§2.5.2.4")
    b.add("HPLC-DLL-CAST", category="hplc-consistency", group="单播/广播",
          name="单播/全网广播/代理广播/本地广播处理测试", protocol="HPLC",
          purpose="验证 CCO/STA/PCO 对单播、全网广播、代理广播、本地广播报文的处理",
          frames=["单播SOF帧", "全网广播SOF帧", "代理广播SOF帧", "本地广播SOF帧"],
          section="§2.5.2.7", detail_level="framework")

    # ---- §2.5.2.12 过零（两列表） ----
    for row in _find_table(_find_subsection(sec["2"], "2.5.2.12"), ["测试项", "涉及帧类型"]):
        b.add("HPLC-DLL-ZX", category="hplc-consistency", group="过零性能",
              name=row["测试项"], protocol="HPLC",
              frames=_split_frames(row.get("涉及帧类型", "")), section="§2.5.2.12")

    # ---- §2.5.3 无线数据链路层 ----
    for row in _find_table(_find_subsection(sec["2"], "2.5.3.1"), ["测试项", "涉及帧类型"]):
        b.add("RF-DLL-BCN", category="wireless-consistency", group="无线信标机制",
              name=row["测试项"], protocol="无线",
              frames=_split_frames(row.get("涉及帧类型", "")), section="§2.5.3.1")
    f = _parse_fields(_find_subsection(sec["2"], "2.5.3.2"))
    b.add("RF-DLL-ACCESS", category="wireless-consistency", group="无线信道访问",
          name="载波和无线双信道同时收发测试", protocol="双模",
          purpose=f.get("测试目的", ""), frames=_split_frames(f.get("涉及帧类型", "")),
          clause="4.3.3.3", section="§2.5.3.2")
    rf_lengths = ["16", "40", "72", "136", "264", "520"]
    rf_headers = ["单跳MAC帧头", "标准短MAC帧头", "标准长MAC帧头"]
    for length in rf_lengths:
        for header in rf_headers:
            b.add("RF-DLL-DATA", category="wireless-consistency", group="无线数据处理",
                  name=f"MPDU 载荷 {length} · {header} 处理测试", protocol="无线",
                  purpose="验证 DUT 对不同载荷长度与 MAC 帧头格式的无线 SOF 帧处理能力",
                  frames=["无线SOF帧", "无线SACK帧"], section="§2.5.3.3", detail_level="framework")
    for row in _find_table(_find_subsection(sec["2"], "2.5.3.4"), ["测试项", "测试目的"]):
        b.add("RF-DLL-CHNL", category="wireless-consistency", group="无线信道协商",
              name=row["测试项"], protocol="无线", purpose=row.get("测试目的", ""),
              frames=_split_frames(row.get("涉及帧类型", "")), section="§2.5.3.4")

    # ---- §2.5.3 无线对称组（蒸馏文档声明结构与 HPLC 对称但未逐条展开）----
    for group in ("时隙管理", "选择确认重传", "报文过滤", "单播/广播", "时钟同步", "单网络组网", "网络维护"):
        b.add("RF-DLL-MIRROR", category="wireless-consistency", group=f"无线{group}",
              name=f"无线〈{group}〉协议一致性测试组", protocol="无线",
              purpose=f"无线数据链路层〈{group}〉测试结构与 HPLC 对称（蒸馏文档 §2.5.3 声明），"
                      f"原检测文档未逐条展开，待补充原始文档后细化为独立条目。",
              frames=["无线帧"], section="§2.5.3", detail_level="framework",
              fields={"mirror_of": "HPLC 对应组"})

    # ---- §2.5.4 应用层 ----
    for sub, prefix, group in (
        ("2.5.4.1", "HPLC-APP-READ", "抄表"), ("2.5.4.2", "HPLC-APP-REG", "从节点注册"),
        ("2.5.4.3", "HPLC-APP-CLK", "校时"), ("2.5.4.4", "HPLC-APP-EVT", "事件上报"),
        ("2.5.4.5", "HPLC-APP-COMM", "通信测试命令"), ("2.5.4.6", "HPLC-APP-UPG", "在线升级"),
        ("2.5.4.7", "HPLC-APP-TA", "台区户变识别"), ("2.5.4.8", "HPLC-APP-ID", "ID 信息读取"),
    ):
        for row in _find_table(_find_subsection(sec["2"], sub), ["测试项", "测试目的"]):
            frames_col = next((k for k in row if "帧" in k and "类型" in k), None)
            b.add(prefix, category="hplc-consistency", group=group, name=row["测试项"],
                  protocol="HPLC", purpose=row.get("测试目的", ""),
                  frames=_split_frames(row.get(frames_col, "") if frames_col else ""),
                  section=f"§{sub}")

    # ---- §2.5.5 安全算法 14 项 ----
    for row in _find_table(_find_subsection(sec["2"], "2.5.5"), ["测试项", "输入数据"]):
        b.add("HPLC-SEC", category="hplc-consistency", group="安全算法",
              name=row["测试项"], protocol="HPLC",
              purpose="验证安全算法一致性（扩展命令数据域按密钥/IV/公钥/签名分长度域封装）",
              frames=["加密透传扩展命令帧"], section="§2.5.5",
              fields={"输入数据": row.get("输入数据", ""), "输出数据": row.get("输出数据", "")})

    # ---- §2.6 互操作 11 项 ----
    for row in _find_table(_find_subsection(sec["2"], "2.6"), ["测试项", "测试目的"]):
        b.add("IOP", category="interop", group="互操作", name=row["测试项"], protocol="双模",
              purpose=row.get("测试目的", ""), frames=_split_frames(row.get("涉及帧类型", "")),
              section="§2.6")

    # ---- §3 检测线抄控器协议 ----
    for sub, block, name in (("3.1.1.1", "", "射频参数广播配置（STA 检测线）"),
                             ("3.1.1.2", "", "设置抄控模式（STA 检测线）"),
                             ("3.1.1.4", "", "抄读芯片 ID（STA 检测线）"),
                             ("3.1.2.1", "虚拟表地址请求", "虚拟表地址请求（CCO 检测线）"),
                             ("3.1.2.1", "射频参数配置", "射频参数配置（CCO 检测线）"),
                             ("3.1.2.1", "无线射频控制", "无线射频控制（CCO 检测线）")):
        lines_ = _find_subsection(sec["3"], sub)
        if block:
            lines_ = _find_bullet_block(lines_, block)
        f = _parse_fields(lines_)
        if not f:
            raise SystemExit(f"generate: 检测线小节 §{sub}「{block or name}」解析失败（蒸馏 md 结构变更？）")
        b.add("TST-MSG", category="tester-protocol", group="抄控器消息", name=name, protocol="检测线698扩展",
              purpose=f.get("OI", ""), frames=["698 扩展帧"], section=f"§{sub}",
              fields={"OAD": f.get("OAD", ""), "携带字段": f.get("携带字段", ""),
                      "数据类型": f.get("数据类型", ""), "值": f.get("值", "")})
    f = _parse_fields(_find_subsection(sec["3"], "3.1.1.3"))
    b.add("TST-MSG", category="tester-protocol", group="抄控器消息", name="抄表消息（标准 698）",
          protocol="检测线698扩展", purpose="标准 698 抄表（正向有功），抄控器模拟表计应答",
          frames=["698.45 抄表帧"], section="§3.1.1.3")
    for row in _find_table(_find_subsection(sec["4"], "4.1.4"), ["AFN", "Fn", "含义"]):
        b.add("TST-3762", category="tester-protocol", group="抄控器 376.2 扩展",
              name=row.get("含义", ""), protocol="检测线376.2扩展",
              frames=["1376.2 帧"], section="§4.1.4",
              fields={"AFN": row.get("AFN", ""), "Fn": row.get("Fn", ""), "数据内容": row.get("数据内容", "")})
    for row in _find_table(_find_subsection(sec["3"], "3.2.1"), ["步骤", "操作", "涉及帧"]):
        b.add("TST-FLOW", category="tester-protocol", group="STA 双模抄控方案",
              name=row.get("操作", ""), protocol="检测线698扩展",
              frames=[row.get("涉及帧/协议", "")], section="§3.2.1", detail_level="framework")
    for row in _find_table(_find_subsection(sec["3"], "3.2.2"), ["步骤", "操作", "涉及帧"]):
        b.add("TST-FLOW", category="tester-protocol", group="CCO 双模抄控方案",
              name=row.get("操作", ""), protocol="检测线376.2扩展",
              frames=[row.get("涉及帧/协议", "")], section="§3.2.2", detail_level="framework")

    # ---- §5 河南流水线 ----
    for sub, name in (("5.1.1", "载波信道检测阶段"), ("5.1.2", "无线信道检测阶段")):
        rows = _find_table(_find_subsection(sec["5"], sub), ["步骤", "操作"])
        steps = [f"{r.get('步骤', '')} {r.get('操作', '')}（{r.get('涉及协议/命令', '')}）" for r in rows]
        b.add("HN-STAGE", category="henan-pipeline", group="流水线检测步骤", name=name,
              protocol="河南流水线", steps=steps, section=f"§{sub}", detail_level="detailed" if steps else "framework")
    b.add("HN-EXT", category="henan-pipeline", group="1376.2 扩展命令",
          name="AFN=03 F18 读取组网方式", protocol="河南流水线",
          frames=["1376.2 扩展帧"], section="§5.2.3",
          fields={"数据内容": "组网方式 BIN 1 字节（0=混合组网，1=仅载波组网，2=仅无线组网）"})
    b.add("HN-EXT", category="henan-pipeline", group="1376.2 扩展命令",
          name="AFN=05 F18 设置组网方式", protocol="河南流水线",
          frames=["1376.2 扩展帧"], section="§5.2.3",
          fields={"数据内容": "组网方式 BIN 1 字节；上行报文为确认/否认帧"})
    b.add("HN-EXT", category="henan-pipeline", group="判定标准",
          name="载波/无线数据一致性判定", protocol="河南流水线",
          purpose="CCO 接收 STA 应答时，只有当接收信道与组网信道相同，且载波抄读与无线抄读数据相同时，才认为抄读成功",
          section="§5.2.1")

    # ---- §2.1.1 参数表 / §4.1.3-4.1.4 检测线扩展 / §5.2.1 无线信标标志 ----
    for row in _find_table(_find_subsection(sec["2"], "2.1.1"), ["值", "含义"]):
        if not row["值"].strip().isdigit():
            continue
        b.add("PARAM-MODE", category="params", group="测试模式扩展命令", entry_type="param_table",
              name=row["含义"], protocol="测试模式", section="§2.1.1",
              fields={"值": row["值"], "模式持续时间/配置值": row.get("模式持续时间/配置值", "")})
    for row in _find_table(_find_subsection(sec["2"], "2.1.1"), ["安全算法"]):
        b.add("PARAM-SEC", category="params", group="安全测试模式", entry_type="param_table",
              name=row["安全算法"], protocol="测试模式", section="§2.1.1",
              fields={"值": row["值"]})
    for row in _find_table(_find_subsection(sec["4"], "4.1.3"), ["OAD", "含义"]):
        b.add("PARAM-OAD", category="params", group="检测线扩展 OAD", entry_type="param_table",
              name=row.get("含义", ""), protocol="检测线698扩展", section="§4.1.3",
              fields={"OAD": row.get("OAD", ""), "属性": row.get("属性", ""), "适用场景": row.get("适用场景", "")})
    henan_3762 = _find_table(_find_subsection(sec["6"], "6.2"), ["AFN/Fn"])
    for row in henan_3762:
        meaning = next(
            (v for v in (row.get("国网通用定义"), row.get("河南扩展")) if v and v.strip() != "—"),
            "",
        )
        if not meaning:
            continue
        b.add("PARAM-3762", category="params", group="检测线/河南扩展 376.2", entry_type="param_table",
              name=meaning, protocol="检测线376.2扩展", section="§6.2",
              fields={"AFN/Fn": row.get("AFN/Fn", ""), "国网通用定义": row.get("国网通用定义", ""),
                      "河南扩展": row.get("河南扩展", "")})
    for row in _find_table(_find_subsection(sec["6"], "6.3"), ["值", "定义"]):
        b.add("PARAM-BCNFLAG", category="params", group="无线信标标志", entry_type="param_table",
              name=row.get("定义", ""), protocol="双模", section="§6.3",
              fields={"值": row["值"], "应用场景": row.get("应用场景", "")})

    return b


def _as_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value).strip()
    if not text:
        return None
    return [p.strip() for p in re.split(r"[；;\n]", text) if p.strip()]


def build_library(src: Path) -> dict[str, Any]:
    text = src.read_text(encoding="utf-8")
    sec = _split_sections(text)
    b = build(sec)
    case_count = sum(1 for e in b.entries if e["entry_type"] == "case")
    param_count = sum(1 for e in b.entries if e["entry_type"] == "param_table")
    per_category: dict[str, int] = {}
    for e in b.entries:
        per_category[e["category"]] = per_category.get(e["category"], 0) + 1
    return {
        "meta": {
            "title": "双模通信检测用例库",
            "description": "国网计量中心双模通信互联互通检测条目（269 项体系）+ 检测线抄控器协议 + 河南流水线方案，"
                           "从蒸馏库 06_测试用例.md 半自动转换（generate.py），逐条保留来源小节与条款号。",
            "source": {"distill": DISTILL, "source_doc": SOURCE_DOC,
                       "knowledge_base_id": "7416304956882450"},
            "declared": {
                "total": 269,
                "breakdown": {"HPLC性能测试": 7, "无线通信性能测试": 11,
                              "HPLC协议一致性": "~120", "无线协议一致性": "~80", "互操作性测试": 11},
                "note": "蒸馏文档 §7.1：原文 286K 字符，协议一致性细节仅展开代表性条目；"
                        "本库枚举 md 全部可枚举检测条目 + 扩展协议消息 + 参数表，"
                        "detail_level=framework 表示仅条目名级（原 md 未展开步骤/判定）。",
            },
            "counts": {"entries": len(b.entries), "cases": case_count,
                       "param_rows": param_count, "by_category": per_category},
            "red_line": "国网口径；南网/ 目录未引用。",
        },
        "categories": CATEGORIES,
        "entries": b.entries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="蒸馏库 06_测试用例.md 路径")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    ap.add_argument("--check", action="store_true", help="只打印现有 JSON 的分类计数")
    args = ap.parse_args()

    if args.check:
        data = json.loads(args.out.read_text(encoding="utf-8"))
        print(json.dumps(data["meta"]["counts"], ensure_ascii=False, indent=2))
        return
    if not args.src.exists():
        raise SystemExit(f"蒸馏文档不存在：{args.src}")
    lib = build_library(args.src)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(lib, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"生成 {args.out}：entries={lib['meta']['counts']['entries']} "
          f"cases={lib['meta']['counts']['cases']} param_rows={lib['meta']['counts']['param_rows']}")
    for cat in CATEGORIES:
        n = lib["meta"]["counts"]["by_category"].get(cat["id"], 0)
        print(f"  {cat['id']:<22} {n}")


if __name__ == "__main__":
    main()
