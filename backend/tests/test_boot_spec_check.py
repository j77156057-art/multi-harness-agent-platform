"""启动时规范校验：默认告警，严格模式才拦截。

用例走真实启动路径（TestClient 会触发 ``create_app()`` 的 lifespan），并放在
子进程里跑 —— 断言的是进程退出码，而不只是某个函数的返回值。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
REAL_SPEC = PROJECT_ROOT / "docs" / "spec" / "0.3-R10.md"

#: 章节标题齐全、但内容为空：docstate 能正常跑完并报出大量不一致，
#: 不会因为解析器崩溃而掩盖"不一致时到底怎么处理"这件事。
BROKEN_SPEC = """## 10. 状态机

## 11. Claim

## 8. Canonical 数据模型

## 9. 索引

### 37.7 事件 allowlist

## 39. 分配与传阅
"""

_STARTUP_SCRIPT = (
    "from fastapi.testclient import TestClient\n"
    "from app.api.app import create_app\n"
    "with TestClient(create_app(), base_url='http://127.0.0.1') as client:\n"
    "    assert client.get('/').status_code == 200\n"
)


def _run_startup(tmp_path: Path, spec: Path, *, strict: bool) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "AP_SPEC_PATH": str(spec),
            "AP_CHECK_SPEC": "1",
            "AP_SPEC_STRICT": "1" if strict else "0",
            # 全部运行期目录指到 tmp_path，避免启动时写仓库里的 data/ 与 wt/。
            "AP_DATA_DIR": str(tmp_path / "data"),
            "AP_WORKTREE_ROOT": str(tmp_path / "wt"),
            "AP_OVERLAY_ROOT": str(tmp_path / "overlays"),
            "AP_CANONICAL_REPO": str(tmp_path / "canonical.git"),
        }
    )
    pythonpath = [str(BACKEND_ROOT), str(PROJECT_ROOT / "tools")]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    return subprocess.run(
        [sys.executable, "-c", _STARTUP_SCRIPT],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _output(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


def test_consistent_spec_starts_without_warning(tmp_path: Path) -> None:
    proc = _run_startup(tmp_path, REAL_SPEC, strict=False)
    out = _output(proc)
    assert proc.returncode == 0, out
    assert "[ok] 规范一致性检查通过" in out
    assert "[warn] 规范一致性检查失败" not in out
    assert "[error] 规范一致性检查失败" not in out


def test_inconsistent_spec_warns_but_starts(tmp_path: Path) -> None:
    broken = tmp_path / "broken-R10.md"
    broken.write_text(BROKEN_SPEC, encoding="utf-8")
    proc = _run_startup(tmp_path, broken, strict=False)
    out = _output(proc)
    assert proc.returncode == 0, out
    assert "[warn] 规范一致性检查失败" in out
    assert "服务继续启动" in out
    assert "AP_SPEC_STRICT=1" in out
    # 告警要说清哪里不一致，不能只给一句"检查失败"。
    assert "M 集合" in out


def test_inconsistent_spec_blocks_start_in_strict_mode(tmp_path: Path) -> None:
    broken = tmp_path / "broken-R10.md"
    broken.write_text(BROKEN_SPEC, encoding="utf-8")
    proc = _run_startup(tmp_path, broken, strict=True)
    out = _output(proc)
    assert proc.returncode != 0, out
    assert "[error] 规范一致性检查失败" in out
    assert "拒绝启动" in out
    assert "AP_SPEC_STRICT=1" in out
