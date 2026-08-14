"""loghooks: 配置驱动的日志运行状态钩子。

从 module_log（文本行）与 listener（hex 帧）双来源抓取关键运行状态事件，
生成摘要 JSON 供 AI 烧录验证核查，排除海量轮询/状态机噪音。
"""

__version__ = "0.1.0"