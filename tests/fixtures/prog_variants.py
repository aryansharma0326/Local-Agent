"""Programming-task spec variants and graders (TEST ONLY).

Each variant = the verbatim official tally.py prompt (requirements 1-6) plus
invented requirements 7-12 (the real ones are hidden). grade() runs the
agent's tally.py from the workspace and compares stdout/stderr/exit code with
the reference implementation, one check per requirement-level test.
"""

import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference"))
import tally_ref as R  # noqa: E402

HEAD = """# Task: implement `tally.py`

Write a command-line tool `tally.py` (Python 3, standard library only) in the current directory.
It summarises spending stored in a CSV file. A sample data file, `transactions.csv`, is provided
(it is large: about 40,000 rows).

## Invocation

```
python3 tally.py <command> [arguments] [--file PATH] [--json]
```

* `--file PATH` selects the CSV file. Default: `transactions.csv` in the current directory.
* Options may appear anywhere after the command name.

## CSV format

The first line is a header: `date,description,category,amount`. Every following line is one transaction.
Fields follow normal CSV quoting rules (a field may be wrapped in double quotes and contain commas).
`amount` is a decimal number of dollars (it may be negative, e.g. a refund).

## Requirements

Every requirement below is checked by an automated test. Output must match exactly
(no extra lines, no extra spaces). All money values are printed with exactly two decimals
and no thousands separator, e.g. `1234.50`, `-3.00`.

1. **`count`** — `python3 tally.py count` prints the number of valid transactions, e.g. `39876`.
2. **`total`** — `python3 tally.py total` prints the sum of all amounts, e.g. `1834512.07`.
3. **`total --category NAME`** (an option of the `total` command, not a separate command) — `python3 tally.py total --category NAME` prints the sum of amounts of
   transactions whose category equals NAME. The comparison is case-insensitive.
4. **`top N`** — `python3 tally.py top N` prints the N categories with the largest total amount, one per line,
   as `CATEGORY<TAB>AMOUNT`. Sort by amount descending; if two amounts are equal, sort those categories
   alphabetically ascending. If there are fewer than N categories, print all of them.
5. **category case** — categories are case-insensitive everywhere: `Food`, `FOOD` and `food` are the
   same category. Whenever a category name is printed, it is printed in lowercase.
6. **`range --from D1 --to D2`** — `python3 tally.py range --from YYYY-MM-DD --to YYYY-MM-DD` considers transactions whose
   date is between the two dates, both ends inclusive, and prints one line `count=K total=X.XX`.

"""

TAIL = """
When you are done, `tally.py` must be in the current directory.
Grading: each of the 12 requirements is checked by a hidden test and is worth 1 point.
"""

VARIANTS = {
    "OFFICIAL": "7–12. *(hidden)*\n",
    "A": """7. **`--json`** — with `--json`, `count` prints `{"count": N}`, `total` prints `{"total": "X.XX"}`, `top` prints a JSON list `[{"category": c, "amount": "X.XX"}, ...]` and `range` prints `{"count": K, "total": "X.XX"}`.
8. **invalid rows** — a row is valid only if it has exactly 4 fields, a real calendar date in `YYYY-MM-DD` form and an amount that is a decimal number. Invalid rows are skipped by every command.
9. **`months`** — `python3 tally.py months` prints one line per month that has transactions, `YYYY-MM<TAB>AMOUNT`, in ascending month order.
10. **missing file** — if the CSV file does not exist, print `error: file not found: PATH` to stderr (PATH as given), print nothing to stdout and exit with status 1.
11. **`avg --category NAME`** — prints the mean amount of that category with two decimals; `0.00` if it has no transactions.
12. **unknown command** — an unknown command prints a usage message to stderr and exits with status 2.
""",
    "B": """7. **`search TEXT`** — prints `count=K total=X.XX` for transactions whose description contains TEXT (case-insensitive).
8. **`categories`** — prints every distinct category once, one per line, sorted alphabetically.
9. **whitespace** — category names are trimmed of surrounding whitespace before comparing or printing.
10. **open ranges** — `range` with only `--from` or only `--to` is open-ended on the other side.
11. **`largest N`** — prints the N transactions with the largest amounts as `DATE<TAB>AMOUNT<TAB>DESCRIPTION`, largest first (ties: earlier date first, then file order).
12. **rounding** — amounts are rounded half-up to two decimals, and `-0.00` is never printed (print `0.00`).
""",
    "C": """7. **`--file=PATH`** — the file option may also be written as `--file=PATH`.
8. **empty data** — a file that is empty or contains only the header gives `count` = `0` and `total` = `0.00`.
9. **`top 0`** — prints nothing (no output at all) and exits with status 0.
10. **invalid N** — if N is not a non-negative integer, print an error to stderr and exit with status 2.
11. **reversed range** — `range` with `--from` later than `--to` prints `count=0 total=0.00`.
12. **invalid date argument** — a `--from`/`--to` value that is not a valid `YYYY-MM-DD` date prints an error to stderr and exits with status 2.
""",
}


def prompt(variant):
    return HEAD + VARIANTS[variant] + TAIL


# ------------------------------------------------------------- reference
def ref_rows(path):
    return R.load(path)


def m(d):
    return R.money(d)


def run_tally(ws, args, timeout=20):
    p = subprocess.run([sys.executable, "tally.py"] + args, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def write_small(path, text):
    with open(path, "w", newline="") as f:
        f.write(text)


SMALL = ('date,description,category,amount\n'
         '2026-01-05,"coffee, large",Food,3.50\n'
         '2026-01-06,train,transport,12.00\n'
         '2026-01-31,"the ""best"" lunch",FOOD,10.005\n'
         '2026-02-01,refund,food,-5.00\n'
         '2026-02-30,bad date,food,1.00\n'
         '2026-02-02,bad amount,food,abc\n'
         '2026-02-03,short,food\n'
         '\n'
         '2026-03-01,ticket,Transport,13.50\n')
# valid rows: food 3.50 + 10.005 - 5.00 = 8.505 -> 8.51 ; transport 12.00 + 13.50 = 25.50 ; total 34.005 -> 34.01


def grade(variant, ws):
    """[(test name, passed, detail)]"""
    out = []
    tally = os.path.join(ws, "tally.py")
    if not os.path.exists(tally):
        return [("tally.py exists", False, "missing")]
    rows = ref_rows(os.path.join(ws, "transactions.csv"))
    tmp = tempfile.mkdtemp(prefix="pg_small_")
    small = os.path.join(tmp, "small.csv")
    write_small(small, SMALL)

    def check(name, args, want_out, want_code=0, stderr_nonempty=None, cwd=ws):
        try:
            code, so, se = run_tally(cwd, args)
        except subprocess.TimeoutExpired:
            out.append((name, False, "timeout"))
            return
        ok = so == want_out and code == want_code and (stderr_nonempty is None or bool(se.strip()) == stderr_nonempty)
        out.append((name, ok, "args=%s want=%r/%d got=%r/%d stderr=%r" % (args, want_out[:200], want_code,
                                                                          so[:200], code, se[:200])))

    def ref(args, path=None):
        o, c = R.run(args + ["--file", path or os.path.join(ws, "transactions.csv")])
        return (o + "\n") if o else ""

    cats = sorted(set(r[2] for r in rows))
    some = cats[0]
    dates = sorted(set(r[0] for r in rows))
    d1, d2 = dates[len(dates) // 4].isoformat(), dates[len(dates) // 2].isoformat()
    check("1 count", ["count"], ref(["count"]))
    check("2 total", ["total"], ref(["total"]))
    check("3 total --category (upper)", ["total", "--category", some.upper()], ref(["total", "--category", some]))
    check("3b total --category nonexistent", ["total", "--category", "nonexistent"], "0.00\n")
    check("4 top 3", ["top", "3"], ref(["top", "3"]))
    check("4b top 100 (fewer cats, tie order)", ["top", "100"], ref(["top", "100"]))
    check("5 lowercase output / small file", ["top", "5", "--file", small], "transport\t25.50\nfood\t8.51\n")
    check("6 range", ["range", "--from", d1, "--to", d2], ref(["range", "--from", d1, "--to", d2]))
    check("6b range single day inclusive", ["range", "--from", d1, "--to", d1], ref(["range", "--from", d1, "--to", d1]))
    check("6c range reversed option order", ["range", "--to", d2, "--from", d1], ref(["range", "--from", d1, "--to", d2]))
    check("opt position: --file before args", ["total", "--file", small, "--category", "food"], "8.51\n")
    check("small count", ["count", "--file", small], "4\n" if False else "5\n")
    if variant == "A":
        n = len(rows)
        check("7a json count", ["count", "--json"], json.dumps({"count": n}) + "\n")
        tot = m(sum((r[3] for r in rows), Decimal(0)))
        code, so, se = run_tally(ws, ["total", "--json"])
        try:
            out.append(("7b json total", json.loads(so) == {"total": tot} and code == 0, so[:200]))
        except ValueError:
            out.append(("7b json total", False, so[:200]))
        code, so, se = run_tally(ws, ["top", "2", "--json", "--file", small])
        try:
            out.append(("7c json top", json.loads(so) == [{"category": "transport", "amount": "25.50"},
                                                           {"category": "food", "amount": "8.51"}], so[:200]))
        except ValueError:
            out.append(("7c json top", False, so[:200]))
        check("8 invalid rows skipped (small)", ["total", "--file", small], "34.01\n")
        months = {}
        for r in rows:
            k = r[0].strftime("%Y-%m")
            months[k] = months.get(k, Decimal(0)) + r[3]
        check("9 months", ["months"], "".join("%s\t%s\n" % (k, m(v)) for k, v in sorted(months.items())))
        check("10 missing file", ["count", "--file", "nope.csv"], "", 1, True)
        code, so, se = run_tally(ws, ["count", "--file", "nope.csv"])
        out.append(("10b missing file message", se.strip() == "error: file not found: nope.csv", se[:200]))
        check("11 avg", ["avg", "--category", "TRANSPORT", "--file", small], "12.75\n")
        check("11b avg none", ["avg", "--category", "zzz", "--file", small], "0.00\n")
        check("12 unknown command", ["frobnicate"], "", 2, True)
    elif variant == "B":
        check("7 search", ["search", "COFFEE", "--file", small], "count=1 total=3.50\n")
        write_small(os.path.join(tmp, "sp.csv"), "date,description,category,amount\n2026-01-01,a, Food ,1.00\n"
                    "2026-01-02,b,food,2.00\n2026-01-03,c,Bills ,3.00\n")
        check("8 categories", ["categories", "--file", small], "food\ntransport\n")
        check("9 trimmed categories", ["categories", "--file", os.path.join(tmp, "sp.csv")], "bills\nfood\n")
        check("9b trimmed total", ["total", "--category", "food", "--file", os.path.join(tmp, "sp.csv")], "3.00\n")
        check("10 open range from", ["range", "--from", "2026-02-01", "--file", small], "count=2 total=8.50\n")
        check("10b open range to", ["range", "--to", "2026-01-31", "--file", small], "count=3 total=25.51\n")
        check("11 largest", ["largest", "2", "--file", small], "2026-03-01\t13.50\tticket\n2026-01-06\t12.00\ttrain\n")
        write_small(os.path.join(tmp, "z.csv"), "date,description,category,amount\n2026-01-01,a,x,0.004\n"
                    "2026-01-02,b,x,-0.004\n2026-01-03,c,y,2.345\n")
        check("12 no -0.00", ["total", "--category", "x", "--file", os.path.join(tmp, "z.csv")], "0.00\n")
        check("12b half-up", ["total", "--category", "y", "--file", os.path.join(tmp, "z.csv")], "2.35\n")
    elif variant == "C":
        check("7 --file=PATH", ["count", "--file=" + small], "5\n")
        write_small(os.path.join(tmp, "h.csv"), "date,description,category,amount\n")
        check("8 header-only count", ["count", "--file", os.path.join(tmp, "h.csv")], "0\n")
        check("8b header-only total", ["total", "--file", os.path.join(tmp, "h.csv")], "0.00\n")
        check("9 top 0", ["top", "0"], "", 0)
        check("10 invalid N", ["top", "x"], "", 2, True)
        check("11 reversed range", ["range", "--from", "2026-12-01", "--to", "2026-01-01"], "count=0 total=0.00\n")
        check("12 invalid date arg", ["range", "--from", "2026-02-30", "--to", "2026-03-01"], "", 2, True)
    # performance
    import time
    t0 = time.monotonic()
    run_tally(ws, ["total"])
    out.append(("perf total < 2s", time.monotonic() - t0 < 2.0, "%.2fs" % (time.monotonic() - t0)))
    return out
