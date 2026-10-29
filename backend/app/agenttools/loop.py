"""One turn, one JSON object: the loop the platform runs when a model cannot.

The contract is deliberately the simplest thing that still works over a
channel that has no function calling:

    {"thought": "...", "tool": "read_file", "input": {"path": "docs/a.md"}}

and, when the work is done,

    {"thought": "...", "tool": "finish", "input": {"summary": "..."}}

Why a JSON object and not a native ``tool_calls`` array: the attempt is
served by whatever the quota pool routed it to, and a *CLI-as-model* slot (the
free tier) answers with prose -- it has no ``tool_calls`` to give.  A JSON
object in text is the one shape every channel can produce, including the ones
that cannot call a tool at all.  It is the same contract this platform's
assistant already runs on, and the same one the author's workbench uses.

Three rules the loop keeps, each because the alternative lies:

* **A refusal is an observation.**  ``Tool`` raises ``ToolRefused``/``FloorRefused``
  with a sentence; the loop puts that sentence in the transcript and gives the
  model the next turn.  A refusal that ends the run teaches nothing.
* **A malformed turn is an observation too.**  Models that cannot call tools
  also cannot reliably emit JSON; a fenced block, a preamble, or a truncated
  object is normal.  The parser is the admin channel's (it already handles
  prose and fences and refuses to truncate nesting), and a parse failure is
  fed back with the shape expected.
* **The transcript is the whole state.**  Every turn re-sends the plan, the
  floor, the task and every observation so far.  Nothing is assumed to be
  remembered, because a CLI-as-model remembers nothing between calls.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agenttools.floor import Floor, FloorRefused, describe
from app.agenttools.registry import Tool, ToolRefused, tool_plans
from app.admin.service import AdminHarnessError, extract_json_object


class Model(Protocol):
    """One round trip with whatever channel serves this attempt."""

    def text(self, prompt: str) -> str: ...


@dataclass
class Step:
    no: int
    thought: str
    tool: str
    tool_input: Any
    observation: str
    parsed: bool = True
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "no": self.no,
            "thought": self.thought,
            "tool": self.tool,
            "input": self.tool_input,
            "observation": self.observation,
            "parsed": self.parsed,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class LoopResult:
    ok: bool
    summary: str
    steps: list[Step] = field(default_factory=list)
    #: Why the loop stopped when it did not finish the work.
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "error": self.error,
            "steps": [step.as_dict() for step in self.steps],
        }


CONTRACT = """你在替这个平台干活。这台机器上的 Harness 有的用不了工具，所以工具由平台执行：
你说下一步做什么，平台就替你做，然后把结果给你。

**每一回合只输出一个 JSON 对象**，不要解释、不要 Markdown 围栏、不要在对象前后写任何字：

  {{"thought": "一句话说清这一步要做什么", "tool": "工具名", "input": {{...}}}}

可用工具：
{tools}

规矩：
- `input` 写成对象；写不成对象时，用该工具说明里的文本写法。
- 写完一个文件、改完一处，再进下一步。一次只做一件事。
- 工具拒绝了就照它说的改，别重复同一次调用。
- 全部做完后，用 `finish` 交活，并在 `input.summary` 里写清：改了哪些文件、为什么这么改、怎么验证的、还有什么没做。
- 没有把握的地方去读、去搜；不要凭猜测写路径。
- 不要试图自己提交或推代码：平台会在你交活后提交本任务工作树里的改动。
"""


class PlatformLoop:
    """Run one task: the model plans, the platform executes, the platform records.

    ``max_steps`` defaults to ``None`` -- **no step cap**.  A cap counted in
    turns is a guess about how long a real task takes, and when the guess is
    low the work is thrown away at an arbitrary point (the transcript then
    shows "工作没有完成" for a task that was three steps from done).  What
    actually bounds a run is **wall clock**: the worker kills the child at its
    own deadline, so the loop keeps its own ``max_seconds`` honest and lets the
    model take as many turns as it needs inside it.  A caller that really wants
    a cap can still pass one (tests do).
    """

    def __init__(
        self,
        model: Model,
        tools: dict[str, Tool],
        *,
        floor: Floor,
        goal: str,
        max_steps: int | None = None,
        max_seconds: float = 900.0,
        clock: Any = time.monotonic,
    ) -> None:
        self.model = model
        self.tools = tools
        self.floor = floor
        self.goal = goal
        self.max_steps = None if max_steps is None else max(1, int(max_steps))
        self.max_seconds = max(30.0, float(max_seconds))
        self._clock = clock

    # ------------------------------------------------------------------ prompt

    def system_prompt(self) -> str:
        return CONTRACT.format(tools="\n".join(tool_plans(self.tools)))

    def turn_prompt(self, steps: list[Step]) -> str:
        parts = [self.system_prompt(), "", "## 场地", *describe(self.floor), "", "## 这条活", self.goal.strip()]
        if steps:
            parts += ["", "## 到目前为止"]
            for step in steps:
                parts.append(
                    f"第 {step.no} 步 · thought: {step.thought or '（没写）'}\n"
                    f"  调用: {step.tool or '（没写工具）'}"
                )
                observation = step.observation if len(step.observation) <= 6000 else (
                    step.observation[:2000]
                    + f"\n...（观察太长，中间 {len(step.observation) - 4000} 字略）...\n"
                    + step.observation[-2000:]
                )
                parts.append(f"  结果: {observation or '（空）'}")
        else:
            parts += ["", "## 到目前为止", "（还没有做任何一步。先看清场地，再动手。）"]
        budget = (
            "下一步只输出一个 JSON 对象。"
            if self.max_steps is None
            else f"还剩 {self.max_steps - len(steps)} 步。下一步只输出一个 JSON 对象。"
        )
        parts += ["", budget]
        return "\n".join(parts)

    # -------------------------------------------------------------------- run

    def run(self) -> LoopResult:
        steps: list[Step] = []
        started = self._clock()
        while True:
            if self._clock() - started > self.max_seconds:
                return LoopResult(
                    ok=False,
                    summary=self._where(steps),
                    steps=steps,
                    error=f"用完了时间预算（{int(self.max_seconds)} 秒）",
                )
            if self.max_steps is not None and len(steps) >= self.max_steps:
                return LoopResult(
                    ok=False,
                    summary=self._where(steps),
                    steps=steps,
                    error=f"用完了步数预算（{self.max_steps} 步）",
                )
            prompt = self.turn_prompt(steps)
            asked = self._clock()
            try:
                answer = self.model.text(prompt)
            except Exception as exc:  # noqa: BLE001 - the channel can fail any way
                # The channel itself died. That is not the model's mistake and
                # not something one more turn can fix: say so and stop.
                return LoopResult(
                    ok=False,
                    summary=self._where(steps),
                    steps=steps,
                    error=f"模型通道失败（{type(exc).__name__}: {exc}）",
                )
            elapsed = self._clock() - asked
            try:
                decision = extract_json_object(answer)
            except AdminHarnessError:
                steps.append(
                    Step(
                        no=len(steps) + 1,
                        thought="",
                        tool="",
                        tool_input=None,
                        observation=(
                            "上一回合的输出不是 JSON 对象，平台没法读。"
                            "只输出一个 JSON 对象，形如 "
                            '{"thought": "...", "tool": "list_files", "input": {"path": ""}}。'
                            f"你写的是：{_short(answer)}"
                        ),
                        parsed=False,
                        seconds=elapsed,
                    )
                )
                continue
            thought = str(decision.get("thought") or "").strip()
            tool_name = str(decision.get("tool") or decision.get("action") or "").strip()
            if not tool_name:
                # Some models put the answer in ``finish``/``final`` instead of
                # naming a tool. Treat it as the finish step rather than as an
                # error: the loop's job is to get work submitted, not to insist
                # on a field name.
                for key in ("finish", "final", "answer"):
                    if key in decision:
                        summary = str(decision.get(key) or "").strip()
                        steps.append(
                            Step(
                                no=len(steps) + 1,
                                thought=thought,
                                tool="finish",
                                tool_input={"summary": summary},
                                observation=summary,
                                seconds=elapsed,
                            )
                        )
                        return LoopResult(ok=True, summary=summary, steps=steps)
                steps.append(
                    Step(
                        no=len(steps) + 1,
                        thought=thought,
                        tool="",
                        tool_input=decision,
                        observation=(
                            "JSON 里没有 `tool`。要调用工具就写 "
                            '"tool": "工具名"；做完了就写 "tool": "finish"。'
                        ),
                        seconds=elapsed,
                    )
                )
                continue
            tool_input = decision.get("input")
            if tool_input is None and "args" in decision:
                tool_input = decision.get("args")
            observation = self._call(tool_name, tool_input)
            step = Step(
                no=len(steps) + 1,
                thought=thought,
                tool=tool_name,
                tool_input=tool_input,
                observation=observation,
                seconds=elapsed,
            )
            steps.append(step)
            if tool_name == "finish":
                return LoopResult(ok=True, summary=observation.strip(), steps=steps)

    # ----------------------------------------------------------------- helpers

    def _call(self, name: str, raw_input: Any) -> str:
        tool = self.tools.get(name)
        if tool is None:
            available = "、".join(sorted(self.tools))
            return f"没有叫 `{name}` 的工具。可用的是：{available}。"
        try:
            return str(tool.call(raw_input))
        except (ToolRefused, FloorRefused) as exc:
            return f"拒绝：{exc}"
        except Exception as exc:  # noqa: BLE001 - a tool bug must not end the run
            return f"工具 `{name}` 自己出错了（{type(exc).__name__}: {exc}）。换个做法，或换别的工具。"

    def _where(self, steps: list[Step]) -> str:
        """An honest hand-off: what was done, and that the work is not finished."""
        if not steps:
            return "一步也没做成：模型没有给出可执行的下一步。"
        done = [f"第 {step.no} 步 {step.tool}: {step.observation[:200]}" for step in steps[-5:]]
        return "工作没有完成。最近几步：\n" + "\n".join(done)


def _short(text: str) -> str:
    value = str(text or "").strip().replace("\n", " ")
    return value[:300] + ("…" if len(value) > 300 else "")
