"""谁把转接跑起来：管理员。

为什么是管理员而不是界面上一个按钮：

- 转接的信箱没人投递，就只是几个躺在 `outbox/` 里的文件。而"保证某个进程一直在跑"
  是运维动作，办公室界面是**人看的地方**，不是人值守的地方 —— 人手点一次能拉起一个，
  机器重启之后还是没人管；
- 管理员窗口本来就是干这件事的：它巡检、上报、看谁没在跑。把转接交给它，是同一个
  职责的延伸，也不用为"谁能开、开几个、关不掉怎么办"再新开一条面向人的写入面。

三条口径，写死在代码里：

1. **管理员只开关，不代笔**：投递仍是 `relay` 窗口自己的身份（消息作者=令牌主体）。
   管理员从不用自己的口吻替谁说话，也不改信一个字。
2. **幂等：群级单例**。心跳新鲜就当作已经在跑，不起第二个 —— 两个 worker 同时读
   `outbox/` 会把同一封信投两遍。转接是按群服务的，与项目数量无关。
3. **开关仍是 `AP_RELAY_MODE`**：没开就不动手，关着时行为与今天一致。

心跳文件由 worker 每轮写（`Mailbox.beat()`），所以"在不在跑"是一个可核对的事实，
不是进程表推断出来的。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT, settings

#: 心跳超过这个时间就不算在跑（worker 默认每 5 秒一轮）。
RUNNING_WINDOW_SECONDS = 5 * 60

#: 起来之后最多等多久看第一个心跳（第一次启动要几秒）。测试可以调小。
STARTUP_WAIT_SECONDS = 2.5

HEARTBEAT_NAME = ".worker-heartbeat"
PID_NAME = ".worker.pid"


def relay_enabled() -> bool:
    """`AP_RELAY_MODE` 是不是开着（与 start-fresh 认的那几个值一致）。"""
    return (os.environ.get("AP_RELAY_MODE", "") or "").strip().lower() in {"1", "true", "yes", "on"}


def default_root() -> Path:
    """信箱根目录：`AP_RELAY_ROOT` 优先，否则 `<仓库>/relay`。"""
    configured = os.environ.get("AP_RELAY_ROOT", "")
    return Path(configured) if configured.strip() else PROJECT_ROOT / "relay"


def default_token_file() -> Path:
    """relay 窗口自己的凭据：`AP_RELAY_TOKEN_FILE` 优先，否则 `data/relay.key`。"""
    configured = os.environ.get("AP_RELAY_TOKEN_FILE", "")
    return Path(configured) if configured.strip() else settings.data_dir / "relay.key"


@dataclass(frozen=True, slots=True)
class RelayStatus:
    """转接此刻是什么状态。`age_seconds` 为空 = 从来没打过心跳。"""

    running: bool
    heartbeat_at: str
    age_seconds: float | None
    pid: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "heartbeat_at": self.heartbeat_at,
            "age_seconds": self.age_seconds,
            "pid": self.pid,
        }


def _read_pid(root: Path) -> int | None:
    try:
        return int((root / PID_NAME).read_text(encoding="utf-8").strip() or 0) or None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """这个 pid 现在还活着吗。

    为什么要它：worker 起来之后要**几秒**才打第一个心跳（导入、开库都慢）。这段空窗里
    光看心跳会判「没在跑」，于是再起一个 —— 两个 worker 读同一个 outbox，同一封信投两遍。
    所以判定要分两档：心跳新鲜 = 在投递；进程还在 = 正在启动，别再加一个。

    Windows 上不能用 ``os.kill(pid, 0)``：那在 Windows 是 TerminateProcess，会把 worker 杀掉。
    """
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_status(root: Path, *, now: float | None = None) -> RelayStatus:
    """只看心跳与 pid 文件：不猜进程、不翻进程表。"""
    current = time.time() if now is None else now
    heartbeat = root / HEARTBEAT_NAME
    try:
        stamp = heartbeat.stat().st_mtime
    except OSError:
        return RelayStatus(running=False, heartbeat_at="", age_seconds=None, pid=_read_pid(root))
    age = max(0.0, current - stamp)
    return RelayStatus(
        running=age <= RUNNING_WINDOW_SECONDS,
        heartbeat_at=datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"),
        age_seconds=round(age, 1),
        pid=_read_pid(root),
    )


def _command(*, url: str, group: str, admins: list[str], token_file: Path, root: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "app.relay.worker",
        "--url",
        url,
        "--group",
        group,
        "--token-file",
        str(token_file),
        "--root",
        str(root),
    ]
    for admin in admins:
        command += ["--admin", admin]
    return command


def ensure_relay_worker(
    *,
    root: Path | None = None,
    url: str = "http://127.0.0.1:8848",
    group: str,
    admins: list[str] | None = None,
    token_file: Path | None = None,
    spawner: Callable[[list[str], Path], int] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """确保转接在跑；已经在跑就什么都不做。

    返回 `{"running": bool, "started": bool, "pid": int|None, "note": str}`：
    `note` 是**给人看的**说法，失败时说明为什么没起来（凭据缺失 / 起来就退了）。
    """
    mailbox_root = Path(root) if root is not None else default_root()
    token = Path(token_file) if token_file is not None else default_token_file()
    admin_list = [item for item in (admins or []) if item]
    status = read_status(mailbox_root, now=now)
    if status.running:
        return {"running": True, "started": False, "pid": status.pid, "note": "已经在跑，不重复启动"}

    # 还没打心跳，但进程还活着：它正在启动。这时候再加一个就会两个 worker 抢同一个 outbox。
    if status.pid and _pid_alive(status.pid):
        return {
            "running": False,
            "started": False,
            "pid": status.pid,
            "note": "已经在启动（进程还活着，等着第一个心跳），不重复启动",
        }

    if not token.exists():
        # 没有 relay 自己的凭据就不能替它发言 —— 这条不能靠"用管理员的令牌顶一下"绕过。
        return {
            "running": False,
            "started": False,
            "pid": status.pid,
            "note": f"没有 relay 窗口的凭据文件（{token}），管理员不能替它发言；先让平台给它签发凭据",
        }

    command = _command(url=url, group=group, admins=admin_list, token_file=token, root=mailbox_root)
    spawn = spawner or _spawn_detached
    try:
        pid = spawn(command, mailbox_root)
    except OSError as problem:
        return {
            "running": False,
            "started": False,
            "pid": status.pid,
            "note": f"起不来：{type(problem).__name__}: {problem}"[:300],
        }

    # 起来之后等一小会儿再复核心跳。真的第一次启动要几秒（导入、开库），所以这里
    # **不把"心跳还没来"当成失败**：starter 已经动手了，下一轮由 pid 存活挡住重复启动。
    deadline = time.time() + STARTUP_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(0.25)
        after = read_status(mailbox_root)
        if after.running:
            return {"running": True, "started": True, "pid": pid, "note": "已启动"}
    return {
        "running": False,
        "started": True,
        "pid": pid,
        "note": "已启动，心跳还没来（第一次启动要几秒）；日志见 data/logs/relay-worker.*.log",
    }


def _spawn_detached(command: list[str], mailbox_root: Path) -> int:
    """起一个不跟着调用方退出的 worker，输出落 data/logs/relay-worker.*.log。"""
    logs = settings.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if os.name == "nt":
        # 不跟着管理员窗口一起死：它是一台"一直在那儿"的邮局。
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    # 用 with 打开：句柄交给子进程之后这边就该关掉，否则每次拉起都漏一个文件句柄。
    with (logs / "relay-worker.out.log").open("a", encoding="utf-8") as out, (
        logs / "relay-worker.err.log"
    ).open("a", encoding="utf-8") as err:
        process = subprocess.Popen(  # noqa: S603 - 命令是这里拼的，不接收外部输入
            command,
            cwd=str(PROJECT_ROOT / "backend"),
            stdout=out,
            stderr=err,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    mailbox_root.mkdir(parents=True, exist_ok=True)
    (mailbox_root / PID_NAME).write_text(str(process.pid), encoding="utf-8")
    return process.pid
