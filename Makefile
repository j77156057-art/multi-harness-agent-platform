# 多 Harness Agent 协作平台 —— MVP
#
# 设计原则：所有门禁类命令（check/test/lint/docstate）必须可在无GUI、无网络下运行。
# Windows 用户用 Git Bash 或 WSL 执行；若无可用 make，直接看下方等价命令。

PY := .venv/Scripts/python.exe
PIP := .venv/Scripts/pip.exe

.PHONY: help install dev test lint fmt fmt-check docstate discipline check merge lock clean smoke flow-regression

help:
	@echo "make install   - 创建 venv 并安装依赖"
	@echo "make dev       - 启动后端 (http://127.0.0.1:8848)"
	@echo "make test      - 运行 pytest"
	@echo "make lint      - ruff 静态检查"
	@echo "make fmt       - ruff 自动修复 + 格式化"
	@echo "make docstate  - 只跑规范一致性检查"
	@echo "make discipline- 只跑协作纪律检查"
	@echo "make check     - 完整门禁 (docstate + discipline + lint + test) —— 提交前必须全绿"
	@echo "make smoke     - 真栈冒烟 (真后端 + vite + playwright，需浏览器与网络；不进 check)"
	@echo "make flow-regression - 接入前后判定一致性回归（手动；不进 check）"
	@echo "make merge BR  - 受控地把分支合入 main（带互斥锁，须在根工作区执行）"
	@echo "make lock      - 打印当前合并锁状态"
	@echo "make clean     - 清理缓存与运行期数据"

install:
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements-dev.txt
	@echo "done. next: make dev"

dev:
	cd backend && ../$(PY) -m app.main

# 并行跑：串行 25 分钟 → 4 worker 约 10 分钟。--dist loadfile 按文件分片，
# 模块级状态不跨文件互相污染。AP_TEST_WORKERS=1 可退回串行（怀疑顺序依赖时）。
test:
	cd backend && ../$(PY) -m pytest -n $(or $(AP_TEST_WORKERS),4) --dist loadfile

lint:
	cd backend && ../.venv/Scripts/ruff.exe check .
	cd backend && ../.venv/Scripts/ruff.exe format --check .

fmt:
	cd backend && ../.venv/Scripts/ruff.exe check --fix .
	cd backend && ../.venv/Scripts/ruff.exe format .

docstate:
	@$(PY) tools/docstate/docstate.py

discipline:
	@$(PY) tools/discipline/discipline.py

# 受控合并入口。必须经它合 main，不要裸跑 git merge——见 docs/dev-convention.md
# 「共享 main 的合并必须走受控入口」。锁在 git common dir，跨 worktree 生效。
merge:
	@$(PY) tools/mergeguard/cli.py merge $(BR) $(if $(TASK),--task-id $(TASK),)

lock:
	@$(PY) tools/mergeguard/cli.py status

# 完整门禁。顺序有意为之：先查规范（最快、最可能失败），再 lint，最后跑测试。
#
# 注意：discipline 曾经在 这里缺席，导致 `make check` 比 `./scripts.sh check`
# 弱一档（少一道协作纪律检查）。两个入口必须等价，改一处就要改另一处。
check: docstate discipline lint test
	@echo ""
	@echo "=== 全量门禁通过 ==="

# 真栈冒烟单列：check 必须离线可跑，smoke 要真起栈 + 浏览器，二者不混。
# PYTHON 指到本仓库 venv（ci-stack 默认找 PATH 上的 python，会拿错解释器）。
smoke:
	cd office && PYTHON=../$(PY) node scripts/ci-stack.mjs

# 判定一致性回归：v5 冻结判据 vs 接入后的交付验收层。手动/发布流程用，
# 不进 check（它比对历史落盘产物，不该挡住日常门禁）。
flow-regression:
	cd backend && PYTHONPATH=.. ../$(PY) scripts/experience_acceptance_flow_regression.py

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned (data/ 与 wt/ 未动，删它们需人工确认)"

# The standalone frontend was removed on 2026-10-19; the human UI is Agent Office (4174).
