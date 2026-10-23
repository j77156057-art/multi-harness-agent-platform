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
    "harness released": "租约被放开了（这条活还等着有人接）",
    "role_not_allowed": "这个身份不能做这件事",
    "identity_mismatch": "身份对不上（拿这份凭据的人不是那条活的负责人）",
    "token_missing": "这次调用没带凭据",
    "token_invalid": "凭据不对",
}

#: 平台内部错误的代码 → 人话。
#:
#: 这些代码是写给日志看的（`HarnessLeaseLost`），人读到它只会知道"出错了"，
#: 不知道出了什么事。真数据里输出页显示过「它当时说：formal worker error:
#: HarnessLeaseLost」—— 认不出的代码原样留着，但认得出的换成人话。
WORKER_ERROR_TEXT: dict[str, str] = {
    "harnessleaselost": "租约丢了（这个窗口的活被接管了，或者租约过期了）",
    "harnessleasemissing": "租约不见了（这条活已经被别人领走）",
    "harnessnotregistered": "这个窗口没在平台登记",
    "harnessoffline": "这个窗口不在线",
    "cliunavailable": "这个窗口的命令没装好（或者不在 PATH 上）",
    "budgetexhausted": "预算用完了",
    "providererror": "模型那边出错了",
    "verifystalled": "检查卡住了",
    "timeout": "超时了",
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
    # 平台内部错误：代码本身是给日志看的，认得出的换成人话，认不出的照旧留着
    # （总比整句英文看不懂强，但也别把信息丢了）。
    worker = re.match(r"^formal worker error:\s*(\w+)", text.strip(), re.I)
    if worker:
        code = worker.group(1)
        human = WORKER_ERROR_TEXT.get(code.lower())
        return f"平台内部出错了（{human}）" if human else f"平台内部出错了（{code}）"
    # 失败原因前面可能带着任务号：「FD-93c9e4fa07914dbc: harness CLI exited 1」。
    # 那是同一个失败原因，只是多带了个前缀（现场报告里就是这么记的）—— 去掉前缀
    # 再认一次；里面认不出就照旧留着，别把前缀去掉却把意思丢了。
    prefixed = re.match(r"^FD-[0-9a-f]{16}:\s*(.+)$", text.strip(), re.I)
    if prefixed:
        return _reason_text(prefixed.group(1))
    # Windows 的退出码是**无符号**的：-1 传上来是 4294967295（真数据里出现过）。
    # 十个数字摆在那里没人认得，转回负数才是人平时说的那个数。
    exited = re.match(r"^harness cli exited (\d+)$", text.strip(), re.I)
    if exited:
        code = int(exited.group(1))
        if code >= 2**31:
            code -= 2**32
        return f"窗口的命令退出了（退出码 {code}）"
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

    ``original`` 回显**调用方给的原文**（一个字不动），因为调用方要拿它当
    回填的钥匙：方案正文末尾带换行时，按洗过的文本找回来会找不到。
    """
    original = str(text or "")
    raw = original.strip()
    if not raw:
        return {"text": "", "kind": kind, "translated": False, "original": original}
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
    elif chosen in ("text", "prose"):
        # 自由文本：把里面的机器标记换成能读的说法。
        cleaned = humanize_text(raw, names=names)
        return {
            "text": cleaned,
            "kind": chosen,
            "translated": cleaned != raw,
            "original": original,
        }
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
        return {"text": raw, "kind": chosen, "translated": False, "original": original}
    return {"text": human, "kind": chosen, "translated": True, "original": original}


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


#: 自由文本里的**机器标记**：出现在人写的段落里（结论、报告、评审意见）。
#:
#: 这些词本身没有错，错的是**给人看的时候**还留着 —— 一句结论的第一句就是
#: "FD-0b0bcc415aa9fe2f（attempt .a1）· 提交 32f84d6 · exclusive_paths"，
#: 人读到的是编号，不是"这件事"。这里把它们换成能读的说法。
#:
#: **只动标识符，不动英文词**：散文里的 "commit"（动词）、"harness"（测试台）
#: 不是编号，替掉它们只会把句子改坏 —— 所以这些词不在表里。
#:
#: **字段名也不动**：`attempt_id` 是平台接口里的字段名，方案里那句"attempt_no
#: 是序号，不是 attempt_id"是给人对照口径的；把它换成"轮次"就把话改错了。
TEXT_MARKS: tuple[tuple[Any, str], ...] = (
    (re.compile(r"`?\battempt[`_\s]*\.?a(\d+)\b`?", re.I), r"第 \1 轮"),
    # 「管理员 Harness」是这台机器上的**说法**（管理窗口），不是"测试台"。
    # 中文前面带着角色词的才是这个词；英文散文里的 "the test harness" 不动。
    # 后面的空格一起吃掉：中文里"管理员窗口改派"才是顺的。
    (re.compile(r"管理员\s*[Hh]arness\s*"), "管理员窗口"),
    # 机器词后面常常紧跟着它的人话解释：「改动只落在 `exclusive_paths` 唯一允许的
    # 路径上」。直接替换会变成"落在 允许改的路径 唯一允许的路径上"（同一件事说了
    # 两遍），所以先把这一整段折成一句人话，再轮到下面那条兜底规则。
    (
        re.compile(
            r"`?\bexclusive_paths\b`?\s*(?:唯一允许的路径|唯一可改的路径|the only allowed path)",
            re.I,
        ),
        "唯一允许的路径",
    ),
    (re.compile(r"`?\bexclusive_paths\b`?", re.I), "允许改的路径"),
    (re.compile(r"\btaskIds?\b", re.I), "任务号"),
    (re.compile(r"\bkind\s*=\s*development\b", re.I), "开发租约"),
    (re.compile(r"\bworktree\b", re.I), "独立工作区"),
    (re.compile(r"\bagent/[A-Za-z0-9._/-]+"), "一条分支"),
)


#: 任务号：``FD-`` 加 16 位十六进制。
_TASK_ID_IN_TEXT_RE = re.compile(r"FD-[0-9a-f]{16}", re.I)


def _task_ids(text: str) -> str:
    """任务号 → 人话。

    **只有一个**的时候说"这条活"（人读到的是"这件事"，不是编号）；
    **有两个以上**就得用短号 —— 方案里那句"只有 plan_id 与任务号变了
    （FD-b0302acf397341cd → FD-e88e4c97e92843c9）"洗成"这条活 → 这条活"
    就成了一句废话，两头还分不清。
    """
    ids = {match.group(0) for match in _TASK_ID_IN_TEXT_RE.finditer(text)}
    if len(ids) > 1:
        # 连着外面的反引号一起吃掉（`` `FD-…` `` → 短号）：留着半对反引号，
        # 后面 markdown 化的时候配对会错位，页面上就会冒出孤零零的一个 `。
        # 短号取 7 位（跟 git 的短 hash 一样长，也就不会再被 shorten_hash 砍一刀）。
        return re.sub(
            r"`?FD-([0-9a-f]{16})`?",
            lambda match: match.group(1)[:7],
            text,
            flags=re.I,
        )
    return re.sub(r"`?\bFD-[0-9a-f]{16}\b`?", "这条活", text, flags=re.I)


def shorten_hash(text: str) -> str:
    """一次提交的完整 hash → 短 hash（7 位）。

    长短 hash 指的是同一件事，但长的是 40 个字符 —— 摆在一句话里比一句话本身
    还长。短 hash 是人（和 git）都认得的写法，留着比换成一个词更好。
    """
    return re.sub(
        r"\b(?=[0-9a-f]{7,40}\b)(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b",
        lambda match: match.group(0)[:7],
        text,
    )


#: 「窗口：<id>」这种写法 —— id 后面通常跟的是**窗口 id**。
_WINDOW_LABEL_RE = re.compile(
    r"(执行窗口|窗口|执行者|负责人|owner|发起人|发起|传阅对象)\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9._-]{0,63})",
    re.I,
)


def _window_names(text: str, names: dict[str, str] | None) -> str:
    """「执行窗口：opencode」→「执行窗口：OpenCode」。

    窗口 id 是机器 id：小白看到 ``opencode`` 不知道那是谁。登记名在平台的名册
    里，所以这一层拿得到 —— 拿不到就照旧显示 id。
    """
    if not names:
        return text

    def replace(match: re.Match[str]) -> str:
        label, ident = match.group(1), match.group(2)
        name = names.get(ident)
        return f"{label}：{name}" if name else match.group(0)

    return _WINDOW_LABEL_RE.sub(replace, text)


def humanize_text(text: str, names: dict[str, str] | None = None) -> str:
    """一段自由文本 → 把里面的机器标记换成能读的说法。

    这是**规则**（确定性的、不调模型）：只换标记本身，句子照旧。
    整段的"翻译"仍然是模型那一层的事。
    """
    out = str(text or "")
    out = _task_ids(out)
    for pattern, replacement in TEXT_MARKS:
        out = pattern.sub(replacement, out)
    out = shorten_hash(out)
    return _window_names(out, names)


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
