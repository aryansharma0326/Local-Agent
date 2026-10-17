"""Checkpoint 3 runs: programming (tally.py variants) and debugging (incident logs).

Each run is one ``run_agent.sh PROMPT SESSION`` call with a 300 s limit in a
fresh workspace that holds the inputs and TASK.md, like the grader's.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "cp3"))
import gen_incident  # noqa: E402
import prog_specs  # noqa: E402

CASES = []


def prog(id, variant, tag, attack):
    CASES.append(dict(id=id, cp="cp3", group="programming", tag=tag, attack=attack, kind="prog", variant=variant,
                      session="3%03d" % len(CASES)))


def debug(id, tag, attack, seed, names=("gateway", "orders", "inventory", "payments"), order="ORD-77341",
          dev_generator=False, scenarios="", variant=None):
    CASES.append(dict(id=id, cp="cp3", group="debugging", tag=tag, attack=attack, kind="debug", seed=seed,
                      names=list(names), order=order, dev_generator=dev_generator, scenarios=scenarios,
                      variant=variant, session="3%03d" % len(CASES)))


prog("PG-O", "O", "grader-like", "The official prompt word for word (7-12 hidden); 40k rows with quoted commas, "
     "negatives, mixed-case categories, a category with '&' and a forced top-N tie. Graded on 1-6.")
prog("PG-D", "D", "grader-like", "Hidden-style 7-12 the agent was never tuned on: --json on every command (object "
     "shapes), count --invalid, monthly, stats with exit 1, usage errors with exit 2, exact half-up rounding of "
     "3-decimal amounts. Data has CRLF, a repeated header, a blank line and 200 invalid rows.")
prog("PG-E", "E", "stress", "Hidden-style 7-12: --from/--to on every command, comma-separated category lists, "
     "bottom N, whitespace trimming, $/+ amount formats, --name=value options. Data has a UTF-8 BOM.")

debug("DB-1", "grader-like", "Misspelled culprit class (StockReconcilationJob) while the correct spelling appears "
      "in noise; earlier 409 attempt for the same order; older recovered stall on the same worker; look-alike "
      "decoy orders; four different log formats incl. log4j comma timestamps and JSON lines.", seed=12)
debug("DB-2", "stress", "Same traps with renamed service files (edge/checkout/stock/billing) and a different order "
      "id, so nothing about ORD-77341 or the default file names can be assumed.", seed=13,
      names=("edge", "checkout", "stock", "billing"), order="ORD-58212")
debug("DB-3", "grader-like", "Culprit SkuIndexReBuilder (capital B) vs SkuIndexRebuilder in noise.", seed=14)
debug("DB-4", "grader-like", "Baseline: the submission's own development generator with every scenario enabled "
      "(retry, decoy ids, decoy class, JSON lines, mixed timestamps).", seed=9, dev_generator=True,
      scenarios="retry,decoy_ids,decoy_class,json_lines,mixed_ts")


# ---------------------------------------------------------------- round 2
prog("PG-F", "F", "grader-like", "Hidden-style 7-12 with bigger outputs: monthly and monthly --json, median with an "
     "even/odd count, daily, export PATH (writes a CSV and prints 'wrote PATH'), and a 2-second speed limit on a "
     "60,000-row file. This also checks the time budget when the self-review step runs.")
debug("DB-B01", "grader-like", "The cause is not a lock: the innermost 'Caused by' is java.util.concurrent."
      "TimeoutException, whose first frame is java's FutureTask; the real component SupplierFeedClient is the next "
      "frame and has its own WARN line. The root-cause check may steer the model to the wrong class. Key 11 asks "
      "for the component's timeout instead of a lock name.", seed=21, variant="pool")
debug("DB-B02", "grader-like", "Different hidden keys: order_number (the string ORD-77341, but the '_number' suffix "
      "forces an integer), upstream_service, payment_cancel_event.", seed=22, variant="keys2")
debug("DB-B03", "stress", "The gateway retried upstream: one req= has a first upstream_trace (connection reset, "
      "no hold) and a second one that waited on the hold and failed. The answer is the second trace.",
      seed=23, variant="retry_up")


def setup(case, ws, fixtures_dir):
    """Populate ``ws``; return (prompt, answer key or None)."""
    if case["kind"] == "prog":
        p = prog_specs.prompt(case["variant"])
        with open(os.path.join(ws, "transactions.csv"), "wb") as f:
            f.write(prog_specs.generate(case["variant"]))
        with open(os.path.join(ws, "TASK.md"), "w") as f:
            f.write(p)
        return p, None
    if case["dev_generator"]:
        tmp = tempfile.mkdtemp(prefix="adv_gl_")
        subprocess.check_call([sys.executable, os.path.join(fixtures_dir, "gen_logs.py"), "--seed",
                               str(case["seed"]), "--out", tmp, "--scenarios", case["scenarios"], "--hidden", "6"])
        key = json.load(open(os.path.join(tmp, "answer_key.json")))
        prompt = open(os.path.join(tmp, "TASK.md")).read()
        os.makedirs(os.path.join(ws, "logs"), exist_ok=True)
        for n in os.listdir(os.path.join(tmp, "logs")):
            with open(os.path.join(tmp, "logs", n), "rb") as s, open(os.path.join(ws, "logs", n), "wb") as d:
                d.write(s.read())
        with open(os.path.join(ws, "TASK.md"), "w") as f:
            f.write(prompt)
        return prompt, key
    key, prompt = gen_incident.write(case["seed"], ws, case["names"], case["order"], case.get("variant"))
    return prompt, key


def grade(case, ws, key):
    """-> (points, max_points, [(name, ok, detail)])"""
    if case["kind"] == "prog":
        res = prog_specs.grade(case["variant"], ws)
        reqs = prog_specs.score(res)
        rows = [("req %d: %s" % (r, n), ok, d) for r, n, ok, d in res]
        return sum(1 for v in reqs.values() if v), len(reqs), rows
    path = os.path.join(ws, "REPORT.json")
    rows = []
    try:
        rep = json.load(open(path))
    except Exception as e:  # noqa: BLE001
        return 0, len(key), [(k, False, "REPORT.json unreadable: %s" % e) for k in key]
    if not isinstance(rep, dict):
        return 0, len(key), [(k, False, "REPORT.json is not an object") for k in key]
    extra = sorted(set(rep) - set(key))
    for k, want in key.items():
        got = rep.get(k, "<missing>")
        ok = got == want and type(got) is type(want)
        rows.append((k, ok, "want %r (%s) got %r (%s)" % (want, type(want).__name__, got, type(got).__name__)))
    if extra:
        rows.append(("no extra keys", False, "extra keys %s" % extra))
    pts = sum(1 for k, ok, _ in rows if ok and k in key)
    return pts, len(key), rows
