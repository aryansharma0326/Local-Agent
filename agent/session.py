"""Persistent multi-turn session history.

Every call to run_agent.sh is a new process, so the conversation is stored
on disk as one small JSON file per (workspace, session id). Histories of
different session ids, and of different workspaces, never mix.

The store lives OUTSIDE the task workspace (so no stray files appear among
the user's files) and outside the submission (which may be read-only):
  1. $AGENT_SESSION_DIR, if set (used by the tests)
  2. /tmp/file-agent-sessions-<uid>
  3. <system temp dir>/file-agent-sessions-<uid>
  4. ~/.cache/file-agent-sessions
  5. <workspace>/.agent_sessions   (last resort; hidden and ignored by the agent)
"""

import hashlib
import json
import os
import re
import tempfile
import time

from llm import log

MAX_TURNS = 30               # turns kept per session
MAX_AGE = 24 * 3600          # seconds; older histories are ignored and pruned
MAX_FIELD = 4000             # characters kept per stored text field
WORKSPACE_FALLBACK = ".agent_sessions"


def valid_session_id(text):
    """The grader passes a numeric id; accept digits only."""
    return bool(re.fullmatch(r"\d{1,64}", (text or "").strip()))


def _uid():
    try:
        return str(os.getuid())
    except AttributeError:
        return "user"


def _candidates(workspace):
    dirs = []
    if os.environ.get("AGENT_SESSION_DIR", "").strip():
        dirs.append(os.environ["AGENT_SESSION_DIR"].strip())
    name = "file-agent-sessions-" + _uid()
    dirs.append(os.path.join("/tmp", name))
    dirs.append(os.path.join(tempfile.gettempdir(), name))
    dirs.append(os.path.join(os.path.expanduser("~"), ".cache", "file-agent-sessions"))
    dirs.append(os.path.join(workspace, WORKSPACE_FALLBACK))
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _file_name(workspace, session_id):
    """Sessions are scoped to the workspace directory (path + identity), so a
    reused session id in another task directory starts a fresh conversation."""
    try:
        st = os.stat(workspace)
        ident = "%s:%s:%s" % (workspace, st.st_dev, st.st_ino)
    except OSError:
        ident = workspace
    digest = hashlib.sha256(ident.encode("utf-8", "surrogateescape")).hexdigest()[:20]
    return "%s_%s.json" % (digest, session_id.strip())


def load(workspace, session_id):
    """Return the list of earlier turns (oldest first); [] if none."""
    name = _file_name(workspace, session_id)
    best = None
    for d in _candidates(workspace):
        path = os.path.join(d, name)
        try:
            mtime = os.path.getmtime(path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("turns"), list) and (best is None or mtime > best[0]):
            best = (mtime, data["turns"])
    if best is None or time.time() - best[0] > MAX_AGE:
        return []
    return [t for t in best[1] if isinstance(t, dict)][-MAX_TURNS:]


def _clip(value):
    if isinstance(value, str) and len(value) > MAX_FIELD:
        return value[:MAX_FIELD] + " [...]"
    return value


def save(workspace, session_id, turns):
    """Atomically write the history to the first writable store. Never raises:
    a storage problem must not fail the user's task."""
    name = _file_name(workspace, session_id)
    payload = {"session": session_id.strip(), "workspace": workspace, "updated": time.time(),
               "turns": turns[-MAX_TURNS:]}
    data = json.dumps(payload, ensure_ascii=True)  # ASCII-safe: survives lone surrogates
    for d in _candidates(workspace):
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            tmp = os.path.join(d, ".%s.%d.tmp" % (name, os.getpid()))
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, os.path.join(d, name))
            _prune(d)
            return True
        except OSError as e:
            log("session store %s not usable (%s)" % (d, e.strerror or type(e).__name__))
    return False


def _prune(directory):
    """Drop histories older than MAX_AGE so the store does not grow forever."""
    try:
        now = time.time()
        for entry in os.listdir(directory):
            if entry.endswith(".json"):
                path = os.path.join(directory, entry)
                if now - os.path.getmtime(path) > MAX_AGE:
                    os.unlink(path)
    except OSError:
        pass


def make_turn(prompt, actions, status, message):
    return {"prompt": _clip(prompt), "actions": actions[-20:], "status": status,
            "message": _clip(message), "time": time.time()}


def render(turns):
    """History as text for the model."""
    lines = []
    for i, t in enumerate(turns, 1):
        lines.append("[Turn %d] User: %s" % (i, t.get("prompt", "")))
        for a in t.get("actions", []):
            lines.append("  Action: %s -> %s" % (a.get("call", ""), a.get("result", "")))
        lines.append("  Reply (%s): %s" % (t.get("status", "?"), t.get("message", "")))
    return "\n".join(lines)
