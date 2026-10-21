"""Which model is this harness actually going to use?

The office has to answer a human question -- "the upstream died, who can work
now?" -- and the honest answer lives in two places:

* **the model** is a *deployment* fact: the CLI reads its own config, and the
  platform does not hold model credentials. So the platform reads the same
  config file the CLI reads and reports what it says. Best effort: a CLI whose
  config we cannot read reports "unknown", never a guess.
* **whether it works right now** is a *platform* fact: the platform saw the last
  attempt fail (a provider error, a missing binary, a refused submission) and
  can say so.

Nothing here writes state; it is a read for the observation surface.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

#: Where each CLI keeps the model it is configured to use.
_MODEL_SOURCES: dict[str, tuple[str, str]] = {
    # harness id -> (config path relative to home, json key)
    "claude-code": (".claude/settings.json", "model"),
    "opencode": (".config/opencode/opencode.json", "model"),
    "codex": (".codex/config.toml", "model"),
    "qoder": (".qoder-cn/entry/qoder-cn.cmd", ""),
    "codebuddy": ("", ""),
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_toml_model(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


_INSTANCE_RE = re.compile(r"^(?P<base>.+)-(?:member|window)-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


def configured_model(harness_id: str, *, home: Path | None = None) -> dict[str, Any]:
    """The model this harness's CLI is configured to use.

    ``model`` is ``None`` when the CLI does not keep a readable config (or the
    file is absent): the platform reports "unknown" rather than inventing one.

    ``home`` is injectable so the lookup can be tested without touching the
    real user profile.
    """
    source = _MODEL_SOURCES.get(harness_id)
    base_id = harness_id
    if source is None:
        # A window of a known CLI reads that CLI's config: "codex-member-1" is
        # the same ``codex`` binary with the same configuration.
        match = _INSTANCE_RE.match(harness_id)
        base_id = match.group("base") if match else ""
        source = _MODEL_SOURCES.get(base_id)
        if source is None:
            return {"model": None, "source": "unknown-harness"}
    relative, key = source
    if not relative:
        return {"model": None, "source": "not-readable"}
    path = (home if home is not None else Path.home()) / relative
    if path.suffix == ".toml":
        model = _read_toml_model(path)
        return {"model": model, "source": str(path)}
    document = _read_json(path)
    if not isinstance(document, dict):
        return {"model": None, "source": str(path)}
    value = document.get(key)
    if base_id == "claude-code":
        return _claude_model(document, value, path)
    if isinstance(value, str) and value.strip():
        return {"model": value.strip(), "source": str(path)}
    return {"model": None, "source": str(path)}


def _claude_model(document: dict[str, Any], value: Any, path: Path) -> dict[str, Any]:
    """The model the Claude CLI will actually use.

    The CLI's ``model`` setting names an **alias** ("fable"), and the alias is
    resolved through the ``env`` block of the same settings file
    (``ANTHROPIC_DEFAULT_FABLE_MODEL``).  That ``env`` block also overrides the
    process environment when the CLI runs, so it is what has to be read here:
    reporting the bare alias would tell the office "fable" while the provider
    is really ``deepseek-v4-flash``.
    """
    env_block = document.get("env")
    env_block = env_block if isinstance(env_block, dict) else {}
    alias = value.strip() if isinstance(value, str) and value.strip() else ""
    resolved: str | None = None
    if alias:
        candidate = env_block.get(f"ANTHROPIC_DEFAULT_{alias.upper()}_MODEL")
        if isinstance(candidate, str) and candidate.strip():
            resolved = candidate.strip()
    if not resolved:
        candidate = env_block.get("ANTHROPIC_MODEL")
        if isinstance(candidate, str) and candidate.strip():
            resolved = candidate.strip()
    if not resolved:
        candidate = os.environ.get("ANTHROPIC_MODEL")
        if candidate and candidate.strip():
            resolved = candidate.strip()
    if resolved:
        return {"model": resolved, "model_alias": alias or None, "source": str(path)}
    if alias:
        return {"model": alias, "model_alias": None, "source": str(path)}
    return {"model": None, "source": str(path)}
