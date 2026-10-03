#!/usr/bin/env bash
# Run every test suite that does not need the live Qwen endpoint.
# Usage: bash tests/run_offline.sh
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
status=0
for suite in tests/test_sandbox.py tests/test_verify.py tests/test_csv.py tests/test_offline.py tests/test_edge_cases.py tests/test_sessions.py; do
    echo "=== $suite"
    out="$(python3 "$suite" 2>&1)"
    rc=$?
    printf '%s\n' "$out" | grep -vE '^(PASS  |\.*$|\.*\[agent\])'
    [ "$rc" -eq 0 ] || status=1
done
if [ "$status" -eq 0 ]; then echo "ALL OFFLINE SUITES PASSED"; else echo "SOME OFFLINE TESTS FAILED"; fi
exit "$status"
