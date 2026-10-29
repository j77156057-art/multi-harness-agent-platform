"""The platform's own tools: what a Harness uses when it has no tools of its own.

Why this package exists. A Harness is an external CLI, and it brings its own
tools -- unless the model behind it cannot ask for one. When an attempt is
served from the quota pool by a *CLI-as-model* slot, the "model" is another
CLI's text output: it cannot emit ``tool_calls`` at all, so the caller's
harness loop has nothing to execute and the attempt produces a polite essay
instead of a deliverable. That is the failure this package answers: the
**platform** runs the tool loop, and the model only has to say what to do
next, in text.

The three parts:

* :mod:`app.agenttools.floor` -- what a tool may touch (the attempt's own
  worktree, inside its authorized paths, minus credentials, minus the
  commands that end a machine).
* :mod:`app.agenttools.registry` -- the tool table itself.
* :mod:`app.agenttools.loop` -- one turn = one JSON object; a refusal is
  handed back to the model as an observation it can correct, never as a
  dead end.

The tools are modelled on the ones the author already runs elsewhere
(``rag-agent``'s ``TOOLS`` table: name -> description + input schema +
handler, with a universal text ``input`` so a text-protocol model can call
anything). They are **re-implemented** against this platform's guarantees
rather than copied: that table is bound to a different application's runtime
(its project root, its region approvals, its sandbox), and importing it would
put a second application's policies inside this one.
"""

from app.agenttools.floor import Floor, FloorRefused
from app.agenttools.loop import LoopResult, PlatformLoop, Step
from app.agenttools.registry import Tool, ToolRefused, build_registry, tool_plans

__all__ = [
    "Floor",
    "FloorRefused",
    "LoopResult",
    "PlatformLoop",
    "Step",
    "Tool",
    "ToolRefused",
    "build_registry",
    "tool_plans",
]
