"""R10 §39 分配与传阅状态机的代码侧镜像（R10-fix-13）。"""

from app.allocation.machine import (
    ALLOCATION_STATE_NAMES,
    AllocationState,
    AllocationTable,
    CirculationState,
    InvalidAllocationTransition,
    default_allocation_table,
)

__all__ = [
    "ALLOCATION_STATE_NAMES",
    "AllocationState",
    "AllocationTable",
    "CirculationState",
    "InvalidAllocationTransition",
    "default_allocation_table",
]
