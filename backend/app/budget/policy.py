"""纯政策：预算三桶、准入判定与阈值分类（budget-guard）。

机制核读自 Paperclip `budget-reservations.ts`（committed = observed + held；
`committed >= limit || committed + amount > limit` 即拒）与 `budgets.ts`
（事故阈值是 **observed** 口径）。本文件无 IO、无时间——决策可用整数直接测。

三桶的含义（诚实分账，不发明数字）：

* ``settled``——已结算且有 harness 上报的实际上报值；
* ``unreported``——已结算但**没有**上报：按预留额保守计入（宁可高估）；
* ``held``——在途预留（attempt 正在跑或尚未收口）。

两个口径，刻意分开（Paperclip 同款分工）：

* **准入**（`admission_blocked`）看 ``committed`` = 三桶之和——在途预留
  也是已承诺的容量；
* **事故**（`classify_observed`）只看 ``observed`` = settled + unreported
  ——在途预留不制造事故："预算被未完成的运行占着"是暂态并发状况，
  不该给人发告警（Paperclip 的 soft/hard 阈值同为 observed 口径）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BudgetState(StrEnum):
    OK = "ok"
    WARNING = "warning"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class UsageBuckets:
    settled_tokens: int
    unreported_tokens: int
    held_tokens: int

    @property
    def observed(self) -> int:
        """Accounted spend -- what incidents are judged on."""
        return self.settled_tokens + self.unreported_tokens

    @property
    def committed(self) -> int:
        """Observed plus in-flight holds -- what admission checks."""
        return self.settled_tokens + self.unreported_tokens + self.held_tokens


def reservation_amount(estimate: int, limit: int) -> int:
    """The hold to place for one attempt.

    Paperclip takes the max over policies' configured ``reservationCents``;
    we have one deployment-level estimate, capped at the task's own limit --
    a task whose whole budget is smaller than the estimate runs its attempt
    under a full-budget hold instead of being locked out forever.
    """
    return max(0, min(estimate, limit))


def admission_blocked(*, committed: int, amount: int, limit: int) -> bool:
    """Paperclip's admission predicate, verbatim in shape.

    ``committed >= limit`` blocks even a zero-amount hold ("available budget
    is reserved by unfinished runs"); otherwise the new hold must fit:
    ``committed + amount > limit`` (equality is allowed).
    """
    if limit <= 0:
        return False
    return committed >= limit or committed + amount > limit


def classify_observed(*, observed: int, limit: int, warn_percent: int) -> BudgetState:
    """Incident state from accounted spend, inclusive at both boundaries.

    ``exhausted`` at ``observed >= limit`` is Paperclip's hard stop (dispatch
    would be blocked); ``warning`` at ``observed >= warn%`` is the soft
    threshold. In-flight holds never raise incidents here.
    """
    if limit <= 0:
        return BudgetState.OK
    if observed >= limit:
        return BudgetState.EXHAUSTED
    if observed * 100 >= limit * warn_percent:
        return BudgetState.WARNING
    return BudgetState.OK
