"""Real harness adapters (MVP-10b, R10 §25 phase 5).

Each module binds one real CLI harness to the §7 surface: a pinned
registration (so its registration_version is traceable, R10 §6), the argv
contract for one unattended run, and a tolerant parser for the CLI's output.
The shared loop lives in :mod:`app.adapter.harnesses.cli`.
"""

from app.adapter.harnesses.claude_code import CLAUDE_CODE_REGISTRATION, ClaudeCodeAdapter
from app.adapter.harnesses.cli import (
    CliFailed,
    CliHarness,
    CliHarnessWorker,
    CliRunner,
    CliRunResult,
    CliUnavailable,
    HarnessLeaseLost,
    HarnessTimeout,
    SubprocessRunner,
    WorkOutcome,
    WorkRefused,
)
from app.adapter.harnesses.codex import CODEX_REGISTRATION, CodexAdapter
from app.adapter.harnesses.opencode import OPENCODE_REGISTRATION, OpenCodeAdapter
from app.adapter.harnesses.pi import PI_REGISTRATION, PiAdapter

__all__ = [
    "CLAUDE_CODE_REGISTRATION",
    "CODEX_REGISTRATION",
    "OPENCODE_REGISTRATION",
    "PI_REGISTRATION",
    "ClaudeCodeAdapter",
    "CliFailed",
    "CliHarness",
    "CliHarnessWorker",
    "CliRunResult",
    "CliRunner",
    "CliUnavailable",
    "CodexAdapter",
    "HarnessLeaseLost",
    "HarnessTimeout",
    "OpenCodeAdapter",
    "PiAdapter",
    "SubprocessRunner",
    "WorkOutcome",
    "WorkRefused",
]
