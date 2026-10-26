#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

command -v python3 >/dev/null 2>&1 || {
  echo 'Python 3.11 or newer is required.' >&2
  exit 1
}

python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt

command -v node >/dev/null 2>&1 || {
  echo 'Node.js 18 or newer is required for Agent Office.' >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo 'npm is required for Agent Office.' >&2
  exit 1
}

(cd office && npm ci)
echo 'Setup complete. Run ./start-backend.sh or (cd office && npm run dev).'
