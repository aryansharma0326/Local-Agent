# AUDIT.md — audit of the existing agent (Phase 1)

Audit date: 2026-10-14. Branch: `claude/agentic-ai-homework-audit-86a82b` (base `7245cb5`, the Checkpoint 1/2 submission).

Sources of truth: `docs/assignment/CP1.md`, `CP2.md`, `CP3.md`. Engineering spec: `docs/CLAUDE_CODE_PROMPT.md` and `docs/TEST_PLAN.md`.
Where the spec and the assignment disagree, the assignment wins (see "Conflicts" at the end).

## 1. Inventory (before changes)

| File | Lines | Purpose |
|---|---|---|
| `run_agent.sh` | 27 | Bash entry point. Finds Python ≥ 3.8, sets `PYTHONUTF8/PYTHONIOENCODING/PYTHONDONTWRITEBYTECODE`, `exec`s `agent/main.py`. |
| `agent/main.py` | 96 | Stdout guard, argv parsing, session load/save, top-level exception → JSON. |
| `agent/llm.py` | 171 | urllib client for `/chat/completions`; retries; `<think>` stripping. **Turns thinking ON for read turns.** |
| `agent/harness.py` | 599 | CP1/CP2 loop: one JSON tool call per model reply (content protocol, not native tools), max 12 steps, one mutation per task, guardrails, finish checks, on-disk verification. |
| `agent/guards.py` | 400 | Lenient JSON parsing, path-token extraction, literal-content restoration, word/char limit parsing and trimming. |
| `agent/tools.py` | 276 | Tool registry + schemas: list/read/read_files/write/delete/copy, csv_aggregate/row_sum/update_cell/delete_rows, finish. Arg aliasing. |
| `agent/sandbox.py` | 236 | Path normalization, symlink-safe resolution, atomic writes, delete, read, listing, unique-basename fallback for reads. |
| `agent/csvtools.py` | 309 | Deterministic CSV engine with `Decimal`, record-level byte preservation, BOM/CRLF/delimiter handling. |
| `agent/session.py` | 145 | Session history store (one JSON per workspace+id), atomic write, 24 h expiry. |
| `agent/verify.py` | 121 | Workspace snapshot, write/delete verification, unexpected-change detection (detect only, no restore). |
| `agent/errors.py` | 5 | `AgentError`. |
| `tests/*.py`, `tests/run_offline.sh` | ~4,500 | unittest + scripted-mock E2E (CP1/CP2) and live tests. |
| `readme.md` | 465 | Documentation and earlier results. |
| `requirements.txt` | 1 | Comment: no dependencies. |

No `.gitignore`, no packaging script, no CP3 code at all.

## 2. Test results observed during the audit

| Run | Result |
|---|---|
| `bash tests/run_offline.sh` (macOS, Python 3.13.2, bash 3.2.57) | 363/363 pass (sandbox 53, verify 24, csv 61, offline 56, edge 107, sessions 62); 2 min 29 s |
| Live smoke: "Read inventory.txt. How many blue notebooks…" against `https://llm.montii.me/v1` | success, `12`, 8.0 s, 2 model calls |
| Live probe: native `tools` request | the endpoint (vLLM) returns native `tool_calls` (`finish_reason: tool_calls`) |
| Live probe: decode throughput | ≈ 25.6 completion tokens/s (1,399 tokens in 54.6 s) — a 6k-token file write would take ~4 min, so CP3 code must be written compactly |

Tools not available on this machine: `python3.8`, `docker`, `shellcheck`, `uv`, `ollama`. Python-3.8 compatibility is therefore checked statically (AST scan for 3.9+ syntax/APIs) rather than by running 3.8.

## 3. Key findings (ranked by grading risk)

1. **CP3 is entirely missing** (programming + debugging = 8 of 14 points). No shell/test runner, no grep/read_lines, no context compaction, no 120-call accounting, 12-step cap, 170 s budget.
2. **CLI-10 violated**: `llm.py` enables Qwen thinking (`enable_thinking: true`) on read/summary turns, appends `/no_think` to every prompt, and drops `chat_template_kwargs` entirely after an HTTP 400. CP3 makes "thinking disabled on every request" mandatory for the final submission, and the final submission re-runs CP1/CP2.
3. **GEN-1/CR-5 broken**: the harness allows only one file mutation per task, so "create a.txt … and b.txt …" or "create X then delete Y" fail.
4. **No signal handling / watchdog (OUT-3)**: a SIGTERM from the grader produces no JSON.
5. **Change audit detects but never restores (LOOP-13)**; fine for CP1/CP2 (tools are narrow) but unsafe once CP3 adds a shell.
6. **CLI-2/CLI-5 gaps**: extra argv words are glued into the prompt; a non-numeric session id is silently ignored; all three env vars are mandatory (no defaults, no `OPENAI_API_KEY` fallback).
7. **SS-9 missing**: a follow-up like "What is the average?" in a session with no history lets the model pick any CSV in the workspace — this looks like leakage to an isolation test.
8. **RQ-7 partial**: non-UTF-8 files are decoded with `errors="replace"` (latin-1 `Zoë` → `Zo�`).
9. `run_agent.sh` invoked via a symlink resolves `SCRIPT_DIR` to the symlink's directory (CT-08).
10. No packaging script, no `.gitignore`, no secrets scan.

Decision per module: **keep and patch** `sandbox.py`, `csvtools.py`, `guards.py`, `tools.py`, `harness.py` (proven 91/91 live on CP1/CP2); **rewrite** `llm.py` (client contract), `main.py` (signals, emit-once), `session.py` (locking, stale guard, structured context); **add** `config.py`, `router.py`, `output.py`, `workspace.py` (audit+restore), `deep.py` (CP3 long-horizon loop with native tool calling), `deeptools.py` (shell, grep, read_lines, edit, notes), `prompts.py`, `verify.py` extensions (REPORT.json, deliverables).

## 4. Requirement Traceability Matrix

Status at audit time. The "Final" columns are filled in at the end of the work (§14 report).

| Req ID | Requirement (short) | Initial status | Initial evidence / notes | Final status | Final evidence |
|---|---|---|---|---|---|
| CLI-1 | run_agent.sh at root, LF, +x, works via `bash` | partial | LF/+x OK; symlink invocation breaks SCRIPT_DIR | | |
| CLI-2 | argv[1] prompt, argv[2] optional session, extras ignored+logged | partial | `main.parse_args` joins extra args into prompt; non-digit id ignored | | |
| CLI-3 | cwd is the workspace; ops stay inside after symlink resolution | done | `sandbox.resolve`, tests `t_symlink_dir_escape` | | |
| CLI-4 | sources resolved relative to run_agent.sh | done | `run_agent.sh` SCRIPT_DIR | | |
| CLI-5 | env config with defaults, key fallback, fail-fast | partial | all three vars required; no OPENAI_API_KEY fallback | | |
| CLI-6 | exactly one JSON object on stdout | done | `main.main`, `t_no_stdout_noise_on_success` | | |
| CLI-7 | exit code ⇔ status | done | `t_exit_code_matches_status_for_every_failure_kind` | | |
| CLI-8 | internal deadline 165 s / 285 s, AGENT_TIME_BUDGET | partial | fixed 170 s | | |
| CLI-9 | ≤120 successful calls; internal cap 110 | missing | no counter | | |
| CLI-10 | enable_thinking=false on every request | **broken** | `llm.chat(thinking=True)` on read turns; kwarg dropped on 400 | | |
| CLI-11 | only Qwen3-14B endpoint | done | only urllib call in `llm.py` | | |
| CLI-12 | bash 3.2 / Python 3.8 compatible | untested | bash 3.2 OK on macOS; 3.8 unchecked | | |
| CLI-13 | stdlib only, requirements.txt comment | done | `requirements.txt` | | |
| CLI-14 | empty/whitespace prompt → error | done | `t_missing_prompt` | | |
| LLM-1 | URL build, headers, UA `hw1-agent/1.0` | partial | UA `file-agent/1.0` | | |
| LLM-2 | body fields: temp, top_p, max_tokens, tools, tool_choice | partial | no tools/top_p | | |
| LLM-3 | backend portability, AGENT_NO_THINK_SUFFIX opt-in | partial | `/no_think` always appended | | |
| LLM-4 | retry/backoff/jitter, Retry-After, never past deadline | partial | 3 attempts, timeouts not retried | | |
| LLM-5 | 401/403/404 fail fast | partial | 404 retried as generic | | |
| LLM-6 | context-length 400 → shrink + retry | missing | 400 drops chat_template_kwargs instead | | |
| LLM-7 | per-request timeout min(remaining−safety,120) | partial | | | |
| LLM-8 | counter counts successes only, `calls_used` | missing | | | |
| LLM-9 | token estimate, dynamic max_tokens ≤ 40,960 | missing | | | |
| LLM-10 | desired max_tokens per purpose | partial | | | |
| LLM-11 | native tool_calls + `<tool_call>`/bare/fenced fallbacks | partial | content protocol only + first native call | | |
| LLM-12 | argument repair (fences, trailing commas, literal_eval) | done | `guards._loads_lenient` | | |
| LLM-13 | strip `<think>` incl. unterminated | done | `llm.strip_think` | | |
| LLM-14 | finish_reason=length → don't execute partial call | partial | salvage for writes only | | |
| LLM-15 | raw `FILE: path` fenced-block fallback | missing | | | |
| LLM-16 | unknown tool → list valid tools | done | `t_unknown_tool` | | |
| LOOP-1 | router → playbook → loop → verify → finish | partial | single CP1/CP2 loop | | |
| LOOP-2 | pre-fetch named files, CSV profile, tree listing | partial | listing only | | |
| LOOP-3 | repetition detection + nudge | partial | identical failure ×3 stops | | |
| LOOP-4 | premature finish with missing deliverable → push back | done | `_pending_output`, `t_summary_finish_without_writing_rejected` | | |
| LOOP-5 | talk-without-action nudge | partial | invalid-call nudge only | | |
| LOOP-6 | wrap-up on low time/calls | partial | time only | | |
| LOOP-7 | keep best artifact | missing | | | |
| LOOP-8 | pinned context layout | missing | | | |
| LOOP-9 | tool output truncation head+tail | partial | 60k char head-only truncation | | |
| LOOP-10 | compaction of old exchanges | missing | | | |
| LOOP-11 | deliverable extraction + existence check | partial | `_pending_output` | | |
| LOOP-12 | capability validators | partial | word limit only | | |
| LOOP-13 | workspace audit + restore | partial | detect only (`verify.check_workspace`) | | |
| TOOL-1 | every tool path goes through sandbox | done | `sandbox.resolve` | | |
| TOOL-2 | tool errors become results, never raise | done | `Harness._execute` | | |
| TOOL-3 | tools log to stderr with duration | partial | | | |
| TOOL-4 | list_dir hides internal entries | done | `sandbox.listing` | | |
| TOOL-5 | read_file encoding detection + pagination | partial | 60k truncation | | |
| TOOL-6 | read_lines | missing | | | |
| TOOL-7 | write_file/append_file atomic + read-back | partial | no append | | |
| TOOL-8 | edit_file exact replace with near-miss hint | missing | | | |
| TOOL-9 | delete_file allowlist, files only | done | `_guard_delete` | | |
| TOOL-10 | copy_file byte copy | done | `t_csv_*copy*` | | |
| TOOL-11 | make_dir | missing | | | |
| TOOL-12 | grep / find_files / log_stats | missing | | | |
| TOOL-13 | csv_op family | partial | 4 separate tools, no lookup/median/multi-cond | | |
| TOOL-14 | run_command sandboxed (pgroup kill, DEVNULL, blocklist) | missing | | | |
| TOOL-15 | run_python | missing | | | |
| TOOL-16 | notes ledger | missing | | | |
| TOOL-17 | finish verified by harness | done | `Harness._finish` | | |
| TOOL-18 | only relevant tools exposed per task type | missing | | | |
| CR-1 | content extraction rules | done | `guards.fix_content`, `t_create_*` | | |
| CR-2 | no added trailing newline (D-1) | done | `t_create_simple` | | |
| CR-3 | parents created, overwrite | done | `t_create_nested`, `t_create_overwrites_only_target` | | |
| CR-4 | path resolution incl. "inside the abc directory" | done | model + `conflicting_prompt_path` | | |
| CR-5 | multiple files in one prompt | **broken** | one mutation per task | | |
| CR-6 | read-back verification | done | `verify.check_write` | | |
| CR-7 | outside-workspace refused | done | `t_traversal_create`, `t_absolute_path` | | |
| DL-1 | unlink only, never dirs/parents | done | `sandbox.delete_file` | | |
| DL-2 | resolution order incl. unique basename | partial | no basename fallback in delete | | |
| DL-3 | exact name matching | done | `t_delete_similar_name_blocked` | | |
| DL-4 | siblings unchanged | done | `t_delete_nested_siblings_kept` | | |
| DL-5 | missing target → error | done | `t_delete_missing` | | |
| DL-6 | directory refused, symlink unlinked | done | `t_delete_directory_refused`, `t_delete_symlink_removes_link_only` | | |
| DL-7 | delete allowlist guard | done | `t_hallucinated_delete_rejected` | | |
| RQ-1 | read-only | done | `t_question_cannot_modify_the_csv` | | |
| RQ-2 | all requested facts verbatim | partial | prompt-level only | | |
| RQ-3 | arithmetic via tool | partial | CSV only | | |
| RQ-4 | superseding info | untested | | | |
| RQ-5 | not-in-file → success, missing file → error | done | `t_answer_not_in_file_is_success`, `t_read_missing` | | |
| RQ-6 | large files chunked | partial | truncation | | |
| RQ-7 | encoding fallback chain | partial | latin-1 decoded with U+FFFD | | |
| RQ-8 | formats | done | | | |
| SM-1 | read all sources, missing → error | done | `t_summary_must_read_all_sources`, `t_summary_missing_source` | | |
| SM-2 | constraint parsing | partial | words/chars only | | |
| SM-3 | aim ≤85% of limit | partial | retry target 75% | | |
| SM-4 | plain text, no preamble/fences | done | `t_summary_code_fence_and_preamble_stripped` | | |
| SM-5 | faithfulness | untested | | | |
| SM-6 | deterministic validators + revision loop | partial | word limit only | | |
| SM-7 | parents created, sources untouched | done | | | |
| SS-1 | no id → stateless | done | `t_no_session_id_is_stateless` | | |
| SS-2 | store outside workspace, ordered fallbacks | partial | different order; workspace fallback last | | |
| SS-3 | file naming, exact-string ids | partial | numeric only | | |
| SS-4 | stale-session guard, AGENT_SESSION_SCOPE | partial | workspace in filename hash; no switch | | |
| SS-5 | flock, atomic, schema version, corrupt recovery | partial | atomic only | | |
| SS-6 | turn record incl. structured context | partial | actions only | | |
| SS-7 | inject recent turns + context | partial | | | |
| SS-8 | reference resolution | done | `t_assignment_three_turn_example` | | |
| SS-9 | no referent → error | missing | | | |
| SS-10 | absolute isolation | done | `t_different_session_ids_are_separate` | | |
| CV-1 | robust parsing | done | `test_csv.py` | | |
| CV-2 | profile | missing | | | |
| CV-3 | column matching incl. prefix | partial | no unique-prefix | | |
| CV-4 | numeric parsing with Decimal | done | `test_csv.py` | | |
| CV-5 | op set | partial | no median/lookup/multi-condition | | |
| CV-6 | ID column choice, duplicates | partial | | | |
| CV-7 | condition semantics | done | | | |
| CV-8 | byte preservation on write | done | `t_csv_semicolon_and_crlf_preserved` | | |
| CV-9 | copy byte-for-byte; self-copy no-op | partial | self-copy is an error | | |
| CV-10 | all removed → header; none → identical | done | | | |
| CV-11 | number formatting | partial | | | |
| CV-12 | re-parse after write | partial | | | |
| PG-1..PG-9 | programming tasks | missing | no CP3 code | | |
| DB-1..DB-8 | debugging tasks | missing | no CP3 code | | |
| GEN-1 | compound requests | **broken** | one mutation per task | | |
| GEN-2 | file contents are data | done | `t_delete_injected_by_file_content_refused` | | |
| GEN-3 | unclear → error | done | | | |
| SAFE-1 | path sandboxing | done | `test_sandbox.py` | | |
| SAFE-2 | no network except LLM | done | no shell exists yet | | |
| SAFE-3 | no scratch in workspace | done | `t_create_leaves_no_temp_files` | | |
| SAFE-4 | logs to stderr, AGENT_LOG_FILE | partial | | | |
| SAFE-5 | change audit + restore | partial | detect only | | |
| SAFE-6 | key never logged | done | | | |
| OUT-1 | stdout redirected, single emit | done | `main.main` | | |
| OUT-2 | top-level except → error JSON | done | | | |
| OUT-3 | signals + watchdog | missing | | | |
| OUT-4 | safe serialization, clamp | partial | no clamp | | |
| OUT-5 | status/exit mapping | done | | | |
| PKG-1 | make_submission.sh | missing | | | |
| PKG-2 | pre-package checks | missing | | | |
| PKG-3 | readme contents | partial | no CP3, no design-decision table | | |
| PKG-4 | .gitignore | missing | | | |

## 5. Conflicts between the spec and the assignment (assignment wins)

- **C-1 Thinking.** CP1/CP2 did not require thinking off; CP3 requires it on every request for the final submission. We disable it everywhere.
- **C-2 Session ids.** The assignment says ids are numeric; the spec (SS-3) also accepts non-digit ids (hashed). Both are supported; numeric ids are used as-is.
- **C-3 CP3 harness language.** CP3 says the stdlib-only rule applies to `tally.py`, not the harness; we keep the harness stdlib-only anyway (CLI-13).
- **C-4 Time limit.** CP1/CP2 reruns in the final submission are still 180 s per case (CP3: 300 s for "each of the 24 test cases"). We keep 165 s for non-CP3 tasks.
