"""Local operator-run pull worker; no browser can impersonate a Harness."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import time
from pathlib import Path

import httpx

from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
from app.adapter.harnesses.cli import (
    CliFailed,
    CliHarnessWorker,
    CliRunResult,
    CliUnavailable,
    HarnessTimeout,
    ProviderRoute,
    SubprocessRunner,
)
from app.adapter.harnesses.codebuddy import CodeBuddyAdapter
from app.adapter.harnesses.codex import CodexAdapter
from app.adapter.harnesses.generic import GenericCliAdapter, load_declarations
from app.adapter.harnesses.opencode import OpenCodeAdapter
from app.adapter.harnesses.pi import PiAdapter
from app.adapter.harnesses.qoder import QoderAdapter
from app.adapter.scope import path_in_scope
from app.config import settings
from app.db.connection import Database
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeCapacityExceeded, WorktreeManager
from app.tasks.service import SchedulerService
from app.verify.profile import VerifyPolicy
from app.verify.worker import VerifyWorker

ADAPTER_TYPES = {
    "codex": CodexAdapter,
    "claude-code": ClaudeCodeAdapter,
    "opencode": OpenCodeAdapter,
    "pi": PiAdapter,
    "qoder": QoderAdapter,
    "codebuddy": CodeBuddyAdapter,
}

_INSTANCE_RE = re.compile(
    r"^(?P<base>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)"
    r"-(?P<kind>member|window)-(?P<slot>[A-Za-z0-9][A-Za-z0-9_-]{0,31})$"
)


def known_bases() -> set[str]:
    """Every base a formal worker can run: the built-ins plus declarations.

    The declarations are read here, not cached at import, so an operator can
    add a CLI without restarting anything that has not started yet.  A broken
    declaration raises: a half-read registry would look like a missing harness.
    """
    return set(ADAPTER_TYPES) | set(load_declarations())


def _unquote_git_path(path: str) -> str:
    """Undo git's C-style quoting of a path.

    ``core.quotepath=false`` (set for every platform git call) already hands
    back raw UTF-8 paths, but git still quotes a path that contains a quote or
    a control character. Comparing the quoted form against the authorized path
    would refuse a file that is really in scope, so unwrap it here.

    Git's octal escapes stand for **bytes**, so the escapes are collected as
    bytes and the whole path is decoded as UTF-8 at the end.
    """
    text = path.strip()
    if len(text) < 2 or not text.startswith('"') or not text.endswith('"'):
        return text
    body = text[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and body[index + 1 : index + 4].isdigit():
            try:
                out.append(int(body[index + 1 : index + 4], 8))
                index += 4
                continue
            except ValueError:
                pass
        out.extend(char.encode("utf-8"))
        index += 1
    return out.decode("utf-8", errors="replace")


def base_harness_id(harness_id: str) -> str:
    """Resolve a registered window identity to its explicit CLI adapter.

    Only the two documented instance forms are accepted, and the base has to
    be either built in or declared in ``adapters/cli.json``.  This keeps a typo
    or an unregistered provider from silently selecting a different CLI.
    """
    declared = load_declarations()
    if harness_id in ADAPTER_TYPES or harness_id in declared:
        return harness_id
    match = _INSTANCE_RE.fullmatch(harness_id)
    if match:
        base = match.group("base")
        if base in ADAPTER_TYPES or base in declared:
            return base
    raise ValueError(
        "unsupported formal Harness instance: "
        f"{harness_id}; use a registered base ID or <base>-member-<slot>"
    )


def build_adapter(base_id: str, client, *, harness_id: str):
    """Build the adapter for a base, built in or declared.

    The built-ins are classes written against a measured CLI; a declared base
    is the same ``CliHarness`` contract read from operator-owned facts.  The
    caller keeps the *registered* ``harness_id`` so the receipt names the
    identity that ran, while ``registration.adapter`` names the executable.
    """
    adapter_type = ADAPTER_TYPES.get(base_id)
    if adapter_type is not None:
        return adapter_type(client, harness_id=harness_id)
    declaration = load_declarations().get(base_id)
    if declaration is None:
        raise ValueError(f"unknown base harness: {base_id!r}")
    return GenericCliAdapter(client, harness_id=harness_id, declaration=declaration)


def adapter_for_harness(client, harness_id: str):
    """Build the real CLI adapter selected by the registered Harness identity.

    A formal worker is launched once per Harness token. Selecting the adapter
    from that explicit identity prevents a Claude/OpenCode token from silently
    running the Codex command, which would make the platform receipt lie about
    the executable that produced it.
    """
    return build_adapter(base_harness_id(harness_id), client, harness_id=harness_id)


class ScopedRunner(SubprocessRunner):
    def __init__(
        self,
        spec: dict,
        base: str,
        *,
        platform_commit: bool = False,
        commit_message: str = "chore(formal): submit harness deliverable",
    ):
        super().__init__()
        self.spec, self.base = spec, base
        self.platform_commit = platform_commit
        self.commit_message = commit_message

    def run(self, argv, *, cwd, **kwargs):
        result = super().run(argv, cwd=cwd, **kwargs)
        if result.returncode:
            return result
        if self.platform_commit:
            self._commit_uncommitted_scope(cwd)
        changed = run_git("diff", "--name-only", f"{self.base}..HEAD", cwd=cwd).stdout.splitlines()
        paths = self.spec["exclusive_paths"]
        dirty = run_git("status", "--porcelain", cwd=cwd).stdout.strip()
        # Name the condition that fired. The old single sentence ("dirty, empty
        # or out-of-scope") made a scope-rule bug look like a member mistake and
        # cost a full re-run to tell the two apart.
        reasons: list[str] = []
        if dirty:
            reasons.append("uncommitted changes: " + "; ".join(dirty.splitlines()[:3]))
        if not changed:
            reasons.append(f"no committed change against base {self.base}")
        out_of_scope = [p for p in changed if not path_in_scope(p, paths)]
        if out_of_scope:
            reasons.append("out of scope: " + ", ".join(out_of_scope[:3]))
        if reasons:
            return CliRunResult(
                result.argv,
                1,
                result.stdout,
                result.stderr + "\nRefused: " + "; ".join(reasons) + "\n",
                result.duration_seconds,
            )
        return result

    def _commit_uncommitted_scope(self, cwd: Path) -> None:
        """Submit an authorized Claude result without granting Claude Git access."""
        status = run_git("status", "--porcelain", cwd=cwd).stdout.splitlines()
        if not status:
            return
        paths = self.spec["exclusive_paths"]
        changed_paths: list[str] = []
        for line in status:
            path = line[3:].strip()
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[1]
            path = _unquote_git_path(path)
            path = path.replace("\\", "/")
            candidates = (
                [
                    _unquote_git_path(candidate)
                    for candidate in run_git(
                        "ls-files", "--others", "--exclude-standard", cwd=cwd
                    ).stdout.splitlines()
                ]
                if path.endswith("/")
                else [path]
            )
            for candidate in candidates:
                candidate = candidate.replace("\\", "/")
                if not candidate or not path_in_scope(candidate, paths):
                    raise RuntimeError(
                        "platform commit refused: dirty path outside exclusive_paths"
                    )
                changed_paths.append(candidate)
        run_git("add", "--", *sorted(set(changed_paths)), cwd=cwd)
        staged = [
            _unquote_git_path(path)
            for path in run_git("diff", "--cached", "--name-only", cwd=cwd).stdout.splitlines()
        ]
        if not staged or any(not path_in_scope(path, paths) for path in staged):
            raise RuntimeError("platform commit refused: staged path outside exclusive_paths")
        run_git("commit", "-m", self.commit_message, cwd=cwd)


def ask_pool_plan(
    client,
    *,
    task_id: str,
    harness_id: str,
    attempt_no: int,
    platform_url: str,
    token: str,
) -> dict[str, object]:
    """Ask the platform where this attempt's model calls should go.

    The platform decides: it is the one that knows the previous attempt
    failed on this window's own provider and which quota is alive.  The
    worker only carries out the answer, and a failure to ask is not a reason
    to stop working -- the attempt then runs on the window's own provider, as
    before.
    """
    try:
        response = client.get(
            "/api/v1/pool/plan",
            params={
                "task_id": task_id,
                "harness_id": harness_id,
                "attempt_no": attempt_no,
            },
        )
        if response.status_code >= 400:
            return {
                "mode": "direct",
                "reason": f"the platform did not answer ({response.status_code})",
            }
        plan = response.json()
    except Exception as exc:  # pragma: no cover - network path
        return {"mode": "direct", "reason": f"could not ask the platform ({type(exc).__name__})"}
    if not isinstance(plan, dict):
        return {"mode": "direct", "reason": "the platform's answer was not an object"}
    plan["platform_url"] = platform_url
    plan["token"] = token
    return plan


CIRCULATION_REVIEW_PROMPT = """你是 {harness}。下面是一版方案草案（管理员细化之后传阅给你）。

请给出你的审阅意见，只说下面三件事，最多 5 条，每条一行：
- 哪里不清楚（你照着做会卡住的地方）
- 哪里做不到（缺权限、缺上下文、路径不对）
- 验收怎么检查才说得清

不要复述方案，不要客套，只输出意见正文。

方案：
{document}
"""


def _platform_base_url(client) -> str:
    """The platform's own base URL (the pool lives on the same server)."""
    base = getattr(client, "base_url", "") or ""
    return str(base).rstrip("/")


def _platform_token(client) -> str:
    """This worker's platform token, taken from the client's own header."""
    headers = getattr(client, "headers", None) or {}
    try:
        value = headers.get("Authorization") or headers.get("authorization") or ""
    except Exception:  # pragma: no cover - exotic header containers
        value = ""
    text = str(value)
    if text.lower().startswith("bearer "):
        return text[7:].strip()
    return text.strip()


def submit_circulation_review(
    client,
    adapter,
    *,
    harness_id: str,
    group: str,
    runner: SubprocessRunner | None = None,
) -> bool:
    """Give the platform this window's opinion on a plan that is in circulation.

    The window is asked **by the platform** (a draft is waiting for it), and it
    answers with its own model channel -- the same CLI that would do the work.
    Nothing about the task flow changes: the plan is still a draft, and the
    human still has the final word.
    """
    try:
        rounds = client.get(
            "/api/v1/allocation/circulation",
            params={"group_id": group, "state": "circulating"},
        ).json()
    except Exception:
        return False
    for round_info in rounds.get("rounds") or []:
        reviewers = [str(item) for item in (round_info.get("reviewer_harness_ids") or [])]
        if harness_id not in reviewers:
            continue
        if any(
            str(review.get("harness_id")) == harness_id
            for review in (round_info.get("reviews") or [])
        ):
            continue
        plan_id = str(round_info.get("plan_id") or "")
        try:
            document = client.get(f"/api/v1/plans/{plan_id}/document").json().get("markdown") or ""
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "circulation_review_failed": type(exc).__name__,
                        "plan_id": plan_id,
                        "detail": str(exc)[:200],
                    }
                ),
                flush=True,
            )
            return False
        if not document.strip():
            return False
        scratch = settings.data_dir / "circulation-scratch" / harness_id
        scratch.mkdir(parents=True, exist_ok=True)
        prompt = CIRCULATION_REVIEW_PROMPT.format(harness=harness_id, document=document[:20000])
        # 额度池：评审也是这个窗口的活，人给窗口指定的模型对评审同样算数。
        # 之前只有"执行"走了池子，于是一个自带 provider 不可用的窗口（模型有地域
        # 限制、CLI 挂掉）永远交不出意见 —— 那一轮就永远等不到收齐。
        route_env: dict[str, str] = {}
        platform_base = _platform_base_url(client)
        worker_token = _platform_token(client)
        plan = ask_pool_plan(
            client,
            task_id=f"review:{plan_id}",
            harness_id=harness_id,
            attempt_no=0,
            platform_url=platform_base,
            token=worker_token,
        )
        if plan.get("mode") == "pool":
            protocol = getattr(adapter, "pool_protocol", "openai")
            base_path = str(
                plan.get("anthropic_path")
                if protocol == "anthropic"
                else plan.get("base_path") or ""
            )
            route = ProviderRoute(
                base_url=f"{platform_base}{base_path}",
                token=worker_token,
                model=str(plan.get("model") or ""),
                protocol=protocol,
            )
            adapter.provider_route = route
            route_env = dict(adapter.route_environment())
        try:
            # The work order must travel through the adapter's own transport:
            # some CLIs (and the Windows cmd wrapper) lose everything after the
            # first newline, and a truncated plan would be reviewed blind.
            # The review command is the **read-only** one: a reviewer may look
            # at things, it may not change them.
            work_order = adapter.prepare_work_order(prompt, scratch)
            argv = adapter.review_command(work_order, scratch)
            # The adapter's own environment (Pi's isolated agent directory,
            # for one) belongs to the review as much as it belongs to a task:
            # without it the CLI reads the operator's **global** config and
            # does not find the provider the route points at.
            child_env = dict(adapter.process_environment() or {})
            child_env.update(route_env)
            run = (runner or SubprocessRunner()).run(
                argv,
                cwd=scratch,
                timeout_seconds=900,
                on_tick=None,
                tick_interval_seconds=None,
                stdin_text=prompt if getattr(adapter, "prompt_via_stdin", False) else None,
                env=child_env or None,
            )
        except Exception as exc:
            import traceback

            print(
                json.dumps(
                    {
                        "circulation_review_failed": type(exc).__name__,
                        "detail": str(exc)[:200],
                        "trace": traceback.format_exc()[-600:],
                    }
                ),
                flush=True,
            )
            return False
        finally:
            adapter.cleanup_work_order()
        if run.returncode != 0:
            # 说清楚为什么失败：只报 "cli_exit" 的话，成员既不知道自己哪里错了，
            # 平台上也没人查得到（模型有地域限制、模型没装、CLI 退出码非零，
            # 看起来一模一样）。
            detail = " ".join(
                part
                for part in (getattr(run, "stderr", "") or "", getattr(run, "stdout", "") or "")
                if part
            )
            print(
                json.dumps(
                    {
                        "circulation_review_failed": "cli_exit",
                        "returncode": run.returncode,
                        "detail": detail[-400:],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return False
        # The adapter knows its own CLI's output format; the generic reader
        # only understands JSON event lines.  Pi prints **plain text**, so
        # asking the generic reader first lost its answer and the platform
        # recorded "(没有给出文字意见)" for a window that had answered.
        body = ""
        try:
            parsed = adapter.parse_output(run.stdout) or {}
            body = str(parsed.get("final_message") or "").strip()
        except Exception:  # pragma: no cover - a parser must not break the review
            body = ""
        if not body:
            body = (_last_text(run.stdout) or "").strip()
        body = body or "(没有给出文字意见)"
        try:
            client.post(
                "/api/v1/allocation/circulation/review",
                json={
                    "circulation_id": str(round_info.get("circulation_id") or ""),
                    "harness_id": harness_id,
                    "body": body[:8000],
                },
            )
        except Exception as exc:
            print(json.dumps({"circulation_review_failed": type(exc).__name__}), flush=True)
            return False
        print(
            json.dumps(
                {"circulation_review": plan_id, "harness_id": harness_id, "chars": len(body)},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return True
    return False


ALLOCATION_APPLICATION_REASON = (
    "本窗口申请执行这条任务：方案里指派的执行窗口就是它，"
    "范围与验收标准已在方案快照中写明。"
)


def _allocation_rounds(client, *, group: str) -> list[dict[str, object]]:
    """The allocation rounds of this group (read-only).

    §39 hard rule 2 makes three steps, each with its own actor: the member
    **applies**, the administrator **decides**, the selected window
    **accepts**.  The member needs the read side to take its own step -- there
    is no way to apply for work you cannot see.
    """
    try:
        response = client.get("/api/v1/allocation/rounds", params={"group_id": group})
    except Exception:
        return []
    rounds = response.json().get("rounds")
    return [item for item in (rounds or []) if isinstance(item, dict)]


def _my_tasks_in_round(round_info: dict[str, object], harness_id: str) -> list[str]:
    """The task ids of this round that name this window.

    A task the plan assigned to somebody else is not this window's to apply
    for: the administrator decides among the candidates, and the plan already
    said who the work is for.  A task with no named window is open to anyone
    who wants it -- that is the "no designated window" case, and the
    administrator picks among whoever applied.
    """
    out: list[str] = []
    for task in round_info.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        assigned = [str(x) for x in (task.get("assigned_harness_ids") or []) if str(x)]
        if assigned and harness_id not in assigned:
            continue
        out.append(task_id)
    return out


def submit_allocation_applications(client, *, harness_id: str, group: str) -> bool:
    """Apply for the tasks of an open round that name this window.

    Applying is **not** claiming (§39 hard rule 2): it puts this window in the
    candidate set and nothing else -- no owner, no attempt, no lease, no
    worktree.  Only an *accepted* allocation authorises a Claim, and that
    acceptance is this window's own decision, made in `accept_allocation`.
    """
    applied = False
    for round_info in _allocation_rounds(client, group=group):
        if str(round_info.get("state") or "") != "opened":
            continue
        round_id = str(round_info.get("round_id") or "")
        if not round_id:
            continue
        # One application per harness per round (§39): a second one is refused
        # by the platform, and updating the first one is the only way to change
        # one's mind.
        already = any(
            str(item.get("harness_id") or "") == harness_id
            and str(item.get("state") or "") != "withdrawn"
            for item in (round_info.get("applications") or [])
            if isinstance(item, dict)
        )
        if already:
            continue
        task_ids = _my_tasks_in_round(round_info, harness_id)
        if not task_ids:
            continue
        try:
            client.post(
                "/api/v1/allocation/application",
                json={
                    "round_id": round_id,
                    "harness_id": harness_id,
                    "task_ids": task_ids,
                    "reason": ALLOCATION_APPLICATION_REASON,
                    "body": (
                        "本窗口按方案快照的指派申请执行：" + "、".join(task_ids)
                    ),
                },
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "allocation_application_failed": type(exc).__name__,
                        "round_id": round_id,
                        "harness_id": harness_id,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            continue
        applied = True
        print(
            json.dumps(
                {
                    "allocation_application": round_id,
                    "harness_id": harness_id,
                    "task_ids": task_ids,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return applied


def accept_allocation(client, *, harness_id: str, group: str) -> bool:
    """Accept the assignment the administrator made to this window.

    This is the step that turns an *invited* allocation into an *accepted*
    one, and only an accepted allocation authorises a Claim (§39 hard rule 2).
    The platform never accepts on the window's behalf: the window that takes
    the work is the one that says yes.
    """
    accepted = False
    for round_info in _allocation_rounds(client, group=group):
        for assignment in round_info.get("assignments") or []:
            if not isinstance(assignment, dict):
                continue
            if str(assignment.get("harness_id") or "") != harness_id:
                continue
            if str(assignment.get("state") or "") != "invited":
                continue
            allocation_id = str(assignment.get("allocation_id") or "")
            task_id = str(assignment.get("task_id") or "")
            if not allocation_id:
                continue
            try:
                client.post(
                    "/api/v1/allocation/accept",
                    json={"allocation_id": allocation_id, "harness_id": harness_id},
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "allocation_accept_failed": type(exc).__name__,
                            "allocation_id": allocation_id,
                            "harness_id": harness_id,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                continue
            accepted = True
            print(
                json.dumps(
                    {
                        "allocation_accepted": allocation_id,
                        "task_id": task_id,
                        "harness_id": harness_id,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return accepted


def _last_text(stdout: str) -> str:
    """The last text the CLI printed (its own words, not our guess)."""
    answer = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        for key in ("text", "result", "message"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                answer = value
        part = event.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
            answer = part["text"]
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                answer = content
            elif isinstance(content, list):
                for block in content:
                    if (
                        isinstance(block, dict)
                        and isinstance(block.get("text"), str)
                        and block["text"].strip()
                    ):
                        answer = block["text"]
    return answer


def build_goal(
    spec: dict[str, object],
    *,
    harness_id: str,
    review: dict[str, object] | None = None,
    handoff: dict[str, object] | None = None,
) -> str:
    """The CLI goal for one attempt, carrying the findings when it is a rework.

    R10 §10 ``claim_for_revision`` makes a ``changes_requested`` task claimable
    again, but this goal used to be built from ``spec`` alone. A rework then
    repeated the previous deliverable verbatim: the reviewer's findings were
    only ever stored as a hash, so nothing downstream could act on them and the
    review loop could not converge -- it just burned the attempt budget. The
    findings are now recorded on the review row, served by ``read_context`` and
    appended here verbatim.
    """
    goal = (
        "Use ONLY the immutable approved task below. This workspace may contain only a seed README; "
        "there is no separate TASKS.md requirement if absent. Create a nonempty committed deliverable "
        "strictly inside exclusive_paths; do not read or modify other projects. "
        "Do not change branch, install software or push. "
        + (
            "Do not run git add or git commit; the platform will submit your files after checking scope. "
            if harness_id == "claude-code"
            else (
                "Only write files inside exclusive_paths. Do not run shell commands or Git; "
                "the platform will validate scope and create the commit. "
                if harness_id == "pi"
                else "Commit the deliverable on the current branch. "
            )
        )
        + "Task specification: "
        + json.dumps(spec, ensure_ascii=False)
    )
    findings = (review or {}).get("findings") or []
    if (review or {}).get("verdict") == "request_changes" and findings:
        goal += (
            "\nThis attempt is a REWORK: a reviewer requested changes on the previous submission. "
            "Address every finding below; do not resubmit the previous deliverable unchanged. "
            f"Reviewer findings (verbatim, from review {(review or {}).get('review_id')}): "
            + json.dumps(findings, ensure_ascii=False)
        )
    if handoff:
        goal += (
            "\nThe previous attempt did NOT finish; here is its breakpoint report. "
            "Continue from it instead of starting over: keep what is already written, "
            "fix what it says is broken, and do not repeat the steps it already did. "
            "Breakpoint report (verbatim): " + json.dumps(handoff, ensure_ascii=False)
        )
    return goal


def _classify_failure(
    exc: BaseException,
    *,
    task: dict[str, object],
    manager: WorktreeManager,
    attempt_id: str | None,
) -> tuple[bool, str]:
    """Is this failure worth retrying, and why?

    The distinction matters because the two outcomes are different in the
    platform's own vocabulary:

    * **retryable** -- the *harness or its provider* failed (upstream down, no
      balance, a CLI that died on its own). The work is still wanted, so the
      task goes back to ``ready`` (``RELEASE``) and whoever claims it next
      continues from the breakpoint report.
    * **not retryable** -- the *work* is wrong (the deliverable violates the
      scope) or the platform itself broke, or the attempt budget is spent.
      Those end as ``abandoned``: retrying them would only burn the budget
      again.
    """
    if isinstance(exc, CliFailed):
        stderr = str(exc.result.stderr or "")
        if "Refused:" in stderr:
            # The platform refused the submission: the deliverable is out of
            # scope. A retry would produce the same refusal.
            return False, "the platform refused the submission (out of scope)"
        return True, "the harness CLI failed before it could deliver"
    if isinstance(exc, WorktreeCapacityExceeded):
        # 没有空闲工位**不是这条活的错**：平台现在腾不出手。判死会让一条好活
        # 因为"别人占着位子"而消失。正确做法是把占位还回去、任务留在队列，
        # 等容量松开再被领走（跟 provider 类失败同一条路：release）。
        return True, "the platform has no free worktree slot right now (capacity)"
    if isinstance(exc, CliUnavailable):
        # The binary is not on PATH: the same window would fail the same way.
        return False, "the harness CLI is not available on this host"
    if isinstance(exc, HarnessTimeout):
        return True, "the harness CLI ran out of time"
    budget = int(task.get("max_attempts") or 0)
    used = int(task.get("attempt_no") or 0)
    if budget and used >= budget:
        return False, f"the attempt budget is spent ({used}/{budget})"
    return False, f"the platform failed the attempt ({type(exc).__name__})"


def write_handoff_report(
    path: Path,
    *,
    task: dict[str, object],
    harness_id: str,
    failure: dict[str, object],
    worktree: str | None,
    manager: WorktreeManager,
    attempt_id: str | None,
) -> dict[str, object]:
    """Write the breakpoint report of a failed attempt.

    It is what the next harness needs to continue instead of starting over:
    the error, what the CLI said (its own words), which files it touched, and
    how far the diff got. The worktree itself is archived by the sweeper
    (R10 §14: the old attempt's worktree goes to ``pending_archive`` and is
    bundled into ``data/archives/<attempt_id>/``), so the *files* survive too.
    """
    changed: list[str] = []
    diff_summary = ""
    if worktree:
        candidate = Path(str(worktree))
        if candidate.exists():
            try:
                status = run_git("status", "--porcelain", cwd=candidate, check=False).stdout
                changed = [
                    _unquote_git_path(line[3:].strip()).replace("\\", "/")
                    for line in status.splitlines()
                    if line.strip()
                ]
                diff = run_git("diff", "--stat", cwd=candidate, check=False).stdout.strip()
                diff_summary = diff[:2000]
            except Exception:  # pragma: no cover - evidence path
                changed = []
    report = {
        "task_id": task.get("task_id"),
        "failed_harness": harness_id,
        "failed_attempt": attempt_id,
        "error_type": failure.get("error_type"),
        "error": failure.get("error"),
        "retry_reason": failure.get("retry_reason"),
        "cli_stderr_tail": str(failure.get("stderr") or "")[-2000:],
        "cli_stdout_tail": str(failure.get("stdout") or "")[-2000:],
        "files_touched": changed[:50],
        "diff_stat": diff_summary,
        "archived_worktree": (f"data/archives/{attempt_id}/" if attempt_id else None),
        "scope": "breakpoint report of a failed attempt; the next owner continues from here",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def read_handoff_report(path: Path) -> dict[str, object] | None:
    """The breakpoint report of the last failed attempt, if there is one."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def release_or_abandon(adapter, task_id: str, reason: str) -> str:
    """Give the attempt back, and only call it dead if that is impossible.

    ``abandon`` is not an edge out of ``claimed``: an attempt that failed
    *before* the CLI started (no free worktree, say) leaves the task claimed
    with nobody working on it.  The claim has to go back to the queue in that
    case -- otherwise the task is a zombie until its lease expires, and the
    window's parallel slot stays taken the whole time.

    Returns what happened, for the evidence.
    """
    try:
        adapter.abandon(task_id=task_id, reason=reason)
        return "abandoned"
    except Exception as abandon_error:
        with contextlib.suppress(Exception):
            adapter.release(
                task_id=task_id,
                reason=f"{reason} (abandon refused: {abandon_error})",
            )
            return "released"
        return f"abandon_failed: {abandon_error}"


def resolve_timeout_seconds(explicit: float | None = None) -> float:
    """The member CLI deadline: CLI flag > AP_HARNESS_TIMEOUT_SECONDS > default.

    A non-positive value is treated as "misconfigured" and falls back to the
    built-in default rather than disabling the deadline: an unbounded CLI run
    would hold a worktree slot and a lease forever.
    """
    value = explicit if explicit is not None else settings.harness_timeout_seconds
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 3600.0
    return seconds if seconds > 0 else 3600.0


def run_one(
    client,
    database,
    repo,
    *,
    harness_id: str,
    group: str,
    evidence: Path,
    timeout_seconds: float | None = None,
    task_id: str | None = None,
    attempt_no: int | None = None,
    platform_url: str = "http://127.0.0.1:8848",
    token: str = "",
) -> bool:
    adapter = adapter_for_harness(client, harness_id)
    if task_id is None:
        ready = adapter.list_ready_tasks(group_id=group, limit=1)["tasks"]
        if not ready:
            return False
        task = ready[0]
        claim_request_id = f"formal-{task['task_id']}-a{int(task['attempt_no'])+1}"
    else:
        # Explicit recovery run: list_ready_tasks only returns `ready` tasks, so
        # a worker that claimed but failed before provisioning can never see its
        # own task again. Replaying the original claim_request_id returns the
        # same attempt; the worker then re-provisions that attempt (see
        # CliHarnessWorker.work_on). The operator names both ids so the worker
        # cannot silently replay a different attempt.
        if attempt_no is None:
            raise ValueError("--task-id requires --attempt-no (the claim to replay)")
        task = {"task_id": task_id}
        claim_request_id = f"formal-{task_id}-a{int(attempt_no)}"
    context = adapter.read_context(task_id=task["task_id"])
    spec = context["spec"]
    review = context.get("review") or {}
    manager = WorktreeManager(database, repo)
    service = SchedulerService(database)
    captured = {}

    def provision(item):
        captured.update(item)
        scoped_runner.base = item["base_commit"] or service.repo_head(manager)
        result = service.start_task(
            task_id=item["task_id"],
            attempt_id=item["attempt_id"],
            fencing_epoch=item["fencing_epoch"],
            worktrees=manager,
        )
        # Keep the path the platform actually provisioned. The timeout evidence
        # below used to read ``scoped_runner.worktree``, but ScopedRunner is a
        # subprocess runner and has no such attribute: every timeout was then
        # replaced by AttributeError before the failure evidence was written.
        captured["worktree_path"] = result["worktree_path"]
        return Path(result["worktree_path"])

    scoped_runner = ScopedRunner(
        spec,
        service.repo_head(manager),
        # Claude 与 Pi 不自己提交：平台替它们提交（Claude 是因为它跑在沙箱里，
        # Pi 是因为它不写 Git）。
        platform_commit=isinstance(adapter, ClaudeCodeAdapter | PiAdapter),
        commit_message=f"chore({task['task_id']}): submit Claude deliverable"
        if isinstance(adapter, ClaudeCodeAdapter)
        else (
            f"chore({task['task_id']}): submit Pi deliverable"
            if isinstance(adapter, PiAdapter)
            else "chore(formal): submit harness deliverable"
        ),
    )
    worker = CliHarnessWorker(adapter, runner=scoped_runner, heartbeat_seconds=20)
    # 额度池：平台说这条活该换额度，就走池子；没说，就用这个窗口自己的 provider。
    route_env: dict[str, str] = {}
    plan = ask_pool_plan(
        client,
        task_id=task["task_id"],
        harness_id=harness_id,
        attempt_no=int(task.get("attempt_no") or 0) + 1,
        platform_url=platform_url,
        token=token,
    )
    if plan.get("mode") == "pool":
        protocol = getattr(adapter, "pool_protocol", "openai")
        base_path = str(
            plan.get("anthropic_path") if protocol == "anthropic" else plan.get("base_path") or ""
        )
        route = ProviderRoute(
            base_url=f"{platform_url.rstrip('/')}{base_path}",
            token=token,
            model=str(plan.get("model") or ""),
            protocol=protocol,
        )
        adapter.provider_route = route
        route_env = dict(adapter.route_environment())
        route_env["AGENT_OFFICE_POOL_SOURCE"] = str(plan.get("upstream") or "pool")
        route_env["AGENT_OFFICE_POOL_REASON"] = str(plan.get("reason") or "")
    # 上一轮失败留下的断点报告：这一次（重试或别人接手）从断点继续，而不是从零开始。
    handoff = read_handoff_report(evidence / f"{task['task_id']}.handoff.json")
    goal = build_goal(spec, harness_id=harness_id, review=review, handoff=handoff)
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        outcome = worker.work_on(
            task_id=task["task_id"],
            claim_request_id=claim_request_id,
            # A command-line failure that happens before the CLI can run (for
            # example a Git worktree error during provisioning) used to leave
            # the task `claimed` and its claim row open forever: the worker
            # abandoned only the attempt id it had captured, and provisioning
            # raises before that value exists. Ask the platform for the real
            # attempt before cleaning up.
            on_error_attempt=lambda attempt: captured.update({"attempt_id": attempt})
            if attempt
            else None,
            on_claimed=provision,
            timeout_seconds=resolve_timeout_seconds(timeout_seconds),
            goal=goal,
            # The worker decides what a failure means: a dead provider should put
            # the task back in the queue, not end it.
            abandon_on_failure=False,
            env=route_env or None,
        )
    except HarnessTimeout as exc:
        # R10-fix-15: a deadline kill keeps the attempt and its worktree. Within
        # the lease a rerun with the same claim_request_id replays the claim and
        # continues in the same workspace; after the lease the existing
        # stale/reclaiming sweep takes over. Partial work is still never submitted.
        failure = {
            "task_id": task["task_id"],
            "harness_id": harness_id,
            "error_type": "HarnessTimeout",
            "error": str(exc),
            "timeout_seconds": resolve_timeout_seconds(timeout_seconds),
            "duration_seconds": exc.duration_seconds,
            "stdout_tail": exc.stdout,
            "stderr_tail": exc.stderr,
            "workspace_recycle": {"status": "preserved"},
            "resumable": {
                "claim_request_id": claim_request_id,
                "attempt_id": captured.get("attempt_id"),
                "worktree": captured.get("worktree_path"),
                "lease_until": captured.get("lease_until"),
            },
            "scope": "real local CLI timeout; attempt and workspace kept for replay within the lease",
        }
        (evidence / f"{task['task_id']}.failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    except Exception as exc:
        recycled: dict[str, object] = {"status": "not_attempted"}
        attempt_id = captured.get("attempt_id")
        retryable, reason = _classify_failure(
            exc, task=task, manager=manager, attempt_id=attempt_id
        )
        if attempt_id:
            if retryable:
                # 可重试的失败：把任务**放回队列**（``RELEASE`` 边：in_progress →
                # ready；``task:release`` 本来就发给 harness），而不是 abandon 成
                # 终态。现场不回收：清扫器会按规范把旧 worktree 归档留档
                # （data/archives/<attempt_id>/），接手方拿到的是**断点报告**。
                try:
                    adapter.release(
                        task_id=task["task_id"],
                        reason=f"retryable failure ({type(exc).__name__}): {reason}",
                    )
                    recycled = {"status": "preserved_for_handoff"}
                except Exception as release_error:
                    # 放不回队列就只能结束它：留着一条没人能领的活更糟。
                    recycled = {"status": "release_failed", "error": type(release_error).__name__}
                    with contextlib.suppress(Exception):
                        adapter.abandon(
                            task_id=task["task_id"],
                            reason=f"formal worker error: {type(exc).__name__}",
                        )
                    try:
                        manager.archive_and_recycle(attempt_id=str(attempt_id))
                    except Exception as cleanup_error:  # pragma: no cover - evidence path
                        recycled["recycle_error"] = type(cleanup_error).__name__
            else:
                release_or_abandon(
                    adapter,
                    task["task_id"],
                    f"formal worker error: {type(exc).__name__}",
                )
                try:
                    manager.archive_and_recycle(attempt_id=str(attempt_id))
                    recycled = {"status": "archived"}
                except Exception as cleanup_error:  # pragma: no cover - defensive evidence path
                    recycled = {"status": "failed", "error": type(cleanup_error).__name__}
        failure = {
            "task_id": task["task_id"],
            "harness_id": harness_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "workspace_recycle": recycled,
            "retryable": retryable,
            "retry_reason": reason,
            "scope": (
                "real local CLI failure; the task went back to the queue and the"
                " breakpoint report is attached to the next attempt"
                if retryable
                else "real local CLI failure; attempt was abandoned by the worker"
            ),
        }
        if isinstance(exc, CliFailed):
            failure.update(
                {
                    "cli_returncode": exc.result.returncode,
                    "argv": exc.result.argv,
                    "stdout": exc.result.stdout,
                    "stderr": exc.result.stderr,
                    "duration_seconds": exc.result.duration_seconds,
                }
            )
        (evidence / f"{task['task_id']}.failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if retryable:
            # 断点报告：接手方（或下一次重试）要知道的全部事实。
            write_handoff_report(
                evidence / f"{task['task_id']}.handoff.json",
                task=task,
                harness_id=harness_id,
                failure=failure,
                worktree=captured.get("worktree_path"),
                manager=manager,
                attempt_id=attempt_id,
            )
        raise
    (evidence / f"{task['task_id']}.json").write_text(
        json.dumps(
            {
                "task_id": outcome.task_id,
                "attempt_id": outcome.attempt_id,
                "commit": outcome.commit,
                "branch": outcome.branch,
                "changed_files": outcome.changed_files,
                "cli_returncode": outcome.cli.returncode,
                "duration_seconds": outcome.cli.duration_seconds,
                "publish": outcome.publish,
                "submit": outcome.submit,
                "scope": f"real local {harness_id} execution; structural verification; human acceptance pending",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    policy = VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                "formal-artifact": {
                    "command": [
                        "python",
                        str(Path(__file__).with_name("verify_artifact.py")),
                        json.dumps(spec["exclusive_paths"]),
                    ],
                    "timeout_seconds": 30,
                }
            },
        }
    )
    verification = VerifyWorker(
        database,
        repo,
        policy,
        overlay_root=settings.data_dir / "formal-overlays",
        evidence_root=settings.data_dir / "formal-evidence",
    ).run(
        task_id=outcome.task_id,
        attempt_id=outcome.attempt_id,
        commit=outcome.commit,
        base_commit=captured.get("base_commit"),
        verify_profile="formal-artifact",
        fencing_epoch=captured["fencing_epoch"],
    )
    print(
        json.dumps(
            {
                "task_id": task["task_id"],
                "commit": outcome.commit,
                "verify": verification.as_json(),
                "human_acceptance": "pending",
            }
        ),
        flush=True,
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8848")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument(
        "--harness-id",
        required=True,
        help="registered base ID or explicit window ID such as codex-member-1",
    )
    parser.add_argument("--group", required=True)
    parser.add_argument("--canonical-repo", type=Path)
    parser.add_argument("--worktree-root", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--task-id",
        default=None,
        help=(
            "recover one specific task instead of polling the ready list; "
            "requires --attempt-no and implies --once"
        ),
    )
    parser.add_argument(
        "--attempt-no",
        type=int,
        default=None,
        help="attempt number whose claim is replayed by --task-id (e.g. 1 for .a1)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="member CLI deadline for one attempt (default: AP_HARNESS_TIMEOUT_SECONDS, else 3600)",
    )
    args = parser.parse_args()
    base_harness_id(args.harness_id)
    if args.task_id is not None and args.attempt_no is None:
        raise SystemExit("--task-id requires --attempt-no (the claim to replay)")
    once = args.once or args.task_id is not None
    if settings.auth_mode != "enforce":
        raise SystemExit("formal worker requires enforce authentication")
    if args.canonical_repo is not None:
        settings.canonical_repo = args.canonical_repo
    if args.worktree_root is not None:
        settings.worktree_root = args.worktree_root
    database, repo = Database(), CanonicalRepo.from_settings()
    repo.ensure()
    last_no_ready_report = 0.0
    # 自报心跳：窗口定期告诉平台"我还在、我是什么适配器"。这条 probe 只写
    # capability_probes（自报能力 vs 登记表、host_mode、是否降级），**不改任务状态、
    # 不派单、不领取**。探测失败也不能拖垮 worker：探针只是观察面。
    probe_every_seconds = 300.0
    last_probe_at = 0.0
    # 传阅审阅：不用跑得那么勤（有草案时它会等），但也别睡太久。
    review_every_seconds = 30.0
    last_review_at = 0.0
    # 分配治理（§39 硬规则 2）：成员要**申请**，被选中后自己**接受**。
    # 这两步都要及时：管理员开轮后会等申请，宽限期一过就冻结候选集合。
    allocation_every_seconds = 10.0
    last_allocation_at = 0.0
    worker_token = args.token_file.read_text().strip()
    with httpx.Client(
        base_url=args.url,
        headers={"Authorization": "Bearer " + worker_token},
        timeout=30,
    ) as client:
        while True:
            try:
                now = time.monotonic()
                # 传阅：平台有草案在等这个窗口的意见，就先给意见（不领活）。
                if now - last_review_at >= review_every_seconds:
                    last_review_at = now
                    try:
                        submit_circulation_review(
                            client,
                            adapter_for_harness(client, args.harness_id),
                            harness_id=args.harness_id,
                            group=args.group,
                        )
                    except Exception as exc:
                        # A review that cannot be delivered must be visible:
                        # the round would otherwise wait for ever.
                        print(
                            json.dumps({"circulation_review_error": type(exc).__name__}),
                            flush=True,
                        )
                if now - last_probe_at >= probe_every_seconds:
                    last_probe_at = now
                    # A probe that fails is not the run's failure: the probe
                    # only refreshes what the window can do.
                    with contextlib.suppress(Exception):
                        adapter_for_harness(client, args.harness_id).probe()
                if now - last_allocation_at >= allocation_every_seconds:
                    last_allocation_at = now
                    # 分配治理的两步都是**本人的**动作：先申请（把自己放进候选
                    # 集合），被选中后自己接受（这才构成 Claim 的授权）。
                    with contextlib.suppress(Exception):
                        submit_allocation_applications(
                            client, harness_id=args.harness_id, group=args.group
                        )
                    with contextlib.suppress(Exception):
                        accept_allocation(
                            client, harness_id=args.harness_id, group=args.group
                        )
                completed = run_one(
                    client,
                    database,
                    repo,
                    harness_id=args.harness_id,
                    group=args.group,
                    evidence=settings.data_dir / "formal-runs",
                    timeout_seconds=args.timeout_seconds,
                    task_id=args.task_id,
                    attempt_no=args.attempt_no,
                    platform_url=args.url,
                    token=worker_token,
                )
                if not completed:
                    now = time.monotonic()
                    if once or now - last_no_ready_report >= 30:
                        print(
                            json.dumps(
                                {
                                    "status": "no_ready_task",
                                    "harness_id": args.harness_id,
                                    "group": args.group,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                        last_no_ready_report = now
                else:
                    last_no_ready_report = 0.0
            except Exception as exc:
                # Never log bodies/tokens. State is still whatever the API says.
                print(json.dumps({"worker_error": type(exc).__name__}), flush=True)
                if once:
                    raise
            if once:
                return
            time.sleep(3)


if __name__ == "__main__":
    main()
