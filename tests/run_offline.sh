#!/usr/bin/env bash
# Every test that needs no API key: static checks, unit tests, mock-LLM
# end-to-end tests (scripted model + fault injection), CP3 mock twins and
# contract tests. Suites run in parallel (each has its own mock server).
# Usage: bash tests/run_offline.sh        (exit 0 only if everything passes)
# Bash 3.2 compatible.
set -u
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$TESTS_DIR/.." && pwd -P)"
cd "$ROOT" || exit 1
export PYTHONDONTWRITEBYTECODE=1
PY="${PYTHON:-python3}"
LOGDIR="$(mktemp -d "${TMPDIR:-/tmp}/hw1_offline.XXXXXX")"
START=$(date +%s)

UNIT="tests/unit/test_static.py tests/unit/test_core.py tests/unit/test_sessions_store.py tests/unit/test_cp3_units.py tests/unit/test_adversarial_fixes.py tests/unit/test_cp3_report_fixes.py tests/test_sandbox.py tests/test_verify.py tests/test_csv.py"
E2E="tests/test_offline.py tests/test_edge_cases.py tests/test_sessions.py tests/test_cp3_mock.py tests/test_contract.py"

pids=""
for suite in $UNIT $E2E; do
    name="$(basename "$suite" .py)"
    ( "$PY" -B "$suite" > "$LOGDIR/$name.log" 2>&1; echo $? > "$LOGDIR/$name.rc" ) &
    pids="$pids $!"
done
for p in $pids; do wait "$p"; done

status=0
total_pass=0
total_fail=0
printf '%-28s %-8s %s\n' "suite" "result" "detail"
for suite in $UNIT $E2E; do
    name="$(basename "$suite" .py)"
    rc="$(cat "$LOGDIR/$name.rc" 2>/dev/null || echo 99)"
    log="$LOGDIR/$name.log"
    detail="$(grep -E '^Ran [0-9]+ tests|^[0-9]+ cases:' "$log" | tail -1)"
    n="$(printf '%s' "$detail" | sed -E 's/^Ran ([0-9]+).*/\1/; s/^([0-9]+) cases.*/\1/')"
    case "$n" in ''|*[!0-9]*) n=0 ;; esac
    if [ "$rc" = "0" ]; then
        printf '%-28s %-8s %s\n' "$name" "PASS" "$detail"
        total_pass=$((total_pass + n))
    else
        printf '%-28s %-8s %s\n' "$name" "FAIL" "$detail"
        grep -E '^(FAIL|ERROR|INFRA)' "$log" | head -20 | sed 's/^/    /'
        status=1
        fails="$(grep -cE '^(FAIL|ERROR|INFRA)' "$log")"
        total_fail=$((total_fail + fails))
        total_pass=$((total_pass + n - fails))
    fi
done
END=$(date +%s)
echo "------------------------------------------------------------"
echo "tests passed: $total_pass, failed: $total_fail, duration: $((END - START))s, logs: $LOGDIR"
if [ "$status" -eq 0 ]; then echo "ALL OFFLINE SUITES PASSED"; else echo "SOME OFFLINE TESTS FAILED"; fi
exit "$status"
