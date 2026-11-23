"""能力适配层（REQS-0041 §10.3 / §10.4）：每个文件只对接一个领域库，彼此零 import。

- ``simcon.py``   —— 工作台 HTTP ``/api/simcon/*``、``/api/ai/v2/verification-runs``
- ``simeter.py``  —— 工作台 HTTP ``/api/simeter/*``
- ``module.py``   —— 工作台 HTTP ``/api/module-serial/*``、``/api/ai/v2/module-actions``
  （模块串口控制台：复位/控制台命令/该口会话日志）
- ``frames.py``   —— 进程内只读 ``parser_lib.facade``（decode/build/verify）

本 ``__init__`` **不导入任何适配器**：能力层之间互不 import 的铁律同样适用于
包初始化，避免「import 一个适配器顺带拉进另一个」。
适配器请按需显式导入：``from case_runner.capabilities.simcon import SimconCapability``。
"""
