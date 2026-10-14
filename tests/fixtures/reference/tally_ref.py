"""Reference implementation of the tally.py spec (TEST ONLY - used to compute
expected outputs for grading the agent's generated program; it is never
reachable by the agent, which runs in a temp workspace with sandboxed tools).

Validity rules (requirements 1-6 plus the test variants' hidden rules):
a valid row has exactly 4 fields, an ISO date that exists, and an amount
matching -?digits[.digits]. Blank lines and repeated header lines are skipped.
Categories are compared trimmed and case-insensitively, printed lowercase.
Money: Decimal, ROUND_HALF_UP to 2 places, never -0.00.
"""

import csv
import datetime
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

AMOUNT = re.compile(r"^-?\d+(\.\d+)?$")


def money(d):
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == 0:
        q = Decimal("0.00")
    return "%s" % q


def valid_date(s):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return datetime.date(int(s[:4]), int(s[5:7]), int(s[8:]))
    except ValueError:
        return None


def load(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if not r or all(not c.strip() for c in r):
                continue
            if [c.strip().lower() for c in r] == ["date", "description", "category", "amount"]:
                continue
            if len(r) != 4:
                continue
            d = valid_date(r[0].strip())
            a = r[3].strip()
            if d is None or not AMOUNT.match(a):
                continue
            rows.append((d, r[1], r[2].strip().lower(), Decimal(a)))
    return rows


def parse(argv):
    opts, pos, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            opts["json"] = True
        elif a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            opts[k] = v
        elif a.startswith("--"):
            opts[a[2:]] = argv[i + 1] if i + 1 < len(argv) else None
            i += 1
        else:
            pos.append(a)
        i += 1
    return opts, pos


def run(argv):
    """Returns (stdout, exit_code)."""
    if not argv:
        return "", 2
    cmd, (opts, pos) = argv[0], parse(argv[1:])
    rows = load(opts.get("file") or "transactions.csv")
    if cmd == "count":
        return "%d" % len(rows), 0
    if cmd == "total":
        c = opts.get("category")
        sel = [r for r in rows if c is None or r[2] == c.strip().lower()]
        return money(sum((r[3] for r in sel), Decimal(0))), 0
    if cmd == "top":
        n = int(pos[0])
        tot = {}
        for r in rows:
            tot[r[2]] = tot.get(r[2], Decimal(0)) + r[3]
        items = sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        return "\n".join("%s\t%s" % (k, money(v)) for k, v in items), 0
    if cmd == "range":
        lo = valid_date(opts["from"])
        hi = valid_date(opts["to"])
        sel = [r for r in rows if lo <= r[0] <= hi]
        return "count=%d total=%s" % (len(sel), money(sum((r[3] for r in sel), Decimal(0)))), 0
    return "", 2


if __name__ == "__main__":
    out, code = run(sys.argv[1:])
    if out:
        print(out)
    sys.exit(code)
