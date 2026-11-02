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
prog("PG-R", "R", "grader-like", "Requirements 9 and 11 as the hw1-3 grading report quotes them, with its exact "
     "commands: formatted amounts the sample data never shows, and a different exit status per error.")
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
    if case["kind"] == "words":
        with open(os.path.join(ws, "book.txt"), "w") as f:
            f.write(BOOK)
        with open(os.path.join(ws, "TASK.md"), "w") as f:
            f.write(WORDS_PROMPT)
        return WORDS_PROMPT, None
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
    if case["kind"] == "words":
        return _wordstat_grade(ws)
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

# ---------------------------------------------------------------- word-count task (agent-dev practice task)
WORDS_PROMPT = """# Task: implement `wordstat.py`

Write a command-line tool `wordstat.py` (Python 3, standard library only) in the current directory.
It reports statistics about a UTF-8 text file. A sample file, `book.txt`, is provided.

## Invocation

```
python3 wordstat.py <command> [arguments] [--file PATH]
```

* `--file PATH` selects the text file. Default: `book.txt`. Options may appear anywhere after the command.

## Requirements

A *word* is a maximal run of letters (a-z, A-Z) and apostrophes; words are compared case-insensitively and
printed in lowercase.

1. **`lines`** — `python3 wordstat.py lines` prints the number of lines in the file, e.g. `120`.
2. **`words`** — `python3 wordstat.py words` prints the number of words.
3. **`top N`** — `python3 wordstat.py top N` prints the N most frequent words as `WORD<TAB>COUNT`, most frequent
   first; equal counts are sorted alphabetically. Fewer than N distinct words: print all.
4. **`find WORD`** — `python3 wordstat.py find WORD` prints the 1-based numbers of the lines containing WORD (as a
   whole word, case-insensitive), comma-separated on one line, e.g. `3,17,40`; if none, print `none`.

When you are done, `wordstat.py` must be in the current directory.
"""

BOOK = "".join(["The cat sat on the mat.\n", "A dog, a cat and THE bird!\n", "\n",
                "It's the cat's toy; it's not the dog's.\n"] * 30 + ["Zebra quietly ended the story.\n"])




def _wordstat_grade(ws):
    import re as _re

    def words(text):
        return [w.lower() for w in _re.findall(r"[A-Za-z']+", text)]

    def out(*argv):
        p = subprocess.run([sys.executable, "wordstat.py"] + list(argv), cwd=ws, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=20)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    rows = []
    if not os.path.exists(os.path.join(ws, "wordstat.py")):
        return 0, 4, [("wordstat.py exists", False, "missing")]
    w = words(BOOK)
    counts = {}
    for x in w:
        counts[x] = counts.get(x, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = BOOK.split("\n")
    hits = [str(i + 1) for i, line in enumerate(lines) if "zebra" in words(line)]
    with open(os.path.join(ws, "mini.txt"), "w") as f:
        f.write("one two\nTwo three\n")
    checks = [
        ("req 1 lines", [out("lines")], [(0, "%d\n" % BOOK.count("\n"))]),
        ("req 2 words", [out("words")], [(0, "%d\n" % len(w))]),
        ("req 3 top", [out("top", "5"), (out("top", "999")[0], str(out("top", "999")[1].count("\n")))],
         [(0, "".join("%s\t%d\n" % kv for kv in ranked[:5])), (0, str(len(ranked)))]),
        ("req 4 find", [out("find", "ZEBRA"), out("find", "unicorn"), out("find", "two", "--file", "mini.txt")],
         [(0, ",".join(hits) + "\n"), (0, "none\n"), (0, "1,2\n")]),
    ]
    pts = 0
    for name, got, want in checks:
        ok = got == want
        pts += ok
        rows.append((name, ok, "got %r want %r" % ([g[1][:80] for g in got], [x[1][:80] for x in want])))
    os.remove(os.path.join(ws, "mini.txt"))
    return pts, 4, rows


debug("DB-S31", "grader-like", "Verification on an unseen seed (31) of the independent generator.", seed=31)
debug("DB-S32", "grader-like", "Verification on an unseen seed (32), renamed services.", seed=32,
      names=("edge", "checkout", "stock", "billing"), order="ORD-61007")

CASES.append(dict(id="PG-W", cp="cp3", group="programming", tag="grader-like", kind="words",
                  attack="The word-count practice task (wordstat.py, 4 requirements): a non-CSV program, used to "
                         "re-time the programming loop with the self-review and probes.", session="3990"))
