# Homework 1 — File-management agent (Checkpoints 1 and 2)

A small agent harness, built from scratch, that works inside the current working directory.
Qwen3-14B does the language understanding. Plain, deterministic Python does every filesystem operation and every calculation.

- **Checkpoint 1:** create a file, delete a file, answer a question about a file, and summarize several files into a new file.
- **Checkpoint 2:** multi-turn sessions (a numeric session ID as the second argument, with history kept across separate processes) and CSV tasks: answer questions about values, total or average a column, add values in a row, update a value in a row found by its ID, remove rows that match a condition, and save a copy of a table.

Checkpoint 2 is built on the same framework. Every Checkpoint 1 capability, guard and test is unchanged and still passes.

## Folder structure
```
run_agent.sh          entry point (bash); finds its own directory and runs agent/main.py
agent/main.py         entry: builds the harness, enforces the stdout JSON contract and exit codes
agent/harness.py      the agent harness: bounded tool-calling loop, system prompt, guardrail checks
agent/tools.py        tool registry: list_files, read_file, read_files, write_file, delete_file, copy_file,
                      csv_aggregate, csv_row_sum, csv_update_cell, csv_delete_rows, finish
                      (each with a JSON schema, argument validation and a sandboxed handler)
agent/csvtools.py     deterministic CSV engine: exact decimal totals/averages, row sums, cell updates and
                      row removal that keep every untouched row byte-for-byte
agent/session.py      multi-turn session history, stored on disk per (workspace, session ID)
agent/guards.py       deterministic guardrails: tool-call parsing, anti-hallucination path checks,
                      literal-content restore, word-limit parsing/trimming
agent/verify.py       independent verification: workspace snapshot before the task, disk checks after it
agent/sandbox.py      workspace path validation + the only filesystem primitives (create/delete/read)
agent/llm.py          OpenAI-compatible /chat/completions client (urllib), timeouts, retries, <think> stripping
agent/errors.py       AgentError (user-facing error messages)
tests/test_offline.py end-to-end tests through run_agent.sh against a scripted mock model server (no key needed)
tests/test_sandbox.py unit tests for sandbox, tool schemas and guards
tests/test_verify.py  verification tests: dishonest tools (a write that doesn't write, etc.) must never yield success
tests/test_edge_cases.py  end-to-end edge cases with the mock model
tests/test_csv.py     unit tests for the CSV engine, CSV/copy tools, session store and argument parsing
tests/test_sessions.py  end-to-end multi-turn and CSV tests with the mock model (each turn a separate process)
tests/check_run.py    run the agent and print what actually changed on disk next to what it claimed
tests/run_offline.sh  runs every suite that needs no API key
tests/test_live.py    live tests against the real Qwen3-14B endpoint, in two separated parts:
                      Checkpoint 1 (single requests) and Checkpoint 2 (multi-turn conversations, names "cp2_...")
tests/mock_server.py, tests/common.py   test helpers
requirements.txt      empty on purpose: standard library only
```

## Language / runtime
- Python 3 (standard library only, compatible with Python 3.8+). Developed with Python 3.13.2.
- Bash is used only for the entry script (tested with GNU bash 3.2.57).
- Operating system tested: macOS 27.0 (build 26A428, Darwin 27, Apple silicon / arm64).
- Linux uses the same code paths, because only POSIX APIs are used. It has not been run on Linux.

## Installation
Nothing to install. You only need `python3` on `PATH`.

## Running it locally
You need Python 3.8 or newer and your Qwen API key. There's nothing to install.

### What you need
| Requirement | Details |
|---|---|
| Operating system | macOS or Linux. Developed and tested on macOS 27.0 (build 26A428, Apple silicon / arm64). |
| Shell | Bash. Tested with GNU bash 3.2.57 at `/bin/bash`; your terminal can be zsh, since the agent is started with `bash run_agent.sh`. |
| Python | 3.8 or newer, available as `python3` on `PATH`. Tested with Python 3.13.2. Check with `python3 --version`. |
| Python packages | None. Only the standard library is used; there is no virtual environment and no `pip install`. |
| Network | HTTPS access to the Qwen endpoint, `https://llm.montii.me/v1`. No other host is contacted. |
| API key | A key for that endpoint, passed in the `QWEN_API_KEY` environment variable. It is never stored in the submission. |
| Environment variables | `OPENAI_BASE_URL`, `MODEL` and `QWEN_API_KEY`. All three are required. |
| Standard tools | `printf`, `mkdir`, `cat`, `ls` and `chmod`, used only by the example commands below. |

The offline tests need only Python and Bash. They start their own mock model on `127.0.0.1`, so they need no key and no internet.

### Steps
The commands below use the path where this submission lives on the development machine,
`/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1`. Replace it with wherever you unpacked the submission.

1. Save your key once, outside the submission folder:

```
printf '%s' 'YOUR_KEY_HERE' > ~/.qwen_key && chmod 600 ~/.qwen_key
```

2. Set the three environment variables in your terminal session:

```
export OPENAI_BASE_URL="https://llm.montii.me/v1" MODEL="Qwen/Qwen3-14B" QWEN_API_KEY="$(cat ~/.qwen_key)"
```

3. Make a scratch workspace and go into it. The agent treats the folder you run it from as its workspace, so never run it from inside the submission folder.

```
mkdir -p ~/agent_playground && cd ~/agent_playground
```

4. Run the agent with a request. The path in quotes is the submission's `run_agent.sh`:

```
bash "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/run_agent.sh" "Create notes/hello.txt containing: Hello world"
```

It prints one JSON line, such as `{"status": "success", "message": "Created notes/hello.txt."}`. Its step-by-step log goes to stderr, so you'll see it in the terminal too. To hide the log, add `2>/dev/null` to the end of the command.

Some example requests for the other three tasks:

* Read and answer:

```
printf 'Final presentation: December 5\nReport due: December 12\n' > schedule.txt
```

```
bash "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/run_agent.sh" "According to schedule.txt, when is the final presentation?"
```

* Summarize:

```
bash "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/run_agent.sh" "Summarize schedule.txt and notes/hello.txt into reports/summary.txt in under 40 words."
```

* Delete:

```
bash "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/run_agent.sh" "Delete notes/hello.txt."
```

To check the exit code after any run (0 means success, 1 means error):

```
echo $?
```

Running the test suites (see the **Tests** section below for every option). These are run from the submission folder.

* Offline tests need no key:

```
cd "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1" && bash tests/run_offline.sh
```

* Live tests call the real Qwen endpoint and read the key from `~/.qwen_key`:

```
cd "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1" && python3 tests/test_live.py
```

* To run just one group of live cases, pass `cp1` or `cp2` for a whole checkpoint, or a name fragment (`create_`, `delete_`, `read_`, `summary_`, `edge_`, `cp2_`):

```
python3 tests/test_live.py summary_
```

### Checking what really happened
The agent verifies its own work before it reports success, but you can also check the disk yourself.

* Look at the result directly:

```
cat notes/hello.txt
```

```
ls -la notes
```

* Or run the agent through `tests/check_run.py`. It prints the agent's JSON and exit code, then the files that were actually created, deleted or modified, with a content preview:

```
cd ~/agent_playground && python3 "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/tests/check_run.py" "Create notes/hello.txt containing: Hello world"
```

### Useful extras
* Save the step-by-step log to a file instead of the terminal:

```
bash "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/run_agent.sh" "Delete notes/hello.txt." 2> agent.log
```

* Check that the three variables are set, without printing the key:

```
echo "$OPENAI_BASE_URL | $MODEL | key length: ${#QWEN_API_KEY}"
```

* The `export` line lasts only for the current terminal window. Run it again in every new terminal.
* Remove the scratch workspace when you're done:

```
rm -r ~/agent_playground
```

### Troubleshooting
| What you see | Cause and fix |
|---|---|
| `Missing required environment variable(s): ...` | The `export` line wasn't run in this terminal. Run step 2 again. |
| `HTTP 401: the API key was rejected` | The key is wrong or empty. Check `~/.qwen_key`, then run step 2 again. |
| `HTTP 403: blocked by the endpoint's firewall` | The endpoint's firewall rejected the request. The agent already sends its own `User-Agent` to avoid this; if it still appears, the block is on the server side. |
| `HTTP 502`, `520` to `524`, `530`, or `connection failure or timeout` | The Qwen server is down or unreachable (530 with code 1033 means its Cloudflare tunnel is offline). The agent retries a few times; wait and try again. In `tests/test_live.py` these show as `INFRA`, not `FAIL`. |
| `Python 3.8+ is not installed.` | `python3` isn't on `PATH`, or it is older than 3.8. |
| `Refusing path ...` | The request pointed outside the workspace, or at the agent's own code. This is intended. |
| `Could not find ...` | The file isn't in the folder you ran the agent from. Check with `ls`. |
| `Verification failed: ...` | The disk didn't match what the agent intended, so it refused to report success. |

## How the grader runs it
```
cd /path/to/task_workspace
OPENAI_BASE_URL="https://llm.montii.me/v1" MODEL="Qwen/Qwen3-14B" QWEN_API_KEY="..." \
  bash /path/to/submission/run_agent.sh "$PROMPT" "$SESSION_ID"
```
- The first argument is the complete prompt. The second is the numeric session ID (Checkpoint 2).
- With no session ID, the call is stateless, exactly as in Checkpoint 1.
- stdout is always exactly one JSON object, `{"status": "success"|"error", "message": "..."}`. Exit code is 0 on success and 1 on error. Diagnostics go to stderr.
- Requested answers, including numeric results, are in `message`.

## Checkpoint 2: multi-turn sessions
Each call to `run_agent.sh` is a new process, so the conversation is saved on disk between calls.

- **What is stored:** one small JSON file per session. For every turn it holds the prompt, the tool calls the agent made with their short results (for example `csv_aggregate {"path": "sales.csv", "column": "revenue", "operation": "sum"} -> ... = 300`), and the final status and message. File contents are not stored; the agent re-reads files, because they may have changed.
- **How it is used:** the earlier turns are shown to the model before the new request, so "What is the average?" resolves to the same file and column as the previous calculation. Numbers are recomputed with the tools, not recalled.
- **Separation:** history is keyed by the workspace directory and the session ID. Different session IDs never share history, and the same ID in a different task directory starts fresh.
- **Where it is stored:** outside the task workspace, so no stray files appear among the task's files, and outside the submission, which may be read-only. The agent uses the first writable location from this list:
  1. `$AGENT_SESSION_DIR`, if set (the tests use this);
  2. `/tmp/file-agent-sessions-<uid>`;
  3. the system temp directory;
  4. `~/.cache/file-agent-sessions`;
  5. as a last resort, a hidden `.agent_sessions` folder in the workspace, which the agent ignores and protects.
- **Housekeeping:** histories older than 24 hours are ignored and removed. At most 30 turns are kept per session. A storage problem never fails the task.
- **Failed turns are remembered too**, so "Sorry, I meant sales.csv" works after an error.

The assignment's example, run from the submission folder with the three variables set:
```
submission_dir="$(pwd)"; task_dir="$(mktemp -d)"; cd "$task_dir"
printf 'month,revenue\nJanuary,120\nFebruary,180\n' > sales.csv
bash "$submission_dir/run_agent.sh" "Read sales.csv and tell me the total of the revenue column." "1001"   # 300
bash "$submission_dir/run_agent.sh" "What is the average?" "1001"                                         # 150
bash "$submission_dir/run_agent.sh" "Save a copy of the table as reports/sales_copy.csv." "1001"
cat reports/sales_copy.csv
```

## Checkpoint 2: CSV tasks
The model chooses the operation and its arguments. Python code does the reading, the arithmetic and the rewriting, so results don't depend on the model's ability to add numbers or retype a table.

| Request | Tool | What the code guarantees |
|---|---|---|
| Question about values | `read_file` | The model answers from the rows it is shown. |
| Total, average, min, max, count of a column | `csv_aggregate` | Exact decimal arithmetic (0.1 + 0.2 = 0.3). `1,200`, `$3.50` and `45%` are read as numbers; blank cells are skipped. |
| Add values in a selected row | `csv_row_sum` | The row is found by its ID; the named columns are added exactly. |
| Update a value in the row with a given ID | `csv_update_cell` | Only that cell changes. Every other row is kept byte-for-byte, including quoting and line endings. |
| Remove rows matching a condition | `csv_delete_rows` | The header and all remaining rows are untouched. Conditions: equals, not equals, contains, greater/less than (or equal), is empty. Text matches ignore case. |
| Save a copy of a table | `copy_file` | A byte-identical copy; parent folders are created. |

Other details:
- **Output path:** when the prompt says "save the result as X", the result goes to X and the original stays unchanged. If the model tries to overwrite the original instead, the harness refuses and tells it to use X. With no output named ("... and save the file"), the file itself is updated.
- **Column names** match regardless of case, spaces or underscores (`Unit Price` matches `unit_price`). An unknown column returns the list of real columns, so the model can correct itself.
- **Delimiters** `,` `;` tab and `|` are detected, and CRLF line endings are preserved.
- **Safety nets:**
  - If a request asks for a total or average and the model answers without calling the calculator, it is sent back once.
  - A value the tool computed is added to the final message if the model left it out.
  - If the model retypes a table as a "copy", the original's exact text is written instead.
  - A hand-written `.csv` always ends with a newline.
- **Multi-turn guards:** a file may be created, edited or deleted if it is named in the current request or in an earlier turn of the same session. One file may be changed per turn (several edits to that same file are allowed). A request that only asks a question can't modify the file.

## Runs unattended in a sandbox
The agent is built to be driven by fixed commands, with no human in the loop.
- Its only input is the prompt passed as the first argument. `run_agent.sh` connects stdin to `/dev/null`, so nothing can ever block waiting for a reply.
- Qwen is told there is no human to answer questions. It must act on the most reasonable interpretation, or finish with an error. If it still asks the user something, the harness rejects that once and makes it act.
- The only network request is to `$OPENAI_BASE_URL/chat/completions`. There are no config files, no dependence on `$HOME`, and no locale requirement.
- Nothing is written to the submission directory (`PYTHONDONTWRITEBYTECODE=1`), so a read-only submission works.
- Every run ends with exactly one JSON object and an exit code, within about 170 s.

These cases are covered by `tests/test_offline.py`:
- a run with stdin left open, which must not hang;
- a minimal environment with no `HOME` or locale;
- a read-only copy of the submission running all four task types.

## Verification: success is never taken on trust
The agent checks the disk itself before it reports success (`agent/verify.py`).
1. Before the task starts, it takes a SHA-256 snapshot of every entry in the workspace.
2. Right after a write, it reads the file back and compares it byte-for-byte with the intended content. Right after a delete, it confirms the path is gone. If the check fails, the tool call counts as an error, however the tool itself answered.
3. Just before printing `success`, it checks everything again against the starting snapshot:
   - the target is present with the right bytes (for a write) or absent (for a delete);
   - every other file is unchanged, with nothing removed and nothing unexpectedly created;
   - for read and summary tasks, the source files are byte-identical.

   Any discrepancy turns the result into `{"status":"error","message":"Verification failed: ..."}` with exit code 1.
4. The model can't talk its way to success. A `finish(success)` with no completed operation is rejected.

To see the ground truth yourself, run the agent through `tests/check_run.py` from a workspace:
```
cd /path/to/workspace
python3 /path/to/submission/tests/check_run.py "Delete notes/old-draft.txt."
```
It prints the agent's JSON and exit code, then the files that were actually created, deleted or modified, with a content preview.

## How it works
```
prompt ─▶ Harness ──(system prompt + tool schemas + workspace listing + request)──▶ Qwen3-14B
             ▲                                                                        │
             │                                              {"tool": ..., "args": {...}}
             │                                                                        ▼
        observation ◀── execute in Sandbox ◀── guardrails ◀── schema validation ◀── parse
             ... repeat until the model calls finish(status, message) ─▶ {"status","message"} on stdout
```
- **Tools** (`agent/tools.py`): `list_files`, `read_file`, `read_files`, `write_file`, `delete_file`, `copy_file`, `csv_aggregate`, `csv_row_sum`, `csv_update_cell`, `csv_delete_rows` and `finish`. Each tool has a JSON schema; the schemas are written into the system prompt. Bad arguments are rejected (wrong types, missing or unknown fields, bad enum values). Handlers only call `sandbox.py`. There is no shell tool, and model output is never executed as code.
- **Loop** (`agent/harness.py`): the model makes one JSON tool call per turn. The harness validates it and applies the guardrails. Then it either executes the call or sends the error back as an observation, so the model can correct itself. The loop stops after 12 steps, after about 170 seconds, or after 3 consecutive malformed replies. Thinking mode is turned on only for turns where file contents are in context (answering and summarizing).
- **Guardrails** (`agent/guards.py`, enforced by the harness):
  - Only one file may be changed per task (several edits to that same file are allowed).
  - A delete must name exactly a file named in the request, so `todo.md` won't match `todo-old.md`. Wildcards and directories are refused.
  - The write target must be named in the request, and a source file that was read can never be overwritten.
  - Quoted literal content is restored exactly from the prompt.
  - A summary can't be written until every existing file named in the request has been read.
  - Summary word limits are parsed from the request. Over-long content is sent back to the model up to twice, then trimmed deterministically.
  - The model can't report success without doing any operation, or without writing a requested output file.
- **Sandbox** (`agent/sandbox.py`):
  - Paths must be relative. `..` and absolute paths are refused.
  - Parent directories are resolved through symlinks and must stay inside the workspace.
  - The agent never writes through a symlink, never deletes a directory, and never deletes more than one file.

## Tests
There are two kinds of tests:
- **Offline tests** need no API key and no internet. The unit tests call the agent's modules directly. The end-to-end tests run the real `run_agent.sh` against a small mock model server on `127.0.0.1` that replays scripted tool calls, so harness behaviour (guards, sandbox, verification, sessions, CSV engine, error handling) is tested exactly and repeatably.
- **Live tests** run the real `run_agent.sh` against the real Qwen3-14B endpoint, to check that the actual model understands the requests.

Every end-to-end test (offline and live) runs the agent exactly as the grader does: `cd` into a fresh temporary task folder, then `bash run_agent.sh "$PROMPT"` for Checkpoint 1 or `bash run_agent.sh "$PROMPT" "$SESSION_ID"` for Checkpoint 2. Each turn of a conversation is a separate process. The tests check:
- the exit code and that stdout is exactly one JSON object with string `status` and `message`;
- the actual files on disk, not the agent's message: expected contents byte-for-byte;
- SHA-256 checksums before and after, proving unrelated and source files are unchanged.

### What you need
- Python 3.8+ and Bash, as for the agent. Nothing to install.
- For the live tests only: your key, either in `$QWEN_API_KEY` or in `~/.qwen_key`:

```
printf '%s' 'YOUR_KEY_HERE' > ~/.qwen_key && chmod 600 ~/.qwen_key
```

All test commands are run from the submission folder:

```
cd "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1"
```

### Run every offline test (no key needed, about 1-2 minutes)

```
bash tests/run_offline.sh
```

It runs the six offline suites in turn, prints only failures and the per-suite totals, and ends with `ALL OFFLINE SUITES PASSED` (exit code 0) or `SOME OFFLINE TESTS FAILED` (exit code 1).

### Run one offline suite

| Command | Checkpoint | Count | What it covers |
|---|---|---|---|
| `python3 tests/test_sandbox.py` | 1 | 53 unit tests | Path validation, symlink/hard-link/FIFO handling, encodings, tool schemas and aliases, guards (name checks, literal-content restore, word/char limits, salvaging cut-off replies), lenient JSON parsing that never executes code, LLM helpers |
| `python3 tests/test_verify.py` | 1+2 | 24 unit tests | Independent verification: dishonest tools (a write that doesn't write, a delete that deletes the wrong file, hidden side effects) must never produce `success` |
| `python3 tests/test_csv.py` | 2 | 61 unit tests | CSV engine (exact decimals, currency/thousands/percent, delimiters, quoting, CRLF, BOM, extra fields, duplicate headers), CSV and copy tools, session store (separation, expiry, corruption, fallback), argument parsing |
| `python3 tests/test_offline.py` | 1 | 56 end-to-end | The four Checkpoint 1 tasks, the stdout contract, security (traversal, absolute paths, symlink escapes), malformed replies, API failures, unattended and read-only running |
| `python3 tests/test_edge_cases.py` | 1 | 107 end-to-end | Edge cases, listed below |
| `python3 tests/test_sessions.py` | 2 | 62 end-to-end | Multi-turn sessions and CSV tasks, listed below |

To run only some end-to-end cases, pass part of their names:

```
python3 tests/test_edge_cases.py delete_
```

```
python3 tests/test_sessions.py csv_
```

### Run the live tests (needs the key and the endpoint)
`tests/test_live.py` has two clearly separated parts. Each prints its own header and tally, followed by an overall total.

| Command | What runs | Typical time |
|---|---|---|
| `python3 tests/test_live.py` | Both checkpoints | about 6-8 minutes |
| `python3 tests/test_live.py cp1` | Checkpoint 1: 66 single-request cases | about 4 minutes |
| `python3 tests/test_live.py cp2` | Checkpoint 2: 25 multi-turn conversations (58 turns) | about 3 minutes |
| `python3 tests/test_live.py edge_` | Cases whose name contains `edge_` | |
| `python3 tests/test_live.py cp2 update` | Checkpoint 2 conversations whose name contains `update` | |

Before a live run, this quick check confirms the endpoint is up. `200` means up; `502`, `520`-`524` or `530` mean the server is down.

```
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $(cat ~/.qwen_key)" https://llm.montii.me/v1/models
```

### Reading the results
- `PASS name` means the case passed. For live cases, the line above it shows each turn's time and the agent's message.
- `FAIL name -- reason` means the agent got it wrong. The reason says what was expected; live failures also include the agent's step-by-step log, showing what the model tried and what came back.
- `INFRA name -- ...` (live only) means the endpoint failed (down, timeout, rejected key), not the agent. Re-run when the endpoint is healthy.
- Each suite ends with a total such as `91 cases: PASS=91`. The exit code is 0 only if everything passed.

### Edge cases covered
**Checkpoint 1** (`tests/test_edge_cases.py`, and the `edge_` cases in the Checkpoint 1 part of `tests/test_live.py`):
- **Paths:** spaces, Unicode (NFC/NFD), right-to-left text, dotfiles, `~name`, names that look like shell flags (`-rf`) or contain shell metacharacters, deep nesting, a workspace path with spaces.
- **Hostile input:** shell metacharacters in content (never executed), content that looks like a tool call, prompt injection inside files, code inside a model reply (parsed as data, never run), `../` and absolute paths, symlink and hard-link escapes.
- **Odd workspaces:** symlink loops, unreadable directories, 3,000-file workspaces, FIFOs, read-only files and directories, binary, UTF-16, Latin-1 and empty files, 200 KB files.
- **Content fidelity:** CRLF and tabs, requested trailing newlines, leading spaces inside quotes, Windows backslash paths, curly quotes, Markdown code fences, 50 KB prompts, long repetitive content (the model is rescued when it loops).
- **Model slip-ups:** wrong directory, a same-named file elsewhere, a name without its extension, extra deletes, repeated failing calls, trailing commas, Python-style quotes, prose around the JSON, two calls in one reply, cut-off replies, runaway generations that time out, null or boolean arguments.
- **Infrastructure:** hung model (time budget), 429 with Retry-After, 401, Cloudflare 403 and 530, empty `choices`, a rejected `chat_template_kwargs`, base-URL variants, invalid UTF-8 in arguments.

**Checkpoint 2** (`tests/test_sessions.py`, `tests/test_csv.py`, and the `cp2_` conversations in `tests/test_live.py`):
- **Sessions:** the assignment's three-turn example, interleaved sessions, the same ID in another workspace, `0` vs `00`, calls without an ID, remembered failed turns, the 30-turn history window, a corrupt or unwritable session store, files edited between turns.
- **Follow-ups:** "What is the average?", "do the same for ID 107", "delete that copy", "save that total to a file", "in the copy, change...", answering from memory, refusing to invent new file names.
- **CSV:**
  - tasks: totals, averages, min/max with the matching row, filtered totals, row sums by ID or by name, updates by ID (save-as or in place), row removal by text or number;
  - file variations: byte-identical copies, BOM files, semicolon and tab delimiters, currency values, leading-zero IDs, case-insensitive column names;
  - refusals: wrong column names (self-corrected), missing IDs, binary, empty or header-only files, questions that must not modify the file, outputs outside the workspace or onto unrelated files.

### Test-only settings
These environment variables exist only so the tests can run quickly and in isolation. The grader never needs them.
- `AGENT_TIME_BUDGET` (seconds) can only *lower* the 170 s total budget, to test timeout handling quickly.
- `AGENT_CALL_TIMEOUT` (seconds) can only *lower* the per-request cap, to test recovery from a hung model.
- `AGENT_SESSION_DIR` points the session store at a temporary folder. The test helpers set it automatically, so test runs never touch the real store.

### Checking a single run by hand
`tests/check_run.py` runs the agent once from your current folder. It prints the agent's JSON and exit code, then what actually changed on disk (created, deleted or modified files, with a content preview):

```
cd ~/agent_playground && python3 "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/tests/check_run.py" "Delete notes/hello.txt."
```

For Checkpoint 2, add the session ID as a second argument:

```
python3 "/Users/aryan/Documents/Grad Files/Agentic_AI/Homework_1/tests/check_run.py" "What is the average?" "1001"
```

## Assumptions
- Created files contain exactly the requested text, with no trailing newline added.
- Creating a file that already exists overwrites it. The write is atomic (a temporary file, then a rename), so a hard-linked file outside the workspace is never modified. Read-only files are refused.
- Delete removes exactly one regular file (or a symlink itself, never its target). Directories, FIFOs, wildcards and "delete all ..." requests are refused with an error. A name given without its extension ("Delete the README") is accepted only if exactly one file matches.
- Deleting is refused once any file has been read in the same task. File contents are treated as data, so an instruction inside a file can't trigger a delete.
- If a requested file is missing, the agent returns an error. The one exception is a read or summary where exactly one file in the workspace has the requested file name; that file is used. Deletes never fall back.
- Answers come from the file content only. The answer text goes in `message`. If the file exists but doesn't contain the answer, the result is success with a message saying so.
- Text files are read as UTF-8 (a BOM is stripped) or as UTF-16 when a BOM is present. Binary files are refused.
- Total runtime is capped internally at about 170 s, with per-request HTTP timeouts.
- The session ID is the second argument and must be numeric. Any other argument shape is treated as a prompt only, with no session.
- Session history lives outside the workspace (see "Checkpoint 2: multi-turn sessions"). It assumes that the calls of one conversation run on the same machine and in the same task directory.
- CSV files have a header row. Averages are taken over the numeric, non-empty cells of the column.
- A non-integer result is reported exactly (up to 10 decimals) together with its 2-decimal rounding, for example `5.9375 (rounded to 2 decimals: 5.94)`.
