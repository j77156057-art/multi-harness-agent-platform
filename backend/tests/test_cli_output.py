"""CLI 的原话在**跑的时候**就要能被读到。

在这些测试之前，child 的 stdout 只活在 worker 的临时文件里，跑完才读；失败
时才把尾巴写进 failure 证据。也就是说一次跑了六分钟的 CLI，在平台的记录里
留下了六分钟静默。

现在 worker 边跑边把 stdout/stderr 抄进 data 目录，平台用一条只读路由把尾巴
交出去，办公室才能显示"它正在说什么"。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from app.adapter import cli_output
from app.adapter.harnesses.cli import SubprocessRunner
from app.config import settings
from tests.api_support import bootstrap, make_client
from tests.claim_support import seed_task


def test_log_paths_are_named_after_the_attempt(tmp_path):
    stdout, stderr = cli_output.log_paths(tmp_path, "FD-1", "FD-1.a2")

    assert stdout == tmp_path / "formal-runs" / "FD-1.FD-1.a2.log"
    assert stderr == tmp_path / "formal-runs" / "FD-1.FD-1.a2.err.log"
    assert stdout != stderr


def test_read_tail_of_a_missing_file_is_empty_not_an_error(tmp_path):
    tail = cli_output.read_tail(tmp_path / "nope.log")

    assert tail["text"] == ""
    assert tail["bytes"] == 0
    assert tail["updated_at"] is None


def test_read_tail_keeps_the_last_lines(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("\n".join(f"line-{n}" for n in range(50)), encoding="utf-8")

    tail = cli_output.read_tail(path, max_lines=5)

    assert tail["text"] == "line-45\nline-46\nline-47\nline-48\nline-49"
    assert tail["lines"] == 50
    assert tail["bytes"] > 0
    assert tail["updated_at"] is not None


def test_the_runner_streams_the_child_output_into_the_log(tmp_path):
    """真跑一个 child：它打印的东西在它退出前就已经在日志文件里。"""
    stdout_log = tmp_path / "child.log"
    stderr_log = tmp_path / "child.err.log"

    result = SubprocessRunner().run(
        [
            sys.executable,
            "-c",
            "import sys; print('hello-from-the-cli'); print('on-stderr', file=sys.stderr)",
        ],
        cwd=tmp_path,
        timeout_seconds=60,
        log_path=stdout_log,
        err_log_path=stderr_log,
    )

    assert result.returncode == 0
    assert "hello-from-the-cli" in result.stdout
    # 同样的字节在日志文件里：读的是 child's 原话，不是我们转述的摘要。
    assert "hello-from-the-cli" in stdout_log.read_text(encoding="utf-8")
    assert "on-stderr" in stderr_log.read_text(encoding="utf-8")


def test_the_runner_works_without_a_log_path(tmp_path):
    result = SubprocessRunner().run(
        [sys.executable, "-c", "print('quiet')"],
        cwd=tmp_path,
        timeout_seconds=60,
    )

    assert result.returncode == 0
    assert "quiet" in result.stdout


def _seed_attempt(database, task_id: str = "T-1", attempt_id: str = "T-1.a1") -> None:
    now = datetime.now(UTC).isoformat()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
            " fencing_epoch, base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES (?, ?, 1, 'claude-code', 1, 'base000', 1, NULL, 'in_progress', ?)",
            (attempt_id, task_id, now),
        )
        conn.execute(
            "UPDATE tasks SET current_attempt_id = ?, owner = 'claude-code' WHERE task_id = ?",
            (attempt_id, task_id),
        )


def test_the_read_route_hands_out_the_tail(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bootstrap(client, project_id="P-1", group_id="G-1")
    seed_task(database, task_id="T-1", group_id="G-1")
    _seed_attempt(database)
    stdout_log, stderr_log = cli_output.log_paths(tmp_path, "T-1", "T-1.a1")
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stdout_log.write_text("first line\nsecond line\n", encoding="utf-8")
    stderr_log.write_text("a warning\n", encoding="utf-8")

    response = client.get("/api/v1/tasks/T-1/output")

    assert response.status_code == 200, response.text
    output = response.json()["output"]
    assert output["stdout"] == "first line\nsecond line"
    assert output["stderr"] == "a warning"
    assert output["attempt_id"] == "T-1.a1"
    assert output["attempt_status"] == "in_progress"
    assert output["harness_id"] == "claude-code"
    assert output["stdout_bytes"] > 0


def test_the_read_route_is_honest_about_a_missing_output(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bootstrap(client, project_id="P-1", group_id="G-1")
    seed_task(database, task_id="T-1", group_id="G-1")
    _seed_attempt(database)

    response = client.get("/api/v1/tasks/T-1/output")

    assert response.status_code == 200, response.text
    output = response.json()["output"]
    assert output["stdout"] == ""
    assert output["stdout_bytes"] == 0


def test_the_read_route_without_an_attempt_is_empty(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bootstrap(client, project_id="P-1", group_id="G-1")
    seed_task(database, task_id="T-1", group_id="G-1")

    response = client.get("/api/v1/tasks/T-1/output")

    assert response.status_code == 200, response.text
    assert response.json()["output"] is None


def test_the_read_route_falls_back_to_the_last_attempt(tmp_path, monkeypatch):
    """任务已经没有当前 attempt（abandoned/merged）时，仍要看得到那段 transcript。

    人想看的是"这个 harness 临死前说了什么"——那段话还在磁盘上，不该因为
    任务进入了终态就消失。
    """
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bootstrap(client, project_id="P-1", group_id="G-1")
    seed_task(database, task_id="T-1", group_id="G-1")
    _seed_attempt(database)
    stdout_log, _ = cli_output.log_paths(tmp_path, "T-1", "T-1.a1")
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stdout_log.write_text("the last words\n", encoding="utf-8")
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET current_attempt_id = NULL, status = 'abandoned' WHERE task_id = 'T-1'"
        )

    response = client.get("/api/v1/tasks/T-1/output")

    assert response.status_code == 200, response.text
    output = response.json()["output"]
    assert output["stdout"] == "the last words"
    assert output["attempt_id"] == "T-1.a1"
    assert output["current"] is False


def test_the_read_route_can_serve_one_named_attempt(tmp_path, monkeypatch):
    """失败那一轮的原话，要能**单独**取出来。

    一条活失败后重跑，"当前 attempt"的输出是重跑那一轮的——想看失败现场就得
    点名要哪一次 attempt，否则人看到的永远是"现在正在跑的那个"。
    """
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bootstrap(client, project_id="P-1", group_id="G-1")
    seed_task(database, task_id="T-1", group_id="G-1")
    now = datetime.now(UTC).isoformat()
    with database.transaction() as conn:
        for attempt_id, attempt_no in (("T-1.a1", 1), ("T-1.a2", 2)):
            conn.execute(
                "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
                " fencing_epoch, base_commit, base_locked_seq, head_commit, status, created_at)"
                " VALUES (?, 'T-1', ?, 'claude-code', 1, 'base000', 1, NULL, 'in_progress', ?)",
                (attempt_id, attempt_no, now),
            )
        conn.execute(
            "UPDATE tasks SET current_attempt_id = 'T-1.a2', owner = 'claude-code'"
            " WHERE task_id = 'T-1'"
        )
    for attempt_id, text in (("T-1.a1", "the failed words\n"), ("T-1.a2", "the current words\n")):
        stdout_log, _ = cli_output.log_paths(tmp_path, "T-1", attempt_id)
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        stdout_log.write_text(text, encoding="utf-8")

    current = client.get("/api/v1/tasks/T-1/output").json()["output"]
    assert current["attempt_id"] == "T-1.a2"
    assert current["current"] is True

    failed = client.get("/api/v1/tasks/T-1/output?attempt_id=T-1.a1").json()["output"]
    assert failed["attempt_id"] == "T-1.a1"
    assert failed["current"] is False
    assert failed["stdout"] == "the failed words"


def test_the_read_route_refuses_an_attempt_of_another_task(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    bootstrap(client, project_id="P-1", group_id="G-1")
    seed_task(database, task_id="T-1", group_id="G-1")
    seed_task(database, task_id="T-2", group_id="G-1")
    _seed_attempt(database, task_id="T-2", attempt_id="T-2.a1")

    response = client.get("/api/v1/tasks/T-1/output?attempt_id=T-2.a1")

    assert response.status_code == 200, response.text
    assert response.json()["output"] is None
