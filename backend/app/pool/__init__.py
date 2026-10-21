"""The platform's model quota pool.

The pool turns "which harness has quota" into "what can I call": one
OpenAI-compatible door (plus an Anthropic-compatible one, so the Claude CLI
can be pointed at it) in front of the providers the harness CLIs already have
credentials for.

The rules that shape it:

* the **execution slot never moves**.  The pool serves model calls; the CLI
  that was asked to do the work still does it, in its own worktree.  Only the
  quota behind the model is shared;
* the **provider key never leaves**.  The pool injects it server-side; the
  harness that calls the pool holds only the pool's own token -- and the pool
  knows exactly *who* spent *whose* quota;
* the pool is **not a dispatcher**.  It never claims a task, never moves an
  attempt, and never speaks for a harness.
"""

from __future__ import annotations

from app.pool.routing import plan_attempt
from app.pool.service import PoolError, PoolService
from app.pool.sources import Upstream, discover_upstreams

__all__ = [
    "PoolError",
    "PoolService",
    "Upstream",
    "discover_upstreams",
    "plan_attempt",
]
