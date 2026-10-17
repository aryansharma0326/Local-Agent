"""Reference semantics for the tally.py variants used by the adversarial suite
(TEST ONLY; never shown to the agent).

Variant "O": the official requirements 1-6 only.
Variant "D": 1-6 plus D's hidden 7-12 (json, invalid rows + count --invalid,
             monthly, stats, error handling, exact rounding).
Variant "E": 1-6 plus E's hidden 7-12 (date filters everywhere, category
             lists, bottom N, whitespace trimming, $/+ amounts, --opt=value).

run(variant, argv, cwd) -> (stdout, code, stderr_nonempty)
"""

import csv
import io
import json
import os
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

HEADER = ["date", "description", "category", "amount"]
AMT_D = re.compile(r"^-?\d+(\.\d+)?$")
AMT_E = re.compile(r"^([+-])?\$?(\d+(?:\.\d+)?)$")


def money(d):
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q == 0:
        q = Decimal("0.00")
    return str(q)


def parse_date(s):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


def load(path, variant):
    """-> (rows [(date, desc, cat, Decimal)], invalid_count)"""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig")
    rows, invalid = [], 0
    reader = csv.reader(io.StringIO(text, newline=""))
    first = True
    for rec in reader:
        if first:
            first = False
            continue
        if not rec or all(not c.strip() for c in rec):
            continue
        cells = [c.strip() for c in rec] if variant == "E" else rec
        if [c.strip().lower() for c in rec] == HEADER:
            continue
        if len(cells) != 4:
            invalid += 1
            continue
        d = parse_date(cells[0])
        cat = cells[2].strip().lower() if variant == "E" else cells[2].lower()
        if variant == "E":
            m = AMT_E.match(cells[3])
            amt = None if not m else Decimal(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        else:
            amt = Decimal(cells[3]) if AMT_D.match(cells[3]) else None
        if d is None or amt is None or (variant == "D" and not cat):
            invalid += 1
            continue
        rows.append((d, cells[1], cat, amt))
    return rows, invalid


class Usage(Exception):
    pass


class Fail(Exception):
    pass


def parse_args(argv):
    if not argv:
        raise Usage()
    cmd, pos, opts = argv[0], [], {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            name = a[2:]
            if "=" in name:
                name, val = name.split("=", 1)
                opts[name] = val
            elif name in ("json", "invalid"):
                opts[name] = True
            else:
                if i + 1 >= len(argv):
                    raise Usage()
                opts[name] = argv[i + 1]
                i += 1
        else:
            pos.append(a)
        i += 1
    return cmd, pos, opts


def cats_match(cat, spec, variant):
    if spec is None:
        return True
    if variant == "E":
        return cat in [c.strip().lower() for c in spec.split(",")]
    return cat == spec.lower()


def run(variant, argv, cwd="."):
    try:
        out = _run(variant, argv, cwd)
        return (out + "\n") if out != "" else "", 0, False
    except Usage:
        return "", 2, True
    except Fail:
        return "", 1, True


def _ranked(rows):
    tot = {}
    for r in rows:
        tot[r[2]] = tot.get(r[2], Decimal(0)) + r[3]
    return tot


def _run(variant, argv, cwd):
    cmd, pos, opts = parse_args(argv)
    path = opts.get("file", "transactions.csv")
    full = path if os.path.isabs(path) else os.path.join(cwd, path)
    if not os.path.isfile(full):
        raise Fail()
    rows, invalid = load(full, variant)
    js = bool(opts.get("json"))

    def dfilter(rs, frm, to):
        lo = parse_date(frm) if frm else None
        hi = parse_date(to) if to else None
        if (frm and lo is None) or (to and hi is None):
            raise Usage()
        return [r for r in rs if (lo is None or r[0] >= lo) and (hi is None or r[0] <= hi)]

    if variant == "E" and cmd in ("count", "total", "top", "bottom"):
        rows = dfilter(rows, opts.get("from"), opts.get("to"))

    if cmd == "count":
        if variant == "D" and opts.get("invalid"):
            return json.dumps({"invalid": invalid}) if js else str(invalid)
        sel = [r for r in rows if cats_match(r[2], opts.get("category"), variant)] if variant == "E" else rows
        return json.dumps({"count": len(sel)}) if js else str(len(sel))
    if cmd == "total":
        cat = opts.get("category")
        s = sum((r[3] for r in rows if cats_match(r[2], cat, variant)), Decimal(0))
        if js:
            return json.dumps({"category": cat.lower(), "total": money(s)} if cat else {"total": money(s)})
        return money(s)
    if cmd in ("top", "bottom"):
        if not pos:
            raise Usage()
        try:
            n = int(pos[0])
        except ValueError:
            raise Usage()
        if n < 0:
            raise Usage()
        tot = _ranked(rows)
        if cmd == "top":
            items = sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        else:
            items = sorted(tot.items(), key=lambda kv: (kv[1], kv[0]))[:n]
        if js:
            lst = [{"category": c, "amount": money(a)} for c, a in items]
            return json.dumps({"top": lst} if variant == "D" else lst)
        return "\n".join("%s\t%s" % (c, money(a)) for c, a in items)
    if cmd == "range":
        frm, to = opts.get("from"), opts.get("to")
        if not frm or not to:
            raise Usage()
        sel = dfilter(rows, frm, to)
        s = sum((r[3] for r in sel), Decimal(0))
        if js:
            return json.dumps({"from": frm, "to": to, "count": len(sel), "total": money(s)})
        return "count=%d total=%s" % (len(sel), money(s))
    if variant == "F" and cmd == "monthly":
        months = {}
        for r in rows:
            k = r[0].strftime("%Y-%m")
            months[k] = months.get(k, Decimal(0)) + r[3]
        if js:
            return json.dumps(dict((k, money(v)) for k, v in sorted(months.items())))
        return "\n".join("%s\t%s" % (k, money(v)) for k, v in sorted(months.items()))
    if variant == "F" and cmd == "median":
        cat = opts.get("category")
        if not cat:
            raise Usage()
        vals = sorted(r[3] for r in rows if r[2] == cat.lower())
        if not vals:
            return "0.00"
        n = len(vals)
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        return money(med)
    if variant == "F" and cmd == "daily":
        if not pos:
            raise Usage()
        d = parse_date(pos[0])
        if d is None:
            raise Usage()
        sel = [r for r in rows if r[0] == d]
        return "count=%d total=%s" % (len(sel), money(sum((r[3] for r in sel), Decimal(0))))
    if variant == "F" and cmd == "export":
        if not pos:
            raise Usage()
        tot = _ranked(rows)
        out = os.path.join(cwd, pos[0]) if not os.path.isabs(pos[0]) else pos[0]
        with open(out, "w", newline="") as f:
            f.write("category,total\n" + "".join("%s,%s\n" % (c if "," not in c else '"%s"' % c, money(a))
                                                  for c, a in sorted(tot.items())))
        return "wrote %s" % pos[0]
    if variant == "D" and cmd == "monthly":
        cat = opts.get("category")
        months = {}
        for r in rows:
            if cats_match(r[2], cat, variant):
                k = r[0].strftime("%Y-%m")
                c, a = months.get(k, (0, Decimal(0)))
                months[k] = (c + 1, a + r[3])
        return "\n".join("%s\t%d\t%s" % (k, c, money(a)) for k, (c, a) in sorted(months.items()))
    if variant == "D" and cmd == "stats":
        cat = opts.get("category")
        if not cat:
            raise Usage()
        vals = [r[3] for r in rows if r[2] == cat.lower()]
        if not vals:
            raise Fail()
        avg = sum(vals, Decimal(0)) / len(vals)
        return "min=%s max=%s avg=%s" % (money(min(vals)), money(max(vals)), money(avg))
    raise Usage()
