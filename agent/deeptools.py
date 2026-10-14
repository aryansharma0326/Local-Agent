"""Tools for the long-horizon CP3 loop (programming and debugging).

Every tool resolves paths through the workspace sandbox (the scratch dir is
the one allowed exception for shell/test files), returns structured,
truncated text, and never raises into the loop: failures become "ERROR: ..."
results. Each call is logged to stderr with its duration.
"""

import fnmatch
import json
import os
import re
import signal
import subprocess
import sys
import time

import profiling
from config import log
from errors import AgentError

OUTPUT_CHARS = 7000          # ~2.3k tokens per tool result (LOOP-9)
READ_LINES_DEFAULT = 200
MAX_TIMEOUT = 120

_BLOCKED = [
    (re.compile(r"(^|[\s;&|(`$])(curl|wget|ssh|scp|sftp|rsync|nc|ncat|netcat|telnet|ftp|socat)\b"),
     "network tools are not allowed"),
    (re.compile(r"(^|[\s;&|(`$])(sudo|su|doas)\b"), "privilege escalation is not allowed"),
    (re.compile(r"\b(pip3?|python3? -m pip|npm|yarn|pnpm|apt(-get)?|brew|conda|gem|cargo)\s+(install|add|i)\b"),
     "installing packages is not allowed (standard library only)"),
    (re.compile(r"\bgit\s+(clone|push|pull|fetch)\b"), "network git commands are not allowed"),
    (re.compile(r"\brm\s+(-\w*\s+)*(/|~|\$HOME|\.\.)(\s|/|$)"), "deleting outside the workspace is not allowed"),
    (re.compile(r"\b(mkfs|shutdown|reboot|halt|kill\s+-9\s+-1|:\(\)\s*\{)"), "destructive system commands are not allowed"),
    (re.compile(r"(urllib|requests|http\.client|socket)\b.*\b(urlopen|get|post|connect)\s*\("), "network access is not allowed"),
]


def truncate(text, limit=OUTPUT_CHARS, hint=""):
    """Keep head and tail with an accurate marker (LOOP-9)."""
    if len(text) <= limit:
        return text
    head, tail = text[:int(limit * 0.6)], text[-int(limit * 0.3):]
    dropped = text[len(head):len(text) - len(tail)]
    marker = "\n...[truncated %d characters / %d lines%s]...\n" % (
        len(dropped), dropped.count("\n"), ("; " + hint) if hint else "")
    return head + marker + tail


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def _reindent_match(text, old, new):
    """Find `old` in `text` ignoring indentation (a common slip when copying a
    snippet). If exactly one block matches line-by-line after stripping, return
    (exact_old_block, new_reindented) so the edit applies with the file's
    indentation; else None."""
    olines = [l for l in old.split("\n")]
    while olines and not olines[0].strip():
        olines.pop(0)
    while olines and not olines[-1].strip():
        olines.pop()
    if not olines:
        return None
    want = [l.strip() for l in olines]
    tlines = text.split("\n")
    hits = []
    for i in range(len(tlines) - len(want) + 1):
        if all(tlines[i + j].strip() == want[j] for j in range(len(want))):
            hits.append(i)
            if len(hits) > 1:
                return None
    if len(hits) != 1:
        return None
    i = hits[0]
    real_old = "\n".join(tlines[i:i + len(want)])
    have, had = _indent(tlines[i]), _indent(olines[0])
    out = []
    for l in new.split("\n"):
        if l.startswith(had):
            out.append(have + l[len(had):])
        elif l.strip():
            out.append(have + l.lstrip())
        else:
            out.append(l)
    return real_old, "\n".join(out)


def blocked_reason(cmd):
    for rx, why in _BLOCKED:
        if rx.search(cmd):
            return why
    return None


def run_process(cmd, cwd, timeout, env, shell=True):
    """Run a command in its own process group with stdin=/dev/null and
    captured output; on timeout the whole group is killed."""
    t0 = time.monotonic()
    args = ["/bin/bash", "-c", cmd] if shell else cmd
    try:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return None, "", "cannot start: %s" % e, 0.0, False
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    return (proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"),
            time.monotonic() - t0, timed_out)


def child_env(scratch):
    env = dict(os.environ)
    for k in list(env):
        if k in ("QWEN_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY") or k.startswith("AGENT_"):
            env.pop(k, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["SCRATCH"] = scratch
    env.pop("PYTHONPATH", None)
    return env


def _schema(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}


class DeepTools:
    def __init__(self, sandbox, scratch, kind, deliverables, protected, report=None):
        self.sb = sandbox
        self.root = sandbox.root
        self.scratch = os.path.realpath(scratch)
        self.kind = kind
        self.deliverables = list(deliverables)   # workspace-relative paths the task asks for
        self.protected = set(protected)          # pre-existing files that must not change
        self.report = report                     # debugging: Report object
        self.created = set()                     # workspace files written by tools this run
        self.notes = []
        self.on_write = None                     # callback(rel) after a deliverable changes
        self.env = child_env(self.scratch)

    # ------------------------------------------------------------- schemas
    def schemas(self):
        t = [
            _schema("list_dir", "List files under a directory (default: workspace root).",
                    {"path": S, "depth": I}, []),
            _schema("read_file", "Read a text file with line numbers (use start_line/num_lines for parts).",
                    {"path": S, "start_line": I, "num_lines": I}, ["path"]),
            _schema("grep", "Search files for a pattern. Literal by default; IDs match whole tokens only. "
                    "Returns file:line: text plus identifiers found in matching lines.",
                    {"pattern": S, "path": {"type": "string", "description": "file, directory or glob; default all"},
                     "regex": B, "ignore_case": B, "context": I, "max_matches": I}, ["pattern"]),
            _schema("notes", "Add a finding to your pinned notes (kept in context for the whole task).",
                    {"text": S}, ["text"]),
            _schema("run_python", "Run a short Python 3 script (saved in the scratch dir, cwd = workspace). "
                    "Use for counting/aggregating or quick experiments.",
                    {"code": S, "timeout": I}, ["code"]),
            _schema("finish", "Finish the task with a short summary message.", {"message": S}, ["message"]),
        ]
        if self.kind == "debugging":
            t.insert(2, _schema("read_lines", "Read lines start..start+count-1 of a file (1-based).",
                                {"path": S, "start": I, "count": I}, ["path", "start"]))
            t.insert(3, _schema("log_stats", "Line count, timestamp formats, levels and top error shapes of a log.",
                                {"path": S}, ["path"]))
            t.insert(4, _schema("report_set", "Set one or more keys of the report. The harness writes the file "
                                "and validates each value against the logs.",
                                {"values": {"type": "object", "description": "{key: value, ...}"}}, ["values"]))
        else:
            t.insert(2, _schema("write_file", "Create or overwrite a file with the complete content. Paths starting "
                                "with $SCRATCH/ go to the scratch dir.", {"path": S, "content": S},
                                ["path", "content"]))
            t.insert(3, _schema("append_file", "Append content to a file (for writing a long file in parts).",
                                {"path": S, "content": S}, ["path", "content"]))
            t.insert(4, _schema("edit_file", "Replace an exact snippet of a file with new text (old must match "
                                "exactly, including indentation).", {"path": S, "old": S, "new": S,
                                                                     "count": I}, ["path", "old", "new"]))
            t.insert(5, _schema("run_command", "Run a shell command (cwd = workspace; $SCRATCH = scratch dir). "
                                "Output is captured; network and installs are blocked.",
                                {"command": S, "timeout": I}, ["command"]))
        return t

    def names(self):
        return [s["function"]["name"] for s in self.schemas()]

    # ------------------------------------------------------------- paths
    def _resolve(self, raw, for_write=False):
        """-> (abs_path, display, in_scratch)."""
        if not isinstance(raw, str) or not raw.strip():
            raise AgentError("path must be a non-empty string")
        p = raw.strip().strip("`'\"")
        for pre in ("$SCRATCH/", "${SCRATCH}/", "SCRATCH/"):
            if p.startswith(pre):
                p = os.path.join(self.scratch, p[len(pre):])
                break
        if os.path.isabs(p):
            real = os.path.realpath(p)
            if real == self.scratch or real.startswith(self.scratch + os.sep):
                return real, real, True
            if real == self.root or real.startswith(self.root + os.sep):
                p = os.path.relpath(real, self.root)
            else:
                raise AgentError("path %s is outside the workspace" % raw)
        rel, full = self.sb.resolve(p)
        return full, rel, False

    def _files(self, raw):
        """Expand a file/dir/glob (default: whole workspace) to text files."""
        raw = (raw or ".").strip() or "."
        out = []
        if any(ch in raw for ch in "*?["):
            for dp, dns, fns in os.walk(self.root):
                dns[:] = sorted(d for d in dns if not d.startswith("."))
                for fn in sorted(fns):
                    rel = os.path.relpath(os.path.join(dp, fn), self.root).replace(os.sep, "/")
                    if fnmatch.fnmatch(rel, raw) or fnmatch.fnmatch(fn, raw):
                        out.append(os.path.join(dp, fn))
            return out
        full, rel, _ = self._resolve(raw)
        if os.path.isdir(full):
            for dp, dns, fns in os.walk(full):
                dns[:] = sorted(d for d in dns if not d.startswith(".") and d != "__pycache__")
                for fn in sorted(fns):
                    if not fn.startswith(".") and not fn.endswith((".pyc",)):
                        out.append(os.path.join(dp, fn))
            return out
        if not os.path.exists(full):
            raise AgentError("%s does not exist" % rel)
        return [full]

    def _check_writable(self, full, rel, in_scratch):
        if in_scratch:
            return
        if rel in self.protected:
            raise AgentError("%s is an input file of the task and must not be modified" % rel)
        if rel in self.deliverables or rel in self.created:
            return
        if not self.deliverables:
            return
        raise AgentError("only the deliverable(s) %s may be written in the workspace; put test files and "
                         "fixtures in $SCRATCH/ (e.g. $SCRATCH/%s)" % (", ".join(self.deliverables),
                                                                       os.path.basename(rel)))

    # ------------------------------------------------------------- dispatch
    def call(self, name, args):
        t0 = time.monotonic()
        fn = getattr(self, "t_" + name, None)
        if fn is None or name not in self.names():
            return "ERROR: unknown tool '%s'. Valid tools: %s" % (name, ", ".join(self.names()))
        if not isinstance(args, dict):
            return "ERROR: arguments must be a JSON object"
        try:
            out = fn(**self._clean_args(name, args))
        except TypeError as e:
            out = "ERROR: bad arguments for %s: %s" % (name, e)
        except AgentError as e:
            out = "ERROR: %s" % e
        except OSError as e:
            out = "ERROR: %s" % (e.strerror or e)
        except Exception as e:  # a tool bug must never kill the run
            out = "ERROR: %s failed: %s: %s" % (name, type(e).__name__, e)
        short = json.dumps({k: (v if not isinstance(v, str) or len(v) < 80 else v[:77] + "...")
                            for k, v in args.items()}, ensure_ascii=False)[:200]
        log("tool %s %s -> %s (%.2fs)" % (name, short, out[:120].replace("\n", " | "), time.monotonic() - t0))
        return out

    @staticmethod
    def _clean_args(name, args):
        alias = {"file": "path", "filename": "path", "file_path": "path", "text": "content", "cmd": "command",
                 "old_str": "old", "new_str": "new", "old_text": "old", "new_text": "new", "query": "pattern",
                 "offset": "start_line", "limit": "num_lines", "line": "start", "lines": "count"}
        out = {}
        for k, v in args.items():
            if name == "notes" and k in ("note", "content", "finding", "text"):
                k2 = "text"
            elif name == "finish" and k in ("summary", "result", "answer", "status_message", "text"):
                k2 = "message"
            elif name == "finish" and k == "status":
                continue
            elif name == "run_python" and k in ("script", "source", "python"):
                k2 = "code"
            else:
                k2 = alias.get(k, k)
            out[k2] = v
        return out

    # ------------------------------------------------------------- tools
    def t_list_dir(self, path=".", depth=2):
        full, rel, _ = self._resolve(path or ".")
        if not os.path.isdir(full):
            raise AgentError("%s is not a directory" % rel)
        depth = max(1, min(int(depth or 2), 4))
        lines, n = [], 0
        base = full
        for dp, dns, fns in os.walk(base):
            d = os.path.relpath(dp, base)
            lvl = 0 if d == "." else d.count(os.sep) + 1
            dns[:] = sorted(x for x in dns if not x.startswith(".") and x != "__pycache__")
            if lvl >= depth:
                dns[:] = []
            for fn in sorted(fns):
                if fn.startswith("."):
                    continue
                p = os.path.join(dp, fn)
                r = os.path.relpath(p, self.root).replace(os.sep, "/")
                try:
                    size = os.path.getsize(p)
                except OSError:
                    size = -1
                lines.append("%s (%d bytes)" % (r, size))
                n += 1
                if n >= 200:
                    lines.append("... (listing capped at 200 entries)")
                    return "\n".join(lines)
        return "\n".join(lines) or "(empty)"

    def t_read_file(self, path, start_line=1, num_lines=None):
        full, rel, _ = self._resolve(path)
        if os.path.isdir(full):
            raise AgentError("%s is a directory" % rel)
        with open(full, "rb") as f:
            data = f.read()
        if b"\x00" in data[:4096]:
            raise AgentError("%s is a binary file" % rel)
        text, _ = profiling.read_text(full)
        lines = text.splitlines()
        start = max(1, int(start_line or 1))
        n = int(num_lines) if num_lines else (len(lines) if len(text) <= OUTPUT_CHARS else READ_LINES_DEFAULT)
        chunk = lines[start - 1:start - 1 + max(1, n)]
        body = "\n".join("%5d| %s" % (start + i, l[:500]) for i, l in enumerate(chunk))
        more = ""
        if start - 1 + len(chunk) < len(lines):
            more = "\n[%d of %d lines shown; call read_file with start_line=%d for more]" % (
                len(chunk), len(lines), start + len(chunk))
        return "%s (%d lines)\n%s%s" % (rel, len(lines), truncate(body, hint="use start_line/num_lines"), more)

    def t_read_lines(self, path, start, count=40):
        return self.t_read_file(path, start_line=start, num_lines=min(int(count or 40), 300))

    def t_log_stats(self, path):
        full, rel, _ = self._resolve(path)
        return profiling.log_stats(full, rel)

    def t_grep(self, pattern, path=".", regex=False, ignore_case=False, context=0, max_matches=40, word=None):
        if not isinstance(pattern, str) or not pattern:
            raise AgentError("pattern must be a non-empty string")
        try:
            rx = profiling.compile_pattern(pattern, bool(regex), bool(ignore_case), word)
        except re.error as e:
            raise AgentError("invalid regex: %s (set regex=false for a literal search)" % e)
        files = [f for f in self._files(path) if os.path.getsize(f) < 50 * 1024 * 1024]
        text, n = profiling.grep_files(files, rx, self.root, max(0, min(int(context or 0), 10)),
                                       max(1, min(int(max_matches or 40), 200)))
        return truncate(text, hint="narrow the pattern, pass a path, or lower max_matches")

    def t_notes(self, text):
        text = str(text).strip()
        if text:
            self.notes.append(text[:600])
            self.notes = self.notes[-30:]
        return "noted (%d notes pinned)" % len(self.notes)

    def _write(self, path, content, mode):
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False) if content is not None else ""
        full, rel, scratch = self._resolve(path, for_write=True)
        self._check_writable(full, rel, scratch)
        if os.path.isdir(full):
            raise AgentError("%s is a directory" % rel)
        if scratch:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "a" if mode == "a" else "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return "OK: wrote %d characters to %s (scratch)" % (len(content), full)
        if mode == "a" and os.path.exists(full):
            with open(full, "r", encoding="utf-8", errors="replace", newline="") as f:
                content = f.read() + content
        self.sb.create_file(rel, content)
        with open(os.path.join(self.root, rel), "r", encoding="utf-8", errors="replace", newline="") as f:
            if f.read() != content:
                raise AgentError("read-back of %s did not match what was written" % rel)
        self.created.add(rel)
        extra = self.on_write(rel) if self.on_write else ""
        return "OK: %s %s (%d lines, %d characters)%s" % (
            "appended to" if mode == "a" else "wrote", rel, content.count("\n") + 1, len(content), extra or "")

    def t_write_file(self, path, content):
        return self._write(path, content, "w")

    def t_append_file(self, path, content):
        return self._write(path, content, "a")

    def t_edit_file(self, path, old, new, count=1):
        full, rel, scratch = self._resolve(path, for_write=True)
        self._check_writable(full, rel, scratch)
        with open(full, "r", encoding="utf-8", errors="replace", newline="") as f:
            text = f.read()
        if not isinstance(old, str) or not old:
            raise AgentError("'old' must be a non-empty exact snippet of the file")
        new = new if isinstance(new, str) else str(new)
        n = text.count(old)
        if n == 0:
            # tolerate a whitespace-only mismatch at line ends
            stripped = "\n".join(l.rstrip() for l in old.split("\n"))
            if stripped and text.count(stripped) == 1:
                old, n = stripped, 1
        if n == 0:
            hit = _reindent_match(text, old, new)
            if hit is not None:
                old, new = hit
                n = 1
        if n == 0 and new and new in text:
            raise AgentError("'old' text not found in %s - but the file already contains your 'new' text: this "
                             "edit was ALREADY APPLIED. Do not repeat it; move on to the next problem." % rel)
        if n == 0:
            first = old.strip().split("\n")[0].strip()
            near = [("%5d| %s" % (i + 1, l)) for i, l in enumerate(text.split("\n")) if first and first[:40] in l]
            hint = ("\nlines containing its first line:\n" + "\n".join(near[:5])) if near else (
                "\n(no line contains its first line; read_file the file to copy the exact text)")
            raise AgentError("'old' text not found in %s%s" % (rel, hint))
        count = int(count or 1)
        if n > 1 and count == 1:
            raise AgentError("'old' occurs %d times in %s; include more surrounding lines to make it unique, "
                             "or pass count=%d to replace all" % (n, rel, n))
        text = text.replace(old, new, count if count > 0 else n)
        if scratch:
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            return "OK: edited %s" % full
        self.sb.create_file(rel, text)
        self.created.add(rel)
        extra = self.on_write(rel) if self.on_write else ""
        return "OK: edited %s (%d replacement%s)%s" % (rel, min(n, count if count > 0 else n),
                                                       "" if n == 1 else "s", extra or "")

    def t_run_command(self, command, timeout=30):
        if not isinstance(command, str) or not command.strip():
            raise AgentError("command must be a non-empty string")
        why = blocked_reason(command)
        if why:
            raise AgentError("command refused: %s" % why)
        timeout = max(1, min(int(timeout or 30), MAX_TIMEOUT))
        code, out, err, dt, timed_out = run_process(command, self.root, timeout, self.env)
        return self._fmt_run(code, out, err, dt, timed_out, timeout)

    def t_run_python(self, code, timeout=30):
        if not isinstance(code, str) or not code.strip():
            raise AgentError("code must be a non-empty string")
        why = blocked_reason(code)
        if why:
            raise AgentError("code refused: %s" % why)
        path = os.path.join(self.scratch, "snippet_%d.py" % int(time.time() * 1000))
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        timeout = max(1, min(int(timeout or 30), MAX_TIMEOUT))
        code_, out, err, dt, timed_out = run_process([sys.executable, "-B", path], self.root, timeout,
                                                     self.env, shell=False)
        return self._fmt_run(code_, out, err, dt, timed_out, timeout)

    @staticmethod
    def _fmt_run(code, out, err, dt, timed_out, timeout):
        head = "exit code %s (%.2fs)%s" % (code, dt, " - TIMED OUT after %ds, process group killed" % timeout
                                          if timed_out else "")
        parts = [head, "stdout:\n" + (truncate(out, 4500) if out else "(empty)")]
        if err:
            parts.append("stderr:\n" + truncate(err, 2000))
        return "\n".join(parts)

    def t_report_set(self, values):
        if self.report is None:
            raise AgentError("report_set is only available for report tasks")
        if isinstance(values, str):
            from llm import _normalize_args
            values, err = _normalize_args(values)
            if err:
                raise AgentError("values must be a JSON object: %s" % err)
        if not isinstance(values, dict) or not values:
            raise AgentError("values must be a non-empty object {key: value}")
        return self.report.set(values)

    def t_finish(self, message=""):
        return "FINISH"
