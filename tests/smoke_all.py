#!/usr/bin/env python3
"""One-command smoke test of all three checkpoints, run exactly like the grader.

  python3 tests/smoke_all.py            # CP1 + CP2 + CP3 (about 6-10 minutes; CP3 dominates)
  python3 tests/smoke_all.py --no-cp3   # CP1 + CP2 only (about 1-2 minutes)

Each step calls `bash run_agent.sh PROMPT [SESSION]` from a fresh temp workspace and checks the
exit code, the single JSON object on stdout, and the answer / files. The API key comes from
$QWEN_API_KEY or ~/.qwen_key; saved conversations go to a throwaway folder.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGENT = os.environ.get("AGENT_UNDER_TEST") or os.path.join(ROOT, "run_agent.sh")
sys.path.insert(0, os.path.join(HERE, "adversarial", "cp3"))

RESULTS = []


def env():
    key = os.environ.get("QWEN_API_KEY", "").strip()
    kf = os.path.expanduser("~/.qwen_key")
    if not key and os.path.exists(kf):
        key = open(kf).read().strip()
    if not key:
        sys.exit("No API key: set QWEN_API_KEY or create ~/.qwen_key")
    e = dict(os.environ)
    e.update({"OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "https://llm.montii.me/v1"),
              "MODEL": os.environ.get("MODEL", "Qwen/Qwen3-14B"), "QWEN_API_KEY": key,
              "AGENT_STATE_DIR": tempfile.mkdtemp(prefix="smoke_state_")})
    return e


ENV = None


def workspace(files=None):
    ws = tempfile.mkdtemp(prefix="smoke_ws_")
    for rel, content in (files or {}).items():
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p) or ws, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    return ws


def run(ws, prompt, session=None, limit=200):
    args = ["bash", AGENT, prompt] + ([str(session)] if session is not None else [])
    t0 = time.monotonic()
    p = subprocess.run(args, cwd=ws, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=limit + 30)
    dt = time.monotonic() - t0
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        obj = json.loads(out)
        ok = set(obj) == {"status", "message"} and (p.returncode == 0) == (obj["status"] == "success")
    except ValueError:
        obj, ok = {"status": "?", "message": out[:200]}, False
    return obj, p.returncode, dt, ok


def read(ws, rel):
    p = os.path.join(ws, rel)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def step(cp, name, ws, prompt, check, session=None, limit=200):
    obj, code, dt, contract = run(ws, prompt, session, limit)
    try:
        ok = contract and bool(check(obj, ws))
    except Exception as e:  # noqa: BLE001
        ok = False
        obj["message"] += " [check error: %s]" % e
    RESULTS.append((cp, name, ok))
    print("%s  %-4s %-34s exit=%s %5.1fs  %s" % ("PASS" if ok else "FAIL", cp, name, code, dt,
                                                 obj.get("message", "")[:90].replace("\n", " ")), flush=True)
    return ok


def has(*words):
    return lambda o, ws: all(w.lower() in o["message"].lower() for w in words)


# ------------------------------------------------------------------ checkpoint 1
def cp1():
    ws = workspace({"old.txt": "old\n", "keep.txt": "keep\n"})
    step("CP1", "create a file", ws, "Create notes/todo.txt containing: buy milk",
         lambda o, w: o["status"] == "success" and read(w, "notes/todo.txt") == "buy milk")
    step("CP1", "delete a file", ws, "Delete old.txt.",
         lambda o, w: not os.path.exists(os.path.join(w, "old.txt")) and read(w, "keep.txt") == "keep\n")
    ws = workspace({"inventory.txt": "Blue notebooks: 12\nRed notebooks: 7\n"})
    step("CP1", "read and answer", ws, "Read inventory.txt. How many blue notebooks are currently in stock?",
         lambda o, w: "12" in o["message"] and read(w, "inventory.txt") == "Blue notebooks: 12\nRed notebooks: 7\n")
    ws = workspace({"logs/start.txt": "09:02 Checkout latency alert fired.\n",
                    "logs/resolution.txt": "09:44 Root cause: misconfigured load balancer health check; fixed.\n"})
    step("CP1", "multi-file summary", ws,
         "Use logs/start.txt and logs/resolution.txt to create reports/summary.txt with a summary under 50 words.",
         lambda o, w: read(w, "reports/summary.txt") and "load balancer" in read(w, "reports/summary.txt").lower()
         and len(read(w, "reports/summary.txt").split()) < 50)


# ------------------------------------------------------------------ checkpoint 2
def cp2():
    ws = workspace({"sales.csv": "month,revenue\nJanuary,120\nFebruary,180\n",
                    "orders.csv": "id,status\n1,shipped\n2,cancelled\n3,pending\n"})
    step("CP2", "statement turn (memory)", ws, "My project code name is Cedar.", lambda o, w: o["status"] == "success",
         session=7101)
    step("CP2", "recall in same session", ws, "What is my project code name?", has("cedar"), session=7101)
    step("CP2", "isolation (other session)", ws, "What is my project code name?",
         lambda o, w: "cedar" not in o["message"].lower(), session=7102)
    step("CP2", "CSV total", ws, "Read sales.csv and tell me the total of the revenue column.", has("300"),
         session=7103)
    step("CP2", "follow-up uses context", ws, "What is the average?", has("150"), session=7103)
    step("CP2", "CSV copy from context", ws, "Save a copy of the table as reports/sales_copy.csv.",
         lambda o, w: read(w, "reports/sales_copy.csv") == read(w, "sales.csv"), session=7103)
    step("CP2", "CSV remove rows", ws,
         "In orders.csv, remove every row whose status is cancelled, and save the result as active.csv.",
         lambda o, w: read(w, "active.csv") == "id,status\n1,shipped\n3,pending\n", session=7104)


# ------------------------------------------------------------------ checkpoint 3
def cp3():
    import gen_incident
    import prog_specs
    # programming: the official tally.py prompt (requirements 1-6 graded)
    ws = workspace()
    with open(os.path.join(ws, "transactions.csv"), "wb") as f:
        f.write(prog_specs.generate("O", n=4000))
    prompt = prog_specs.prompt("O")
    with open(os.path.join(ws, "TASK.md"), "w") as f:
        f.write(prompt)

    def prog_ok(o, w):
        res = prog_specs.grade("O", w)
        reqs = prog_specs.score(res)
        print("        tally.py requirements passed: %d/%d" % (sum(reqs.values()), len(reqs)), flush=True)
        return all(reqs.values())
    step("CP3", "programming (tally.py)", ws, prompt, prog_ok, session=3001, limit=300)

    # debugging: trace one failed request across 4 logs into REPORT.json
    ws = workspace()
    key, prompt = gen_incident.write(41, ws)

    def debug_ok(o, w):
        try:
            rep = json.load(open(os.path.join(w, "REPORT.json")))
        except Exception:  # noqa: BLE001
            return False
        good = sum(1 for k, v in key.items() if rep.get(k) == v and type(rep.get(k)) is type(v))
        print("        REPORT.json keys correct: %d/%d" % (good, len(key)), flush=True)
        return good >= 11
    step("CP3", "debugging (REPORT.json)", ws, prompt, debug_ok, session=3002, limit=300)


def main():
    global ENV
    ENV = env()
    t0 = time.monotonic()
    print("Agent: %s" % AGENT, flush=True)
    cp1()
    cp2()
    if "--no-cp3" not in sys.argv:
        cp3()
    passed = sum(1 for r in RESULTS if r[2])
    print("\n%d/%d steps passed in %.0fs" % (passed, len(RESULTS), time.monotonic() - t0))
    for cp in ("CP1", "CP2", "CP3"):
        rs = [r for r in RESULTS if r[0] == cp]
        if rs:
            print("  %s: %d/%d" % (cp, sum(1 for r in rs if r[2]), len(rs)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
