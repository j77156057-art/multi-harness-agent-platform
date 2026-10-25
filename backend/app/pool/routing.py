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
    selection: Any = None,
) -> dict[str, Any]:
    """Decide where this attempt's model calls go.

    ``mode`` is ``"direct"`` (the harness uses its own provider) or ``"pool"``
    (the platform serves the attempt from the pool).  The answer always says
    *why*, because a silent switch of provider is exactly the kind of thing
    that has to be explainable afterwards.

    ``selection`` is what the **human** chose for this window (see
    ``app.adapter.model_choice``): when it names a model in the pool, this
    attempt runs on that model right away -- not only after the window's own
    provider died.  The pool stays the emergency path for everyone else.
    """
    evidence = failure_evidence(task_id, data_dir=data_dir)
    chosen = _chosen_model_id(selection)
    if chosen and evidence and str(evidence.get("model") or "") == chosen:
        # The chosen model is the one that just died: do not run it twice.
        # Fall through to the automatic path, which skips the failed model
        # anyway.
        chosen = ""
    if chosen:
        return _plan_for_chosen_model(
            task_id,
            harness_id=harness_id,
            attempt_no=attempt_no,
            model_id=chosen,
            pool=pool,
            consented_tier=str(getattr(selection, "model_tier", "") or ""),
        )
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
        # every call costs, and last the **user's own key** -- and that one
        # only when the user has already said yes.
        #
        # The **reserve** tier is deliberately not in this list: a plan kept in
        # reserve is there when nothing else is left, but the platform does not
        # spend it on its own -- asking for it has to be explicit.
        for tier in ("free", "metered", "user"):
            for entry in entries:
                if str(entry.get("id")) == str(evidence.get("model")):
                    continue
                upstream_name = str(entry.get("upstream") or "")
                if upstream_name in blocked or _owned_by_harness(upstream_name, harness_id):
                    continue
                if str(entry.get("tier") or "metered") != tier:
                    continue
                if tier == "user" and not _user_key_approved(str(entry.get("upstream") or "")):
                    # Not approved yet: the question is asked below, and this
                    # attempt does not spend the user's own money.
                    continue
                model = str(entry.get("id"))
                upstream = str(entry.get("upstream"))
                protocol = str(entry.get("protocol") or "")
                break
            if model:
                break
    if not model:
        # 自己的 key：平台**不自己花**。用户点头了才用；没点头就只**问**一次，
        # 这一轮照旧回窗口自己的 provider（失败照旧记在案上）。
        asked = _ask_for_user_key(
            entries,
            task_id=task_id,
            harness_id=harness_id,
            reason=(
                "the free and metered quotas could not serve this attempt; "
                "the platform asked the user about their own key"
            ),
        )
        return {
            "mode": "direct",
            "reason": (
                "the pool has no model to offer right now"
                + (
                    "; the platform is waiting for the user's answer about their own key"
                    if asked
                    else ""
                )
                + "; the window retries on its own provider (and the failure stays on record)"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    return {
        "mode": "pool",
        "reason": (
            f"the last attempt failed on this window's own provider "
            f"({evidence.get('error_type')}: {evidence.get('retry_reason') or evidence.get('error')}); "
            + (
                "this attempt runs on the user's own key (they said yes)"
                if upstream.startswith("user:")
                else "this attempt runs on the platform's pool instead"
            )
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


def _chosen_model_id(selection: Any) -> str:
    """The pool model the human chose for this window (empty when they did not)."""
    if selection is None:
        return ""
    mode = str(getattr(selection, "model_mode", "") or "")
    model = str(getattr(selection, "model_id", "") or "")
    if mode != "pool" or not model:
        return ""
    return model


def _plan_for_chosen_model(
    task_id: str,
    *,
    harness_id: str,
    attempt_no: int,
    model_id: str,
    pool: Any,
    consented_tier: str = "",
) -> dict[str, Any]:
    """Run this attempt on the model the human chose.

    Three things this must never do: swap the model silently, spend the user's
    own money without a yes, or treat "could not ask the pool" as "the pool is
    empty".
    """
    entries: list[dict[str, Any]] = []
    if pool is not None:
        try:
            entries = pool.models()
        except Exception:
            entries = []
    entry = next((item for item in entries if str(item.get("id")) == model_id), None)
    if entry is None:
        # The model is not in the pool right now (the quota went away, the
        # config changed).  Do **not** swap in a similar one: then nobody
        # could tell which model the work actually ran on.
        return {
            "mode": "direct",
            "reason": (
                f"the model {model_id} is not in the pool right now; "
                "the window retries on its own provider"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    upstream = str(entry.get("upstream") or "")
    current_tier = str(entry.get("tier") or "")
    if current_tier == "metered" and consented_tier != "metered":
        # The human said yes to a **free** quota; the quota is a paid one now.
        # Spending it without asking again would be spending the user's money
        # on the strength of an old yes.
        return {
            "mode": "direct",
            "reason": (
                f"the chosen model {model_id} is a paid quota now "
                "(it was not one when it was chosen); the platform will not spend "
                "it until the user says yes again"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    if current_tier == "user" and not _user_key_approved(upstream):
        # The user's own key: the platform never spends it on its own.  Ask
        # once; this attempt stays on the window's own provider.
        asked = _ask_for_user_key(
            entries,
            task_id=task_id,
            harness_id=harness_id,
            reason=(
                "the operator chose the user's own key for this window; "
                "the platform asked the user about spending it"
            ),
        )
        return {
            "mode": "direct",
            "reason": (
                "the chosen model is the user's own key and they have not said yes"
                + ("; the platform is waiting for the user's answer" if asked else "")
                + "; the window retries on its own provider"
            ),
            "task_id": task_id,
            "harness_id": harness_id,
        }
    return {
        "mode": "pool",
        "reason": (
            "the operator chose this model for this window" + (f" ({upstream})" if upstream else "")
        ),
        "model": model_id,
        "upstream": upstream,
        "upstream_protocol": str(entry.get("protocol") or ""),
        "base_path": POOL_BASE_PATH,
        "anthropic_path": POOL_ANTHROPIC_PATH,
        "task_id": task_id,
        "harness_id": harness_id,
        "attempt_no": attempt_no,
    }


def _user_key_approved(upstream_name: str) -> bool:
    """Has the user said yes to spending this key?"""
    if not upstream_name.startswith("user:"):
        return False
    from app.pool import user_keys  # local import: keeps the module cycle out

    return user_keys.approved(upstream_name.split(":", 1)[1])


def _ask_for_user_key(
    entries: list[dict[str, Any]],
    *,
    task_id: str,
    harness_id: str,
    reason: str,
) -> bool:
    """Ask the user to spend one of their own keys -- if they have one.

    Returns whether a question was raised.  An approved key is **not** asked
    about again: it is used (the caller handles that case before getting here).
    """
    from app.pool import user_keys  # local import: keeps the module cycle out

    for entry in entries:
        if str(entry.get("tier") or "") != user_keys.USER_TIER:
            continue
        upstream_name = str(entry.get("upstream") or "")
        key_id = upstream_name.split(":", 1)[1] if upstream_name.startswith("user:") else ""
        if not key_id or user_keys.approved(key_id):
            continue
        if user_keys.pending() is not None:
            # The question is already on the table; asking again would pile up.
            return True
        user_keys.raise_request(
            key_id=key_id,
            reason=reason,
            task_id=task_id,
            model=str(entry.get("id") or ""),
        )
        return True
    return False


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
