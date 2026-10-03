#!/usr/bin/env bash
# Entry point for the file-management agent.
# Usage: bash run_agent.sh "<natural-language prompt>"
# The current working directory is the task workspace; source files are
# located relative to this script, never relative to the workspace.
# The agent runs unattended: its only input is the prompt argument, stdin is
# never read, and it never waits for a human.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PYTHONDONTWRITEBYTECODE=1

PY=""
for cand in python3 python /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
        PY="$cand"
        break
    fi
done
if [ -z "$PY" ]; then
    printf '%s\n' '{"status": "error", "message": "Python 3.8+ is not installed."}'
    exit 1
fi

exec "$PY" "$SCRIPT_DIR/agent/main.py" "$@" < /dev/null
