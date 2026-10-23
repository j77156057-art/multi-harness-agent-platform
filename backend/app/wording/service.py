"""把机器写的文本翻成人话 —— 平台的**单一出处**。

为什么放在平台这一层：这些文本的出处就在平台 —— 失败原因是平台写的、事件名是
平台的事件、窗口的登记名在平台的注册表里。以前人话表散在前端三处
（`state-labels.js`、`admin-agent.js` 的 `REASON_LABEL`、`flat-office.js` 的
`reasonText`），改一次要改三处，还各翻各的。

两层：

* **规则层**（:func:`translate`）：确定性的、永远可用。失败原因、事件名、状态、
  窗口 id、绝对路径、commit / 分支 / 任务号。
* **模型层**（:func:`summarize`）：方案原文、规格正文这种"源码一样"的自由段落 ——
  走管理员自己的模型通道翻成一句人话，带缓存。

**一条规矩贯穿两层：认不出的原样留着。** 规则表里没有的文本照实返回，
并在结果里标 ``translated: False`` —— 宁可原样，也不要编一个错的说法。
模型层翻不出来（通道不可用、超时、没给 JSON）就报明确原因，不给半句假话。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from app.config import settings

#: 失败原因：精确匹配（键小写）。
REASON_TEXT: dict[str, str] = {
    "harness cli exited 1": "窗口的命令退出了（退出码 1）",
    "no credential": "这个窗口没有平台凭据，领不了活",
    "not_registered": "这个窗口还没在平台登记",
    "offline": "这个窗口现在不在线",
    "provider_quota_exhausted": "模型那边的额度用完了",
    "quota exhausted": "额度用完了",
    "rate limited": "模型那边限流了",
    "insufficient_quota": "额度不够",
    "merge conflict": "合并撞车了，要人来定",
    "verify failed": "检查没通过",
    "timeout": "超时了",
    "abandoned": "这条活被放弃了",
    "cancelled": "这条活被取消了",
}

#: 失败原因：按模式认（顺序有意义，先具体后宽泛）。
REASON_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^harness cli exited (\d+)$", re.I), "窗口的命令退出了（退出码 {0}）"),
    (re.compile(r"^harness timeout:.*exceeded (\d+)s", re.I), "窗口跑超时了（超过 {0} 秒没做完）"),
    (re.compile(r"^harness timeout", re.I), "窗口跑超时了，没在时间内做完"),
    (re.compile(r"^formal worker error:\s*(\w+)", re.I), "平台内部出错了（{0}）"),
    (re.compile(r"quota|insufficient_credit|insufficient_quota", re.I), "额度用完了"),
    (re.compile(r"rate.?limit", re.I), "模型那边限流了"),
    (re.compile(r"merge conflict|conflict.*merge", re.I), "合并撞车了，要人来定"),
    (re.compile(r"verify|verification.*fail", re.I), "检查没通过"),
    (re.compile(r"^(provider|upstream)\b", re.I), "模型那边出了问题"),
)

#: 事件名：平台的事件 → 人话（真数据里出现过的都收了）。
EVENT_TEXT: dict[str, str] = {
    "claim": "有人领了这条活",
    "claim_for_revision": "领回去返工",
    "start": "开工了",
    "submit_result": "交上去了",
    "abandon": "放弃了",
    "release": "放开了",
    "cancel": "取消了",
    "reopen": "重新打开了",
    "approve": "批准了",
    "reassign": "换人做了",
    "human.resume": "你回答了",
    "human.release": "你放行了",
    "human.reopen": "你重新打开了",
    "human.cancel": "你取消了",
    "human.escalate": "你把它交上来了",
    "human.adjudicate": "你裁决了",
    "human_wait_timeout": "等人回答超时了",
    "plan.approve": "方案批准了",
    "plan.reject": "方案被驳回了",
    "review.claim": "有人领了审查",
    "review.submit": "审查交上去了",
    "request_changes": "要求返工",
    "reopen_for_changes": "打回去返工",
    "verify_passed": "校验通过了",
    "merge_start": "开始合并",
    "merge_success": "合进主分支了",
    "merge_reverted": "合并回退了",
    "merge_conflict": "合并撞车了",
    "merge_conflict_needs_owner": "合并撞车了，要你定",
    "merge_post_commit_failure_escalated": "合并之后又出错了，交上来了",
    "lease_expire": "工位过期了",
    "attempt_budget_exhausted": "尝试次数用完了",
    "stuck_threshold_reached": "卡到阈值了",
    "escalation_reminder": "提醒管理员了",
    "permission_denied": "有人被拒绝了",
    "secret_scan_blocked": "被密钥扫描拦下了",
    "idempotency_conflict": "重复提交了",
    "task.created": "活建好了",
    "task.dispatched": "派下去了",
}

#: 状态：平台的状态 → 人话（与办公室的状态词一致）。
STATE_TEXT: dict[str, str] = {
    "draft": "还没开始",
    "ready": "等人接",
    "queued": "排队中",
    "claimed": "已有人接",
    "in_progress": "正在做",
    "verifying": "在检查",
    "awaiting_review": "等着审",
    "changes_requested": "要返工",
    "approved": "审过了",
    "merging": "正在合并",
    "merged": "已完成",
    "abandoned": "已放弃",
    "stale": "卡住了",
    "cancelled": "已取消",
    "escalated": "等你处理",
    "awaiting_human": "等你回答",
    "reclaiming": "重新找人做",
    "plan_drift": "需求变了",
    "impl_drift": "做偏了",
    "blocked": "遇到问题",
    "needs_help": "在求助",
}

#: 哪些 kind 是"自由文本"（要模型才翻得动）。
FREE_FORM_KINDS = frozenset({"document", "prose"})

_TASK_ID_RE = re.compile(r"^FD-[0-9a-f]{16}$", re.I)
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:\\")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _reason_text(text: str) -> str | None:
    """失败原因 → 人话（认不出的返回 None）。"""
    lowered = text.strip().lower()
    if lowered in REASON_TEXT:
        return REASON_TEXT[lowered]
    for pattern, template in REASON_PATTERNS:
        match = pattern.search(text)
        if match:
            return template.format(*match.groups()) if match.groups() else template
    return None


def _path_text(text: str) -> str | None:
    """绝对路径 → 文件名（路径是给机器看的，文件名才是人能读的）。"""
    if not _WINDOWS_PATH_RE.match(text) and "/" not in text:
        return None
    parts = [part for part in re.split(r"[\\/]", text) if part]
    if len(parts) < 2:
        return None
    return parts[-1]


def _id_text(text: str) -> str | None:
    """任务号 / commit / 分支 → 人能读的短形式。"""
    if _TASK_ID_RE.match(text):
        return f"任务 {text[3:11]}"
    if _COMMIT_RE.match(text):
        return f"提交 {text[:7]}"
    if text.startswith("agent/"):
        return "一条工作分支"
    return None


def translate(
    text: str,
    *,
    kind: str = "auto",
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """一段机器文本 → 人话。

    ``kind`` 告诉它这是什么类型的文本（``reason`` / ``event`` / ``status`` /
    ``harness`` / ``path`` / ``id`` / ``auto``）。``auto`` 时按文本长什么样自己认。

    认不出的**原样返回**，``translated`` 为 False —— 调用方可以据此决定是不是
    要把原文也摆出来。
    """
    raw = str(text or "").strip()
    if not raw:
        return {"text": "", "kind": kind, "translated": False, "original": ""}
    chosen = kind
    if chosen == "auto":
        chosen = _guess_kind(raw)
    human: str | None = None
    if chosen == "reason":
        human = _reason_text(raw)
    elif chosen == "event":
        human = EVENT_TEXT.get(raw)
    elif chosen == "status":
        human = STATE_TEXT.get(raw)
    elif chosen == "harness":
        human = (names or {}).get(raw)
    elif chosen == "path":
        human = _path_text(raw)
    elif chosen == "id":
        human = _id_text(raw)
    if human is None and chosen != "auto":
        # 猜的那一类没命中，再按别的类试一遍（一个字符串可能既是路径也是 id）。
        for other in ("reason", "event", "status", "path", "id"):
            if other == chosen:
                continue
            human = _translate_as(raw, other, names)
            if human is not None:
                chosen = other
                break
    if human is None:
        return {"text": raw, "kind": chosen, "translated": False, "original": raw}
    return {"text": human, "kind": chosen, "translated": True, "original": raw}


def _translate_as(raw: str, kind: str, names: dict[str, str] | None) -> str | None:
    if kind == "reason":
        return _reason_text(raw)
    if kind == "event":
        return EVENT_TEXT.get(raw)
    if kind == "status":
        return STATE_TEXT.get(raw)
    if kind == "path":
        return _path_text(raw)
    if kind == "id":
        return _id_text(raw)
    if kind == "harness":
        return (names or {}).get(raw)
    return None


def _guess_kind(raw: str) -> str:
    """按形状猜这是哪一类文本。"""
    if raw in EVENT_TEXT:
        return "event"
    if raw in STATE_TEXT:
        return "status"
    if raw.lower() in REASON_TEXT:
        return "reason"
    if _TASK_ID_RE.match(raw) or _COMMIT_RE.match(raw) or raw.startswith("agent/"):
        return "id"
    if _WINDOWS_PATH_RE.match(raw) or "/" in raw:
        return "path"
    if _reason_text(raw) is not None:
        return "reason"
    return "prose"


def translate_many(
    texts: list[str],
    *,
    kind: str = "auto",
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """一批文本一起翻（界面一次要翻好几条，别让人等好几次）。"""
    return [translate(text, kind=kind, names=names) for text in texts]


# ------------------------------------------------------------------ 模型层


#: 翻一句的缓存：按文本的 sha256 存，翻过的不再问模型。
def _cache_path() -> Path:
    return Path(settings.data_dir) / "wording-cache.json"


def _read_cache() -> dict[str, Any]:
    try:
        document = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _write_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # 缓存写不进去不算失败：下一次再问一遍模型而已。
        pass


#: 给模型的那句话：要人话，不要"优化/重构/对齐"这种词。
SUMMARY_PROMPT = (
    "把下面这段技术文本翻成一句**人话**（中文，不超过 {max_chars} 个字），"
    "说清「这件事要做什么」，不要罗列术语，不要复述原文。\n"
    "只输出一个 JSON 对象：{'summary': '……'}\n\n"
    "原文：\n{text}\n"
)


def summarize(
    text: str,
    *,
    invoke_model: Any,
    max_chars: int = 60,
) -> dict[str, Any]:
    """自由文本 → 一句人话（走管理员自己的模型通道，带缓存）。

    ``invoke_model`` 是"跑一次模型并把它的 JSON 答案给我"的 callable ——
    平台里那是管理员服务的 ``_invoke_model``（它跑管理员窗口自己的 CLI）。
    这里不自己造通道：**翻译要用的是管理员那台 CLI**，别的通道说的不是同一件事。

    翻不出来就抛（调用方报明确原因），**不给半句假话**。
    """
    raw = str(text or "").strip()
    if not raw:
        return {"summary": "", "cached": False, "original": ""}
    key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cache = _read_cache()
    hit = cache.get(key)
    if isinstance(hit, str) and hit.strip():
        return {"summary": hit.strip(), "cached": True, "original": raw}
    prompt = SUMMARY_PROMPT.replace("{max_chars}", str(max_chars)).replace("{text}", raw[:8000])
    payload, _model = invoke_model(prompt)
    summary = ""
    if isinstance(payload, dict):
        summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise ValueError("the model did not return a summary")
    cache[key] = summary
    _write_cache(cache)
    return {"summary": summary, "cached": False, "original": raw}
