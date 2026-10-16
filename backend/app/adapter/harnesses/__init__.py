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
from app.adapter.harnesses.codebuddy import CODEBUDDY_REGISTRATION, CodeBuddyAdapter
from app.adapter.harnesses.codex import CODEX_REGISTRATION, CodexAdapter
from app.adapter.harnesses.opencode import OPENCODE_REGISTRATION, OpenCodeAdapter
from app.adapter.harnesses.qoder import QODER_REGISTRATION, QoderAdapter

__all__ = [
    "CLAUDE_CODE_REGISTRATION",
    "CODEBUDDY_REGISTRATION",
    "CODEX_REGISTRATION",
    "OPENCODE_REGISTRATION",
    "QODER_REGISTRATION",
    "ClaudeCodeAdapter",
    "CodeBuddyAdapter",
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
    "QoderAdapter",
    "SubprocessRunner",
    "WorkOutcome",
    "WorkRefused",
]
