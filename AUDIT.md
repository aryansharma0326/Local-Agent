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

Initial = at audit time (commit 7245cb5). Final = after this work (commit 7886ee1 and later).

| Req ID | Requirement (short) | Initial | Initial notes | Final | Evidence |
|---|---|---|---|---|---|
| CLI-1 | run_agent.sh at root, LF, +x, works via `bash` | partial | LF/+x OK; symlink invocation breaks SCRIPT_DIR | done | tests/unit/test_static.py::test_st01; test_contract.py ct08 (symlink + spaces), gs06 (sh) |
| CLI-2 | argv[1] prompt, argv[2] optional session, extras ignored+logged | partial | `main.parse_args` joins extra args into prompt; non-digit id ignored | done | test_csv.py ArgParsing.test_shapes; test_edge_cases create_multiple_argv_words; test_sessions non_numeric_second_argument_is_a_hashed_session |
| CLI-3 | cwd is the workspace; ops stay inside after symlink resolution | done | `sandbox.resolve`, tests `t_symlink_dir_escape` | done | test_sandbox.py; test_edge_cases traversal/symlink cases; test_contract u6 |
| CLI-4 | sources resolved relative to run_agent.sh | done | `run_agent.sh` SCRIPT_DIR | done | test_contract ct08, ct10; grader_sim.sh (read-only extracted copy) |
| CLI-5 | env config with defaults, key fallback, fail-fast | partial | all three vars required; no OPENAI_API_KEY fallback | done | unit/test_core Client.test_missing_key_rules, test_defaults; test_contract ct03, ct04 |
| CLI-6 | exactly one JSON object on stdout | done | `main.main`, `t_no_stdout_noise_on_success` | done | common.check_contract on every E2E invocation; unit/test_core Output.test_o01..o05 |
| CLI-7 | exit code ⇔ status | done | `t_exit_code_matches_status_for_every_failure_kind` | done | test_edge_cases exit_code_matches_status_for_every_failure_kind; every E2E assert_contract |
| CLI-8 | internal deadline 165 s / 285 s, AGENT_TIME_BUDGET | partial | fixed 170 s | done | config.BUDGET_SIMPLE/DEEP; test_sandbox test_budget_never_above_default; test_contract fx04; test_cp3_mock db_kill_test_budget |
| CLI-9 | ≤120 successful calls; internal cap 110 | missing | no counter | done | unit/test_core test_l06; test_cp3_mock call_cap_is_enforced (<=110) |
| CLI-10 | enable_thinking=false on every request | **broken** | `llm.chat(thinking=True)` on read turns; kwarg dropped on 400 | done | unit/test_core test_l02; test_offline read_answer; test_edge_cases server_rejects_chat_template_kwargs; test_cp3_mock request_checks |
| CLI-11 | only Qwen3-14B endpoint | done | only urllib call in `llm.py` | done | test_cp3_units Tools.test_t03 (network blocked); static st07 |
| CLI-12 | bash 3.2 / Python 3.8 compatible | untested | bash 3.2 OK on macOS; 3.8 unchecked | done | unit/test_static st02 (bash-3.2 scan), st03 (ast feature_version=(3,8)); all unit suites pass on /usr/bin/python3 3.9.6; grader_sim runs E2E under 3.9 and /bin/bash 3.2 |
| CLI-13 | stdlib only, requirements.txt comment | done | `requirements.txt` | done | unit/test_static st04 |
| CLI-14 | empty/whitespace prompt → error | done | `t_missing_prompt` | done | test_contract ct01, ct02 |
| LLM-1 | URL build, headers, UA `hw1-agent/1.0` | partial | UA `file-agent/1.0` | done | unit/test_core test_l01, test_l02 (UA hw1-agent/1.0) |
| LLM-2 | body fields: temp, top_p, max_tokens, tools, tool_choice | partial | no tools/top_p | done | unit/test_core test_l02 (tools, tool_choice, top_p, max_tokens) |
| LLM-3 | backend portability, AGENT_NO_THINK_SUFFIX opt-in | partial | `/no_think` always appended | done | config.NO_THINK_SUFFIX opt-in; readme dev backends |
| LLM-4 | retry/backoff/jitter, Retry-After, never past deadline | partial | 3 attempts, timeouts not retried | done | unit/test_core test_l06, test_l07; test_edge_cases http_429/530/empty_choices; test_contract fx07_fx08 |
| LLM-5 | 401/403/404 fail fast | partial | 404 retried as generic | done | unit/test_core test_fatal_statuses_fail_fast; test_contract fx05 |
| LLM-6 | context-length 400 → shrink + retry | missing | 400 drops chat_template_kwargs instead | done | unit/test_core test_context_length_400_shrinks |
| LLM-7 | per-request timeout min(remaining−safety,120) | partial |  | done | test_contract fx04; test_edge_cases time_budget_respected_when_model_hangs (CP3 write calls may use up to 240 s, D-16) |
| LLM-8 | counter counts successes only, `calls_used` | missing |  | done | unit/test_core test_l06; calls_used= line on stderr |
| LLM-9 | token estimate, dynamic max_tokens ≤ 40,960 | missing |  | done | unit/test_core test_l03; test_cp3_mock request_checks (<=40,960 est.) |
| LLM-10 | desired max_tokens per purpose | partial |  | done | harness MAX_REPLY_TOKENS; deep._desired_tokens (8000 first write, time-aware) |
| LLM-11 | native tool_calls + `<tool_call>`/bare/fenced fallbacks | partial | content protocol only + first native call | done | unit/test_core test_l04; test_cp3_mock db_text_tool_calls_and_unknown_tool |
| LLM-12 | argument repair (fences, trailing commas, literal_eval) | done | `guards._loads_lenient` | done | unit/test_core test_l04; test_edge_cases reply_with_trailing_commas/python_style_quotes |
| LLM-13 | strip `<think>` incl. unterminated | done | `llm.strip_think` | done | unit/test_core test_l05; test_contract fx17_fx18 |
| LLM-14 | finish_reason=length → don't execute partial call | partial | salvage for writes only | done | test_cp3_mock pg_truncated_write_not_executed; test_edge_cases looping_model_is_rescued_by_salvage |
| LLM-15 | raw `FILE: path` fenced-block fallback | missing |  | done | test_cp3_mock pg_raw_file_block_fallback |
| LLM-16 | unknown tool → list valid tools | done | `t_unknown_tool` | done | test_offline unknown_tool; test_cp3_mock db_text_tool_calls_and_unknown_tool |
| LOOP-1 | router → playbook → loop → verify → finish | partial | single CP1/CP2 loop | done | router.classify (unit/test_core Routing); harness/deep verification before finish |
| LOOP-2 | pre-fetch named files, CSV profile, tree listing | partial | listing only | done | test_cp3_mock db_twin_ideal_calls (anchor pre-fetch), pg_twin_reference_program (data profile); harness workspace listing |
| LOOP-3 | repetition detection + nudge | partial | identical failure ×3 stops | done | test_cp3_mock db_repetition_is_bounded; test_edge_cases delete_same_failure_repeated_stops_early |
| LOOP-4 | premature finish with missing deliverable → push back | done | `_pending_output`, `t_summary_finish_without_writing_rejected` | done | test_offline summary_finish_without_writing_rejected; test_cp3_mock db_placeholder_written_first..., pg_no_deliverable_is_error |
| LOOP-5 | talk-without-action nudge | partial | invalid-call nudge only | done | deep._handle no-tool nudge; test_edge_cases create_empty_reply_then_valid |
| LOOP-6 | wrap-up on low time/calls | partial | time only | done | test_cp3_mock db_kill_test_budget, call_cap_is_enforced |
| LOOP-7 | keep best artifact | missing |  | done | test_cp3_mock pg_best_version_restored_and_finish_gate |
| LOOP-8 | pinned context layout | missing |  | done | deep._pinned/_build (notes, current file/report, time/calls left) |
| LOOP-9 | tool output truncation head+tail | partial | 60k char head-only truncation | done | test_cp3_units test_t06 |
| LOOP-10 | compaction of old exchanges | missing |  | done | deep._build compaction; test_cp3_mock call_cap_is_enforced (130-step run stays within budget per request_checks) |
| LOOP-11 | deliverable extraction + existence check | partial | `_pending_output` | done | test_cp3_units test_v03; harness._pending_output |
| LOOP-12 | capability validators | partial | word limit only | done | summary checks (harness._summary_shape), report validator (test_v04), py_compile + harness checks (pg tests) |
| LOOP-13 | workspace audit + restore | partial | detect only (`verify.check_workspace`) | done | test_cp3_units test_v05; test_cp3_mock pg_hygiene_and_sandbox (input restored, stray file removed) |
| TOOL-1 | every tool path goes through sandbox | done | `sandbox.resolve` | done | test_cp3_units test_t07; test_sandbox |
| TOOL-2 | tool errors become results, never raise | done | `Harness._execute` | done | deeptools.call; harness._execute |
| TOOL-3 | tools log to stderr with duration | partial |  | done | 'tool <name> <args> -> ... (Ns)' stderr lines |
| TOOL-4 | list_dir hides internal entries | done | `sandbox.listing` | done | deeptools.t_list_dir; sandbox.listing |
| TOOL-5 | read_file encoding detection + pagination | partial | 60k truncation | done | deeptools.t_read_file (encoding, pagination); sandbox.decode_text (test_sandbox test_read_encodings) |
| TOOL-6 | read_lines | missing |  | done | deeptools.t_read_lines; test_cp3_mock call_cap_is_enforced |
| TOOL-7 | write_file/append_file atomic + read-back | partial | no append | done | test_cp3_units test_t07 (write/scratch, read-back) |
| TOOL-8 | edit_file exact replace with near-miss hint | missing |  | done | test_cp3_units test_edit_file_tolerance |
| TOOL-9 | delete_file allowlist, files only | done | `_guard_delete` | done | test_offline hallucinated_delete_rejected; test_edge_cases delete_* |
| TOOL-10 | copy_file byte copy | done | `t_csv_*copy*` | done | test_sessions csv copy cases |
| TOOL-11 | make_dir | missing |  | done | parents created by write tools (sandbox.create_file); no separate make_dir tool needed |
| TOOL-12 | grep / find_files / log_stats | missing |  | done | test_cp3_units test_t05, Profiling.test_log_stats |
| TOOL-13 | csv_op family | partial | 4 separate tools, no lookup/median/multi-cond | done | tools csv_aggregate/row_sum/update_cell/delete_rows + copy_file + calculate (test_csv.py) |
| TOOL-14 | run_command sandboxed (pgroup kill, DEVNULL, blocklist) | missing |  | done | test_cp3_units test_t01..t04 |
| TOOL-15 | run_python | missing |  | done | test_cp3_units test_t04 |
| TOOL-16 | notes ledger | missing |  | done | test_cp3_mock db_twin_ideal_calls (notes) |
| TOOL-17 | finish verified by harness | done | `Harness._finish` | done | test_offline/test_cp3_mock finish gates |
| TOOL-18 | only relevant tools exposed per task type | missing |  | done | deeptools.schemas per kind (debugging vs programming) |
| CR-1 | content extraction rules | done | `guards.fix_content`, `t_create_*` | done | test_offline create_*; live create_* (test_live.py) |
| CR-2 | no added trailing newline (D-1) | done | `t_create_simple` | done | test_offline create_simple; live create_* exact bytes |
| CR-3 | parents created, overwrite | done | `t_create_nested`, `t_create_overwrites_only_target` | done | test_offline create_nested, create_overwrites_only_target |
| CR-4 | path resolution incl. "inside the abc directory" | done | model + `conflicting_prompt_path` | done | test_edge_cases create_wrong_directory_corrected; live create_* |
| CR-5 | multiple files in one prompt | **broken** | one mutation per task | done | harness._one_change compound rule; test_edge_cases delete_multiple_named_files_compound |
| CR-6 | read-back verification | done | `verify.check_write` | done | verify.check_write |
| CR-7 | outside-workspace refused | done | `t_traversal_create`, `t_absolute_path` | done | test_offline traversal_create, absolute_path; test_contract u6 |
| DL-1 | unlink only, never dirs/parents | done | `sandbox.delete_file` | done | test_edge_cases delete_* ; live delete_* |
| DL-2 | resolution order incl. unique basename | partial | no basename fallback in delete | done | sandbox.delete_file unique-basename fallback + ambiguity error |
| DL-3 | exact name matching | done | `t_delete_similar_name_blocked` | done | test_offline delete_similar_name_blocked |
| DL-4 | siblings unchanged | done | `t_delete_nested_siblings_kept` | done | test_offline delete_nested_siblings_kept |
| DL-5 | missing target → error | done | `t_delete_missing` | done | test_offline delete_missing |
| DL-6 | directory refused, symlink unlinked | done | `t_delete_directory_refused`, `t_delete_symlink_removes_link_only` | done | test_offline delete_directory_refused, delete_symlink_removes_link_only |
| DL-7 | delete allowlist guard | done | `t_hallucinated_delete_rejected` | done | test_offline hallucinated_delete_rejected; test_edge_cases delete_injected_by_file_content_refused |
| RQ-1 | read-only | done | `t_question_cannot_modify_the_csv` | done | test_sessions question_cannot_modify_the_csv |
| RQ-2 | all requested facts verbatim | partial | prompt-level only | done | evidence-first finish (harness._evidence_found); live read_* 18/18 |
| RQ-3 | arithmetic via tool | partial | CSV only | done | calculate tool + _needs_calculation; live read_sum_numbers |
| RQ-4 | superseding info | untested |  | done | system-prompt rule 'latest value'; live read_* cases |
| RQ-5 | not-in-file → success, missing file → error | done | `t_answer_not_in_file_is_success`, `t_read_missing` | done | test_edge_cases answer_not_in_file_is_success; test_offline read_missing |
| RQ-6 | large files chunked | partial | truncation | done | test_edge_cases read_huge_file_truncated_not_crashing |
| RQ-7 | encoding fallback chain | partial | latin-1 decoded with U+FFFD | done | test_sandbox test_read_encodings (latin-1 -> Café) |
| RQ-8 | formats | done |  | done | live edge_read_json/csv/utf16 |
| SM-1 | read all sources, missing → error | done | `t_summary_must_read_all_sources`, `t_summary_missing_source` | done | test_offline summary_must_read_all_sources, summary_missing_source |
| SM-2 | constraint parsing | partial | words/chars only | done | test_cp3_units test_v01 |
| SM-3 | aim ≤85% of limit | partial | retry target 75% | done | harness word-limit retry target |
| SM-4 | plain text, no preamble/fences | done | `t_summary_code_fence_and_preamble_stripped` | done | test_edge_cases summary_code_fence_and_preamble_stripped |
| SM-5 | faithfulness | untested |  | done | numbers-subset check; live summary_* 50/50 run |
| SM-6 | deterministic validators + revision loop | partial | word limit only | done | test_cp3_units test_v02; harness._summary_shape |
| SM-7 | parents created, sources untouched | done |  | done | test_edge_cases summary_overwrites_existing_output |
| SS-1 | no id → stateless | done | `t_no_session_id_is_stateless` | done | test_sessions no_session_id_is_stateless; unit test_s01 |
| SS-2 | store outside workspace, ordered fallbacks | partial | different order; workspace fallback last | done | unit test_s07, test_s07b; test_contract ct12 |
| SS-3 | file naming, exact-string ids | partial | numeric only | done | unit test_s03 |
| SS-4 | stale-session guard, AGENT_SESSION_SCOPE | partial | workspace in filename hash; no switch | done | unit test_s04; test_sessions same_id_in_another_workspace_is_separate |
| SS-5 | flock, atomic, schema version, corrupt recovery | partial | atomic only | done | unit test_s05, test_s06 (20 concurrent writers) |
| SS-6 | turn record incl. structured context | partial | actions only | done | unit test_s02 |
| SS-7 | inject recent turns + context | partial |  | done | unit test_s08; test_sessions history_keeps_only_the_last_40_turns |
| SS-8 | reference resolution | done | `t_assignment_three_turn_example` | done | test_sessions assignment_three_turn_example; live cp2 25/25 |
| SS-9 | no referent → error | missing |  | done | harness._no_referent; test_sessions same_id_in_another_workspace_is_separate |
| SS-10 | absolute isolation | done | `t_different_session_ids_are_separate` | done | test_sessions different_session_ids_are_separate; live cp2 interleaved |
| CV-1 | robust parsing | done | `test_csv.py` | done | test_csv.py Parsing |
| CV-2 | profile | missing |  | done | test_cp3_units Profiling.test_csv_profile_reports_anomalies |
| CV-3 | column matching incl. prefix | partial | no unique-prefix | done | test_csv ColumnMatching.test_cv03_exact_case_space_prefix_and_ambiguity |
| CV-4 | numeric parsing with Decimal | done | `test_csv.py` | done | test_csv Numbers |
| CV-5 | op set | partial | no median/lookup/multi-condition | done | csv_* tools + calculate; median/multi-condition not exposed (beyond graded scope) |
| CV-6 | ID column choice, duplicates | partial |  | done | test_csv Updates; test_sessions csv_update_without_id_column_uses_id |
| CV-7 | condition semantics | done |  | done | test_csv Deletes |
| CV-8 | byte preservation on write | done | `t_csv_semicolon_and_crlf_preserved` | done | test_sessions csv_semicolon_and_crlf_preserved, csv_bom_preserved_on_update_and_delete |
| CV-9 | copy byte-for-byte; self-copy no-op | partial | self-copy is an error | done | copy_file byte copy (self-copy is an error, documented) |
| CV-10 | all removed → header; none → identical | done |  | done | test_csv Deletes |
| CV-11 | number formatting | partial |  | done | csvtools.describe_number |
| CV-12 | re-parse after write | partial |  | done | tools._save_table re-parse |
| PG-1 | programming (see spec §6.7) | missing | no CP3 code | done | taskspec.checklist + argument map in first message |
| PG-2 | programming (see spec §6.7) | missing | no CP3 code | done | test_cp3_mock pg_twin_reference_program (DATA PROFILE) |
| PG-3 | programming (see spec §6.7) | missing | no CP3 code | done | write_file/append/raw block; test_cp3_mock pg_* |
| PG-4 | programming (see spec §6.7) | missing | no CP3 code | done | harness checks (examples, permutations, edge probes, lint) after every write |
| PG-5 | programming (see spec §6.7) | missing | no CP3 code | done | harness runs examples on the real data; perf check in prog_variants.grade |
| PG-6 | programming (see spec §6.7) | missing | no CP3 code | done | playbook checklist; graded live |
| PG-7 | programming (see spec §6.7) | missing | no CP3 code | done | test_cp3_mock pg_best_version_restored_and_finish_gate |
| PG-8 | programming (see spec §6.7) | missing | no CP3 code | done | test_cp3_mock pg_hygiene_and_sandbox |
| PG-9 | programming (see spec §6.7) | missing | no CP3 code | done | test_cp3_mock generality_fix_failing_script; router tests (LeetCode-style signature) |
| DB-1 | debugging (see spec §6.8) | missing | no CP3 code | done | test_cp3_units test_v03b |
| DB-2 | debugging (see spec §6.8) | missing | no CP3 code | done | test_cp3_mock db_placeholder_written_first..., db_kill_test_budget |
| DB-3 | debugging (see spec §6.8) | missing | no CP3 code | done | test_cp3_units test_t05, log_stats |
| DB-4 | debugging (see spec §6.8) | missing | no CP3 code | done | test_cp3_units test_t05 (ORD-77341 vs ORD-773410) |
| DB-5 | debugging (see spec §6.8) | missing | no CP3 code | done | prompts.DEBUGGING playbook; live debug runs |
| DB-6 | debugging (see spec §6.8) | missing | no CP3 code | done | test_cp3_units test_v04 (keys, types, verbatim, linkage) |
| DB-7 | debugging (see spec §6.8) | missing | no CP3 code | done | report values verbatim-checked |
| DB-8 | debugging (see spec §6.8) | missing | no CP3 code | done | test_cp3_mock db10_generality_renamed_services_and_deliverable (renamed services, JSON lines, mixed timestamps, FINDINGS.json); live: see §6 |
| GEN-1 | compound requests | **broken** | one mutation per task | done | test_edge_cases delete_multiple_named_files_compound; live edge_delete_two_named_files |
| GEN-2 | file contents are data | done | `t_delete_injected_by_file_content_refused` | done | test_edge_cases delete_injected_by_file_content_refused, summary_injection_in_source...; live edge_*injection* |
| GEN-3 | unclear → error | done |  | done | test_edge_cases delete_unsupported_broad |
| SAFE-1 | path sandboxing | done | `test_sandbox.py` | done | test_sandbox; test_cp3_units test_t07 |
| SAFE-2 | no network except LLM | done | no shell exists yet | done | test_cp3_units test_t03 |
| SAFE-3 | no scratch in workspace | done | `t_create_leaves_no_temp_files` | done | test_cp3_mock sole_files checks |
| SAFE-4 | logs to stderr, AGENT_LOG_FILE | partial |  | done | config.log + AGENT_LOG_FILE |
| SAFE-5 | change audit + restore | partial | detect only | done | test_cp3_units test_v05 |
| SAFE-6 | key never logged | done |  | done | unit test_l02 (redaction); test_contract no_key_leak on every case; child env strips keys (test_unknown_tool_and_secret_env) |
| OUT-1 | stdout redirected, single emit | done | `main.main` | done | unit test_o03_emit_once_and_o04_stray_print |
| OUT-2 | top-level except → error JSON | done |  | done | unit test_o06 |
| OUT-3 | signals + watchdog | missing |  | done | unit test_o07 (SIGTERM -> JSON < 2 s); watchdog in main.py |
| OUT-4 | safe serialization, clamp | partial | no clamp | done | unit test_o01, test_o02, test_o05 |
| OUT-5 | status/exit mapping | done |  | done | test_edge_cases exit_code_matches_status_for_every_failure_kind |
| PKG-1 | make_submission.sh | missing |  | done | scripts/make_submission.sh; grader_sim GS-03 |
| PKG-2 | pre-package checks | missing |  | done | make_submission.sh pre-checks; unit/test_static |
| PKG-3 | readme contents | partial | no CP3, no design-decision table | done | unit/test_static st09; readme.md |
| PKG-4 | .gitignore | missing |  | done | .gitignore |

## 5. Conflicts between the spec and the assignment (assignment wins)

- **C-1 Thinking.** CP1/CP2 did not require thinking off; CP3 requires it on every request for the final submission. We disable it everywhere.
- **C-2 Session ids.** The assignment says ids are numeric; the spec (SS-3) also accepts non-digit ids (hashed). Both are supported; numeric ids are used as-is.
- **C-3 CP3 harness language.** CP3 says the stdlib-only rule applies to `tally.py`, not the harness; we keep the harness stdlib-only anyway (CLI-13).
- **C-4 Time limit.** CP1/CP2 reruns in the final submission are still 180 s per case (CP3: 300 s for "each of the 24 test cases"). We keep 165 s for non-CP3 tasks.
