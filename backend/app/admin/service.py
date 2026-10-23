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

from app.adapter.formal_worker import base_harness_id, build_adapter, known_bases
from app.adapter.harnesses.cli import (
    CliRunner,
    CliUnavailable,
    HarnessTimeout,
    SubprocessRunner,
)
from app.config import settings
from app.db.connection import Database
from app.tasks.service import SchedulerService

#: 执行者身份只接受"注册过的 base harness id"或"<base>-member|window-<slot>"，
#: 与 ``app.adapter.formal_worker.base_harness_id`` 同一口径：不让模型发明一个新身份。
#: base 是否注册由 ``known_bases()`` 现读（内置 + ``adapters/cli.json`` 声明），
#: 所以这里只认形状，不抄一份名单。
_INSTANCE_RE = re.compile(
    r"^(?P<base>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)-(?:member|window)"
    r"-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"
)

#: The platform assistant is neither an administrator window nor a project
#: member. It has a model identity but no worker seat; a goal must never be
#: assigned to it even when an API caller omits the project member list.
PLATFORM_ASSISTANT_HARNESS_ID = "office-admin"


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
    if not isinstance(value, str):
        return False
    value = value.strip()
    bases = known_bases()
    if value in bases:
        return True
    match = _INSTANCE_RE.fullmatch(value)
    return bool(match and match.group("base") in bases)


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
    raise AdminHarnessError("admin harness output carried no balanced JSON object")


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


def _answer_text(adapter: Any, stdout: str) -> str:
    """Pull the model's message out of the CLI's JSON-line envelope.

    Parsing belongs to the adapter, not to this module: the envelope differs per
    CLI (``qoderclicn --output-format json`` prints ``result``/``text``/... lines;
    other CLIs answer in plain text). Keeping a copy here would make the refine
    call read one channel's envelope for every channel. Falls back to the raw
    stdout so a CLI that changes its envelope still gets a chance to parse.
    """
    parsed = adapter.parse_output(stdout)
    message = parsed.get("final_message") if isinstance(parsed, dict) else None
    if isinstance(message, str) and message.strip():
        return message
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

    # --------------------------------------------------------------- revise

    def revise_with_reviews(
        self,
        *,
        plan_id: str,
        goal: str,
        tasks: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fold the circulation comments into the plan.

        The administrator's own model channel does the folding -- the same
        channel that produced the first refinement.  The comments are quoted
        verbatim, and the result is the task list again (the same tasks, better
        specified).  Nothing is dispatched here: the plan stays a draft until a
        **human** approves it.
        """
        if not reviews:
            return list(tasks)
        comments = "\n".join(
            f"- {review.get('harness_id')}: {review.get('body')}" for review in reviews
        )
        prompt = (
            "你是管理员 Harness。下面是一版方案和传阅收到的意见。请把意见并进方案，"
            "然后**只输出一个 JSON 对象**："
            '{"tasks": [{"title": …, "goal": …, "exclusive_paths": [...], '
            '"acceptance": …, "harness_id": …}]}。'
            "意见没有要求改动的任务原样保留。\n\n"
            f"方案目标：{goal}\n\n"
            f"当前任务：{json.dumps(tasks, ensure_ascii=False)}\n\n"
            f"收到的意见：\n{comments}\n\n"
            "只输出那个 JSON 对象，不要别的文字。"
        )
        payload, model = self._invoke_model(prompt)
        if isinstance(payload, list):
            payload = {"tasks": payload}
        revised = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(revised, list) or not revised:
            # The model did not answer with a task list: keep the plan as it is
            # rather than inventing one.
            return list(tasks)
        items = [item for item in revised if isinstance(item, dict)]
        if len(items) != len(tasks):
            # A different number of tasks is not a revision: it is a new plan,
            # and that is the owner's call, not the model's.
            return list(tasks)
        # The **ids stay**: a revision is the same tasks, better specified.
        # A model that invents task ids would silently create new work.
        merged: list[dict[str, Any]] = []
        for original, item in zip(tasks, items, strict=False):
            changed = {k: v for k, v in item.items() if k not in ("id", "task_id")}
            merged.append({**original, **changed})
        return merged

    # ------------------------------------------------------------------ refine

    def running_harnesses(self) -> list[str]:
        rows = self.database.read(
            "SELECT harness_id FROM harnesses WHERE status = 'online'"
            " AND registration_version IS NOT NULL ORDER BY harness_id"
        )
        return [str(row["harness_id"]) for row in rows]

    def harness_has_credential(self, harness_id: str) -> bool:
        """Whether this window can actually authenticate to the platform.

        "Registered and online" is not the same as "can act": a window that was
        registered but never issued a token cannot claim a task, so a plan
        assigned to it would sit in ``ready`` forever. The administrator must
        only hand work to windows that can pick it up.
        """
        rows = self.database.read(
            "SELECT COUNT(*) AS n FROM auth_tokens WHERE subject_type = 'harness'"
            " AND subject_id = ? AND revoked_at IS NULL",
            (str(harness_id),),
        )
        return bool(rows and int(rows[0]["n"]) > 0)

    def dispatched_task_ids(self, prefix: str = "admin-") -> set[str]:
        """The task ids this administrator dispatched.

        The dispatch idempotency table remembers every dispatch by its request
        id, and the administrator's own requests are prefixed (``admin-goal-…``
        for a refinement, ``admin-reassign-…`` for a re-point). The re-point
        only touches **its own** dispatches: a task somebody else dispatched is
        that dispatcher's business, not something to silently rewrite.
        """
        rows = self.database.read(
            "SELECT first_result_json FROM command_idempotency"
            " WHERE operation = 'formal_dispatch' AND idempotency_key LIKE ?",
            (f"{prefix}%",),
        )
        task_ids: set[str] = set()
        for row in rows:
            try:
                result = json.loads(str(row["first_result_json"] or "{}"))
            except (TypeError, ValueError):
                continue
            task_id = result.get("task_id")
            if isinstance(task_id, str) and task_id:
                task_ids.add(task_id)
        return task_ids

    def assignable_harnesses(self) -> list[str]:
        """The members this administrator may hand work to.

        One rule, used by both the refinement and the re-pointing, so the two
        cannot drift: a candidate must be online and registered, hold a
        platform credential (otherwise it could never claim the task), not be an
        administrator window, and not share the administrator's provider
        (cross-provider review would be impossible).
        """
        admin_ids = set(getattr(settings, "admin_subjects", frozenset())) | {self.harness_id}
        available = [
            harness_id
            for harness_id in self.running_harnesses()
            if harness_id not in admin_ids and harness_id != PLATFORM_ASSISTANT_HARNESS_ID
        ]
        admin_provider = self._provider(self.harness_id)
        if admin_provider is not None:
            available = [
                harness_id
                for harness_id in available
                if self._provider(harness_id) != admin_provider
            ]
        return [harness_id for harness_id in available if self.harness_has_credential(harness_id)]

    def _provider(self, harness_id: str) -> str | None:
        """The registry's provider for a harness, or None when it is unknown.

        ``review_policy=cross_provider`` compares this registry value, not what
        the administrator's CLI happens to be. It must therefore also bound
        who the administrator may assign work to: a task given to a member of
        the administrator's own provider can never be reviewed by that
        administrator, so dispatching it would create a task that can only
        stall (or force someone to bypass the policy).
        """
        rows = self.database.read(
            "SELECT provider FROM harnesses WHERE harness_id = ?", (harness_id,)
        )
        if not rows or not rows[0]["provider"]:
            return None
        return str(rows[0]["provider"])

    def refine(
        self,
        *,
        requirement: str,
        group_id: str,
        max_tasks: int = 3,
        answers: list[str] | None = None,
        members: list[str] | None = None,
        project_name: str | None = None,
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
        # 候选只有一处定义（`assignable_harnesses`）：在线且已登记、**有平台
        # 凭据**（否则它永远领不了任务）、不是管理员窗口、provider 与管理员不同
        # （否则交叉审核不可能成立）。细化与改派用同一条规则，不会各说各话。
        actionable = self.assignable_harnesses()
        requested_members = members
        if requested_members is not None:
            selected = [str(item).strip() for item in requested_members if str(item).strip()]
            actionable = [item for item in actionable if item in selected]
        members = actionable
        if not members:
            raise AdminHarnessError(
                "no registered online member harness to assign work to"
                + (
                    " (the requested project members are offline, administrators,"
                    " share the administrator's provider (cross-provider review"
                    " would be impossible), or hold no platform credential, so"
                    " they could never claim the task)"
                    if requested_members is not None
                    else " (no online member with a credential has a provider"
                    " different from the administrator)"
                )
            )

        prompt = self._prompt(
            text,
            group_id=group_id,
            max_tasks=max_tasks,
            members=members,
            answers=replies,
            project_name=project_name,
        )
        # 模型通道来自**管理员是谁**，不是写死的 qoder：管理员 harness 换成别的
        # CLI，细化就必须走它自己的那条通道。选不出来就明确失败，不退回一条
        # "看起来能用"的默认通道 —— 那会让回执里的 `model.adapter` 说谎。
        payload, model = self._invoke_model(prompt)
        questions = _questions(payload)
        result: dict[str, Any] = {
            "harness_id": self.harness_id,
            "group_id": group_id,
            "requirement": text,
            "project_name": project_name,
            "members": list(members),
            "answers": replies,
            "model": model,
        }
        if questions:
            result.update({"status": "needs_owner", "tasks": [], "questions": questions})
        else:
            tasks = self._parse_payload(
                payload,
                members=members,
                max_tasks=max_tasks,
                project_name=project_name,
            )
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

    def answer(
        self,
        *,
        question: str,
        group_id: str,
        asker: str,
    ) -> dict[str, Any]:
        """Answer one private question, and send the answer back privately.

        The owner's question arrives as a *direct* message (R10 §19): a message
        row whose ``refs`` carry the ``direct`` signature. The answer leaves the
        same way -- same table, same ordering authority, same visibility rule
        (sender and recipients only). That is why this adds no event name, no
        state and no schema column.

        The model is given the group's task ledger and nothing else, and is told
        to say it does not know when the ledger does not answer: a private
        channel is still a place where a guess would be read as a fact.
        """
        text = question.strip() if isinstance(question, str) else ""
        if not text:
            raise AdminHarnessError("question must not be empty")
        recipient = asker.strip() if isinstance(asker, str) else ""
        if not recipient:
            raise AdminHarnessError("a direct answer needs a recipient")
        if recipient == self.harness_id:
            # A window answering itself would produce a private echo the owner
            # never sees and that still claims to be an answer.
            raise AdminHarnessError("refusing to answer the administrator's own message")
        prompt = self._answer_prompt(text, group_id=group_id, asker=recipient)
        payload, model = self._invoke_model(prompt)
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            raise AdminHarnessError("the administrator model returned no answer text")
        message = SchedulerService(self.database).post_message(
            group_id=group_id,
            author_kind="harness",
            author_id=self.harness_id,
            body=answer,
            mentions=[recipient],
            direct=True,
        )
        return {
            "harness_id": self.harness_id,
            "group_id": group_id,
            "asker": recipient,
            "question": text,
            "answer": answer,
            "model": model,
            "message": message,
        }

    # --------------------------------------------------------------- internals

    def _model_channel(self) -> tuple[Any, str]:
        """Which CLI answers the refinement -- decided by the admin's identity.

        The refinement used to hard-wire ``qoder_command``: the administrator was
        a qoder harness *by accident*, and choosing a different administrator
        would still have run the qoder CLI while the receipt claimed that was
        the model channel. Resolving through the same adapter table the worker
        uses keeps one executable per registered identity and fails closed when
        the identity is not a registered base harness or window.
        """
        try:
            base = base_harness_id(self.harness_id)
        except ValueError as exc:
            raise AdminHarnessError(
                f"administrator {self.harness_id!r} has no registered CLI channel: {exc}"
            ) from exc
        pinned = getattr(settings, "admin_model_channel", "auto")
        if pinned and pinned != "auto":
            if base not in known_bases():
                raise AdminHarnessError(
                    f"administrator {self.harness_id!r} resolves to an unknown base harness {base!r}"
                )
            # 身份必须能解析（上面那句），但通道可以被显式钉到另一条 CLI：这是
            # "管理员窗口的 CLI 不在本机"时的部署选择，不是猜。
            base = pinned
        try:
            # No client: the refine call needs the argv contract and the output
            # parser, not the HTTP surface.
            adapter = build_adapter(base, None, harness_id=self.harness_id)
        except ValueError as exc:
            raise AdminHarnessError(
                f"administrator {self.harness_id!r} resolves to an unknown base harness {base!r}: {exc}"
            ) from exc
        return adapter, base

    def _invoke_model(self, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run the administrator's own model channel once and parse its JSON.

        Both planning and review go through this path so the receipt's
        ``model.adapter`` names the executable that actually answered. The
        caller must treat every exception as a hard failure; there is no
        fallback model and no invented empty result.
        """
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        adapter, base = self._model_channel()
        work_order = adapter.prepare_work_order(prompt, self.scratch_dir)
        argv = adapter.command(work_order, self.scratch_dir)
        # CodeBuddy-like CLIs keep only the first positional line; long prompts
        # must travel through stdin (same rule as the member worker).
        via_stdin = bool(getattr(adapter, "prompt_via_stdin", False))
        started = time.monotonic()
        try:
            run = self.runner.run(
                argv,
                cwd=self.scratch_dir,
                timeout_seconds=float(self.timeout_seconds),
                on_tick=None,
                tick_interval_seconds=None,
                stdin_text=prompt if via_stdin else None,
            )
        except CliUnavailable as exc:
            raise AdminHarnessError(f"admin harness CLI unavailable: {exc}") from exc
        except HarnessTimeout as exc:
            raise AdminHarnessError(f"admin harness call timed out: {exc}") from exc
        finally:
            adapter.cleanup_work_order()
        if run.returncode != 0:
            # Codex reports stream/provider failures on stdout JSONL, not
            # stderr; keep both tails so the group message is diagnosable.
            detail = "\n".join(
                part for part in (run.stderr.strip()[-400:], run.stdout.strip()[-800:]) if part
            )
            raise AdminHarnessError(f"admin harness call exited {run.returncode}: {detail}")
        payload = extract_json_object(_answer_text(adapter, run.stdout))
        model = {
            # Keep the registered adapter name (``qoderclicn-print`` etc.) so
            # old and new receipts remain comparable.
            "adapter": getattr(getattr(adapter, "registration", None), "adapter", None)
            or getattr(adapter, "binary", base),
            "channel": base,
            "duration_seconds": round(time.monotonic() - started, 1),
        }
        return payload, model

    def review(
        self,
        *,
        task: dict[str, Any],
        spec: dict[str, Any],
        diff: dict[str, Any],
        verification: dict[str, Any] | None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """The administrator's own verdict on one submitted attempt.

        This method does not claim or submit a review lease -- that is the
        caller's state transition. It only runs the administrator model and
        validates its JSON. ``request_changes`` must carry at least one
        concrete finding, otherwise the rework loop would repeat blindly.
        """
        prompt = self._review_prompt(
            task=task,
            spec=spec,
            diff=diff,
            verification=verification or {},
            project_name=project_name,
        )
        payload, model = self._invoke_model(prompt)
        verdict = str(payload.get("verdict") or "").strip()
        if verdict not in {"approve", "request_changes"}:
            raise AdminHarnessError("review output verdict must be 'approve' or 'request_changes'")
        raw_findings = payload.get("findings") or []
        if not isinstance(raw_findings, list):
            raise AdminHarnessError("review output findings must be a list")
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(raw_findings, start=1):
            if isinstance(item, str):
                message = item.strip()
                severity = "note"
            elif isinstance(item, dict):
                message = str(item.get("message") or "").strip()
                severity = str(item.get("severity") or "note").strip()
            else:
                raise AdminHarnessError(f"review finding {index} is not text or an object")
            if not message:
                raise AdminHarnessError(f"review finding {index} has no message")
            findings.append({"severity": severity, "message": message})
        if verdict == "request_changes" and not findings:
            raise AdminHarnessError("request_changes requires at least one finding")
        return {
            "verdict": verdict,
            "findings": findings,
            "model": model,
            "task_id": task.get("task_id"),
            "attempt_id": task.get("attempt_id") or task.get("current_attempt_id"),
        }

    def _review_prompt(
        self,
        *,
        task: dict[str, Any],
        spec: dict[str, Any],
        diff: dict[str, Any],
        verification: dict[str, Any],
        project_name: str | None,
    ) -> str:
        patch = diff.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            # No patch is not approval evidence. Tell the model to refuse
            # instead of letting it invent a positive verdict from a summary.
            patch = "(no patch available; the attempt did not publish a readable diff)"
        # Keep the prompt bounded. A huge generated file is still evidence, but
        # the model needs the beginning and the explained truncation more than
        # it needs 2 MB of minified output.
        if len(patch) > 60000:
            patch = patch[:60000] + "\n…(patch truncated at 60000 characters)"
        return "\n".join(
            [
                "You are the ADMINISTRATOR harness reviewing a member's submitted attempt.",
                "Decide only from the task specification and the real diff below.",
                "Do not approve because the member says it is done; do not request",
                "changes for style preferences that the acceptance does not require.",
                "",
                f"Project: {project_name or 'unspecified'}",
                "Task row:",
                json.dumps(task, ensure_ascii=False, indent=2),
                "Approved specification:",
                json.dumps(spec, ensure_ascii=False, indent=2),
                "Verification receipt:",
                json.dumps(verification, ensure_ascii=False, indent=2),
                "",
                "Real diff:",
                patch,
                "",
                "Rules:",
                "1. Answer with ONE JSON object and nothing else.",
                '2. Shape: {"verdict":"approve"|"request_changes","findings":['
                '{"severity":"note|minor|major|critical","message":"..."}]}',
                "3. approve only when the diff satisfies the acceptance and stays",
                "   inside exclusive_paths. Otherwise request_changes.",
                "4. request_changes must name every concrete defect a reworking member",
                "   can act on; do not write a general complaint.",
                "5. If the diff is missing or unreadable, request_changes and say so.",
                "6. Never claim to have run tests or read files you were not given.",
            ]
        )

    def _prompt(
        self,
        requirement: str,
        *,
        group_id: str,
        max_tasks: int,
        members: list[str],
        answers: list[str],
        project_name: str | None = None,
    ) -> str:
        rule = "is the fixed deliverable path and must never be rewritten by you"
        lines = [
            "You are the ADMINISTRATOR harness of a multi-harness coordination platform.",
            "The group owner states requirements only in natural language; turning that",
            "statement into executable task specifications is your job, and only yours.",
            "",
            f"Project: {project_name}" if project_name else "",
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
        ]
        if project_name:
            lines += [
                f"8. Every task title must start with {project_name!r} followed by ' · '.",
                "   The office UI uses that prefix to keep one project's tasks separate.",
            ]
        lines += [
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

    def _answer_prompt(self, question: str, *, group_id: str, asker: str) -> str:
        """One question, one JSON answer, no dispatch and no promises.

        The question is private, but the facts behind it are public inside the
        group, so the ledger is handed over verbatim instead of a summary: a
        summary is where an invented reason would hide.
        """
        lines = [
            "You are the ADMINISTRATOR harness of a multi-harness coordination platform.",
            "The group owner is asking you a question in a PRIVATE channel. Only the",
            "owner and you can read the answer -- but it is still a claim about real",
            "work, so it must be true.",
            "",
            f"Group: {group_id}",
            f"Recipient of this answer: {asker}",
            "",
            "The platform's task ledger for this group (your only ground truth; you",
            "cannot see the members' screens, their private notes or their reasoning):",
            "---",
            self._task_ledger(group_id),
            "---",
            "",
            "Group owner's question (verbatim):",
            "---",
            question,
            "---",
            "",
            "Hard rules:",
            '1. Answer with ONE JSON object and nothing else: {"answer":"..."}',
            "2. Answer only from the ledger above. If it does not contain what the",
            "   owner asks for (for example why a member abandoned a task, or how",
            "   many tokens were spent), say plainly that you do not know and name",
            "   what is missing. Never invent a reason, a person, a number or a time.",
            "3. Do not promise work you are not doing in this same turn: you are",
            "   answering a question, not dispatching, reviewing or re-pointing.",
            "4. Write in the language the owner used. Keep it short: a few sentences.",
        ]
        return "\n".join(lines)

    def _task_ledger(self, group_id: str) -> str:
        """What the platform actually knows about this group's work.

        Handing the ledger to the model is the difference between "answering
        from the record" and "inventing a reason". A broken read fails closed
        instead of degrading into an empty ledger the model would fill in.
        """
        try:
            tasks = SchedulerService(self.database).list_tasks(group_id)
        except Exception as exc:  # noqa: BLE001 - a failed read must not become a guess
            raise AdminHarnessError(f"cannot read the task ledger: {exc}") from exc
        if not tasks:
            return "（这个群里还没有任务。）"
        lines: list[str] = []
        for task in tasks:
            failure = task.get("last_failure")
            reason = str(failure.get("reason") or "").strip() if isinstance(failure, dict) else ""
            line = (
                f"- {task.get('task_id')}｜{task.get('title') or '（无标题）'}"
                f"｜状态 {task.get('status')}"
                f"｜执行者 {task.get('owner') or '（无人）'}"
            )
            if reason:
                line += f"｜最近一次失败：{reason}"
            lines.append(line)
        return "\n".join(lines)

    def _parse_payload(
        self,
        payload: dict[str, Any],
        *,
        members: list[str],
        max_tasks: int,
        project_name: str | None = None,
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
            # Titles are the only project scope marker on a task row (tasks
            # carry group_id, not a local project id). The office reads the
            # prefix to separate projects that share one formal group, so make
            # it deterministic instead of trusting the model to remember.
            if project_name and not title.startswith(f"{project_name} · "):
                title = f"{project_name} · {title}"
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
            f"项目：{result.get('project_name') or '未命名项目'}",
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
