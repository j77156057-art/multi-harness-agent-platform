"""CORE-STATE-COVERAGE-001: every non-terminal state has a proven recovery exit.

R10 §10 gives 23 states, 21 non-terminal. "Coverage" here is a two-way
mechanical contract, not a claim in prose:

* every non-terminal state is either in :data:`RECOVERY` or in one of the two
  waiver sets -- nothing may be silently skipped, and the waiver sets are
  pinned, so implementing a missing mechanism (or reaching a previously
  unreachable state) turns the suite red until the map is updated;
* every mapped edge must be a legal triple in the code transition table (which
  mirrors R10 §10 byte-for-byte, ``test_transition_mirror``), must have a real
  trigger somewhere in ``app/`` (an event constant only the table knows about
  proves nothing), and must point at a test function that actually exists;
* every "no exit" waiver must still be true: the state's outgoing events have
  no trigger anywhere in ``app/``;
* every "unreachable" waiver must still be true: the state's incoming events
  have no trigger anywhere in ``app/``.

The integration proofs themselves live where they were written (test_adapter,
test_api_flow, test_verify, test_merge, test_lease), except the two exits that
had none -- they are the first two tests below.
"""

from __future__ import annotations

import importlib
import pathlib
import re

from app.claim.service import ClaimRequest, ClaimService
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.spec import PSEUDO_STATES, TaskStatus
from app.state.machine import default_transition_table
from app.tasks.service import SchedulerService
from tests.claim_support import make_database, seed_project, seed_task
from tests.merge_support import build_chain

#: state -> [(event, next_state, "module::test")] -- at least one proven exit.
RECOVERY: dict[str, list[tuple[str, str, str]]] = {
    "draft": [
        (
            "plan.reject",
            "draft_rejected",
            "tests.test_api_flow::test_reject_plan_leaves_tasks_draft_rejected",
        ),
        (
            "plan.approve",
            "ready",
            "tests.test_api_flow::test_full_path_over_http_with_two_scaffolded_transitions",
        ),
    ],
    "ready_waiting_deps": [
        (
            "dependencies_satisfied",
            "ready",
            "tests.test_sweeper_deps_resources::test_dependencies_satisfied_moves_the_task_to_ready",
        ),
        (
            "orphan_detected",
            "escalated",
            "tests.test_sweeper_deps_resources::test_orphan_detection_escalates_when_upstreams_died_without_output",
        ),
        (
            "block_timeout",
            "escalated",
            "tests.test_sweeper_deps_resources::test_block_timeout_fires_only_past_twice_the_expected_duration",
        ),
    ],
    "queued": [
        (
            "resource_available",
            "claimed",
            "tests.test_sweeper_deps_resources::test_a_queued_task_resumes_to_claimed_when_the_resource_frees",
        ),
    ],
    "ready": [
        ("claim", "claimed", "tests.test_claim::test_claim_grants_owner_attempt_and_lease"),
        (
            "claim_deferred_resource",
            "queued",
            "tests.test_claim::test_deferred_resource_queues_without_owner_attempt_or_lease",
        ),
    ],
    "verifying": [
        (
            "verify_passed",
            "awaiting_review",
            "tests.test_verify::test_passing_head_moves_the_task_to_awaiting_review",
        ),
        (
            "verify_code_failed",
            "changes_requested",
            "tests.test_verify::test_failing_head_cannot_reach_review",
        ),
        (
            "verify_env_failed",
            "verifying",
            "tests.test_verify::test_an_environment_failure_is_recorded_as_env_failed",
        ),
        (
            "verify_execution_timeout",
            "escalated",
            "tests.test_verify::test_timeout_is_recorded_as_timeout_not_code_failure",
        ),
        (
            "differential_not_applicable",
            "awaiting_human",
            "tests.test_verify::test_differential_not_applicable_goes_to_a_human",
        ),
    ],
    "claimed": [
        ("start", "in_progress", "tests.test_api_flow::test_owner_actions_over_http"),
        (
            "release",
            "ready",
            "tests.test_adapter::test_release_returns_the_task_to_the_pool",
        ),
        (
            "lease_expire",
            "stale",
            "tests.test_adapter::test_a_stale_attempt_can_be_taken_over",
        ),
    ],
    "in_progress": [
        (
            "submit_result",
            "verifying",
            "tests.test_adapter::test_the_fake_harness_runs_the_whole_loop",
        ),
        (
            "abandon",
            "abandoned",
            "tests.test_adapter::test_abandon_ends_the_attempt",
        ),
        (
            "request_help",
            "needs_help",
            "tests.test_api_flow::test_owner_actions_over_http",
        ),
        (
            "dependency_missing",
            "blocked",
            "tests.test_state_coverage::test_blocked_has_a_recovery_exit",
        ),
        (
            "split_parent_blocked",
            "blocked",
            "tests.test_adapter_verbs::test_split_creates_child_drafts_and_blocks_the_parent",
        ),
    ],
    "needs_help": [
        (
            "release",
            "ready",
            "tests.test_api_flow::test_owner_actions_over_http",
        ),
        (
            "help_accepted",
            "needs_help",
            "tests.test_adapter_verbs::test_accept_help_records_the_collaborator_without_moving_the_task",
        ),
        (
            "help_rejected",
            "needs_help",
            "tests.test_adapter_verbs::test_reject_help_self_loops",
        ),
    ],
    "blocked": [
        (
            "release",
            "ready",
            "tests.test_state_coverage::test_blocked_has_a_recovery_exit",
        ),
        (
            "all_children_merged",
            "ready",
            "tests.test_split_closure::test_all_children_merged_wakes_the_parent",
        ),
    ],
    "changes_requested": [
        (
            "release",
            "ready",
            "tests.test_state_coverage::test_changes_requested_has_a_recovery_exit",
        ),
    ],
    "awaiting_review": [
        (
            "approve",
            "approved",
            "tests.test_merge::test_merge_writes_integration_and_marks_the_task_merged",
        ),
        (
            "request_changes",
            "changes_requested",
            "tests.test_api_flow::test_full_path_over_http_with_two_scaffolded_transitions",
        ),
    ],
    "approved": [
        (
            "merge_start",
            "merging",
            "tests.test_merge::test_merge_writes_integration_and_marks_the_task_merged",
        ),
    ],
    "merging": [
        (
            "merge_success",
            "merged",
            "tests.test_merge::test_merge_writes_integration_and_marks_the_task_merged",
        ),
        (
            "merge_conflict",
            "approved",
            "tests.test_merge::test_rename_rename_conflict_belongs_to_the_merge_worker",
        ),
        (
            "merge_conflict_needs_owner",
            "changes_requested",
            "tests.test_merge::test_conflict_in_the_tasks_own_files_needs_the_owner",
        ),
        (
            "merge_post_commit_failure",
            "approved",
            "tests.test_merge::test_post_merge_failure_is_reverted_with_the_tree_assertion",
        ),
    ],
    "stale": [
        (
            "reclaim",
            "reclaiming",
            "tests.test_lease::test_stale_task_can_be_reclaimed_and_taken_over",
        ),
        (
            "attempt_budget_exhausted",
            "escalated",
            "tests.test_lease::test_budget_exhaustion_escalates_from_stale",
        ),
    ],
    "reclaiming": [
        (
            "take_over",
            "claimed",
            "tests.test_lease::test_stale_task_can_be_reclaimed_and_taken_over",
        ),
    ],
    "awaiting_human": [
        (
            "human.resume_verify_not_applicable",
            "verifying",
            "tests.test_verify::test_a_human_can_resume_verification_after_the_declaration",
        ),
        (
            "human_wait_timeout",
            "awaiting_human",
            "tests.test_human_ops::test_the_first_reminder_fires_after_the_window",
        ),
    ],
    "plan_drift": [
        (
            "lease_expire",
            "stale",
            "tests.test_sweeper_drift::test_drift_states_have_a_recovery_exit",
        ),
    ],
    "impl_drift": [
        (
            "lease_expire",
            "stale",
            "tests.test_sweeper_drift::test_drift_states_have_a_recovery_exit",
        ),
    ],
    "abandoned": [
        (
            "reopen",
            "ready",
            "tests.test_human_ops::test_reopen_revives_an_abandoned_task",
        ),
    ],
    "escalated": [
        (
            "human.release",
            "ready",
            "tests.test_human_ops::test_release_returns_an_escalated_task_to_ready",
        ),
        (
            "human.adjudicate",
            "awaiting_human",
            "tests.test_human_ops::test_adjudicate_parks_an_escalated_task_with_a_decision",
        ),
        (
            "escalation_reminder",
            "escalated",
            "tests.test_human_ops::test_the_escalation_reminder_backs_off_with_the_event_count",
        ),
    ],
}

#: States whose exits exist in the table but have no trigger anywhere in the
#: MVP: the mechanism (the plan revision flow) is later-stage work. Every
#: outgoing event must still lack a trigger.
WAIVERS_NO_EXIT: dict[str, str] = {
    "draft_rejected": "plan revision (plan.revise / plan.supersede) is not implemented in this MVP",
}

#: States with exits but no reachable entry -- now empty: sweeper-drift landed
#: the detectors, and both states moved into RECOVERY above. Kept as a set so
#: the reverse guard still fails loudly if a state is ever put back here
#: without its mechanism.
WAIVERS_UNREACHABLE: dict[str, str] = {}


# ------------------------------------------------------------ the proofs


def _claim_and_start(tmp_path):
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    seed_project(database, integration_commit=base)
    seed_task(database, task_id="T-1")
    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    service.start_task(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=manager,
    )
    return database, service, item


def _status(database) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])


def test_blocked_has_a_recovery_exit(tmp_path):
    """in_progress -> blocked (report_blocked) -> ready (release)."""
    database, service, item = _claim_and_start(tmp_path)
    service.report_blocked(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        blocker_ref="dep:T-9",
    )
    assert _status(database) == "blocked"
    blocked_event = database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'dependency_missing'"
    )
    assert blocked_event[0]["n"] == 1

    service.release(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        reason="blocker cleared",
    )
    assert _status(database) == "ready"
    released = database.read("SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'release'")
    assert released[0]["n"] == 1


def test_changes_requested_has_a_recovery_exit(tmp_path):
    """verify fails -> changes_requested -> ready (release hands it back)."""
    chain = build_chain(
        tmp_path,
        verify_command=["python", "-c", "import sys; sys.exit(1)"],
    )
    status = chain.database.read("SELECT status FROM tasks WHERE task_id = ?", (chain.task_id,))[0]
    assert str(status["status"]) == "changes_requested"

    chain.service.release(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        fencing_epoch=chain.fencing_epoch,
        reason="handed back for rework",
    )
    after = chain.database.read("SELECT status FROM tasks WHERE task_id = ?", (chain.task_id,))[0]
    assert str(after["status"]) == "ready"


# ------------------------------------------------------- the mechanical map


def _triples() -> list[tuple[str, str, str, str]]:
    """(current, event_value, next, event_name) for every table edge."""
    out = []
    for transition in default_transition_table().transitions:
        nxt = transition.next if transition.next in PSEUDO_STATES else transition.next.value
        for current in transition.current:
            out.append((current.value, transition.event.value, nxt, transition.event.name))
    return out


def _app_event_names() -> set[str]:
    """EventKind members referenced outside the enum and the table itself."""
    names: set[str] = set()
    app = pathlib.Path(__file__).resolve().parents[1] / "app"
    for path in app.rglob("*.py"):
        if path.name in {"spec.py", "machine.py"}:
            continue
        for match in re.finditer(r"EventKind\.([A-Z_]+)", path.read_text(encoding="utf-8")):
            names.add(match.group(1))
    return names


def test_every_non_terminal_state_is_mapped_or_explicitly_waived():
    states = {status.value for status in TaskStatus if not status.is_terminal}
    assert len(states) == 21
    mapped = set(RECOVERY)
    waived = set(WAIVERS_NO_EXIT) | set(WAIVERS_UNREACHABLE)
    assert not (mapped & waived)
    missing = sorted(states - mapped - waived)
    assert not missing, f"non-terminal states with no coverage decision: {missing}"
    unknown = sorted((mapped | waived) - states)
    assert not unknown, f"coverage decisions for unknown states: {unknown}"
    for state, reason in {**WAIVERS_NO_EXIT, **WAIVERS_UNREACHABLE}.items():
        assert reason.strip(), state


def test_the_waiver_sets_are_pinned():
    """Implementing a mechanism must consciously move a state into the map."""
    assert sorted(WAIVERS_NO_EXIT) == ["draft_rejected"]
    assert sorted(WAIVERS_UNREACHABLE) == [], "drift detectors landed; nothing unreachable"


def test_each_mapped_exit_is_legal_has_a_trigger_and_points_at_a_live_test():
    triples = _triples()
    legal = {(cur, event, nxt) for cur, event, nxt, _name in triples}
    triggers = _app_event_names()
    names = {event: name for _cur, event, _nxt, name in triples}

    for state, exits in RECOVERY.items():
        assert exits, state
        for event, next_state, ref in exits:
            assert (
                state,
                event,
                next_state,
            ) in legal, f"illegal edge {state} --{event}--> {next_state}"
            assert names[event] in triggers, f"{state}: no trigger for {event} anywhere in app/"
            module_name, test_name = ref.split("::")
            module = importlib.import_module(module_name)
            assert callable(getattr(module, test_name, None)), f"stale reference: {ref}"


def test_each_waived_state_still_lacks_the_mechanism_it_waives():
    triggers = _app_event_names()
    names = {event: name for _cur, event, _nxt, name in _triples()}

    for state in WAIVERS_NO_EXIT:
        out_events = {event for cur, event, _nxt, _name in _triples() if cur == state}
        now_triggered = sorted(e for e in out_events if names[e] in triggers)
        assert not now_triggered, (
            f"{state} now has a trigger for {now_triggered}; move it from "
            "WAIVERS_NO_EXIT into RECOVERY"
        )

    for state in WAIVERS_UNREACHABLE:
        in_events = {event for _cur, event, nxt, _name in _triples() if nxt == state}
        now_triggered = sorted(e for e in in_events if names[e] in triggers)
        assert not now_triggered, (
            f"{state} is now reachable via {now_triggered}; move it from "
            "WAIVERS_UNREACHABLE into RECOVERY"
        )
