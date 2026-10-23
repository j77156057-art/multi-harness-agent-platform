"""A harness adapter built from a declaration, not from a Python class.

The platform ships five CLI adapters because five CLIs were measured by hand.
A sixth CLI should not need a sixth class: its binary, argv contract, read-only
variant and protocol are facts an operator can write down.  This module reads
those facts from ``<AP_DATA_DIR>/adapters/cli.json`` and builds the same
``CliHarness`` the built-ins build.

What is deliberately **not** automatic:

* the declaration is **trusted input** -- it names a binary and its argv, so
  the file is operator-owned and never written by the platform;
* a declaration is **refused, not guessed at** when it is incomplete (no
  binary, no tier of its own, an unknown protocol, a base that collides with a
  built-in), and the refusal says why;
* a **review** is only offered when the declaration says how to run the CLI
  read-only.  A review that can edit files is not a review.

The receipt keeps naming the real executable: ``registration.adapter`` is the
declared name (or ``<base>-cli``), never a built-in it was mistaken for.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration
from app.config import settings

#: The bases the platform already ships an adapter class for.  A declaration
#: may not take one of these names: the built-in was measured, the declaration
#: was not, and a silent override would make the receipt lie.
BUILTIN_BASES = ("codex", "claude-code", "opencode", "qoder", "codebuddy")

#: A declared base is a lowercase, hyphen-separated identifier.
BASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

#: The protocols a CLI may speak to the platform's pool.
PROTOCOLS = ("openai", "anthropic")

#: Where declarations live: next to the pool's own config, under the data
#: directory the platform was pointed at.  The CWD-relative path is the
#: historical fallback.
DEFAULT_CONFIG_PATH = Path("data") / "adapters" / "cli.json"


@dataclass(frozen=True, slots=True)
class CliDeclaration:
    """One CLI, as its operator declared it."""

    base: str
    binary: str
    args: tuple[str, ...]
    #: The question travels on stdin instead of argv (long prompts survive).
    prompt_via_stdin: bool = False
    #: The protocol the CLI speaks when it is pointed at the pool.
    pool_protocol: str = "openai"
    #: The argv for a read-only review.  Empty means "this CLI cannot review".
    review_args: tuple[str, ...] = ()
    #: The default model for ``{model}`` when the attempt is not on the pool.
    model: str = ""
    #: What the registration says this executable is.
    display_name: str = ""
    adapter: str = ""
    capabilities: tuple[str, ...] = ("coding",)


def declaration_path() -> Path:
    """Where the declarations live: the data directory first, then the CWD."""
    scoped = Path(settings.data_dir) / "adapters" / "cli.json"
    if scoped.exists():
        return scoped
    return DEFAULT_CONFIG_PATH


def _problem(index: int, message: str) -> ValueError:
    return ValueError(f"adapters/cli.json entry {index}: {message}")


def _strings(value: Any, *, field: str, index: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
        raise _problem(index, f"{field!r} must be a list of strings")
    parts = tuple(str(part) for part in value)
    if any(not part for part in parts):
        raise _problem(index, f"{field!r} must not contain empty strings")
    return parts


def _declaration(index: int, entry: dict[str, Any]) -> CliDeclaration:
    base = str(entry.get("base") or "").strip()
    if not BASE_PATTERN.fullmatch(base):
        raise _problem(index, f"base {base!r} is not a lowercase hyphen-separated name")
    if base in BUILTIN_BASES:
        raise _problem(index, f"base {base!r} is a built-in adapter and cannot be redefined")
    binary = str(entry.get("binary") or "").strip()
    if not binary:
        raise _problem(index, "binary is required")
    args = _strings(entry.get("args"), field="args", index=index)
    review_args = _strings(entry.get("review_args"), field="review_args", index=index)
    protocol = str(entry.get("pool_protocol") or "openai").strip().lower()
    if protocol not in PROTOCOLS:
        raise _problem(index, f"pool_protocol must be one of {PROTOCOLS}, got {protocol!r}")
    model = str(entry.get("model") or "").strip()
    if any("{model}" in part for part in (*args, *review_args)) and not model:
        raise _problem(index, "args use {model} but no default model is declared")
    capabilities = _strings(
        entry.get("capabilities", ["coding"]), field="capabilities", index=index
    )
    return CliDeclaration(
        base=base,
        binary=binary,
        args=args,
        prompt_via_stdin=bool(entry.get("prompt_via_stdin")),
        pool_protocol=protocol,
        review_args=review_args,
        model=model,
        display_name=str(entry.get("display_name") or "").strip(),
        adapter=str(entry.get("adapter") or "").strip(),
        capabilities=capabilities or ("coding",),
    )


def load_declarations(path: Path | None = None) -> dict[str, CliDeclaration]:
    """Read and validate the declared CLIs, keyed by base.

    A missing file is an empty registry (the built-ins still work).  A file
    that exists but cannot be understood is an error: silently ignoring half a
    registry would make a harness "unsupported" for a reason nobody can see.
    """
    source = path or declaration_path()
    if not source.exists():
        return {}
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{source}: cannot be read as JSON ({exc})") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{source}: the document must be an object")
    entries = document.get("adapters")
    if entries is None:
        return {}
    if not isinstance(entries, list):
        raise ValueError(f"{source}: 'adapters' must be a list")
    declarations: dict[str, CliDeclaration] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise _problem(index, "must be an object")
        declaration = _declaration(index, entry)
        if declaration.base in declarations:
            raise _problem(index, f"base {declaration.base!r} is declared twice")
        declarations[declaration.base] = declaration
    return declarations


def declared_bases() -> set[str]:
    """The bases a declared CLI can answer for."""
    return set(load_declarations())


class GenericCliAdapter(CliHarness):
    """The ``CliHarness`` a declaration describes."""

    def __init__(
        self,
        client: Any,
        *,
        harness_id: str,
        declaration: CliDeclaration,
        prefix: str = "/api/v1",
    ) -> None:
        registration = HarnessRegistration(
            harness_id=harness_id,
            name=declaration.display_name or f"{declaration.binary} CLI",
            provider=declaration.base,
            adapter=declaration.adapter or f"{declaration.base}-cli",
            adapter_version="1.0.0",
            capabilities=declaration.capabilities,
            registration_version="reg-001",
        )
        super().__init__(client, registration, prefix=prefix)
        self.declaration = declaration
        self.binary = declaration.binary
        self.prompt_via_stdin = declaration.prompt_via_stdin
        self.pool_protocol = declaration.pool_protocol

    def _binary(self) -> str:
        configured = self.declaration.binary
        if Path(configured).exists() or os.path.sep in configured:
            return configured
        found = shutil.which(configured) or shutil.which(f"{configured}.cmd")
        return found or configured

    def _argv(self, order: str, workdir: Path, template: Iterable[str]) -> list[str]:
        route = self.provider_route
        model = (route.model if route is not None else "") or self.declaration.model
        argv = [self._binary()]
        for part in template:
            argv.append(str(part).replace("{workdir}", str(workdir)).replace("{model}", model))
        if not self.prompt_via_stdin:
            argv.append(order)
        return argv

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return self._argv(work_order, worktree, self.declaration.args)

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """A review is only offered when the declaration says how to stay read-only."""
        if not self.declaration.review_args:
            raise ValueError(
                f"generic adapter {self.declaration.base!r} declares no review_args: "
                "a review must be a read-only invocation and the platform will not guess one"
            )
        return self._argv(prompt, workdir, self.declaration.review_args)

    def route_environment(self) -> dict[str, str]:
        """Point the CLI at the pool, in the protocol it declared."""
        route = self.provider_route
        if route is None:
            return {}
        if route.protocol == "anthropic":
            return {
                "ANTHROPIC_BASE_URL": route.base_url,
                "ANTHROPIC_AUTH_TOKEN": route.token,
                "ANTHROPIC_MODEL": route.model,
            }
        return {"OPENAI_BASE_URL": route.base_url, "OPENAI_API_KEY": route.token}

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """Read the last text/session the CLI said, tolerantly.

        The declaration does not describe an output format -- that would be a
        parser, not a declaration.  The JSON-lines shapes the measured CLIs
        use (``result``/``text``/``message``/``content``) are read; a CLI that
        prints plain text keeps its tail.
        """
        parsed: dict[str, Any] = {}
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            for key in ("session_id", "sessionId", "conversation_id"):
                if isinstance(event.get(key), str) and event[key].strip():
                    parsed.setdefault("session_id", event[key])
            message = event.get("message")
            for key in ("result", "text", "content"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    parsed["final_message"] = value
                elif isinstance(value, list):
                    for block in value:
                        if (
                            isinstance(block, dict)
                            and isinstance(block.get("text"), str)
                            and block["text"].strip()
                        ):
                            parsed["final_message"] = block["text"]
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    parsed["final_message"] = content
        if "final_message" not in parsed and stdout.strip():
            parsed["final_message"] = stdout.strip()[-4000:]
        return parsed


__all__ = [
    "BUILTIN_BASES",
    "BASE_PATTERN",
    "CliDeclaration",
    "GenericCliAdapter",
    "declaration_path",
    "declared_bases",
    "load_declarations",
]
