"""``GET /adapter/availability``: who can work right now, and with which model.

The office asks one human question after every failure -- "the upstream died,
who takes over?" -- and the platform has to answer it from facts, not from a
guess:

* the **model** is a deployment fact: the CLI reads its own config, and the
  platform holds no model credentials, so it reads the same config file and
  reports what it says (never a guess);
* a window **without a platform credential** cannot claim a task, so it is not
  a candidate;
* a **provider failure** says the window cannot work now -- but a platform-side
  failure (a Git error, a locked database) is the platform's problem and must
  not black-list the window;
* a window that **delivered something after** its last failure is working
  again;
* a window whose **process is not running** cannot work either -- the registry
  says "online" from the moment of registration and never changes, so the
  presence heartbeat is the only honest source for "is it running right now"
  (2026-10-29: the registry listed 13 windows while only 7 workers ran).

The route is read-only: it writes no state and grants nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.adapter.models import configured_model
from app.adapter.presence import record_presence
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth.tokens import TokenService
from app.events.store import EventStore
from tests.api_support import make_client
from tests.claim_support import seed_project, seed_task


def _register(database, harness_id: str, *, status: str = "online") -> None:
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=harness_id,
            name=harness_id,
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
            status=status,
        )
    )


def _present(database, harness_id: str) -> None:
    """这个窗口的进程正在跑（worker 的心跳）。"""
    record_presence(database, harness_id, role="member")


def _availability(client) -> dict[str, dict[str, object]]:
    response = client.get("/api/v1/adapter/availability")
    assert response.status_code == 200, response.text
    return {str(item["harness_id"]): item for item in response.json()["availability"]}


def test_model_is_read_from_the_cli_config_not_guessed(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"model": "some-model"}), encoding="utf-8"
    )
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "codex-model"\n', encoding="utf-8")
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(
        json.dumps({"model": "opencode/free-model"}), encoding="utf-8"
    )

    assert configured_model("claude-code", home=home)["model"] == "some-model"
    assert configured_model("codex", home=home)["model"] == "codex-model"
    assert configured_model("opencode", home=home)["model"] == "opencode/free-model"
    # A window of a known CLI reads that CLI's configuration.
    assert configured_model("codex-member-1", home=home)["model"] == "codex-model"
    # A CLI whose config cannot be read reports "unknown" -- never a guess.
    assert configured_model("qoder", home=home)["model"] is None
    assert configured_model("nobody", home=home)["model"] is None


def test_the_claude_alias_is_resolved_to_the_model_it_means(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "model": "fable",
                "env": {
                    "ANTHROPIC_BASE_URL": "https://example.invalid/anthropic",
                    "ANTHROPIC_DEFAULT_FABLE_MODEL": "deepseek-v4-flash[1M]",
                    "ANTHROPIC_MODEL": "deepseek-v4-flash[1M]",
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = configured_model("claude-code", home=home)
    # The alias is what the settings name; the model is what the provider runs.
    assert resolved["model"] == "deepseek-v4-flash[1M]"
    assert resolved["model_alias"] == "fable"


def test_a_window_without_a_credential_is_not_a_candidate(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": f"model-of-{harness_id}", "source": "test"},
    )
    _register(database, "h-ready")
    _register(database, "h-no-credential")
    TokenService(database).issue(subject_type="harness", subject_id="h-ready", label="primary")
    _present(database, "h-ready")

    entries = _availability(client)
    assert entries["h-ready"]["usable"] is True
    assert entries["h-ready"]["model"] == "model-of-h-ready"
    assert entries["h-ready"]["reasons"] == []
    assert entries["h-no-credential"]["usable"] is False
    assert "no_credential" in [r["code"] for r in entries["h-no-credential"]["reasons"]]


def test_a_window_whose_process_is_not_running_is_not_a_candidate(tmp_path, monkeypatch):
    """登记在册 ≠ 进程在跑。

    2026-10-29 的真实演练：注册表上 13 个窗口全是「在线」（那是注册那一刻
    写下的声明），实际只有 7 个 worker 在跑。界面上写「13 在线」是假数字。
    所以「在线」只认在场：worker 定期报一声「我还在」，没报就算没在跑。
    """
    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": f"model-of-{harness_id}", "source": "test"},
    )
    _register(database, "h-alive")
    _register(database, "h-gone")
    for harness_id in ("h-alive", "h-gone"):
        TokenService(database).issue(subject_type="harness", subject_id=harness_id, label="p")
    _present(database, "h-alive")

    entries = _availability(client)

    alive = entries["h-alive"]
    assert alive["live"] is True
    assert alive["status"] == "online"
    assert alive["usable"] is True
    assert alive["last_seen_at"]

    gone = entries["h-gone"]
    assert gone["live"] is False
    assert gone["status"] == "offline"
    # 注册表里它仍然写着 online —— 那是登记面，不能拿来当「在线」。
    assert gone["registered_status"] == "online"
    assert gone["usable"] is False
    assert "worker_not_running" in [r["code"] for r in gone["reasons"]]


def test_the_registry_status_does_not_outlive_the_process(tmp_path, monkeypatch):
    """进程停了很久之后，「在线」要自己掉下来。

    没有这条，界面会在人停了 worker 之后一直显示「在线」—— 而那正是这次
    要修的那个假数字。
    """
    from app.adapter.presence import presence_snapshot

    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": None, "source": "test"},
    )
    _register(database, "h-stopped")
    TokenService(database).issue(subject_type="harness", subject_id="h-stopped", label="p")
    _present(database, "h-stopped")

    # 心跳停在一个很久以前的时间点（模拟进程被杀掉后再没报过）。
    long_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with database.transaction() as conn:
        conn.execute(
            "UPDATE harness_presence SET seen_at = ?, started_at = ? WHERE harness_id = ?",
            (long_ago, long_ago, "h-stopped"),
        )

    assert presence_snapshot(database)["h-stopped"]["live"] is False
    entry = _availability(client)["h-stopped"]
    assert entry["live"] is False
    assert entry["status"] == "offline"


def test_an_offline_window_is_not_a_candidate(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": None, "source": "test"},
    )
    _register(database, "h-offline", status="offline")
    TokenService(database).issue(subject_type="harness", subject_id="h-offline", label="primary")

    entries = _availability(client)
    assert entries["h-offline"]["usable"] is False
    assert "offline" in [r["code"] for r in entries["h-offline"]["reasons"]]


def _attempt(database, *, task_id: str, owner: str, status: str = "abandoned") -> None:
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
            " fencing_epoch, base_commit, base_locked_seq, head_commit, status, created_at,"
            " registration_version) VALUES (?, ?, 1, ?, 1, 'base', 0, NULL, ?, ?, 'reg-1')",
            (f"att-{task_id}", task_id, owner, status, datetime.now(UTC).isoformat()),
        )


def _evidence(tmp_path, task_id: str, payload: dict[str, object]) -> None:
    evidence = tmp_path / "formal-runs"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"{task_id}.failure.json").write_text(json.dumps(payload), encoding="utf-8")


def test_a_provider_failure_marks_the_window_unusable(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": None, "source": "test"},
    )
    _register(database, "h-fail")
    TokenService(database).issue(subject_type="harness", subject_id="h-fail", label="primary")
    _present(database, "h-fail")
    seed_project(database)
    seed_task(database, task_id="T-1")
    _attempt(database, task_id="T-1", owner="h-fail")
    _evidence(
        tmp_path,
        "T-1",
        {
            "error_type": "CliFailed",
            "error": "the harness CLI failed before it could deliver",
            "retryable": True,
            "retry_reason": "the harness CLI failed before it could deliver",
        },
    )

    entries = _availability(client)
    assert entries["h-fail"]["usable"] is False
    assert any("CliFailed" in r["detail"] for r in entries["h-fail"]["reasons"])


def test_a_platform_side_failure_does_not_black_list_the_window(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": None, "source": "test"},
    )
    _register(database, "h-git")
    TokenService(database).issue(subject_type="harness", subject_id="h-git", label="primary")
    _present(database, "h-git")
    seed_project(database)
    seed_task(database, task_id="T-1")
    _attempt(database, task_id="T-1", owner="h-git")
    _evidence(
        tmp_path,
        "T-1",
        {
            "error_type": "GitError",
            "error": "git fetch failed",
            "retryable": False,
            "retry_reason": "",
        },
    )

    entries = _availability(client)
    # The platform's own git hiccup is not the window's fault.
    assert entries["h-git"]["usable"] is True


def test_delivering_after_a_failure_makes_the_window_usable_again(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(
        "app.api.routes.configured_model",
        lambda harness_id: {"model": None, "source": "test"},
    )
    _register(database, "h-back")
    TokenService(database).issue(subject_type="harness", subject_id="h-back", label="primary")
    _present(database, "h-back")
    seed_project(database)
    seed_task(database, task_id="T-1")
    _attempt(database, task_id="T-1", owner="h-back")
    _evidence(
        tmp_path,
        "T-1",
        {
            "error_type": "CliFailed",
            "error": "provider said no",
            "retryable": True,
            "retry_reason": "provider said no",
        },
    )
    store = EventStore(database)
    store.append(
        event_name="release",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="h-back",
        payload={"reason": "retryable failure (CliFailed): provider said no"},
    )

    assert _availability(client)["h-back"]["usable"] is False

    store.append(
        event_name="submit_result",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="h-back",
        payload={"commit": "abc123"},
    )

    entry = _availability(client)["h-back"]
    assert entry["recovered"] is True
    assert entry["usable"] is True
    assert entry["reasons"] == []


def test_the_heartbeat_keeps_reporting_while_the_worker_is_busy():
    """心跳走自己的线程：干活那条线卡住时也要报「我还在」。

    2026-10-29 的真实演练：opencode 正在执行一条要跑好几分钟的活，主循环腾不出
    手来报心跳，界面就把一个**正在干活**的窗口写成「进程没在跑」—— 而它的进程
    明明活着。心跳必须与干活那条线分开。
    """
    import threading
    import time

    from app.adapter.presence import start_heartbeat

    calls: list[dict] = []
    busy = threading.Event()

    def post(body):
        busy.wait(0.2)  # 模拟主循环卡住时 HTTP 客户端还在忙
        calls.append(dict(body))

    thread = start_heartbeat(post, role="member", every_seconds=0.05)
    try:
        deadline = time.monotonic() + 5.0
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        # 线程是 daemon，主流程结束它自己会退出。
        pass
    assert len(calls) >= 2, "the heartbeat kept reporting"
    assert calls[0].get("role") == "member"
