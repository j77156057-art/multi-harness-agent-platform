#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -x .venv/bin/python ]]; then
  echo '[backend] .venv is missing; run ./setup.sh first.' >&2
  exit 2
fi

cd backend
exec ../.venv/bin/python -m app.main
