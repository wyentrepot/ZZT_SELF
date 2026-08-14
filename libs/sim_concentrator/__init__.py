"""sim_concentrator — 模拟集中器模块。

模拟集中器上位机：向 CCO 模块下发 1376.2（Q/GDW 10376.2）帧、接收并解析
模块上行帧、按内置/用例规则主动应答、执行 AI 传入的验证任务并返回结论。

设计要点（DECISIONS.md ADR-10）：
- 帧格式统一走 parser_lib.adapters.adapter_10376（构帧 build_frame / 解析
  QGDW103762Adapter.decode），保证构帧与解析口径一致。
- 独立模块，不侵入 listener 现有采集流程；loghooks 预留的第三来源
  concentrator_10376 由本模块的 parse 接口补上（见 sources.py）。
- 串口通道可读写（读线程 + 写队列），区别于 listener 的只读监听。
"""
__version__ = "0.1.0"
