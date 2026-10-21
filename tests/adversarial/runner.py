#!/usr/bin/env python3
"""Adversarial end-to-end runner (grader-style) for HW1 checkpoints 1-3.

  python3 tests/adversarial/runner.py --submission DIR --label NAME
          [--only cp1,cp2,cp3,CASE-ID,PREFIX] [--workers 2] [--reruns 2] [--keep]
  python3 tests/adversarial/runner.py --selftest

Each case gets a fresh workspace (with the grader's unrelated/sentinel.bin and
unrelated/empty/) and a fresh, empty session store shared by its turns. The
submission is driven only through ``bash run_agent.sh PROMPT [SESSION]``.
Results go to tests/adversarial/results/<label>.json.
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cases_cp1  # noqa: E402
import cases_cp2  # noqa: E402
import cases_cp3  # noqa: E402
from checks import Ctx, unchanged_except  # noqa: E402
from common import cleanup, is_infra, load_env, make_workspace, run_agent, snapshot, wait_endpoint  # noqa: E402

LOCK = threading.Lock()


def all_cases():
    return cases_cp1.CASES + cases_cp2.CASES + cases_cp3.CASES


def selected(cases, only):
    if not only:
        return cases
    keys = [k.strip() for k in only.split(",") if k.strip()]
    return [c for c in cases if any(c["cp"] == k or c["id"] == k or c["id"].startswith(k) for k in keys)]


def log(msg):
    with LOCK:
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        sys.stderr.flush()


# ------------------------------------------------------------------ CP1/CP2
def attempt_conversation(case, sub, env, keep):
    ws = make_workspace(case["files"])
    state = tempfile.mkdtemp(prefix="adv_state_")
    before = snapshot(ws)
    guard = unchanged_except(case["changes"])
    turns, ok_all, infra = [], True, False
    for t in case["turns"]:
        res = run_agent(sub, ws, t["prompt"], t["sid"], env, state, int(os.environ.get("ADV_CP12_LIMIT", "180")))
        if is_infra(res):
            infra = True
        ctx = Ctx(ws, res, before)
        rows = []
        if res["contract_error"]:
            rows.append(("output contract (one JSON object, exit code matches status, <=180 s)", False,
                         res["contract_error"]))
        if t["expect"] != "either":
            rows.append(("status is %s (exit %d)" % (t["expect"], 0 if t["expect"] == "success" else 1),
                         res["status"] == t["expect"], "status=%s exit=%s" % (res["status"], res["code"])))
        for chk in t["checks"]:
            try:
                ok, detail = chk(ctx)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, "check crashed: %r" % (e,)
            rows.append((getattr(chk, "desc", chk.__name__), ok, detail))
        ok, detail = guard(ctx)
        rows.append((guard.desc, ok, detail))
        tok = all(r[1] for r in rows)
        ok_all = ok_all and tok
        turns.append(dict(sid=t["sid"], prompt=t["prompt"], expect=t["expect"], passed=tok,
                          result=res, checks=[dict(desc=a, ok=b, detail=c) for a, b, c in rows]))
    if keep:
        log("%s workspace kept at %s (state %s)" % (case["id"], ws, state))
    else:
        cleanup(ws, state)
    return dict(passed=ok_all, infra=infra and not ok_all, turns=turns, workspace=ws if keep else None)


# ------------------------------------------------------------------ CP3
def attempt_cp3(case, sub, env, keep, fixtures):
    ws = make_workspace({})
    state = tempfile.mkdtemp(prefix="adv_state_")
    prompt, key = cases_cp3.setup(case, ws, fixtures)
    before = snapshot(ws)
    res = run_agent(sub, ws, prompt, case["session"], env, state, int(os.environ.get("ADV_CP3_LIMIT", "300")))
    pts, mx, rows = cases_cp3.grade(case, ws, key)
    after = snapshot(ws)
    extra = sorted(p for p in after if p not in before and p not in ("tally.py", "REPORT.json"))
    changed = sorted(p for p in before if after.get(p) != before[p])
    rows = list(rows)
    if res["contract_error"]:
        rows.insert(0, ("output contract", False, res["contract_error"]))
    if changed:
        rows.append(("inputs unchanged", False, "changed/deleted: %s" % changed[:10]))
    info = {"extra_files": extra[:20]}
    if keep:
        log("%s workspace kept at %s" % (case["id"], ws))
    else:
        cleanup(ws, state)
    return dict(passed=(pts == mx and not res["contract_error"]), infra=is_infra(res), points=pts, max=mx,
                prompt=prompt, result=res, info=info,
                checks=[dict(desc=a, ok=b, detail=c) for a, b, c in rows], workspace=ws if keep else None)


# ------------------------------------------------------------------ driver
GATE = threading.Lock()
LIVE = {"on": True}


def run_case(case, sub, env, reruns, keep, fixtures):
    attempts = []
    infra_retries = 0
    while True:
        if LIVE["on"]:
            with GATE:
                wait_endpoint(env, log)
        log("%s start (attempt %d)" % (case["id"], len(attempts) + 1))
        t0 = time.time()
        if case["cp"] == "cp3":
            a = attempt_cp3(case, sub, env, keep, fixtures)
        else:
            a = attempt_conversation(case, sub, env, keep)
        a["seconds"] = round(time.time() - t0, 1)
        if a["infra"] and infra_retries < 6:
            infra_retries += 1
            log("%s INFRA (endpoint), retrying" % case["id"])
            time.sleep(30)
            continue
        attempts.append(a)
        extra = (" %d/%d" % (a["points"], a["max"])) if case["cp"] == "cp3" else ""
        log("%s %s%s in %.0fs" % (case["id"], "PASS" if a["passed"] else "FAIL", extra, a["seconds"]))
        if a["passed"] or case["cp"] == "cp3" or len(attempts) > reruns:
            break
    first = attempts[0]
    fails = sum(1 for a in attempts if not a["passed"])
    out = {k: v for k, v in case.items() if k not in ("files", "turns")}
    out["files"] = sorted(case.get("files", {}).keys())
    out.update(verdict="PASS" if first["passed"] else ("INFRA" if first["infra"] else "FAIL"),
               attempts=attempts, fail_count=fails, runs=len(attempts),
               stability=("deterministic" if fails == len(attempts) else "flaky") if fails else "stable")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission")
    ap.add_argument("--label")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--reruns", type=int, default=2)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--fixtures", help="dir with gen_logs.py (default: <submission>/tests/fixtures)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--resume", action="store_true", help="skip cases already recorded as PASS or FAIL")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.submission or not a.label:
        ap.error("--submission and --label are required")
    sub = os.path.abspath(a.submission)
    fixtures = a.fixtures or os.path.join(sub, "tests", "fixtures")
    env = load_env()
    cases = selected(all_cases(), a.only)
    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, a.label + ".json")
    prev = {}
    if os.path.exists(out_path):
        prev = {r["id"]: r for r in json.load(open(out_path)).get("results", [])}
    if a.resume:
        cases = [c for c in cases if prev.get(c["id"], {}).get("verdict") not in ("PASS", "FAIL")]
    log("running %d cases against %s" % (len(cases), sub))
    results = dict(prev)

    def save():
        with LOCK:
            data = dict(label=a.label, submission=sub, updated=time.strftime("%Y-%m-%d %H:%M:%S"),
                        results=[results[c["id"]] for c in all_cases() if c["id"] in results])
            with open(out_path + ".tmp", "w") as f:
                json.dump(data, f, indent=1)
            os.replace(out_path + ".tmp", out_path)

    def job(c):
        r = run_case(c, sub, env, a.reruns, a.keep, fixtures)
        with LOCK:
            results[c["id"]] = r
        save()
        return r

    # CP3 runs are long: start them first so they overlap the short cases.
    order = [c for c in cases if c["cp"] == "cp3"] + [c for c in cases if c["cp"] != "cp3"]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        rs = list(ex.map(job, order))
    bad = [r["id"] for r in rs if r["verdict"] != "PASS"]
    log("done: %d/%d passed; not passed: %s" % (len(rs) - len(bad), len(rs), bad))
    return 0


# ------------------------------------------------------------------ selftest
def selftest():
    """Catalog sanity plus two stub agents: one that does nothing and claims
    success, one that always errors. Every check must be callable, and every
    case with a real expectation must fail against the do-nothing stub."""
    import stat
    cases = all_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        if c["cp"] == "cp3":
            continue
        for t in c["turns"]:
            for chk in t["checks"]:
                assert callable(chk) and getattr(chk, "desc", None), (c["id"], chk)
    tmp = tempfile.mkdtemp(prefix="adv_stub_")
    stubs = {}
    for name, body in (("noop", '{"status": "success", "message": "ok"}', ),
                       ("err", '{"status": "error", "message": "cannot"}')):
        d = os.path.join(tmp, name)
        os.makedirs(d)
        p = os.path.join(d, "run_agent.sh")
        with open(p, "w") as f:
            f.write("#!/usr/bin/env bash\nprintf '%%s\\n' '%s'\nexit %d\n" % (body, 0 if name == "noop" else 1))
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        stubs[name] = d
    env = load_env(stub=True)
    LIVE["on"] = False
    weak = []
    for c in [c for c in cases if c["cp"] != "cp3"]:
        r = run_case(c, stubs["noop"], env, 0, False, None)
        if r["verdict"] == "PASS":
            weak.append(c["id"])
    print("cases: %d (cp1 %d, cp2 %d, cp3 %d)" % (len(cases), len(cases_cp1.CASES), len(cases_cp2.CASES),
                                                  len(cases_cp3.CASES)))
    print("cases a do-nothing 'success' stub passes (should be none or deliberate):", weak)
    # CP3 setup + grading against an empty workspace
    for c in cases_cp3.CASES:
        ws = make_workspace({})
        prompt, key = cases_cp3.setup(c, ws, os.environ.get("ADV_FIXTURES", ""))  if not c.get("dev_generator") \
            else (None, None)
        if prompt is None:
            cleanup(ws)
            continue
        pts, mx, rows = cases_cp3.grade(c, ws, key)
        print("cp3 %s: empty workspace scores %d/%d (want 0)" % (c["id"], pts, mx))
        cleanup(ws)
    cleanup(tmp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
