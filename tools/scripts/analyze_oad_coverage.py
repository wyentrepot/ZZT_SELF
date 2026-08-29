#!/usr/bin/env python3
"""提取698.45协议附录A的完整OI清单，与项目oad.json对比，输出缺失项。

用法: python tools/scripts/analyze_oad_coverage.py
"""
import json
import re
import os
from pathlib import Path

# 路径
PROJECT = Path(r"D:\2-侦听台改造")
IMA_DIR = Path(r"D:\3-obsidian-data\ima\645_698协议\md版本")
OAD_JSON = PROJECT / "libs" / "parser_lib" / "adapters" / "adapter_698" / "metadata" / "oad.json"
DI_JSON = PROJECT / "libs" / "parser_lib" / "adapters" / "adapter_645" / "metadata" / "di.json"
PROTOCOL_MD = IMA_DIR / "DLT698.45电能信息采集与管理系统-面向对象的数据交换协议（20170412）.md"
IMI_MD = IMA_DIR / "DLT 698.42-2010 电能信息采集与管理系统 第4-2部分 通信协议－集中器下行通信.md"

# ========== 1. 提取698.45附录A的OI清单 ==========

def extract_oi_from_md(md_path):
    """从698.45协议md中提取附录A的OI清单"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 找到附录A的表格部分（从"表A.1"到文档末尾）
    lines = content.split('\n')
    
    oi_entries = {}  # OI -> {name, ic, class_name, line}
    
    # 当前正在解析的表格类别
    current_class = ""
    in_appendix = False
    
    # 从"表A.1"标记开始
    start_line = 0
    for i, line in enumerate(lines):
        if '表A.1' in line and '电能量' in line:
            start_line = i
            break
    
    # 表A.1到A.14的表格行格式: | OI | IC | 对象名称 | ...
    # 示例: | 0000 | 1 | 组合有功电能 | ...
    oi_pattern = re.compile(r'^\|\s*([0-9A-Fa-f]{4})\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|')
    # 纯文本行备用（md 部分表格被转成无管道符文本，如 "5004 9 日冻结"）
    # 名称捕获到"下一个 OI+数字"或行尾为止（不按 字母+数字 截断，避免 RS232→R）
    oi_pattern_plain = re.compile(r'^\s*([0-9A-Fa-f]{4})\s+(\d+)\s+([^\s|][^|]{0,80}?)(?=\s+(?:[0-9A-Fa-f]{4})\s+\d+\s|\s*$)', re.IGNORECASE)

    # —— 权威 OI 编号段 → 标准类别 映射 ——
    # 依据：协议附录A各表真实OI段（实测统计，见 OAD覆盖分析报告）。
    # md 源文件存在排版错乱（表边界不可靠，如参变量残留行混入冻结表、真实冻结
    # 续表被标成采集监控），故类别以 OI 段判定，不依赖 md 表格标题切换。
    def _class_of_oi(oi):
        v = int(oi, 16)
        if 0x0000 <= v <= 0x00FF: return '电能量类'          # 表A.1
        if 0x0300 <= v <= 0x04FF: return '最大需量类'        # 表A.2
        if 0x1000 <= v <= 0x2FFF: return '变量类'            # 表A.3（含 1173~2xxx）
        if 0x3000 <= v <= 0x33FF and oi not in ('3320',): return '事件类'  # 表A.4（3320 为参变量）
        if oi == '3320': return '参变量类'
        if 0x4000 <= v <= 0x45FF: return '参变量类'          # 表A.5（含 4000~45xx）
        if 0x5000 <= v <= 0x5011: return '冻结类'            # 表A.6（真实冻结 5000~5011）
        if 0x6040 <= v <= 0x70FF and oi not in ('7000','7001','7010','7011','7012','7013','7014'): return '采集监控类'  # 表A.7
        if oi in ('7000','7001','7010','7011','7012','7013','7014'): return '集合类'
        if 0x8000 <= v <= 0x811F and oi not in ('810D','810E','810F','8110'): return '控制类'  # 表A.9
        if oi in ('810D','810E','810F','8110'): return '文件传输类'  # 表A.11
        if 0xF000 <= v <= 0xF002: return '文件传输类'        # 表A.11
        if oi in ('F100','F101'): return 'ESAM接口类'        # 表A.12
        if 0xF200 <= v <= 0xF20F and oi not in ('F20C',): return '输入输出设备类'  # 表A.13
        if oi in ('F20C','F210','F300','F301','3467'): return '显示类'  # 表A.14
        if oi == '3467': return '显示类'
        return current_class  # 兜底：无法判定时沿用md表标题
    
    for i, line in enumerate(lines[start_line:], start=start_line):
        # 识别当前表格类别（仅用于兜底；主判定走 _class_of_oi）
        if '表A.1' in line:
            current_class = '电能量类'
        elif '表A.2' in line:
            current_class = '最大需量类'
        elif '表A.3' in line:
            current_class = '变量类'
        elif '表A.4' in line:
            current_class = '事件类'
        elif '表A.5' in line:
            current_class = '参变量类'
        elif '表A.6' in line:
            if '冻结' in line:
                current_class = '冻结类'
        elif '表A.7' in line:
            current_class = '采集监控类'
        elif '表A.8' in line:
            current_class = '集合类'
        elif '表A.9' in line:
            current_class = '控制类'
        elif '表A.11' in line:
            current_class = '文件传输类'
        elif '表A.12' in line:
            current_class = 'ESAM接口类'
        elif '表A.13' in line:
            current_class = '输入输出设备类'
        elif '表A.14' in line:
            current_class = '显示类'
        
        # 匹配表格行（优先管道表格，其次纯文本行）
        m = oi_pattern.match(line.strip())
        if not m:
            m = oi_pattern_plain.match(line.strip())
        if m:
            oi = m.group(1).upper()
            ic = m.group(2)
            name = m.group(3).strip()
            # 名称清洗：截断混入的表格描述/方法定义/后续行内容
            for sep in ('属性', '方法', '∷=', '（属性', '(属性'):
                idx = name.find(sep)
                if idx > 0:
                    name = name[:idx]
                    break
            # 清理 md 残留标记（表/节标题、方法/属性编号），保留正常英文名（RS232/ESAM/DL/T 等）
            name = re.sub(r'\s*[A-Za-z]\.\d+.*$', '', name)      # 尾部 "A.7 采集监控类对象"
            name = re.sub(r'\s*表A\.[0-9]+.*$', '', name)        # 尾部 "表A.7..."
            name = re.sub(r'\s+方法\d+.*$', '', name)            # 尾部 "方法127：出厂启用..."
            name = re.sub(r'\s*F3\d\d.*$', '', name)             # 尾部 "F301 17 按键轮显"
            name = name.strip('| ')  # 去掉可能残留的管道符
            if oi not in oi_entries:
                oi_entries[oi] = {
                    'oi': oi,
                    'name': name,
                    'ic': ic,
                    'class': _class_of_oi(oi)
                }
    
    return oi_entries


# ========== 2. 提取当前项目支持的OAD/OI ==========

def extract_oads_from_json(json_path):
    """从oad.json提取当前支持的OAD和OI"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    oad_entries = {}
    oi_set = set()
    
    for oad_key, info in data.items():
        oad_entries[oad_key] = info
        oi = oad_key[:4].upper()  # OAD = OI(2B) + 属性(1B) + 索引(1B)
        oi_set.add(oi)
    
    return oad_entries, oi_set


def extract_dis_from_json(json_path):
    """从di.json提取当前支持的DI"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


# ========== 3. 对比分析 ==========

def analyze_698_coverage(oi_entries, supported_oi_set, oad_entries):
    """对比698.45标准OI与项目支持的OI"""
    standard_ois = set(oi_entries.keys())
    missing_ois = standard_ois - supported_oi_set
    
    # 按类别分组缺失的OI
    missing_by_class = {}
    for oi in sorted(missing_ois):
        entry = oi_entries[oi]
        cls = entry['class']
        if cls not in missing_by_class:
            missing_by_class[cls] = []
        missing_by_class[cls].append(entry)
    
    # 已支持的OI
    supported_by_class = {}
    for oi in sorted(supported_oi_set & standard_ois):
        entry = oi_entries[oi]
        cls = entry['class']
        if cls not in supported_by_class:
            supported_by_class[cls] = []
        supported_by_class[cls].append(entry)
    
    return {
        'total_standard': len(standard_ois),
        'total_supported': len(supported_oi_set & standard_ois),
        'total_missing': len(missing_ois),
        'missing_by_class': missing_by_class,
        'supported_by_class': supported_by_class,
        'oad_entries': oad_entries,
        'oi_entries': oi_entries,
    }


def main():
    print("=" * 60)
    print("698.45协议OAD覆盖分析报告")
    print("=" * 60)
    
    # 1. 提取标准OI
    print("\n[1/4] 提取698.45协议附录A的OI清单...")
    oi_entries = extract_oi_from_md(PROTOCOL_MD)
    print(f"  共提取 {len(oi_entries)} 个标准OI")
    
    # 2. 提取项目OAD
    print("\n[2/4] 提取项目oad.json...")
    oad_entries, supported_oi_set = extract_oads_from_json(OAD_JSON)
    print(f"  共 {len(oad_entries)} 条OAD，涉及 {len(supported_oi_set)} 个OI")
    
    # 3. 对比分析
    print("\n[3/4] 对比分析...")
    result = analyze_698_coverage(oi_entries, supported_oi_set, oad_entries)
    
    print(f"\n  标准OI总数: {result['total_standard']}")
    print(f"  已支持OI数: {result['total_supported']}")
    print(f"  缺失OI数:   {result['total_missing']}")
    print(f"  覆盖率:     {result['total_supported']/result['total_standard']*100:.1f}%")
    
    # 4. 按类别输出缺失
    print("\n" + "=" * 60)
    print("按类别缺失的OI")
    print("=" * 60)
    
    for cls in sorted(result['missing_by_class'].keys()):
        entries = result['missing_by_class'][cls]
        supported = result['supported_by_class'].get(cls, [])
        print(f"\n【{cls}】 标准={len(entries)+len(supported)} 已支持={len(supported)} 缺失={len(entries)}")
        for e in entries[:10]:  # 最多显示10条
            print(f"    OI={e['oi']}  {e['name']}")
        if len(entries) > 10:
            print(f"    ... 还有 {len(entries)-10} 条")
    
    # 5. 645协议概况
    print("\n" + "=" * 60)
    print("645协议DI覆盖概况")
    print("=" * 60)
    di_entries = extract_dis_from_json(DI_JSON)
    print(f"  当前di.json共 {len(di_entries)} 条DI")
    
    # 类别统计
    di_categories = {}
    for di_key, info in di_entries.items():
        cat = di_key[:2]
        di_categories.setdefault(cat, 0)
        di_categories[cat] += 1
    print(f"  DI类别分布: {len(di_categories)} 个类别")
    for cat, cnt in sorted(di_categories.items()):
        print(f"    DI{cat}xx: {cnt} 条")
    
    # 6. 保存报告
    print("\n[4/4] 输出缺失清单...")
    report_lines = []
    report_lines.append("# 698.45协议OAD/OI覆盖缺失清单\n")
    report_lines.append(f"> 分析时间: 基于IMA知识库「645/698协议」\n")
    report_lines.append(f"> 698.45标准附录A OI总数: {result['total_standard']}\n")
    report_lines.append(f"> 当前oad.json OI数: {result['total_supported']}\n")
    report_lines.append(f"> 覆盖率: {result['total_supported']/result['total_standard']*100:.1f}%\n")
    report_lines.append("\n---\n")
    
    # 已支持的OI
    report_lines.append("\n## ✅ 已支持的OI\n")
    report_lines.append("| OI | 名称 | 类别 |\n")
    report_lines.append("| --- | --- | --- |\n")
    for oi in sorted(supported_oi_set & set(oi_entries.keys())):
        e = oi_entries[oi]
        report_lines.append(f"| {oi} | {e['name']} | {e['class']} |\n")
    
    # 缺失的OI
    report_lines.append("\n---\n")
    report_lines.append("## ❌ 缺失的OI（按类别）\n")
    
    for cls in sorted(result['missing_by_class'].keys()):
        entries = result['missing_by_class'][cls]
        supported = result['supported_by_class'].get(cls, [])
        report_lines.append(f"\n### {cls}（缺失 {len(entries)} 个，已支持 {len(supported)} 个）\n")
        report_lines.append("| OI | 名称 | 接口类IC |\n")
        report_lines.append("| --- | --- | --- |\n")
        for e in entries:
            report_lines.append(f"| {e['oi']} | {e['name']} | {e['ic']} |\n")
    
    # 645协议说明
    report_lines.append("\n---\n")
    report_lines.append("## 645协议DI覆盖\n")
    report_lines.append(f"当前di.json共 {len(di_entries)} 条DI\n")
    report_lines.append("> 645协议DI覆盖范围已在《电能量/最大需量/事件记录》等主要类别有充分覆盖，")
    report_lines.append("后续可根据实际报文补充更多DI条目。\n")
    
    # 待办事项
    report_lines.append("\n---\n")
    report_lines.append("## 待办事项\n")
    report_lines.append("1. **高优先级**：补充分钟采集业务场景中可能用到的OAD（如冻结类、费率电能类）\n")
    report_lines.append("2. **中优先级**：补全变量类（电压/电流/功率/频率等所有分相OAD）\n")
    report_lines.append("3. **中优先级**：补全事件类（失压/欠压/过压/断相/失流/过流等事件记录）\n")
    report_lines.append("4. **低优先级**：补全参变量类（通信参数、波特率、协议版本等）\n")
    report_lines.append("5. **低优先级**：补充控制类、文件传输类、ESAM接口类等\n")
    report_lines.append("6. **后续**：645协议DI可根据实际抄表业务中遇到的未知DI逐步补充\n")
    
    report_path = PROJECT / "docs" / "协议" / "698.45-OAD覆盖" / "OAD覆盖分析报告.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.writelines(report_lines)
    print(f"  报告已写入: {report_path}")
    
    print("\n分析完成！")


if __name__ == '__main__':
    main()