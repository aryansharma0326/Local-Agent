#!/usr/bin/env bash
# Live catalog [KEY]: CP1/CP2 cases (tests/test_live.py) and CP3 generators
# (tests/live_cp3.py) against a real Qwen3-14B backend, with repetitions.
#
#   bash tests/run_live.sh [--reps N] [--only cp1,cp2,cp3,read_,...] [--backend course|openrouter|local]
#                          [--delay S] [--seeds 1,2,3]
#
# A CP1/CP2 case passes only if it passes in every repetition. The report goes
# to tests/reports/live_<timestamp>.md. Key: $QWEN_API_KEY (or ~/.qwen_key);
# for --backend openrouter: $OPENROUTER_API_KEY.  Bash 3.2 compatible.
set -u
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$TESTS_DIR/.." && pwd -P)"
cd "$ROOT" || exit 1
export PYTHONDONTWRITEBYTECODE=1
REPS=3; ONLY="cp1,cp2,cp3"; BACKEND="course"; DELAY=0; SEEDS="1,2,3"
while [ $# -gt 0 ]; do
    case "$1" in
        --reps) REPS="$2"; shift 2 ;;
        --only) ONLY="$2"; shift 2 ;;
        --backend) BACKEND="$2"; shift 2 ;;
        --delay) DELAY="$2"; shift 2 ;;
        --seeds) SEEDS="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
case "$BACKEND" in
    course) export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://llm.montii.me/v1}" MODEL="${MODEL:-Qwen/Qwen3-14B}" ;;
    openrouter) export OPENAI_BASE_URL="https://openrouter.ai/api/v1" MODEL="qwen/qwen3-14b" QWEN_API_KEY="${OPENROUTER_API_KEY:-}" ;;
    local) export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://localhost:11434/v1}" MODEL="${MODEL:-qwen3:14b}" ;;
    *) echo "unknown backend $BACKEND" >&2; exit 2 ;;
esac
if [ -z "${QWEN_API_KEY:-}" ] && [ -f "$HOME/.qwen_key" ] && [ "$BACKEND" = "course" ]; then
    QWEN_API_KEY="$(cat "$HOME/.qwen_key")"; export QWEN_API_KEY
fi
mkdir -p tests/reports
TS="$(date +%Y%m%d_%H%M%S)"
REPORT="tests/reports/live_$TS.md"
{
    echo "# Live run $TS"
    echo "backend=$BACKEND model=$MODEL reps=$REPS only=$ONLY seeds=$SEEDS"
    echo
} > "$REPORT"
OLDIFS="$IFS"; IFS=","; set -- $ONLY; IFS="$OLDIFS"
CP12=""; CP3=0
for f in "$@"; do
    if [ "$f" = "cp3" ]; then CP3=1; else CP12="$CP12 $f"; fi
done
status=0
if [ -n "$CP12" ]; then
    r=1
    while [ "$r" -le "$REPS" ]; do
        echo "== CP1/CP2 rep $r/$REPS ($CP12)"
        # shellcheck disable=SC2086
        python3 -B tests/test_live.py $CP12 > "tests/reports/live_${TS}_cp12_rep$r.log" 2>&1 || status=1
        grep -E '^(FAIL|INFRA)|cases:' "tests/reports/live_${TS}_cp12_rep$r.log" | tee -a "$REPORT"
        r=$((r + 1))
        [ "$DELAY" != "0" ] && sleep "$DELAY"
    done
fi
if [ "$CP3" -eq 1 ]; then
    echo "== CP3 debugging" | tee -a "$REPORT"
    python3 -B tests/live_cp3.py debug --seeds "$SEEDS" --scenarios basic,retry,decoy_ids,decoy_class,json_lines,mixed_ts,all \
        --reps "$REPS" 2>&1 | grep -vE '^\s' | tee -a "$REPORT"
    echo "== CP3 programming" | tee -a "$REPORT"
    python3 -B tests/live_cp3.py prog --seeds "$SEEDS" --variants OFFICIAL,A,B,C --reps "$REPS" 2>&1 | grep -vE '^\s' \
        | tee -a "$REPORT"
fi
echo "report: $REPORT"
exit "$status"
