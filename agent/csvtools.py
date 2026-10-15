"""Deterministic CSV operations. The model chooses the operation and its
arguments; this code does the reading, arithmetic and rewriting, so totals
are exact and untouched rows are preserved byte-for-byte.

Pure functions over text: no file access happens here.
"""

import csv
import io
import re
from decimal import Decimal, InvalidOperation, getcontext

from errors import AgentError

getcontext().prec = 40

OPERATORS = {
    "equals": "eq", "eq": "eq", "==": "eq", "=": "eq", "is": "eq", "equal": "eq",
    "not_equals": "ne", "ne": "ne", "!=": "ne", "<>": "ne", "is_not": "ne", "not_equal": "ne",
    "greater_than": "gt", "gt": "gt", ">": "gt", "above": "gt", "more_than": "gt",
    "greater_or_equal": "ge", "ge": "ge", ">=": "ge", "gte": "ge", "at_least": "ge",
    "less_than": "lt", "lt": "lt", "<": "lt", "below": "lt", "under": "lt",
    "less_or_equal": "le", "le": "le", "<=": "le", "lte": "le", "at_most": "le",
    "contains": "contains", "includes": "contains",
    "is_empty": "empty", "empty": "empty", "blank": "empty",
}


# ------------------------------------------------------------------ numbers
def parse_number(text):
    """'1,200' / '$3.50' / '45%' / '(12)' / ' 7 ' -> Decimal, else None."""
    s = (text or "").strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1].strip()
    s = s.lstrip("$€£¥₹").rstrip("%").strip()
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    if not re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def fmt_number(d):
    """Plain decimal notation: 300 not 300.0, at most 10 decimals, no exponent."""
    d = d.quantize(Decimal("1e-10")) if d != d.to_integral_value() else d.to_integral_value()
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def describe_number(d):
    """Value plus a 2-decimal rounding when the value has more decimals."""
    exact = fmt_number(d)
    if "." in exact and len(exact.split(".")[1]) > 2:
        return "%s (rounded to 2 decimals: %s)" % (exact, format(d.quantize(Decimal("0.01")), "f"))
    return exact


# ------------------------------------------------------------------ parsing
def split_records(text):
    """Split CSV text into raw records, each keeping its own line terminator.
    Newlines inside quoted fields do not end a record."""
    records, start, in_quotes, i, n = [], 0, False, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            in_quotes = not in_quotes
        elif not in_quotes and c in "\r\n":
            end = i + 2 if c == "\r" and i + 1 < n and text[i + 1] == "\n" else i + 1
            records.append(text[start:end])
            start = i = end
            continue
        i += 1
    if start < n:
        records.append(text[start:])
    return records


def _terminator(raw):
    m = re.search(r"(\r\n|\r|\n)$", raw)
    return m.group(1) if m else ""


def _key(name):
    return re.sub(r"[\s_\-]+", "", (name or "").strip().lower())


class Table:
    def __init__(self, text, name="the file"):
        self.name = name
        self.records = split_records(text)
        first = next((r for r in self.records if r.strip()), None)
        if first is None:
            raise AgentError("%s is empty; it has no header row." % name)
        self.delimiter = self._detect_delimiter(first)
        self.rows = []  # (record index, fields) for non-blank records, header first
        for i, raw in enumerate(self.records):
            if raw.strip():
                self.rows.append((i, self._parse(raw)))
        self.header = [h.strip() for h in self.rows[0][1]]
        body = first[:len(first) - len(_terminator(first))]
        self.quote_all = len(self.header) > 0 and all(
            f.strip().startswith('"') for f in self._split_top(body))
        self.newline = _terminator(first) or "\n"

    @staticmethod
    def _detect_delimiter(header_raw):
        counts, in_quotes = {",": 0, ";": 0, "\t": 0, "|": 0}, False
        for c in header_raw:
            if c == '"':
                in_quotes = not in_quotes
            elif not in_quotes and c in counts:
                counts[c] += 1
        best = max(counts, key=lambda k: counts[k])
        return best if counts[best] > 0 else ","

    def _split_top(self, raw):
        out, cur, in_quotes = [], "", False
        for c in raw:
            if c == '"':
                in_quotes = not in_quotes
            if c == self.delimiter and not in_quotes:
                out.append(cur)
                cur = ""
            else:
                cur += c
        return out + [cur]

    def _parse(self, raw):
        body = raw[:len(raw) - len(_terminator(raw))]
        try:
            return next(csv.reader(io.StringIO(body, newline=""), delimiter=self.delimiter))
        except (csv.Error, StopIteration):
            return body.split(self.delimiter)

    def _serialize(self, fields, terminator):
        buf = io.StringIO(newline="")
        # lineterminator "\r\n" (not ""), so older Pythons also quote fields containing newlines
        csv.writer(buf, delimiter=self.delimiter, lineterminator="\r\n",
                   quoting=csv.QUOTE_ALL if self.quote_all else csv.QUOTE_MINIMAL).writerow(fields)
        return buf.getvalue()[:-2] + terminator

    # -- lookups
    @property
    def data(self):
        return self.rows[1:]

    def column(self, name, role="column"):
        want = (name or "").strip()
        for i, h in enumerate(self.header):
            if h == want:
                return i
        hits = [i for i, h in enumerate(self.header) if _key(h) == _key(want)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise AgentError("%s: column '%s' is ambiguous; candidates: %s." % (
                self.name, name, ", ".join(self.header[i] for i in hits)))
        w = _key(want)
        if len(w) >= 3 and role != "id":  # unique prefix: 'qty' -> 'qty_on_hand'
            pre = [i for i, h in enumerate(self.header) if _key(h).startswith(w)]
            if len(pre) == 1:
                return pre[0]
        if role == "id":  # 'id' may be spelled 'ID', 'record_id', 'Order Id', ...
            hits = [i for i, h in enumerate(self.header) if _key(h).endswith("id")]
            if len(hits) == 1 and _key(want) in ("id", "rowid", "recordid", ""):
                return hits[0]
        raise AgentError("%s has no column '%s'. Its columns are: %s."
                         % (self.name, name, ", ".join(self.header)))

    @staticmethod
    def cell(fields, idx):
        return fields[idx] if idx < len(fields) else ""

    def matching(self, col_idx, op, value):
        return [(ri, f) for ri, f in self.data if compare(self.cell(f, col_idx), op, value)]

    def text(self, records=None):
        return "".join(self.records if records is None else records)


def compare(cell, op, value):
    a, b = (cell or "").strip(), ("" if value is None else str(value)).strip()
    if op == "empty":
        return a == ""
    na, nb = parse_number(a), parse_number(b)
    numeric = na is not None and nb is not None
    if op in ("eq", "ne"):
        same = (na == nb) if numeric else (a.casefold() == b.casefold())
        return same if op == "eq" else not same
    if op == "contains":
        return b.casefold() in a.casefold()
    if not numeric:  # ordering on text: fall back to case-insensitive string order
        x, y = a.casefold(), b.casefold()
    else:
        x, y = na, nb
    return {"gt": x > y, "ge": x >= y, "lt": x < y, "le": x <= y}[op]


def normalize_operator(op):
    key = re.sub(r"[\s\-]+", "_", (op or "equals").strip().lower())
    if key not in OPERATORS:
        raise AgentError("Unknown operator '%s'. Use one of: equals, not_equals, contains, greater_than, "
                         "greater_or_equal, less_than, less_or_equal, is_empty." % op)
    return OPERATORS[key]


# --------------------------------------------------------------- operations
def describe(table):
    return "%d data rows; columns: %s" % (len(table.data), ", ".join(table.header))


def aggregate(table, column, operation, where_column=None, where_value=None):
    """Returns (value_text, sentence)."""
    idx = table.column(column)
    rows, scope = table.data, ""
    if where_column:
        widx = table.column(where_column)
        rows = [(ri, f) for ri, f in rows if compare(table.cell(f, widx), "eq", where_value)]
        scope = " where %s = %s" % (table.header[widx], where_value)
    name = table.header[idx]
    if operation == "count":
        n = sum(1 for _, f in rows if table.cell(f, idx).strip() != "")
        return str(n), "count of non-empty %s values%s in %s = %d" % (name, scope, table.name, n)
    values, skipped = [], 0
    for _, f in rows:
        raw = table.cell(f, idx)
        d = parse_number(raw)
        if d is None:
            skipped += 1 if raw.strip() else 0
        else:
            values.append(d)
    if not values:
        raise AgentError("Column '%s' of %s has no numeric values%s." % (name, table.name, scope))
    if operation == "sum":
        result = sum(values, Decimal(0))
    elif operation == "average":
        result = sum(values, Decimal(0)) / Decimal(len(values))
    elif operation == "min":
        result = min(values)
    else:
        result = max(values)
    note = " (%d non-numeric cell(s) ignored)" % skipped if skipped else ""
    if operation in ("min", "max"):  # say which row holds the value, so "who/which" can be answered
        hits = [f for _, f in rows if parse_number(table.cell(f, idx)) == result]
        shown = ["{%s}" % ", ".join("%s=%s" % (h, table.cell(f, i)) for i, h in enumerate(table.header))[:300]
                 for f in hits[:3]]
        note += "; found in %d row(s): %s" % (len(hits), " ".join(shown))
    sentence = "%s of %s%s in %s over %d value(s) = %s%s" % (
        operation, name, scope, table.name, len(values), describe_number(result), note)
    return fmt_number(result), sentence


def row_sum(table, id_column, id_value, columns):
    """Add the values of several columns in the row(s) matching the id."""
    iidx = table.column(id_column, role="id")
    cidx = [table.column(c) for c in columns]
    rows = table.matching(iidx, "eq", id_value)
    if not rows:
        raise AgentError("No row in %s has %s = %s." % (table.name, table.header[iidx], id_value))
    if len(rows) > 1:
        raise AgentError("%d rows in %s have %s = %s; the row is ambiguous."
                         % (len(rows), table.name, table.header[iidx], id_value))
    fields = rows[0][1]
    parts, total = [], Decimal(0)
    for i in cidx:
        d = parse_number(table.cell(fields, i))
        if d is None:
            raise AgentError("The value of %s in that row ('%s') is not a number."
                             % (table.header[i], table.cell(fields, i)))
        parts.append("%s=%s" % (table.header[i], fmt_number(d)))
        total += d
    sentence = "row with %s = %s in %s: %s; sum = %s" % (
        table.header[iidx], id_value, table.name, ", ".join(parts), describe_number(total))
    return fmt_number(total), sentence


def update_cell(table, id_column, id_value, column, new_value):
    """Returns (new_text, sentence). Only the matching record(s) are rewritten."""
    iidx = table.column(id_column, role="id")
    cidx = table.column(column)
    rows = table.matching(iidx, "eq", id_value)
    if not rows:
        raise AgentError("No row in %s has %s = %s." % (table.name, table.header[iidx], id_value))
    records = list(table.records)
    old = []
    for ri, fields in rows:
        fields = list(fields) + [""] * (len(table.header) - len(fields))
        old.append(fields[cidx])
        fields[cidx] = "" if new_value is None else str(new_value)
        records[ri] = table._serialize(fields, _terminator(table.records[ri]))
    sentence = "set %s to '%s' (was '%s') in %d row(s) where %s = %s" % (
        table.header[cidx], new_value, "', '".join(old), len(rows), table.header[iidx], id_value)
    return table.text(records), sentence


def delete_rows(table, column, operator, value):
    """Returns (new_text, removed, kept, sentence). Kept records are untouched."""
    idx = table.column(column)
    op = normalize_operator(operator)
    doomed = set(ri for ri, _ in table.matching(idx, op, value))
    records = [r for i, r in enumerate(table.records) if i not in doomed]
    # If the last record was removed, do not leave the file ending differently than before.
    if records and table.records and not _terminator(table.records[-1]) and _terminator(records[-1]):
        records[-1] = records[-1][:len(records[-1]) - len(_terminator(records[-1]))]
    kept = len(table.data) - len(doomed)
    sentence = "removed %d of %d data row(s) where %s %s %s; %d row(s) remain" % (
        len(doomed), len(table.data), table.header[idx], operator or "equals",
        "" if op == "empty" else value, kept)
    return table.text(records), len(doomed), kept, sentence


# ------------------------------------------------------------ calculator
def evaluate(expression):
    """Safely evaluate + - * / ( ) and unary minus over decimal literals with
    Decimal arithmetic. Returns (Decimal, formatted text). Raises AgentError."""
    import ast
    expr = (expression or "").strip().replace("\u00d7", "*").replace("\u2212", "-").replace("x", "*") \
        if re.fullmatch(r"[\d\s.,+\-*/()x\u00d7\u2212$%]+", (expression or "").strip()) else (expression or "").strip()
    expr = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr).replace("$", "")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise AgentError("calculate accepts only numbers, + - * / and parentheses (got %r)" % expression)

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if b == 0:
                raise AgentError("division by zero")
            return a / b
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        num = getattr(n, "value", getattr(n, "n", None))
        if isinstance(n, (ast.Constant, getattr(ast, "Num", ast.Constant))) and isinstance(num, (int, float)) \
                and not isinstance(num, bool):
            return Decimal(str(num))
        raise AgentError("calculate accepts only numbers, + - * / and parentheses (got %r)" % expression)

    value = ev(tree)
    return value, describe_number(value)
