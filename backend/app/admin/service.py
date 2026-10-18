"""需求细化：群主的一句话 -> 管理员 harness 模型产出的结构化任务规格。

为什么单独一层：``changes_requested`` 的返修闭环证明过同一类缺陷——"人以为系统
记住了，其实只留了一个哈希"。分派也一样：表单由谁填、内容是哪儿来的，平台必须
留得下痕迹。这里因此只做一件事：把自然语言要求交给管理员 harness 自己的 CLI，
拿回严格 JSON，校验不过就明确失败；**不猜、不替模型发明规格**。

失败一律 fail-closed：CLI 超时/非零退出/输出里没有合法 JSON/路径不合法，都抛
:class:`AdminHarnessError`，绝不返回一个"看起来像规格"的降级结果。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import (
    CliRunner,
    CliUnavailable,
    HarnessTimeout,
    SubprocessRunner,
)
from app.adapter.harnesses.qoder import qoder_command
from app.config import settings
from app.db.connection import Database
from app.tasks.service import SchedulerService

#: 执行者身份只接受"注册过的 base harness id"或"<base>-member|window-<slot>"，
#: 与 ``app.adapter.formal_worker.base_harness_id`` 同一口径：不让模型发明一个新身份。
_BASE_HARNESSES = ("codex", "claude-code", "opencode", "qoder", "codebuddy")
_INSTANCE_RE = re.compile(
    r"^(?:codex|claude-code|opencode|qoder|codebuddy)-(?:member|window)"
    r"-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"
)


class AdminHarnessError(RuntimeError):
    """细化失败。调用方必须把它当失败，不能当"空规格"。"""


@dataclass(frozen=True, slots=True)
class RefinedTask:
    title: str
    goal: str
    exclusive_paths: list[str]
    acceptance: str
    harness_id: str

    def as_spec(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "goal": self.goal,
            "exclusive_paths": list(self.exclusive_paths),
            "acceptance": self.acceptance,
            "harness_id": self.harness_id,
        }


def _valid_exclusive_path(raw: str) -> bool:
    """Same normalization rule the dispatch service enforces (dispatch.py L49).

    Duplicating the predicate here is deliberate: the refine layer must refuse a
    spec the dispatch layer would refuse, and it must refuse it *before* the
    admin harness claims to have produced a spec.
    """
    if not isinstance(raw, str):
        return False
    value = raw.strip()
    if not value or value.startswith(("/", ".")) or ":" in value or "\\" in value:
        return False
    return ".." not in value.split("/")


def _known_harness_id(value: str) -> bool:
    return value in _BASE_HARNESSES or bool(_INSTANCE_RE.fullmatch(value))


def extract_json_object(text: str) -> dict[str, Any]:
    """Find the first balanced JSON object in ``text``.

    The CLI prints JSON lines; a model's answer may also arrive wrapped in prose
    or a fenced block. Scanning for a *balanced* object is the only extraction
    that does not silently truncate nested arrays -- a regex would cut the task
    list short and hand dispatch a half spec.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    raise AdminHarnessError("refine output carried no balanced JSON object")


def _questions(payload: dict[str, Any]) -> list[str]:
    """The admin's questions back to the owner, or ``[]`` when it produced a spec.

    Asking wins over splitting: if the model returned both, the spec was built
    on an assumption the admin just told us it is unsure about, and dispatch
    would freeze that assumption into a task.
    """
    raw = payload.get("questions")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _answer_text(stdout: str) -> str:
    """Pull the model's message out of the CLI's JSON-line envelope.

    ``qoderclicn --output-format json`` prints one object per line with the
    answer under ``result``/``text``/``message``/``content`` -- the same keys the
    worker adapter reads. Falls back to the raw stdout so a CLI that changes its
    envelope still gets a chance to parse, and fails loudly if neither works.
    """
    collected: list[str] = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("result", "text", "message", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                collected.append(value)
    if collected:
        return "\n".join(collected)
    return stdout


class AdminService:
    """The admin harness's own model call, plus its audit trail."""

    def __init__(
        self,
        database: Database,
        *,
        harness_id: str,
        runner: CliRunner | None = None,
        scratch_dir: Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.database = database
        self.harness_id = harness_id
        self.runner = runner or SubprocessRunner()
        self.scratch_dir = Path(scratch_dir or settings.admin_scratch_dir)
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.admin_refine_timeout_seconds
        )

    # ------------------------------------------------------------------ refine

    def running_harnesses(self) -> list[str]:
        rows = self.database.read(
            "SELECT harness_id FROM harnesses WHERE status = 'online'"
            " AND registration_version IS NOT NULL ORDER BY harness_id"
        )
        return [str(row["harness_id"]) for row in rows]

    def refine(
        self,
        *,
        requirement: str,
        group_id: str,
        max_tasks: int = 3,
        answers: list[str] | None = None,
    ) -> dict[str, Any]:
        """One model call: requirement -> validated task specs, or owner questions.

        Two honest outcomes, both persisted to the group conversation:

        * ``ok`` -- a validated spec, ready for dispatch;
        * ``needs_owner`` -- the admin harness refused to guess and asked the
          group owner instead.

        The second is required behaviour, not an error: it is neither an
        exception nor a silent empty spec. Guessing the owner's intent is the
        failure mode this branch exists to prevent, and the owner reads the
        question through the platform's own conversation.
        """
        text = (requirement or "").strip()
        if not text:
            raise AdminHarnessError("requirement must not be empty")
        if max_tasks < 1 or max_tasks > 8:
            raise AdminHarnessError("max_tasks must be between 1 and 8")
        replies = [str(item).strip() for item in (answers or []) if str(item).strip()]
        members = self.running_harnesses()
        if not members:
            raise AdminHarnessError("no registered online harness to assign work to")

        prompt = self._prompt(
            text, group_id=group_id, max_tasks=max_tasks, members=members, answers=replies
        )
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        argv = qoder_command(prompt, self.scratch_dir)
        started = time.monotonic()
        try:
            run = self.runner.run(
                argv,
                cwd=self.scratch_dir,
                timeout_seconds=float(self.timeout_seconds),
                on_tick=None,
                tick_interval_seconds=None,
            )
        except CliUnavailable as exc:
            raise AdminHarnessError(f"admin harness CLI unavailable: {exc}") from exc
        except HarnessTimeout as exc:
            raise AdminHarnessError(f"admin harness refine timed out: {exc}") from exc
        if run.returncode != 0:
            raise AdminHarnessError(
                f"admin harness refine exited {run.returncode}: {run.stderr.strip()[-400:]}"
            )

        payload = extract_json_object(_answer_text(run.stdout))
        questions = _questions(payload)
        result: dict[str, Any] = {
            "harness_id": self.harness_id,
            "group_id": group_id,
            "requirement": text,
            "answers": replies,
            "model": {
                "adapter": "qoderclicn-print",
                "duration_seconds": round(time.monotonic() - started, 1),
            },
        }
        if questions:
            result.update({"status": "needs_owner", "tasks": [], "questions": questions})
        else:
            tasks = self._parse_payload(payload, members=members, max_tasks=max_tasks)
            result.update(
                {
                    "status": "ok",
                    "tasks": [task.as_spec() for task in tasks],
                    "questions": [],
                }
            )
        # Posted for BOTH outcomes: the owner's global view is the group
        # conversation, so a question the admin refused to guess must land there
        # too, not only in an HTTP response the owner never sees.
        result["receipt"] = self._post_receipt(result, group_id=group_id)
        return result

    # --------------------------------------------------------------- internals

    def _prompt(
        self,
        requirement: str,
        *,
        group_id: str,
        max_tasks: int,
        members: list[str],
        answers: list[str],
    ) -> str:
        rule = "is the fixed deliverable path and must never be rewritten by you"
        lines = [
            "You are the ADMINISTRATOR harness of a multi-harness coordination platform.",
            "The group owner states requirements only in natural language; turning that",
            "statement into executable task specifications is your job, and only yours.",
            "",
            "Group owner requirement (verbatim):",
            "---",
            requirement,
            "---",
            "",
            f"Group: {group_id}",
            f"Split into 1..{max_tasks} tasks. Each task is executed by exactly one member.",
            "",
            "Hard rules:",
            "1. Answer with ONE JSON object and nothing else: no prose, no markdown fence.",
            "2. Every task carries exactly these keys: title, goal, exclusive_paths,",
            "   acceptance, harness_id.",
            "3. exclusive_paths are repository-relative POSIX paths. Absolute paths, a",
            "   leading '.', a ':' or a '\\' or a '..' segment are rejected by the platform.",
            "   Keep them narrow: the file or directory this task really touches.",
            f"   Note: {rule}.",
            "4. acceptance must be checkable by a human reviewer, not a restatement of the goal.",
            f"5. harness_id must be one of these registered members: {', '.join(members)}.",
            "6. Before splitting, check what already exists: when your channel has network",
            "   access, use it (official docs, upstream repositories, standards) and prefer",
            "   reusing a proven approach over inventing one. Do not design in isolation.",
            "7. Never guess the group owner's intent. If the requirement is ambiguous, or a",
            "   choice would change the outcome, stop and ask: answer with",
            '   {"questions":["..."]} instead of {"tasks":[...]}. The owner answers in plain',
            "   language; only then produce the task list.",
            "",
            'Output shape: {"tasks":[{"title":"...","goal":"...",'
            '"exclusive_paths":["..."],"acceptance":"...","harness_id":"..."}]}',
            'or, when you must ask first: {"questions":["...","..."]}',
        ]
        if answers:
            lines += [
                "",
                "The group owner already answered your earlier questions. Do not ask these again:",
                "---",
                *answers,
                "---",
                "Produce the task list now, incorporating those answers verbatim.",
            ]
        return "\n".join(lines)

    def _parse_payload(
        self, payload: dict[str, Any], *, members: list[str], max_tasks: int
    ) -> list[RefinedTask]:
        raw_tasks = payload.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise AdminHarnessError("refine output had no non-empty 'tasks' array")
        if len(raw_tasks) > max_tasks:
            raise AdminHarnessError(
                f"refine output proposed {len(raw_tasks)} tasks, more than the {max_tasks} allowed"
            )
        tasks: list[RefinedTask] = []
        for index, raw in enumerate(raw_tasks, start=1):
            if not isinstance(raw, dict):
                raise AdminHarnessError(f"task {index} is not an object")
            title = str(raw.get("title") or "").strip()
            goal = str(raw.get("goal") or "").strip()
            acceptance = str(raw.get("acceptance") or "").strip()
            harness_id = str(raw.get("harness_id") or "").strip()
            paths = raw.get("exclusive_paths")
            if not title or not goal or not acceptance:
                raise AdminHarnessError(f"task {index} is missing title/goal/acceptance")
            if not isinstance(paths, list) or not paths:
                raise AdminHarnessError(f"task {index} has no exclusive_paths")
            cleaned = [str(item).strip() for item in paths]
            bad = [item for item in cleaned if not _valid_exclusive_path(item)]
            if bad:
                raise AdminHarnessError(f"task {index} has out-of-scope paths: {bad}")
            if harness_id not in members:
                raise AdminHarnessError(
                    f"task {index} names an executor that is not a registered online member:"
                    f" {harness_id!r}"
                )
            if not _known_harness_id(harness_id):
                raise AdminHarnessError(
                    f"task {index} names an unsupported harness id: {harness_id!r}"
                )
            tasks.append(
                RefinedTask(
                    title=title,
                    goal=goal,
                    exclusive_paths=cleaned,
                    acceptance=acceptance,
                    harness_id=harness_id,
                )
            )
        return tasks

    def _post_receipt(self, result: dict[str, Any], *, group_id: str) -> dict[str, Any]:
        """Post the refinement into the group conversation as the admin harness.

        Deliberately NOT a new ``task_events`` row: the M/O/R allowlist is a closed
        set inside the normative spec, and inventing a name there is an R10-fix, not
        a feature side effect. The conversation is the platform's own channel for
        "someone said something in the group", it already carries ``sender_type`` /
        ``sender_id`` / ``created_seq``, and the group owner can read it in the app.
        The body is the model's own output forwarded verbatim -- a receipt, not
        narration, so the authorship claim stays true.
        """
        lines = [
            f"管理员 harness 细化回执（{self.harness_id}）",
            f"群主要求：{result['requirement']}",
        ]
        if result.get("answers"):
            lines.append("群主对上一轮问题的回答：" + "；".join(result["answers"]))
        if result.get("status") == "needs_owner":
            lines.append("管理员判断：要求有歧义，先问清楚，不猜。请群主用大白话回答：")
            lines.extend(
                f"{index}. {question}"
                for index, question in enumerate(result["questions"], start=1)
            )
        else:
            for index, task in enumerate(result["tasks"], start=1):
                lines.append(
                    f"{index}. 标题：{task['title']}｜执行者：{task['harness_id']}"
                    f"｜允许路径：{', '.join(task['exclusive_paths'])}"
                )
                lines.append(f"   目标：{task['goal']}")
                lines.append(f"   验收：{task['acceptance']}")
        model = result.get("model") or {}
        tail = (
            "下一步由管理员 harness 分派。"
            if result.get("status") != "needs_owner"
            else "群主回答后会再细化一次。"
        )
        lines.append(
            f"（模型通道：{model.get('adapter', 'unknown')}，"
            f"耗时 {model.get('duration_seconds', '?')}s；{tail}）"
        )
        return SchedulerService(self.database).post_message(
            group_id=group_id,
            author_kind="harness",
            author_id=self.harness_id,
            body="\n".join(lines),
            mentions=[],
        )


__all__ = ["AdminHarnessError", "AdminService", "RefinedTask", "extract_json_object"]
