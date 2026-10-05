#!/usr/bin/env bash
# 把 worktree 的 .venv 从「junction 指向根目录」换成「各自独立」。
#
# 背景：2026-10-05 23:48 根 .venv 被清空重建，8 个 junction 一起坏，
# 每个人的门禁都莫名失败。新 AI 默认已是独立 venv（new-ai.ps1），
# 这个脚本只用来迁移存量。
#
# 用法：./scripts/migrate-venvs.sh [--dry-run]
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

REQ="backend/requirements-dev.txt"
# 选一个稳定的解释器来源：优先已存在的根 venv，其次 PATH 上的 python
if [ -x ".venv/Scripts/python.exe" ]; then
  BASE=".venv/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
  BASE="$(command -v python)"
else
  echo "找不到可用的 python（根 .venv 不存在且 PATH 上没有 python）" >&2
  exit 1
fi

echo "用 $BASE 作为解释器来源"
echo ""

for d in .worktrees/*/; do
  name=$(basename "$d")
  venv="$d.venv"
  [ -e "$venv" ] || { echo "  $name: 无 .venv，跳过"; continue; }

  if [ -L "$venv" ]; then
    kind="junction -> $(readlink "$venv")"
  elif [ -d "$venv" ]; then
    if [ -x "$venv/Scripts/python.exe" ] && "$venv/Scripts/python.exe" -c 'import pytest' >/dev/null 2>&1; then
      echo "  $name: 已是独立且可用，跳过"
      continue
    fi
    kind="实体目录（不完整）"
  else
    kind="未知"
  fi

  echo "  $name: $kind"
  [ "$DRY" = "1" ] && continue

  # 删掉不完整的实体目录；junction 用 rmdir 删除（不跟随目标）
  rm -rf "$venv" 2>/dev/null || true

  echo "    -> 创建独立 venv（约 1 分钟）..."
  "$BASE" -m venv "$venv"
  "$venv/Scripts/python.exe" -m pip install --quiet --upgrade pip
  "$venv/Scripts/python.exe" -m pip install --quiet -r "$REQ"
  "$venv/Scripts/python.exe" -c 'import pytest, fastapi' || {
    echo "    !! 冒烟测试失败，保留现场供排查" >&2
    exit 1
  }
  echo "    -> 完成，$(ls "$venv/Lib/site-packages" | wc -l) 个包"
done

echo ""
echo "完成。磁盘代价约 106 MB x N。"
