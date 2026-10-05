"""The Adapter interface of R10 §7, written once.

R10 §7 defines a pull interface: a Harness asks for work rather than being
spawned. This module is the client-side mirror of its 16 verbs.

It exists as an interface rather than only as the Fake Harness because the
checklist's claim -- that 10a running proves the platform's own collaboration
ability while real adapters only prove integration -- holds only if the 10b
adapters implement the same surface. A fake that invented its own shape would
prove nothing about what a real adapter has to satisfy.

The 16 verbs are also checked against the spec text by a test: it parses §7's code
block and asserts every verb has a method here. R10 is the truth source for the
interface, not this file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HarnessRegistration:
    """R10 §6's registration record.

    ``registration_version`` is what a Claim pins; R10 §6 requires registration
    changes to keep history so the pinned version can be looked up later.
    """

    harness_id: str
    name: str
    provider: str
    adapter: str
    adapter_version: str
    capabilities: tuple[str, ...]
    max_parallel_tasks: int = 1
    status: str = "online"
    running_tasks: int = 0
    registration_version: str = "reg-001"

    def as_json(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "name": self.name,
            "provider": self.provider,
            "adapter": self.adapter,
            "adapter_version": self.adapter_version,
            "capabilities": list(self.capabilities),
            "max_parallel_tasks": self.max_parallel_tasks,
            "status": self.status,
            "running_tasks": self.running_tasks,
            "registration_version": self.registration_version,
        }


class HarnessAdapter(ABC):
    """One method per R10 §7 verb, with the spec's own argument names.

    Deliberately not a Protocol: the point is that an implementation must state
    it satisfies the surface, and that the surface is enumerable by a test.
    """

    # --- §7, in the order the spec lists them ---------------------------------
    @abstractmethod
    def probe(self, *, adapter_version: str, host_mode: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def capabilities(self) -> dict[str, Any]: ...

    @abstractmethod
    def list_ready_tasks(
        self, *, visibility: str = "authorized", group_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]: ...

    @abstractmethod
    def claim(
        self,
        *,
        claim_request_id: str,
        task_ids: list[str],
        payload_hash: str | None = None,
        partial_allowed: bool = False,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def heartbeat(
        self,
        *,
        task_id: str,
        attempt_id: str,
        lease_token: str,
        fencing_epoch: int,
        progress_hash: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def read_context(self, *, task_id: str, since_seq: int = 0) -> dict[str, Any]: ...

    @abstractmethod
    def submit_result(
        self,
        *,
        task_id: str,
        attempt_id: str,
        fencing_epoch: int,
        completion: dict[str, Any],
    ) -> dict[str, Any]: ...

    @abstractmethod
    def request_help(self, *, task_id: str, reason: str) -> dict[str, Any]: ...

    @abstractmethod
    def report_blocked(self, *, task_id: str, blocker_ref: str) -> dict[str, Any]: ...

    @abstractmethod
    def accept_help(self, *, task_id: str, help_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def reject_help(self, *, task_id: str, help_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def split_task(
        self, *, task_id: str, child_specs: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def ask_human(self, *, task_id: str, question: str) -> dict[str, Any]: ...

    @abstractmethod
    def abandon(self, *, task_id: str, reason: str) -> dict[str, Any]: ...

    @abstractmethod
    def release(self, *, task_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def publish_task_branch(
        self,
        *,
        task_id: str,
        attempt_id: str,
        branch: str,
        commit: str,
        fencing_epoch: int,
        idempotency_key: str,
    ) -> dict[str, Any]: ...
