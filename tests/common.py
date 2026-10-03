"""Helpers shared by the test suites: run the agent exactly as the grader
does and check the stdout contract."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_AGENT = os.path.join(ROOT, "run_agent.sh")


_ALL_WORKSPACES = []
SESSION_DIR = os.path.realpath(tempfile.mkdtemp(prefix="agent_sessions_"))


def cleanup_all():
    for ws in _ALL_WORKSPACES:
        ws.cleanup()
    shutil.rmtree(SESSION_DIR, ignore_errors=True)


class Workspace:
    """A fresh temporary task directory, separate from the submission."""

    def __init__(self, files=None, subdir=None):
        self._root = os.path.realpath(tempfile.mkdtemp(prefix="agent_task_"))
        self.dir = self._root
        if subdir:  # e.g. a workspace path containing spaces / Unicode
            self.dir = os.path.join(self._root, subdir)
            os.makedirs(self.dir)
        _ALL_WORKSPACES.append(self)
        for rel, content in (files or {}).items():
            self.write(rel, content)

    def path(self, rel):
        return os.path.join(self.dir, rel)

    def write(self, rel, content):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(content.encode("utf-8") if isinstance(content, str) else content)

    def read(self, rel):
        with open(self.path(rel), "rb") as f:
            return f.read().decode("utf-8")

    def exists(self, rel):
        return os.path.lexists(self.path(rel))

    def snapshot(self):
        """{relpath: sha256} for every file and a marker for every dir."""
        snap = {}
        for dp, dns, fns in os.walk(self.dir):
            for d in dns:
                snap[os.path.relpath(os.path.join(dp, d), self.dir) + "/"] = "dir"
            for fn in fns:
                p = os.path.join(dp, fn)
                if os.path.islink(p):
                    snap[os.path.relpath(p, self.dir)] = "link:" + os.readlink(p)
                    continue
                if not stat.S_ISREG(os.lstat(p).st_mode):  # FIFOs etc.: never open (would block)
                    snap[os.path.relpath(p, self.dir)] = "special"
                    continue
                with open(p, "rb") as f:
                    snap[os.path.relpath(p, self.dir)] = hashlib.sha256(f.read()).hexdigest()
        return snap

    def cleanup(self):
        for dp, dns, fns in os.walk(self._root):  # undo read-only permissions set by tests
            for n in dns + fns:
                p = os.path.join(dp, n)
                if not os.path.islink(p):
                    try:
                        os.chmod(p, 0o755)
                    except OSError:
                        pass
        try:
            os.chmod(self._root, 0o755)
        except OSError:
            pass
        shutil.rmtree(self._root, ignore_errors=True)


class Result:
    def __init__(self, proc, elapsed):
        self.code = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.elapsed = elapsed
        self.json = None
        self.contract_error = check_contract(self.stdout)
        if self.contract_error is None:
            self.json = json.loads(self.stdout)

    @property
    def status(self):
        return self.json["status"] if self.json else None

    @property
    def message(self):
        return self.json["message"] if self.json else ""


def check_contract(stdout):
    """Return None if stdout is exactly one JSON object with exactly the
    string fields status/message, else a description of the violation."""
    text = stdout.strip()
    if not text:
        return "empty stdout"
    try:
        dec = json.JSONDecoder()
        obj, end = dec.raw_decode(text)
    except ValueError as e:
        return "stdout is not JSON: %r (%s)" % (stdout[:200], e)
    if text[end:].strip():
        return "extra output after JSON: %r" % text[end:][:200]
    if not isinstance(obj, dict):
        return "JSON is not an object"
    if set(obj) != {"status", "message"}:
        return "JSON keys are %r" % sorted(obj)
    if not all(isinstance(obj[k], str) for k in obj):
        return "fields are not both strings"
    if obj["status"] not in ("success", "error"):
        return "bad status %r" % obj["status"]
    return None


def run_agent(ws, prompt, env_overrides, timeout=200, extra_args=None, base_env=None,
              run_agent_path=RUN_AGENT, keep_stdin_open=False):
    """Invoke the agent exactly like the grader: cd $TASK_DIR && bash run_agent.sh "$PROMPT".
    keep_stdin_open leaves an open pipe on stdin that is never written or
    closed, to prove the agent never waits for input."""
    env = dict(os.environ if base_env is None else base_env)
    env.setdefault("AGENT_SESSION_DIR", SESSION_DIR)  # keep test sessions out of the real store
    env.update(env_overrides)
    for k, v in list(env_overrides.items()):
        if v is None:
            env.pop(k, None)
    args = ["bash", run_agent_path] + ([prompt] if prompt is not None else []) + (extra_args or [])
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
        proc = subprocess.Popen(args, cwd=ws.dir, env=env, stdout=out_f, stderr=err_f,
                                stdin=subprocess.PIPE if keep_stdin_open else subprocess.DEVNULL)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError("agent did not exit within %ss" % timeout)
        finally:
            if proc.stdin:
                proc.stdin.close()
        out_f.seek(0)
        err_f.seek(0)
        out = out_f.read().decode("utf-8", "replace")
        err = err_f.read().decode("utf-8", "replace")
    return Result(subprocess.CompletedProcess(args, proc.returncode, out, err), time.monotonic() - t0)


class Runner:
    """Minimal test runner that reports pass/fail/infra per case."""

    def __init__(self):
        self.results = []

    def case(self, name, fn):
        try:
            fn()
            self.results.append((name, "PASS", ""))
            print("PASS  %s" % name, flush=True)
        except InfraError as e:
            self.results.append((name, "INFRA", str(e)))
            print("INFRA %s -- %s" % (name, e), flush=True)
        except AssertionError as e:
            self.results.append((name, "FAIL", str(e)))
            print("FAIL  %s -- %s" % (name, e), flush=True)
        except Exception as e:
            self.results.append((name, "FAIL", repr(e)))
            print("FAIL  %s -- %r" % (name, e), flush=True)

    def summary(self):
        cleanup_all()
        counts = {}
        for _, s, _ in self.results:
            counts[s] = counts.get(s, 0) + 1
        print("\n%d cases: %s" % (len(self.results),
                                  ", ".join("%s=%d" % kv for kv in sorted(counts.items()))))
        return 0 if counts.get("FAIL", 0) == 0 and counts.get("INFRA", 0) == 0 else 1


class InfraError(Exception):
    """The model endpoint was unreachable/failed -- not a code defect."""


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def assert_contract(r):
    expect(r.contract_error is None, "stdout contract violated: %s" % r.contract_error)
    expect((r.code == 0) == (r.status == "success"),
           "exit code %d inconsistent with status %r" % (r.code, r.status))
