"""The single decision function for API-layer permissions (R10 §17, auth-layer).

Every refusal goes through :func:`decide`, so a denial carries one stable,
attributable ``resource:action`` key -- the reason ``permission_denied`` exists
(R10-fix-12). The table below mirrors R10 §17 row by row; where v1 does not
model an R10 role as a token subject, the fold is written down here rather
than left implicit:

* **Planner** folds into ``human`` (v1 has no planner subject; ``plan:submit``
  is the row that matters).
* **Verify Worker / Merge Worker** are process-side (sweeper family, runtime
  driver, mergeguard) and do not pass through the HTTP layer at all.
* **协作者** is a harness acting on the help face; its scope limit is the
  ``help-<n>`` branch rule (§14 naming), enforced at publish.
* Rows with no HTTP face in v1 (pin/unpin, delete_worktree, merge/integration
  write, read_secret) are deliberately absent -- no invented endpoints; the
  card records them as 出范围.

The approval/deny table maps 1:1 onto the matrix rows that DO have an HTTP
face; ``review:submit`` carries R10's "自己的 review lease" scope, which stays
a fact check inside the service (lease owner), not a second policy table.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.tokens import Principal

HARNESS = "harness"
HUMAN = "human"

#: permission key -> subject types allowed to call it.
POLICY: dict[str, frozenset[str]] = {
    # --- R10 §17 adapter face (harness token) ---
    "harness:probe": frozenset({HARNESS}),
    "harness:capabilities": frozenset({HARNESS}),
    "task:list_ready": frozenset({HARNESS}),
    "task:context": frozenset({HARNESS}),
    # claim/heartbeat: allowed for every harness role (§17 row).
    "task:claim": frozenset({HARNESS}),
    "task:heartbeat": frozenset({HARNESS}),
    # submit_result: 开发 Harness 自己的 attempt -- the "own attempt" scope is
    # the owner binding at the API layer plus the existing fencing facts.
    "task:submit_result": frozenset({HARNESS}),
    "task:publish_branch": frozenset({HARNESS}),
    "task:request_help": frozenset({HARNESS}),
    "task:ask_human": frozenset({HARNESS}),
    "task:report_blocked": frozenset({HARNESS}),
    "task:accept_help": frozenset({HARNESS}),
    "task:reject_help": frozenset({HARNESS}),
    "task:split": frozenset({HARNESS}),
    "task:release": frozenset({HARNESS}),
    "task:abandon": frozenset({HARNESS}),
    # --- R10 §17 review face (Reviewer is a harness subject) ---
    "review:claim": frozenset({HARNESS}),
    "review:submit": frozenset({HARNESS}),
    # R10 §17 also permits the human administrator/owner to submit reviews.
    "review:human_claim": frozenset({HUMAN}),
    "review:human_submit": frozenset({HUMAN}),
    # --- R10 §17 human face (人类管理员/负责人; Planner folded in) ---
    "plan:submit": frozenset({HUMAN}),
    "plan:approve": frozenset({HUMAN}),
    "plan:reject": frozenset({HUMAN}),
    "harness:register": frozenset({HUMAN}),
    "project:create": frozenset({HUMAN}),
    "group:create": frozenset({HUMAN}),
    "task:resume": frozenset({HUMAN}),
    "task:reopen": frozenset({HUMAN}),
    # release_escalated in §17 == /human/release here.
    "task:release_escalated": frozenset({HUMAN}),
    "task:adjudicate": frozenset({HUMAN}),
    "task:cancel": frozenset({HUMAN}),
}


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    key: str
    reason: str | None = None


def decide(principal: Principal, key: str) -> Decision:
    """The one place permission is granted or refused.

    Fail closed: an unknown key is a denial (``unknown_permission``), not an
    accidental allow -- a route wired to a key that was never registered must
    break loudly in tests, not silently in production.
    """
    roles = POLICY.get(key)
    if roles is None:
        return Decision(False, key, "unknown_permission")
    if principal.subject_type in roles:
        return Decision(True, key, None)
    return Decision(False, key, "role_not_allowed")
