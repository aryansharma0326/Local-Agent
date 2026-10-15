# Homework 1 — a from-scratch Qwen3-14B agent (Checkpoints 1, 2 and 3)

The agent is a small harness written from scratch in Python (standard library only). Qwen3-14B, behind an
OpenAI-compatible `/chat/completions` endpoint, decides what to do. Deterministic code does every filesystem,
CSV, shell and validation step. No agent framework or SDK is used, and the configured endpoint is the only
network call.

It handles:

- **CP1**: create, delete, read-and-answer, and multi-file summaries.
- **CP2**: multi-turn sessions across processes, session isolation, and CSV questions/edits.
- **CP3**: programming tasks (write or fix a program against a spec) and debugging tasks (follow one failed
  request through large logs and write a JSON report).

## Folder structure

```
run_agent.sh            entry point (Bash 3.2 compatible, LF, executable; works via a symlink or `sh`)
requirements.txt        no dependencies (standard library only)
agent/
  main.py               stdout guard, argv, signal handlers + watchdog, routing, session save
  config.py             environment variables, deadlines, model-call caps, stderr logging
  output.py             the single JSON emitter (emit-once, UTF-8 safe, length clamp)
  llm.py                HTTP client: retries/backoff, fail-fast codes, token budgeting, call counter,
                        native + text tool-call parsing, <think> stripping
  router.py             heuristic task classification: simple | programming | debugging
  harness.py            CP1/CP2 loop (one JSON tool call per turn, guardrails, on-disk verification)
  tools.py              CP1/CP2 tools: list/read/write/delete/copy, calculate, csv_* tools, finish
  guards.py             lenient JSON parsing, path checks, literal-content fidelity, summary limits
  csvtools.py           deterministic CSV engine (Decimal, byte-preserving rewrites) and calculator
  sandbox.py            workspace path sandbox and safe file primitives
  verify.py             workspace snapshots and post-condition checks
  session.py            persistent session store (locking, atomic writes, stale guard, working context)
  deep.py               CP3 loop: native tool calls, pinned context + compaction, harness checks,
                        best-version restore, wrap-up
  deeptools.py          CP3 tools: read/read_lines/grep/log_stats, write/append/edit, run_command,
                        run_python, notes, report_set, finish
  prompts.py            CP3 system prompts and playbooks (generic, no task answers)
  taskspec.py           prompt parsing: deliverables, report keys and types, example commands
  report.py             JSON report writer and validator (verbatim, type and linkage checks)
  profiling.py          CSV anomaly profile, log statistics, word-boundary grep
  workspace.py          change audit: backup at start, restore unexpected changes at the end
  errors.py             AgentError
tests/                  offline + live test suites, fixtures and generators (see "Tests")
scripts/make_submission.sh  builds dist/hw1_submission.zip after pre-package checks
docs/                   the assignment texts (docs/assignment/CP1-3.md) and the engineering spec
AUDIT.md, PLAN.md       requirement traceability matrix and plan
```

## Tested environment

| | |
|---|---|
| OS | macOS 27.0.1 (Darwin 27.0.0, arm64): `Darwin Aryans-MacBook-Pro.local 27.0.0 ... RELEASE_ARM64_T8132 arm64` |
| Python | 3.13.2 (the code targets Python ≥ 3.8; 3.9+ syntax/APIs are rejected by a static test) |
| Bash | GNU bash 3.2.57 (`/bin/bash` on macOS); no Bash-4 features are used |
| Model | `Qwen/Qwen3-14B` at `https://llm.montii.me/v1` (about 25 output tokens/s during development) |

Linux is supported: only POSIX tools and the Python standard library are used. Docker and Python 3.8 were not
available on the development machine, so the Linux and 3.8 checks are static (see "Known limitations").

## Setup

Nothing needs to be installed. You need `python3` (3.8 or newer) and `bash` on the PATH.

## Running

```bash
cd "$TASK_DIR"
OPENAI_BASE_URL="https://llm.montii.me/v1" MODEL="Qwen/Qwen3-14B" QWEN_API_KEY="$KEY" \
  bash "$SUBMISSION_DIR/run_agent.sh" "$PROMPT" ["$SESSION_ID"]
```

Stdout is always exactly one JSON object, `{"status": "success"|"error", "message": "..."}`, followed by a
newline. The exit code is 0 if and only if the status is `success`. Diagnostics, including a
`calls_used=N` line, go to stderr.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `OPENAI_BASE_URL` | no (default `https://llm.montii.me/v1`) | OpenAI-compatible base URL; `/chat/completions` is appended |
| `MODEL` | no (default `Qwen/Qwen3-14B`) | model name |
| `QWEN_API_KEY` | yes for remote endpoints | Bearer token; `OPENAI_API_KEY` is used if it is empty. With a localhost URL (Ollama/vLLM) an empty key is allowed |
| `AGENT_TIME_BUDGET` | no | overrides the internal deadline in seconds (default 165 s, or 285 s for CP3 tasks) |
| `AGENT_MAX_CALLS` | no | lowers the model-call cap (default 110; the grader allows 120) |
| `AGENT_CALL_TIMEOUT` | no | per-request timeout cap in seconds (default 120) |
| `AGENT_STATE_DIR` | no | session store directory (overrides the default locations below) |
| `AGENT_SESSION_SCOPE` | no | `id+workspace` (default) or `id` (see "Sessions") |
| `AGENT_LOG_FILE` | no | also append the stderr log to this file (keep it outside the workspace) |
| `AGENT_TEMPERATURE` | no | sampling temperature (default 0.2; the CP1/CP2 loop uses 0.0) |
| `AGENT_NO_THINK_SUFFIX` | no | `1` also appends `/no_think` for backends that ignore `chat_template_kwargs` (off for grading) |
| `AGENT_DEBUG_DUMP` | no | development only: write the latest CP3 request to this file |

Every request sends `"chat_template_kwargs": {"enable_thinking": false}`. Thinking mode is never used.

## How it works

1. **Routing** (`router.py`). Strong signals send a prompt to the CP3 loop:
   - a code deliverable together with "implement", "command-line tool" or "fix … tests";
   - a JSON report together with logs or "root cause";
   - a `TASK.md` file that matches the prompt.

   Everything else goes to the CP1/CP2 harness. So a CP3 misroute can never take a file, CSV or session task
   away from the proven path.
2. **CP1/CP2 harness** (`harness.py`).
   - Each turn, the model returns one JSON tool call. The harness validates it and applies guardrails:
     - exact-name delete allowlist;
     - literal-content restoration;
     - every summary source must be read before writing;
     - word/sentence/bullet limits;
     - numbers in a summary must come from the sources;
     - arithmetic must go through `calculate` or the CSV tools.
   - Answers are "evidence first": the model quotes the exact line, then reasons, then answers.
   - Afterwards the harness checks the disk directly and accepts no unexpected change.
   - Compound requests ("create a.txt … and delete b.txt") may change every file the request names.
   - A follow-up with nothing to refer to in its own session ("What is the average?" in a new session) is
     answered with a clarification error and no guessing.
3. **CP3 loop** (`deep.py`). Native tool calling over a pinned context:
   - system playbook;
   - the task plus a harness pre-fetch: data profile, log statistics, and anchor-ID grep results;
   - rolling tool exchanges, which are compacted when the 40,960-token budget gets tight;
   - a PINNED STATE block: notes ledger, the current deliverable or report, and time and calls left.

   *Programming*:
   - After every write or edit, the harness compiles the program and runs the task's own example commands,
     with placeholders filled from the data.
   - It also runs option-order permutations, edge probes (unknown value, large N, missing file) and a
     static lint. It reports a prioritized PROBLEMS list.
   - The best version is kept and restored at wrap-up.
   - Input files are protected, scratch files live outside the workspace, and the change audit restores
     anything else.

   *Debugging*:
   - The report file is written with type-correct placeholders immediately.
   - `report_set` coerces types and checks three things for every value: it occurs verbatim in the logs, it
     is linked to the failed request through a shared log record (decoy guard), and it has the right JSON
     type.
4. **Deadlines.**
   - The internal budget is 165 s for CP1/CP2 and 285 s for CP3. Wrap-up starts 45 s before the end.
   - A watchdog and the TERM/INT/HUP handlers still print valid JSON if anything hangs.
   - At most 110 model calls are made, and wrap-up starts when 10 are left.

## Sessions

- With a second argument, history is stored in `session_<id>.json`. Digits are used as-is; any other id is
  hashed with sha1, so `007` and `7` are different sessions.
- The file goes in the first writable directory of:
  1. `$AGENT_STATE_DIR`
  2. `${XDG_STATE_HOME:-$HOME/.local/state}/hw1_agent/sessions`
  3. `<submission>/.agent_state/sessions`
  4. `${TMPDIR:-/tmp}/hw1_agent_<uid>/sessions`

  The store is never inside the workspace. If no location is writable, the run is stateless.
- Writes use `fcntl.flock`, read-modify-write, temp file + `os.replace`, and a schema version. A corrupt
  file is set aside as `*.corrupt-<ts>`.
- Each turn stores the prompt, actions, message and status, plus a structured working context: last files,
  last CSV operation, created outputs and standing instructions.
- The last 8 turns are shown verbatim and older ones are compacted. 40 turns are kept.
- Scope (`AGENT_SESSION_SCOPE`): the default `id+workspace` treats a reused id from a different workspace as
  a new conversation, and the old file is renamed `*.stale-<ts>`. `id` shares history across workspaces.
- Without a session id nothing is read or written (CP1 calls never share context).

## Design decisions

| ID | Decision |
|---|---|
| D-1 | No trailing newline is added to created file content unless the user's content has one; multi-line content is joined with `\n`. |
| D-2 | Creating a file that already exists overwrites it. |
| D-3 | Deleting a missing file returns an error (exit 1) and changes nothing. |
| D-4 | Path matching is case-sensitive; a bare file name that exists only once in a subfolder is resolved to it, an ambiguous one is an error. |
| D-5 | If a summary source is missing, the run fails and no partial summary is written. |
| D-6 | Sessions are keyed by id **and** workspace; history from a different workspace is stale (switch: `AGENT_SESSION_SCOPE=id`). |
| D-7 | Session ids are exact strings (`007` ≠ `7`). |
| D-8 | A follow-up with no referent in its own session returns an error asking which file/column is meant; the workspace is never scanned for a guess. |
| D-9 | CSV text conditions are case-insensitive and trimmed; numbers compare numerically. |
| D-10 | A CSV change with no output path modifies the file in place; with an output path the source is never modified. |
| D-11 | Numbers in messages have no thousands separators; integers print without decimals; longer decimals also show a 2-decimal rounding. |
| D-12 | Compound requests: every file the request names may be changed (e.g. "Delete a.txt and b.txt" deletes both); files it does not name are protected. |
| D-13 | Thinking mode is disabled for every request (CP3 rule), including CP1/CP2 reruns; read answers use structured evidence → reasoning → answer instead. |
| D-14 | CP3 programming: the harness runs only the task's own example commands plus generic CLI property checks (option order, unknown values, missing file); it never contains task-specific answers. |
| D-15 | CP3 debugging: report values are taken verbatim from the logs; integers are coerced from numeric strings; keys not in the task are rejected. |
| D-16 | The CP3 per-request timeout may reach 240 s for a whole-program write (the spec's 120 s cap would cut a ~3k-token write at 25 tokens/s); every wait still respects the run deadline. |

## Tests

| Command | Needs a key | What it runs | Duration |
|---|---|---|---|
| `bash tests/run_offline.sh` | no | static checks, unit tests, mock-LLM E2E (CP1/CP2 + CP3 twins + contract + fault injection), all in parallel | about 75 s |
| `bash tests/grader_sim.sh` | no | builds the zip, checks its contents, extracts it read-only into a path with spaces and runs the offline suite with `/bin/bash` and a sanitized env | about 2 min |
| `bash tests/grader_sim.sh --live` | yes | the same plus the CP2 example and one create case with the exact grader command form | +1 min |
| `python3 tests/test_live.py [cp1\|cp2\|fragment]` | yes | 91 CP1/CP2 live cases (66 single requests, 25 conversations) | about 8 min |
| `python3 tests/live_cp3.py debug --seeds 1,2 --scenarios basic,retry,decoy_ids,decoy_class` | yes | CP3 debugging generator runs, per-key accuracy | about 1 min per case |
| `python3 tests/live_cp3.py prog --variants A,B,C --seeds 1` | yes | CP3 programming spec variants, graded per requirement | 1–4 min per case |
| `bash tests/run_live.sh --reps 5 --only cp1,cp2,cp3` | yes | the release-gate catalog with repetitions (`--backend openrouter\|local`, `--delay S`) | long |

The live tests read the key from `$QWEN_API_KEY` or `~/.qwen_key`. The key is never stored in the repository,
and a static test scans for it.

Fixtures and expected results:

- `tests/fixtures/gen_transactions.py`: seeded 40k-row CSV with anomalies.
- `tests/fixtures/gen_logs.py`: four services with decoys, plus an answer key.
- `tests/fixtures/prog_variants.py`: the official tally.py prompt with three invented sets of requirements
  7–12, and their graders.
- `tests/fixtures/reference/tally_ref.py`: test-only reference implementation.
- The CP1/CP2 cases are defined inline in the test files.

## Dev backends

- **OpenRouter**: `bash tests/run_live.sh --backend openrouter` (uses `$OPENROUTER_API_KEY` and model
  `qwen/qwen3-14b`).
- **Local Ollama/vLLM**: `OPENAI_BASE_URL=http://localhost:11434/v1 MODEL=qwen3:14b` (an empty key is fine on
  localhost). Add `AGENT_NO_THINK_SUFFIX=1` if the backend ignores `chat_template_kwargs`.

## Known limitations

- **Model limits on CP3 programming.** Qwen3-14B without thinking sometimes misreads option values in its first
  draft. The harness checks, lint and diagnostics recover most cases, but not all. See the latest live numbers in
  `AUDIT.md`.
- **Hidden debugging keys.** These are inferred from the task wording; a key whose type is ambiguous is typed
  from the model's value.
- **Untested platforms.** Python 3.8 and Linux containers were not run (not installed on the development
  machine). Compatibility is enforced statically: `ast` parsing with `feature_version=(3, 8)`, a scan for
  3.9+ APIs, and POSIX-only shell.
- **Throughput.** At about 25 tokens/s, a CP3 program write takes 60–90 s. The 300 s limit leaves room for
  roughly two full rewrites plus edits.
