"""启动前置条件：缺 Git Bash 的 CodeBuddy worker 必须启动即失败、不进池。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.adapter.prerequisites import (
    CODEBUDDY_GIT_BASH,
    check_harness_prerequisites,
    codebuddy_git_bash_failure,
)

BACKEND = Path(__file__).resolve().parents[1]


def test_codebuddy_without_declared_git_bash_is_a_prerequisite_failure():
    failure = codebuddy_git_bash_failure(env={}, probe=lambda path: None)
    assert failure is not None
    assert failure.code == CODEBUDDY_GIT_BASH
    assert "Bash" in failure.detail


def test_codebuddy_declared_path_must_exist(tmp_path: Path):
    failure = codebuddy_git_bash_failure(
        env={"CODEBUDDY_CODE_GIT_BASH_PATH": str(tmp_path / "missing-bash.exe")},
        probe=lambda path: None,
    )
    assert failure is not None
    assert failure.code == CODEBUDDY_GIT_BASH
    assert "not a file" in failure.detail


def test_codebuddy_declared_path_must_run(tmp_path: Path):
    declared = tmp_path / "bash.exe"
    declared.write_text("not really bash", encoding="utf-8")
    failure = codebuddy_git_bash_failure(
        env={"CODEBUDDY_CODE_GIT_BASH_PATH": str(declared)},
        probe=lambda path: f"{path} exited 127",
    )
    assert failure is not None
    assert "exited 127" in failure.detail


def test_codebuddy_with_working_git_bash_passes(tmp_path: Path):
    declared = tmp_path / "bash.exe"
    declared.write_text("ok", encoding="utf-8")
    assert (
        codebuddy_git_bash_failure(
            env={"CODEBUDDY_CODE_GIT_BASH_PATH": str(declared)}, probe=lambda path: None
        )
        is None
    )


def test_other_harnesses_have_no_codebuddy_prerequisite():
    assert check_harness_prerequisites("qoder", env={}) == []


def test_worker_refuses_to_start_and_never_joins_the_pool(tmp_path: Path):
    """真实进程级验证：缺失变量时 worker 立刻退出，并给出可检索的原因码。"""
    token = tmp_path / "codebuddy.key"
    token.write_text("test-token", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()}
    env.pop("CODEBUDDY_CODE_GIT_BASH_PATH", None)
    env["AP_AUTH_MODE"] = "enforce"
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.adapter.formal_worker",
            "--url",
            "http://127.0.0.1:1",
            "--token-file",
            str(token),
            "--harness-id",
            "codebuddy",
            "--group",
            "g-prerequisite-test",
        ],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["status"] == "worker_prerequisite_missing"
    assert payload["reason"] == CODEBUDDY_GIT_BASH
