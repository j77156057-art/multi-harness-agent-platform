"""``GET /harnesses/installers``: where a CLI comes from, and whether it is here.

The office answers one question for a person on a computer that has never run
this project: *what do I install, from where, and did it work*.  The tests pin
the parts that would make that answer a lie:

* every base the platform can drive has an entry (a missing one reads as
  "nothing to install", which is wrong);
* an unverified address is **empty, not invented**;
* ``ready`` means the command printed a version -- not that something was
  found on PATH;
* asking about another OS does not claim anything about this one;
* the probe reads a version line, never a credential.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.adapter import installers
from app.adapter.formal_worker import ADAPTER_TYPES
from tests.api_support import make_client


def _write_shim(directory: Path, name: str, *, body: str) -> Path:
    """A fake CLI on this platform, so the probe runs a real process."""
    if sys.platform == "win32":
        path = directory / f"{name}.cmd"
        path.write_text(f"@echo off\r\n{body}\r\n", encoding="utf-8")
    else:
        path = directory / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
    return path


@pytest.fixture
def shimmed(monkeypatch, tmp_path):
    """Put a directory first on PATH and hand back a helper to fill it."""

    def install(name: str, *, version: str = "9.9.9", exit_code: int = 0) -> Path:
        body = (
            f'echo "{version}"' if exit_code == 0 else f'echo "boom" & exit /b {exit_code}'
        ) if sys.platform == "win32" else (
            f'echo "{version}"' if exit_code == 0 else f'echo boom; exit {exit_code}'
        )
        return _write_shim(tmp_path, name, body=body)

    monkeypatch.setenv("PATH", str(tmp_path) + (";" if sys.platform == "win32" else ":") + (__import__("os").environ.get("PATH") or ""))
    return install


def test_every_installed_base_has_an_entry():
    """A base the platform can drive but cannot tell you how to get is a gap.

    "How to get" has two honest answers: install this CLI (a catalog entry),
    or it ships with the platform and there is nothing to install
    (``SELF_HOSTED_BASES``).  A base in neither is the gap this checks for.
    """
    listed = {entry.harness_id for entry in installers.CATALOG}

    assert set(ADAPTER_TYPES) <= listed | set(installers.SELF_HOSTED_BASES)


def test_a_self_hosted_base_names_why_there_is_nothing_to_install():
    """「不用装」也是一句要说清楚的话，不是一个空目录。"""
    for harness_id in installers.SELF_HOSTED_BASES:
        assert harness_id not in {entry.harness_id for entry in installers.CATALOG}
        assert installers.SELF_HOSTED_BASES[harness_id].strip()


def test_an_entry_names_the_command_the_adapter_launches():
    """The probe has to look for what the worker will launch, or it lies."""
    qoder = next(entry for entry in installers.CATALOG if entry.harness_id == "qoder")

    # app/adapter/harnesses/qoder.py launches qoderclicn.
    assert qoder.command == "qoderclicn"


def test_a_confirmed_entry_carries_an_official_address_and_a_command():
    """An address is the vendor's; a command is what a person pastes."""
    for entry in installers.CATALOG:
        assert entry.official_url.startswith("https://"), entry.harness_id
        assert entry.command, entry.harness_id
        for platform in entry.supported:
            if platform in entry.unsupported_arch:
                continue
            assert entry.install.get(platform), f"{entry.harness_id} has no {platform} command"


def test_a_platform_the_vendor_does_not_ship_says_so(shimmed):
    """Qoder documents no Windows arm64 build; that is not a broken install."""
    rows = installers.catalog(target_os="windows", arch="arm64")
    qoder = next(row for row in rows if row["harness_id"] == "qoder")

    assert qoder["supported"] is False
    assert qoder["status"] == "unsupported"
    assert qoder["install_command"] == ""
    assert "arm64" in qoder["detail"]


def test_probing_another_platform_claims_nothing(shimmed):
    """Asking about Linux from Windows is guidance, not a local measurement."""
    rows = installers.probe(target_os="linux")

    assert all(row["status"] == "unknown" for row in rows)
    assert all(row["path"] is None for row in rows)


def test_a_found_command_that_prints_a_version_is_ready(shimmed, monkeypatch):
    """Found + version printed = the adapter could start it."""
    monkeypatch.setattr(installers, "CATALOG", (
        installers.CliInstall(
            harness_id="probe-case",
            display_name="Probe Case",
            provider="test",
            command="probectl",
            official_url="https://example.invalid/",
            install={"windows": "echo install", "macos": "echo install", "linux": "echo install"},
        ),
    ))
    shimmed("probectl", version="1.2.3")

    row = installers.probe(harness_id="probe-case")[0]

    assert row["status"] == "ready"
    assert row["version"] == '"1.2.3"' or row["version"] == "1.2.3"
    assert row["detail"] == ""


def test_a_found_command_that_fails_is_not_ready(shimmed, monkeypatch):
    """Installed but unusable has to look different from working."""
    monkeypatch.setattr(installers, "CATALOG", (
        installers.CliInstall(
            harness_id="probe-case",
            display_name="Probe Case",
            provider="test",
            command="probectl",
            official_url="https://example.invalid/",
            install={"windows": "echo install", "macos": "echo install", "linux": "echo install"},
        ),
    ))
    shimmed("probectl", version="0.0.1", exit_code=3)

    row = installers.probe(harness_id="probe-case")[0]

    assert row["status"] == "version_failed"
    assert "probectl" in row["detail"]
    assert row["version"] is None


def test_a_missing_command_says_where_to_look(shimmed, monkeypatch):
    """'not installed' has to name the command and the PATH catch."""
    monkeypatch.setattr(installers, "CATALOG", (
        installers.CliInstall(
            harness_id="probe-case",
            display_name="Probe Case",
            provider="test",
            command="definitely-not-installed-xyz",
            official_url="https://example.invalid/",
            install={"windows": "echo install", "macos": "echo install", "linux": "echo install"},
        ),
    ))

    row = installers.probe(harness_id="probe-case")[0]

    assert row["status"] == "missing"
    assert "definitely-not-installed-xyz" in row["detail"]
    assert "PATH" in row["detail"]


def test_an_alias_counts_as_found(shimmed, monkeypatch):
    """Qoder ships under more than one name; the probe accepts the documented ones."""
    monkeypatch.setattr(installers, "CATALOG", (
        installers.CliInstall(
            harness_id="probe-case",
            display_name="Probe Case",
            provider="test",
            command="probe-primary",
            aliases=("probe-alias",),
            official_url="https://example.invalid/",
            install={"windows": "echo install", "macos": "echo install", "linux": "echo install"},
        ),
    ))
    shimmed("probe-alias", version="5.0.0")

    row = installers.probe(harness_id="probe-case")[0]

    assert row["status"] == "ready"
    assert row["found_command"] == "probe-alias"


def test_a_cli_the_adapter_finds_off_path_is_not_reported_missing(tmp_path, monkeypatch):
    """The probe must use the adapter's own binary resolution.

    Measured on the development machine: ``codex`` was not on PATH, so the
    first version of this probe called it "missing" -- while
    ``app/adapter/harnesses/codex.py`` finds it under
    ``AppData/Local/OpenAI/Codex/bin`` and the worker starts it fine.  A probe
    that is stricter than the worker sends a person to reinstall a working CLI.
    """
    elsewhere = tmp_path / "offpath"
    elsewhere.mkdir()
    executable = _write_shim(elsewhere, "codex", body='echo "9.1.0"')
    monkeypatch.setenv("AP_CODEX_BIN", str(executable))

    row = installers.probe(harness_id="codex")[0]

    assert row["status"] == "ready"
    assert row["path"] == str(executable)


def test_the_probe_ships_no_credential(tmp_path):
    """A version line is the only thing that may leave the probe."""
    client, _ = make_client(tmp_path)

    response = client.get("/api/v1/harnesses/installers")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["platform"]["os"] in installers.PLATFORMS
    assert body["probed"] is True
    assert isinstance(body["installers"], list) and body["installers"]
    text = response.text
    for marker in ("api_key", "apiKey", "token", "SECRET", "PASSWORD"):
        assert marker not in text, marker


def test_the_summary_counts_the_same_rows(tmp_path):
    """The office shows counts; they have to add up to the list it shows."""
    client, _ = make_client(tmp_path)

    body = client.get("/api/v1/harnesses/installers").json()

    total = sum(body["summary"].values())

    assert total == len(body["installers"])


def test_asking_without_probing_returns_pure_guidance(tmp_path):
    """A person may want the links without waiting for six version commands."""
    client, _ = make_client(tmp_path)

    body = client.get("/api/v1/harnesses/installers?probe_local=false").json()

    assert body["probed"] is False
    assert body["guidance_only"] is True
    assert all(row["status"] == "unknown" for row in body["installers"])
    assert all(row["official_url"].startswith("https://") for row in body["installers"])


def test_driven_is_read_from_the_adapter_table_not_written_by_hand(tmp_path):
    """「装上能不能接活」只能由适配器表回答。

    Rapired by hand it drifts: the day an adapter is added, the office would
    still say "no adapter for this one" (or worse, the reverse -- promising a
    window the worker cannot start).
    """
    client, _ = make_client(tmp_path)

    body = client.get("/api/v1/harnesses/installers?probe_local=false").json()
    driven = {row["harness_id"] for row in body["installers"] if row["driven"]}

    assert driven == set(ADAPTER_TYPES) & {row["harness_id"] for row in body["installers"]}


def test_a_catalog_row_without_an_adapter_says_it_cannot_take_tasks(monkeypatch):
    """清单不是适配器清单：没有适配器的行必须自己说「装了也接不了任务」。

    这一条刻意**不依赖今天的目录内容**（现在 16 条全都适配了，一条 reference 也
    没有）。它问的是那个标记本身：只要某个 harness_id 没有适配器，那一行就得是
    ``driven: false`` —— 否则办公室会让人去装一个装上也不会干活的东西。
    """
    monkeypatch.setattr(installers, "_DRIVEN_IDS", {"codex"})

    rows = installers.catalog(target_os="windows")
    reference = [row for row in rows if not row["driven"]]

    assert reference, [row["harness_id"] for row in rows]
    for row in reference:
        assert row["harness_id"] != "codex"
        assert row["official_url"].startswith("https://"), row["harness_id"]
        assert row["install_command"], row["harness_id"]
        assert row["verify_command"], row["harness_id"]
