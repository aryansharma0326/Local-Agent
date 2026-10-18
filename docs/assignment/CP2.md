In Checkpoint 1, your agent handled each request independently. In this checkpoint, you will extend it to support multi-turn interactions and work with CSV files.

You must continue building on the same agent framework you submitted for Checkpoint 1, retaining its capabilities and adding the requirements below. Do not start a separate framework for this checkpoint. All general Homework 1 rules continue to apply, including the from-scratch requirement, model and API restrictions, runtime environment, workspace rules, command and output requirements, submission contents, and late-submission policy. This checkpoint adds a session ID to the command-line interface and requires context to persist across calls in the same session; its scoring criteria are given below.

Late submissions

The course's two late-submission exceptions are shared across all submissions. After both exceptions are used, each late day for hw1-2 deducts 1 point (one-third of its 3-point maximum) from the score earned before the penalty. Each started 24-hour period after the deadline counts as one late day. Round the remaining score down to a whole point, with a minimum of 0: max(0, floor(earned score - late days)).

The same 1-point daily penalty applies to hw1-1. For hw1-3, the daily deduction is 2 points, with a score of 0 after seven late days; see the Homework 1 late-submission policy for all three submissions and examples.

Required capabilities

1. Maintain multi-turn sessions
Your agent must maintain conversation history across multiple requests in the same session. Each request will include a numeric session ID. Requests with the same session ID belong to the same conversation, while requests with different session IDs belong to separate conversations.

Your agent must use relevant information from earlier turns when responding to later turns in the same session. A later request may refer to a file, value, result, or instruction introduced previously without repeating it.

Example:

Session 1001, Turn 1:

Read sales.csv and tell me the total of the revenue column.
Session 1001, Turn 2:

What is the average?
Session 1001, Turn 3:

Save a copy of the table as reports/sales_copy.csv.
The agent should understand the later requests using the context established earlier in session 1001.

Conversation histories from different session IDs must remain separate. The conversations used for this checkpoint will sit comfortably within the model’s context window.

You decide how to store and retrieve session history within the Homework 1 rules. We do not require a particular storage format or implementation. Grading is based on observable behavior: whether your agent uses relevant context from the same session and keeps different sessions separate.

2. Work with CSV files
Your agent must be able to understand and complete natural-language requests involving CSV files. These requests may require the agent to read, analyze, calculate from, or modify CSV data and save the requested result.

Grading is limited to the CSV operations illustrated in this assignment:

read a CSV file and answer questions about its values;
calculate the total or average of a numeric column, or add specified numeric values in a selected row;
update a specified value in a row identified by an ID and save the result;
remove rows that match a specified condition and save the result; and
save a copy of a CSV table to a requested path.
Prompts may refer to earlier turns in the same session. Their wording, filenames, column names, and values may vary. Excel programming, macros, and Excel workbook manipulation are outside the scope of this checkpoint. Your agent must preserve unrelated data and files, follow any output requirements in the prompt, and produce a valid CSV file when a file is requested.

Example requests:

Read inventory.csv and tell me the total of the quantity column.
In records.csv, change the status of the row with ID 104 to complete, and save the result as updated_records.csv.
In orders.csv, remove every row whose status is cancelled, and save the result as active_orders.csv.
Read candidates.csv. For the row whose id is 1, add the values in the interview_score and test_score columns. What is the result?
Session interface

The grader will pass the complete user prompt as the first command-line argument and the numeric session ID as the second command-line argument:

bash "$SUBMISSION_DIR/run_agent.sh" "$PROMPT" "$SESSION_ID"
The grader may invoke run_agent.sh multiple times during one conversation. Calls with the same session ID must continue the same conversation even though each call starts a new process.

All other command and output requirements from Checkpoint 1 still apply to each call, including the 180-second time limit, exit codes, and exactly one JSON object on standard output with the string fields status and message. Put requested answers, including numeric results, in message. When a task requests an output file, the grader checks that file's contents.

Example: run a three-turn conversation
Run the following in Bash on Linux, macOS, or WSL after installing your agent's dependencies. Start in your submission folder, where run_agent.sh is located. As in Checkpoint 1, use a separate task workspace and provide the model configuration through environment variables. Replace YOUR_KEY with your development API key locally; do not include it in your submission.

submission_dir="$(pwd)"
task_dir="$(mktemp -d)"
cd "$task_dir"

export OPENAI_BASE_URL="https://llm.montii.me/v1"
export MODEL="Qwen/Qwen3-14B"
export QWEN_API_KEY="YOUR_KEY"

cat > sales.csv <<'EOF'
month,revenue
January,120
February,180
EOF

# Turn 1: establish the file and column. Expected total: 300.
bash "$submission_dir/run_agent.sh" \
  "Read sales.csv and tell me the total of the revenue column." "1001"

# Turn 2: reuse the context from Turn 1. Expected average: 150.
bash "$submission_dir/run_agent.sh" \
  "What is the average?" "1001"

# Turn 3: reuse the file context and save a copy of the original table.
bash "$submission_dir/run_agent.sh" \
  "Save a copy of the table as reports/sales_copy.csv." "1001"

# Inspect the saved copy.
cat reports/sales_copy.csv
Each call starts a new process, so keeping history only in a running process's memory is insufficient. The second call should answer 150 using the file and column established in the first call. The third call should create reports/sales_copy.csv with the same header and two data rows as sales.csv, creating the reports directory if needed and leaving sales.csv unchanged. Also test with different session IDs to verify that their conversation histories remain separate.

Submission contents

Submit Checkpoint 2 through our course Slack platform using the command submit hw1-2. The deadline is Monday, October 12, 2026, at 11:59pm ET, as listed in the Homework 1 schedule.

Submit the complete updated framework in one archive or repository, following the same submission requirements as Checkpoint 1: run_agent.sh at the submission root, all source code, dependency files, and a brief readme.md describing the folder structure, tested OS and runtime versions, and setup instructions. Include your own test inputs and expected results. Do not submit API keys, model weights, generated caches, virtual environments, or temporary test workspaces.

Testing and grading

Grading procedure for all Homework 1 submissions: We will run your agentic framework in our own sandbox environment with specified tasks to test its functionality. We will first use an autograder, a program that generates test inputs and checks whether your agent behaves correctly. If your submission cannot run directly in our environment because of configuration or other technical issues, we will use GPT-6 Astra to attempt deployment-related fixes only; we will not fix logic errors in your submission. If those fixes make your submission runnable, it will be graded normally under the checkpoint's scoring criteria. If it still cannot run, you will receive a grade of 0 for that submission.

Checkpoint 2 is worth 3 points, divided equally among three test categories. The grader will use hidden prompts, multi-turn conversations, and CSV files.

TEST CATEGORY	WHAT IS EVALUATED	POINTS
Context within a session	Later turns correctly use relevant files, values, results, or instructions from earlier turns with the same session ID, across separate process invocations.	1
Session isolation	Requests use only their own session's conversation history; context from another session does not leak into the response or actions.	1
CSV operations	The agent correctly performs the CSV operations listed above and produces the requested answers and output files.	1
As in Checkpoint 1, a category passes only if all of its test cases pass. Each passing category earns 1 point; a failed category earns 0 points, with no partial credit within a category. Passing all three categories earns 3 points.

Each test case is pass/fail. A multi-turn case passes only when all required turns succeed. The grader checks the requested behavior, exit codes, JSON output, answers and output files, and preservation of unrelated data and files. Session tests require context to persist within a session, replacing Checkpoint 1's independent-conversation setup.

The examples above illustrate the kinds of interactions your agent may receive. The grading prompts, files, values, and session IDs will differ.