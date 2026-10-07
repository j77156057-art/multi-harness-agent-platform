"""R10 §7 over the scheduler's REST surface.

The adapter remembers the attempt context each ``claim`` returned. §7's
signatures for heartbeat / release / abandon name only the task, while the API
needs the attempt and the fencing epoch to authorise the write; a real harness
tracks its own attempts too, so keeping that state here is what lets the §7
argument names stay intact instead of being widened with transport concerns.

Two verbs gained their routes in MVP-10b, because the real adapters call them:
``list_ready_tasks`` (the pull model's entry point; ``visibility=redacted`` is
refused until R10 §17 defines redaction) and ``read_context`` (the task's
event page). ``ask_human`` followed in human-ops (the §18 decision surface).
Three verbs -- ``accept_help``, ``reject_help`` and ``split_task`` -- still
have no route, because MVP-08 covered the verbs its own card listed. They
raise :class:`UnsupportedVerb` rather than pretending, and the gap is recorded
in the MVP-10a card and in docs/traceability.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.adapter.protocol import HarnessAdapter, HarnessRegistration


class AdapterError(RuntimeError):
    """The scheduler refused a verb. ``code`` is the wire code when there is one."""

    def __init__(self, verb: str, status: int, body: Any) -> None:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = error.get("code", f"http.{status}")
        super().__init__(f"{verb} failed: {code} ({status})")
        self.verb = verb
        self.status = status
        self.code = code
        self.detail = error.get("message")


class UnsupportedVerb(NotImplementedError):
    """The verb is in R10 §7 but the scheduler has no route for it yet."""


class HttpTransport(Protocol):
    """Whatever the adapter posts through; ``httpx.Client`` and TestClient both fit."""

    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class AttemptContext:
    attempt_id: str
    fencing_epoch: int
    lease_token: str


class HttpAdapter(HarnessAdapter):
    def __init__(
        self,
        client: HttpTransport,
        registration: HarnessRegistration,
        *,
        prefix: str = "/api/v1",
    ) -> None:
        self.client = client
        self.registration = registration
        self.prefix = prefix
        self.attempts: dict[str, AttemptContext] = {}

    # ------------------------------------------------------------- transport

    def _post(self, verb: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(f"{self.prefix}{path}", json=payload)
        if response.status_code >= 400:
            raise AdapterError(verb, response.status_code, response.json())
        return dict(response.json())

    def _get(self, verb: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(f"{self.prefix}{path}", params=params or {})
        if response.status_code >= 400:
            raise AdapterError(verb, response.status_code, response.json())
        return dict(response.json())

    def _context(self, task_id: str) -> AttemptContext:
        try:
            return self.attempts[task_id]
        except KeyError as exc:
            raise AdapterError(
                "context", 0, {"error": {"code": "adapter.no_attempt", "message": task_id}}
            ) from exc

    # ------------------------------------------------------------------ §7

    def probe(
        self, *, adapter_version: str | None = None, host_mode: str | None = None
    ) -> dict[str, Any]:
        return self._post(
            "probe",
            "/adapter/probe",
            {
                "harness_id": self.registration.harness_id,
                "adapter": self.registration.adapter,
                "adapter_version": adapter_version or self.registration.adapter_version,
                "self_reported_capabilities": list(self.registration.capabilities),
                "host_mode": host_mode or "native_cli",
            },
        )

    def capabilities(self) -> dict[str, Any]:
        return self._get(
            "capabilities",
            "/adapter/capabilities",
            {"harness_id": self.registration.harness_id},
        )

    def list_ready_tasks(
        self, *, visibility: str = "authorized", group_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"visibility": visibility, "limit": limit}
        if group_id is not None:
            params["group_id"] = group_id
        return self._get("list_ready_tasks", "/adapter/list_ready_tasks", params)

    def claim(
        self,
        *,
        claim_request_id: str,
        task_ids: list[str],
        payload_hash: str | None = None,
        partial_allowed: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "claim_request_id": claim_request_id,
            "harness_id": self.registration.harness_id,
            "task_ids": task_ids,
            "partial_allowed": partial_allowed,
        }
        if payload_hash is not None:
            payload["payload_hash"] = payload_hash
        result = self._post("claim", "/adapter/claim", payload)
        for item in result.get("items", ()):
            if item.get("outcome") == "claimed" and item.get("attempt_id"):
                self.attempts[str(item["task_id"])] = AttemptContext(
                    attempt_id=str(item["attempt_id"]),
                    fencing_epoch=int(item["fencing_epoch"]),
                    lease_token=str(item["lease_token"] or ""),
                )
        return result

    def heartbeat(
        self,
        *,
        task_id: str,
        attempt_id: str | None = None,
        lease_token: str | None = None,
        fencing_epoch: int | None = None,
        progress_hash: str | None = None,
    ) -> dict[str, Any]:
        context = self._context(task_id)
        payload: dict[str, Any] = {
            "task_id": task_id,
            "attempt_id": attempt_id or context.attempt_id,
            "lease_token": lease_token or context.lease_token,
            "fencing_epoch": context.fencing_epoch if fencing_epoch is None else fencing_epoch,
        }
        if progress_hash is not None:
            payload["progress_hash"] = progress_hash
        return self._post("heartbeat", "/adapter/heartbeat", payload)

    def read_context(self, *, task_id: str, since_seq: int = 0) -> dict[str, Any]:
        return self._get("read_context", f"/adapter/context/{task_id}", {"since_seq": since_seq})

    def submit_result(
        self,
        *,
        task_id: str,
        attempt_id: str | None = None,
        fencing_epoch: int | None = None,
        completion: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._context(task_id)
        return self._post(
            "submit_result",
            "/adapter/submit_result",
            {
                "task_id": task_id,
                "attempt_id": attempt_id or context.attempt_id,
                "fencing_epoch": context.fencing_epoch if fencing_epoch is None else fencing_epoch,
                "completion": completion,
            },
        )

    def request_help(self, *, task_id: str, reason: str) -> dict[str, Any]:
        context = self._context(task_id)
        return self._post(
            "request_help",
            "/adapter/request_help",
            {
                "task_id": task_id,
                "attempt_id": context.attempt_id,
                "fencing_epoch": context.fencing_epoch,
                "reason": reason,
            },
        )

    def report_blocked(self, *, task_id: str, blocker_ref: str) -> dict[str, Any]:
        context = self._context(task_id)
        return self._post(
            "report_blocked",
            "/adapter/report_blocked",
            {
                "task_id": task_id,
                "attempt_id": context.attempt_id,
                "fencing_epoch": context.fencing_epoch,
                "blocker_ref": blocker_ref,
            },
        )

    def accept_help(self, *, task_id: str, help_id: str) -> dict[str, Any]:
        raise UnsupportedVerb("accept_help: no route yet (see the MVP-10a card)")

    def reject_help(self, *, task_id: str, help_id: str) -> dict[str, Any]:
        raise UnsupportedVerb("reject_help: no route yet (see the MVP-10a card)")

    def split_task(
        self, *, task_id: str, child_specs: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        raise UnsupportedVerb("split_task: no route yet (see the MVP-10a card)")

    def ask_human(self, *, task_id: str, question: str) -> dict[str, Any]:
        """R10 §7: the adapter's attempt context rides along as the §13 guard.

        Unlike ``release``/``abandon``, the task is not handed back: ownership
        is unchanged and the same attempt continues after the human resumes
        it, so the recorded context is kept.
        """
        context = self._context(task_id)
        return self._post(
            "ask_human",
            "/adapter/ask_human",
            {
                "task_id": task_id,
                "attempt_id": context.attempt_id,
                "fencing_epoch": context.fencing_epoch,
                "question": question,
                "harness_id": self.registration.harness_id,
            },
        )

    def abandon(self, *, task_id: str, reason: str) -> dict[str, Any]:
        context = self._context(task_id)
        result = self._post(
            "abandon",
            "/adapter/abandon",
            {
                "task_id": task_id,
                "attempt_id": context.attempt_id,
                "fencing_epoch": context.fencing_epoch,
                "reason": reason,
            },
        )
        self.attempts.pop(task_id, None)
        return result

    def release(self, *, task_id: str) -> dict[str, Any]:
        context = self._context(task_id)
        result = self._post(
            "release",
            "/adapter/release",
            {
                "task_id": task_id,
                "attempt_id": context.attempt_id,
                "fencing_epoch": context.fencing_epoch,
                "reason": "harness released",
            },
        )
        self.attempts.pop(task_id, None)
        return result

    def publish_task_branch(
        self,
        *,
        task_id: str,
        attempt_id: str | None = None,
        branch: str,
        commit: str,
        fencing_epoch: int | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        context = self._context(task_id)
        return self._post(
            "publish_task_branch",
            "/adapter/publish_task_branch",
            {
                "task_id": task_id,
                "attempt_id": attempt_id or context.attempt_id,
                "branch": branch,
                "commit": commit,
                "fencing_epoch": context.fencing_epoch if fencing_epoch is None else fencing_epoch,
                "idempotency_key": idempotency_key,
            },
        )
