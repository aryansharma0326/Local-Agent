"""Run-wide configuration: environment variables, deadlines and call caps.

Everything time-related is measured from process start (START), so every
module sees the same clock. Values are read once at import; the router later
calls set_task_kind() to pick the deadline for the routed task type.
"""

import os
import sys
import time

START = time.monotonic()
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

DEFAULT_BASE_URL = "https://llm.montii.me/v1"
DEFAULT_MODEL = "Qwen/Qwen3-14B"
USER_AGENT = "hw1-agent/1.0"

CONTEXT_TOKENS = 40960      # Qwen3-14B on the course endpoint: prompt + output
CONTEXT_SAFETY = 512

# Model-call caps: the grader allows 120 successful completions per run.
HARD_CALL_LIMIT = 120
CALL_CAP = 110              # internal hard stop; wrap-up starts before this
WRAPUP_CALLS_LEFT = 10

# Internal deadlines (seconds from start). The grader's limits are 180 s
# (CP1/CP2) and 300 s (CP3).
BUDGET_SIMPLE = 165.0
BUDGET_DEEP = 285.0
# The watchdog fires this long after the internal deadline, still inside the
# grader's limit, if the main thread is stuck in an uninterruptible call.
WATCHDOG_SLACK = 8.0


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _float_env(name):
    try:
        v = float(_env(name))
        return v if v > 0 else None
    except ValueError:
        return None


def _int_env(name):
    try:
        v = int(_env(name))
        return v if v > 0 else None
    except ValueError:
        return None


def base_url():
    return _env("OPENAI_BASE_URL", DEFAULT_BASE_URL)


def model():
    return _env("MODEL", DEFAULT_MODEL)


def api_key():
    return _env("QWEN_API_KEY") or _env("OPENAI_API_KEY")


BASE_URL = base_url()
MODEL = model()
API_KEY = api_key()
TEMPERATURE = _float_env("AGENT_TEMPERATURE")
NO_THINK_SUFFIX = _env("AGENT_NO_THINK_SUFFIX") == "1"
STREAM = _env("AGENT_STREAM") == "1"
LOG_FILE = _env("AGENT_LOG_FILE")
SESSION_SCOPE = _env("AGENT_SESSION_SCOPE", "id+workspace").lower()
CALL_TIMEOUT_CAP = _float_env("AGENT_CALL_TIMEOUT") or 120.0
_cap = _int_env("AGENT_MAX_CALLS")
if _cap:
    CALL_CAP = min(CALL_CAP, _cap)

_budget_override = _float_env("AGENT_TIME_BUDGET")
TASK_KIND = "simple"
BUDGET = _budget_override or BUDGET_SIMPLE


def set_task_kind(kind):
    """Pick the deadline for the routed task type (unless overridden)."""
    global TASK_KIND, BUDGET
    TASK_KIND = kind
    if _budget_override:
        BUDGET = _budget_override
    else:
        BUDGET = BUDGET_DEEP if kind in ("programming", "debugging") else BUDGET_SIMPLE


def elapsed():
    return time.monotonic() - START


def remaining():
    return BUDGET - elapsed()


def is_local_url(url):
    """True for a local development backend (Ollama/vLLM on this machine)."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return (host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")
            or host.endswith(".localhost"))


_log_fh = None


def log(*args):
    """Diagnostics go to stderr (never stdout), and to AGENT_LOG_FILE if set."""
    global _log_fh
    line = "[agent %6.1fs] %s" % (elapsed(), " ".join(str(a) for a in args))
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    if LOG_FILE:
        try:
            if _log_fh is None:
                _log_fh = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            _log_fh.write(line + "\n")
            _log_fh.flush()
        except Exception:
            pass
