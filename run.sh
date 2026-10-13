#!/usr/bin/env bash
# One-click start for macOS/Linux (also works in Git Bash/MSYS on Windows):
# creates venv, installs deps, starts the gateway.
# Usage:
#   ./run.sh                    start the gateway
#   ./run.sh qoder-login        Qoder device login (one-time)
#   ./run.sh qoder-status       Qoder account / quota status
set -e
cd "$(dirname "$0")"

if [ -z "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi

if [ ! -d .venv ]; then
  echo "[run] creating .venv with $PYTHON"
  "$PYTHON" -m venv .venv
fi

# venv layout differs by platform: bin/ on Unix, Scripts/ on Windows
# (a Windows python invoked from Git Bash produces the Scripts/ layout).
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
  # shellcheck disable=SC1091
  . .venv/Scripts/activate
else
  echo "[run] .venv exists but has no activate script; delete .venv and retry" >&2
  exit 1
fi

echo "[run] installing dependencies"
if ! pip install -q -r requirements.txt; then
  echo "[run] default index failed, retrying with pypi.org"
  pip install -q -i https://pypi.org/simple -r requirements.txt
fi

echo "[run] starting gateway (Ctrl+C to stop)"
exec python -m gateway "$@"
