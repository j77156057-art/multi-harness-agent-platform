"""Should this attempt run on its own provider, or on somebody else's quota?

The platform's rule, in one sentence: **a harness that just failed because its
provider ran out (or died) does not get to run the next attempt on the same
provider.**  That attempt is routed through the platform's pool instead, so
the work continues on a quota that is actually alive.

The decision is made by the platform, not by the worker: the worker only
knows its own CLI, while the platform knows what failed, for which task, and
which quota is available.  The worker's own provider stays the default -- the
pool is the *emergency* path, not the normal one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings

#: Where the pool's own surface lives on the platform API.
POOL_BASE_PATH = "/api/v1/pool/v1"
#: The Anthropic-compatible surface (the Claude CLI appends /v1/messages).
POOL_ANTHROPIC_PATH = "/api/v1/pool"


def _base_of(harness_id: str) -> str:
    """The base harness a window belongs to (``qoder-member-1`` → ``qoder``)."""
    return str(harness_id).split("-member-")[0].split("-window-")[0]


def _owned_by_harness(upstream: str, harness_id: str) -> bool:
    """Whether an upstream is the failing window's own quota.

    Upstreams are named after the harness that owns them (``codex:geekspace``,
    ``qoder:Qwen3.8-Flash``, ``opencode-cli:...``).  A harness that follows
    that convention is protected without a code change; the explicit map in
    :func:`_blocked_upstreams` covers the names that do not.
    """
    base = _base_of(harness_id)
    if not base:
        return False
    name = str(upstream or "")
    head = name.split(":", 1)[0]
    return head == base or head.startswith(base + "-") or name.startswith(base + "/")


def failure_evidence(task_id: str, *, data_dir: Path | None = None) -> dict[str, Any] | None:
    """The last failure the worker recorded for a task, if any."""
    root = (data_dir or Path(settings.data_dir)) / "formal-runs"
    try:
        document = json.loads((root / f"{task_id}.failure.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def plan_attempt(
    task_id: str,
    *,
    harness_id: str,
    attempt_no: int = 0,
    data_dir: Path | None = None,
    pool: Any = None,
) -> dict[str, Any]:
    """Decide where this attempt's model calls go.

    ``mode`` is ``"direct"`` (the harness uses its own provider) or ``"pool"``
    (the platform serves the attempt from the pool).  The answer always says
    *why*, because a silent switch of provider is exactly the kind of thing
    that has to be explainable afterwards.
    """
    evidence = failure_evidence(task_id, data_dir=data_dir)
    if not evidence:
        return {
            "mode": "direct",
            "reason": "no failure on record for this task",
            "task_id": task_id,
            "harness_id": harness_id,
        }
    if evidence.get("harness_id") and str(evidence["harness_id"]) != str(harness_id):
        return {
            "mode": "direct",
            "reason": (
                "the last failure belongs to another harness "
                f"({evidence.get('harness_id')}); this window's own provider is not implicated"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    if evidence.get("retryable") is not True:
        return {
            "mode": "direct",
            "reason": (
                f"the last failure was not a provider failure "
                f"({evidence.get('error_type')}); retrying elsewhere would not help"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    # A provider-class failure on this very window: its quota is the suspect.
    model = None
    upstream = None
    protocol = ""
    if pool is not None:
        try:
            entries = pool.models()
        except Exception:
            entries = []
        # The failing window's own quota is exactly what cannot be used again.
        blocked = _blocked_upstreams(harness_id)
        # Which quota to spend, in order: somebody's **free** tier first (the
        # CLI's own free allowance), then the metered relay accounts where
        # every call costs.
        #
        # The **reserve** tier is deliberately not in this list: a plan kept in
        # reserve is there when nothing else is left, but the platform does not
        # spend it on its own -- asking for it has to be explicit.
        for tier in ("free", "metered"):
            for entry in entries:
                if str(entry.get("id")) == str(evidence.get("model")):
                    continue
                upstream_name = str(entry.get("upstream") or "")
                if upstream_name in blocked or _owned_by_harness(upstream_name, harness_id):
                    continue
                if str(entry.get("tier") or "metered") != tier:
                    continue
                model = str(entry.get("id"))
                upstream = str(entry.get("upstream"))
                protocol = str(entry.get("protocol") or "")
                break
            if model:
                break
    if not model:
        return {
            "mode": "direct",
            "reason": (
                "the pool has no model to offer right now; the window retries on its own "
                "provider (and the failure stays on record)"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    return {
        "mode": "pool",
        "reason": (
            f"the last attempt failed on this window's own provider "
            f"({evidence.get('error_type')}: {evidence.get('retry_reason') or evidence.get('error')}); "
            f"this attempt runs on the platform's pool instead"
        ),
        "model": model,
        "upstream": upstream,
        "upstream_protocol": protocol,
        "base_path": POOL_BASE_PATH,
        "anthropic_path": POOL_ANTHROPIC_PATH,
        "task_id": task_id,
        "harness_id": harness_id,
        "attempt_no": attempt_no,
    }


def _blocked_upstreams(harness_id: str) -> set[str]:
    """The pool sources that belong to the failing window's own quota.

    Routing a dead provider's task back to the same provider would be a
    loop: the second attempt would fail the same way.
    """
    base = _base_of(harness_id)
    if base == "claude-code":
        return {"claude-provider"}
    if base == "codex":
        return {"codex:geekspace", "codex:my_codex", "codex:fhl"}
    if base == "opencode":
        # The opencode CLI's own free tier is the same quota as the CLI slot.
        return {"opencode:1", "opencode:2", "opencode:openai", "opencode:local", "opencode-cli"}
    return set()
