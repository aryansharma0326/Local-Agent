"""JSON findings report for debugging tasks (DB-2, DB-6).

The harness owns the file: it is written with every key and a type-correct
placeholder at the start, rewritten after every report_set, and validated:
exact key set, JSON types (ints are real ints), every string value verbatim
in the evidence files, every int present as a number in the evidence.
"""

import json
import os
import re

import taskspec
from config import log

PLACEHOLDER = {"int": 0, "str": "", "auto": ""}


class Report:
    def __init__(self, sandbox, path, keys, expected_count, evidence_files, anchors=()):
        self.sb = sandbox
        self.path = path                       # workspace-relative
        self.order = [k for k, _ in keys]
        self.desc = dict(keys)
        self.types = {k: taskspec.key_type(k, d) for k, d in keys}
        self.expected = expected_count or len(self.order)
        self.values = {k: PLACEHOLDER[self.types[k]] for k in self.order}
        self.set_keys = set()
        self.evidence = evidence_files          # absolute paths of logs
        self.anchors = [a for a in anchors if a]  # identifiers named in the task (e.g. the order id)
        self._corpus = None
        self._lines = None

    # ------------------------------------------------------------ evidence
    def corpus(self):
        if self._corpus is None:
            parts = []
            for p in self.evidence:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        parts.append(f.read())
                except OSError:
                    pass
            self._corpus = "\n".join(parts)
        return self._corpus

    def records(self):
        """Log records: a line plus its continuation lines (indented stack frames,
        'Caused by:' lines), so evidence is linked per event, not per neighbourhood."""
        if self._lines is None:
            self._lines = []
            cont = re.compile(r"^(\s|Caused by|\.\.\. \d+ more|at [\w.$]+\(|[a-z]\w*(?:\.[\w$]+)+(?::|$))")
            for p in self.evidence:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.read().splitlines()
                except OSError:
                    continue
                recs = []
                for l in lines:
                    if recs and cont.match(l):
                        recs[-1] += "\n" + l
                    else:
                        recs.append(l)
                self._lines.append(recs)
        return self._lines

    WINDOW = 0

    def _near(self, value, others):
        """Does some log record containing `value` also contain one of `others`?"""
        for recs in self.records():
            for r in recs:
                if value in r and any(o in r for o in others if o != value):
                    return True
        return False

    def _linked_errors(self, limit=2):
        """Error/stack-trace log entries that mention a linked identifier (the
        evidence a root-cause answer should come from), stack traces with a
        'Caused by' first."""
        import profiling
        linked = [x for x in self.linked_values() if len(x) >= 4]
        hits = []
        for recs in self.records():
            for r in recs:
                if ("Caused by" in r or "Exception" in r or "ERROR" in r) and any(x in r for x in linked):
                    rank = (2 if "Caused by" in r else 0) + (1 if "Exception" in r else 0)
                    hits.append((-rank, len(hits), r))
        if not hits:
            return ""
        shown = [profiling.render_line(r, 900) if r.lstrip().startswith("{") else r[:900]
                 for _, _, r in sorted(hits)[:limit]]
        return (" Error entries that DO mention the request's identifiers (the innermost 'Caused by' usually names "
                "the responsible component):\n" + "\n---\n".join(shown))

    _FRAMEWORK = re.compile(r"(?:java|javax|jdk|sun|kotlin|kotlinx|scala|akka|reactor|io\.netty|org\.springframework|"
                            r"org\.apache|com\.zaxxer|com\.google\.common)\.")

    def _linked_cause_records(self):
        """Stack-trace records ('Caused by:') linked to the request, oldest first."""
        own = set(v for k, v in self.values.items() if self.is_component_key(k))
        linked = [x for x in self.linked_values() if len(x) >= 4 and x not in own]
        return [r for recs in self.records() for r in recs if "Caused by" in r and any(x in r for x in linked)]

    def _cause_of(self, record):
        """The class a record blames: a lock holder ("held by X"), else the first frame under the
        innermost 'Caused by' that is not a JDK/framework class."""
        tail = record[record.rindex("Caused by"):]
        m = re.search(r"\b(?:held|owned|locked|taken) by\s+`?([A-Za-z_][\w$]*)", tail)
        if m:
            return m.group(1)
        for f in re.finditer(r"\n\s*at\s+(?:[\w.]+/)?([\w.$]*?)\.?([A-Z][\w$]*)\.[\w$<>]+\(", tail):
            if not self._FRAMEWORK.match(f.group(1) + "."):
                return f.group(2)
        return None

    def cause_candidates(self):
        """Classes blamed by linked stack traces, most recent first (an older stall on the same
        worker that recovered is usually not this request's cause)."""
        out = []
        for r in reversed(self._linked_cause_records()):
            c = self._cause_of(r)
            if c and c not in out:
                out.append(c)
        return out

    def lock_names(self):
        """Lock names in the innermost 'Caused by' of linked stack traces ("lock 'sku-ledger#7' held by X")."""
        own = set(v for k, v in self.values.items() if self.is_component_key(k) or re.search(r"lock", k, re.I))
        linked = [x for x in self.linked_values() if len(x) >= 4 and x not in own]
        out = []
        for recs in self.records():
            for r in recs:
                if "Caused by" in r and any(x in r for x in linked):
                    for m in re.finditer(r"\block\s+['\"`]?([^\s'\"`]+)['\"`]?\s+(?:is\s+)?held\s+by", r[r.rindex("Caused by"):]):
                        if m.group(1) not in out:
                            out.append(m.group(1))
        return out

    def is_component_key(self, key):
        """The key asks for the responsible component/class (judged by its name first: 'lock_name' whose
        description mentions "the root-cause component was holding" is a lock, not a component)."""
        k = key.lower()
        if re.search(r"root[ _-]?cause|culprit|component|class", k):
            return True
        if re.search(r"lock|_id$|_ids$|_at$|time|_name$|status|code|count|_ms$|depth|cents|amount", k):
            return False
        return bool(re.search(r"root[ _-]?cause|culprit|component|class name", self.desc.get(key, "").lower()))

    def linked_values(self):
        """Values transitively linked to the task's anchors: a value is linked
        when it occurs within a few lines of an anchor or of a linked value."""
        cand = [v for k, v in self.values.items() if k in self.set_keys and isinstance(v, str) and len(v) >= 3]
        linked = set(a for a in self.anchors)
        changed = True
        while changed:
            changed = False
            for v in cand:
                if v not in linked and self._near(v, linked):
                    linked.add(v)
                    changed = True
        return linked

    def in_evidence(self, s):
        return bool(s) and s in self.corpus()

    def int_in_evidence(self, n):
        return re.search(r"(?<![\w.])%d(?![\d])" % n, self.corpus()) is not None

    # ------------------------------------------------------------ values
    def _coerce(self, key, value):
        t = self.types.get(key, "auto")
        if isinstance(value, bool):
            value = str(value).lower() if t != "int" else int(value)
        if t == "int":
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
                return int(value)
            if isinstance(value, str):
                m = re.fullmatch(r"\s*(-?\d+)\s*(ms|s|sec|seconds|milliseconds|bytes|x|times)?\s*", value, re.I)
                if m:
                    return int(m.group(1))
            return value
        if t == "str":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return str(value)
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        # auto: integral numbers -> int, numeric strings stay strings unless clearly a count
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    def set(self, values):
        lines = []
        for k, v in values.items():
            key = str(k).strip().strip("`\"'")
            if key not in self.values:
                if len(self.order) >= self.expected:
                    lines.append("%s: REJECTED - not one of the required keys (%s)" % (key, ", ".join(self.order)))
                    continue
                self.order.append(key)
                self.types[key] = taskspec.key_type(key, "")
                self.desc[key] = ""
            v = self._coerce(key, v)
            if isinstance(v, str):
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "`'\"":
                    v = v[1:-1]
                # "sched=s-20410" -> "s-20410": the task asks for the identifier, not the log's key=value token
                # (its example shape shows bare values). Only when the bare value is itself in the logs.
                m = re.fullmatch(r"([A-Za-z_][\w.-]{0,30})=(\S+)", v)
                if m and self.in_evidence(m.group(2)) and not re.search(r"key=value|key/value|pair",
                                                                       self.desc.get(key, ""), re.I):
                    v = m.group(2)
            self.values[key] = v
            self.set_keys.add(key)
            lines.append("%s = %s: %s" % (key, json.dumps(v, ensure_ascii=False), self.check(key)))
        self.write()
        missing = [k for k in self.order if k not in self.set_keys]
        lines.append("report written to %s; keys still unset: %s" % (self.path, ", ".join(missing) or "none"))
        return "\n".join(lines)

    def check(self, key):
        v, t = self.values[key], self.types.get(key, "auto")
        if key not in self.set_keys:
            return "not set yet"
        if t == "int" and not (isinstance(v, int) and not isinstance(v, bool)):
            return "WRONG TYPE: must be a JSON integer"
        if t == "str" and not isinstance(v, str):
            return "WRONG TYPE: must be a JSON string"
        if isinstance(v, str):
            if not v:
                return "EMPTY"
            if self.in_evidence(v):
                if re.search(r"lock", key, re.I) and not self.is_component_key(key):
                    names = self.lock_names()
                    if v in self.cause_candidates() or (names and v not in names):
                        return ("CHECK: %r is not the lock's name (it may be the class holding the lock or another "
                                "word from the line).%s" % (v, (" The stack traces linked to this request name the "
                                                                "lock(s): %s." % ", ".join(names[:3])) if names
                                                               else " Copy the lock's own name from the 'Caused by' "
                                                                    "line."))
                if self.is_component_key(key):
                    cands = self.cause_candidates()
                    if cands and v != cands[0]:
                        return ("CHECK: %r is not the class the failure chain blames. The most recent stack trace "
                                "linked to this request blames %s%s (the class holding the lock, or the first "
                                "application frame under the innermost 'Caused by'). A logger name or a frame higher "
                                "up is a symptom; use the culprit class spelled exactly as logged."
                                % (v, cands[0], (" (older linked traces: %s)" % ", ".join(cands[1:4])) if cands[1:]
                                   else ""))
                if self.anchors and len(v) >= 3 and v not in self.linked_values():
                    return ("UNLINKED (verify): %r occurs in the logs, but never in the same log entry as the failed "
                            "request's identifiers found so far (%s) - it may belong to another request/worker (a "
                            "decoy). Set the identifiers that connect it first, or keep it only if you are sure.%s"
                            % (v, ", ".join(sorted(self.linked_values())[:6]), self._linked_errors()))
                return "ok (found verbatim in the logs)"
            low = self.corpus().lower()
            if v.lower() in low:
                i = low.index(v.lower())
                return "NOT VERBATIM: the logs spell it %r - copy the exact spelling" % self.corpus()[i:i + len(v)]
            return "NOT FOUND verbatim in the logs - copy the value exactly as logged"
        if isinstance(v, int) and not isinstance(v, bool):
            if t == "auto":
                glued = re.search(r"(?<![\w-])([A-Za-z][A-Za-z_]*[-_#:])%d(?![\d.])" % v, self.corpus())
                if glued and not re.search(r"(?<![\w.#:-])%d(?![\w.])" % v, self.corpus()):
                    return ("CHECK: the logs write this as %r, never as the bare number %d - copy the value exactly as "
                            "logged (a JSON string)" % (glued.group(0), v))
            return "ok" if self.int_in_evidence(v) else "WARNING: the number %d does not appear in the logs" % v
        return "WARNING: unusual JSON type %s" % type(v).__name__

    def problems(self):
        out = []
        for k in self.order:
            c = self.check(k)
            if not c.startswith("ok"):
                out.append("%s: %s" % (k, c))
        return out

    def obj(self):
        return dict((k, self.values[k]) for k in self.order)

    def write(self):
        data = taskspec.dumps_report(self.obj())
        self.sb.create_file(self.path, data)

    def summary(self):
        return json.dumps(self.obj(), ensure_ascii=False)

    def file_ok(self):
        """The file on disk parses and has exactly our keys with JSON-legal types."""
        try:
            with open(os.path.join(self.sb.root, self.path), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            log("report file invalid: %s" % e)
            return False
        return isinstance(data, dict) and list(data) == self.order
