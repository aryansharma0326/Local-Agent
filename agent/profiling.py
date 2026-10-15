"""Deterministic data inspection used before the model reasons about data:
CSV anomaly profiles (CV-2, PG-2), log statistics and word-boundary grep
(DB-3). Pure functions over files; output is compact text for the prompt.
"""

import csv
import datetime
import io
import json
import os
import re
from decimal import Decimal, InvalidOperation

MAX_EXAMPLES = 5
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATEISH = re.compile(r"^\s*(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})\s*$")
_NUM = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)$")


def read_text(path, limit=None):
    with open(path, "rb") as f:
        data = f.read() if limit is None else f.read(limit)
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text, bom


def _valid_iso(s):
    try:
        datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
        return True
    except ValueError:
        return False


def _num(s):
    t = s.strip()
    if not _NUM.match(t):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _examples(items):
    return ", ".join(items[:MAX_EXAMPLES]) + (" ..." if len(items) > MAX_EXAMPLES else "")


def csv_profile(path, display=None, facts=None):
    """Text profile of a CSV file: format, field-count distribution, malformed
    lines, per-column types, invalid values and case/whitespace variants.
    If `facts` (a dict) is given it receives machine-usable values:
    "dates" (sorted distinct valid ISO dates) and "categories"
    ({column: [normalised values, most frequent first]})."""
    name = display or os.path.basename(path)
    facts = facts if facts is not None else {}
    facts.setdefault("categories", {})
    try:
        size = os.path.getsize(path)
        text, bom = read_text(path)
    except OSError as e:
        return "%s: cannot read (%s)" % (name, e)
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    sample = text[:8192]
    try:
        delim = csv.Sniffer().sniff(sample.split("\n", 1)[0] + "\n", delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    out = ["CSV profile of %s: %d bytes, %s line endings%s, delimiter %r" % (
        name, size, "CRLF" if crlf > lf else "LF", ", UTF-8 BOM" if bom else "", delim)]
    if not rows:
        out.append("  (empty file)")
        return "\n".join(out)
    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    out.append("  header (%d columns): %s" % (len(header), ",".join(rows[0])))
    raw_lines = text.splitlines()
    out.append("  first lines: " + " | ".join(l[:120] for l in raw_lines[1:4]))
    counts, blank, rep_header, wrong = {}, [], [], []
    good = []
    line_no = 1
    for r in body:
        line_no += 1
        if not r or all(not c.strip() for c in r):
            blank.append(str(line_no))
            continue
        if [c.strip() for c in r] == header:
            rep_header.append(str(line_no))
            continue
        counts[len(r)] = counts.get(len(r), 0) + 1
        if len(r) != len(header):
            wrong.append("%d(%d fields)" % (line_no, len(r)))
            continue
        good.append((line_no, r))
    out.append("  data rows: %d (excluding header); rows with %d fields: %d" % (
        len(body), len(header), len(good)))
    if blank:
        out.append("  blank lines: %d (rows %s)" % (len(blank), _examples(blank)))
    if rep_header:
        out.append("  repeated header lines: %d (rows %s)" % (len(rep_header), _examples(rep_header)))
    if wrong:
        out.append("  rows with the wrong number of fields: %d (rows %s)" % (len(wrong), _examples(wrong)))
    quoted = sum(1 for l in raw_lines[1:] if '"' in l)
    if quoted:
        out.append("  lines containing double quotes (quoted fields): %d" % quoted)
    for ci, col in enumerate(header):
        vals = [(ln, r[ci]) for ln, r in good]
        if not vals:
            continue
        empty = [str(ln) for ln, v in vals if not v.strip()]
        nonempty = [(ln, v) for ln, v in vals if v.strip()]
        iso = [v for _, v in nonempty if _ISO_DATE.match(v.strip())]
        nums = [v for _, v in nonempty if _num(v) is not None]
        line = "  column %r:" % col
        if nonempty and len(iso) >= 0.5 * len(nonempty):
            bad_fmt = ["%d:%r" % (ln, v) for ln, v in nonempty if not _ISO_DATE.match(v.strip())]
            bad_cal = ["%d:%r" % (ln, v) for ln, v in nonempty if _ISO_DATE.match(v.strip()) and not _valid_iso(v.strip())]
            spaced = [str(ln) for ln, v in nonempty if v != v.strip()]
            ok = sorted(v.strip() for _, v in nonempty if _ISO_DATE.match(v.strip()) and _valid_iso(v.strip()))
            if "dates" not in facts:
                facts["dates"] = sorted(set(ok))
            line += " date YYYY-MM-DD; valid %d, range %s..%s" % (len(ok), ok[0] if ok else "-", ok[-1] if ok else "-")
            if bad_fmt:
                line += "; NOT YYYY-MM-DD: %d (%s)" % (len(bad_fmt), _examples(bad_fmt))
            if bad_cal:
                line += "; impossible calendar dates: %d (%s)" % (len(bad_cal), _examples(bad_cal))
            if spaced:
                line += "; with surrounding spaces: %d" % len(spaced)
        elif nonempty and len(nums) >= 0.5 * len(nonempty):
            ds = [_num(v) for v in nums]
            bad = ["%d:%r" % (ln, v) for ln, v in nonempty if _num(v) is None]
            neg = sum(1 for d in ds if d < 0)
            many_dec = [v.strip() for v in nums if "." in v and len(v.strip().split(".")[1]) > 2]
            spaced = sum(1 for _, v in nonempty if v != v.strip())
            line += " numeric; %d parse, min %s, max %s, negative %d" % (len(ds), min(ds), max(ds), neg)
            if many_dec:
                line += "; >2 decimals: %d (e.g. %s)" % (len(many_dec), _examples(many_dec))
            if bad:
                line += "; NOT numeric: %d (%s)" % (len(bad), _examples(bad))
            if spaced:
                line += "; with surrounding spaces: %d" % spaced
        else:
            groups = {}
            for _, v in nonempty:
                groups.setdefault(v.strip().lower(), {}).setdefault(v, 0)
                groups[v.strip().lower()][v] += 1
            line += " text; %d distinct (%d case-insensitive)" % (
                len(set(v for _, v in nonempty)), len(groups))
            if len(groups) <= 40:
                facts["categories"][col.strip().lower()] = sorted(groups, key=lambda g: -sum(groups[g].values()))
                parts = []
                for k in sorted(groups, key=lambda g: -sum(groups[g].values())):
                    variants = groups[k]
                    if len(variants) > 1:
                        parts.append("%s <- %s" % (k, ", ".join("%r x%d" % kv for kv in
                                                                sorted(variants.items(), key=lambda kv: -kv[1])[:6])))
                    else:
                        (v, n), = variants.items()
                        parts.append("%r x%d" % (v, n))
                line += "; values: " + "; ".join(parts)
            else:
                commas = sum(1 for _, v in nonempty if delim in v)
                if commas:
                    line += "; values containing %r: %d" % (delim, commas)
        if empty:
            line += "; EMPTY: %d (rows %s)" % (len(empty), _examples(empty))
        out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------------ logs
_TS_PATTERNS = [
    ("ISO-8601 with T", re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")),
    ("date time", re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?")),
    ("epoch ms", re.compile(r"\b1[5-9]\d{11}\b")),
    ("bracketed time", re.compile(r"\[\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}[^\]]*\]")),
]
_LEVEL = re.compile(r"\b(ERROR|WARN(?:ING)?|INFO|DEBUG|FATAL|CRITICAL|TRACE)\b")


def _signature(line):
    s = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", "<ts>", line)
    s = re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", s)
    s = re.sub(r"[\w.-]*\d[\w.-]*", "#", s)
    return s.strip()[:140]


def log_stats(path, display=None):
    name = display or os.path.basename(path)
    try:
        text, _ = read_text(path)
    except OSError as e:
        return "%s: cannot read (%s)" % (name, e)
    lines = text.splitlines()
    out = ["%s: %d lines, %d bytes" % (name, len(lines), len(text.encode("utf-8")))]
    jsonish = sum(1 for l in lines[:200] if l.lstrip().startswith("{"))
    if lines and jsonish > 0.5 * min(200, len(lines)):
        out.append("  format: JSON lines")
    found = []
    for label, rx in _TS_PATTERNS:
        hits = [rx.search(l) for l in lines[:500]]
        hits = [h.group(0) for h in hits if h]
        if hits:
            found.append("%s (e.g. %s)" % (label, hits[0]))
    if found:
        out.append("  timestamps: " + "; ".join(found))
    first = next((l for l in lines if l.strip()), "")
    last = next((l for l in reversed(lines) if l.strip()), "")
    out.append("  first line: %s" % first[:220])
    out.append("  last line:  %s" % last[:220])
    levels = {}
    sigs = {}
    for l in lines:
        m = _LEVEL.search(l)
        if m:
            lv = m.group(1)
            levels[lv] = levels.get(lv, 0) + 1
            if lv in ("ERROR", "FATAL", "CRITICAL"):
                k = _signature(l)
                sigs[k] = sigs.get(k, 0) + 1
    if levels:
        out.append("  levels: " + ", ".join("%s=%d" % kv for kv in sorted(levels.items(), key=lambda kv: -kv[1])))
    if sigs:
        top = sorted(sigs.items(), key=lambda kv: -kv[1])[:6]
        out.append("  top error shapes: " + " || ".join("%dx %s" % (n, k) for k, n in top))
    return "\n".join(out)


# ------------------------------------------------------------------ grep
_KV = re.compile(r"\b([A-Za-z_][\w.]{0,30})=(\"[^\"]{1,80}\"|[^\s,;)\]}\"']{1,80})")
_IDTOK = re.compile(r"\b(?=[\w-]*\d)[A-Za-z]{1,8}[-_][A-Za-z0-9][\w-]{2,40}\b")


def ids_in(lines, limit=30):
    seen = []
    for l in lines:
        for k, v in _KV.findall(l):
            t = "%s=%s" % (k, v.strip('"'))
            if t not in seen:
                seen.append(t)
        for t in _IDTOK.findall(l):
            if t not in seen and not any(s.endswith("=" + t) for s in seen):
                seen.append(t)
        if len(seen) >= limit:
            break
    return seen[:limit]


def looks_like_id(pattern):
    return bool(re.fullmatch(r"[\w.:-]+", pattern)) and bool(re.search(r"\d", pattern))


def compile_pattern(pattern, regex=False, ignore_case=False, word=None):
    if word is None:
        word = (not regex) and looks_like_id(pattern)
    body = pattern if regex else re.escape(pattern)
    if word:
        body = r"(?<![\w-])(?:%s)(?![\w-]|\.\d)" % body
    return re.compile(body, re.I if ignore_case else 0)


_SALIENT = re.compile(r"\b(ERROR|FATAL|CRITICAL|WARN(?:ING)?|SEVERE)\b|Exception|Caused by|Traceback|"
                      r"\b(fail(?:ed|ure)?|timeout|timed out|blocked|stalled|deadlock|refused|denied)\b", re.I)


def render_line(line, max_line=400):
    """A log line for display. A JSON-lines record is shown as key=value pairs
    with multi-line string values (stack traces) expanded on their own lines."""
    s = line
    if s.lstrip().startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            flat, blocks = [], []
            for k, v in obj.items():
                if isinstance(v, str) and "\n" in v:
                    blocks.append("    %s:\n" % k + "\n".join("        " + x for x in v.split("\n")))
                else:
                    flat.append("%s=%s" % (k, v if isinstance(v, str) else json.dumps(v)))
            s = "{json} " + " ".join(flat)
            if len(s) > max_line:
                s = s[:max_line] + " [...]"
            return s + ("\n" + "\n".join(blocks) if blocks else "")
    return s if len(s) <= max_line else s[:max_line] + " [...]"


def grep_files(files, rx, root, context=0, max_matches=40, max_line=400):
    """files: list of absolute paths. Returns (text, n_matches). When there are
    more matches than max_matches, error/warning/exception lines are shown
    first (they are what an investigation needs), then the rest in order."""
    hits = []  # (rel, lines, index)
    for path in files:
        try:
            text, _ = read_text(path)
        except OSError:
            continue
        lines = text.splitlines()
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        for i, l in enumerate(lines):
            if rx.search(l):
                hits.append((rel, lines, i))
    total = len(hits)
    if not total:
        return "no matches", 0
    max_matches = max(5, max_matches)
    if context:
        max_matches = min(max_matches, max(4, 120 // (2 * context + 1)))  # keep the output readable
    chosen = hits
    note = ""
    if total > max_matches:
        salient = [h for h in hits if _SALIENT.search(h[1][h[2]])]
        rest = [h for h in hits if not _SALIENT.search(h[1][h[2]])]
        chosen = salient[:max_matches] + sorted(rest[:max(0, max_matches - len(salient))],
                                                key=lambda h: (h[0], h[2]))
        note = (" (showing %d: %d error/warning-like lines first, then others; narrow with regex=true, e.g. "
                "'ID.*ERROR', or pass a path)" % (max_matches, min(len(salient), max_matches)))
    out, last_key = [], None
    for rel, lines, i in chosen:
        lo, hi = max(0, i - context), min(len(lines), i + context + 1)
        if context and out:
            out.append("--")
        for j in range(lo, hi):
            mark = ":" if j == i else "-"
            s = render_line(lines[j], max_line)
            out.append("%s:%d%s %s" % (rel, j + 1, mark, s))
    # stack-trace continuation lines right after a shown match (multi-line entries)
    head = "%d matching line(s)%s" % (total, note)
    ids = ids_in([h[1][h[2]] for h in chosen])
    tail = ("\nidentifiers in the matching lines: " + ", ".join(ids)) if ids else ""
    return head + "\n" + "\n".join(out) + tail, total
