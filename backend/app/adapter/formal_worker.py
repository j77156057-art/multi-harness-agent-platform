"""Local operator-run pull worker; no browser can impersonate a Harness."""

from __future__ import annotations

import argparse
import json
import time
from fnmatch import fnmatchcase
from pathlib import Path

import httpx

from app.adapter.harnesses.cli import CliHarnessWorker, CliRunResult, SubprocessRunner
from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
from app.adapter.harnesses.codex import CodexAdapter
from app.adapter.harnesses.opencode import OpenCodeAdapter
from app.config import settings
from app.db.connection import Database
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeManager
from app.tasks.service import SchedulerService
from app.verify.profile import VerifyPolicy
from app.verify.worker import VerifyWorker


ADAPTER_TYPES = {
    "codex": CodexAdapter,
    "claude-code": ClaudeCodeAdapter,
    "opencode": OpenCodeAdapter,
}


def adapter_for_harness(client, harness_id: str):
    """Build the real CLI adapter selected by the registered Harness identity.

    A formal worker is launched once per Harness token. Selecting the adapter
    from that explicit identity prevents a Claude/OpenCode token from silently
    running the Codex command, which would make the platform receipt lie about
    the executable that produced it.
    """
    adapter_type = ADAPTER_TYPES.get(harness_id)
    if adapter_type is None:
        raise ValueError(f"unsupported formal Harness: {harness_id}")
    return adapter_type(client)


class ScopedRunner(SubprocessRunner):
    def __init__(self, spec: dict, base: str):
        super().__init__()
        self.spec, self.base = spec, base

    def run(self, argv, *, cwd, **kwargs):
        result = super().run(argv, cwd=cwd, **kwargs)
        if result.returncode:
            return result
        changed = run_git("diff", "--name-only", f"{self.base}..HEAD", cwd=cwd).stdout.splitlines()
        paths = self.spec["exclusive_paths"]
        dirty = run_git("status", "--porcelain", cwd=cwd).stdout.strip()
        if (
            dirty
            or not changed
            or any(not any(fnmatchcase(p, pattern) for pattern in paths) for p in changed)
        ):
            return CliRunResult(
                result.argv,
                1,
                result.stdout,
                result.stderr + "\nRefused: dirty, empty or out-of-scope result.\n",
                result.duration_seconds,
            )
        return result


def run_one(client, database, repo, *, harness_id: str, group: str, evidence: Path) -> bool:
    adapter = adapter_for_harness(client, harness_id)
    ready = adapter.list_ready_tasks(group_id=group, limit=1)["tasks"]
    if not ready:
        return False
    task = ready[0]
    spec = adapter.read_context(task_id=task["task_id"])["spec"]
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
        return Path(result["worktree_path"])

    scoped_runner = ScopedRunner(spec, service.repo_head(manager))
    worker = CliHarnessWorker(adapter, runner=scoped_runner, heartbeat_seconds=20)
    goal = (
        "Use ONLY the immutable approved task below. This workspace may contain only a seed README; "
        "there is no separate TASKS.md requirement if absent. Create a nonempty committed deliverable "
        "strictly inside exclusive_paths; do not read or modify other projects. "
        "Do not change branch, install software or push. Task specification: "
        + json.dumps(spec, ensure_ascii=False)
    )
    outcome = worker.work_on(
        task_id=task["task_id"],
        claim_request_id=f"formal-{task['task_id']}-a{int(task['attempt_no'])+1}",
        on_claimed=provision,
        timeout_seconds=600,
        goal=goal,
    )
    evidence.mkdir(parents=True, exist_ok=True)
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
                "scope": "real local Codex execution; structural verification; human acceptance pending",
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
    parser.add_argument("--harness-id", required=True, choices=sorted(ADAPTER_TYPES))
    parser.add_argument("--group", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if settings.auth_mode != "enforce":
        raise SystemExit("formal worker requires enforce authentication")
    database, repo = Database(), CanonicalRepo.from_settings()
    repo.ensure()
    with httpx.Client(
        base_url=args.url,
        headers={"Authorization": "Bearer " + args.token_file.read_text().strip()},
        timeout=30,
    ) as client:
        while True:
            try:
                run_one(
                    client,
                    database,
                    repo,
                    harness_id=args.harness_id,
                    group=args.group,
                    evidence=settings.data_dir / "formal-runs",
                )
            except Exception as exc:
                # Never log bodies/tokens. State is still whatever the API says.
                print(json.dumps({"worker_error": type(exc).__name__}), flush=True)
                if args.once:
                    raise
            if args.once:
                return
            time.sleep(3)


if __name__ == "__main__":
    main()
