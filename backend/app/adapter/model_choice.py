"""人给窗口指定的模型来源：用自带模型，还是用额度池里的某个模型。

为什么单独一个模块：这个选择是**人的决定**，它的语义（两个取值、什么时候生效、
什么时候不许生效）要有一处说了算，不能散在路由、池子与界面里各写一遍 —— 各写一遍
就会出现「界面说一套、平台做另一套」。

四条不变式：

1. ``model_mode`` 只有两个取值：``cli``（用窗口自己 CLI 配置里的那个模型）与
   ``pool``（走额度池）。别的一律不认 —— 平台不猜，也不替人选。
2. ``model_mode='pool'`` 时 ``model_id`` 必须是**额度池里的模型 id**（原样存池子
   的 id）：这样界面能写回「哪家的什么模型」，平台也能对回池子里那一条。
3. **人自己的 key 仍然要人点头**：选择**不**授予花钱的权力 —— 池子那一档的规矩
   没变（见 ``app.pool.user_keys``）。
4. 窗口没在册就谈不上给它选模型：没登记的窗口不能被指定。

存的是**当前**的选择，不是历史：改一次就覆盖上一次（这一轮生效的就是最后一次）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db.connection import Database

#: 用这个窗口自己 CLI 配置里的模型（默认，也是现状）。
MODEL_MODE_CLI = "cli"
#: 走额度池：这一轮直接用池子里被指定的那个模型。
MODEL_MODE_POOL = "pool"
#: 平台只认这两个取值。
MODEL_MODES = (MODEL_MODE_CLI, MODEL_MODE_POOL)


class ModelChoiceError(ValueError):
    """The requested choice cannot be stored (bad mode, unknown window, ...)."""


@dataclass(frozen=True)
class ModelChoice:
    """一个窗口当前的模型选择。"""

    harness_id: str
    model_mode: str = MODEL_MODE_CLI
    model_id: str = ""
    updated_at: str | None = None

    @property
    def uses_pool(self) -> bool:
        """是否真的指定了池子里的某个模型。"""
        return self.model_mode == MODEL_MODE_POOL and bool(self.model_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "harness_id": self.harness_id,
            "model_mode": self.model_mode,
            "model_id": self.model_id or None,
            "updated_at": self.updated_at,
        }


def read_choice(database: Database, harness_id: str) -> ModelChoice | None:
    """这个窗口当前的选择；窗口没在册就是 ``None``。"""
    rows = database.read(
        "SELECT harness_id, model_mode, model_id, model_updated_at FROM harnesses"
        " WHERE harness_id = ?",
        (harness_id,),
    )
    if not rows:
        return None
    return _choice_from_row(rows[0])


def save_choice(
    database: Database,
    *,
    harness_id: str,
    model_mode: str,
    model_id: str | None = None,
) -> ModelChoice:
    """记下人给这个窗口的选择。

    校验在写之前做完：存下一个池子里没有的模型，只会让界面显示「已保存」而下一轮
    悄悄回落 —— 那比当场拒绝更坏（人以为自己的活跑在指定模型上）。
    """
    mode = str(model_mode or "").strip().lower()
    if mode not in MODEL_MODES:
        raise ModelChoiceError("model_mode must be 'cli' or 'pool'")
    chosen = str(model_id or "").strip()
    if mode == MODEL_MODE_POOL and not chosen:
        raise ModelChoiceError("a pool model must be named")
    if mode == MODEL_MODE_CLI:
        # 自带模型时池子的那个 id 没有意义，清掉（不留一个半截的选择）。
        chosen = ""
    now = datetime.now(UTC).isoformat()
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT harness_id FROM harnesses WHERE harness_id = ?", (harness_id,)
        ).fetchone()
        if row is None:
            raise ModelChoiceError(f"harness {harness_id} is not registered")
        conn.execute(
            "UPDATE harnesses SET model_mode = ?, model_id = ?, model_updated_at = ?"
            " WHERE harness_id = ?",
            (mode, chosen or None, now, harness_id),
        )
    return ModelChoice(harness_id=harness_id, model_mode=mode, model_id=chosen, updated_at=now)


def _choice_from_row(row: Any) -> ModelChoice:
    mode = str(row["model_mode"] or MODEL_MODE_CLI)
    if mode not in MODEL_MODES:
        # 库里存了个不认的值：按自带处理，并把「不认」这件事留给调用方去显示，
        # 不在这里悄悄换成一个看着合理的模式。
        mode = MODEL_MODE_CLI
    updated = row["model_updated_at"]
    return ModelChoice(
        harness_id=str(row["harness_id"]),
        model_mode=mode,
        model_id=str(row["model_id"] or ""),
        updated_at=str(updated) if updated else None,
    )
