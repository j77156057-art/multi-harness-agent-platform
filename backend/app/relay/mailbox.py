"""文件信箱（outbox / archive / returned / inbox）。

为什么传输用文件而不是终端屏幕：2026-11-01 实测，TUI 两分钟产出 2911 字节、173 个
控制序列，VT 重建之后**一行可用的都没有**；而写文件是每个 coding CLI 都会做的一件事，
写出来是干净字节，写完才被读到（原子）。传输用文件，读取用文件，人看的是平台消息。

目录约定（全部在 ``<仓库>/relay/`` 下，运行时产物，不进版本库）::

    outbox/             发送方写这里，一个文件就是一封信
    archive/            投过的信挪到这里（同一封信不会被投第二次）
    returned/           格式不合规/收件人认不出的信挪到这里，附原因.md
    inbox/<窗口 id>/    投给这个窗口的信（CLI 自己读）

信件格式（第一行必须是作者行）::

    来自 codex

    给管理员：
    这件事要你定方向：口径 A 还是 B。

    给 opencode：
    我改了 docs/x.md 第 3 段。

作者由信自己声明、平台验它已登记：文件名不可信，平台也不能冒充任何人说话。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

#: 只认 .md：别的文件放在 outbox 里不是"信"，不去碰它。
LETTER_SUFFIX = ".md"

#: 文件名里只留安全字符：信件名最终会变成归档名，别让 `..` 或斜杠跑出来。
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class Mailbox:
    """一个仓库上的信箱目录。"""

    root: Path

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def returned(self) -> Path:
        return self.root / "returned"

    @property
    def threads(self) -> Path:
        return self.root / "threads"

    def inbox(self, recipient: str) -> Path:
        return self.root / "inbox" / _safe(recipient)

    def ensure(self) -> None:
        for path in (self.outbox, self.archive, self.returned, self.threads):
            path.mkdir(parents=True, exist_ok=True)

    def pending(self) -> list[Path]:
        """还没投的信：按文件名排序，处理顺序稳定（便于复现与排查）。"""
        if not self.outbox.is_dir():
            return []
        return sorted(
            path for path in self.outbox.iterdir() if path.is_file() and path.suffix.lower() == LETTER_SUFFIX
        )

    def deliver(self, recipient: str, *, name: str, text: str) -> Path:
        """投一封进某个窗口的收件箱，返回信落在哪。"""
        folder = self.inbox(recipient)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = folder / f"{stamp}-{_safe(name)}"
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def archive_letter(self, letter: Path) -> Path:
        self.archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.archive / f"{stamp}-{_safe(letter.name)}"
        letter.replace(target)
        return target

    def return_letter(self, letter: Path, reason: str) -> Path:
        """退回：信挪到 returned/，旁边放一份原因（人一眼能看出为什么没投）。"""
        self.returned.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self.returned / f"{stamp}-{_safe(letter.name)}"
        letter.replace(target)
        (self.returned / f"{stamp}-{_safe(letter.name)}.reason.md").write_text(
            f"# 这封信没有投出去\n\n- 原因：{reason}\n- 原文件：{letter.name}\n"
            f"- 时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
            "改好格式之后重新放进 `relay/outbox/` 即可。\n",
            encoding="utf-8",
            newline="\n",
        )
        return target


def _safe(name: str) -> str:
    cleaned = _UNSAFE.sub("-", str(name or "")).strip("-.")
    return cleaned or "letter"


def envelope_for_delivery(*, sender: str, text: str, thread_id: str = "", hop: int = 0) -> str:
    """投进收件箱的那份文件长什么样：写明谁说的、第几跳，再接正文。

    这一步不做"润色"：正文一个字不改，只在前面加两行来源。**跳数与线程写在最上面**，
    因为收信方要照着它回信（`回复 <thread id>`）—— 那正是跳数能算准的前提。
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    trace = f"来自 {sender}（转接于 {stamp}）"
    if thread_id:
        trace += f"\n线程 {thread_id} · 第 {hop} 跳（回信请写：回复 {thread_id}）"
    return f"{trace}\n\n{text.strip()}\n"
