"""OFFICE-UI-31: the assistant's deeper evidence (redacted, bounded, sourced).

Three things are pinned here:

* a line carrying a credential-looking value is masked **before** anybody
  reads it, and the count of masked lines travels with the text;
* the tail is bounded (a 5 MB transcript does not belong in a model context),
  and the size of the whole log travels with it;
* every byte comes with its provenance: which task, which attempt, which log
  file, how big, when.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.adapter import cli_output
from app.api.app import create_app
from app.assistant.tools import build_registry
from app.config import settings
from tests.git_support import make_fixture, seed_and_claim

LOOPBACK = "http://127.0.0.1"

CREDENTIAL = 'export OPENAI_API_KEY="sk-abcdefghijklmnopqrstuvwxyz012345"'


def test_a_line_with_a_credential_is_masked_and_counted():
    """The raw value must not reach the reader."""
    text = "\n".join(["running the build", CREDENTIAL, "done"])

    masked_text, masked = cli_output.redact(text)

    assert masked == 1
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in masked_text
    assert cli_output.REDACTED_LINE in masked_text
    # The rest of the transcript is untouched -- the mask is per line.
    assert "running the build" in masked_text
    assert "done" in masked_text


def test_a_clean_transcript_is_not_masked_at_all():
    text = "running the build\ndone"

    masked_text, masked = cli_output.redact(text)

    assert masked == 0
    assert masked_text == text


def test_the_tail_is_bounded_and_reports_the_whole_size(tmp_path: Path):
    """A huge transcript is cut to the bound, and says how big it was."""
    path = tmp_path / "out.log"
    path.write_text("\n".join(f"line {index}" for index in range(5000)), encoding="utf-8")

    tail = cli_output.read_tail(path, max_bytes=4096, max_lines=10)

    assert len(tail["text"].splitlines()) <= 10, "the returned text obeys the line bound"
    assert tail["bytes"] == path.stat().st_size, "the size of the whole log is reported"
    assert tail["lines"] > 10, "more was read than was returned"
    assert tail["updated_at"], "the log says when it was last written"


def test_the_face_hands_out_masked_output_with_its_provenance(tmp_path, monkeypatch):
    """Every byte comes with its provenance; the raw credential never does."""
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture, task_id="T-1")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    stdout_path, stderr_path = cli_output.log_paths(tmp_path, "T-1", str(item.attempt_id))
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(f"running the build\n{CREDENTIAL}\nall good\n", encoding="utf-8")
    stderr_path.write_text("boom\n", encoding="utf-8")

    client = TestClient(create_app(fixture.database, fixture.repo), base_url=LOOPBACK)
    response = client.get("/api/v1/tasks/T-1/forensics")

    assert response.status_code == 200, response.text
    pack = response.json()["forensics"]
    assert pack["task_id"] == "T-1"
    assert pack["attempt_id"] == item.attempt_id
    assert pack["stdout"]["redacted_lines"] == 1
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in pack["stdout"]["text"]
    assert "all good" in pack["stdout"]["text"]
    assert pack["stderr"]["text"] == "boom"
    # The provenance: which file, and how big it really is.
    assert pack["source"]["stdout_log"].endswith(f"T-1.{item.attempt_id}.log")
    assert pack["bounds"]["max_bytes"] == 256 * 1024
    assert pack["bounds"]["max_lines"] == 2000


def test_an_unknown_task_is_a_404_not_a_bag_of_bytes(tmp_path):
    """The face must not invent material for a task that does not exist."""
    fixture = make_fixture(tmp_path)
    client = TestClient(create_app(fixture.database, fixture.repo), base_url=LOOPBACK)

    response = client.get("/api/v1/tasks/NOPE/forensics")

    assert response.status_code == 404, response.text


def test_the_assistant_gets_a_read_only_tool_for_it(tmp_path, monkeypatch):
    """The tool is read-only, and it says what it took."""
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture, task_id="T-1")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    stdout_path, _ = cli_output.log_paths(tmp_path, "T-1", str(item.attempt_id))
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(f"{CREDENTIAL}\nall good\n", encoding="utf-8")

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, route, **params):
            self.calls.append(route)
            inner = TestClient(create_app(fixture.database, fixture.repo), base_url=LOOPBACK)
            return inner.get("/api/v1" + route, params=params).json()

    client = Client()
    registry = build_registry(client, group_id="g", author_id="office-assistant")
    tool = registry["task.forensics"]
    assert tool.kind == "read"

    result = tool.call({"task_id": "T-1"})

    assert result["task_id"] == "T-1"
    assert result["forensics"]["stdout"]["redacted_lines"] == 1
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in result["forensics"]["stdout"]["text"]
