"""Seeded generator for transactions.csv-style data with anomalies (TEST ONLY).

  python3 gen_transactions.py --rows 40000 --seed 1 --out DIR [--crlf] [--bom] [--spaces]

Writes DIR/transactions.csv. Mostly valid rows plus: quoted descriptions with
commas and escaped quotes, mixed-case categories, optional surrounding spaces
on categories, negative refunds, amounts like 12.5 / -3 / 0.005, an injected
tie in category totals, and invalid rows: impossible or wrongly formatted
dates, unparsable amounts, wrong field counts, blank lines and a repeated
header line.
"""

import argparse
import os
import random

CATS = ["food", "transport", "rent", "utilities", "entertainment", "health", "shopping", "travel",
        "education", "gifts"]
WORDS = ["coffee", "train", "groceries", "cinema", "pharmacy", "books", "hotel", "taxi", "lunch", "gym",
         "phone", "internet", "concert", "flowers", "bakery", "museum"]


def variant(cat, rng, spaces):
    r = rng.random()
    c = cat.capitalize() if r < 0.2 else cat.upper() if r < 0.3 else cat
    if spaces and rng.random() < 0.05:
        c = " " + c + " "
    return c


def amount(rng):
    r = rng.random()
    if r < 0.05:
        return "-%d.%02d" % (rng.randint(1, 80), rng.randint(0, 99))
    if r < 0.07:
        return "%d.5" % rng.randint(1, 50)
    if r < 0.08:
        return "-%d" % rng.randint(1, 20)
    if r < 0.085:
        return "0.005"
    return "%d.%02d" % (rng.randint(1, 400), rng.randint(0, 99))


def description(rng):
    r = rng.random()
    if r < 0.08:
        return '"%s, %s and more"' % (rng.choice(WORDS), rng.choice(WORDS))
    if r < 0.1:
        return '"the ""%s"" special"' % rng.choice(WORDS)
    return "%s %d" % (rng.choice(WORDS), rng.randint(1, 99))


def generate(rows, seed, spaces=False):
    rng = random.Random(seed)
    lines = ["date,description,category,amount"]
    for i in range(rows):
        d = "2026-%02d-%02d" % (rng.randint(1, 12), rng.randint(1, 28))
        line = "%s,%s,%s,%s" % (d, description(rng), variant(rng.choice(CATS), rng, spaces), amount(rng))
        r = rng.random()
        if r < 0.0015:
            line = "%s,%s,%s,%s" % (rng.choice(["2026-02-30", "2026-13-01", "17/09/2026", "", "2026-9-7"]),
                                    description(rng), rng.choice(CATS), amount(rng))
        elif r < 0.003:
            line = "%s,%s,%s,%s" % (d, description(rng), rng.choice(CATS), rng.choice(["abc", "", "12.34.5", "$5"]))
        elif r < 0.0038:
            line = "%s,%s,%s" % (d, description(rng), rng.choice(CATS))
        elif r < 0.0042:
            line = "%s,%s,%s,%s,extra" % (d, description(rng), rng.choice(CATS), amount(rng))
        elif r < 0.0045:
            line = ""
        lines.append(line)
        if i == rows // 2:
            lines.append("date,description,category,amount")
    # a tie: two extra categories with identical totals
    lines.append("2026-06-15,tie a,zeta,1000.00")
    lines.append("2026-06-15,tie b,alpha,1000.00")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=40000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--crlf", action="store_true")
    ap.add_argument("--bom", action="store_true")
    ap.add_argument("--spaces", action="store_true")
    a = ap.parse_args()
    lines = generate(a.rows, a.seed, a.spaces)
    nl = "\r\n" if a.crlf else "\n"
    data = (nl.join(lines) + nl).encode("utf-8")
    if a.bom:
        data = b"\xef\xbb\xbf" + data
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "transactions.csv"), "wb") as f:
        f.write(data)


if __name__ == "__main__":
    main()
