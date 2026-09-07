# REQS-0029 — TODO

## 阶段划分

### P0：机制验证
- [x] 确认 vhci status 的 dev 字段编码 usbipd BusId（00060001→6-1）
- [x] 确认 pyserial location ↔ vhci local_busid 对应

### P1：代码实现
- [x] `serial_io.vhci_busid_map()` + `device_busid()`（单口/双口 interface 后缀）
- [x] `SerialPortMapping.usb_busid` + `find_by_busid` + `merge_system_ports(device_busids)`
- [x] `resolve_serial_config` mapping 分支按 busid 定位
- [x] `config/serial_ports.json` 加 usb_busid
- [x] `uart-map.ps1` 输出 BusId↔tty 对照

### P2：测试与验证
- [x] 单测：usb_busid 匹配 / 枚举优先 busid / CH342 双口 interface
- [x] 实机：`mapping_id: simcon` 打开定位到 /dev/ttyUSB1（busid 6-1）
- [x] REQS-INDEX 登记

### 后续
- [ ] COM8/COM9 ↔ ttyACM0/1 的 interface 配对已按当前枚举假设（5-2:1.0=cco, 5-2:1.2=sta），
      若与实际接线不符需调 config
