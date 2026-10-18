#!/usr/bin/env bash
# One end-to-end story across all three checkpoints, run with ONE command:
#
#   bash tests/story_all.sh
#
# CP1 and CP2 share one workspace and one session (77): a fact stated in the first turn is
# used later by a file task, CSV follow-ups rely on earlier turns, and session 78 must not see
# session 77's facts. The CP3 tasks run in their own sub-folders (as the grader runs them), in
# the same session. Every call goes through run_agent.sh exactly like the grader's. Needs the
# key in $QWEN_API_KEY or ~/.qwen_key. About 5-7 minutes (CP3 is most of it).

set -u
SUB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEN="$SUB/tests/adversarial/cp3"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://llm.montii.me/v1}" MODEL="${MODEL:-Qwen/Qwen3-14B}"
export QWEN_API_KEY="${QWEN_API_KEY:-$(cat ~/.qwen_key 2>/dev/null)}"
export AGENT_STATE_DIR="$(mktemp -d)"          # fresh, throwaway conversation store
LOG="$(mktemp)"                                  # agent diagnostics (stderr)
WS="$(mktemp -d)"
cd "$WS" || exit 1
PASS=0; FAIL=0
OUT=""

say() { printf '\n== %s\n' "$1"; }
agent() {  # agent PROMPT [SESSION]: run like the grader, keep stdout in $OUT
    OUT="$(bash "$SUB/run_agent.sh" "$@" 2>>"$LOG")"
    CODE=$?
    local shown="$1"
    [ "${#shown}" -gt 110 ] && shown="${shown:0:60}... (${#1} characters, the task prompt)"
    printf '   $ agent "%s" %s\n   -> exit=%s %s\n' "$shown" "${2:-}" "$CODE" "$OUT"
}
check() {  # check NAME CONDITION...
    local name="$1"; shift
    if eval "$@"; then printf '   PASS %s\n' "$name"; PASS=$((PASS + 1))
    else printf '   FAIL %s\n' "$name"; FAIL=$((FAIL + 1)); fi
}
msg_has() { printf '%s' "$OUT" | grep -qi -- "$1"; }

# ---------------------------------------------------------------- inputs
printf 'old draft\n' > old.txt
printf 'Blue notebooks: 12\nRed notebooks: 7\n' > inventory.txt
mkdir -p logs
printf '09:02 Checkout latency alert fired.\n' > logs/start.txt
printf '09:44 Root cause: misconfigured load balancer health check; fixed at 09:52.\n' > logs/resolution.txt
printf 'month,revenue\nJanuary,120\nFebruary,180\n' > sales.csv
printf 'id,status\n1,shipped\n2,cancelled\n3,pending\n' > orders.csv
echo "Workspace: $WS"

say "CP2 context: a fact stated in a statement-only first turn"
agent "My team lead is Priya Raman." 77
check "statement turn is acknowledged" '[ "$CODE" = 0 ]'

say "CP1 create (uses the earlier fact)"
agent "Create memo.txt containing a one-line note addressed to my team lead by name." 77
check "memo.txt names the team lead" 'grep -qi priya memo.txt 2>/dev/null'

say "CP1 read and answer"
agent "Read inventory.txt. How many blue notebooks are currently in stock?" 77
check "answer is 12 and the file is unchanged" 'msg_has 12 && [ "$(cat inventory.txt)" = "$(printf "Blue notebooks: 12\nRed notebooks: 7")" ]'

say "CP1 multi-file summary"
agent "Use logs/start.txt and logs/resolution.txt to create reports/incident.txt with a summary under 50 words." 77
check "summary exists, under 50 words, names the cause" \
    '[ -f reports/incident.txt ] && [ "$(wc -w < reports/incident.txt)" -lt 50 ] && grep -qi "load balancer" reports/incident.txt'

say "CP1 delete"
agent "Delete old.txt." 77
check "old.txt gone, other files kept" '[ ! -e old.txt ] && [ -f inventory.txt ]'

say "CP2 CSV with follow-ups that rely on earlier turns"
agent "Read sales.csv and tell me the total of the revenue column." 77
check "total is 300" 'msg_has 300'
agent "What is the average?" 77
check "follow-up average is 150" 'msg_has 150'
agent "Save a copy of the table as reports/sales_copy.csv." 77
check "copy equals sales.csv" 'cmp -s reports/sales_copy.csv sales.csv'
agent "In orders.csv, remove every row whose status is cancelled, and save the result as active.csv." 77
check "active.csv keeps rows 1 and 3, orders.csv unchanged" \
    '[ "$(cat active.csv 2>/dev/null)" = "$(printf "id,status\n1,shipped\n3,pending")" ] && grep -q cancelled orders.csv'

say "CP2 session isolation"
agent "Who is my team lead?" 78
check "session 78 does not know the team lead" '! msg_has priya'
agent "Who is my team lead?" 77
check "session 77 still remembers" 'msg_has priya'

say "CP3 programming: the official tally.py task (requirements 1-6 graded)"
mkdir -p cp3_prog && cd cp3_prog
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import prog_specs; open('transactions.csv','wb').write(prog_specs.generate('O', n=4000)); open('TASK.md','w').write(prog_specs.prompt('O'))" "$GEN"
agent "$(cat TASK.md)" 77
REQS="$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import prog_specs; r=prog_specs.score(prog_specs.grade('O','.')); print('%d/%d' % (sum(r.values()), len(r)))" "$GEN")"
echo "   tally.py requirements passed: $REQS"
check "tally.py passes requirements 1-6" '[ "$REQS" = "6/6" ]'
cd "$WS"

say "CP3 debugging: trace one failed request across four logs into REPORT.json"
mkdir -p cp3_debug && cd cp3_debug
python3 -c "import sys, json; sys.path.insert(0, sys.argv[1]); import gen_incident; k,_=gen_incident.write(41,'.'); json.dump(k, open(sys.argv[2],'w'))" "$GEN" "$WS/answer_key.json"
agent "$(cat TASK.md)" 77
KEYS="$(python3 -c "import json,sys; k=json.load(open(sys.argv[1])); r=json.load(open('REPORT.json')); print('%d/%d' % (sum(1 for x,v in k.items() if r.get(x)==v and type(r.get(x)) is type(v)), len(k)))" "$WS/answer_key.json" 2>/dev/null || echo "0/12")"
echo "   REPORT.json keys correct: $KEYS"
check "at least 11 of 12 keys correct" '[ "${KEYS%%/*}" -ge 11 ]'
cd "$WS"

printf '\n%d passed, %d failed  (workspace %s, agent log %s)\n' "$PASS" "$FAIL" "$WS" "$LOG"
[ "$FAIL" = 0 ]
