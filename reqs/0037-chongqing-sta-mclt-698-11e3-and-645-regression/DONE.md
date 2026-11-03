# REQS-0037 — DONE（只追加，最新在上）

## 2026-11-03 task 0/1/15 内层 698 补测 PASS（REQS-0037 收口）

- task 0/1/15 内层 698 全链路断言 **ALL PASS**（冻结时刻 2026-11-03 01:00:00，各任务 own 冻结点）：
  外层 task_id 0/1/15 回显、protocol=3、result=0、di_cnt=3、bufflen=54；内层 C=0xC3、
  APDU 85 02 01 03、OAD1 20000201=1101、OAD2 20010201=1102、OAD3 20000200=FFFF（缺项满配置长度 2B）、
  APDU 长度 0x22（含 9B 尾）、HCS/FCS OK、TimeTag=2026-11-03 01:00:00；06H-F230 任务号/协议/时间/datalen 一致。
- 机制澄清：**11E3 由 CCO 自主发起**（冻结+9min，逐任务），raw APS 帧写 CCO 本地口（COM4）不可达
  （实测两次无 rxlen:24）；昨日"恢复摘要"的 down/up 即 CCO 自主读日志帧。
- 698 采集依赖电表 698 应答：simeter 脑 698 仅支持 jzjc OAD，采集 OAD 需注入应答（inject_698_reply.py，
  昨日 manual_send 同款帧）；修复 simeter 物理波特率（桥静态 9600E → 实际 2400E，应用层动态切换）后 01:00 周期 5 任务全存数。
- 645 回归 PASS：task2 三 DI 正常；task3 第三 DI 02030100 被模拟表否认 → 内层 `32 32 32`（0x32 满长度）。
- P3：删除任务（task3 语义删 + task0 raw 删迁移快照 + 0xFF 广播删，mclt_list n=0）；STA console `reboot` 软重启
  （重连 ~20s 内 CCO 自动重发配置；STA 任务持久化逐 slot，仅 task1 恢复，其余靠 CCO 重配重建；冻结数据索引恢复无报错）；
  早于存数点读 → result=2 空响应（di_cnt=0/bufflen=0，无内层帧/TimeTag）。
- W1–W5 真机验证：AI v2 API / simcon 配任务 / F230 入库应答观察 / wait+read 场景（含边界发现）/ parser 渲染 全 PASS。
- 台架已清理：mclt_list n=0、STA 任务 clear success、simcon/simeter/listener 关闭、模块会话停止、六口 in_use=false。
- 证据：`/d/sta/01-sta/20261103-003456/`（BENCH-task015-RETEST.md + 断言输出 + 帧证据 + raw-logs）、
  `/d/cco/001-cco/20261103-003456/raw-logs/`。未烧录未重编译（两板固件与昨日一致）。

## 2026-11-02 645 分钟采集回归 PASS

- task 1：协议 2、5 min、DI `02010100`(2B)/`02020100`(3B)/`0203FF00`(3B)；`CFGOK`。
- STA `recv mclt 645 data seq 0/1/2 dlen 2/3/3`，`Freeze data for task 1 stored successfully`。
- 11E3 外层 `task_id=1/protocol=2/fz=18:05:00/di_cnt=3/bufflen=56`；内层 3 个 645 帧，一 DI 一帧，无 698 APDU。
- 缺项 task 2：第三 DI `02030100` 被表端否认，数据 `32 32 32`（3B），645 缺项仍 `0x32` 满长度。
- 06H-F230 task 1/2 协议 2、任务号/时间/56B 内容与 11E3 一致。
- 证据：`/home/H_STA/01/sta/reqs/0005.../evidence/bench-645-regression-20261102-180650/`；原始日志 `/d/sta/01-sta/20261102-180650/`、`/d/cco/001-cco/20261102-180650/`。
- 台架已清理：task 1/2 删除，`mclt_list n=0`；simcon/simeter close、listener stop。

## 2026-11-02 698 11E3 修复 + 重庆 CCO 台架

- STA v5：读取层 698 缺项按配置长度写 FF；构帧层固定长度拷贝；C=`0xC3`、APDU `85 02 01 <di_cnt>`、TimeTag=`read_start_sec`、APDU 长度含 9 字节尾。
- 真机 Unity：9/9 mclt PASS；48 Tests / 1 既有 jzjc 失败。
- CCO 重庆固件：`fix_13762_develop` → `CHONG_QING_MODE / sv002741 / isv 060026`；COM8 直连烧录。
- 11E3 task 1 全链路 PASS：外层 task_id=1；内层 698 `C=C3`、`85 02 01 03`、缺项 `20000200 01 FF FF`、APDU 长度 `0x22`、总长 54、TimeTag=17:10:00；06H-F230 任务号 1/54B 一致。
- task 0/1/15 外层 task_id 0/1/15 正确；task 0 内部映射 `TASK_O_ID=16`。
- task 0/15 内层 698 待补测（17:10 无数据；task 15 到 17:20:05 才存数）。
- 源码提交：`c43f682a`；`6650c21d` 为版本号/日期 CI。
- 注意：STA 工程原 `reqs/` 被后续清理删除，已在 `/home/H_STA/01/sta/reqs/0005...` 重建并留恢复摘要。
