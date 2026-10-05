#!/usr/bin/env bash
# Makefile 的 Bash 等价实现（Windows Git Bash 无 make 时用这个）
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/Scripts/python.exe"

cmd_docstate()   { "$PY" tools/docstate/docstate.py; }
cmd_discipline() { "$PY" tools/discipline/discipline.py; }
cmd_lint()    { (cd backend && ../.venv/Scripts/ruff.exe check . && ../.venv/Scripts/ruff.exe format --check .); }
cmd_test()    { (cd backend && ../$PY -m pytest); }
cmd_check()   { cmd_docstate; cmd_discipline; cmd_lint; cmd_test; echo; echo "=== 全量门禁通过 ==="; }
cmd_merge()   { shift; "$PY" tools/mergeguard/cli.py merge "$@"; }
cmd_lock()    { "$PY" tools/mergeguard/cli.py status; }

case "${1:-help}" in
  docstate)   cmd_docstate ;;
  discipline) cmd_discipline ;;
  lint)     cmd_lint ;;
  test)     cmd_test ;;
  check)    cmd_check ;;
  merge)    shift; cmd_merge "$@" ;;
  lock)     cmd_lock ;;
  dev)      (cd backend && ../$PY -m app.main) ;;
  install)  "$PY" -m pip install -r backend/requirements-dev.txt ;;
  new-ai)   pwsh -File scripts/new-ai.ps1 -Harness "${2:-}" ;;
  *) echo "usage: ./scripts.sh {docstate|discipline|lint|test|check|merge <branch> [--task-id X]|lock|dev|install|new-ai <harness>}"; exit 1 ;;
esac
