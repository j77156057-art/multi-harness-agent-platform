"""README 里的命令必须能被复制出来直接跑 —— 这条测试就是那份「可执行文档」。

为什么要有它：README 的示例命令是公开承诺（「复制粘贴就能跑」）。命令写错、参数写漏、
或者代码改了文档没改，只有一条机器能查的路：把命令从 README 原文里抠出来**真的跑一遍**。

怎么跑才算「真的跑」：

- **命令从 README 原文抠**，不是抄一份到测试里 —— 文档改了，测的就是改后的那一句；
- **用子进程跑**（不是 import 进来调 main）：走的是读者复制后那条命令的完整路径
  —— 解释器参数、工作目录、模块可见性、stdout 捕获、退出码，一个不少；
- **数据指向临时目录**（AP_DATA_DIR），不碰真平台数据；跑完还要确认那个目录没被写脏。

断言的口径就是 README 自己写的：人读报告的三面结构、--json 的单文档约定、
以及退出码三态（0 没发现问题 / 1 发现问题 / 2 读不到平台数据）。
"""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.db.migrations import apply_migrations
from app.pool.sources import DEFAULT_CLI_SLOTS

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
README = REPO_ROOT / "README.md"

#: Markdown 代码围栏（用常量拼，免得本文件里出现一串裸围栏）。
FENCE = chr(96) * 3

#: README 里那段「平台给自己造了一个产品」的小节标题。
SECTION = "## 平台给自己造了一个产品"

#: 这两条命令必须在 README 里出现，且能直接跑。
EXPECTED_COMMANDS = (
    "python tools/doctor/doctor.py",
    "python tools/doctor/doctor.py --json",
)

#: --json 的顶层字段（README 与实现共用的那份契约）。
JSON_TOP_FIELDS = ("ok", "findings", "windows", "budget", "ledger", "error")

#: 人读报告里必须出现的三个面与两行固定句式。
FACE_HEADINGS = ("A 窗口面", "B 额度面", "C 账本面")
FIXED_PHRASES = ("结论：", "本面检查了什么")


# ---------------------------------------------------------------------------
# 从 README 里抠命令
# ---------------------------------------------------------------------------


def readme_section() -> str:
    """取出那一小节的正文（从标题到下一个同级标题）。"""
    text = README.read_text(encoding="utf-8")
    start = text.find(SECTION)
    assert start >= 0, f"README 里找不到小节：{SECTION}"
    rest = text[start + len(SECTION) :]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def documented_commands() -> list[str]:
    """README 里那一节里出现的体检命令（原文，保留参数）。"""
    section = readme_section()
    commands: list[str] = []
    for block in section.split(FENCE)[1::2]:
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("python tools/doctor/doctor.py"):
                commands.append(line)
    return commands


# ---------------------------------------------------------------------------
# 假平台数据
# ---------------------------------------------------------------------------


def build_fake_data_dir(root: Path) -> Path:
    """在 root 下造一份「干净的平台数据目录」。

    数据库用**平台自己的迁移**建（不抄 schema），池子声明把内置槽位按名字关掉
    （槽位名单取自平台常量，不写死），用户 key 清单给空表。
    """
    data_dir = root / "data"
    (data_dir / "pool").mkdir(parents=True)
    (data_dir / "user-keys").mkdir(parents=True)

    connection = sqlite3.connect(str(data_dir / "state.db"))
    try:
        apply_migrations(connection)
        connection.commit()
    finally:
        connection.close()

    disabled = [{"name": str(slot["name"]), "enabled": False} for slot in DEFAULT_CLI_SLOTS]
    (data_dir / "pool" / "config.json").write_text(
        json.dumps({"cli_slots": disabled}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (data_dir / "user-keys" / "index.json").write_text(
        json.dumps({"keys": [], "requests": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data_dir


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """目录内容快照（相对路径 → 大小、mtime），用来证明「只读」。"""
    result: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def run_documented(command: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    """按 README 里那条命令跑一次（只把解释器换成本机的 python）。"""
    argv = shlex.split(command)
    argv[0] = sys.executable
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(data_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------


def test_readme_documents_the_doctor_commands() -> None:
    commands = documented_commands()
    assert commands == list(EXPECTED_COMMANDS), (
        f"README 里的体检命令与预期不一致：{commands}；"
        f"应当是 {list(EXPECTED_COMMANDS)}（改了就同步改这条测试）"
    )


def test_readme_documents_the_exit_code_contract() -> None:
    section = readme_section()
    assert "退出码只有三种" in section, "README 没有说清退出码只有三种"
    for code in ("0", "1", "2"):
        assert chr(96) + code + chr(96) in section, f"README 没有写清退出码 {code} 的含义"


def test_readme_command_runs_and_reports_the_three_faces(tmp_path: Path) -> None:
    data_dir = build_fake_data_dir(tmp_path)
    before = snapshot(data_dir)

    result = run_documented(EXPECTED_COMMANDS[0], data_dir)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode in (
        0,
        1,
    ), f"人读命令的退出码应是 0 或 1，实际 {result.returncode}：{result.stdout}{result.stderr}"
    for heading in FACE_HEADINGS:
        assert heading in result.stdout, f"人读报告里没有「{heading}」这一节：{result.stdout}"
    for phrase in FIXED_PHRASES:
        assert phrase in result.stdout, f"人读报告里没有「{phrase}」：{result.stdout}"
    assert snapshot(data_dir) == before, "体检命令往数据目录里写了东西"


def test_readme_json_command_emits_one_document(tmp_path: Path) -> None:
    data_dir = build_fake_data_dir(tmp_path)
    before = snapshot(data_dir)

    result = run_documented(EXPECTED_COMMANDS[1], data_dir)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode in (0, 1, 2), result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert tuple(payload) == JSON_TOP_FIELDS, f"--json 的顶层字段变了：{list(payload)}"
    # 退出码与内容必须自洽：0 只在没有发现也没有读失败时出现。
    if result.returncode == 0:
        assert payload["ok"] is True
        assert payload["findings"] == []
        assert payload["error"] == []
    if result.returncode == 1:
        assert payload["findings"], "退出码 1 但 findings 为空"
    if result.returncode == 2:
        assert payload["error"], "退出码 2 但 error 为空"
    assert snapshot(data_dir) == before, "体检命令往数据目录里写了东西"


def test_readme_command_reports_missing_platform_data(tmp_path: Path) -> None:
    """文档承诺的退出码 2：读不到平台数据时说清「读不到」，而不是崩。"""
    data_dir = tmp_path / "empty-data"
    data_dir.mkdir()

    result = run_documented(EXPECTED_COMMANDS[0], data_dir)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "读不到" in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, result.stderr

    json_result = run_documented(EXPECTED_COMMANDS[1], data_dir)
    assert json_result.returncode == 2, json_result.stdout + json_result.stderr
    payload = json.loads(json_result.stdout)
    assert payload["error"], "--json 在读不到数据时没有给出 error"
    assert payload["ok"] is False
