"""Entry point: prompt [+ session id] -> agent harness (Qwen3-14B tool-calling
loop over sandboxed tools) -> exactly one JSON object on stdout.

Usage: run_agent.sh "<prompt>" [<numeric session id>]
With a session id, earlier turns of that session are loaded from disk and
this turn is appended afterwards, so the conversation continues across
separate processes. Without one, the call is stateless (Checkpoint 1)."""

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from errors import AgentError  # noqa: E402
from llm import LLM, log  # noqa: E402
from harness import Harness  # noqa: E402
from sandbox import Sandbox  # noqa: E402
import session  # noqa: E402


def safe_text(text):
    """Replace undecodable bytes / lone surrogates (e.g. from a non-UTF-8
    argv or file name) so the text can always be serialized as UTF-8 JSON."""
    try:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeError:
        return text.encode("utf-8", "replace").decode("utf-8")


def parse_args(args):
    """Return (prompt, session_id or None). The grader passes exactly two
    arguments: the prompt and a numeric session id. Any other shape is
    treated as a prompt only (an unquoted prompt is re-joined)."""
    if len(args) == 2 and session.valid_session_id(args[1]):
        return safe_text(args[0]).strip(), args[1].strip()
    return safe_text(" ".join(args)).strip(), None


def run(prompt, session_id=None):
    """Run one turn; returns the success message or raises AgentError."""
    llm = LLM()
    sandbox = Sandbox()
    history = session.load(sandbox.root, session_id) if session_id else []
    if session_id:
        log("session %s: %d earlier turn(s)" % (session_id, len(history)))
    harness = Harness(prompt, llm, sandbox, history)
    try:
        status, message = harness.run()
    except AgentError as e:
        status, message = "error", str(e) or "The request could not be completed."
    if session_id:  # remember this turn, whatever its outcome
        turn = session.make_turn(prompt, harness.actions, status, safe_text(str(message)))
        if not session.save(sandbox.root, session_id, history + [turn]):
            log("WARNING: could not save session history")
    if status != "success":
        raise AgentError(message)
    return message


def main():
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # nothing but the final JSON may reach stdout
    status, code = "error", 1
    try:
        prompt, session_id = parse_args(sys.argv[1:])
        if not prompt:
            raise AgentError("No prompt was provided. Usage: run_agent.sh \"<request>\" [session_id]")
        message = run(prompt, session_id)
        status, code = "success", 0
    except AgentError as e:
        message = str(e) or "The request could not be completed."
    except PermissionError as e:
        message = "Permission denied: %s" % (e.filename or e)
    except OSError as e:
        message = "Filesystem error: %s" % (e.strerror or type(e).__name__)
    except KeyboardInterrupt:
        message = "Interrupted."
    except BaseException as e:  # never leak a traceback to stdout
        traceback.print_exc(file=sys.stderr)
        message = "Internal error: %s" % (type(e).__name__,)
    log("result: %s" % status)
    sys.stdout = real_stdout
    out = json.dumps({"status": status, "message": safe_text(str(message))}, ensure_ascii=False)
    try:
        real_stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    real_stdout.write(out + "\n")
    real_stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
