#!/usr/bin/env python3
"""Mechanical check for the MVP-08 prep contract.

Verifies that nothing in the draft was invented: every TaskStatus value exists
in R10 section 10, every EventName value exists in R10 section 37.7's M/O/R
allowlist, every $ref resolves, every error code cited in openapi.yaml is
defined in error-codes.md, and the contract does not put the per-task claim
idempotency key back on `claim_requests` (the R10-fix-09 defect this prep
originally reproduced).

Usage:  python check_contract.py [--r10 PATH] [--here DIR]
Exit 0 when all assertions pass.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    # docstate and discipline are deliberately stdlib-only; this prep script is
    # not, because it has to parse an OpenAPI document. PyYAML is already in the
    # env as a transitive dep of uvicorn[standard]. Fail loudly rather than
    # silently skipping the parse-dependent assertion groups.
    print("check_contract.py needs PyYAML (a transitive dep of uvicorn[standard]).")
    print("Run it with the repo venv:  ./.venv/Scripts/python.exe check_contract.py")
    sys.exit(2)

# This script lives at <repo>/docs/prep/MVP-08/check_contract.py, so the repo
# root is three levels up. Resolving from __file__ rather than hardcoding an
# absolute path keeps the check runnable from any worktree. If the script is
# copied somewhere shallower there is no repo root to find, so fall back to the
# script's own directory and let main() report a missing R10 cleanly instead of
# raising IndexError at import time.
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent
DEFAULT_R10 = REPO_ROOT / "docs" / "spec" / "0.3-R10.md"

FAILURES: list[str] = []
NOTES: list[str] = []


def check(cond: bool, msg: str) -> bool:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAILURES.append(msg)
    return cond


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"  NOTE  {msg}")


def fenced_blocks(text: str, start: int, end: int) -> list[str]:
    """Return the ~~~text ... ~~~ bodies between two character offsets."""
    region = text[start:end]
    return re.findall(r"~~~text\n(.*?)~~~", region, flags=re.S)


def section_bounds(text: str, heading: str) -> tuple[int, int]:
    """Return [start, end) of a markdown section.

    The section ends at the next heading of the same or higher level. The level
    is the count of leading '#' characters, NOT the first whitespace-separated
    token of the heading text -- headings like "## 8. Canonical 数据模型" have a
    space right after the hashes, so tokenising the text yielded an empty level
    and the bounds silently ran to the end of the document. That made several
    groups pass only by luck (they happened to consume the first fenced block,
    or matched a substring anywhere later in the file). Exposed when R10-fix-10
    added a second `task_claims` row: the over-wide region let the section 37.6
    row overwrite the section 8 one.
    """
    m = re.search(rf"^{re.escape(heading)}\s*$", text, flags=re.M)
    if not m:
        raise SystemExit(f"heading not found: {heading}")
    rest = text[m.end() :]
    level = len(heading) - len(heading.lstrip("#"))
    nxt = re.search(rf"^#{{1,{level}}}\s", rest, flags=re.M)
    end = m.end() + (nxt.start() if nxt else len(rest))
    return m.end(), end


def parse_r10(path: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    text = path.read_text(encoding="utf-8")

    s10, e10 = section_bounds(text, "## 10. 状态机")
    blocks = fenced_blocks(text, s10, e10)
    states = [ln.strip() for ln in blocks[0].splitlines() if ln.strip()]

    s37, e37 = section_bounds(text, "### 37.7 事件 allowlist（DOC-STATE-001 输入）")
    blocks37 = fenced_blocks(text, s37, e37)
    if len(blocks37) < 3:
        raise SystemExit(f"expected 3 allowlist blocks in 37.7, got {len(blocks37)}")
    m_set = blocks37[0].split()
    o_set = blocks37[1].split()
    r_set = blocks37[2].split()
    return states, m_set, o_set, r_set


def walk(node, refs: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                refs.add(v)
            else:
                walk(v, refs)
    elif isinstance(node, list):
        for v in node:
            walk(v, refs)


def walk_code_enums(node, path: str, out: set[str]) -> None:
    """Collect enum values that sit under a property literally named `code`.

    Descriptions are prose and not normative; only enumerated `code` properties
    constrain the wire format, so only those are cross-checked against
    error-codes.md.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}"
            if k == "enum" and path.endswith(".code") and isinstance(v, list):
                out.update(str(x) for x in v)
            else:
                walk_code_enums(v, child, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_code_enums(v, f"{path}[{i}]", out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--r10", type=Path, default=DEFAULT_R10)
    ap.add_argument("--here", type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()

    oapi_path = args.here / "openapi.yaml"
    err_path = args.here / "error-codes.md"

    print(f"R10      : {args.r10}")
    print(f"contract : {oapi_path}")
    print()

    for required in (args.r10, oapi_path, err_path, args.here / "idempotency.md"):
        if not required.exists():
            print(f"missing required input: {required}")
            print("If this script was copied elsewhere, pass --r10 explicitly.")
            return 2

    states, m_set, o_set, r_set = parse_r10(args.r10)
    allow = m_set + o_set + r_set

    print("== A. R10 truth source, counted from the actual elements ==")
    check(
        len(states) == len(set(states)),
        f"section 10 state block has no duplicates ({len(states)} entries)",
    )
    check(len(m_set) == len(set(m_set)), f"M set has no duplicates ({len(m_set)})")
    check(len(o_set) == len(set(o_set)), f"O set has no duplicates ({len(o_set)})")
    check(len(r_set) == len(set(r_set)), f"R set has no duplicates ({len(r_set)})")
    check(not (set(m_set) & set(o_set)), "M and O are disjoint")
    check(not (set(m_set) & set(r_set)), "M and R are disjoint")
    check(not (set(o_set) & set(r_set)), "O and R are disjoint")
    union = set(allow)
    check(
        len(union) == len(m_set) + len(o_set) + len(r_set),
        f"union size equals |M|+|O|+|R| = {len(union)}",
    )
    print()
    print(
        f"  counted: states={len(states)}  M={len(m_set)}  O={len(o_set)}  R={len(r_set)}  union={len(union)}"
    )
    if len(states) != 24:
        note(
            f"R10 section 10 lists {len(states)} states, but checklist H-1 claims 24. "
            "See README Q-1 -- this is exactly the hand-written-number problem "
            "R10 section 37.7 warns about."
        )
    print()

    doc = yaml.safe_load(oapi_path.read_text(encoding="utf-8"))
    schemas = doc.get("components", {}).get("schemas", {})

    print("== B. openapi.yaml is loadable and structurally sane ==")
    check(isinstance(doc, dict), "YAML parses to a mapping")
    check(
        doc.get("openapi", "").startswith("3.1"),
        f"openapi version is 3.1.x (got {doc.get('openapi')})",
    )
    check(
        bool(doc.get("paths")), f"paths present ({len(doc.get('paths', {}))} entries)"
    )
    check(bool(schemas), f"component schemas present ({len(schemas)} entries)")

    refs: set[str] = set()
    walk(doc, refs)
    root = doc.get("components", {})
    broken = []
    for r in sorted(refs):
        if not r.startswith("#/components/"):
            broken.append(r)
            continue
        parts = r.lstrip("#/").split("/")
        if parts[1] not in root or parts[2] not in root[parts[1]]:
            broken.append(r)
    check(
        not broken,
        f"all $ref targets resolve ({len(refs)} distinct refs)"
        + (f" -- broken: {broken}" if broken else ""),
    )

    servers = doc.get("servers", [])
    check(
        all(
            "127.0.0.1" in s.get("url", "") or "localhost" in s.get("url", "")
            for s in servers
        ),
        "every declared server URL is loopback-only (R10 section 5)",
    )
    print()

    print("== C. TaskStatus enum == R10 section 10 ==")
    ts = schemas.get("TaskStatus", {}).get("enum", [])
    check(len(ts) == len(set(ts)), "TaskStatus enum has no duplicates")
    check(
        set(ts) == set(states),
        f"TaskStatus enum set equals the section 10 state set ({len(ts)} vs {len(states)})",
    )
    missing = sorted(set(states) - set(ts))
    extra = sorted(set(ts) - set(states))
    if missing:
        print(f"        missing from contract: {missing}")
    if extra:
        print(f"        invented by contract : {extra}")
    check(
        "child_created" not in ts,
        "child_created is NOT in TaskStatus (R10 37.7 rule 4 calls it a split-only pseudo-state)",
    )
    print()

    print("== D. EventName enum == R10 section 37.7 M/O/R ==")
    ev = schemas.get("EventName", {}).get("enum", [])
    check(
        len(ev) == len(set(ev)), f"EventName enum has no duplicates ({len(ev)} entries)"
    )
    check(
        set(ev) == union,
        f"EventName enum set equals the 37.7 union ({len(set(ev))} vs {len(union)})",
    )
    ev_missing = sorted(union - set(ev))
    ev_extra = sorted(set(ev) - union)
    if ev_missing:
        print(f"        missing from contract: {ev_missing}")
    if ev_extra:
        print(f"        invented by contract : {ev_extra}")
    check(len(ev) == len(union), f"EventName enum length equals union size ({len(ev)})")
    print()

    print("== E. section 7 verb coverage ==")
    r10_text = args.r10.read_text(encoding="utf-8")
    s7, e7 = section_bounds(r10_text, "## 7. Adapter 接口")
    b7 = fenced_blocks(r10_text, s7, e7)
    pull_verbs = []
    for line in b7[0].splitlines():
        mm = re.match(r"^([a-z_]+)\(", line.strip())
        if mm:
            pull_verbs.append(mm.group(1))
    print(f"  R10 section 7 pull verbs ({len(pull_verbs)}): {' '.join(pull_verbs)}")
    paths = doc.get("paths", {})
    ops = {}
    for p, item in paths.items():
        for method, op in item.items():
            if isinstance(op, dict) and "operationId" in op:
                ops[op["operationId"]] = f"{method.upper()} {p}"
    absent = [v for v in pull_verbs if v not in ops]
    check(
        not absent,
        "every section 7 verb has an operationId"
        + (f" -- missing: {absent}" if absent else ""),
    )
    for p in paths:
        if not p.startswith("/adapter/"):
            continue
        tail = p.split("/")[2]
        ok = tail in pull_verbs or tail == "context"
        check(ok, f"adapter path tail '{tail}' matches a section 7 verb spelling")
    print()

    print("== F. section 21 verb coverage ==")
    s21, e21 = section_bounds(r10_text, "## 21. Review 和 Merge Gate")
    b21 = fenced_blocks(r10_text, s21, e21)
    v21 = []
    for line in b21[0].splitlines():
        mm = re.match(r"^([a-z]+)\.([a-z_]+)\(", line.strip())
        if mm:
            v21.append(f"{mm.group(1)}.{mm.group(2)}")
    print(f"  R10 section 21 verbs ({len(v21)}): {' '.join(v21)}")
    expected_ops = {
        "planner.create_plan": "planner_create_plan",
        "planner.validate_plan": "planner_validate_plan",
        "planner.submit_plan": "planner_submit_plan",
        "review.claim": "review_claim",
        "review.submit": "review_submit",
        "git.publish_task_branch": "publish_task_branch",
    }
    for verb in v21:
        op = expected_ops.get(verb)
        check(
            op is not None and op in ops,
            f"section 21 verb '{verb}' is mapped"
            + ("" if op in ops else f" (expected operationId {op})"),
        )
    print()

    print("== G. error codes: contract enumerates only defined codes ==")
    err_text = err_path.read_text(encoding="utf-8")
    defined = set(
        re.findall(r"^\|\s*`([a-z_]+\.[a-z_0-9]+)`\s*\|", err_text, flags=re.M)
    )
    print(f"  error-codes.md defines {len(defined)} codes")
    cited: set[str] = set()
    walk_code_enums(doc, "", cited)
    print(f"  openapi.yaml enumerates {len(cited)} codes under a `code` property")
    undefined = sorted(c for c in cited if c not in defined)
    check(
        bool(cited),
        "the contract does enumerate error codes somewhere (guard against a vacuous pass)",
    )
    check(
        not undefined,
        "every enumerated error code is defined in error-codes.md"
        + (f" -- undefined: {undefined}" if undefined else ""),
    )
    print()

    print("== H. error-codes.md cites only allowlisted event names ==")
    cited_events = set(re.findall(r"`([a-z_]+(?:\.[a-z_]+)?)`\((M|O|R)\)", err_text))
    bad = []
    for name, kind in cited_events:
        pool = {"M": m_set, "O": o_set, "R": r_set}[kind]
        if name not in pool:
            bad.append(f"{name}(claimed {kind})")
    check(
        not bad,
        f"all {len(cited_events)} event citations carry the correct M/O/R tag"
        + (f" -- wrong: {bad}" if bad else ""),
    )
    print()

    print("== I. no hand-written allowlist counts anywhere in the contract ==")
    contract_text = oapi_path.read_text(encoding="utf-8") + err_text
    hardcoded = re.findall(r"\b(?:88|74|13)\s*(?:项|items|entries)", contract_text)
    if hardcoded:
        note(
            f"found prose mentions of counts {sorted(set(hardcoded))}; acceptable in "
            "descriptions but must never be an assertion constant (R10 37.7 methodology)"
        )
    else:
        check(True, "no count strings used as assertions")
    print()

    print("== J. R10-fix-09: per-task claim idempotency stays on task_claims ==")
    # The contract's first draft put the composite key on claim_requests, which is
    # the exact defect R10-fix-09 corrected. These assertions stop a regression in
    # the artifact itself. They do NOT police R10 itself; README Q-6 (now closed
    # by R10-fix-10) documents the spec-side gap they were written alongside.
    s8, e8 = section_bounds(r10_text, "## 8. Canonical 数据模型")
    check(
        "## 9." not in r10_text[s8:e8] and "## 11." not in r10_text[s8:e8],
        "section 8 bounds stop before section 9 (section_bounds regression guard)",
    )
    s8_rows = {
        m.group(1): m.group(0)
        for m in re.finditer(r"^\|\s*([a-z_]+)\s*\|.*$", r10_text[s8:e8], flags=re.M)
    }
    cr_row = s8_rows.get("claim_requests", "")
    check(bool(cr_row), "section 8 has a claim_requests row to inspect")
    check(
        cr_row.count("unique(") == 0 or "task_id" not in cr_row.split("|")[2],
        "claim_requests row declares no composite key involving task_id",
    )
    cr_fields = cr_row.split("|")[3] if cr_row.count("|") >= 3 else ""
    check(
        not re.search(r"\btask_id\b", cr_fields),
        "claim_requests key-column cell does not declare a task_id column",
    )
    # R10-fix-10 (2026-10-05) closed the Q-6 gap: task_claims now has a section 8
    # minimum-definition row, and the §37.6 merged cell was split so the
    # schema-keys assertion group covers it. This was previously a sentinel that
    # asserted the gap EXISTED; it is now a positive assertion on the fix.
    tc_row = s8_rows.get("task_claims", "")
    check(bool(tc_row), "section 8 now has a task_claims row (R10-fix-10 closed Q-6)")
    if tc_row:
        check(
            "unique(claim_request_id, task_id)" in tc_row,
            "task_claims row declares unique(claim_request_id, task_id)",
        )
        tc_key_cell = tc_row.split("|")[2]
        tc_field_cell = tc_row.split("|")[3]
        check(
            re.search(r"\btask_id\b", tc_key_cell) is not None
            and re.search(r"\btask_id\b", tc_field_cell) is not None,
            "task_claims row declares task_id in both the key and field cells "
            "(the exact cross-cell defect schema-keys was added to catch)",
        )
        check(
            "失败项" in tc_row,
            "task_claims row keeps the failed-item-still-writes-a-row rule",
        )
    s11, e11 = section_bounds(r10_text, "## 11. Claim、批量 Claim 和幂等")
    s11_text = r10_text[s11:e11]
    check(
        "不从 JSON 里反解" in s11_text,
        "section 11 forbids decoding per-task results from first_result_json",
    )
    check(
        "失败项同样留行" in s11_text,
        "section 11 requires a task_claims row even for failed items",
    )
    idem_text = (args.here / "idempotency.md").read_text(encoding="utf-8")
    check(
        "task_claims" in idem_text and "claim_requests" in idem_text,
        "idempotency.md names both claim_requests and task_claims",
    )
    bad_placement = re.findall(
        r"`claim_requests`\s*\|\s*`unique\(claim_request_id", idem_text
    )
    check(
        not bad_placement,
        "idempotency.md never places the composite claim key on claim_requests",
    )
    print()

    print("=" * 62)
    if NOTES:
        print(f"notes    : {len(NOTES)}")
        for n in NOTES:
            print(f"  - {n}")
    if FAILURES:
        print(f"FAILURES : {len(FAILURES)}")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("FAILURES : 0")
    print(
        "contract is a faithful projection of R10 sections 7 / 8 / 10 / 11 / 21 / 37.7"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
