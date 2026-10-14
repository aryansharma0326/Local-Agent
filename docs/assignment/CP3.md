In this final checkpoint of Homework 1, extend your agent to handle two new types of tasks: programming and debugging.

You must continue building on the same agent framework you submitted for Checkpoint 1 and Checkpoint 2. Retain its file-management, multi-turn, session-isolation, and CSV capabilities. We will rerun the earlier checkpoint tests on your latest submission; earlier scores do not automatically carry over to this submission.

All general Homework 1 rules continue to apply, including the from-scratch requirement, model and external-API restrictions, workspace rules, command and output requirements, and submission contents. The time limit for each test case in this final submission is five minutes, and we will use online API for fast token streaming when we grade your submission.

Testing and grading

The final submission is worth 14 points:

COMPONENT	HOW IT IS GRADED	MAXIMUM POINTS
Checkpoint 1 capabilities	Rerun the HW1-1 tests on your latest submission, using the original HW1-1 scoring rules.	3
Checkpoint 2 capabilities	Rerun the HW1-2 tests on your latest submission, using the original HW1-2 scoring rules.	3
New programming and debugging capabilities	24 separate task prompts: 12 programming tests (6 explicit + 6 hidden) and 12 debugging tests (6 explicit + 6 hidden). Each passing test earns 1/3 point.	8
The 6 points for earlier capabilities are earned again by passing the rerun tests. If your latest submission fails those tests, it loses points under the original checkpoint scoring rules even if an earlier submission passed them. These 6 points are part of the final submission's 14 points, not bonus points.

Each of the 24 new tests is a separate task prompt and is pass/fail. Passing k new tests earns exactly k / 3 points; do not round individual test scores. Your score before any late penalty is:

Final-submission score = HW1-1 rerun score + HW1-2 rerun score + new tests passed / 3

For example, earning all 6 points from the reruns and passing 18 of the 24 new tests earns 6 + 18/3 = 12 points out of 14.

Each category includes six explicit tests and six hidden tests. The hidden tests will be similar to the provided tests, but we do not release them. Test your agent's abilities beyond the provided tests. You can use programming problems from LeetCode as additional practice, providing the problem statement to your agent and checking its solution yourself.

The TA's programming and debugging specifications below retain their original wording. Their references to 1 point per test mean one raw test point, equivalent to 1/3 of a course point under the scaling above. The 24 raw test points scale to 8 course points.

Each of the 24 test cases receives its own task prompt and five-minute (300-second) limit. We will use fast token streaming during grading, so token generation should not be the bottleneck.

We do not provide the grading CSV files, log files, or a sample data package. Your agent must inspect the files supplied in its task workspace and determine how to complete the requested task. Create your own inputs and expected results for development, including debugging cases with known root causes.

Grading procedure: We will run your agentic framework in our own sandbox environment and use an autograder to check its behavior. If your submission cannot run because of configuration or other technical issues, we will use GPT-6 Astra to attempt deployment-related fixes only; we will not fix logic errors in your submission. If those fixes make it runnable, it will be graded normally. If it still cannot run, you will receive 0 for this submission.

Programming task

Your agent is given a specification and must write a command-line tool that satisfies the specified requirements. During grading, the workspace contains the data file transactions.csv (about 40,000 rows); this file is not provided to you.

This is the prompt your agent receives, word for word, except that requirements 7–12 are hidden:

# Task: implement `tally.py`

Write a command-line tool `tally.py` (Python 3, standard library only) in the current directory.
It summarises spending stored in a CSV file. A sample data file, `transactions.csv`, is provided
(it is large: about 40,000 rows).

## Invocation

```
python3 tally.py <command> [arguments] [--file PATH] [--json]
```

* `--file PATH` selects the CSV file. Default: `transactions.csv` in the current directory.
* Options may appear anywhere after the command name.

## CSV format

The first line is a header: `date,description,category,amount`. Every following line is one transaction.
Fields follow normal CSV quoting rules (a field may be wrapped in double quotes and contain commas).
`amount` is a decimal number of dollars (it may be negative, e.g. a refund).

## Requirements

Every requirement below is checked by an automated test. Output must match exactly
(no extra lines, no extra spaces). All money values are printed with exactly two decimals
and no thousands separator, e.g. `1234.50`, `-3.00`.

1. **`count`** — `python3 tally.py count` prints the number of valid transactions, e.g. `39876`.
2. **`total`** — `python3 tally.py total` prints the sum of all amounts, e.g. `1834512.07`.
3. **`total --category NAME`** (an option of the `total` command, not a separate command) — `python3 tally.py total --category NAME` prints the sum of amounts of
   transactions whose category equals NAME. The comparison is case-insensitive.
4. **`top N`** — `python3 tally.py top N` prints the N categories with the largest total amount, one per line,
   as `CATEGORY<TAB>AMOUNT`. Sort by amount descending; if two amounts are equal, sort those categories
   alphabetically ascending. If there are fewer than N categories, print all of them.
5. **category case** — categories are case-insensitive everywhere: `Food`, `FOOD` and `food` are the
   same category. Whenever a category name is printed, it is printed in lowercase.
6. **`range --from D1 --to D2`** — `python3 tally.py range --from YYYY-MM-DD --to YYYY-MM-DD` considers transactions whose
   date is between the two dates, both ends inclusive, and prints one line `count=K total=X.XX`.

7–12. *(hidden)*

When you are done, `tally.py` must be in the current directory.
Grading: each of the 12 requirements is checked by a hidden test and is worth 1 point.

Debugging task

Your agent is given an incident report and the logs of four services. It must follow one failed request across the services to its root cause and write its findings to REPORT.json. During grading, the workspace contains the logs/ directory; these log files are not provided to you.

This is the prompt your agent receives, word for word, except that keys 7–12 are hidden:

**CONSTRAINT: Your final deliverable is a file named `REPORT.json` in the current directory, containing one JSON
object with exactly the 12 keys listed at the end of this task. Write it before you finish.**

# Incident: checkout failure for order ORD-77341

At 2026-09-17 14:32 UTC a customer's checkout for order `ORD-77341` failed. The browser received:

```
HTTP 502 Bad Gateway  (POST /api/checkout, order ORD-77341)
```

Logs from the four services involved are in `logs/`:

- `logs/gateway.log` – API gateway
- `logs/orders.log` – orders service
- `logs/inventory.log` – inventory service
- `logs/payments.log` – payments service

Each log is large (roughly 15,000–25,000 tokens) and noisy, and contains many unrelated errors. Services do not
log each other's IDs: each service tags its lines with its own identifiers, and a line in one service tells you
which identifier to look for in the next. Follow this specific failed request from the gateway to the component
that actually caused it.

## REPORT.json

Answer each question below with the value exactly as it appears in the logs. Use JSON strings for IDs, names,
paths and timestamps, and JSON integers for counts and numbers.

1. `gateway_request_id` — the gateway's request ID (`req=`) of the failed POST /api/checkout for ORD-77341.
2. `upstream_trace_id` — the trace ID the gateway recorded for the upstream call of that request.
3. `hold_id` — the inventory hold that the orders service was waiting on for that trace.
4. `worker` — the inventory worker that hold was queued on.
5. `root_cause_component` — the software component that caused the failure (a class name, not a service or worker
   name), named exactly as it is spelled in the logs.
6. `payment_intent_id` — the payment intent created for ORD-77341.

7–12. *(hidden)*

Example shape (values are placeholders):

```json
{"gateway_request_id": "gw-000000", "upstream_trace_id": "tr-00000000", "hold_id": "hold-000000",
 "worker": "inv-w0", "root_cause_component": "Name", "payment_intent_id": "pi_0000000000",
 ...the six hidden keys...}
```
Grading: each of the 12 keys is checked by a hidden test and is worth 1 point.

Interface

The grader passes the complete task prompt as the first command-line argument and a numeric session ID as the second, running from inside a fresh task workspace:

bash "$SUBMISSION_DIR/run_agent.sh" "$PROMPT" "$SESSION_ID"
The workspace holds the task's input files and a copy of the prompt as TASK.md. Each call must print exactly one JSON object on standard output with the string fields status and message. Both tasks request an output file (tally.py or REPORT.json); the grader checks that file's contents.

Each run (one task) may make at most 120 model calls (successful chat completion requests).

Runtime and earlier-checkpoint compatibility

As in the earlier checkpoints, we do not restrict the language of your harness. Use a widely supported language and dependencies, include dependency files, and document installation steps and required versions. Your submission must run on Linux or macOS through run_agent.sh; Windows users should test in Linux with Bash, such as WSL. The Python-standard-library requirement in the programming example applies to the generated tally.py, not to your harness as a whole.

Continue supporting the earlier checkpoint interfaces for the rerun tests, including the HW1-1 invocation with only a prompt and the HW1-2 invocation with a prompt and session ID. Calls with the same session ID must retain their conversation context across separate processes, and different sessions must remain isolated.

Model

Qwen3-14B behind an OpenAI-compatible Chat Completions API (tool calling supported; max context 40,960 tokens, prompt plus output). Configuration comes from environment variables, which point to the grader's endpoint during grading:

OPENAI_BASE_URL   e.g. https://llm.montii.me/v1
MODEL             Qwen/Qwen3-14B
QWEN_API_KEY      Bearer token
In this exercise, Qwen3's thinking mode must be disabled. Send "chat_template_kwargs": {"enable_thinking": false} in the body of every request.

Submission contents

Submit your complete updated framework through the course Slack platform using submit hw1-3. The deadline is October 25, 2026, at 11:59pm ET.

Submit one archive or repository containing run_agent.sh at the submission root, all source code, dependency files, and a brief readme.md describing the folder structure, tested OS and runtime versions, and setup instructions. Include your own test inputs and expected results. Submit the agent framework that can perform new tasks, not just a hand-written solution to either example.

Do not submit API keys, model weights, generated caches, virtual environments, or temporary workspaces left over from test runs.