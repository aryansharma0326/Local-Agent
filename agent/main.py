"""Entry point: prompt [+ session id] -> router -> agent loop -> exactly one
JSON object on stdout.

Usage: run_agent.sh "<prompt>" [<session id>]
  * argv[1] is the complete prompt; argv[2] (optional) is the session id;
    any further arguments are ignored (and logged to stderr).
  * Without a session id the call is stateless (Checkpoint 1).

Output guarantees: stdout is redirected to stderr before anything else runs;
output.emit() writes the single JSON object once. Signals (TERM/INT/HUP) and
a watchdog thread at the hard deadline also emit a result, so the grader
always gets valid JSON.
"""

import os
import signal
import sys
import threading
import traceback

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.realpath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import output  # noqa: E402

output.install()

import config  # noqa: E402
from config import log  # noqa: E402
from errors import AgentError  # noqa: E402

# What to report if the run is cut short (signal or watchdog). Long-horizon
# loops update this once a deliverable exists and passes basic validation.
FALLBACK = {"status": "error", "message": "The agent was stopped before it finished the task."}


def set_fallback(status, message):
    FALLBACK["status"], FALLBACK["message"] = status, message


def _final_hooks():
    for hook in list(_HOOKS):
        try:
            hook()
        except Exception:
            pass


_HOOKS = []


def safe_text(text):
    return output.clean_text(text)


def parse_args(args):
    """Return (prompt, session_id or None, ignored extras)."""
    if not args:
        return "", None, []
    prompt = safe_text(args[0])
    sid = None
    if len(args) >= 2 and args[1] is not None and safe_text(args[1]).strip():
        sid = safe_text(args[1]).strip()
    return prompt, sid, list(args[2:])


def _abort(why):
    """Signal/watchdog path: emit the fallback result and exit immediately."""
    if output.emitted():
        os._exit(0 if FALLBACK["status"] == "success" else 1)
    log("aborting: %s" % why)
    _final_hooks()
    status = FALLBACK["status"] if why == "watchdog" else "error"
    message = FALLBACK["message"] if status == "success" else (
        "%s (%s)" % (FALLBACK["message"] if FALLBACK["status"] == "error" else
                     "The agent was stopped before it finished the task.", why))
    code = output.emit(status, message)
    os._exit(code)


def _install_signals():
    def handler(signum, frame):
        name = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}.get(signum, "signal %d" % signum)
        _abort("received %s" % name)
    for s in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
        if s is not None:
            try:
                signal.signal(s, handler)
            except (ValueError, OSError):
                pass


def _start_watchdog():
    def watch():
        import time
        while True:
            left = config.remaining() + config.WATCHDOG_SLACK
            if left <= 0:
                _abort("watchdog")
                return
            time.sleep(min(1.0, max(0.05, left)))
    t = threading.Thread(target=watch, name="watchdog", daemon=True)
    t.start()


def run(prompt, session_id=None):
    """Run one turn. Returns (status, message)."""
    import router
    from sandbox import Sandbox
    sandbox = Sandbox()
    kind = router.classify(prompt, sandbox.root)
    config.set_task_kind(kind)
    log("task kind: %s; deadline %.0fs; session: %s" % (kind, config.BUDGET, session_id or "(none)"))

    sess = None
    history, context = [], {}
    if session_id:
        import session
        sess = session.Session(sandbox.root, session_id)
        history, context = sess.turns, sess.context
        log("session %s: %d earlier turn(s)" % (session_id, len(history)))

    from llm import LLM
    llm = LLM()
    actions = []
    try:
        if kind in ("programming", "debugging"):
            import deep
            agent = deep.DeepAgent(prompt, llm, sandbox, kind, history, context, set_fallback)
        else:
            from harness import Harness
            agent = Harness(prompt, llm, sandbox, history, context)
        _HOOKS.append(getattr(agent, "on_abort", lambda: None))
        try:
            status, message = agent.run()
        except AgentError as e:
            status, message = "error", str(e) or "The request could not be completed."
        actions = getattr(agent, "actions", [])
    finally:
        log("calls_used=%d (total attempts %d)" % (llm.calls_used, llm.attempts))
    if sess is not None:
        import session
        turn = session.make_turn(prompt, actions, status, safe_text(message))
        sess.append(turn, session.turn_context(prompt, actions, status))
    return status, message


def main():
    _install_signals()
    _start_watchdog()
    status, message = "error", "The request could not be completed."
    try:
        prompt, session_id, extras = parse_args(sys.argv[1:])
        if extras:
            log("ignoring %d extra argument(s)" % len(extras))
        if not prompt.strip():
            raise AgentError("No prompt was provided. Usage: run_agent.sh \"<request>\" [session_id]")
        status, message = run(prompt.strip(), session_id)
    except AgentError as e:
        status, message = "error", str(e) or "The request could not be completed."
    except PermissionError as e:
        status, message = "error", "Permission denied: %s" % (e.filename or e)
    except OSError as e:
        status, message = "error", "Filesystem error: %s" % (e.strerror or type(e).__name__)
    except KeyboardInterrupt:
        status, message = "error", "Interrupted."
    except BaseException as e:  # never leak a traceback to stdout
        traceback.print_exc(file=sys.stderr)
        status, message = "error", "Internal error: %s: %s" % (type(e).__name__, str(e)[:200])
    log("result: %s" % status)
    return output.emit(status, message)


if __name__ == "__main__":
    sys.exit(main())
