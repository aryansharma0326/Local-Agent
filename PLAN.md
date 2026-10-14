# PLAN.md — architecture and gap ranking

## Architecture (end state)

```
run_agent.sh            symlink-safe SCRIPT_DIR, Python ≥3.8 probe, exec agent/main.py
agent/
  main.py               stdout guard, argv, signals + watchdog, router dispatch, session save
  config.py             env parsing (defaults, key fallback), deadlines, call caps, flags
  output.py             single-shot JSON emitter (emit-once, surrogate-safe, clamp)
  llm.py                OpenAI-compatible client: retries/backoff/deadline, call counter,
                        token budgeting, native tool calls + text fallbacks, <think> strip
  router.py             task classification: simple (CP1/CP2) | programming | debugging
  harness.py            CP1/CP2 loop (kept: proven 91/91 live), patched for compound tasks,
                        no-referent guard, thinking off, call cap
  deep.py               CP3 loop: native tool calling, pinned context + compaction, notes,
                        wrap-up, best-artifact restore, deliverable validators
  deeptools.py          CP3 tools: fs (read/read_lines/write/append/edit/list/find),
                        grep/log_stats, run_command/run_python (scratch, pgroup kill,
                        blocklist), notes, report_set (debugging), finish
  prompts.py            CP3 system prompts and playbooks
  profiling.py          CSV anomaly profile + log statistics for prompts
  workspace.py          start snapshot + byte backup, end-of-run audit and restore
  session.py            session store: ordered locations, flock, atomic, schema v2,
                        stale guard, structured working context
  sandbox.py csvtools.py guards.py tools.py verify.py errors.py   (kept, extended)
tests/                  unit + mock E2E + fault injection + CP3 mock twins + live runners
scripts/make_submission.sh
```

Routing: CP3 prompts are recognised by strong signals (deliverable `*.py` with "implement"/"command-line tool"/"fix … tests", or `REPORT.json`/"root cause"/`logs/`). Everything else goes to the proven CP1/CP2 harness, so a CP3 misroute can never regress CP1/CP2.

## Gaps ranked by grading risk

| Rank | Gap | Points at risk | Fix |
|---|---|---|---|
| 1 | No debugging capability | 4 | `deep.py` debugging playbook: harness pre-greps prompt IDs, writes placeholder REPORT.json at once, `report_set` tool with type coercion, verbatim-in-logs validator, word-boundary grep with ID extraction |
| 2 | No programming capability | 4 | `deep.py` programming playbook: data profile before coding, raw-file write fallback, harness smoke-runs every example command from the spec, consistency checks, edit loop, best-version restore, py_compile gate |
| 3 | Thinking enabled on some calls (CLI-10) | 6 (CP1/CP2 reruns) + 8 | always `enable_thinking:false`; re-validate CP1/CP2 live without thinking |
| 4 | No signal/watchdog JSON (OUT-3), no CP3 deadline | all | signal handlers + watchdog thread, 165/285 s deadlines |
| 5 | Compound requests fail (GEN-1/CR-5) | CP1 create/delete | allow several mutations when each target is named in the prompt |
| 6 | Follow-up with no referent guesses (SS-9) | CP2 isolation | deterministic pre-check → error asking for the file |
| 7 | Session store robustness (SS-2..7) | CP2 | rewrite store |
| 8 | LLM client gaps (LLM-4..9) | all | rewrite client |
| 9 | Change audit doesn't restore (LOOP-13) | CP3 hygiene | `workspace.py` |
| 10 | Packaging, .gitignore, readme | deployability | `scripts/make_submission.sh`, docs |

## Phase order

1. Audit ✔ → 2. Plan ✔ → 3. Core (config, output, llm, main, workspace, session, mock server upgrades, unit tests) → 4. CP1 patches → 5. CP2 patches → 6. CP3 (deep loop, tools, profiling, validators, generators, mock twins, live runs) → 7. Hardening (faults, grader sim, packaging) → 8. Report.
