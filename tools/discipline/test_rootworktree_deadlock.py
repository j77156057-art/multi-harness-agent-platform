# -*- coding: utf-8 -*-
"""rootworktree 组「合并时把自己锁死」的回归测试。

背景（2026-10-07 实测）：mergeguard 合并时用**子进程**跑
`tools/discipline/discipline.py`，此时 `sys.path[0]` 是 `tools/discipline`，
仓库根不在 path 里，`from tools.mergeguard import guard` 抛
ModuleNotFoundError → fail-closed 分支判「无法证明合并归属，不放行」→
门禁红 → mergeguard 不提交 → MERGE_HEAD 留着 → 下一轮仍红，**死锁**。

复现这个前提**必须用子进程跑脚本**：`python -c` 的 `sys.path[0]` 是空串
（= cwd = 仓库根），导入反而成功 —— 用`-c` 测会得到假绿。
"""
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join("tools", "discipline", "discipline.py")


def _load():
    """按测试文件自己的方式加载 discipline（避免包目录遮蔽）。"""
    spec = importlib.util.spec_from_file_location(
        "discipline_deadlock_probe",
        os.path.join(REPO, "tools", "discipline", "discipline.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_exemption_does_not_fail_on_import():
    """豁免判定**不得**因导入 mergeguard 失败而走进 except 分支。

    这是死锁的直接成因：导入失败被报成「无法证明合并归属 → 不放行」。
    修法是在 import 前把仓库根插入 sys.path。
    """
    module = _load()
    # 用一个不存在的根路径，绕开 `Path(__file__)` 的上溯 ——
    # 验证的是 except 分支不该被「路径推不出」这件事误触。
    ok, reason = module._merge_exemption_verdict(REPO, [])
    assert "无法导入 mergeguard" not in reason, (
        "导入失败被当成合并归属不明 —— 合并时门禁会把自己锁死。reason=%s" % reason
    )


def test_import_works_when_discipline_runs_as_script():
    """以mergeguard 的方式跑门禁脚本时，rootworktree 组不得报导入失败。

    子进程跑脚本，`sys.path[0]` = `tools/discipline`。修复后应能导入。
    """
    proc = subprocess.run(
        [sys.executable, SCRIPT],
        cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    combined = proc.stdout + proc.stderr
    # 脚本方式运行时根目录已在 path里（脚本自身把根插进去了），
    # 所以这里直接要求「无法导入 mergeguard」不出现。
    assert "无法导入 mergeguard" not in combined, (
        "脚本方式下报导入失败 —— merge 时会死锁。输出：\n" + combined
    )


def test_sys_path_root_insertion_is_present():
    """守住修复本身：仓库根必须被插进 sys.path，否则将来重构容易改回去。"""
    module = _load()
    root = os.path.abspath(REPO)
    # 先删掉再调用，模拟「干净进程」的情形
    saved = list(sys.path)
    try:
        while root in sys.path:
            sys.path.remove(root)
        # 有 MERGE_HEAD 才走到导入那一步；这里不构造仓库，
        # 直接验插入动作本身。
        module._ensure_repo_root_on_path()
        assert root in sys.path, "仓库根没被插入 sys.path"
    finally:
        sys.path[:] = saved


def test_no_merge_state_means_no_exemption_needed(tmp_path):
    """无MERGE_HEAD 时不需要豁免 —— 确认豁免路径与常规路径不混淆。"""
    module = _load()
    ok, reason = module._merge_exemption_verdict(str(tmp_path), [])
    assert ok is False
    # 没 MERGE_HEAD 的 tmp 目录：读不到锁就判不放行，但不能是「导入失败」
    assert "无法导入 mergeguard" not in reason


def test_unmerged_entries_always_block():
    """有未合并条目一律不放行 —— 这条不能被导入修复带松。"""
    module = _load()
    ok, reason = module._merge_exemption_verdict(REPO, ["UU TASKS.md"])
    assert ok is False
    assert "未合并条目" in reason