# REQS-0035 — DONE（完成日志，只追加，最新在上）

## v1.0 全量交付 ｜ 2026-09-28

- **新增模块 `libs/sim_meter/`**（独立目录，不侵入 sim_concentrator / listener）：
  - `meter.py`：MeterBrain 645-2007/1997 从站应答引擎——应答地址（13H 广播读 /
    11H+DI04000401 固件口径 / 15H、97：01H+C032 / 06H）、查询时间（04000101 日期及周次 /
    04000102 时间实时生成、08H·97:05H 广播校时、14H 写时间）、抄表（00010000 总电能 +
    费率1~4、电压/电流/瞬时功率、重庆分钟采集别名 00000001/04000100）、
    波特率（17H 国标 + 09H 知识库兼容别名，97:07H）、即装即采状态与上送报文
    （11612.43 事件类型51 数据子域 / 1376.2 AFN=06H F7 两口径）；
    `handle_frame` 内置异常兜底（畸形帧/非法 BCD 不向通道层抛异常）。
  - `serial_channel.py`：MeterSerialChannel 从站通道——adapter_645.try_extract 切帧、
    读线程跨波特率重开存活、先回帧再热切换、serial_for_url（loop:// 可测）。
  - `journal.py`：会话帧日志（rx/tx/event，内存镜像 + data/logs/simeter/*.jsonl）。
  - `api.py`：create_simeter_app（/status /ports /open /close /baud /config /data /clock
    /jzjc /jzjc/report /handle_hex /send_hex /frames）。
  - `cli.py`/`__main__.py`：serve（串口常驻+可选同进程 REST）/ api / ports / demo。
- **协议口径核查记录**（知识库 + 国标比对）：
  - 控制码：知识库 01 卡把 09H 记为更改通信速率、17H 记为电表清零，与 DL/T 645-2007
    国标（17H=更改速率、1AH=电表清零）存在偏差 → 实现以国标 17H 为主、09H 兼容别名；
  - DI：04000101=日期及周次 YYMMDDWW、04000102=时间 hhmmss（湖南时钟卡/吉林卡/厂商手册一致）；
    读表地址 DI 04000401H（07）与 C032H/9010H（97）取自 CCO 底稿 02 + 03 卡固件常量
    （线上 34 37 33 37 / 65 F3 / 43 C3）；
  - 即装即采：设备类型 4036 取值与描述符前缀映射、STA 上报子域（事件类型 51）、
    AFN=06H F7 结构，全部取自知识库 协议层/100、102 卡。
- **测试**：53 passed（test_meter_brain 31 / test_jzjc 11 / test_serial_api 11，
  含 loop:// 回环收发、波特率热切换、REST TestClient 全接口）。
- **验证**：`python -m sim_meter demo` 全链路演示帧序列正确；回归确认
  adapter_645 与 sim_concentrator 无新增失败（存量 1 例
  `test_builtin_14F2_clock_reply` 正则问题与本需求无关）。
- **文档**：libs/sim_meter/README.md（能力表 + API 表 + 报文结构出处）。

## 遗留 / 后续可选项

- workbench AI 编排接入（ai_capability_service 增加 simeter 能力项）——未实施；
- 645 密码/权限校验、1AH 电表清零、12H 分帧续读——按需再加；
- 挂载侦听台 create_app（预留 create_simeter_app(prefix="") 挂载方式，未默认挂载）。

## 实机联调第一轮 ｜ 2026-09-28 ｜ 见 docs/19

- **总结文档**：`docs/19-模拟电表实机联调总结-20260928.md`（拓扑、证据、根因、下一步）。
- 已打通：CCO↔simcon 本地总线（bpsCheck 14-F2 应答 + 10H-F2 查档案）；STA 重烧最新固件
  （XMODEM 经日志口，BURN SUCCESS）；绑表扫掠 645 读地址帧实测到达底座口（99×6/AA×6 广播，
  sim_meter 已扩展支持，帧样例见总结文档 §3.3）。
- 阻塞根因（已定位）：①STA flash 参数 hplc_pwr=8>4 触发功耗管控、载波功放关闭 40 分钟，
  入网关联发不出；②底座桥固定波特率/校验与扫掠组合错配（COM24 已改 115200 8E1 待复验）；
  ③socat 桥 pts 软链漂移需动态重建。
- 待办：sim_meter 挂载进工作台；入网→绑表→并发抄表→透传实机闭环（通过标准：抄表数据上报集中器）。
- 流程纠正：串口统一由工作台接管（module-serial 会话 / simeter 挂载），不再裸开串口、
  不再改动 Windows 桥接脚本（本次两处修改已在总结文档 §5.1 如实登记，待确认保留或恢复）。
