"""The ReAct loop -- the assistant's own engine, owned by this project.

The contract, in one paragraph: the model is a swappable part; the loop is
this repo's code.  Each turn gets the *same* transcript (system rules, the
question, untrusted-fenced observations, the steps so far) and must answer
with one JSON object: keep investigating, or finish.  Finishing is gated
twice -- a mechanical gate (this code) checks that every citation resolves to
a real step or context line, then the self-check round makes the model police
its own draft against the honesty checklist.  A run that has no citable
answer when its budget runs out stops *honestly*: it reports where it is
stuck, never an invented answer -- the engine's version of the platform's
``available: false``.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.admin.service import AdminHarnessError, extract_json_object
from app.assistant.prompts import SYSTEM_PROMPT, UNTRUSTED_PREFIX, reflect_prompt
from app.assistant.tools import Tool, ToolRefused
from app.config import settings


@dataclass(frozen=True)
class Budget:
    """What a run may spend. Tokens are deliberately *not* counted locally:
    the pool meters spend per call with a receipt; a local estimate would
    only be a guess. Calls and wall-clock are the loop's own levers."""

    max_calls: int = 6
    max_seconds: float = 240.0


@dataclass
class Step:
    no: int
    kind: str  # "act" | "reflect"
    thought: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    observation: dict[str, Any] | None = None
    taken_at: float = field(default_factory=lambda: time.time())

    def as_json(self) -> dict[str, Any]:
        return {
            "no": self.no,
            "kind": self.kind,
            "thought": self.thought,
            "tool": self.tool,
            "args": self.args,
            "observation": self.observation,
            "taken_at": self.taken_at,
        }


class Model(Protocol):
    """One round trip with whatever model channel serves the assistant."""

    def step(self, prompt: str) -> str: ...


def _model_json(text: str) -> dict[str, Any] | None:
    """The first balanced JSON object, or ``None`` (an honest parse failure).

    Reuses the admin refine channel's parser: it already handles the prose
    and the fenced blocks real CLIs print, and refuses to truncate nested
    structures.
    """
    try:
        return extract_json_object(text)
    except AdminHarnessError:
        return None


class _Gate(ValueError):
    """A rejected final draft, carrying the reason the model will see."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _checked_final(payload: dict[str, Any], state: _Run) -> dict[str, Any]:
    """The mechanical gate: resolve every citation, or refuse the answer.

    What the gate *can* check: text exists; every citation resolves to a
    recorded step whose observation is real, or to a provided context line.
    What it cannot check is whether a *number inside* the text matches the
    evidence -- that is the reflection layer's job; the gate pretending to
    verify numbers would itself be a guess.
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise _Gate("final text must not be empty")
    cites = payload.get("cites")
    if not isinstance(cites, list) or not cites:
        raise _Gate("final carries no evidence citations")
    seen: list[str] = []
    for cite in cites:
        token = str(cite).strip()
        if not token:
            raise _Gate(f"citation {token!r} is empty")
        body = token[1:-1] if token.startswith("[") and token.endswith("]") else token
        kind, index = body[:1], body[1:]
        if kind not in {"s", "c"} or not index.isdigit():
            raise _Gate(f"citation {token!r} is not a [sN] step or [cN] context reference")
        number = int(index)
        if kind == "s":
            if number < 1 or number > len(state.steps):
                raise _Gate(f"citation {token!r} names no recorded step")
            if state.steps[number - 1].observation is None:
                raise _Gate(f"citation {token!r} points at a step with no observation")
        else:
            if number < 1 or number > len(state.refs):
                raise _Gate(f"citation {token!r} names no provided context")
        if token not in seen:
            seen.append(token)
    return {"text": text, "cites": seen}


@dataclass
class _Run:
    question: str = ""
    refs: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    calls: int = 0
    draft: dict[str, Any] | None = None
    reflected: bool = False
    started: float = field(default_factory=time.monotonic)


class AssistantLoop:
    """One question (or one evidence bundle), start to finish."""

    def __init__(
        self,
        model: Model,
        tools: dict[str, Tool],
        *,
        budget: Budget | None = None,
        system_prompt: str | None = None,
        run_id: str | None = None,
        trace_dir: Path | None = None,
        self_check: bool = True,
    ) -> None:
        self.model = model
        self.tools = tools
        self.budget = budget or Budget()
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.run_id = run_id or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.trace_dir = (
            Path(trace_dir) if trace_dir else Path(settings.data_dir) / "assistant" / "trace"
        )
        self.self_check = self_check

    # ---------------------------------------------------------------- run

    def run(self, question: str, context: Sequence[str]) -> dict[str, Any]:
        state = _Run(question=question, refs=list(context))
        while True:
            elapsed = time.monotonic() - state.started
            if elapsed > self.budget.max_seconds:
                result = self._stop(
                    state,
                    f"time budget exceeded after {elapsed:.0f}s without a citable answer; "
                    "here is how far it got",
                )
                break
            if state.calls >= self.budget.max_calls:
                result = self._stop(
                    state,
                    "model call budget exhausted without a citable answer; "
                    "here is how far it got",
                )
                break
            state.calls += 1
            stage = (
                "reflect"
                if (self.self_check and state.draft is not None and not state.reflected)
                else "act"
            )
            prompt = self._transcript(stage, state)
            parsed = _model_json(str(self.model.step(prompt) or ""))
            if parsed is None:
                self._append(
                    state,
                    kind=stage,
                    thought="",
                    observation={
                        "format": "model output carried no balanced JSON object; nothing "
                        "was executed, answer again in the same JSON shape"
                    },
                )
                continue
            result = (
                self._reflect(state, parsed) if stage == "reflect" else self._act(state, parsed)
            )
            if result is not None:
                break
        result.update(
            run_id=self.run_id,
            calls_used=state.calls,
            steps=len(state.steps),
            trace=self._write_final(result),
        )
        return result

    # ------------------------------------------------------------- turns

    def _act(self, state: _Run, parsed: dict[str, Any]) -> dict[str, Any] | None:
        thought = str(parsed.get("thought") or "").strip()
        if not thought:
            self._append(
                state,
                kind="act",
                thought="",
                observation={"format": "thought is required; say what is missing"},
            )
            return None
        if "final" in parsed:
            return self._attempt_final(state, thought, parsed.get("final"))
        name = str(parsed.get("tool") or "").strip()
        args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        tool = self.tools.get(name)
        if tool is None:
            self._append(
                state,
                kind="act",
                thought=thought,
                tool=name or None,
                args=args,
                observation={
                    "refused": f"tool {name!r} is not in the whitelist; "
                    "the loop does not guess substitutes"
                },
            )
            return None
        try:
            observation = tool.call(args)
        except ToolRefused as exc:
            observation = {"refused": str(exc)}
        except Exception as exc:  # noqa: BLE001 - a tool crash is an observation too
            observation = {"error": f"{type(exc).__name__}: {exc}"}
        self._append(
            state, kind="act", thought=thought, tool=name, args=args, observation=observation
        )
        return None

    def _attempt_final(self, state: _Run, thought: str, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            self._append(
                state,
                kind="act",
                thought=thought,
                observation={"final_rejected": "final must be an object {text, cites}"},
            )
            return None
        try:
            _checked_final(payload, state)
        except _Gate as exc:
            self._append(
                state,
                kind="act",
                thought=thought,
                observation={"final_rejected": exc.reason},
            )
            return None
        # A gated draft still faces the self-check round (unless disabled);
        # the loop accepts it only after reflection, or records the honest
        # "reflection unavailable" marker next to it.
        state.draft = {"text": str(payload.get("text")).strip(), "cites": list(payload["cites"])}
        if not self.self_check:
            return self._done(state, "disabled")
        return None

    def _reflect(self, state: _Run, parsed: dict[str, Any]) -> dict[str, Any] | None:
        state.reflected = True
        verdict = str(parsed.get("verdict") or "").strip()
        thought = str(parsed.get("thought") or "")
        if verdict == "pass":
            assert state.draft is not None
            self._append(state, kind="reflect", thought=thought, observation={"self_check": "pass"})
            return self._done(state, "pass")
        if verdict == "revise":
            revised = parsed.get("revised")
            if not isinstance(revised, dict) or "text" not in revised:
                self._append(
                    state,
                    kind="reflect",
                    thought=thought,
                    observation={"final_rejected": "verdict revise carried no revised text"},
                )
                state.draft = None
                return None
            try:
                state.draft = _checked_final(revised, state)
            except _Gate as exc:
                self._append(
                    state,
                    kind="reflect",
                    thought=thought,
                    observation={"final_rejected": f"revised draft failed the gate: {exc.reason}"},
                )
                state.draft = None
                return None
            self._append(
                state, kind="reflect", thought=thought, observation={"self_check": "revised"}
            )
            return self._done(state, "revised")
        # A reflection that cannot be understood is not a pass; keep the
        # draft, but record that the self-check never landed.
        self._append(
            state,
            kind="reflect",
            thought=thought,
            observation={
                "self_check": "unavailable (verdict not understood); draft kept after gate"
            },
        )
        return self._done(state, "unavailable")

    # -------------------------------------------------------------- misc

    def _done(self, state: _Run, self_check: str) -> dict[str, Any]:
        assert state.draft is not None
        return {"status": "done", "answer": dict(state.draft), "self_check": self_check}

    def _stop(self, state: _Run, reason: str) -> dict[str, Any]:
        last_tool = state.steps[-1].tool if state.steps else None
        return {
            "status": "stopped",
            "reason": reason,
            "stuck_after_tool": last_tool,
            "facts_gathered": len(state.steps),
        }

    def _append(self, state: _Run, **kwargs: Any) -> Step:
        step = Step(no=len(state.steps) + 1, **kwargs)
        state.steps.append(step)
        self._trace_step(step)
        return step

    def _transcript(self, stage: str, state: _Run) -> str:
        parts = [self.system_prompt, "", f"任务：{state.question}", ""]
        if state.refs:
            parts.append("开局给出的上下文（平台已落档的事实，按条引用）：")
            for i, ref in enumerate(state.refs, start=1):
                parts.append(f"[c{i}] {UNTRUSTED_PREFIX}{ref}")
            parts.append("")
        if state.steps:
            parts.append("已做的事与看到的东西：")
            for step in state.steps:
                parts.append(f"[s{step.no}] thought: {step.thought or '(自检轮)'}")
                if step.tool:
                    args_text = json.dumps(step.args, ensure_ascii=False)
                    parts.append(f"    tool: {step.tool} args: {args_text}")
                if step.observation is not None:
                    observ_text = json.dumps(step.observation, ensure_ascii=False)
                    parts.append(f"    observation: {UNTRUSTED_PREFIX}{observ_text}")
            parts.append("")
        if stage == "reflect":
            assert state.draft is not None
            parts.append(reflect_prompt(state.draft))
        parts.append("现在返回一个 JSON 对象：继续取证用 tool，作答用 final。")
        return "\n".join(parts)

    # ---------------------------------------------------------------- trace

    def _trace_path(self) -> Path:
        return self.trace_dir / f"{self.run_id}.jsonl"

    def _trace_step(self, step: Step) -> None:
        self._trace_write(json.dumps(step.as_json(), ensure_ascii=False))

    def _write_final(self, result: dict[str, Any]) -> str:
        self._trace_write(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "finished_at": time.time(),
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "self_check": result.get("self_check"),
                    "calls_used": result.get("calls_used"),
                    "steps": result.get("steps"),
                    "token_accounting": "未记上（池子按调用记账；引擎本地不估算）",
                },
                ensure_ascii=False,
            )
        )
        return str(self._trace_path()) if self._trace_path().exists() else "unavailable"

    def _trace_write(self, line: str) -> None:
        try:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            with self._trace_path().open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            return  # a lost trace line never changes an answer


__all__ = ["AssistantLoop", "Budget"]
