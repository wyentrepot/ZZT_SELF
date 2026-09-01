# REQS-0020 DONE — 实机运行测试完成日志

> 最新在上。

## 2026-09-02 23:26-23:38 — 实机运行测试（第 1 轮）

### 执行结果（15 分钟硬停止内完成）

| 步骤 | 结果 | 证据 |
|---|---|---|
| 服务启动 | ✅ workbench 8790 / 侦听台 8765 / 模块日志 8766 后台常驻 | 三端口 LISTEN；解析后端 `remote` |
| 侦听台 COM24 | ✅ `/dev/ttyUSB1` 9600/E/8/1 采集运行 | listener/ensure；帧索引持续增长 |
| CCO/STA 会话 | ✅ cco-main(COM9=/dev/ttyACM1)、sta-main(COM8=/dev/ttyACM0) | module-sessions/ensure running |
| 档案读取 | ✅ 集中器下发 `10H-F2`（start=0,count=32） | simcon step succeeded；run manual-20260901-233425 |
| 645 任务5 配置 | ✅ `11H-F231`（task_no=5, 645, 周期10min, A相电压 02010100） | simcon step succeeded；run manual-20260901-233637 |
| 645 任务5 关联档案 1-32 | ✅ `11H-F232`（task_no=5, meters=013300000001..032） | simcon step succeeded；run manual-20260901-233644 |
| CCO 主动上报 | ✅ 持续 06H-F230 采集数据上报（集中器侧可见） | simcon session rx=48+, uplink=48+ |
| 服务保持监听 | ✅ 后台进程存活、持续采集 | 见下方运行证据 |

### 实机观察

- 集中器（simcon，COM4）↔ CCO 链路真实闭合：CCO 持续向集中器上报 `06H-F230`
  分钟采集数据与 `06H-F3` 空闲上报；集中器侧 `rx/uplink` 持续增长。
- 侦听台（COM24）同步捕获 485 总线帧（4+ 帧入库）；模块日志 cco/sta 均在写盘。

### 困难点 / 优化点（本次实测目标）

1. **simcon 自动选串口不可靠**：`simcon/step` 不传 `port` 时自动选到
   `/dev/ttyS0` 等虚拟口 → `Could not configure port: (5, 'Input/output error')`。
   必须显式传 `port=/dev/ttyUSB0, 9600/E/8/1`。优化点：自动选择应排除无实体的
   `ttyS*`，或复用当前已开会话端口而非重新选择。
2. **AI `listener/ensure` 忽略映射默认参数**：不传参数时用了 115200/N/8/1，
   与 `serial_ports.json` 的 9600/E/8/1 不符。优化点：ensure 应从 mapping 回填
   默认串口参数（与 module-session 一致）。
3. **WSL 下 `config/serial_ports.json` 缺 `linux_device`**：AI 控制面无法把
   `/dev/ttyUSB1`→listener、`/dev/ttyACM1`→cco 等解析到映射，ensure 报
   「未提供可用端口或映射 ID」。本次已补全 4 个映射的 `linux_device` 并新增
   `sta-main`。优化点：部署脚本映射后自动回填/校验 `linux_device`。
4. **`11H-F231/F232` 下发后未见 CCO `00H-F1` 确认**：集中器侧 tx 成功但 rx 均为
   `06H-F230/F3` 主动上报，未观察到任务配置的显式确认帧。可能原因：CCO 对
   「无地址域(module_id=0)」下行配置帧不确认 / 需带地址域 / 需先 `10H-F2` 等
   查询成功后再配置。待下一轮验证。

### 运行证据（服务保持后台监听）

- 端口：`ss -tln` → 8790 / 8765 / 8766 均在 LISTEN
- 进程：`python -m workbench.run / listener.run / module_log.run` 存活（nohup + disown）
- 解析后端：`GET 127.0.0.1:8765/api/version` → `parse_backend=remote`, dll_available=true
- 串口会话：listener(COM24) / cco(COM9) / sta(COM8) 三路 running
