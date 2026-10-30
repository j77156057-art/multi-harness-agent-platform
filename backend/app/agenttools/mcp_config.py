"""How a CLI is told about the platform's tools.

The platform exposes its tools over MCP (see :mod:`app.agenttools.mcp_routes`).
For a window to actually have them, the CLI has to be *told* where that endpoint
is -- and every CLI says it differently:

* **claude** takes a JSON file on the command line (``--mcp-config``);
* **opencode** reads ``opencode.json`` from its working directory, or from an
  isolated config dir when one is injected.

Two rules this module keeps, both learned the hard way:

**The token never goes into argv.**  A Windows command line is readable by every
process on the machine.  The descriptor goes into a file whose path is passed,
or into a config file the CLI already reads; never onto the command line.

**A config file written into the worktree must be declared transient.**  The
worktree is checked for "uncommitted changes inside the authorized paths" before
the platform commits; a config file the platform itself wrote would look like the
harness left junk behind and the whole deliverable would be refused.  Adapters
declare those files (``CliHarness.transient_paths``) and the runner deletes them.

Nothing here decides *whether* a window gets tools: that is the platform's call
(the endpoint is only reachable for an attempt the platform provisioned), and a
window whose CLI mechanism is unknown simply gets no binding.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The name the CLI sees.  Stable on purpose: prompts and tests refer to it.
SERVER_NAME = "platform"

#: Kept for the descriptor shape claude's ``--mcp-config`` expects.
CLAUDE_CONFIG_SHAPE = "mcpServers"


@dataclass(frozen=True)
class Binding:
    """One attempt's tool endpoint, as the CLI needs to be told it."""

    base_url: str
    token: str
    task_id: str
    attempt_id: str
    path: str = "/api/v1/agenttools/mcp"

    def url(self) -> str:
        root = self.base_url.rstrip("/")
        return f"{root}{self.path}?task_id={self.task_id}&attempt_id={self.attempt_id}"

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def server_entry(self) -> dict[str, Any]:
        return {"type": "remote", "url": self.url(), "headers": self.headers()}

    def claude_document(self) -> dict[str, Any]:
        """claude's ``--mcp-config`` file: ``{"mcpServers": {...}}``."""
        return {CLAUDE_CONFIG_SHAPE: {SERVER_NAME: self.server_entry()}}

    def opencode_document(self) -> dict[str, Any]:
        """opencode's project config fragment: ``{"mcp": {"servers": {...}}}``."""
        return {"mcp": {"servers": {SERVER_NAME: self.server_entry()}}}


def binding_for(adapter: Any, *, base_url: str | None = None) -> Binding | None:
    """The binding for this adapter, or ``None`` when it has no task context.

    ``base_url`` comes from the adapter's own client (the platform it already
    talks to).  A missing task/attempt means the call is a review or a one-off;
    those do not get tools pointing at a task's worktree.
    """
    context = getattr(adapter, "task_context", None) or {}
    task_id = str(context.get("task_id") or "").strip()
    attempt_id = str(context.get("attempt_id") or "").strip()
    if not task_id or not attempt_id:
        return None
    client = getattr(adapter, "client", None)
    root = base_url or str(getattr(client, "base_url", "") or "").strip()
    if not root:
        return None
    headers = getattr(client, "headers", None) or {}
    token = ""
    try:
        value = str(headers.get("Authorization") or headers.get("authorization") or "")
    except Exception:  # pragma: no cover - exotic header containers
        value = ""
    token = value[7:].strip() if value.lower().startswith("bearer ") else value.strip()
    if not token:
        return None
    return Binding(base_url=root, token=token, task_id=task_id, attempt_id=attempt_id)


def write_json(path: Path, document: dict[str, Any]) -> Path:
    """Write a config file only this user can read, and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows ignores POSIX modes
        pass
    return path


def merge(destination: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge a fragment into an existing config without losing keys.

    Used where the platform already writes a config (opencode's isolated pool
    config): the MCP block must be added **next to** the provider section, not
    replace it -- replacing it would take the window's model away.
    """
    out = dict(destination)
    for key, value in fragment.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def remove_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        os.unlink(path)
    except OSError:
        pass
