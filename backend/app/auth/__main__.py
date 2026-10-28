"""Issue, revoke and list subject tokens: ``python -m app.auth ...``.

Same module-entry-point pattern as the other console tools. The plaintext
token is printed once at issuance; only its hash is stored.

Operator note (auth-layer card): enforcement is ``AP_AUTH_MODE=enforce``;
the default ``off`` keeps every route exactly as it was, so tokens issued
here are inert until the switch is flipped -- and before flipping it, the
frontend needs its human token wired in (recorded gap).
"""

from __future__ import annotations

import argparse
import json
import sys

from app.auth.tokens import AuthTokenError, TokenService, UnknownHarness
from app.config import settings
from app.db import Database


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.auth", description="subject tokens (R10 §17)")
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue", help="create a token; the plaintext prints once")
    issue.add_argument("--type", required=True, choices=["harness", "human"])
    issue.add_argument("--id", required=True, dest="subject_id")
    issue.add_argument("--label", default="")

    revoke = commands.add_parser(
        "revoke", help="revoke a token by its plaintext or by the fingerprint `list` prints"
    )
    target = revoke.add_mutually_exclusive_group(required=True)
    target.add_argument("--token", help="the plaintext, if it is still at hand")
    target.add_argument("--fingerprint", help="sha256:<hex> prefix, as `list` prints it")
    revoke.add_argument("--type", choices=["harness", "human"], default=None)
    revoke.add_argument("--id", dest="subject_id", default=None)
    revoke.add_argument("--reason", default="")

    listing = commands.add_parser("list", help="list tokens (no secrets)")
    listing.add_argument("--type", choices=["harness", "human"], default=None)
    listing.add_argument("--id", dest="subject_id", default=None)

    args = parser.parse_args(argv)

    database = Database()
    if not database.path.exists():
        _emit({"refused": f"no database at {database.path}; run `python -m app.db` first"})
        return 2

    service = TokenService(database)
    if args.command == "issue":
        try:
            raw = service.issue(
                subject_type=args.type, subject_id=args.subject_id, label=args.label
            )
        except UnknownHarness as exc:
            _emit({"refused": str(exc)})
            return 2
        except AuthTokenError as exc:
            _emit({"refused": str(exc)})
            return 2
        _emit(
            {
                "issued": {
                    "subject_type": args.type,
                    "subject_id": args.subject_id,
                    "label": args.label,
                    "token": raw,
                    "note": "shown once; only its hash is stored",
                    "enforcement": settings.auth_mode,
                }
            }
        )
        return 0

    if args.command == "revoke":
        if args.fingerprint:
            # 凭据丢了（或换过 key 文件）也能收回：指纹是哈希前缀，`list` 印的就是它。
            try:
                revoked = service.revoke_fingerprint(
                    fingerprint=args.fingerprint,
                    reason=args.reason,
                    subject_type=args.type,
                    subject_id=args.subject_id,
                )
            except AuthTokenError as exc:
                _emit({"refused": str(exc)})
                return 2
        else:
            revoked = service.revoke(raw=args.token, reason=args.reason)
        _emit({"revoked": revoked, "reason": args.reason})
        return 0 if revoked else 1

    _emit({"tokens": service.list_tokens(subject_type=args.type, subject_id=args.subject_id)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
