"""Branch naming for task attempts (R10 §14).

R10-09 requires §14, §17's permission matrix and the Push Proxy's branch
whitelist to share one naming convention. That only holds if one function owns
the format, so this module is the only place in the codebase that builds or
parses an agent branch name -- the whitelist check is
:func:`expected_branch`, not a regex written again at the call-site.

Layout::

    agent/<group-short>/<harness>/<task-id>/<attempt-id>
    agent/<group-short>/<harness>/<task-id>/help-<n>     (help worktree, §14)

A single line of §14 lists both, so a help branch is an agent branch with a
``help-<n>`` tail -- not a separate ``help/`` namespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

AGENT_PREFIX = "agent"
HELP_MARKER = "help"

#: One path segment. Deliberately excludes '/', '..' and whitespace: a task id
#: or harness name carrying either would forge extra segments.
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HELP_TAIL = re.compile(r"^help-(\d+)$")


class BranchNameError(ValueError):
    """The branch name is not a legal R10 §14 agent branch."""


@dataclass(frozen=True, slots=True)
class AgentBranch:
    """A parsed agent branch. ``help_ordinal`` is set for help branches."""

    group_short: str
    harness: str
    task_id: str
    attempt_id: str
    help_ordinal: int | None = None

    @property
    def is_help(self) -> bool:
        return self.help_ordinal is not None

    @property
    def name(self) -> str:
        tail = f"{HELP_MARKER}-{self.help_ordinal}" if self.is_help else self.attempt_id
        return f"{AGENT_PREFIX}/{self.group_short}/{self.harness}/{self.task_id}/{tail}"


def _check_segment(label: str, value: str) -> str:
    if not _SEGMENT.match(value):
        raise BranchNameError(f"{label} is not a legal branch path segment: {value!r}")
    return value


def agent_branch(*, group_short: str, harness: str, task_id: str, attempt_id: str) -> str:
    """Format the branch for one attempt."""
    return AgentBranch(
        group_short=_check_segment("group_short", group_short),
        harness=_check_segment("harness", harness),
        task_id=_check_segment("task_id", task_id),
        attempt_id=_check_segment("attempt_id", attempt_id),
    ).name


def help_branch(*, group_short: str, harness: str, task_id: str, ordinal: int) -> str:
    """Format a collaborator help branch (R10 §14 ``help-<n>``)."""
    if ordinal < 1:
        raise BranchNameError(f"help ordinal must be >= 1, got {ordinal}")
    return AgentBranch(
        group_short=_check_segment("group_short", group_short),
        harness=_check_segment("harness", harness),
        task_id=_check_segment("task_id", task_id),
        attempt_id="",
        help_ordinal=ordinal,
    ).name


def parse_branch(name: str) -> AgentBranch:
    """Parse an agent branch name, raising :class:`BranchNameError` otherwise."""
    parts = name.split("/")
    if len(parts) != 5:
        raise BranchNameError(f"agent branch must have 5 path segments, got {len(parts)}: {name!r}")
    prefix, group_short, harness, task_id, tail = parts
    if prefix != AGENT_PREFIX:
        raise BranchNameError(f"agent branch must start with {AGENT_PREFIX}/: {name!r}")
    help_match = _HELP_TAIL.match(tail)
    if help_match:
        return AgentBranch(
            group_short=_check_segment("group_short", group_short),
            harness=_check_segment("harness", harness),
            task_id=_check_segment("task_id", task_id),
            attempt_id="",
            help_ordinal=int(help_match.group(1)),
        )
    return AgentBranch(
        group_short=_check_segment("group_short", group_short),
        harness=_check_segment("harness", harness),
        task_id=_check_segment("task_id", task_id),
        attempt_id=_check_segment("attempt_id", tail),
    )


def expected_branch(*, group_short: str, harness: str, task_id: str, attempt_id: str) -> str:
    """The only branch a publish for this attempt may target."""
    return agent_branch(
        group_short=group_short,
        harness=harness,
        task_id=task_id,
        attempt_id=attempt_id,
    )


def is_integration_ref(ref: str) -> bool:
    """True for refs only the scheduler may write (R10 §13).

    Kept here rather than at the call-site so the canonical hook and the API
    cannot drift apart on what "integration" means.
    """
    return ref in {"refs/heads/integration", "refs/heads/main"}
