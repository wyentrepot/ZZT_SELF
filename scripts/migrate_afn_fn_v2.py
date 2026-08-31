# -*- coding: utf-8 -*-
"""afn_fn.json v2 契约迁移（REQS-0013 P0-1）。

把 metadata/afn_fn.json 的每个 Fn 条目升级为 v2 结构：
  - fields       → 复制为 req.fields（旧 fields 保留为兼容别名，不删）
  - resp         → 上行响应建模（fields + list 分页契约），按 03 蒸馏文档注入
  - pageMode     → none / manual / auto / both
  - persist      → 上报类 true

注入范围：AFN=10H 全部、AFN=06H 全部、AFN=03H F3（分页列表型 Fn）。
数据来源：D:/3-obsidian-data/蒸馏/03_QGDW10376.2_全帧类型.md（§4.4/4.7/4.8）。
幂等：重复运行只刷新注入内容，不产生重复键。

用法：
    python scripts/migrate_afn_fn_v2.py            # 就地迁移
    python scripts/migrate_afn_fn_v2.py --dry-run  # 只打印统计
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "libs" / "parser_lib" / "adapters" / "adapter_10376" / "metadata" / "afn_fn.json"


def F(n, f, b, d, c="num", **extra):
    """字段速写。"""
    out = {"n": n, "f": f, "b": b, "d": d, "c": c}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# resp 建模表（key = "AFN-Fn"），依据 03_QGDW10376.2_全帧类型.md
# list.total/count   —— 上行头里的总数量 / 本帧条数字段名
# list.reqStart/reqCount —— 对应下行请求的起始序号 / 条数字段名
# list.record        —— 可重复记录模板（变长字段用 len_ref 指向同记录长度字段）
# ---------------------------------------------------------------------------
INJECT: dict[str, dict] = {
    # ============ 03H 查询数据 ============
    "03H-F1": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("厂商代码", "ASCII", 2, "厂商代码"),
                F("芯片代码", "ASCII", 2, "芯片代码"),
                F("版本日期", "BCD", 3, "日/月/年（BCD，低字节在前）"),
                F("版本", "BCD", 2, "版本号（BCD）"),
            ],
        },
    },
    "03H-F2": {
        "pageMode": "none",
        "resp": {"fields": [F("噪声强度", "BIN", 1, "D3~D0 噪声强度(0~15)，D7~D4 备用")]},
    },
    "03H-F4": {
        "pageMode": "none",
        "resp": {"fields": [F("主节点地址", "BCD", 6, "主节点 MAC 地址")]},
    },
    "03H-F5": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("状态字", "BS", 2, "D7~D6 周期抄表模式 / D5~D4 主节点信道特征 / D3~D0 速率数量n / D15~D12 备用 / D11~D8 信道数量"),
                F("通信速率", "BIN", 2, "D15 速率单位(0 bps/1 kbps) + D14~D0 通信速率"),
            ],
        },
    },
    "03H-F6": {
        "pageMode": "none",
        "resp": {"fields": [F("干扰状态", "BIN", 1, "0 无干扰 / 1 有干扰")]},
    },
    "03H-F7": {
        "pageMode": "none",
        "resp": {"fields": [F("最大超时时间", "BIN", 1, "从节点监控最大超时时间，单位 s")]},
    },
    "03H-F8": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("无线信道组", "BIN", 1, "无线信道组"),
                F("无线主节点发射功率", "BIN", 1, "00最高/01次高/02次低/03最低"),
            ],
        },
    },
    "03H-F9": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("广播通信延迟时间", "BIN", 2, "广播通信延迟时间"),
                F("通信协议类型", "BIN", 1, "00透明/01 645-1997/02 645-2007"),
                F("报文长度L", "BIN", 1, "报文内容字节数"),
                F("报文内容", "BIN", "len_ref:报文长度L", "需计算通信下行延时的报文内容"),
            ],
        },
    },
    "03H-F11": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("AFN功能码", "BIN", 1, "AFN 功能码（请求回显）"),
                F("数据单元支持位图", "BIN", 32, "256 位：D0=F1 … D254=F255，0不支持/1支持"),
            ],
        },
    },
    "03H-F10": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("本地通信模式字", "BS", 6, "通信方式/路由管理方式/从节点信息模式/周期抄表模式等"),
                F("从节点监控最大超时时间", "BIN", 1, "单位 s"),
                F("广播命令最大超时时间", "BIN", 2, "单位 s"),
                F("最大支持的报文长度", "BIN", 2, "单位 B"),
                F("文件传输最大单包长度", "BIN", 2, "单位 B"),
                F("升级操作等待时间", "BIN", 1, "单位 s"),
                F("主节点地址", "BIN", 6, "主节点地址（HEX）"),
                F("支持的最大从节点数量", "BIN", 2, "容量"),
                F("当前从节点数量", "BIN", 2, "实际"),
                F("协议发布日期", "BCD", 3, "YYMMDD"),
                F("协议最后备案日期", "BCD", 3, "YYMMDD"),
                F("厂商代码及版本信息", "BIN", 9, "厂商代码及版本"),
            ],
        },
    },
    "03H-F12": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("模块厂商代码", "ASCII", 2, "模块厂商代码"),
                F("模块ID号长度", "BIN", 1, "ID 号字节数 M"),
                F("模块ID号格式", "BIN", 1, "00组合/01BCD/02BIN/03ASCII"),
                F("模块ID号", "BIN", "len_ref:模块ID号长度", "模块 ID 号，M≤50"),
            ],
        },
    },
    "03H-F16": {
        "pageMode": "none",
        "resp": {"fields": [F("宽带载波频段", "BIN", 1, "0=1.953~11.96MHz/1=2.441~5.615MHz/2=0.781~2.930MHz/3=1.758~2.930MHz")]},
    },
    "03H-F100": {
        "pageMode": "none",
        "resp": {"fields": [F("场强门限", "BIN", 1, "取值 50~120，默认 96")]},
    },
    "03H-F3": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("侦听到的从节点总数量m", "BIN", 1, "侦听列表中从节点总数"),
                F("本帧传输的从节点数量n", "BIN", 1, "本帧携带的记录条数"),
            ],
            "list": {
                "total": "侦听到的从节点总数量m",
                "count": "本帧传输的从节点数量n",
                "reqStart": "开始节点指针",
                "reqCount": "读取节点的数量 N≤16",
                "pageMax": 16,
                "record": [
                    F("从节点地址", "BCD", 6, "从节点 MAC 地址"),
                    F("信号品质+中继级别", "BIN", 1, "D7~D4 侦听信号品质(0无/1最低~15)，D3~D0 中继级别(0无中继)"),
                    F("侦听次数", "BIN", 1, "该从节点被侦听到的次数"),
                ],
            },
        },
    },
    # ============ 10H 路由查询 ============
    "10H-F1": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("从节点总数量", "BIN", 2, "当前路由档案中的从节点总数"),
                F("路由支持最大从节点数量", "BIN", 2, "路由支持的最大从节点容量"),
            ],
        },
    },
    "10H-F2": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("从节点总数量", "BIN", 2, "路由档案从节点总数"),
                F("本次应答的从节点数量n", "BIN", 1, "本帧携带记录条数"),
            ],
            "list": {
                "total": "从节点总数量",
                "count": "本次应答的从节点数量n",
                "reqStart": "从节点起始序号",
                "reqCount": "从节点数量",
                "record": [
                    F("从节点地址", "BCD", 6, "从节点 MAC 地址"),
                    F("从节点信息", "BS", 2,
                      "低字节 D7~D4 侦听信号品质 / D3~D0 中继级别；高字节 D15~D12 备用+通信协议类型(3bit) / D11~D8 相位(3bit)"),
                ],
            },
        },
    },
    "10H-F3": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("提供路由的从节点总数量n", "BIN", 1, "为该从节点提供中继的节点数"),
            ],
            "list": {
                "total": "提供路由的从节点总数量n",
                "count": "提供路由的从节点总数量n",
                "record": [
                    F("从节点地址", "BCD", 6, "中继从节点 MAC 地址"),
                    F("从节点信息", "BIN", 2, "同 10H-F2 从节点信息格式"),
                ],
            },
        },
    },
    "10H-F4": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("运行状态字", "BIN", 1, "D7~D4 纠错编码 / D3 上报事件标志 / D2 工作标志 / D1 路由完成标志 / D0 —"),
                F("从节点总数量", "BIN", 2, "路由档案从节点总数"),
                F("已抄从节点数量", "BIN", 2, "已成功抄读的从节点数"),
                F("中继抄到从节点数量", "BIN", 2, "经中继抄读成功的从节点数"),
                F("工作开关", "BIN", 1, "D7~D6 当前状态(00抄表/01搜表/10升级/11其他) / D5~D4 台区识别状态 / D3 上报事件 / D2 注册允许 / D1 工作状态(1学习/0抄表) / D0 —"),
                F("通信速率", "BIN", 2, "当前通信速率"),
                F("第1相中继级别", "BIN", 1, "第 1 相中继级别"),
                F("第2相中继级别", "BIN", 1, "第 2 相中继级别"),
                F("第3相中继级别", "BIN", 1, "第 3 相中继级别"),
                F("第1相工作步骤", "BIN", 1, "1初始/2直抄/3中继/4监控/5广播/6广播召读/7读侦听/8空闲"),
                F("第2相工作步骤", "BIN", 1, "同上"),
                F("第3相工作步骤", "BIN", 1, "同上"),
            ],
        },
    },
    "10H-F5": {
        "pageMode": "both",
        "resp_ref": "10H-F2",  # 03 文档：上行格式同 F2
    },
    "10H-F6": {
        "pageMode": "both",
        "resp_ref": "10H-F2",  # 03 文档：上行格式同 F2
    },
    "10H-F7": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("从节点总数量", "BIN", 2, "路由档案从节点总数"),
                F("本次应答的从节点数量n", "BIN", 1, "本帧携带记录条数"),
            ],
            "list": {
                "total": "从节点总数量",
                "count": "本次应答的从节点数量n",
                "reqStart": "从节点起始序号",
                "reqCount": "从节点数量",
                "record": [
                    F("从节点地址", "BIN", 6, "从节点地址（HEX 序）"),
                    F("节点类型", "BS", 1, "Bit7 更新标识 / Bit6~4 保留 / Bit3~0 模块类型(0电表模块/1采集器模块/15未知)"),
                    F("模块厂商代码", "ASCII", 2, "模块厂商代码"),
                    F("模块ID号长度", "BIN", 1, "本记录 ID 号字节数 M（变长字段）"),
                    F("模块ID号格式", "BIN", 1, "00组合/01BCD/02BIN/03ASCII"),
                    F("模块ID号", "BIN", "len_ref:模块ID号长度", "模块 ID 号，长度由模块ID号长度决定"),
                ],
            },
        },
    },
    "10H-F9": {
        "pageMode": "none",
        "resp": {"fields": [F("网络规模", "BIN", 2, "HPLC 网络规模（节点数）")]},
    },
    "10H-F21": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("节点总数量", "BIN", 2, "网络节点总数"),
                F("节点起始序号", "BIN", 2, "本帧记录的起始序号（请求回显）"),
                F("本次应答的节点数量n", "BIN", 1, "本帧携带记录条数"),
            ],
            "list": {
                "total": "节点总数量",
                "count": "本次应答的节点数量n",
                "reqStart": "节点起始序号",
                "reqCount": "节点数量",
                "record": [
                    F("节点地址", "BIN", 6, "节点通信地址（HEX 序）"),
                    F("节点标识TEI", "BIN", 2, "节点标识（TEI）"),
                    F("代理节点标识", "BIN", 2, "代理节点标识（TEI）"),
                    F("节点信息", "BIN", 1, "D0~D3 节点层级 / D4~D7 节点角色(0x0无效/0x1末梢STA/0x2代理PCO/0x4主节点CCO)"),
                ],
            },
        },
    },
    "10H-F31": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("节点总数量", "BIN", 2, "网络节点总数"),
                F("节点起始序号", "BIN", 2, "本帧记录的起始序号（请求回显）"),
                F("本次应答的节点数量n", "BIN", 1, "本帧携带记录条数"),
            ],
            "list": {
                "total": "节点总数量",
                "count": "本次应答的节点数量n",
                "reqStart": "节点起始序号",
                "reqCount": "节点数量",
                "record": [
                    F("节点地址", "BIN", 6, "节点通信地址（HEX 序）"),
                    F("相线信息", "BIN", 2,
                      "D0~D2 相位(置1依次为第1/2/3相) / D4 电表类型(0单相/1三相) / D5 线路异常 / D7~D5 三相表相序类型(000 ABC 正常…110 零火反接)"),
                ],
            },
        },
    },
    "10H-F40": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("设备类型", "BIN", 1, "1抄控器/2CCO/3电表通信单元/4中继器/5II型采集器/6I型采集器/7三相表通信单元（请求回显）"),
                F("节点地址", "BIN", 6, "节点地址（请求回显）"),
                F("ID类型", "BIN", 1, "1芯片ID(长度24)/2模块ID(长度11)（请求回显）"),
                F("ID长度", "BIN", 1, "ID 信息字节数 M"),
                F("ID信息", "BIN", "len_ref:ID长度", "芯片 ID / 模块 ID 内容"),
            ],
        },
    },
    "10H-F100": {
        "pageMode": "none",
        "resp": {"fields": [F("网络规模", "BIN", 2, "无线微功率网络规模")]},
    },
    "10H-F101": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("从节点总数量", "BIN", 2, "无线从节点总数"),
                F("本次应答的从节点数量n", "BIN", 1, "本帧携带记录条数"),
            ],
            "list": {
                "total": "从节点总数量",
                "count": "本次应答的从节点数量n",
                "reqStart": "从节点起始序号",
                "reqCount": "从节点数量",
                "record": [
                    F("从节点地址", "BCD", 6, "从节点 MAC 地址"),
                    F("从节点信息", "BIN", 2, "同 10H-F2 从节点信息格式"),
                    F("软件版本信息", "BIN", 3, "从节点模块软件版本"),
                ],
            },
        },
    },
    # 10H-F104：03 蒸馏文档未覆盖，待补（不臆造）
    "10H-F111": {
        "pageMode": "none",
        "resp": {
            "fields": [
                F("多网络节点总数量n", "BIN", 1, "可感知的邻居网络数"),
                F("本节点网络标识号NID", "BIN", 3, "本节点 NID（有效 1~16777215）"),
                F("本节点主节点地址", "BIN", 6, "本网络主节点地址"),
            ],
            "list": {
                "total": "多网络节点总数量n",
                "count": "多网络节点总数量n",
                "record": [
                    F("邻居节点网络标识号NID", "BIN", 3, "邻居网络 NID"),
                ],
            },
        },
    },
    "10H-F112": {
        "pageMode": "both",
        "resp": {
            "fields": [
                F("节点总数量", "BIN", 2, "网络节点总数"),
                F("节点起始序号", "BIN", 2, "本帧记录的起始序号（请求回显）"),
                F("本次应答的节点数量n", "BIN", 1, "本帧携带记录条数"),
            ],
            "list": {
                "total": "节点总数量",
                "count": "本次应答的节点数量n",
                "reqStart": "节点起始序号",
                "reqCount": "节点数量",
                "record": [
                    F("节点地址", "BIN", 6, "节点通信地址（HEX 序）"),
                    F("设备类型", "BIN", 1, "0x01 窄带载波 / 0x02 宽带载波"),
                    F("芯片ID信息", "BIN", 24, "24B：0x01,0x02,0x9C,0x01C1FB,设备类别,厂商代码2B,芯片型号2B,序列号5B,校验码8B"),
                    F("芯片软件版本信息", "BCD", 2, "芯片软件版本"),
                ],
            },
        },
    },
    # ============ 06H 主动上报（全部持久入库） ============
    "06H-F1": {
        "pageMode": "none",
        "persist": True,
        "resp": {
            "fields": [F("上报从节点的数量n", "BIN", 1, "本帧上报的从节点条数")],
            "list": {
                "total": "上报从节点的数量n",
                "count": "上报从节点的数量n",
                "record": [
                    F("从节点地址", "BCD", 6, "上报从节点 MAC 地址"),
                    F("通信协议类型", "BIN", 1, "00透明/01 DL/T 645—1997/02 DL/T 645—2007/03 DL/T 698.45"),
                    F("从节点序号", "BIN", 2, "从节点在路由表中的序号"),
                ],
            },
        },
    },
    "06H-F2": {
        "pageMode": "none",
        "persist": True,
        "resp": {
            "fields": [
                F("从节点序号", "BIN", 2, "从节点在路由表中的序号"),
                F("通信协议类型", "BIN", 1, "00透明/01 645-1997/02 645-2007/03 698.45"),
                F("当前报文本地通信上行时长", "BIN", 2, "从节点→主节点通信延迟，单位 s"),
                F("报文长度L", "BIN", 1, "报文内容字节数"),
                F("报文内容", "BIN", "len_ref:报文长度L", "原始抄读报文（可内嵌 645/698 帧）"),
            ],
        },
    },
    "06H-F3": {
        "pageMode": "none",
        "persist": True,
        "resp": {
            "fields": [
                F("路由工作任务变动类型", "BIN", 1, "1 抄表任务结束 / 2 搜表任务结束 / 3 台区识别任务结束"),
            ],
        },
    },
    "06H-F4": {
        "pageMode": "none",
        "persist": True,
        "resp": {
            "fields": [F("上报从节点的数量n", "BIN", 1, "本帧上报的从节点条数")],
            "list": {
                "total": "上报从节点的数量n",
                "count": "上报从节点的数量n",
                "record": [
                    F("从节点通信地址", "BCD", 6, "从节点 MAC 地址"),
                    F("从节点通信协议类型", "BIN", 1, "00透明/01 645-1997/02 645-2007/03 698.45"),
                    F("从节点序号", "BIN", 2, "路由表序号"),
                    F("设备类型", "BIN", 1, "00采集器/01电能表/02~FF 保留"),
                    F("下接从节点数量M", "BIN", 1, "该从节点下接的从节点数"),
                    F("下接从节点列表", "list_ref:下接从节点数量M", 0,
                      "嵌套记录：下接地址 BCD6 + 下接协议类型 BIN1，共 M 组"),
                ],
            },
        },
    },
    "06H-F5": {
        "pageMode": "none",
        "persist": True,
        "resp": {
            "fields": [
                F("从节点设备类型", "BIN", 1, "00采集器/01电能表/02 HPLC/03窄带/04微功率/05微功率+HPLC/06微功率+窄带"),
                F("通信协议类型", "BIN", 1, "00保留/01 645-1997/02 645-2007/03 698.45/04停复电事件/05台区改切拒绝节点"),
                F("报文长度L", "BIN", 1, "报文内容字节数"),
                F("报文内容", "BIN", "len_ref:报文长度L", "事件报文：04H=事件类型1B+地址序列6N；05H=个数1B+地址6+设备类型1 ×n"),
            ],
        },
    },
}


def _resolve_ref(inject: dict, key: str, seen: set | None = None) -> dict:
    """展开 resp_ref 引用（如 10H-F5/F6 复用 F2 的 resp）。"""
    seen = seen or set()
    item = inject[key]
    if "resp_ref" not in item:
        return item
    ref = item["resp_ref"]
    if ref in seen:
        raise ValueError(f"resp_ref 循环引用: {key}")
    seen.add(key)
    source = _resolve_ref(inject, ref, seen)
    out = {k: v for k, v in item.items() if k != "resp_ref"}
    out["resp"] = copy.deepcopy(source["resp"])
    return out


# ---------------------------------------------------------------------------
# 下行请求字段 → 业务键名映射（供 UI 表单化渲染，键名对齐 adapter_10376 构帧模板）
# None = 长度/数量等自动计算字段，前端跳过不显示输入框。
# 特殊键：_time=日期时间（BCD 6B）；nodes/meters/relays/subs=地址列表；
#         payload/data/content=hex 报文内容。
# ---------------------------------------------------------------------------
FIELD_KEYS: dict[str, list] = {
    "02H-F1": ["protocol", None, "payload"],
    "03H-F3": ["start", "count"],
    "03H-F6": ["duration"],
    "03H-F9": ["protocol", None, "payload"],
    "03H-F11": ["afn"],
    "04H-F1": ["duration"],
    "04H-F3": ["rate", "addr", "protocol", None, "payload"],
    "05H-F1": ["addr"],
    "05H-F2": ["enable"],
    "05H-F3": ["ctrl", None, "payload"],
    "05H-F4": ["timeout"],
    "05H-F5": ["channel", "power"],
    "05H-F6": ["enable"],
    "05H-F16": ["band"],
    "05H-F100": ["threshold"],
    "05H-F101": ["_time"],
    "05H-F200": ["enable"],
    "10H-F2": ["start", "count"],
    "10H-F3": ["addr"],
    "10H-F5": ["start", "count"],
    "10H-F6": ["start", "count"],
    "10H-F7": ["start", "count"],
    "11H-F1": ["nodes", None, None],
    "11H-F2": ["meters", None],
    "11H-F3": ["addr", None, "relays"],
    "11H-F4": ["mode", "rate"],
    "11H-F5": ["_time", "duration", "retry", "slices"],
    "11H-F100": ["scale"],
    "13H-F1": ["protocol", "delay_flag", None, "subs", None, "payload"],
    "14H-F1": ["flag", "delay_flag", None, "payload", None, "subs"],
    "14H-F2": ["_time"],
    "14H-F3": [None, "payload"],
    "14H-F4": ["type", "item", "content"],
    "15H-F1": ["file_id", "attr", "cmd", "total_segs", "seg_id", None, "data"],
}


def _apply_field_keys(afn_list: list) -> int:
    """给下行请求字段补 key（业务键名），供 UI 表单化。幂等：重复运行覆盖。"""
    applied = 0
    for afn in afn_list:
        code = afn["code"]
        for fn in afn["fns"]:
            key = f"{code}-{fn['no']}"
            keys = FIELD_KEYS.get(key)
            if not keys:
                continue
            fields = fn.get("req", {}).get("fields") or []
            if not fields:
                continue
            for i, fl in enumerate(fields):
                if i < len(keys):
                    k = keys[i]
                    if k is None:
                        fl["key"] = None  # 自动计算字段
                    else:
                        fl["key"] = k
            applied += 1
    return applied


def migrate(dry_run: bool = False) -> int:
    data = json.loads(io.open(META, encoding="utf-8").read())
    meta_block = {k: v for k, v in data.items() if k != "afn"}
    afn_list = data["afn"]

    stats = {"req_migrated": 0, "resp_injected": 0, "skipped": []}
    for afn in afn_list:
        code = afn["code"]
        for fn in afn["fns"]:
            key = f"{code}-{fn['no']}"
            # 1) fields → req.fields（保留 fields 兼容别名）
            if "req" not in fn and "fields" in fn:
                fn["req"] = {"fields": fn["fields"]}
                stats["req_migrated"] += 1
            # 2) 注入 resp / pageMode / persist
            inj = INJECT.get(key)
            if not inj:
                if "resp" not in fn:
                    fn.setdefault("pageMode", "none")
                continue
            inj = _resolve_ref(INJECT, key)
            if "resp" in inj:
                fn["resp"] = inj["resp"]
                stats["resp_injected"] += 1
            if "pageMode" in inj:
                fn["pageMode"] = inj["pageMode"]
            if inj.get("persist"):
                fn["persist"] = True

    if not dry_run:
        out = dict(meta_block)
        out["afn"] = afn_list
        key_applied = _apply_field_keys(afn_list)
        out["v2"] = {
            "contract": "req/resp/list/pageMode/persist（REQS-0013）",
            "note": "req=下行请求字段；resp=上行响应建模；list=分页契约(total/count/reqStart/reqCount/record/pageMax)；"
                    "pageMode: none|manual|auto|both；persist=true 上报入库。fields 保留为 req.fields 的兼容别名。"
                    "变长字段 b='len_ref:<字段名>'；嵌套记录 f='list_ref:<数量字段名>'。"
                    "req.fields[i].key=业务键名（对齐 adapter_10376 构帧模板；None=自动计算字段）。依据 03_QGDW10376.2_全帧类型.md。",
        }
        io.open(META, "w", encoding="utf-8").write(
            json.dumps(out, ensure_ascii=False, indent=1) + "\n")

    print(f"[migrate] req 迁移 {stats['req_migrated']} 个 Fn；resp 注入 {stats['resp_injected']} 个 Fn"
          f"；key 映射 {len(FIELD_KEYS)} 项（dry_run={dry_run}）")
    injected = sorted(k for k in INJECT)
    print(f"[migrate] 注入清单（{len(injected)} 项）: {', '.join(injected)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    raise SystemExit(migrate(parser.parse_args().dry_run))
