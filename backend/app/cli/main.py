"""CLI 入口：`python -m app.cli <command> [options]`（MVP-09）。

**只经 HTTP。** 本模块不 import `app.db`、不 import 任何 service —— 那正是
清单对 MVP-09 的硬性要求（「前端不能绕过 API 改权威字段」）。绕过 API 的写会同时
绕过 fencing 检查与 `task_events` 记录，让「投影可重建」这条不变量失真，
所以这条约束不是风格偏好。

输出一律是 JSON（每行一个对象），便于脚本与测试断言。退出码：

    0  成功
    1  API 返回错误（错误信封原样打到 stderr）
    2  用法错误（参数不对、缺必填）

`--base-url` 指向运行中的调度器，默认 `http://127.0.0.1:8848`。
`main(argv, client=...)` 允许注入客户端，测试因此不必起真服务。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8848"
API_PREFIX = "/api/v1"

EXIT_OK = 0
EXIT_API_ERROR = 1
EXIT_USAGE = 2


class CliError(RuntimeError):
    """API 返回了错误信封。带上 code 与状态码，便于脚本分支。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"{code} (HTTP {status_code}): {message}")


def _client(base_url: str) -> Any:
    """真实使用时用 httpx；测试通过注入绕开它。"""
    import httpx

    return httpx.Client(base_url=base_url, timeout=30.0)


def _unwrap(response: Any) -> Any:
    if 200 <= response.status_code < 300:
        return response.json()
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - 非 JSON 错误体也要能报出来
        body = {"error": {"code": "http.error", "message": response.text}}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {"code": "http.error", "message": str(body)}
    raise CliError(
        response.status_code,
        str(error.get("code", "http.error")),
        str(error.get("message", "")),
    )


class Commands:
    """把每个子命令映射到一个 HTTP 调用。没有别的逻辑 —— 这是刻意的。"""

    def __init__(self, client: Any) -> None:
        self.client = client

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        return _unwrap(self.client.post(API_PREFIX + path, json=payload))

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return _unwrap(self.client.get(API_PREFIX + path, params=params or {}))

    # ------------------------------------------------------------- 建群与发方案

    def project_create(self, a: argparse.Namespace) -> Any:
        return self._post("/projects", {"project_id": a.project_id, "name": a.name})

    def group_create(self, a: argparse.Namespace) -> Any:
        return self._post(f"/projects/{a.project}/groups", {"group_id": a.group_id, "name": a.name})

    def plan_create(self, a: argparse.Namespace) -> Any:
        snapshot = json.loads(a.snapshot) if a.snapshot else {"tasks": [{"id": a.task}]}
        return self._post(f"/groups/{a.group}/plans", {"plan_id": a.plan_id, "snapshot": snapshot})

    def plan_approve(self, a: argparse.Namespace) -> Any:
        return self._post(f"/plans/{a.plan_id}/approve", {"decided_by": a.by})

    def plan_reject(self, a: argparse.Namespace) -> Any:
        return self._post(f"/plans/{a.plan_id}/reject", {"decided_by": a.by, "reason": a.reason})

    # ------------------------------------------------------------- 看板与详情

    def board(self, a: argparse.Namespace) -> Any:
        params = {"status": a.status} if a.status else None
        return self._get(f"/groups/{a.group}/tasks", params)

    def task(self, a: argparse.Namespace) -> Any:
        return self._get(f"/tasks/{a.task_id}")

    def task_events(self, a: argparse.Namespace) -> Any:
        return self._get(f"/tasks/{a.task_id}/events", {"since_seq": a.since})

    def events(self, a: argparse.Namespace) -> Any:
        params: dict[str, Any] = {"since_seq": a.since}
        if a.task_id:
            params["task_id"] = a.task_id
        return self._get("/events", params)

    # ------------------------------------------------------------- 领取与提交

    def claim(self, a: argparse.Namespace) -> Any:
        payload: dict[str, Any] = {
            "claim_request_id": a.request_id,
            "harness_id": a.harness,
            "task_ids": a.task,
            "partial_allowed": a.partial,
        }
        if a.payload_hash:
            payload["payload_hash"] = a.payload_hash
        return self._post("/adapter/claim", payload)

    def heartbeat(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/adapter/heartbeat",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "lease_token": a.lease_token,
                "fencing_epoch": a.epoch,
            },
        )

    def submit(self, a: argparse.Namespace) -> Any:
        completion: dict[str, Any] = {"commit": a.commit}
        if a.status:
            completion["status"] = a.status
        if a.failure_reason:
            completion["failure_reason"] = a.failure_reason
        return self._post(
            "/adapter/submit_result",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "fencing_epoch": a.epoch,
                "completion": completion,
            },
        )

    def release(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/adapter/release",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "fencing_epoch": a.epoch,
                "reason": a.reason,
            },
        )

    def help_request(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/adapter/request_help",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "fencing_epoch": a.epoch,
                "reason": a.reason,
            },
        )

    def abandon(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/adapter/abandon",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "fencing_epoch": a.epoch,
                "reason": a.reason,
            },
        )

    def publish(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/adapter/publish_task_branch",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "branch": a.branch,
                "commit": a.commit,
                "fencing_epoch": a.epoch,
                "idempotency_key": a.idempotency_key,
            },
        )

    # ------------------------------------------------------------- 审查

    def review_claim(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/review/claim",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "reviewer_harness_id": a.reviewer,
            },
        )

    def review_submit(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/review/submit",
            {
                "task_id": a.task,
                "attempt_id": a.attempt,
                "reviewer_harness_id": a.reviewer,
                "verdict": a.verdict,
                "reviewed_commit": a.commit,
                "review_revision": a.revision,
            },
        )

    # ------------------------------------------------------------- 人类决策面

    def human_resume(self, a: argparse.Namespace) -> Any:
        payload: dict[str, Any] = {"task_id": a.task, "decided_by": a.by}
        if a.answer is not None:
            payload["answer"] = a.answer
        return self._post("/human/resume", payload)

    def human_reopen(self, a: argparse.Namespace) -> Any:
        payload: dict[str, Any] = {"task_id": a.task, "decided_by": a.by}
        if a.answer is not None:
            payload["answer"] = a.answer
        return self._post("/human/reopen", payload)

    def human_release(self, a: argparse.Namespace) -> Any:
        return self._post("/human/release", {"task_id": a.task, "decided_by": a.by})

    def human_adjudicate(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/human/adjudicate",
            {
                "task_id": a.task,
                "decided_by": a.by,
                "question": a.question,
                "options": a.option,
            },
        )

    def human_cancel(self, a: argparse.Namespace) -> Any:
        return self._post(
            "/human/cancel",
            {"task_id": a.task, "decided_by": a.by, "reason": a.reason},
        )

    # ------------------------------------------------------------- 群聊与事件

    def say(self, a: argparse.Namespace) -> Any:
        return self._post(
            f"/groups/{a.group}/messages",
            {"author_kind": a.kind, "author_id": a.author, "body": a.body},
        )

    def log(self, a: argparse.Namespace) -> Any:
        return self._get(f"/groups/{a.group}/messages", {"since_seq": a.since})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="多 Harness 协作平台操作员 CLI（MVP-09，只经 HTTP）",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="调度器地址")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text)

    p = add("project-create", "创建项目")
    p.add_argument("project_id")
    p.add_argument("--name", required=True)

    p = add("group-create", "在项目下创建群组")
    p.add_argument("group_id")
    p.add_argument("--project", required=True)
    p.add_argument("--name", required=True)

    p = add("plan-create", "发布方案（默认含单个任务）")
    p.add_argument("plan_id")
    p.add_argument("--group", required=True)
    p.add_argument("--task", default="T-1", help="单任务方案的 task id")
    p.add_argument("--snapshot", default="", help="完整 snapshot 的 JSON；给了就覆盖 --task")

    p = add("plan-approve", "批准方案，任务转入 ready")
    p.add_argument("plan_id")
    p.add_argument("--by", required=True)

    p = add("plan-reject", "驳回方案")
    p.add_argument("plan_id")
    p.add_argument("--by", required=True)
    p.add_argument("--reason", default="")

    p = add("board", "任务看板")
    p.add_argument("--group", required=True)
    p.add_argument("--status", default="")

    p = add("task", "任务详情")
    p.add_argument("task_id")

    p = add("task-events", "某任务的增量事件")
    p.add_argument("task_id")
    p.add_argument("--since", type=int, default=0)

    p = add("events", "全量增量事件")
    p.add_argument("--since", type=int, default=0)
    p.add_argument("--task-id", default="")

    p = add("claim", "领取任务")
    p.add_argument("--harness", required=True)
    p.add_argument("--task", action="append", required=True, help="可重复")
    p.add_argument("--request-id", required=True)
    p.add_argument("--partial", action="store_true")
    p.add_argument("--payload-hash", default="")

    p = add("heartbeat", "续租")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--lease-token", required=True)
    p.add_argument("--epoch", type=int, required=True)

    p = add("submit", "提交结果")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--status", default="")
    p.add_argument("--failure-reason", default="")

    p = add("release", "交还执行权")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--reason", default="")

    p = add("help", "请求协助")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--reason", required=True)

    p = add("abandon", "放弃执行权")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--reason", required=True)

    p = add("publish", "发布任务分支")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--epoch", type=int, required=True)
    p.add_argument("--idempotency-key", required=True)

    p = add("review-claim", "审查者领取审查权")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--reviewer", required=True)

    p = add("review-submit", "提交审查结论")
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--verdict", required=True, choices=["approve", "request_changes"])
    p.add_argument("--commit", required=True)
    p.add_argument("--revision", type=int, default=1)

    p = add("human-resume", "人类裁决：恢复（awaiting_human/drift → in_progress/ready）")
    p.add_argument("--task", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--answer", default=None, help="结构化答复；给了就写进决策行")

    p = add("human-reopen", "人类裁决：重开（awaiting_human/drift/abandoned → ready）")
    p.add_argument("--task", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--answer", default=None)

    p = add("human-release", "人类裁决：放行（escalated → ready）")
    p.add_argument("--task", required=True)
    p.add_argument("--by", required=True)

    p = add("human-adjudicate", "人类裁决：受理升级（escalated → awaiting_human，开决策行）")
    p.add_argument("--task", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--option", action="append", default=[], help="可重复")

    p = add("human-cancel", "人类裁决：取消任务")
    p.add_argument("--task", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", default="")

    p = add("say", "在群里发言")
    p.add_argument("--group", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--kind", default="human")
    p.add_argument("--body", required=True)

    p = add("log", "读群聊消息")
    p.add_argument("--group", required=True)
    p.add_argument("--since", type=int, default=0)

    return parser


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if client is None:
        client = _client(args.base_url)

    method = getattr(Commands(client), args.command.replace("-", "_"))
    try:
        result = method(args)
    except CliError as error:
        json.dump(
            {"error": {"code": error.code, "message": error.message}},
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return EXIT_API_ERROR

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
