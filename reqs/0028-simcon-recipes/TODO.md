# REQS-0028 — TODO

## 阶段划分

### P0：recipe 库
- [x] 创建 `libs/sim_concentrator/recipes/` 目录
- [x] 初始 recipe：clear_archive（清空档案）、add_archive（添加档案）
- [x] 实现 `recipes.py`：list/get/build_task/run_recipe

### P1：API 挂载
- [x] simcon api 挂载 `GET /recipes`、`GET /recipes/{id}`、`POST /recipes/{id}/run`
- [x] clear_archive 动态流程（分页查询→分批删除→复查）+ confirm 安全闸门
- [x] add_archive 静态 steps（meters/protocol 参数化）

### P2：测试与验证
- [x] test_recipes.py 16 项（加载/参数化/执行/API）
- [x] 实机：添加档案无地址域 11H-F1
- [x] 实机：清空档案 查询→删除→复查 total=0
- [x] REQS-INDEX 登记

### 后续可扩展 / 遗留
- [ ] 更多 recipe（查询档案、配置采集任务、分钟采集下发等）
- [ ] AI 技能层封装（让 AI 直接调 recipes 接口）
- [ ] 遗留：实机验证后物理串口被拔出（dmesg `USB disconnect`）；集中器对空档案
  查询的应答行为需串口恢复后复核
