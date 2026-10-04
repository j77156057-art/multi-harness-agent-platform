"""pytest 全局fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent

# 让 `import app` 与 `import docstate` 都能工作
for p in (str(BACKEND_ROOT), str(PROJECT_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def spec_path(project_root: Path) -> Path:
    path = project_root / "docs" / "spec" / "0.3-R10.md"
    if not path.exists():
        pytest.skip(f"规范文件不存在: {path}")
    return path
