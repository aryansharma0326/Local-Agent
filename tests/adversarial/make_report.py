#!/usr/bin/env python3
"""Build REPORT.md (grader-style results) and CASES.md (the catalog) from
results/<label>.json files and the case modules.

  python3 tests/adversarial/make_report.py --labels audit-fix,audit-86a82b
"""

import argparse
import json
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cases_cp1  # noqa: E402
import cases_cp2  # noqa: E402
import cases_cp3  # noqa: E402

CP1_TYPES = ["create", "delete", "read", "summary"]
CP2_CATS = ["context", "isolation", "csv"]
NAMES = {"create": "Create a file", "delete": "Delete a file", "read": "Read and answer",
         "summary": "Multi-file summary", "context": "Context within a session", "isolation": "Session isolation",
         "csv": "CSV operations", "programming": "Programming", "debugging": "Debugging"}


def q(s):
    if not re.search(r'["$`\\!]', s):
        return '"%s"' % s
    return "'" + s.replace("'", "'\\''") + "'"


def setup_lines(case):
    lines = ['task_dir="$(mktemp -d)"', 'cd "$task_dir"',
             "mkdir -p unrelated/empty && head -c 1024 /dev/urandom > unrelated/sentinel.bin"]
    src = {c["id"]: c for c in cases_cp1.CASES + cases_cp2.CASES}.get(case["id"])
    if not src:
        return lines
    for rel, content in sorted(src["files"].items()):
        d = os.path.dirname(rel)
        pre = "mkdir -p %s && " % q(d) if d else ""
        if content is None:
            lines.append("mkdir -p %s" % q(rel))
            continue
        b = content if isinstance(content, bytes) else content.encode("utf-8")
        try:
            t = b.decode("utf-8")
            text_ok = "\r" not in t and not t.startswith("﻿") and len(b) <= 1500
        except UnicodeDecodeError:
            text_ok = False
        if text_ok and t.endswith("\n") and "EOF" not in t:
            lines.append("%scat > %s <<'EOF'\n%sEOF" % (pre, q(rel), t))
        elif text_ok and t == "":
            lines.append("%s: > %s" % (pre, q(rel)))
        else:
            lines.append("# %s: %d bytes (binary, CRLF, BOM, no final newline or large) - create it with:" % (rel, len(b)))
            lines.append("#   python3 tests/adversarial/runner.py --submission \"$SUBMISSION_DIR\" --label repro "
                         "--only %s --keep" % case["id"])
    return lines


def repro(case):
    out = ['SUBMISSION_DIR="/absolute/path/to/your/submission"'] + setup_lines(case)
    if case["cp"] == "cp3":
        out.append("# workspace: generated inputs + TASK.md (see tests/adversarial/cases_cp3.py); easiest:")
        out.append("python3 tests/adversarial/runner.py --submission \"$SUBMISSION_DIR\" --label repro --only %s "
                   "--keep" % case["id"])
        return "\n".join(out)
    for t in case["attempts"][0]["turns"]:
        out.append('bash "$SUBMISSION_DIR/run_agent.sh" %s%s' % (q(t["prompt"]), (" " + t["sid"]) if t["sid"] else ""))
    return "\n".join(out)


def cp1_score(passed_types):
    return {4: 3, 3: 2, 2: 1}.get(passed_types, 0)


def summarize(res, tags=None):
    by = {}
    for r in res:
        if tags and r["tag"] not in tags:
            continue
        by.setdefault((r["cp"], r["group"]), []).append(r)
    return by


def score_block(res, tags=None):
    by = summarize(res, tags)
    rows, cp1_ok, cp2_pts = [], 0, 0
    for g in CP1_TYPES:
        rs = by.get(("cp1", g), [])
        p = sum(1 for r in rs if r["verdict"] == "PASS")
        ok = bool(rs) and p == len(rs)
        cp1_ok += ok
        rows.append(("CP1", NAMES[g], "%d/%d" % (p, len(rs)), "pass" if ok else "**FAIL**"))
    for g in CP2_CATS:
        rs = by.get(("cp2", g), [])
        p = sum(1 for r in rs if r["verdict"] == "PASS")
        ok = bool(rs) and p == len(rs)
        cp2_pts += ok
        rows.append(("CP2", NAMES[g], "%d/%d" % (p, len(rs)), "pass" if ok else "**FAIL**"))
    cp3 = []
    for g in ("programming", "debugging"):
        for r in by.get(("cp3", g), []):
            a = r["attempts"][0]
            cp3.append((r["id"], g, a.get("points", 0), a.get("max", 0)))
    return rows, cp1_score(cp1_ok), cp2_pts, cp3


def endpoint_errors(stderr):
    return len(re.findall(r"HTTP (?:429|5\d\d|400 from)|SSLError|connection failure|Server is busy", stderr or ""))


def observed(case):
    a = case["attempts"][0]
    out = []
    if case["cp"] == "cp3":
        res = a["result"]
        out.append("Exit code %s after %ss, %s model calls; message: `%s`" % (
            res["code"], res["elapsed"], res.get("calls"), (res["message"] or res["stdout"])[:300].replace("`", "'")))
        out.append("Score: **%d/%d**. Endpoint errors the agent retried through during the run: %d." % (
            a["points"], a["max"], endpoint_errors(res.get("stderr_tail"))))
        for c in a["checks"]:
            if not c["ok"]:
                out.append("- FAIL %s: %s" % (c["desc"], c["detail"][:400]))
        if a.get("info", {}).get("extra_files"):
            out.append("- extra files left in the workspace: %s" % a["info"]["extra_files"])
        return "\n".join(out)
    for i, t in enumerate(a["turns"], 1):
        res = t["result"]
        bad = [c for c in t["checks"] if not c["ok"]]
        flag = "FAIL" if bad else "ok"
        out.append("- Turn %d (session %s) [%s]: exit %s, status `%s`, message: `%s`" % (
            i, t["sid"] or "-", flag, res["code"], res["status"],
            (res["message"] if res["message"] is not None else res["stdout"])[:300].replace("`", "'")
            .replace("\n", " ")))
        for c in bad:
            out.append("    - failed check: %s -> %s" % (c["desc"], c["detail"][:400].replace("\n", " ")))
    return "\n".join(out)


def expected(case):
    if case["cp"] == "cp3":
        return "All %s graded items correct (see the failing items below)." % (
            "requirements" if case["group"] == "programming" else "REPORT.json keys")
    out = []
    for i, t in enumerate(case["attempts"][0]["turns"], 1):
        descs = [c["desc"] for c in t["checks"]]
        out.append("- Turn %d: %s" % (i, "; ".join(descs)))
    return "\n".join(out)


def load_analysis():
    p = os.path.join(HERE, "analysis.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def build_report(labels):
    data, second = {}, {}
    order = {c["id"]: i for i, c in enumerate(cases_cp1.CASES + cases_cp2.CASES + cases_cp3.CASES)}
    for l in labels:
        merged = {}
        for suffix in ("2", "", "-extra"):  # a later rerun ("<label>2") overrides the first pass
            p = os.path.join(HERE, "results", l + suffix + ".json")
            if os.path.exists(p):
                for r in json.load(open(p))["results"]:
                    merged.setdefault(r["id"], r)
        cat = {c["id"]: c for c in cases_cp1.CASES + cases_cp2.CASES + cases_cp3.CASES}
        for r in merged.values():  # tags and attack notes come from the current catalog
            if r["id"] in cat:
                r["tag"], r["attack"] = cat[r["id"]]["tag"], cat[r["id"]]["attack"]
        data[l] = sorted(merged.values(), key=lambda r: order.get(r["id"], 999))
        p = os.path.join(HERE, "results", l + "-cp3b.json")
        second[l] = {r["id"]: r for r in json.load(open(p))["results"]} if os.path.exists(p) else {}
    an = load_analysis()
    L = ["# Adversarial end-to-end report: HW1 checkpoints 1-3", ""]
    L += an.get("_intro", "").splitlines() + [""] if an.get("_intro") else []
    L += ["## How the cases were run", "",
          "- Each case gets a fresh workspace. It also holds the grader's unrelated `unrelated/sentinel.bin` and "
          "`unrelated/empty/`, and a fresh, empty session store.",
          "- Turns run in order through `bash run_agent.sh PROMPT [SESSION_ID]`, with the course endpoint and "
          "`Qwen/Qwen3-14B`.",
          "- Every turn is checked for:",
          "  - exactly one JSON object on stdout;",
          "  - an exit code that matches the status;",
          "  - the time limit (180 s, or 300 s for CP3);",
          "  - the expected status;",
          "  - the requested answer or file;",
          "  - no change to any file the request did not name.",
          "- A failed CP1 or CP2 case was rerun twice more:",
          "  - **deterministic** means it failed all 3 runs;",
          "  - **flaky** means it failed some of them.",
          "- The verdict and the scores use the first run, as a single grading run would.",
          "- Case tags:",
          "  - `grader-known` is one of the four failed hidden cases from the hw1-2 report, copied word for word.",
          "  - `grader-like` matches the published examples in wording and difficulty.",
          "  - `stress` deliberately goes beyond them.", ""]
    for l in labels:
        res = data[l]
        L += ["## Result: `%s`" % l, ""]
        for title, tags in (("All cases", None), ("Excluding `stress` cases", ("grader-known", "grader-like"))):
            rows, s1, s2, cp3 = score_block(res, tags)
            L += ["**%s**" % title, "", "| Checkpoint | Category | Cases passed | Category |", "|---|---|---|---|"]
            L += ["| %s | %s | %s | %s |" % r for r in rows]
            L += ["", "- CP1 score under the CP1 rules: **%d/3**" % s1,
                  "- CP2 score under the CP2 rules: **%d/3**" % s2]
            if cp3:
                L.append("- CP3 runs (raw points; the grader runs 1 programming and 1 debugging prompt, 12 points "
                         "each, 3 raw points = 1 course point):")
                for cid, g, p, m in cp3:
                    b = second[l].get(cid)
                    extra = (", second run %d/%d" % (b["attempts"][0]["points"], b["attempts"][0]["max"])) if b else ""
                    L.append("    - %s (%s): %d/%d%s" % (cid, g, p, m, extra))
            L.append("")
        fails = [r for r in res if r["verdict"] != "PASS"]
        L += ["Failed or partial cases: %s" % (", ".join("`%s`" % r["id"] for r in fails) or "none"), ""]
    if len(labels) > 1:
        L += ["## Branch comparison", "", "| Case | Tag | " + " | ".join("`%s`" % l for l in labels) + " |",
              "|---|---|" + "---|" * len(labels)]
        idx = {l: {r["id"]: r for r in data[l]} for l in labels}
        for c in cases_cp1.CASES + cases_cp2.CASES + cases_cp3.CASES:
            cells = []
            for l in labels:
                r = idx[l].get(c["id"])
                if not r:
                    cells.append("-")
                elif r["cp"] == "cp3":
                    a = r["attempts"][0]
                    cells.append("%d/%d" % (a["points"], a["max"]))
                else:
                    v = r["verdict"]
                    if v != "PASS":
                        v = "**%s** (%d/%d)" % (v, r["fail_count"], r["runs"])
                    elif r["fail_count"]:
                        v = "PASS (flaky %d/%d)" % (r["fail_count"], r["runs"])
                    cells.append(v)
            if len(set(cells)) > 1 or any("FAIL" in x or "/" in x and x.split("/")[0] != x.split("/")[-1]
                                          for x in cells):
                L.append("| %s | %s | %s |" % (c["id"], c["tag"], " | ".join(cells)))
        L += ["", "Rows are listed only where a case did not fully pass on some branch. A FAIL count such as "
              "(3/3) means the case failed that many of its runs.", ""]
    for l in labels:
        fails = [r for r in data[l] if r["verdict"] != "PASS" or (r["cp"] == "cp3" and
                                                                    r["attempts"][0]["points"] < r["attempts"][0]["max"])]
        if not fails:
            continue
        L += ["## Failed cases on `%s`" % l, ""]
        for r in fails:
            L += ["### `%s`: %s (%s, %s)" % (r["id"], NAMES.get(r["group"], r["group"]), r["cp"].upper(), r["tag"]),
                  "", "**What it attacks:** %s" % r["attack"], ""]
            note = an.get("%s@%s" % (r["id"], l)) or an.get(r["id"])
            if note:
                L += ["**Why it failed:** " + note, ""]
            if r["cp"] != "cp3":
                L += ["**Stability:** failed %d of %d runs (%s)." % (r["fail_count"], r["runs"], r["stability"]), ""]
            L += ["**Reproduce:**", "", "```bash", repro(r), "```", "", "**Expected:**", "", expected(r), "",
                  "**Observed:**", "", observed(r), ""]
    return "\n".join(L) + "\n"


def build_catalog():
    L = ["# Adversarial end-to-end case catalog", "",
         "These are the cases in `tests/adversarial/cases_cp1.py`, `cases_cp2.py` and `cases_cp3.py`. Each one runs "
         "in a fresh workspace that also holds `unrelated/sentinel.bin` and `unrelated/empty/`, and every turn "
         "also checks that no file outside the case's allowed set changed.", "",
         "To run them:", "", "```bash",
         'python3 tests/adversarial/runner.py --submission "$SUBMISSION_DIR" --label mine [--only cp2,CV-A03]',
         "```", ""]
    for cp, mod in (("CP1", cases_cp1), ("CP2", cases_cp2)):
        L += ["## %s" % cp, ""]
        for c in mod.CASES:
            L += ["### `%s`: %s (%s)" % (c["id"], NAMES[c["group"]], c["tag"]), "", c["attack"], ""]
            if c["files"]:
                L.append("Workspace: " + ", ".join("`%s`" % f for f in sorted(c["files"])))
                L.append("")
            for i, t in enumerate(c["turns"], 1):
                L.append("%d. `%s`%s  " % (i, t["prompt"].replace("`", "'")[:400],
                                           (" (session %s)" % t["sid"]) if t["sid"] else ""))
                exp = ["status " + t["expect"]] + [getattr(k, "desc", "?") for k in t["checks"]]
                L.append("   Expect: " + "; ".join(exp))
            if c["changes"]:
                L.append("")
                L.append("Allowed changes: " + ", ".join("`%s`" % x for x in c["changes"]))
            L.append("")
    L += ["## CP3", ""]
    for c in cases_cp3.CASES:
        L += ["### `%s`: %s (%s)" % (c["id"], NAMES[c["group"]], c["tag"]), "", c["attack"], ""]
        if c["kind"] == "prog":
            L += ["Prompt: the official `tally.py` task with requirements 7-12 %s. Graded per requirement against "
                  "`cp3/ref_tally.py` (see `cp3/prog_specs.py`)." % (
                      "hidden" if c["variant"] == "O" else "set to variant %s" % c["variant"]), ""]
        else:
            L += ["Logs: %s, seed %d, services %s, order %s. Graded per REPORT.json key (value and JSON type)." % (
                "submission's gen_logs.py (%s)" % c["scenarios"] if c["dev_generator"] else "cp3/gen_incident.py",
                c["seed"], "/".join(c["names"]), c["order"]), ""]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    a = ap.parse_args()
    labels = [l for l in a.labels.split(",") if l]
    with open(os.path.join(HERE, "REPORT.md"), "w") as f:
        f.write(build_report(labels))
    with open(os.path.join(HERE, "CASES.md"), "w") as f:
        f.write(build_catalog())
    print("wrote REPORT.md and CASES.md")


if __name__ == "__main__":
    main()
