#!/usr/bin/env bash
# Makefile 的 Bash 等价实现（Windows Git Bash 无 make 时用这个）
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/Scripts/python.exe"

cmd_docstate() { "$PY" tools/docstate/docstate.py; }
cmd_lint()    { (cd backend && ../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .); }
cmd_test()    { (cd backend && ../$PY -m pytest); }
cmd_check()   { cmd_docstate; cmd_lint; cmd_test; echo; echo "=== 全量门禁通过 ==="; }

case "${1:-help}" in
  docstate) cmd_docstate ;;
  lint)     cmd_lint ;;
  test)     cmd_test ;;
  check)    cmd_check ;;
  dev)      (cd backend && ../$PY -m app.main) ;;
  install)  "$PY" -m pip install -r backend/requirements-dev.txt ;;
  *) echo "usage: ./scripts.sh {docstate|lint|test|check|dev|install}"; exit 1 ;;
esac
