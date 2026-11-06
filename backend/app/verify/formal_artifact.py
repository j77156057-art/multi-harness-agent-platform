"""The single place that turns a task's ``exclusive_paths`` into a verify profile.

``formal-artifact`` is not a static profile: its command carries the task's own
``exclusive_paths`` as a JSON argument, so the policy can only be built once the
task contract is known.  The formal worker used to build it inline while the
runtime driver only knew the static registry, so the same task was verified by
one path and refused by the other ("verify profile 'formal-artifact' is not in
the runtime policy").  Both callers now import this function.

Where it lives: ``app/verify/`` because both callers already depend on this
package (``app.verify.profile`` / ``app.verify.worker``) and neither direction
creates a cycle -- the module reads the checker's path from ``PROJECT_ROOT`` and
imports nothing from ``app.adapter``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from app.config import PROJECT_ROOT
from app.verify.profile import VerifyPolicy, VerifyPolicyError

#: The profile name the dispatch snapshot freezes (``dispatch.py`` /
#: ``routes.py``) and both verify entry points must agree on.
PROFILE_NAME = "formal-artifact"

#: The standalone checker the profile executes.  It lives next to the adapter
#: that produces the deliverable it inspects (``app/adapter``), so the scope
#: rule stays shared with the formal worker.
VERIFY_SCRIPT = PROJECT_ROOT / "backend" / "app" / "adapter" / "verify_artifact.py"

TIMEOUT_SECONDS = 30


class FormalArtifactUnavailable(VerifyPolicyError):
    """The task's ``exclusive_paths`` cannot name a runnable check.

    Callers must treat this as "skip, and say why" -- not as a failing verify.
    """


def _clean_exclusive_paths(exclusive_paths: object) -> list[str]:
    """Validate the contract fields the command will consume.

    The accepted spelling mirrors what dispatch already guarantees
    (``app/adapter/dispatch.py``): normalized relative POSIX paths.  Anything
    else cannot be handed to the checker without guessing what the author
    meant, so it is refused instead of run.
    """
    if isinstance(exclusive_paths, str | bytes) or not isinstance(
        exclusive_paths, Iterable
    ):
        raise FormalArtifactUnavailable("exclusive_paths is not a list of paths")
    cleaned: list[str] = []
    for item in exclusive_paths:
        if not isinstance(item, str):
            raise FormalArtifactUnavailable("exclusive_paths contains a non-string entry")
        path = item.strip()
        if not path:
            raise FormalArtifactUnavailable("exclusive_paths contains an empty entry")
        if path.startswith("/") or ":" in path or "\\" in path or ".." in path.split("/"):
            raise FormalArtifactUnavailable(
                f"exclusive path {path!r} is not a normalized relative path"
            )
        cleaned.append(path)
    if not cleaned:
        raise FormalArtifactUnavailable("exclusive_paths is empty")
    return cleaned


def build_formal_artifact_policy(exclusive_paths: object) -> VerifyPolicy:
    """Build the policy that makes ``VerifyWorker.run`` able to run the profile.

    The returned policy carries exactly one profile, named :data:`PROFILE_NAME`,
    so it can be passed straight to ``VerifyWorker`` together with that name.
    """
    paths = _clean_exclusive_paths(exclusive_paths)
    return VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                PROFILE_NAME: {
                    "command": ["python", str(VERIFY_SCRIPT), json.dumps(paths)],
                    "timeout_seconds": TIMEOUT_SECONDS,
                }
            },
        }
    )
