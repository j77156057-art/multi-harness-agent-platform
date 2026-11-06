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
from collections.abc import Callable, Mapping
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

    def as_mapping(self) -> dict[str, object]:
        """Serialise back to the policy JSON shape (for repo-side merging)."""
        return {
            "version": self.version,
            "command_roots": sorted(self.command_roots),
            "profiles": {
                name: {
                    "command": list(profile.command),
                    "timeout_seconds": profile.timeout_seconds,
                    "max_verify_attempts": profile.max_verify_attempts,
                    "allow_network": profile.allow_network,
                    "allowed_secrets": sorted(profile.allowed_secrets),
                    "lockfile_mutating": profile.lockfile_mutating,
                }
                for name, profile in self.profiles.items()
            },
        }

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


#: 命令里那些「仓库内文件」常见的后缀。只认这些，是为了不把
#: `pytest -q`、`npm run build` 这类没有路径的命令误判成缺失入口。
_SCRIPT_SUFFIXES = (
    ".py",
    ".pyw",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".sh",
    ".ps1",
    ".rb",
    ".pl",
)


def profile_entry_paths(profile: VerifyProfile) -> list[str]:
    """The repository-relative files a profile command needs to exist.

    2026-11-05 的实际事故（FD-045eb96438124aae）：合并闸门跑平台自带的
    ``docs-check``（``python backend/app/verify/docs_profile.py``），而被测
    仓库的合并前后两棵树里都没有这个脚本，于是每次都是 ``exit 2``。命令本身
    没错，错在**没人先问一句「这个仓库里有这个入口吗」**。这里把入口文件从
    argv 里抽出来，approve 阶段就能问这一句。

    只抽**看起来像仓库内脚本**的 token：跳过 flag、环境赋值、变量/通配符、
    URL 与绝对路径。抽不到就返回空 —— 宁可漏报（这条检查只是第一道闸），
    也不要把 ``python -c`` 的代码串当成文件。
    """
    entries: list[str] = []
    for token in profile.command[1:]:
        text = str(token).strip()
        if not text or text.startswith("-"):
            continue
        if any(marker in text for marker in ("=", "$", "%", "{", "}", "*", "?", "://")):
            continue
        if text.startswith(("/", "\\")) or (len(text) > 1 and text[1] == ":"):
            continue
        lowered = text.lower()
        if not lowered.endswith(_SCRIPT_SUFFIXES):
            continue
        normalized = text.replace("\\", "/")
        if normalized not in entries:
            entries.append(normalized)
    return entries


def missing_profile_entries(
    policy: VerifyPolicy, name: str | None, exists: Callable[[str], bool]
) -> list[str]:
    """Which of ``name``'s entry files are absent according to ``exists``."""
    if name is None or name not in policy.profiles:
        return []
    return [path for path in profile_entry_paths(policy.profiles[name]) if not exists(path)]


@dataclass(frozen=True, slots=True)
class ProfileResolution:
    """Where a merge check's profile came from, and where it will run.

    ``repo_source`` 是**目标工作树**的路径（仓库自带 ``verify-profiles.json``
    时），不是平台仓库的路径；``execution_dir`` 是命令真正的工作目录。
    两者都进事件 payload：只有「读错仓库」和「仓库确实缺入口」在日志里能
    分开，revert 才不会变成一种盲目的仪式。
    """

    name: str | None
    profile: VerifyProfile | None
    repo_source: str | None
    policy_file: str | None
    execution_dir: str
    origin: str

    def as_payload(self) -> dict[str, object]:
        return {
            "integration_profile": self.name,
            "repo_source": self.repo_source,
            "policy_file": self.policy_file,
            "execution_dir": self.execution_dir,
            "profile_origin": self.origin,
        }


def resolve_worktree_policy(
    platform_policy: VerifyPolicy,
    *,
    platform_source: Path | str | None,
    integration_profile: str | None,
    worktree: Path | str,
) -> ProfileResolution:
    """Resolve the profile against **the tree the command will run in**.

    ``load_runtime_policy`` 读的是平台仓库 ``integration`` 里的 profile；合并闸门
    跑的却是合并提交的 overlay。两者不是同一棵树，2026-11-05 的 045e 就是在这
    个缝里读到了平台自己的 ``docs-check``。这里只读 ``worktree``：

    * 树里有 ``verify-profiles.json`` → 它是 profile 的来源，``profile_origin``
      为 ``repository``；
    * 没有 → 用平台的 profile 兜底，``profile_origin`` 为 ``platform``。

    无论哪种，``repo_source`` 都是**这棵树**的路径，绝不是平台仓库的路径 ——
    045e 的 payload 之所以看不出问题，就是因为它写的是 ``null``：既不能证明
    "检查跑在哪"，也让「读错仓库」和「仓库确实缺入口」长得一模一样。
    """
    root = Path(worktree)
    name = integration_profile
    policy = platform_policy
    repo_source: str | None = str(root)
    policy_file = str(platform_source) if platform_source is not None else None
    origin = "platform"
    repo_file = root / "verify-profiles.json"
    if repo_file.is_file():
        try:
            raw = json.loads(repo_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VerifyPolicyError(f"repository policy {repo_file} is unreadable: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise VerifyPolicyError(f"repository policy {repo_file} must be a JSON object")
        merged = platform_policy.as_mapping()
        base_profiles = dict(merged["profiles"])  # type: ignore[arg-type]
        merged["profiles"] = {**base_profiles, **dict(raw.get("profiles") or {})}
        merged["version"] = str(raw.get("version", platform_policy.version))
        policy = VerifyPolicy.from_mapping(merged)
        declared = raw.get("integration_profile")
        name = str(declared).strip() if declared else integration_profile
        repo_source = str(root)
        policy_file = str(repo_file)
        origin = "repository"
    if name is None:
        name = next(iter(policy.profiles), None)
    if name is not None and name not in policy.profiles:
        raise VerifyPolicyError(
            f"integration_profile {name!r} is not declared in {policy_file}; "
            f"registered: {sorted(policy.profiles)}"
        )
    return ProfileResolution(
        name=name,
        profile=policy.profiles[name] if name is not None else None,
        repo_source=repo_source,
        policy_file=policy_file,
        execution_dir=str(root),
        origin=origin,
    )
