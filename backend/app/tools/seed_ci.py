"""Seed the fixtures the office smoke scripts need in CI.

What the scripts were skipping for, concretely:

* **没有界面** —— they exit 2 when nothing answers on the office port. The CI
  stack script starts Vite, so that reason is gone.
* **没有任务数据** —— ``requireTasks`` skips on an empty project. This tool
  creates a project, a group, a plan with titled tasks and approves it, so the
  tasks really exist and are claimable.
* **没有成员窗口** —— presence/roster screens need more than the admin window.
  Two extra harnesses are registered and report presence.
* **模型配置不可读** —— ``configured_model`` reads the CLI's own config file.
  ``--model`` writes a ``codex`` config pointing at a stub model name.

Run against an already-started backend:

    python -m app.tools.seed_ci --base http://127.0.0.1:8848 \\
        --data-dir ./data-ci --project demo-project --group demo-group \\
        --members 2 --model stub-model

Idempotent: 409 (already exists) is treated as success, so re-running after a
partial failure finishes the job instead of duplicating it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

#: 办公室的模型/凭据/改档脚本默认找窗口 ``codex``（就是本机 CLI 的登记名），
#: 所以 CI 必须真的登记它，而不是只造几个 codex-member-*。
ADMIN_HARNESS = "codex"


def _request(
    base: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = {"error": payload}
        return error.code, parsed


def _issue_token(token_type: str, subject_id: str, label: str) -> str:
    """Issue a token in-process; the CLI is the only supported issuer."""
    output = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.auth",
            "issue",
            "--type",
            token_type,
            "--id",
            subject_id,
            "--label",
            label,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in output.stdout.splitlines():
        line = line.strip()
        if '"issued"' in line:
            return str(json.loads(line)["issued"]["token"])
    raise RuntimeError(f"could not parse token output: {output.stdout}{output.stderr}")


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _plan_snapshot(
    project: str, goal: str, members: list[str], secondary_project: str | None = None
) -> dict[str, Any]:
    tasks = []
    for index, title in enumerate(("整理需求", "实现改动", "补验证"), start=1):
        # 把三条活分给不同的窗口：办公室的“成员”一栏是从任务
        # （assigned_harness_ids / owner / reviewer）推导的，全空就一个成员都没有。
        owner = members[(index - 1) % len(members)] if members else ""
        # 第二条挂到第二个项目前缀下：项目隔离脚本需要同一分组里两个项目名。
        prefix = secondary_project if (index == 2 and secondary_project) else project
        tasks.append(
            {
                "id": f"FD-{index}",
                # Lobby groups tasks by the "项目名 · " prefix, so carry it.
                "title": f"{prefix} · {title}",
                "goal": f"{goal}（第 {index} 步：{title}）",
                "acceptance": [
                    {
                        "id": f"ACC-{index}",
                        "check": "human",
                        "description": "由人最终审核",
                    }
                ],
                "capabilities": ["coding"],
                "exclusive_paths": [],
                "assigned_harness_ids": [owner] if owner else [],
                "require_human_assign": bool(owner),
                "risk": "medium",
                "expected_duration_min": 30,
                "budget_tokens": 0,
            }
        )
    return {
        "plan_version": "v1",
        "title": f"{project} · 示例方案",
        "goal": goal,
        "tasks": tasks,
        "registered_capabilities": ["coding"],
    }


def _write_model_config(model: str) -> str:
    """Point the ``codex`` CLI config at a stub model name.

    ``app.adapter.models`` reads ``~/.codex/config.toml`` for every
    ``codex-*`` window, so this is what makes ``/api/formal/status`` report a
    model instead of "unknown". Returns the path written.
    """
    import os

    home = Path(os.environ.get("AP_MODEL_HOME") or Path.home())
    path = home / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    # Replace the **top-level** model only. Appending blindly would land inside
    # whatever table happens to be last (``[memories]`` in a real profile).
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("model") and line.strip().split("=")[0].strip() == "model":
            lines[index] = f'model = "{model}"'
            replaced = True
            break
    if not replaced:
        lines.insert(0, f'model = "{model}"')
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return str(path)


def _seed_opencode_config() -> None:
    """An opencode provider config so the pool discovers an opencode upstream.

    Upstream discovery reads ``Path.home()/.config/opencode/opencode.json``;
    ci-stack isolates HOME, so without this the opencode group never appears
    and the tier/review-gate smokes wait for a group that cannot exist.
    """
    import os

    home = Path(os.environ.get("AP_MODEL_HOME") or Path.home())
    path = home / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(
        path,
        json.dumps(
            {
                "provider": {
                    "1": {
                        "options": {"baseURL": "http://127.0.0.1:8402/v1"},
                        "models": {"minimax-m3.1-flash-free": {}},
                    }
                }
            }
        ),
    )


def _seed_opencode_store() -> None:
    """Empty but readable opencode store, so the built-in CLI slot is offered.

    The pool refuses slots whose store it cannot read; CI runners have no
    opencode installation at all. Schema mirrors the CLI's own table.
    """
    import os
    import sqlite3

    home = os.environ.get("XDG_DATA_HOME")
    if not home:
        return
    path = Path(home) / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS session_message(id TEXT PRIMARY KEY, session_id TEXT,"
            " type TEXT, seq INTEGER, time_created INTEGER, time_updated INTEGER, data TEXT)"
        )
        connection.commit()
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.tools.seed_ci")
    parser.add_argument("--base", default="http://127.0.0.1:8848")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--project", default="demo-project")
    parser.add_argument("--group", default="demo-group")
    parser.add_argument("--members", type=int, default=2)
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")
    data_dir = Path(args.data_dir)
    _seed_opencode_store()
    _seed_opencode_config()

    operator_token = _issue_token("human", "operator", "ci operator")
    _write(data_dir / "office-operator.key", operator_token)
    status, whoami = _request(base, "GET", "/api/v1/auth/whoami", token=operator_token)
    if status != 200 or not whoami.get("subject"):
        raise SystemExit(f"operator token rejected: {status} {whoami}")
    operator_id = str(whoami["subject"]["id"])

    # codex/claude-code/qoder 是办公室「成员列表」脚本点名要的三个窗口；
    # 再补 N 个普通成员窗口，让「管理员 + 成员」都有得选。
    windows = [ADMIN_HARNESS, "claude-code", "qoder"] + [
        f"codex-member-{i}" for i in range(1, args.members + 1)
    ]
    for index, harness_id in enumerate(windows):
        status, payload = _request(
            base,
            "POST",
            "/api/v1/harnesses",
            {
                "registered_by": operator_id,
                "harness_id": harness_id,
                "name": f"CI window {index}",
                "provider": "openai",
                "adapter": "codex-exec",
                "adapter_version": "1.0.0",
                "capabilities": ["coding"],
                "max_parallel_tasks": 1,
                "status": "online",
                "registration_version": f"ci-{index + 1:03d}",
            },
            token=operator_token,
        )
        if status not in (200, 201, 409):
            raise SystemExit(f"register {harness_id} failed: {status} {payload}")
        token = _issue_token("harness", harness_id, f"ci {harness_id}")
        _write(data_dir / f"{harness_id}.key", token)
        status, payload = _request(
            base,
            "POST",
            "/api/v1/adapter/presence",
            {"role": "member"},
            token=token,
        )
        if status not in (200, 201):
            raise SystemExit(f"presence {harness_id} failed: {status} {payload}")

    status, payload = _request(
        base,
        "POST",
        "/api/v1/projects",
        {"project_id": args.project, "name": args.project},
        token=operator_token,
    )
    if status not in (200, 201, 409):
        raise SystemExit(f"create project failed: {status} {payload}")
    status, payload = _request(
        base,
        "POST",
        f"/api/v1/projects/{args.project}/groups",
        {"group_id": args.group, "name": args.group},
        token=operator_token,
    )
    if status not in (200, 201, 409):
        raise SystemExit(f"create group failed: {status} {payload}")

    # 一个分组只能有一个方案；第二条任务用第二个「项目名 · 」前缀，
    # 既满足项目隔离脚本，也不用再造一个分组。
    plan_id = f"plan-{args.project}"
    status, payload = _request(
        base,
        "POST",
        f"/api/v1/groups/{args.group}/plans",
        {
            "plan_id": plan_id,
            "snapshot": _plan_snapshot(
                args.project,
                "让办公室在 CI 里有真实的活可看",
                windows,
                secondary_project=f"{args.project}-b",
            ),
        },
        token=operator_token,
    )
    if status not in (200, 201, 409):
        raise SystemExit(f"create plan failed: {status} {payload}")
    status, payload = _request(
        base,
        "POST",
        f"/api/v1/plans/{plan_id}/approve",
        {"decided_by": operator_id},
        token=operator_token,
    )
    if status not in (200, 201) and not (status == 409 and "already" in str(payload).lower()):
        raise SystemExit(f"approve plan failed: {status} {payload}")

    status, tasks = _request(
        base, "GET", f"/api/v1/groups/{args.group}/tasks", token=operator_token
    )
    if status == 200:
        rows = tasks.get("tasks") or []
        print(f"seeded group={args.group} plan={plan_id} tasks={len(rows)}")
    else:
        print(f"seeded group={args.group} plan={plan_id}")

    if args.model:
        path = _write_model_config(args.model)
        print(f"model config: {path} -> {args.model}")
    print(f"operator token: {data_dir / 'office-operator.key'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
