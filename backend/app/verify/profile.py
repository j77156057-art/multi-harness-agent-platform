"""Verify profiles and the human-maintained command policy (R10 §16).

R10 §16: "verify 命令根必须来自仓库侧人类维护的 policy allowlist。Planner 只能
填写允许的参数和测试路径。" That sentence is the reason this module separates the
*command root* from the *profile*: a profile may choose arguments and test paths,
but its ``command[0]`` has to appear in ``command_roots``, which is the part a
human maintains.

Commands are argv tuples, not shell strings. A shell string would make the
allowlist advisory, because ``sh -c`` can express anything.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT


class VerifyPolicyError(ValueError):
    """The policy or a profile is unusable."""


@dataclass(frozen=True, slots=True)
class VerifyProfile:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int = 1800
    max_verify_attempts: int = 2
    allow_network: bool = False
    allowed_secrets: frozenset[str] = frozenset()
    lockfile_mutating: bool = False

    def validate(self, command_roots: frozenset[str]) -> list[str]:
        errors: list[str] = []
        if not self.command:
            errors.append(f"profile {self.name!r} has an empty command")
        elif self.command[0] not in command_roots:
            errors.append(
                f"profile {self.name!r} uses command root {self.command[0]!r}, "
                f"which is not in the human-maintained allowlist"
            )
        if self.timeout_seconds < 1:
            errors.append(f"profile {self.name!r} has a non-positive timeout")
        return errors


@dataclass(frozen=True, slots=True)
class VerifyPolicy:
    """The repo-side, human-maintained source of verify commands."""

    command_roots: frozenset[str]
    profiles: Mapping[str, VerifyProfile]
    version: str = "1"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> VerifyPolicy:
        roots = frozenset(str(item) for item in raw.get("command_roots", ()))  # type: ignore[union-attr]
        if not roots:
            raise VerifyPolicyError("command_roots must not be empty")
        profiles: dict[str, VerifyProfile] = {}
        for name, spec in dict(raw.get("profiles", {})).items():
            if not isinstance(spec, Mapping):
                raise VerifyPolicyError(f"profile {name!r} must be a mapping")
            command = tuple(str(part) for part in spec.get("command", ()))
            profiles[str(name)] = VerifyProfile(
                name=str(name),
                command=command,
                timeout_seconds=int(spec.get("timeout_seconds", 1800)),
                max_verify_attempts=int(spec.get("max_verify_attempts", 2)),
                allow_network=bool(spec.get("allow_network", False)),
                allowed_secrets=frozenset(str(item) for item in spec.get("allowed_secrets", ())),
                lockfile_mutating=bool(spec.get("lockfile_mutating", False)),
            )
        policy = cls(
            command_roots=roots,
            profiles=profiles,
            version=str(raw.get("version", "1")),
        )
        errors = [error for profile in profiles.values() for error in profile.validate(roots)]
        if errors:
            raise VerifyPolicyError("; ".join(errors))
        return policy

    @classmethod
    def from_file(cls, path: Path | str) -> VerifyPolicy:
        text = Path(path).read_text(encoding="utf-8")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VerifyPolicyError(f"verify policy is not valid JSON: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise VerifyPolicyError("verify policy must be a JSON object")
        return cls.from_mapping(raw)

    def profile(self, name: str) -> VerifyProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise VerifyPolicyError(
                f"unknown verify profile {name!r}; registered: {sorted(self.profiles)}"
            ) from exc

    def fingerprint(self) -> str:
        """A stable hash of the policy, for the receipt's verify_env_hash."""
        payload = {
            "version": self.version,
            "command_roots": sorted(self.command_roots),
            "profiles": {
                name: {
                    "command": list(profile.command),
                    "timeout_seconds": profile.timeout_seconds,
                    "allow_network": profile.allow_network,
                    "lockfile_mutating": profile.lockfile_mutating,
                    "allowed_secrets": sorted(profile.allowed_secrets),
                }
                for name, profile in sorted(self.profiles.items())
            },
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


#: The repo-side, human-maintained allowlist. Adding a root or a profile is a
#: human edit to this file, not something a Planner or Harness can do.
DEFAULT_POLICY_PATH = PROJECT_ROOT / "verify-profiles.json"


def load_default_policy() -> VerifyPolicy:
    return VerifyPolicy.from_file(DEFAULT_POLICY_PATH)
