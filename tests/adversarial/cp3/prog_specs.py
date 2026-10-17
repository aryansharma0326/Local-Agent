"""tally.py programming variants, data generator and per-requirement graders
for the adversarial suite (TEST ONLY).

O: the official prompt word for word (7-12 hidden); only 1-6 are graded.
D, E: the official 1-6 plus invented hidden-style requirements 7-12 that differ
from the submission's own development variants (A/B/C).
"""

import csv
import io
import json
import os
import random
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ref_tally as R  # noqa: E402

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

HIDDEN = {
    "O": "7–12. *(hidden)*\n",
    "D": """7. **`--json`** — with `--json`, every command prints exactly one JSON object on one line instead of its normal
   output: `count` → `{"count": N}`; `total` → `{"total": "X.XX"}`, or `{"category": "name", "total": "X.XX"}` when
   `--category` is given (category in lowercase); `top` → `{"top": [{"category": "name", "amount": "X.XX"}, ...]}`;
   `range` → `{"from": "D1", "to": "D2", "count": K, "total": "X.XX"}`.
8. **invalid rows** — a row is valid only if it has exactly 4 fields, a real calendar date in `YYYY-MM-DD` form, a
   non-empty category and an amount of the form `-?DIGITS[.DIGITS]`. Every command skips invalid rows. Blank lines
   and lines identical to the header are ignored and are not invalid rows. `count --invalid` prints the number of
   invalid rows (`{"invalid": N}` with `--json`).
9. **`monthly [--category NAME]`** — prints one line per calendar month that has transactions (of that category,
   if given), oldest first, as `YYYY-MM<TAB>COUNT<TAB>AMOUNT`.
10. **`stats --category NAME`** — prints `min=X.XX max=X.XX avg=X.XX` for the amounts of that category
    (avg = total ÷ count). If the category has no transactions, print an error to stderr, nothing to stdout, and
    exit with status 1.
11. **errors** — if the CSV file does not exist, print an error to stderr, nothing to stdout, and exit with
    status 1. A missing or malformed argument (`top` without N, `top abc`, `top -1`, `range` without both dates,
    an unknown command) prints a usage message to stderr, nothing to stdout, and exits with status 2.
12. **exact rounding** — amounts may have more than two decimals; sums are computed exactly (no binary floating
    point error) and only the printed value is rounded, half-up, to two decimals. `-0.00` is never printed; print
    `0.00` instead.
""",
    "F": """7. **`monthly`** — `python3 tally.py monthly` prints one line per calendar month that has transactions, oldest
   first, as `YYYY-MM<TAB>AMOUNT`.
8. **`monthly --json`** — prints one JSON object on one line that maps each month (`"YYYY-MM"`) to its amount as a
   string with two decimals, keys in ascending order, e.g. `{"2025-01": "1234.50", "2025-02": "99.00"}`.
9. **`median --category NAME`** — prints the median amount of that category (for an even count, the mean of the two
   middle amounts), rounded half-up to two decimals; `0.00` if the category has no transactions.
10. **`daily YYYY-MM-DD`** — prints `count=K total=X.XX` for the transactions of that single date.
11. **`export PATH`** — writes a CSV file at PATH with the header `category,total` and one row per category
    (lowercase), sorted by category name, totals with two decimals; prints exactly `wrote PATH` and exits with 0.
12. **speed** — every command finishes within 2 seconds on the provided file (it has about 60,000 rows).
""",
    "E": """7. **date filters everywhere** — `count`, `total`, `top` and `bottom` also accept `--from YYYY-MM-DD` and/or
   `--to YYYY-MM-DD` (each optional, both ends inclusive) and then only consider transactions in that span.
8. **category lists** — `--category` accepts a comma-separated list such as `--category food,rent`; a
   transaction matches if its category equals any of them (case-insensitive, surrounding spaces ignored).
   `count --category ...` prints the number of matching transactions and `total --category ...` their sum.
9. **`bottom N`** — like `top N`, but the N categories with the smallest totals, smallest first; ties are sorted
   alphabetically ascending.
10. **whitespace** — surrounding whitespace in any field is ignored: ` Food ` is the category `food`, and
    ` 12.00 ` is the amount 12.00.
11. **amount formats** — an amount may carry a sign and/or a dollar sign: `12.00`, `+12.00`, `$12.00`, `-$3.50`
    and `-3.50` are all valid amounts.
12. **`--name=value`** — every option may also be written as `--name=value`, e.g. `--file=data.csv`,
    `--category=food`, `--from=2026-01-01`.
""",
}


def prompt(variant):
    return HEAD + HIDDEN[variant] + TAIL


# ---------------------------------------------------------------- data
CATS = ["food", "transport", "rent", "utilities", "health", "entertainment", "books", "gifts", "travel",
        "home & garden"]
WORDS = ["coffee", "market", "uber", "train", "pharmacy", "cinema", "kindle", "flowers", "hotel", "hardware",
         "lunch", "taxi", "power bill", "gym", "concert", "bookshop", "airfare", "nursery", "bakery", "parking"]


def _case(rng, c):
    r = rng.random()
    return c.upper() if r < 0.15 else (c.title() if r < 0.45 else c)


def generate(variant, seed=7, n=None):
    n = n or (60000 if variant == "F" else 40000)
    rng = random.Random(seed * 31 + ord(variant))
    start = date(2025, 1, 1)
    rows = []
    for _ in range(n):
        d = (start + timedelta(days=rng.randint(0, 545))).isoformat()
        desc = rng.choice(WORDS)
        if rng.random() < 0.15:
            desc += ", " + rng.choice(WORDS)
        if rng.random() < 0.03:
            desc = 'the "%s" special' % desc
        cat = _case(rng, rng.choice(CATS))
        cents = rng.randint(100, 25000) * (-1 if rng.random() < 0.05 else 1)
        if variant == "D" and rng.random() < 0.05:
            amt = "%s%d.%03d" % ("-" if cents < 0 else "", abs(cents) // 100, (abs(cents) % 100) * 10 + 5)
        else:
            amt = "%s%d.%02d" % ("-" if cents < 0 else "", abs(cents) // 100, abs(cents) % 100)
        if variant == "E":
            r = rng.random()
            neg = amt.startswith("-")
            body = amt.lstrip("-")
            if r < 0.2:
                amt = ("-$" if neg else "$") + body
            elif r < 0.3 and not neg:
                amt = "+" + body
            if rng.random() < 0.1:
                cat = "  %s " % cat
                amt = " %s " % amt
        rows.append([d, desc, cat, amt])
    extra = []
    if variant == "D":
        bad = [["2026-02-30", "bad date", "food", "5.00"], ["2026/03/01", "slash date", "food", "5.00"],
               ["2025-13-01", "month 13", "rent", "5.00"], ["2025-05-05", "word amount", "food", "abc"],
               ["2025-05-06", "empty amount", "food", ""], ["2025-05-07", "comma amount", "food", "1,234.00"],
               ["2025-05-08", "no category", "", "9.00"], ["2025-05-09", "three fields", "food"],
               ["2025-05-10", "five", "fields", "food", "1.00"], ["2025-05-11", "dollar", "food", "$4.00"]]
        for i in range(200):
            extra.append(bad[i % len(bad)])
    for r in extra:
        rows.insert(rng.randint(1, len(rows) - 1), r)
    # force a tie in `top` between books and gifts
    sem = "E" if variant == "E" else "D"
    tmp = _render(rows, variant, blank_and_header=False)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "wb") as f:
        f.write(tmp)
    parsed, _ = R.load(path, sem)
    os.remove(path)
    tot = {}
    for r in parsed:
        tot[r[2]] = tot.get(r[2], Decimal(0)) + r[3]
    diff = tot["books"] - tot["gifts"]
    low = "gifts" if diff > 0 else "books"
    rows.append(["2026-06-30", "tie maker", low, str(abs(diff))])
    return _render(rows, variant, blank_and_header=(variant == "D"))


def _render(rows, variant, blank_and_header):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n" if variant == "D" else "\n")
    w.writerow(R.HEADER)
    half = len(rows) // 2
    for k, r in enumerate(rows):
        if blank_and_header and k == half:
            buf.write("\r\n")
            w.writerow(R.HEADER)
        w.writerow(r)
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if variant == "E" else data


# ---------------------------------------------------------------- grading
SMALL = {
    "case": ("date,description,category,amount\n2026-01-05,\"coffee, large\",Food,3.50\n"
             "2026-01-06,train,transport,12.00\n2026-01-31,\"the \"\"best\"\" lunch\",FOOD,10.00\n"
             "2026-02-01,refund,food,-5.00\n2026-03-01,ticket,Transport,13.50\n"),
    "D_invalid": ("date,description,category,amount\r\n2026-01-05,ok,food,3.50\r\n2026-02-30,bad,food,1.00\r\n"
                  "2026-01-07,bad amt,food,abc\r\n2026-01-08,short,food\r\n\r\n"
                  "date,description,category,amount\r\n2026-01-09,ok2,rent,10.00\r\n2026-01-10,nocat,,2.00\r\n"
                  "2026-01-11,\"q, comma\",food,\"1,000.00\"\r\n"),
    "D_round": ("date,description,category,amount\n2026-01-01,a,x,10.005\n2026-01-02,b,x,0.1\n2026-01-03,c,x,0.2\n"
                "2026-01-04,d,y,-0.001\n2026-01-05,e,z,0.004\n2026-01-06,f,z,0.001\n"),
    "E_ws": ("date,description,category,amount\n2026-01-05, coffee , Food , 3.50 \n2026-01-06,train,  transport,12.00\n"
             "2026-01-07,lunch,food  ,  +4.00\n"),
    "E_amt": ("date,description,category,amount\n2026-01-05,a,food,$12.00\n2026-01-06,b,food,+12.00\n"
              "2026-01-07,c,food,-$3.50\n2026-01-08,d,food,-3.50\n2026-01-09,e,rent,12.00\n"),
}


def run_tally(ws, args, timeout=20):
    p = subprocess.run([sys.executable, "tally.py"] + args, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def grade(variant, ws):
    """-> list of (req, name, ok, detail); a requirement passes if all its checks pass."""
    out = []
    if not os.path.exists(os.path.join(ws, "tally.py")):
        return [(r, "tally.py exists", False, "missing") for r in range(1, 13 if variant != "O" else 7)]
    sem = {"O": "D", "D": "D", "E": "E", "F": "F"}[variant]
    tmp = tempfile.mkdtemp(prefix="adv_pg_")
    small = {}
    for k, v in SMALL.items():
        small[k] = os.path.join(tmp, k + ".csv")
        with open(small[k], "w", newline="") as f:
            f.write(v)

    def check(req, name, args, json_compare=False):
        want_out, want_code, want_err = R.run(sem, args, cwd=ws)
        try:
            code, so, se = run_tally(ws, args)
        except subprocess.TimeoutExpired:
            out.append((req, name, False, "timeout (20 s) args=%s" % args))
            return
        if json_compare and want_out:
            try:
                ok_out = json.loads(so) == json.loads(want_out) and so.count("\n") == 1
            except ValueError:
                ok_out = False
        else:
            ok_out = so == want_out
        ok = ok_out and code == want_code and (not want_err or bool(se.strip()))
        out.append((req, name, ok, "args=%s want=%r/exit %d got=%r/exit %d stderr=%r" % (
            args, want_out[:160], want_code, so[:160], code, se[:160])))

    rows, _ = R.load(os.path.join(ws, "transactions.csv"), sem)
    dates = sorted(set(r[0] for r in rows))
    d1, d2 = dates[len(dates) // 4].isoformat(), dates[len(dates) // 2].isoformat()
    cats = sorted(set(r[2] for r in rows))
    check(1, "count", ["count"])
    check(2, "total", ["total"])
    check(3, "total --category FOOD (upper)", ["total", "--category", "FOOD"])
    check(3, "total --category 'Home & Garden'", ["total", "--category", "Home & Garden"])
    check(3, "total --category unknown", ["total", "--category", "nonexistent"])
    check(4, "top 3", ["top", "3"])
    check(4, "top 100 (fewer categories, forced tie books/gifts)", ["top", "100"])
    check(5, "lowercase output (small file)", ["top", "5", "--file", small["case"]])
    check(5, "category MiXeD", ["total", "--category", "TrAnSpOrT", "--file", small["case"]])
    check(6, "range", ["range", "--from", d1, "--to", d2])
    check(6, "range single day", ["range", "--from", d1, "--to", d1])
    check(6, "range options reversed", ["range", "--to", d2, "--from", d1])
    check(6, "option before argument", ["total", "--file", small["case"], "--category", "food"])
    if variant == "D":
        check(7, "count --json", ["count", "--json"], True)
        check(7, "total --json", ["--json", "total"][::-1], True)
        check(7, "total --category --json", ["total", "--json", "--category", "Food"], True)
        check(7, "top --json", ["top", "3", "--json"], True)
        check(7, "range --json", ["range", "--from", d1, "--json", "--to", d2], True)
        check(8, "count --invalid", ["count", "--invalid"])
        check(8, "count (small invalid)", ["count", "--file", small["D_invalid"]])
        check(8, "count --invalid (small)", ["count", "--invalid", "--file", small["D_invalid"]])
        check(8, "count --invalid --json", ["count", "--invalid", "--json", "--file", small["D_invalid"]], True)
        check(8, "total (small invalid)", ["total", "--file", small["D_invalid"]])
        check(9, "monthly", ["monthly"])
        check(9, "monthly --category", ["monthly", "--category", "RENT"])
        check(10, "stats", ["stats", "--category", "Food"])
        check(10, "stats unknown category", ["stats", "--category", "nope"])
        check(11, "missing file", ["count", "--file", "missing.csv"])
        check(11, "top without N", ["top"])
        check(11, "top abc", ["top", "abc"])
        check(11, "top -1", ["top", "-1"])
        check(11, "range without --to", ["range", "--from", d1])
        check(11, "unknown command", ["frobnicate"])
        check(12, "exact sum 10.305 -> 10.31", ["total", "--category", "x", "--file", small["D_round"]])
        check(12, "-0.001 prints 0.00", ["total", "--category", "y", "--file", small["D_round"]])
        check(12, "0.005 total half-up", ["total", "--category", "z", "--file", small["D_round"]])
        check(12, "top with 3-decimal amounts", ["top", "3", "--file", small["D_round"]])
    if variant == "E":
        check(7, "count --from --to", ["count", "--from", d1, "--to", d2])
        check(7, "total --from only", ["total", "--from", d2])
        check(7, "top --to only", ["top", "3", "--to", d1])
        check(8, "count --category list", ["count", "--category", "food,rent"])
        check(8, "total --category list with spaces", ["total", "--category", "FOOD, Rent"])
        check(9, "bottom 3", ["bottom", "3"])
        check(9, "bottom 100", ["bottom", "100"])
        check(10, "whitespace total", ["total", "--file", small["E_ws"]])
        check(10, "whitespace top", ["top", "5", "--file", small["E_ws"]])
        check(11, "amount formats total", ["total", "--category", "food", "--file", small["E_amt"]])
        check(11, "amount formats count", ["count", "--file", small["E_amt"]])
        check(12, "--file=", ["count", "--file=" + small["E_amt"]])
        check(12, "--category=", ["total", "--category=food", "--file=" + small["E_amt"]])
        check(12, "--from= --to=", ["range", "--from=" + d1, "--to=" + d2])
    if variant == "F":
        import time as _time
        check(7, "monthly", ["monthly"])
        check(7, "monthly (small file)", ["monthly", "--file", small["case"]])
        check(8, "monthly --json", ["monthly", "--json"], True)
        check(8, "monthly --json option first", ["--json", "monthly"][::-1], True)
        check(9, "median odd count (small)", ["median", "--category", "transport", "--file", small["case"]])
        check(9, "median even count (small)", ["median", "--category", "FOOD", "--file", small["case"]])
        check(9, "median main file", ["median", "--category", "Books"])
        check(9, "median unknown", ["median", "--category", "zzz"])
        check(10, "daily", ["daily", d1])
        check(10, "daily with --file after", ["daily", "2026-01-05", "--file", small["case"]])
        exp_path = os.path.join(tmp, "exp_ref.csv")
        R.run(sem, ["export", exp_path], cwd=ws)
        want_csv = open(exp_path).read()
        try:
            code, so, se = run_tally(ws, ["export", "agent_export.csv"])
            got = open(os.path.join(ws, "agent_export.csv")).read() if os.path.exists(
                os.path.join(ws, "agent_export.csv")) else None
            ok = code == 0 and so == "wrote agent_export.csv\n" and got == want_csv
            out.append((11, "export", ok, "stdout=%r code=%d file=%r want=%r" % (so[:80], code, (got or "")[:160],
                                                                              want_csv[:160])))
            if os.path.exists(os.path.join(ws, "agent_export.csv")):
                os.remove(os.path.join(ws, "agent_export.csv"))
        except subprocess.TimeoutExpired:
            out.append((11, "export", False, "timeout"))
        for args in (["total"], ["top", "3"], ["monthly"], ["median", "--category", "food"]):
            t0 = _time.monotonic()
            try:
                run_tally(ws, args, timeout=10)
                dt = _time.monotonic() - t0
            except subprocess.TimeoutExpired:
                dt = 99
            out.append((12, "speed %s" % " ".join(args), dt <= 2.0, "%.2fs" % dt))
    return out


def score(results):
    reqs = {}
    for req, _, ok, _ in results:
        reqs[req] = reqs.get(req, True) and ok
    return reqs
