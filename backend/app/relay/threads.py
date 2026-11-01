"""线程与跳数：让"这条对话已经转了几手"变成可数的东西。

为什么需要它：转接是**双向**的（A 写给 B，B 回给 A），两个窗口客客气气地来回，平台就
会一直投下去 —— 没人做错什么，但对话永远不会停。跳数是唯一的刹车。

两条口径，缺一不可：

1. **显式线程**（精确）：信里写一行 `回复 <thread id>`，平台按那条线程记跳数；认不出的
   thread id **整封退回**，不悄悄当新线程（那会把回路计数清零，等于放开刹车）。
2. **回路闸门**（不依赖协议）：模型会忘记写 thread。所以另有一道不依赖协议的限制 ——
   同一对窗口在窗口期内来回超过 `MAX_EXCHANGES` 次就停。这条能挡住"忘记写 thread 的
   来回"，也能挡住两个不同的 thread 交替刷屏。

状态落盘在 `relay/threads/`：跳数要跨进程重启仍然准确（worker 随时可能被重启）。
超过上限一律**整封退回并写清原因**：停下来让人看一眼，而不是静默丢弃或继续投。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: 一条线程最多转几跳。到了就停：再转下去两个窗口只会互相客气。
MAX_HOPS = 6

#: 同一对窗口在窗口期内最多来回几次（不依赖协议的回路闸门）。
MAX_EXCHANGES = 6
#: 回路闸门的时间窗口（秒）。超出窗口的来回不再累计。
EXCHANGE_WINDOW_SECONDS = 10 * 60

#: 线程 id 形状：`thr-` + 8~16 位十六进制。信里写的就是这个。
THREAD_ID = re.compile(r"^thr-[0-9a-f]{8,16}$")


@dataclass
class ThreadState:
    """一条线程的账本。"""

    thread_id: str
    hops: int = 0
    participants: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict:
        return {
            "thread_id": self.thread_id,
            "hops": self.hops,
            "participants": self.participants,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ThreadLedgerError(RuntimeError):
    """账本读不出来（文件坏了/被手改坏了）。

    这条和"线程不存在"是**两件事**：前者是平台侧的问题，后者是写信人抄错了 id。合并成
    一个"不存在"，会让人以为是自己抄错了，反复改也改不对（2026-11-01 实测踩到：手改
    账本时写进了 BOM，回信被判成"线程不存在"）。
    """


class ThreadStore:
    """`relay/threads/` 下的一条条 JSON：线程 id、跳数、参与者、时间。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, thread_id: str) -> Path:
        return self.root / f"{thread_id}.json"

    def get(self, thread_id: str) -> ThreadState | None:
        path = self._path(thread_id)
        if not path.is_file():
            return None
        try:
            # utf-8-sig：这些账本会被人打开看/改，Windows 编辑器写回时常带 BOM。
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as problem:
            raise ThreadLedgerError(f"线程账本 {thread_id} 读不出来：{problem}") from problem
        return ThreadState(
            thread_id=str(payload.get("thread_id") or thread_id),
            hops=int(payload.get("hops") or 0),
            participants=[str(name) for name in payload.get("participants") or []],
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )

    def save(self, state: ThreadState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(state.thread_id).write_text(
            json.dumps(state.as_dict(), ensure_ascii=False, indent=1),
            encoding="utf-8",
            newline="\n",
        )

    def open(self, thread_id: str) -> ThreadState:
        state = self.get(thread_id)
        if state is None:
            state = ThreadState(thread_id=thread_id, created_at=now_iso())
        return state

    def record_hop(self, thread_id: str, *, sender: str, recipients: list[str]) -> ThreadState:
        """记一跳：跳数 +1，参与者并进来。"""
        state = self.open(thread_id)
        state.hops += 1
        for name in [sender, *recipients]:
            if name and name not in state.participants:
                state.participants.append(name)
        state.updated_at = now_iso()
        self.save(state)
        return state


@dataclass
class ExchangeLedger:
    """不依赖协议的回路闸门：`(A,B)` 这对窗口最近几次来回。"""

    root: Path

    def _path(self, a: str, b: str) -> Path:
        left, right = sorted((a, b))
        return Path(self.root) / f"{left}__{right}.json"

    def recent(self, a: str, b: str, *, now: float | None = None) -> list[float]:
        path = self._path(a, b)
        if not path.is_file():
            return []
        try:
            stamps = [float(value) for value in json.loads(path.read_text(encoding="utf-8"))]
        except (ValueError, OSError, TypeError):
            return []
        current = _now(now)
        return [stamp for stamp in stamps if current - stamp <= EXCHANGE_WINDOW_SECONDS]

    def record(self, a: str, b: str, *, now: float | None = None) -> int:
        """记一次来回，返回窗口内累计次数。"""
        current = _now(now)
        stamps = self.recent(a, b, now=current)
        stamps.append(current)
        path = self._path(a, b)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stamps[-50:]), encoding="utf-8", newline="\n")
        return len(stamps)


def _now(value: float | None) -> float:
    if value is not None:
        return value
    return datetime.now(timezone.utc).timestamp()
