"""Workspace, environment and process helpers shared by the adversarial runner."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INFRA_MARKERS = ("language model request failed", "endpoint", "http 5", "http 429", "rate limit",
                 "service unavailable", "bad gateway", "timed out contacting")


def load_env(stub=False):
    e = dict(os.environ)
    if stub:
        e.setdefault("QWEN_API_KEY", "stub")
        return e
    key = os.environ.get("QWEN_API_KEY", "").strip()
    kf = os.path.expanduser("~/.qwen_key")
    if not key and os.path.exists(kf):
        key = open(kf).read().strip()
    if not key:
        raise SystemExit("No API key: set QWEN_API_KEY or create ~/.qwen_key")
    e.update({"OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "https://llm.montii.me/v1"),
              "MODEL": os.environ.get("MODEL", "Qwen/Qwen3-14B"), "QWEN_API_KEY": key})
    return e


def snapshot(root):
    out = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        rel_dp = os.path.relpath(dp, root)
        for d in dns:
            p = os.path.join(dp, d)
            rel = os.path.normpath(os.path.join(rel_dp, d))
            out[rel] = "<link:%s>" % os.readlink(p) if os.path.islink(p) else "<dir>"
        for f in fns:
            p = os.path.join(dp, f)
            rel = os.path.normpath(os.path.join(rel_dp, f))
            if os.path.islink(p):
                out[rel] = "<link:%s>" % os.readlink(p)
            else:
                with open(p, "rb") as fh:
                    out[rel] = hashlib.sha1(fh.read()).hexdigest()
    return out


def make_workspace(files, prefix="adv_ws_"):
    """Fresh workspace with the grader's unrelated decoys plus ``files``
    ({rel: str | bytes | None}); None makes an empty directory."""
    ws = tempfile.mkdtemp(prefix=prefix)
    os.makedirs(os.path.join(ws, "unrelated", "empty"))
    with open(os.path.join(ws, "unrelated", "sentinel.bin"), "wb") as f:
        f.write(bytes(range(256)) * 4)
    for rel, content in files.items():
        p = os.path.join(ws, rel)
        if content is None:
            os.makedirs(p, exist_ok=True)
            continue
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        if callable(content):
            content = content()
        with open(p, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    return ws


def check_contract(stdout):
    """Return (obj, error). Exactly one JSON object with string status/message."""
    s = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout
    st = s.strip()
    if not st:
        return None, "empty stdout"
    try:
        dec = json.JSONDecoder()
        obj, end = dec.raw_decode(st)
    except ValueError as e:
        return None, "stdout is not JSON: %s" % e
    if st[end:].strip():
        return None, "extra text after the JSON object"
    if not isinstance(obj, dict):
        return None, "stdout JSON is not an object"
    if set(obj) != {"status", "message"}:
        return None, "keys are %s, want exactly status and message" % sorted(obj)
    if obj.get("status") not in ("success", "error") or not isinstance(obj.get("message"), str):
        return None, "bad status/message types"
    return obj, None


def run_agent(submission, ws, prompt, session, env, state_dir, timeout):
    args = ["bash", os.path.join(submission, "run_agent.sh"), prompt]
    if session is not None:
        args.append(str(session))
    e = dict(env)
    e["AGENT_STATE_DIR"] = state_dir
    e["AGENT_SESSION_DIR"] = state_dir
    e["XDG_STATE_HOME"] = state_dir
    t0 = time.monotonic()
    try:
        p = subprocess.run(args, cwd=ws, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, timeout=timeout + 20)
        code, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as ex:
        code, out, err = -9, ex.stdout or b"", (ex.stderr or b"") + b"\n[runner] killed after timeout"
    dt = time.monotonic() - t0
    obj, cerr = check_contract(out)
    if obj is not None and cerr is None:
        want = 0 if obj["status"] == "success" else 1
        if (code == 0) != (want == 0):
            cerr = "exit code %d does not match status %s" % (code, obj["status"])
    if dt > timeout:
        cerr = (cerr + "; " if cerr else "") + "took %.0fs (limit %ds)" % (dt, timeout)
    err_s = err.decode("utf-8", "replace")
    calls = 0
    for line in err_s.splitlines():
        if "calls_used=" in line:
            try:
                calls = max(calls, int(line.split("calls_used=")[1].split()[0]))
            except ValueError:
                pass
    return {
        "code": code, "stdout": out.decode("utf-8", "replace")[:4000], "stderr_tail": err_s[-6000:],
        "elapsed": round(dt, 1), "status": obj.get("status") if obj else None,
        "message": obj.get("message") if obj else None, "contract_error": cerr, "calls": calls,
    }


def is_infra(res):
    m = ((res.get("message") or "") + " " + res.get("stderr_tail", "")[-800:]).lower()
    return res.get("status") == "error" and any(k in m for k in INFRA_MARKERS) and \
        "language model request failed" in m


def endpoint_ok(env, timeout=30):
    import urllib.request
    body = json.dumps({"model": env.get("MODEL", "Qwen/Qwen3-14B"), "max_tokens": 4,
                       "messages": [{"role": "user", "content": "hi"}],
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(env.get("OPENAI_BASE_URL", "https://llm.montii.me/v1").rstrip("/") +
                                 "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "curl/8.7.1",
                                          "Authorization": "Bearer " + env.get("QWEN_API_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def wait_endpoint(env, log, max_wait=1800):
    """Block until the endpoint answers a tiny request (checked every 30 s)."""
    t0 = time.monotonic()
    while not endpoint_ok(env):
        if time.monotonic() - t0 > max_wait:
            log("endpoint still busy after %ds; continuing anyway" % max_wait)
            return False
        log("endpoint busy; waiting 30 s")
        time.sleep(30)
    return True


def cleanup(*paths):
    for p in paths:
        shutil.rmtree(p, ignore_errors=True)
