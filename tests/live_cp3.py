"""Live CP3 runs [KEY]: debugging scenarios from gen_logs.py and programming
spec variants from gen_transactions.py, invoked exactly like the grader.

  python3 tests/live_cp3.py debug  [--seeds 1,2] [--scenarios basic,retry,...] [--reps 1]
  python3 tests/live_cp3.py prog   [--variants A,B,C,OFFICIAL] [--seeds 1] [--reps 1]

Writes a markdown report to tests/reports/live_cp3_<timestamp>.md.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import gen_logs  # noqa: E402
import gen_transactions  # noqa: E402
import prog_variants  # noqa: E402
from common import check_contract  # noqa: E402

SCENARIOS = {
    "basic": [], "retry": ["retry"], "decoy_ids": ["decoy_ids"], "decoy_class": ["decoy_class"],
    "json_lines": ["json_lines"], "mixed_ts": ["mixed_ts"],
    "all": ["retry", "decoy_ids", "decoy_class", "json_lines", "mixed_ts"],
}


def env():
    key = os.environ.get("QWEN_API_KEY", "").strip()
    if not key and os.path.exists(os.path.expanduser("~/.qwen_key")):
        key = open(os.path.expanduser("~/.qwen_key")).read().strip()
    if not key:
        sys.exit("No API key: set QWEN_API_KEY or create ~/.qwen_key")
    e = dict(os.environ)
    e.update({"OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", "https://llm.montii.me/v1"),
              "MODEL": os.environ.get("MODEL", "Qwen/Qwen3-14B"), "QWEN_API_KEY": key})
    return e


def invoke(ws, prompt, e, session="3001", limit=300):
    t0 = time.monotonic()
    p = subprocess.run(["bash", os.path.join(ROOT, "run_agent.sh"), prompt, session], cwd=ws, env=e,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=limit + 30)
    dt = time.monotonic() - t0
    err = p.stderr.decode("utf-8", "replace")
    calls = 0
    for line in err.splitlines():
        if "calls_used=" in line:
            try:
                calls = max(calls, int(line.split("calls_used=")[1].split()[0]))
            except ValueError:
                pass
    return p.returncode, p.stdout.decode("utf-8", "replace"), err, dt, calls


def run_debug(seed, scen, e, hidden=6, names=None):
    ws = tempfile.mkdtemp(prefix="cp3_dbg_")
    try:
        logs, key, prompt = gen_logs.generate(seed, set(SCENARIOS[scen]), "ORD-77341",
                                              names or ["gateway", "orders", "inventory", "payments"], hidden)
        os.makedirs(os.path.join(ws, "logs"))
        for n, t in logs.items():
            with open(os.path.join(ws, "logs", n + ".log"), "w") as f:
                f.write(t)
        with open(os.path.join(ws, "TASK.md"), "w") as f:
            f.write(prompt)
        code, out, err, dt, calls = invoke(ws, prompt, e)
        got = {}
        try:
            with open(os.path.join(ws, "REPORT.json")) as f:
                got = json.load(f)
        except (OSError, ValueError):
            pass
        per = {k: (got.get(k) == v and type(got.get(k)) == type(v)) for k, v in key.items()}
        extra = sorted(set(got) - set(key))
        return {"case": "DB seed=%d %s" % (seed, scen), "ok": sum(per.values()), "of": len(key), "per": per,
                "extra": extra, "contract": check_contract(out), "time": dt, "calls": calls, "exit": code,
                "got": got, "key": key, "stdout": out.strip(), "stderr_tail": err[-60000:]}
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def run_prog(variant, seed, e, crlf=False, bom=False):
    ws = tempfile.mkdtemp(prefix="cp3_prog_")
    try:
        lines = gen_transactions.generate(40000, seed, spaces=(variant == "B"))
        nl = "\r\n" if crlf else "\n"
        data = (nl.join(lines) + nl).encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        with open(os.path.join(ws, "transactions.csv"), "wb") as f:
            f.write(data)
        prompt = prog_variants.prompt(variant)
        with open(os.path.join(ws, "TASK.md"), "w") as f:
            f.write(prompt)
        code, out, err, dt, calls = invoke(ws, prompt, e)
        results = prog_variants.grade(variant, ws)
        keep = os.environ.get("LIVE_KEEP_DIR")
        if keep and os.path.exists(os.path.join(ws, "tally.py")):
            os.makedirs(keep, exist_ok=True)
            shutil.copy(os.path.join(ws, "tally.py"), os.path.join(keep, "tally_%s_%d.py" % (variant, seed)))
        leftovers = sorted(set(os.listdir(ws)) - {"transactions.csv", "TASK.md", "tally.py"})
        return {"case": "PG variant=%s seed=%d%s%s" % (variant, seed, " crlf" if crlf else "", " bom" if bom else ""),
                "ok": sum(1 for r in results if r[1]), "of": len(results), "per": dict((r[0], r[1]) for r in results),
                "detail": [r for r in results if not r[1]], "extra": leftovers, "contract": check_contract(out),
                "time": dt, "calls": calls, "exit": code, "stdout": out.strip(), "stderr_tail": err[-60000:]}
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["debug", "prog"])
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--scenarios", default="basic")
    ap.add_argument("--variants", default="A")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--hidden", type=int, default=6)
    ap.add_argument("--crlf", action="store_true")
    ap.add_argument("--bom", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    e = env()
    results = []
    for rep in range(a.reps):
        for seed in [int(s) for s in a.seeds.split(",")]:
            items = a.scenarios.split(",") if a.what == "debug" else a.variants.split(",")
            for it in items:
                r = run_debug(seed, it, e, a.hidden) if a.what == "debug" else run_prog(it, seed, e, a.crlf, a.bom)
                results.append(r)
                print("%-34s %2d/%-2d  %5.1fs  calls=%-3d exit=%s contract=%s %s" % (
                    r["case"], r["ok"], r["of"], r["time"], r["calls"], r["exit"], r["contract"] or "ok",
                    ("extra=%s" % r["extra"]) if r["extra"] else ""), flush=True)
                bad = [k for k, v in r["per"].items() if not v]
                if bad:
                    print("    wrong: %s" % ", ".join(bad))
                    if a.what == "debug":
                        for k in bad:
                            print("      %s: got %r want %r" % (k, r["got"].get(k), r["key"][k]))
                    else:
                        for d in r["detail"][:12]:
                            print("      %s: %s" % (d[0], d[2][:300]))
                if a.v:
                    print(r["stderr_tail"])
    os.makedirs(os.path.join(HERE, "reports"), exist_ok=True)
    path = os.path.join(HERE, "reports", "live_cp3_%s_%s.md" % (a.what, time.strftime("%Y%m%d_%H%M%S")))
    with open(path, "w") as f:
        f.write("| case | score | time s | calls | exit |\n|---|---|---|---|---|\n")
        for r in results:
            f.write("| %s | %d/%d | %.1f | %d | %s |\n" % (r["case"], r["ok"], r["of"], r["time"], r["calls"], r["exit"]))
        tot = sum(r["ok"] for r in results)
        of = sum(r["of"] for r in results)
        f.write("\nTotal: %d/%d (%.1f%%); max time %.1fs; max calls %d\n" % (
            tot, of, 100.0 * tot / max(1, of), max(r["time"] for r in results), max(r["calls"] for r in results)))
    print("report: %s" % path)


if __name__ == "__main__":
    main()
