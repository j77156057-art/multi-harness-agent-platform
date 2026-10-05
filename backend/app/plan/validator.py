"""Deterministic Plan/Task contract checks from R10 §9."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class PlanError(ValueError):
    """Raised when a plan is malformed or violates a configured limit."""


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    capabilities: frozenset[str] = frozenset()
    depends_on: tuple[str, ...] = ()
    exclusive_resources: tuple[str, ...] = ()
    outputs: tuple[Mapping[str, str], ...] = ()
    consumes: tuple[Mapping[str, str], ...] = ()
    acceptance: tuple[Mapping[str, Any], ...] = ()
    verify_profile: str | None = None
    risk: str = "medium"
    budget_tokens: int = 0
    expected_duration_min: int = 0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> TaskSpec:
        task_id = str(raw.get("id", "")).strip()
        return cls(
            task_id=task_id,
            capabilities=frozenset(str(x) for x in raw.get("capabilities", ())),
            depends_on=tuple(str(x) for x in raw.get("depends_on", ())),
            exclusive_resources=tuple(str(x) for x in raw.get("exclusive_resources", ())),
            outputs=tuple(raw.get("outputs", ())),
            consumes=tuple(raw.get("consumes", ())),
            acceptance=tuple(raw.get("acceptance", ())),
            verify_profile=raw.get("verify_profile"),
            risk=str(raw.get("risk", "medium")),
            budget_tokens=int(raw.get("budget_tokens", 0)),
            expected_duration_min=int(raw.get("expected_duration_min", 0)),
        )


@dataclass(frozen=True, slots=True)
class PlanSpec:
    plan_id: str
    tasks: tuple[TaskSpec, ...]
    max_tasks: int = 200
    max_acceptance: int = 1000
    max_budget_tokens: int = 10_000_000
    registered_capabilities: frozenset[str] = frozenset()
    registered_resources: frozenset[str] = frozenset()
    evidence_sources: frozenset[str] = frozenset()
    verify_profiles: frozenset[str] = frozenset()
    path_globs: tuple[str, ...] = ()
    max_path_globs: int = 5000

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> PlanSpec:
        limits = raw.get("limits", {})
        return cls(
            plan_id=str(raw.get("plan_id", raw.get("id", ""))).strip(),
            tasks=tuple(TaskSpec.from_mapping(item) for item in raw.get("tasks", ())),
            max_tasks=int(limits.get("max_tasks", 200)),
            max_acceptance=int(limits.get("max_acceptance", 1000)),
            max_budget_tokens=int(limits.get("max_budget_tokens", 10_000_000)),
            registered_capabilities=frozenset(
                str(x) for x in raw.get("registered_capabilities", ())
            ),
            registered_resources=frozenset(str(x) for x in raw.get("registered_resources", ())),
            evidence_sources=frozenset(str(x) for x in raw.get("evidence_sources", ())),
            verify_profiles=frozenset(str(x) for x in raw.get("verify_profiles", ())),
            path_globs=tuple(str(x) for x in raw.get("path_globs", ())),
            max_path_globs=int(limits.get("max_path_globs", 5000)),
        )


@dataclass(frozen=True, slots=True)
class ValidationResult:
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise PlanError("; ".join(self.errors))


class PlanValidator:
    """Validate a complete plan without writing any state."""

    def validate(self, plan: PlanSpec) -> ValidationResult:
        errors: list[str] = []
        if not plan.plan_id:
            errors.append("plan_id is required")
        if len(plan.tasks) > plan.max_tasks:
            errors.append(f"task count {len(plan.tasks)} exceeds limit {plan.max_tasks}")
        task_ids = [task.task_id for task in plan.tasks]
        duplicates = sorted({task_id for task_id in task_ids if task_ids.count(task_id) > 1})
        errors.extend(f"duplicate task id: {task_id}" for task_id in duplicates)
        known = set(task_ids)
        total_budget = 0
        acceptance_count = 0
        graph: dict[str, set[str]] = defaultdict(set)
        indegree = {task_id: 0 for task_id in known}
        outputs_by_task = {
            task.task_id: {
                (item.get("name"), item.get("type"), item.get("schema_version"))
                for item in task.outputs
            }
            for task in plan.tasks
            if task.task_id
        }
        for task in plan.tasks:
            if not task.task_id:
                errors.append("task id is required")
                continue
            if task.budget_tokens < 0 or task.expected_duration_min < 0:
                errors.append(f"task {task.task_id} has negative budget or duration")
            total_budget += task.budget_tokens
            acceptance_count += len(task.acceptance)
            missing = sorted(task.capabilities - plan.registered_capabilities)
            errors.extend(
                f"task {task.task_id} uses unregistered capability {name}" for name in missing
            )
            resource_missing = sorted(set(task.exclusive_resources) - plan.registered_resources)
            errors.extend(
                f"task {task.task_id} uses unregistered resource {name}"
                for name in resource_missing
            )
            if len(task.exclusive_resources) != len(set(task.exclusive_resources)):
                errors.append(f"task {task.task_id} repeats an exclusive resource")
            if task.verify_profile and task.verify_profile not in plan.verify_profiles:
                errors.append(
                    f"task {task.task_id} uses unregistered verify profile {task.verify_profile}"
                )
            # R10 section 9: a dependency edge declared twice is a malformed
            # plan, not a no-op. De-duplicate before graph construction so the
            # repeated edge cannot inflate indegree and fake a Kahn cycle.
            deps = tuple(dict.fromkeys(task.depends_on))
            if len(deps) != len(task.depends_on):
                errors.append(f"task {task.task_id} repeats a dependency edge")
            unknown_deps = sorted(set(deps) - known)
            errors.extend(
                f"task {task.task_id} depends on unknown task {dep}" for dep in unknown_deps
            )
            for dep in deps:
                if dep == task.task_id:
                    errors.append(f"task {task.task_id} has a self dependency")
                elif dep in known:
                    graph[dep].add(task.task_id)
                    indegree[task.task_id] += 1
            self._validate_contract(task, errors, outputs_by_task, plan.evidence_sources, deps)
        if total_budget > plan.max_budget_tokens:
            errors.append(f"budget {total_budget} exceeds limit {plan.max_budget_tokens}")
        if acceptance_count > plan.max_acceptance:
            errors.append(
                f"acceptance count {acceptance_count} exceeds limit {plan.max_acceptance}"
            )
        normalized_globs = [self._normalize_glob(glob, errors) for glob in plan.path_globs]
        if len(normalized_globs) > plan.max_path_globs:
            errors.append(
                f"path glob count {len(normalized_globs)} exceeds limit {plan.max_path_globs}"
            )
        if len(normalized_globs) != len(set(normalized_globs)):
            errors.append("path globs contain duplicates")
        if self._has_cycle(graph, indegree):
            errors.append("task dependency graph contains a cycle")
        return ValidationResult(tuple(dict.fromkeys(errors)))

    @staticmethod
    def _validate_contract(
        task: TaskSpec,
        errors: list[str],
        outputs_by_task: dict[str, set[tuple[Any, Any, Any]]],
        evidence_sources: frozenset[str],
        deps: tuple[str, ...] | None = None,
    ) -> None:
        dependencies = task.depends_on if deps is None else deps
        upstream_outputs = {
            output
            for dependency in dependencies
            for output in outputs_by_task.get(dependency, set())
        }
        for item in task.consumes:
            key = (item.get("output"), item.get("type"), item.get("schema_version"))
            if key not in upstream_outputs and task.depends_on:
                errors.append(f"task {task.task_id} consumes an unmatched output contract {key}")
        for acceptance in task.acceptance:
            check = acceptance.get("check")
            if check not in {"cmd", "human"}:
                errors.append(f"task {task.task_id} has invalid acceptance check {check!r}")
            source = acceptance.get("evidence_source")
            if source is None:
                # R10 §9：evidence source 必须能产生判据要求的证据形态。`cmd` 是
                # 门禁项且要真跑一条命令，没有 evidence source 就无法确认它跑的是
                # 什么，因此必须声明。`human` 判据由人读，没有可执行文件可指。
                if check == "cmd":
                    errors.append(
                        f"task {task.task_id} has a cmd acceptance without an evidence source"
                    )
            elif not str(source).strip():
                errors.append(f"task {task.task_id} has an empty evidence source")
            elif str(source) not in evidence_sources:
                # fail-closed：注册表为空时不再静默放行。ADR-032 要求 evidence
                # source 必须登记 id、类型、命令模板、输出形态、权限和 policy
                # hash；空注册表意味着什么都没登记，任何 source 都算未登记。
                errors.append(f"task {task.task_id} uses an unregistered evidence source {source}")

    @staticmethod
    def _normalize_glob(glob: str, errors: list[str]) -> str:
        normalized = glob.replace("\\", "/").strip()
        if normalized != glob or normalized.startswith("/") or ":" in normalized:
            errors.append(f"path glob is not a normalized POSIX relative glob: {glob!r}")
        return normalized

    @staticmethod
    def _has_cycle(graph: dict[str, set[str]], indegree: dict[str, int]) -> bool:
        queue = deque(task_id for task_id, degree in indegree.items() if degree == 0)
        visited = 0
        remaining = dict(indegree)
        while queue:
            task_id = queue.popleft()
            visited += 1
            for child in graph[task_id]:
                remaining[child] -= 1
                if remaining[child] == 0:
                    queue.append(child)
        return visited != len(indegree)
