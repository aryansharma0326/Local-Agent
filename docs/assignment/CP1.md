In this homework, you are expected to build an AI agent from scratch, using a small LLM as its core "brain." Your job is to build the harness that lets it operate your computer and handle digital tasks for you. You will build your agent gradually across multiple checkpoints, expanding its capabilities from simple tasks to more complex, long-term behavior. Along the way, you will put what you have learned about AI into practice by building a real agent.

This homework has three checkpoints of increasing difficulty, which we will reveal gradually, week by week. You are expected to submit a version of your agent at each checkpoint. Each submission is graded separately, so if you miss a checkpoint submission, you will lose the points for that checkpoint. Your final submission will also be tested on the requirements from the earlier checkpoints.

Here is how each submission is graded (this assignment is worth 20 pts in total). Submission deadlines follow the course schedule:

Checkpoint 1: 3 pts; submission due: October 4, 2026, 11:59pm ET.
Checkpoint 2: 3 pts; submission due: Monday, October 12, 2026, 11:59pm ET.
Final submission: 14 pts; submission due: October 25, 2026, 11:59pm ET.
Note: The final submission's 14 points include 6 points for the capabilities from Checkpoints 1 and 2 and 8 points for new capabilities. We will rerun the earlier checkpoint tests on your final submission, so keeping those capabilities working can earn you those 6 points again. These are part of the 14 points, not bonus points.

Late submissions

Each student has two late-submission exceptions in total for the entire course, shared across all submissions, including learning notes, homework checkpoints, and project submissions. A late submission covered by an exception is graded normally, without a late penalty.

After both exceptions are used, each late day deducts 1 point for hw1-1 or hw1-2, and 2 points for hw1-3, from the score earned before the late penalty. Round the remaining score down to the nearest whole point, with a minimum of 0. Each started 24-hour period after the posted deadline counts as one late day. HW1-3 submissions that are seven or more late days overdue receive 0 points under this penalty.

Final score = max(0, floor(earned score - late days × daily deduction))

SUBMISSION	MAXIMUM POINTS	DEDUCTION PER LATE DAY	FULL-CREDIT WORK AFTER 1 / 2 / 3 LATE DAYS
HW1-1: Checkpoint 1	3	1 point	2 / 1 / 0
HW1-2: Checkpoint 2	3	1 point	2 / 1 / 0
HW1-3: Final submission	14	2 points	12 / 10 / 8
Round down only after subtracting the total penalty. For example, a final submission that earns 12 of 14 points before the penalty and is one day late receives floor(12 - 2) = 10 points. Full-credit HW1-3 work receives 0 points after seven late days.

Each homework checkpoint is a separate submission and uses the same course-wide allowance as learning notes and all other submissions. An exception removes the late penalty; it does not waive the assignment requirements or earn credit for missing work.

Overview

In the first checkpoint, you will build a small AI agent that understands natural-language requests and works with files in a provided workspace. Grading is based on the agent's observable behavior.

"From scratch" means you must build the agent's own harness: the code that calls the model, carries out file operations, and returns the result. You may use AI coding tools to help write and debug your code. However, your submitted agent must not delegate its work to an existing agent platform such as Claude Code or use an agent SDK. At runtime, the only external API your agent may call is the Qwen3-14B model endpoint described below; no other external APIs are allowed. Ordinary libraries for HTTP requests, JSON, and local file operations are allowed.

We will all use Qwen3-14B for this assignment. You can access it through the course endpoint or use one of the development options below.

Setup instructions and API documentation are available at https://montii.me/llm. Your agent should use https://llm.montii.me/v1 as the OpenAI-compatible API base URL. The simplest way: You can ask Codex or Claude Code to help: "Set up this LLM endpoint (https://montii.me/llm) and test whether you can send a message and receive a response."

Note: This shared endpoint runs on SCC and may be unavailable or busy when many people use it at once. Avoid heavy use during regular development; use it mainly to verify that your agent works with the endpoint used for grading. If a grading run fails, we will check whether the cause is the server or your submission. If it is a server issue, we will rerun the test in a clean environment.

If you encountered issue or busy status when accessing the course endpoint, you can try these method instead:

host the official Qwen3-14B model yourself, with Ollama or vLLM; or
use Qwen3-14B on OpenRouter with your own account and API key.
Regardless of which option you use during development, grading uses the course endpoint and the model name Qwen/Qwen3-14B. Your submission must connect through the endpoint's OpenAI-compatible /chat/completions API.

Note: if any of the terminology or method or platform you are not familiar with or don't know how to use, you should ask AI for step-by-step guide and configure it. This is also a part of the learning process.

Checkpoint 1: Build a File-Management AI Agent
Required capabilities

Your agent must complete all four task types below.

1. Create a file
Create a file at the requested path with exactly the requested content. Create missing parent directories when the request requires it.

Example prompts:

Create foo.txt inside the abc directory. The file should contain: foo
In folder1, make a file named bar.txt containing the single line hello world.
Write "Meeting moved to Friday at 3 PM." to announcements/update.txt.
Make an empty file named ready.flag in the current directory.
I want you to create data/colors.txt with these three lines in this order: red, green, blue.
2. Delete a file
Delete the requested file without deleting unrelated files or directories.

Example prompts:

Delete notes/old-draft.txt.
Remove temporary/cache.txt.
I no longer need todo-old.md. Delete it.
Delete archive/2024/january.log, but leave the other log files unchanged.
Please get rid of the file named duplicate.csv in the records directory.
3. Read a file and answer a question
Read the requested file and answer a specific question using information from that file. Return the answer in the message field of your JSON output. Do not modify the file.

Example prompts:

According to schedule.txt, what date is the final presentation?
Read inventory.txt. How many blue notebooks are currently in stock?
Based on policy.txt, who is eligible for reimbursement and what is the submission deadline?
Read experiment.md and report the test accuracy and the size of the test set.
According to meeting_notes.txt, who is responsible for the deployment and when is it due?
4. Summarize multiple files into a new file
Read every source file named in the prompt, combine the relevant information, and save one concise summary to the requested .txt file. Create the output file's parent directory if it does not already exist.

The combined source files will not exceed 40,000 UTF-8 bytes—approximately 10,000 tokens or 6,000–8,000 English words. This fits comfortably within Qwen3-14B's native 32,768-token context window. The grader will inspect the output file, so the summary does not need to appear in message.

Example task:

Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create reports/incident_summary.txt containing a summary with less than 100 words of the incident timeline, its cause, and its resolution.
Required command-line interface

We will run submissions on Linux or macOS. If you develop on Windows, test your submission in a Linux environment with Bash before submitting. We recommend Windows Subsystem for Linux (WSL) with Ubuntu. Run your agent and install its dependencies inside WSL; testing only in PowerShell or Command Prompt does not establish Linux compatibility. Save run_agent.sh with LF line endings and avoid Windows-only paths, executables, and commands.

Your submission must include a Bash entry point named run_agent.sh at the top level of the submission. Our autograder will invoke it once per test as follows:

cd "$TASK_DIR"
OPENAI_BASE_URL="https://llm.montii.me/v1" \
MODEL="Qwen/Qwen3-14B" \
QWEN_API_KEY="$GRADING_API_KEY" \
bash "$SUBMISSION_DIR/run_agent.sh" "$PROMPT"
This command illustrates the grading contract. The grader will supply the prompt, API key, and absolute paths for the task workspace and submission.

Your program must follow these rules:

Treat the first command-line argument as the complete user prompt.
Treat the current working directory as the task workspace. All requested file operations must remain inside that directory.
Read the model configuration from your environment variables, such as OPENAI_BASE_URL, MODEL, and QWEN_API_KEY. Do not hard-code credentials.
Resolve your program's source files relative to the location of run_agent.sh, not relative to the task workspace.
Finish within 180 seconds.
Exit with code 0 when the agent completes the task and a nonzero code when it cannot complete the task.
Use only Qwen3-14B as the submitted agent's LLM engine.
Required output format

Write exactly one JSON object to standard output. Do not print Markdown fences, progress messages, model responses, or debugging logs to standard output.

Successful run:

{"status":"success","message":"Created abc/foo.txt."}
Failed run:

{"status":"error","message":"Could not find schedule.txt."}
The output object must contain exactly these two string fields:

status: either success or error
message: the requested response for a read-and-answer task; any string for create, delete, and multi-file summary tasks
You may write diagnostic information to standard error. For create, delete, and multi-file summary tasks, the grader ignores message and determines success from the final workspace. Ensure that the JSON remains valid even when filenames or messages contain quotation marks or non-ASCII characters.

Submission contents

Submit Checkpoint 1 through our course Slack platform using the command submit hw1-1.

Submit one archive or repository containing:

run_agent.sh at the submission root;
all source code needed to run the agent;
any dependency file required to install and run your code;
a brief readme.md describing the folder structure, the OS and language/runtime version you tested with, and anything else we need to pay attention to.
We do not restrict the programming language you use, but you are responsible for making your submission run on Linux or macOS. Use a widely supported language and dependencies, and document their required versions.

Do not submit API keys, model weights, generated caches, virtual environments, or temporary workspaces left over from test runs. Include the test inputs and expected results needed to reproduce your own tests.

Walkthrough: build and check one local test

We provide example prompts, not test files or a ready-made test suite. You must create your own input files, expected answers, and tests. Here is one example for the read-and-answer capability. Run these commands in Bash on Linux, macOS, or WSL after installing your agent's dependencies.

Start in your submission folder, where run_agent.sh is located. Save its absolute path, then create a separate, fresh task workspace:
submission_dir="$(pwd)"
task_dir="$(mktemp -d)"
cd "$task_dir"
Create the input file yourself. Since you choose its contents, you know that the expected answer is 12 blue notebooks:
cat > inventory.txt <<'EOF'
Blue notebooks: 12
Red notebooks: 7
EOF
Run your agent with one request. Replace YOUR_KEY with your development API key locally; do not include it in your submission:
OPENAI_BASE_URL="https://llm.montii.me/v1" \
MODEL="Qwen/Qwen3-14B" \
QWEN_API_KEY="YOUR_KEY" \
bash "$submission_dir/run_agent.sh" \
  "Read inventory.txt. How many blue notebooks are currently in stock?"
agent_exit=$?
printf 'Exit code: %s\n' "$agent_exit"
The agent should read inventory.txt from the task workspace and return one JSON object. For example:

{"status":"success","message":"There are 12 blue notebooks in stock."}
Check that the agent exits with code 0, its standard output is exactly one valid JSON object with the required fields, and message answers the question correctly. The shell's Exit code: line is a diagnostic from this walkthrough, not part of the agent's output. Run cat inventory.txt to confirm that the file still contains the two original lines. A correct answer with a modified input file does not pass this test.
Repeat this process with your own prompts and files for the other capabilities. For create and delete tasks, inspect the resulting files; for a summary task, read the generated summary and compare it with the source facts and the requested format. Use a fresh workspace and a new agent process for each test.

Testing and grading

Grading procedure for all Homework 1 submissions: We will run your agentic framework in our own sandbox environment with specified tasks to test its functionality. We will first use an autograder, a program that generates test inputs and checks whether your agent behaves correctly. If your submission cannot run directly in our environment because of configuration or other technical issues, we will use GPT-6 Astra to attempt deployment-related fixes only; we will not fix logic errors in your submission. If those fixes make your submission runnable, it will be graded normally under the checkpoint's scoring criteria. If it still cannot run, you will receive a grade of 0 for that submission.

Checkpoint 1 evaluates four task types. Each task type is tested with multiple prompt cases—for example, five or six different file-creation requests. The grading prompts and files are hidden; the examples above illustrate the kinds of requests to expect. Wording, paths, and contents may differ from the examples.

A task type passes only if all of its individual prompt cases pass. If even one case fails, that task type is counted as failed; there is no partial credit for passing some of its cases.

Passing all four task types earns 3 points. Each failed task type reduces the score by 1 point, down to a minimum of 0:

TASK TYPES PASSED	CHECKPOINT 1 SCORE
4	3 pts
3	2 pts
2	1 pt
1 or 0	0 pts
Each individual prompt case contains one request and is run through a separate call to run_agent.sh. Cases run sequentially, each with a fresh workspace, a new agent process, and a separate conversation context. Even cases of the same task type do not share context; do not rely on files or conversation history from previous calls. For create, delete, and multi-file summary cases, the grader checks the process exit code, JSON structure, final filesystem state, and whether unrelated files were preserved. The text in message is not graded for these cases.

For read-and-answer tasks, the grader checks whether message contains the fact or facts requested in the prompt. The questions are designed to have clear answers that can be checked without requiring exact wording.

For multi-file summary tasks, the grader also checks that the requested output file exists and evaluates the summary stored in that file. The combined source files will not exceed 40,000 UTF-8 bytes. Handling inputs larger than the context window is not required for this checkpoint. Prompts will state useful constraints such as the desired length or the information that should be covered. A summary is evaluated on whether it:

includes the important facts requested in the prompt;
accurately reflects the source without adding unsupported claims;
follows the requested length and format; and
is concise and understandable.
For your own read-and-answer and summary tests, write down the expected facts when you create the source files, then compare your agent's output with those facts. The walkthrough above shows how to do this for one read-and-answer test.

After each checkpoint, you will receive a report identifying which tests your system failed and how it failed. We will use a combination of programmatic checks and AI evaluation to grade your submission, including the summary criteria above.

Each individual prompt case is pass/fail; there is no partial credit within a case. A case passes only when the requested task is completed correctly, the command and output requirements are met, and unrelated files remain unchanged. The scoring table above applies to the four task types, not to the number of individual prompt cases.