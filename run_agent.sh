#!/usr/bin/env bash
# Entry point for the HW1 agent.
#   bash run_agent.sh "<prompt>"                (Checkpoint 1: stateless)
#   bash run_agent.sh "<prompt>" "<session id>" (Checkpoint 2/3: persistent session)
# The current working directory is the task workspace. Source files are found
# relative to this script (symlinks resolved), never relative to the workspace.
# LF line endings, Bash 3.2 compatible. Nothing but the agent's single JSON
# object is ever written to stdout.

# Re-exec under bash if started as `sh run_agent.sh ...`.
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    LINK_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    case "$SOURCE" in
        /*) ;;
        *) SOURCE="$LINK_DIR/$SOURCE" ;;
    esac
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONSTARTUP PYTHONPATH PYTHONHOME 2>/dev/null

PY=""
for cand in python3 python /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$cand" >/dev/null 2>&1 && \
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done
if [ -z "$PY" ]; then
    printf '%s\n' '{"status": "error", "message": "Python 3.8+ is not installed."}'
    exit 1
fi

# -B: never write bytecode (the submission dir may be read-only).
# stdin is /dev/null: the agent runs unattended and never waits for input.
exec "$PY" -B "$SCRIPT_DIR/agent/main.py" "$@" < /dev/null
