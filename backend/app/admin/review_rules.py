"""What a review is judged against — written down where a person can change it.

Before this module the two decisive rules lived as literal strings inside the
review prompt:

    "approve only when the diff satisfies the acceptance and stays inside
     exclusive_paths. Otherwise request_changes."

That is a *judgement standard*, and it was the platform's private opinion: a
group owner who wanted a different bar (say "approve unless the acceptance is
contradicted", or "always demand the diff be re-read by a second member") had
no way to say so short of editing Python. Standards belong to the people the
work is for, so they live in ``review-rules.json`` at the repository root and
are read from there on every review.

What stays in code, deliberately, is the **protocol**: the JSON shape of the
answer, the contract-id requirement, and "one finding per acceptance contract".
Those are not judgement calls — the platform parses the answer and refuses a
verdict that ignores a contract, so a configurable shape would only produce
reviews the platform has to reject.

Failure is not fatal: a missing or malformed file falls back to the built-in
rules and reports why, because refusing every review over a typo in a config
file would stop the whole queue.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: The bar the platform used to hard-code. Kept as the fallback so a machine
#: without the file behaves exactly as before.
DEFAULT_CRITERIA: tuple[str, ...] = (
    "approve only when the diff satisfies the acceptance and stays inside exclusive_paths."
    " Otherwise request_changes.",
)

#: Repository-root file. Resolution follows ``verify-profiles.json``: a file in
#: the repo, overridable by an environment variable for other deployments.
DEFAULT_RULES_PATH = Path(__file__).resolve().parents[3] / "review-rules.json"


def rules_path(explicit: Path | str | None = None) -> Path:
    """Where the rules come from: explicit argument > env var > repo default."""
    if explicit is not None:
        return Path(explicit)
    raw = os.environ.get("AP_REVIEW_RULES_FILE", "").strip()
    return Path(raw) if raw else DEFAULT_RULES_PATH


def load_review_rules(path: Path | str | None = None) -> tuple[tuple[str, ...], str | None]:
    """Return ``(criteria, problem)``.

    ``problem`` is None on success; otherwise it says why the built-in rules are
    in use, so a caller can surface it instead of silently pretending the file
    was read.
    """
    target = rules_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_CRITERIA, None
    except (OSError, ValueError) as exc:
        return DEFAULT_CRITERIA, f"{target.name} is unreadable ({type(exc).__name__}); using built-in rules"
    if not isinstance(payload, dict):
        return DEFAULT_CRITERIA, f"{target.name} is not a JSON object; using built-in rules"
    raw = payload.get("criteria")
    if not isinstance(raw, list) or not raw:
        return DEFAULT_CRITERIA, f"{target.name} has no non-empty 'criteria'; using built-in rules"
    cleaned = tuple(str(item).strip() for item in raw if str(item).strip())
    if not cleaned:
        return DEFAULT_CRITERIA, f"{target.name} has no non-empty 'criteria'; using built-in rules"
    return cleaned, None
