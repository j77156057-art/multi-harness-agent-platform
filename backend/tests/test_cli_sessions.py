"""每个窗口都能看到自己 CLI 的会话：干活有，对话也要有。

干活时的输出早就落盘（``data/formal-runs/<任务>.<尝试>.log``），界面上是
「查看成员输出」。但**对话**那一半——审阅方案、细化需求、回答问题——跑的
是同一个 CLI，却没有留下任何 transcript，于是窗口的对话里只剩平台的记录。

这一组钉的是：

* 对话类运行也落盘，文件名能看出「什么场合 / 哪个会话 / 哪个窗口」；
* 读面按窗口列出它的运行（干活的与对话的都在内），读不到就如实说没有；
* 读面给的日志与落盘的一致，且凭据照旧遮挡。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.adapter import cli_output
from app.api.app import create_app
from app.config import settings
from tests.claim_support import DEFAULT_GROUP, make_database, seed_project

SNAPSHOT = "snap-0001"
PLAN = "plan-0001"
ADMIN = "office-admin"
MEMBER = "qoder-member-1"


def test_a_conversation_run_gets_a_readable_name():
    """名字要能读懂：什么场合、哪个会话、哪个窗口。"""
    stdout, stderr = cli_output.conversation_paths(
        "/data", kind="review", key="circ-1-r1-qoder-member-1", harness_id=MEMBER
    )

    assert stdout.name == "review__circ-1-r1-qoder-member-1__qoder-member-1.log"
    assert stderr.name.endswith(".err.log")

    parsed = cli_output.parse_conversation_name(stdout.name)
    assert parsed == {
        "kind": "review",
        "key": "circ-1-r1-qoder-member-1",
        "harness_id": MEMBER,
    }


def test_a_task_attempt_log_is_not_mistaken_for_a_conversation_run():
    """两种日志住在同一个目录里，靠分隔符分开 —— 不能认错。"""
    stdout, _ = cli_output.log_paths("/data", "FD-1", "FD-1.a1")

    assert cli_output.parse_conversation_name(stdout.name) is None


def test_the_runs_face_lists_the_window_own_runs(tmp_path, monkeypatch):
    """读面按窗口列出它跑过的 CLI —— 干活的与对话的都在内。"""
    database = make_database(tmp_path)
    seed_project(database)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    stdout_path, stderr_path = cli_output.conversation_paths(
        tmp_path, kind="review", key="circ-1-r1-qoder-member-1", harness_id=MEMBER
    )
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text('{"type":"text","part":{"text":"我的意见"}}\n', encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    client = TestClient(create_app(database, None), base_url="http://127.0.0.1")
    response = client.get(f"/api/v1/harnesses/{MEMBER}/runs")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["harness_id"] == MEMBER
    (run,) = payload["runs"]
    assert run["kind"] == "review"
    assert run["key"] == "circ-1-r1-qoder-member-1"
    assert "我的意见" in run["stdout"]


def test_a_window_without_runs_says_so_instead_of_inventing_one(tmp_path, monkeypatch):
    """没有就是没有 —— 不能拿平台的记录冒充它的会话。"""
    database = make_database(tmp_path)
    seed_project(database)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    client = TestClient(create_app(database, None), base_url="http://127.0.0.1")
    payload = client.get(f"/api/v1/harnesses/{MEMBER}/runs").json()

    assert payload["runs"] == []
    assert payload["note"]


def test_an_id_that_is_not_a_window_id_is_refused(tmp_path, monkeypatch):
    """窗口 id 会进文件名的通配 —— 带通配符的 id 会读到别人的记录。"""
    database = make_database(tmp_path)
    seed_project(database)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    client = TestClient(create_app(database, None), base_url="http://127.0.0.1")

    assert client.get("/api/v1/harnesses/*/runs").status_code == 404
    assert client.get("/api/v1/harnesses/qoder-member-1%2F..%2Fetc/runs").status_code == 404
