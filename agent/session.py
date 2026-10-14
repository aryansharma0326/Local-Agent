"""Persistent multi-turn sessions (Checkpoint 2).

Each run_agent.sh call is a new process, so a conversation lives on disk as
one JSON file per session id: session_<id>.json (digits as-is, any other id
hashed with sha1, so "007" and "7" are different sessions).

Store location (outside the workspace, first directory that is actually
writable):
  1. $AGENT_STATE_DIR   (or the older name $AGENT_SESSION_DIR)
  2. ${XDG_STATE_HOME:-$HOME/.local/state}/hw1_agent/sessions
  3. <submission>/.agent_state/sessions
  4. ${TMPDIR:-/tmp}/hw1_agent_<uid>/sessions  (and /tmp/hw1_agent_<uid>/sessions)
If none is writable the run is stateless (a warning goes to stderr).

Scope (AGENT_SESSION_SCOPE): "id+workspace" (default) stores the workspace
path in the record; a later call with the same id from a different workspace
starts a fresh conversation and the old file is renamed *.stale-<ts>. "id"
shares history across workspaces.

Integrity: fcntl.flock on session_<id>.lock, read-modify-write under the lock
(so concurrent turns both land), atomic temp-file + os.replace, schema
version, a corrupt file is moved to *.corrupt-<ts> and the session restarts.
"""

import hashlib
import json
import os
import re
import tempfile
import time

import config
from config import log

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

SCHEMA = 2
MAX_TURNS = 40          # turns kept on disk per session
RECENT_TURNS = 8        # turns shown verbatim to the model
MAX_FIELD = 4000        # characters kept per stored text field
PRUNE_AGE = 7 * 24 * 3600

_STANDING = re.compile(
    r"\b(from now on|for the rest of|going forward|in (?:all )?future|for all future|always|never|"
    r"remember(?: that)?|keep in mind|note that|use the \S+ column|reply (?:only )?(?:with|in)|only reply|"
    r"respond (?:only )?(?:with|in)|answer (?:only )?(?:with|in)|from here on)\b", re.I)


# ------------------------------------------------------------------ ids/paths
def valid_session_id(text):
    """Any non-empty id is accepted (the grader passes a numeric one)."""
    return isinstance(text, str) and bool(text.strip())


def session_key(session_id):
    sid = session_id.strip()
    if re.fullmatch(r"\d{1,64}", sid):
        return sid
    return "h" + hashlib.sha1(sid.encode("utf-8", "surrogateescape")).hexdigest()


def _uid():
    try:
        return str(os.getuid())
    except AttributeError:
        return "user"


def _candidates():
    dirs = []
    for var in ("AGENT_STATE_DIR", "AGENT_SESSION_DIR"):
        v = (os.environ.get(var) or "").strip()
        if v:
            dirs.append(v)
    home = (os.environ.get("HOME") or "").strip()
    xdg = (os.environ.get("XDG_STATE_HOME") or "").strip()
    if xdg:
        dirs.append(os.path.join(xdg, "hw1_agent", "sessions"))
    elif home:
        dirs.append(os.path.join(home, ".local", "state", "hw1_agent", "sessions"))
    dirs.append(os.path.join(config.SCRIPT_DIR, ".agent_state", "sessions"))
    tmp = (os.environ.get("TMPDIR") or "").strip()
    name = "hw1_agent_" + _uid()
    if tmp:
        dirs.append(os.path.join(tmp, name, "sessions"))
    dirs.append(os.path.join("/tmp", name, "sessions"))
    try:
        dirs.append(os.path.join(tempfile.gettempdir(), name, "sessions"))
    except Exception:
        pass
    out = []
    for d in dirs:
        d = os.path.abspath(os.path.expanduser(d))
        if d not in out:
            out.append(d)
    return out


def _usable(d, workspace):
    """Writable, and not inside the task workspace."""
    try:
        real = os.path.realpath(d)
        ws = os.path.realpath(workspace)
        if real == ws or real.startswith(ws.rstrip(os.sep) + os.sep):
            return False
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".probe-", dir=d)
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


def store_dir(workspace):
    for d in _candidates():
        if _usable(d, workspace):
            return d
    return None


# --------------------------------------------------------------------- records
def _clip(value, n=MAX_FIELD):
    if isinstance(value, str) and len(value) > n:
        return value[:n] + " [...]"
    return value


def _fresh(workspace, key):
    return {"schema": SCHEMA, "session": key, "workspace": workspace, "created": time.time(),
            "updated": time.time(), "turns": [], "context": {}}


def _read_record(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("turns"), list):
        raise ValueError("not a session record")
    if not isinstance(data.get("context"), dict):
        data["context"] = {}
    data["turns"] = [t for t in data["turns"] if isinstance(t, dict)]
    return data


def _set_aside(path, tag):
    try:
        os.replace(path, "%s.%s-%d" % (path, tag, int(time.time() * 1000)))
    except OSError:
        pass


def _load_unlocked(path, workspace, key):
    if not os.path.exists(path):
        return _fresh(workspace, key)
    try:
        rec = _read_record(path)
    except (OSError, ValueError) as e:
        log("session file %s is corrupt (%s); starting fresh" % (os.path.basename(path), e))
        _set_aside(path, "corrupt")
        return _fresh(workspace, key)
    scope = (os.environ.get("AGENT_SESSION_SCOPE") or config.SESSION_SCOPE or "id+workspace").strip().lower()
    if scope != "id" and rec.get("workspace") != workspace:
        log("session %s belongs to another workspace; starting a new conversation" % key)
        _set_aside(path, "stale")
        return _fresh(workspace, key)
    return rec


class _Lock:
    def __init__(self, path):
        self.path = path
        self.fh = None

    def __enter__(self):
        try:
            self.fh = open(self.path, "a")
            if fcntl is not None:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            self.fh = None
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
                self.fh.close()
            except OSError:
                pass
        return False


def _paths(d, key):
    return os.path.join(d, "session_%s.json" % key), os.path.join(d, "session_%s.lock" % key)


class Session:
    """A loaded conversation. `turns` are oldest first."""

    def __init__(self, workspace, session_id):
        self.workspace = os.path.realpath(workspace)
        self.id = session_id
        self.key = session_key(session_id)
        self.dir = store_dir(self.workspace)
        self.turns = []
        self.context = {}
        if self.dir is None:
            log("WARNING: no writable session store; this session runs statelessly")
            return
        path, lock = _paths(self.dir, self.key)
        with _Lock(lock):
            rec = _load_unlocked(path, self.workspace, self.key)
        self.turns = rec["turns"][-MAX_TURNS:]
        self.context = rec.get("context") or {}

    def append(self, turn, context):
        """Add this turn under the lock (re-reading first, so concurrent turns of
        the same session are never lost). Never raises."""
        if self.dir is None:
            return False
        path, lock = _paths(self.dir, self.key)
        try:
            with _Lock(lock):
                rec = _load_unlocked(path, self.workspace, self.key)
                rec["turns"] = (rec["turns"] + [turn])[-MAX_TURNS:]
                rec["context"] = merge_context(rec.get("context") or {}, context)
                rec["updated"] = time.time()
                rec["schema"] = SCHEMA
                data = json.dumps(rec, ensure_ascii=True)
                fd, tmp = tempfile.mkstemp(prefix=".session-", dir=self.dir)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(data)
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, path)
                except BaseException:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            _prune(self.dir)
            return True
        except OSError as e:
            log("WARNING: could not save session history (%s)" % (e.strerror or type(e).__name__))
            return False


def _prune(directory):
    try:
        now = time.time()
        for entry in os.listdir(directory):
            if entry.startswith("session_"):
                p = os.path.join(directory, entry)
                if now - os.path.getmtime(p) > PRUNE_AGE:
                    os.unlink(p)
    except OSError:
        pass


# -------------------------------------------------------------- turn content
def make_turn(prompt, actions, status, message):
    slim = []
    for a in actions[-20:]:
        slim.append({k: (_clip(v, 600) if isinstance(v, str) else v) for k, v in a.items()
                     if k in ("call", "result", "tool")})
    return {"time": time.time(), "prompt": _clip(prompt), "actions": slim, "status": status,
            "message": _clip(message)}


def turn_context(prompt, actions, status):
    """Structured working context distilled from one turn's actions."""
    ctx = {"last_files": [], "outputs_created": [], "standing_instructions": []}
    for a in actions:
        if str(a.get("result", "")).startswith("ERROR"):
            continue
        tool, args = a.get("tool"), a.get("args") or {}
        paths = [args[k] for k in ("path", "source_path") if isinstance(args.get(k), str)]
        paths += [p for p in (args.get("paths") or []) if isinstance(p, str)]
        for p in paths:
            if p not in ctx["last_files"]:
                ctx["last_files"].append(p)
        out = args.get("output_path") or args.get("destination_path")
        if tool in ("write_file", "copy_file", "csv_update_cell", "csv_delete_rows"):
            out = out or args.get("path")
        if isinstance(out, str) and out not in ctx["outputs_created"]:
            ctx["outputs_created"].append(out)
        if tool and tool.startswith("csv_") and isinstance(args.get("path"), str):
            csv = {"path": args["path"], "operation": tool}
            for k in ("column", "operation", "columns", "id_value", "id_column"):
                if k in args:
                    csv["last_" + k if k != "operation" else "last_operation"] = args[k]
            if isinstance(out, str) and tool in ("csv_update_cell", "csv_delete_rows"):
                csv["result_file"] = out
            csv["last_result"] = _clip(str(a.get("result", "")), 300)
            ctx["last_csv"] = csv
    if _STANDING.search(prompt or ""):
        ctx["standing_instructions"].append(_clip(prompt.strip(), 300))
    return ctx


def merge_context(old, new):
    out = dict(old or {})
    new = new or {}
    files = [f for f in (new.get("last_files") or [])] + [f for f in (old.get("last_files") or [])
                                                        if f not in (new.get("last_files") or [])]
    out["last_files"] = files[:10]
    outs = list(old.get("outputs_created") or [])
    for f in new.get("outputs_created") or []:
        if f not in outs:
            outs.append(f)
    out["outputs_created"] = outs[-20:]
    if new.get("last_csv"):
        out["last_csv"] = new["last_csv"]
    si = list(old.get("standing_instructions") or [])
    for s in new.get("standing_instructions") or []:
        if s not in si:
            si.append(s)
    out["standing_instructions"] = si[-6:]
    return out


def render(turns, context=None):
    """History as text for the model: older turns compacted, the most recent
    RECENT_TURNS verbatim, then the structured working context."""
    lines = []
    n = len(turns)
    for i, t in enumerate(turns, 1):
        if i <= n - RECENT_TURNS:
            lines.append("[Turn %d] User: %s -> (%s) %s" % (
                i, _clip(t.get("prompt", ""), 200), t.get("status", "?"), _clip(t.get("message", ""), 200)))
            continue
        lines.append("[Turn %d] User: %s" % (i, t.get("prompt", "")))
        for a in t.get("actions", []):
            lines.append("  Action: %s -> %s" % (a.get("call", ""), a.get("result", "")))
        lines.append("  Reply (%s): %s" % (t.get("status", "?"), t.get("message", "")))
    ctx = context or {}
    extra = []
    if ctx.get("last_files"):
        extra.append("Most recently used files (newest first): %s" % ", ".join(ctx["last_files"][:5]))
    if ctx.get("last_csv"):
        c = ctx["last_csv"]
        bits = ["file %s" % c.get("path")]
        if c.get("last_column"):
            bits.append("column %s" % c["last_column"])
        if c.get("last_columns"):
            bits.append("columns %s" % ", ".join(map(str, c["last_columns"])))
        if c.get("result_file"):
            bits.append("result saved as %s" % c["result_file"])
        extra.append("Last CSV operation: %s (%s)" % (c.get("last_operation", c.get("operation")), "; ".join(bits)))
    if ctx.get("outputs_created"):
        extra.append("Files created in this session: %s" % ", ".join(ctx["outputs_created"][-8:]))
    if ctx.get("standing_instructions"):
        extra.append("Standing instructions from earlier turns (still apply):\n" +
                     "\n".join("  - %s" % s for s in ctx["standing_instructions"]))
    if extra and lines:
        lines.append("Working context:")
        lines.extend("  " + e if not e.startswith("Standing") else e for e in extra)
    return "\n".join(lines)


# ------------------------------------------------------- compatibility helpers
def load(workspace, session_id):
    """Earlier turns (oldest first) of this session in this workspace."""
    return Session(workspace, session_id).turns


def save(workspace, session_id, turns):
    """Replace-style save used by older tests: appends turns not yet stored."""
    s = Session(workspace, session_id)
    ok = True
    for t in turns[len(s.turns):] if len(turns) >= len(s.turns) else turns:
        ok = s.append(t, {}) and ok
    return ok
