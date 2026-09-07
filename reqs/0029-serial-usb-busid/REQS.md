# REQS-0029 — WSL 串口稳定识别（usbipd BusId 锚点）

> 状态：✅ 已完成
> 创建：2026-09-02
> 关联：REQS-0020（实机测试）、REQS-0028（recipes）、tools/scripts/uart-map.ps1

## 1. 背景与问题

Windows 侧串口用 COM4/COM9 等命名；经 usbipd 挂载到 WSL 后变成 ttyUSB1/ttyACM0 等
节点。这些节点按 **attach 顺序**动态分配，USB 重插/重挂载后 **tty 编号会漂移**
（如本次 /dev/ttyUSB0 消失、变 /dev/ttyUSB1//dev/ttyUSB2），导致
`config/serial_ports.json` 里固定的 `linux_device` 失效，无法稳定识别设备。

用户需求：**按 Windows 侧的稳定 USB 标识（usbipd BusId）来定位串口**，
不受 tty 节点漂移影响。

## 2. 方案（usbipd BusId 作稳定锚点）

**关键机制**：usbipd 的 BusId（如 6-1、6-4、5-2）是 Windows 物理 USB 端口的稳定锚点。
usbip attach 到 WSL 后，vhci_hcd 的 status 文件（`/sys/devices/platform/vhci_hcd.0/status`）
每行的 `dev` 字段编码了 BusId：

```
hub port sta spd dev      sockfd local_busid
hs  0001 006 002 00060001 000003 1-2     →  bus=0x0006, port=0x0001 → BusId 6-1
```

- `local_busid`（1-2）对应 tty 的 pyserial `location`，会漂移
- `dev`（00060001）编码的 **usbipd BusId 稳定**

### 实现
1. `serial_io.vhci_busid_map()`：解析 vhci status → `{local_busid: usbipd_busid}`
2. `serial_io.device_busid(device, port_info)`：tty → usbipd BusId（单口返回 `6-1`，
   双口 CH342 返回 `5-2:1.0`/`5-2:1.2`）
3. `SerialPortMapping` 加 `usb_busid` 字段；`SerialPortCatalog.find_by_busid()` +
   `merge_system_ports(device_busids=...)` 枚举时优先按 busid 匹配
4. `resolve_serial_config` mapping 分支：WSL 下按 usb_busid 定位真实设备（回退静态路径）
5. `config/serial_ports.json` 加 usb_busid；`uart-map.ps1` map 后输出 BusId↔tty 对照

## 3. 验收

- [x] vhci status 解析：`ttyUSB1→6-1, ttyUSB2→6-4, ttyACM0→5-2:1.0, ttyACM1→5-2:1.2`
- [x] 枚举：4 个设备全部按 busid 正确映射（即使 linux_device 静态值已失效）
- [x] `mapping_id: simcon` 打开 → busid 定位到 `/dev/ttyUSB1`（而非静态 /dev/ttyUSB0）
- [x] 测试 49 项全绿（新增 usb_busid/CH342 双口/busid 匹配用例）
- [x] config：simcon=6-1, listener=6-4, cco=5-2:1.0, sta=5-2:1.2

## 4. 变更记录

- 2026-09-02：实现 BusId 锚点识别；`/dev/ttyUSB0` 漂移场景实测通过。
