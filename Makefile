# 多 Harness Agent 协作平台 —— MVP
#
# 设计原则：所有门禁类命令（check/test/lint/docstate）必须可在无GUI、无网络下运行。
# Windows 用户用 Git Bash 或 WSL 执行；若无可用 make，直接看下方等价命令。

PY := .venv/Scripts/python.exe
PIP := .venv/Scripts/pip.exe

.PHONY: help install dev test lint fmt fmt-check docstate check clean frontend-install frontend-dev

help:
	@echo "make install   - 创建 venv 并安装依赖"
	@echo "make dev       - 启动后端 (http://127.0.0.1:8848)"
	@echo "make test      - 运行 pytest"
	@echo "make lint      - ruff 静态检查"
	@echo "make fmt       - ruff 自动修复 + 格式化"
	@echo "make docstate  - 只跑规范一致性检查"
	@echo "make check     - 完整门禁 (docstate + lint + test) —— 提交前必须全绿"
	@echo "make clean     - 清理缓存与运行期数据"

install:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements-dev.txt
	@echo "done. next: make dev"

dev:
	cd backend && ../$(PY) -m app.main

test:
	cd backend && ../$(PY) -m pytest

lint:
	cd backend && ../.venv/Scripts/ruff.exe check .
	cd backend && ../.venv/Scripts/ruff.exe format --check .

fmt:
	cd backend && ../.venv/Scripts/ruff.exe check --fix .
	cd backend && ../.venv/Scripts/ruff.exe format .

docstate:
	@$(PY) tools/docstate/docstate.py

# 完整门禁。顺序有意为之：先查规范（最快、最可能失败），再 lint，最后跑测试。
check: docstate lint test
	@echo ""
	@echo "=== 全量门禁通过 ==="

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned (data/ 与 wt/ 未动，删它们需人工确认)"

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev
