#!/usr/bin/env python3
"""Mutation test for check_contract.py.

A checker that never fails is worse than no checker. Each case below injects one
specific defect into a copy of the contract and asserts that check_contract.py
exits non-zero AND names the right guard. If any case passes silently, the
corresponding assertion in check_contract.py is vacuous.

Usage:  python mutation_test.py
Exit 0 when every mutation is caught.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (name, filename, find, replace, expected FAIL substring)
MUTATIONS = [
    (
        "invented event name",
        "openapi.yaml",
        "        - scheduler_restart_lease_expiry",
        "        - scheduler_restart_lease_expiry\n        - task_resurrected",
        "EventName enum set equals the 37.7 union",
    ),
    (
        "dropped a formal state",
        "openapi.yaml",
        "        - reclaiming\n",
        "",
        "TaskStatus enum set equals the section 10 state set",
    ),
    (
        "added the split pseudo-state to tasks.status domain",
        "openapi.yaml",
        "        - merged\n",
        "        - merged\n        - child_created\n",
        "child_created is NOT in TaskStatus",
    ),
    (
        "broke section 7 verb spelling alignment",
        "openapi.yaml",
        "  /adapter/submit_result:",
        "  /adapter/submitResult:",
        "adapter path tail",
    ),
    (
        "unbound the loopback-only server",
        "openapi.yaml",
        "  - url: http://127.0.0.1:8787/api/v1",
        "  - url: http://0.0.0.0:8787/api/v1",
        "loopback-only",
    ),
    (
        "dangling $ref",
        "openapi.yaml",
        "                $ref: '#/components/schemas/ClaimResponse'",
        "                $ref: '#/components/schemas/ClaimResult'",
        "$ref targets resolve",
    ),
    (
        "enumerated an undefined error code",
        "openapi.yaml",
        "                            - plan.cycle_detected",
        "                            - plan.circular_dependency",
        "every enumerated error code is defined",
    ),
    (
        "mis-tagged an event's M/O/R set",
        "error-codes.md",
        "`idempotency_conflict`(O)",
        "`idempotency_conflict`(M)",
        "correct M/O/R tag",
    ),
    (
        "cited an event name that is not in the allowlist",
        "error-codes.md",
        "`attempt_budget_exhausted`(M)",
        "`attempt_budget_exceeded`(M)",
        "correct M/O/R tag",
    ),
    (
        "put the per-task claim key back on claim_requests (the R10-fix-09 defect)",
        "idempotency.md",
        "| `claim`（任务项级） | **`task_claims`** | `unique(claim_request_id, task_id)` |",
        "| `claim`（任务项级） | `claim_requests` | `unique(claim_request_id, task_id)` |",
        "never places the composite claim key on claim_requests",
    ),
]


def run_one(name: str, fname: str, find: str, repl: str, expect: str) -> bool:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for f in ("openapi.yaml", "error-codes.md", "idempotency.md"):
            shutil.copy(HERE / f, tmp / f)
        target = tmp / fname
        text = target.read_text(encoding="utf-8")
        if find not in text:
            print(f"  SKIP  {name}: anchor not found in {fname} (mutation is stale)")
            return False
        target.write_text(text.replace(find, repl, 1), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(HERE / "check_contract.py"), "--here", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = proc.stdout + proc.stderr
        caught = proc.returncode != 0
        named = expect in out
        ok = caught and named
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not caught:
            print("        checker exited 0 -- guard is vacuous")
        elif not named:
            print(f"        exited non-zero but did not report '{expect}'")
            for line in out.splitlines():
                if "FAIL" in line:
                    print(f"        reported: {line.strip()}")
        return ok


def main() -> int:
    print("baseline (unmutated contract):")
    base = subprocess.run(
        [sys.executable, str(HERE / "check_contract.py"), "--here", str(HERE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if base.returncode != 0:
        print("  the unmutated contract already fails; fix that before mutation testing")
        print(base.stdout[-2000:])
        return 1
    print("  exit 0, clean")
    print()

    print(f"{len(MUTATIONS)} mutations:")
    results = [run_one(*m) for m in MUTATIONS]
    missed = [MUTATIONS[i][0] for i, r in enumerate(results) if not r]

    print()
    print("=" * 62)
    print(f"caught {sum(results)}/{len(results)}")
    if missed:
        print("MISSED:")
        for m in missed:
            print(f"  - {m}")
        return 1
    print("every guard in check_contract.py is non-vacuous")
    return 0


if __name__ == "__main__":
    sys.exit(main())
