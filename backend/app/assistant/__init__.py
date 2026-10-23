"""平台助手（office-assistant）的 ReAct 引擎。

定位分工：平台代码负责"能写成 if/else 的"（收集、阈值、打包证据束）；
助手只干"能读懂但写不成规则的"（术后诊断、审核助攻、语义排险）。
本包只装引擎本体：工具表、prompt、回路、trace。worker 进程与模型通道的
接线在下一期（见 docs/proposals/assistant-react-loop.md）。
"""

from app.assistant.loop import AssistantLoop, Budget

__all__ = ["AssistantLoop", "Budget"]
