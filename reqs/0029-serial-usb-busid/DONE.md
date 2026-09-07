# REQS-0029 — DONE

> 状态：✅ 已完成
> 完成：2026-09-02

## 已交付

1. **vhci BusId 解析**（`serial_io.py`）：
   - `vhci_busid_map()`：解析 `/sys/devices/platform/vhci_hcd.0/status` → `{local_busid: usbipd_busid}`
   - `device_busid()`：tty 设备 → usbipd BusId；双口 CH342 返回 `5-2:1.0`/`5-2:1.2`
2. **映射层**（`serial_mapping.py`）：
   - `SerialPortMapping.usb_busid` 字段；`SerialPortCatalog.find_by_busid()`
   - `merge_system_ports(device_busids=...)` 枚举时优先按 busid 匹配
3. **打开定位**（`serial_io.py`）：
   - `_find_device_by_busid()` + `resolve_serial_config` mapping 分支按 usb_busid 定位真实设备
4. **配置**（`config/serial_ports.json`）：simcon=6-1, listener=6-4, cco=5-2:1.0, sta=5-2:1.2
5. **脚本**（`uart-map.ps1`）：map 成功后输出 BusId↔tty 对照（awk 解析 vhci status）

## 实机验证

- 枚举：`ttyUSB1→simcon(6-1), ttyUSB2→listener(6-4), ttyACM0→cco(5-2:1.0), ttyACM1→sta(5-2:1.2)` 全部在线
- `POST /api/simcon/open {"mapping_id":"simcon"}` → 通过 busid 6-1 定位到 `/dev/ttyUSB1`
  （静态配置的 /dev/ttyUSB0 已失效，仍正确打开）
- 测试：`test_serial_mapping.py`（含新增 usb_busid/CH342/busid 优先用例）+ `test_serial_io.py` + recipes 等 49 项全绿

## 遗留

- COM8/COM9 与 ttyACM0/1 的 interface 配对按当前枚举假设（cco=1.0, sta=1.2），
  若实际接线相反需调整 config 的 usb_busid。
