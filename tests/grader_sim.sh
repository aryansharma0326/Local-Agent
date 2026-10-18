#!/usr/bin/env bash
# Grader simulation (GS-01, GS-03, GS-05; GS-02 with --live).
#   bash tests/grader_sim.sh [--live]
# Builds the zip, checks its contents, extracts it to a path with spaces,
# makes it read-only, then runs the offline suite against the extracted copy
# with /bin/bash and a sanitized environment. --live additionally runs one
# live case per capability with the exact grader command form.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT" || exit 1
LIVE=0
[ "${1:-}" = "--live" ] && LIVE=1
bash scripts/make_submission.sh || exit 1

# GS-03: archive contents
python3 -B - <<'EOF' || exit 1
import sys, zipfile
names = zipfile.ZipFile("dist/hw1_submission.zip").namelist()
need = ["hw1_submission/run_agent.sh", "hw1_submission/readme.md", "hw1_submission/requirements.txt",
        "hw1_submission/agent/main.py", "hw1_submission/tests/run_offline.sh"]
missing = [n for n in need if n not in names]
banned = [n for n in names if any(b in n for b in ("/.git/", "__pycache__", ".agent_state", "/.env", "/dist/",
                                                    "tests/reports/", "/venv/", ".pyc"))]
if missing or banned:
    print("GS-03 FAIL missing=%s banned=%s" % (missing, banned[:5]))
    sys.exit(1)
print("GS-03 PASS: %d entries, required files present, nothing excluded leaked" % len(names))
EOF

BASE="$(mktemp -d "${TMPDIR:-/tmp}/gs.XXXXXX")"
DEST="$BASE/sub with space"
mkdir -p "$DEST"
python3 -B -c "import zipfile,sys; zipfile.ZipFile('dist/hw1_submission.zip').extractall(sys.argv[1])" "$DEST"
SUB="$DEST/hw1_submission"
chmod +x "$SUB/run_agent.sh"
chmod -R a-w "$SUB"
echo "GS-05: /bin/bash is $(/bin/bash --version | head -1)"
# GS-01: full offline suite against the read-only extracted copy, /bin/bash, sanitized env
env -i PATH="/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:$(dirname "$(command -v python3)")" HOME="$BASE" \
    LANG=C TMPDIR="${TMPDIR:-/tmp}" /bin/bash "$SUB/tests/run_offline.sh"
rc=$?
if [ "$LIVE" -eq 1 ]; then
    KEY="${QWEN_API_KEY:-}"
    [ -z "$KEY" ] && [ -f "$HOME/.qwen_key" ] && KEY="$(cat "$HOME/.qwen_key")"
    TASK_DIR="$(mktemp -d)"
    cd "$TASK_DIR" || exit 1
    printf 'month,revenue\nJanuary,120\nFebruary,180\n' > sales.csv
    export OPENAI_BASE_URL="https://llm.montii.me/v1" MODEL="Qwen/Qwen3-14B" QWEN_API_KEY="$KEY"
    bash "$SUB/run_agent.sh" "Read sales.csv and tell me the total of the revenue column." "1001"; echo "exit=$?"
    bash "$SUB/run_agent.sh" "What is the average?" "1001"; echo "exit=$?"
    bash "$SUB/run_agent.sh" "Save a copy of the table as reports/sales_copy.csv." "1001"; echo "exit=$?"
    cmp sales.csv reports/sales_copy.csv && echo "GS-02 copy byte-identical"
    bash "$SUB/run_agent.sh" "Create foo.txt inside the abc directory. The file should contain: foo"; echo "exit=$?"
    printf 'foo' | cmp - abc/foo.txt && echo "GS-02 create exact"
    cd "$ROOT" || exit 1
fi
chmod -R u+w "$BASE"
rm -rf "$BASE"
echo "grader simulation exit: $rc"
exit "$rc"
