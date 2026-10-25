"""平台上的"翻译"：把机器写的文本翻成人话。"""

from __future__ import annotations

from app.wording.service import summarize, translate, translate_many

__all__ = ["summarize", "translate", "translate_many"]
