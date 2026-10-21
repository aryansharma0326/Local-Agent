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
  story_all.sh          one connected story through all three checkpoints (see "Trying it by hand")
  smoke_all.py          one-command check of all three checkpoints, one step per objective
  heldout.py            33 held-out black-box conversations
  adversarial/          grader-style adversarial suite: cases_cp1/2/3.py, runner.py, make_report.py,
                        cp3/ generators and reference tally, CASES.md (catalog), REPORT.md (results)
scripts/make_submission.sh  builds dist/hw1_submission.zip after pre-package checks
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

## Trying it by hand (for graders)

### One story through all three checkpoints

```bash
bash tests/story_all.sh
```

This runs one connected scenario in a single workspace and a single session (77). It takes 5-7 minutes and
prints each prompt, the agent's JSON reply and PASS/FAIL.

1. A statement-only first turn: "My team lead is Priya Raman." (CP2 context)
2. A create that uses that fact: "...a note addressed to my team lead by name." Then a read-and-answer, a
   multi-file summary and a delete. (CP1)
3. A CSV total, then the follow-up "What is the average?", then "Save a copy of the table", then a row
   removal. (CP2 CSV, where each follow-up relies on an earlier turn)
4. "Who is my team lead?" in session 78 (it must not know) and again in session 77 (it must). (CP2 isolation)
5. The official `tally.py` task (requirements 1-6) and an incident-log debugging task (12 REPORT.json
   keys). Each runs in its own sub-folder, the way the grader runs them. (CP3)

### One command, one step per objective

`tests/smoke_all.py` runs one prompt for each main objective of every checkpoint, through `run_agent.sh`
exactly as the grader does. Each step runs in a fresh temp workspace, and the script prints PASS/FAIL per
step. It reads the key from `$QWEN_API_KEY` or `~/.qwen_key`.

```bash
python3 tests/smoke_all.py            # CP1 + CP2 + CP3, about 5 minutes (CP3 is most of it)
python3 tests/smoke_all.py --no-cp3   # CP1 + CP2 only, about 1 minute
```

| Step | Checkpoint objective |
|---|---|
| create, delete, read-and-answer, multi-file summary | CP1: the four task types |
| statement turn, recall in the same session, the same question in another session | CP2: context within a session, and session isolation |
| CSV total, follow-up "What is the average?", "Save a copy of the table", remove rows | CP2: CSV operations, including follow-ups that use earlier turns |
| the official `tally.py` prompt on a generated data file, graded on requirements 1-6 | CP3: programming |
| an incident with four service logs, graded on all 12 REPORT.json keys (at least 11 must be right) | CP3: debugging |

### Prompt by prompt in a terminal

Define a shortcut once per terminal. Use the submission folder's path, and keep the key outside the
submission:

```bash
agent() { OPENAI_BASE_URL="https://llm.montii.me/v1" MODEL="Qwen/Qwen3-14B" QWEN_API_KEY="$(cat ~/.qwen_key)" \
  AGENT_STATE_DIR="$HOME/agent_playground_state" bash "$SUBMISSION_DIR/run_agent.sh" "$@"; echo "exit=$?"; }
mkdir -p ~/agent_playground/all && cd ~/agent_playground/all   # the agent's workspace is the current folder
```

The trailing number is the session ID. Leave it out for independent CP1-style calls. Calls that share a
number form one conversation, and a different number is a separate conversation.

**CP1** (no number):

```bash
printf 'old\n' > old.txt; printf 'Blue notebooks: 12\nRed notebooks: 7\n' > inventory.txt; mkdir -p logs
printf '09:02 Checkout latency alert fired.\n' > logs/start.txt
printf '09:44 Root cause: misconfigured load balancer health check; fixed.\n' > logs/resolution.txt
agent "Create notes/todo.txt containing: buy milk"
agent "Delete old.txt."
agent "Read inventory.txt. How many blue notebooks are currently in stock?"
agent "Use logs/start.txt and logs/resolution.txt to create reports/summary.txt with a summary under 50 words."
```

Expected: `notes/todo.txt` holds `buy milk`; `old.txt` is gone; the answer is 12; `reports/summary.txt` is
under 50 words and names the load balancer.

**CP2** (sessions 7 and 8):

```bash
printf 'month,revenue\nJanuary,120\nFebruary,180\n' > sales.csv
printf 'id,status\n1,shipped\n2,cancelled\n3,pending\n' > orders.csv
agent "My project code name is Cedar." 7
agent "What is my project code name?" 7
agent "What is my project code name?" 8
agent "Read sales.csv and tell me the total of the revenue column." 7
agent "What is the average?" 7
agent "Save a copy of the table as reports/sales_copy.csv." 7
agent "In orders.csv, remove every row whose status is cancelled, and save the result as active.csv." 7
```

Expected:
- session 7 answers Cedar, and session 8 does not know it;
- the total is 300 and the average is 150;
- `reports/sales_copy.csv` equals `sales.csv`;
- `active.csv` keeps rows 1 and 3.

**CP3 programming**, about 2-4 minutes. `$SUBMISSION_DIR/tests/adversarial/cp3` holds the test generators;
the agent never sees them.

```bash
mkdir -p ~/agent_playground/prog && cd ~/agent_playground/prog
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import prog_specs; open('transactions.csv','wb').write(prog_specs.generate('O', n=4000)); open('TASK.md','w').write(prog_specs.prompt('O'))" "$SUBMISSION_DIR/tests/adversarial/cp3"
agent "$(cat TASK.md)" 3001
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import prog_specs; print(prog_specs.score(prog_specs.grade('O','.')))" "$SUBMISSION_DIR/tests/adversarial/cp3"
```

Expected: requirements 1-6 all `True`.

**CP3 debugging**, about 1-3 minutes. The answer key is written one folder up, outside the agent's
workspace.

```bash
mkdir -p ~/agent_playground/debug && cd ~/agent_playground/debug
python3 -c "import sys, json; sys.path.insert(0, sys.argv[1]); import gen_incident; k,_=gen_incident.write(41,'.'); json.dump(k, open('../answer_key.json','w'), indent=1)" "$SUBMISSION_DIR/tests/adversarial/cp3"
agent "$(cat TASK.md)" 3002
python3 -c "import json; k=json.load(open('../answer_key.json')); r=json.load(open('REPORT.json')); [print('ok ' if r.get(x)==v else 'BAD', x, r.get(x), '| want', v) for x,v in k.items()]"
```

Expected: every line starts with `ok`.

To see what the agent did, add `2> agent.log` to any call and read the log.

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
| `AGENT_CONTEXT_TOKENS` | no | the model's context window in tokens (default 40960, the course endpoint's); lower it for a smaller local window |
| `AGENT_REASONING_NONE` | no | `1` also sends `reasoning_effort: "none"`. Ollama needs this: it ignores `chat_template_kwargs` and `/no_think`, so without it thinking stays on (off for grading) |
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
| D-17 | A turn that only states something is acknowledged, never refused. A statement here means: no question, no request phrasing, no file named, and no sentence or clause that opens with an imperative. Example: "My total budget is 1200 euros. Don't change anything yet." This holds even when the statement contains a verb such as total, list, read or change. |
| D-18 | CSV comparisons: blank, `N/A` and other text cells never satisfy a numeric or date comparison. Dates in common formats (ISO, `M/D/YYYY`, `March 1, 2026`) are compared as dates. Row IDs match exactly first, so `007` never also selects `7`. |
| D-19 | A run that ends in an error restores any file it deleted, so the workspace is never left half-changed. |
| D-20 | Long files are read in parts (`read_file` with `offset`). An answer saying something is "not in the file" is pushed back while part of the file is still unread. |
| D-21 | Summaries: a word count that describes the sources ("the notes are each under 40 words") is not a limit on the output, and "50 to 80 words" allows up to 80. "One bullet per source file" means one bullet for each named source. When the prompt asks for a total, a sum of source numbers counts as supported. |
| D-22 | CP3: report keys ending in `_code` take the logged value's own type unless they are status or exit codes; `*_cents` keys are integers. A root-cause value is checked against the innermost `Caused by:` and lock-holder classes of the stack traces linked to the request. Before accepting `finish` with time to spare, the programming loop has the model test each numbered requirement itself. |
| D-23 | Read answers: an answer is pushed back once when it states a multi-digit number that appears nowhere it could have come from. Those sources are the files read, the question, the conversation and tool results such as `calculate`. This catches totals added in the model's head (12 + 3 damaged = 15). Single digits are exempt, and after one pushback the answer is accepted. |
| D-24 | A verb used as a noun at the start of a statement ("Read receipts are on", "List prices went up", "Check-in time is 3 PM") is not a command. A first-person statement may name a file ("I store my recipes in recipes.txt") and is still only acknowledged. "X needs to be deleted" counts as a request. |
| D-25 | Per-item word limits are recognised in every word order ("each bullet under 25 words", "15 words each", "3 sentences, each under 20 words") and are checked per line or per sentence. "Between N and M words" is a range, and its minimum is enforced. |
| D-26 | Older session turns without file actions keep up to 600 characters instead of 200, within a 12,000-character budget, so facts stated early are not cut off. |
| D-27 | `csv_delete_rows` takes `values` (any of several) and `and_column/and_operator/and_value` (both conditions). A literal "x or y" that matches nothing is split into separate values. Slash dates follow the column's own day/month order. Removing no rows or every row adds a warning to the result. |
| D-28 | CP3: the root-cause check blames the most recent linked stack trace: the lock holder, or the first non-JDK/framework frame under the innermost cause. `*_number` keys keep the logged value's type, and an integer that the logs only show inside an identifier is flagged. An identical tool call is refused from the 4th repeat on, a repeated `grep` shows more matches, and a report with no findings ends with status error. The self-review shows the task commands' current outputs. |

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
| `bash tests/story_all.sh` | yes | one connected story through CP1, CP2 (context and isolation) and CP3 in a single session, PASS/FAIL per step | 5-7 min |
| `python3 tests/smoke_all.py [--no-cp3]` | yes | one prompt per main objective of CP1, CP2 and CP3, with PASS/FAIL per step (see "Trying it by hand") | about 5 min |
| `python3 tests/adversarial/runner.py --submission . --label NAME [--only cp2,CX-12]` | yes | adversarial grader-style suite: 155 cases (65 CP1, 76 CP2 conversations, 14 CP3 runs), including the four failed hidden CP2 cases, independent CP3 log/spec generators, and reruns of failures; `make_report.py` writes `tests/adversarial/REPORT.md` | about 70 min |
| `AGENT_UNDER_TEST=$PWD/run_agent.sh python3 tests/heldout.py --reps 3` | yes | 33 held-out conversations, written after development and never tuned on: statement-shaped facts and requests, CSV, CP1 corner cases | about 25 min |
| `bash tests/kaggle_eval.sh [quick\|full]` | no | on a Kaggle T4 x2 notebook: installs vLLM, serves `Qwen/Qwen3-14B-AWQ` locally, checks that thinking is off, then runs story_all, the held-out set and test_live (`full` adds the adversarial CP1/CP2 cases and four CP3 runs); results in `/kaggle/working/eval_results.tgz` | quick about 40 min, full about 70 min |

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
- **Kaggle (self-hosted vLLM, free T4 x2)**: `bash tests/kaggle_eval.sh full`; see "Self-hosted Qwen3-14B on
  Kaggle" under Test results.

## Test results

Measured on 2026-10-17 on the final code, with the course endpoint (`Qwen/Qwen3-14B`) on macOS. Every live
run uses a fresh workspace and a fresh session store per case.

| Suite | Result |
|---|---|
| Offline (`bash tests/run_offline.sh`: static, unit, mock end-to-end, fault injection) | **505/505** |
| Grader simulation (`bash tests/grader_sim.sh`: the zip is built, unpacked read-only into a path with spaces, and the offline suite is run under `/bin/bash` 3.2 with a sanitized env) | **pass**, exit 0 |
| Project live suite (`tests/test_live.py`, CP1 + CP2) | **99/99** |
| Held-out set (`tests/heldout.py --reps 3`, 33 conversations written after development) | **33/33**; a conversation counts only if all 3 reps pass |
| Adversarial suite, CP1 + CP2 (`tests/adversarial`, 141 cases, failures rerun twice) | **141/141** on the first run, no flaky cases |
| The four failed hidden CP2 cases from the hw1-2 report (GR-01 to GR-04) | **4/4** |
| CP3 debugging: audit generator seeds 21 and 22, every scenario (`tests/live_cp3.py debug`) | **12/12** and **12/12** |
| CP3 debugging: independent generator (DB-1 to DB-4, DB-B02, unseen seeds 31 and 32) | **12/12** on each |
| CP3 debugging: hard variants DB-B01 (non-lock cause) and DB-B03 (retried upstream call) | 11/12 and 10/12 |
| CP3 programming: official `tally.py` prompt (PG-O), 5 runs | **6/6 on all 5**, 138-220 s |
| CP3 programming: `wordstat.py` practice task (PG-W), 5 runs | 4/4 on 3 runs, 3/4 on 2 (`find WORD --file PATH`), 164-242 s |
| CP3 programming: invented hidden-requirement variants D / E / F | 10/12, 1/12, 9/12 (model-capability limits, see below) |

Full per-case details, including reproduction commands and root-cause notes for every failure found during
development, are in `tests/adversarial/REPORT.md`. The case catalog is in `tests/adversarial/CASES.md`.

### Self-hosted Qwen3-14B on Kaggle (vLLM)

To test against a model other than the course endpoint, the agent was also run against its own vLLM server on
a Kaggle notebook (2x Tesla T4, driver 580, torch 2.13 + CUDA 13.0, vLLM 0.32.0), serving
`Qwen/Qwen3-14B-AWQ` (4-bit AWQ, 40960-token context) under the name `Qwen/Qwen3-14B`, with
`bash tests/kaggle_eval.sh full`. The thinking check sends the grader's request
(`chat_template_kwargs: {"enable_thinking": false}`) and got `content='Hello.'` in 3 tokens: thinking is off.
The T4s generate about 20 tokens/s per request, slower than the course endpoint.

| Suite | Kaggle vLLM (AWQ, T4 x2) | Course endpoint |
|---|---|---|
| `story_all.sh` | **13/13** | 13/13 |
| Held-out set (1 rep) | 32/33 (`csv_or_condition`) | 33/33 x3 |
| `test_live.py` CP1 + CP2 | 98/99 (1 timeout, `edge_create_long_line`) | 99/99 |
| Adversarial CP1 + CP2 | 140/141 (`SM-B05`) | 141/141 |
| CP3 `tally.py` (PG-O) | **6/6** in 196 s | 6/6 on 5 runs |
| CP3 debugging DB-1 and DB-S31 | **12/12** and **12/12** | 12/12 and 12/12 |
| CP3 `wordstat.py` (PG-W) | 2/4 | 4/4 on 3 of 5 runs |

The three CP1/CP2 misses were agent bugs that the different model exposed, and are fixed:

- `csv_or_condition`: the model put one value in `value` and the other in `values`, and the CSV delete
  tool used only `values`. It now removes rows matching either.
- `SM-B05`: the model miscomputed a duration (71 minutes instead of 63). The number guard rejected it as
  intended, but the model then dropped the number. Durations between times in the sources now count as
  supported when the request asks how long something lasted, and the rejection names the earliest and latest
  times and the minutes between them.
- `edge_create_long_line`: on the slower GPUs a looping reply hit the 75 s call limit twice. The retry after
  a timeout is now capped at 400 tokens, so a cut-off write of literal content is completed from the request.

After the fixes, on the course endpoint: offline 505/505, `test_live.py` 99/99, the held-out CSV
conversations 5/5 x3, and the adversarial summary and CSV cases (SM, CV, CX) 75/75. PG-W stays a
model-capability miss (see Known limitations).

### `bash tests/story_all.sh` (one story through all three checkpoints)

```
== CP2 context: a fact stated in a statement-only first turn
   PASS statement turn is acknowledged
== CP1 create (uses the earlier fact)
   PASS memo.txt names the team lead
== CP1 read and answer
   PASS answer is 12 and the file is unchanged
== CP1 multi-file summary
   PASS summary exists, under 50 words, names the cause
== CP1 delete
   PASS old.txt gone, other files kept
== CP2 CSV with follow-ups that rely on earlier turns
   PASS total is 300
   PASS follow-up average is 150
   PASS copy equals sales.csv
   PASS active.csv keeps rows 1 and 3, orders.csv unchanged
== CP2 session isolation
   PASS session 78 does not know the team lead
   PASS session 77 still remembers
== CP3 programming: the official tally.py task (requirements 1-6 graded)
   tally.py requirements passed: 6/6
   PASS tally.py passes requirements 1-6
== CP3 debugging: trace one failed request across four logs into REPORT.json
   REPORT.json keys correct: 12/12
   PASS at least 11 of 12 keys correct
13 passed, 0 failed
```

### `python3 tests/smoke_all.py` (one step per objective)

```
PASS  CP1  create a file                      exit=0   4.7s  Created notes/todo.txt with the content 'buy milk'.
PASS  CP1  delete a file                      exit=0   3.7s  Deleted old.txt.
PASS  CP1  read and answer                    exit=0   5.2s  There are 12 blue notebooks currently in stock.
PASS  CP1  multi-file summary                 exit=0   8.8s  Created reports/summary.txt with a summary under 50 words.
PASS  CP2  statement turn (memory)            exit=0   2.3s  Noted: your project code name is Cedar.
PASS  CP2  recall in same session             exit=0   2.1s  Your project code name is Cedar.
PASS  CP2  isolation (other session)          exit=0   3.4s  The question cannot be answered as there is no information about your project code name in
PASS  CP2  CSV total                          exit=0   5.3s  The total of the revenue column in sales.csv is 300.
PASS  CP2  follow-up uses context             exit=0   4.5s  The average of the revenue column in sales.csv is 150.
PASS  CP2  CSV copy from context              exit=0   4.4s  Saved a copy of the table as reports/sales_copy.csv.
PASS  CP2  CSV remove rows                    exit=0   6.0s  Removed rows with status 'cancelled' from orders.csv and saved the result as active.csv.
        tally.py requirements passed: 6/6
PASS  CP3  programming (tally.py)             exit=0 211.6s  ...
        REPORT.json keys correct: 12/12
PASS  CP3  debugging (REPORT.json)            exit=0  57.9s  ...

13/13 steps passed in 320s
```

### `bash tests/grader_sim.sh` (the packaged zip, tested the way the grader will run it)

```
wrote dist/hw1_submission.zip (98 entries)
GS-03 PASS: 98 entries, required files present, nothing excluded leaked
GS-05: /bin/bash is GNU bash, version 3.2.57(1)-release (arm64-apple-darwin26)
suite                        result   detail
test_static                  PASS     Ran 9 tests in 0.686s
test_core                    PASS     Ran 20 tests in 13.834s
test_sessions_store          PASS     Ran 10 tests in 0.357s
test_cp3_units               PASS     Ran 19 tests in 1.516s
test_adversarial_fixes       PASS     Ran 25 tests in 0.531s
test_sandbox                 PASS     Ran 53 tests in 0.144s
test_verify                  PASS     Ran 24 tests in 0.085s
test_csv                     PASS     Ran 50 tests in 0.438s
test_offline                 PASS     56 cases: PASS=56
test_edge_cases              PASS     115 cases: PASS=115
test_sessions                PASS     78 cases: PASS=78
test_cp3_mock                PASS     28 cases: PASS=28
test_contract                PASS     16 cases: PASS=16
------------------------------------------------------------
tests passed: 503, failed: 0, duration: 214s
ALL OFFLINE SUITES PASSED
grader simulation exit: 0
```

## Known limitations

- **Model limits on CP3 programming.** Qwen3-14B without thinking sometimes leaves a requirement unimplemented
  or misreads one, and its own tests rarely cover the edge cases a requirement names. The harness's checks,
  self-review, best-version restore and broken-edit restore recover most cases. Still observed:
  - `count --invalid` (variant D);
  - `$`/`+` amounts and date filters (variant E);
  - `monthly --json` (variant F);
  - `find WORD --file PATH` in 2 of 5 `wordstat` runs.
- **CP3 programming time.** Runs take 140-240 s of the 300 s limit (`tally` 138-220 s, `wordstat` 164-242 s).
  The internal deadline (285 s) and wrap-up keep every run inside the limit, but there is little spare time
  on a slow endpoint.
- **Hidden debugging keys.** These are inferred from the task wording; a key whose type is ambiguous is typed
  from the model's value.
- **Untested platforms.** Python 3.8 and Linux containers were not run (not installed on the development
  machine). The unit suites pass on Python 3.9.6, the grader simulation runs the agent under 3.9.6, and a static
  test parses every module with the Python 3.8 grammar.
- **Shared endpoint.** Busy/rate-limited responses (429/503, proxy HTML 400s) are retried with backoff for up
  to ~60 s inside the deadline; longer outages fail the run.
- **Throughput.** At about 25 tokens/s, a CP3 program write takes 60–90 s. The 300 s limit leaves room for
  roughly two full rewrites plus edits.
