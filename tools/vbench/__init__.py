"""vbench —— 虚拟串口台架（virtual serial bench）。

日常开发的实机平替：用 os.openpty 伪终端对搭建被测件（sim_concentrator /
sim_meter 等）的黑盒/白盒测试台架，模拟 CCO/STA 的工作节奏发帧应答。

架构铁律（见 README）：
1. 唯一数据交换通道是虚拟串口字节流——被测件与模拟角色之间、模拟角色相互
   之间（含 HPLC 跳）都不共享内存队列、不直接函数调用；
2. 每个角色只持有自己的串口端点，接线（谁连谁）只发生在 bench 装配层；
3. 不作为 AI 工作台技能暴露，仅供开发/CI 使用。

模块：
- pair         虚拟串口对（openpty）与字节泵
- frames645    DL/T 645 构帧/解帧（自包含，零仓库依赖）
- frames13762  1376.2 本地帧薄封装（复用 sim_concentrator.frame_codec 保证字节口径一致）
- fake_sta     模拟 STA：绑表扫掠/抄表/改速率节奏
- fake_cco     模拟 CCO：bpsCheck/档案/路由抄读中继节奏
- bench        台架装配与场景（bind / read / e2e）
- cli          命令行入口：python3 -m tools.vbench <scenario>
"""
