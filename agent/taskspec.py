"""Parse a CP3-style task prompt: required deliverables (LOOP-11, PG-1),
report keys and their types (DB-1), example invocations to smoke-run, and
function signatures. Generic: nothing here knows a specific task.
"""

import json
import re

_CODE_EXT = r"(?:py|js|ts|sh|rb|go|java|c|cpp|rs|pl|php)"
_FILE = r"`?([\w./-]{0,200}[\w-]\.(?:%s|json|csv|txt|md))`?" % _CODE_EXT

_DELIV_PATTERNS = [
    r"\bimplement\s+`?([\w./-]{1,200}\.%s)`?" % _CODE_EXT,
    r"\b(?:write|create|build|develop|produce)\s+(?:a|an|the)?\s*(?:[\w-]+\s+){0,4}?`([\w./-]{1,200}\.%s)`" % _CODE_EXT,
    r"`([\w./-]{1,200}\.%s)`\s+must\s+be\b" % _CODE_EXT,
    r"\bfinal deliverable is (?:a file )?(?:named |called )?`?([\w./-]{1,200}\.\w{1,5})`?",
    r"\b(?:save|write|store)\s+(?:it|them|the \w+|your \w+)?\s*(?:to|as|in(?:to)?)\s+`?([\w./-]{1,200}\.\w{1,5})`?",
    r"\bfile named\s+`?([\w./-]{1,200}\.\w{1,5})`?",
    r"\b(?:fix|repair|debug)\s+`?([\w./-]{1,200}\.%s)`?" % _CODE_EXT,
    r"`([\w./-]{1,200}\.%s)`\s+(?:fails|is failing|has a bug|is broken|crashes)" % _CODE_EXT,
    r"\bdeliverable\b[^\n]{0,60}?`([\w./-]{1,200}\.\w{1,5})`",
]


def deliverables(prompt):
    """Workspace-relative paths the task asks the agent to produce/modify."""
    found = []
    for pat in _DELIV_PATTERNS:
        for m in re.finditer(pat, prompt, re.I):
            p = m.group(1).strip("./") if m.group(1).startswith("./") else m.group(1)
            if p.lower() in ("task.md",) or p.startswith(("logs/",)):
                continue
            if p not in found:
                found.append(p)
    return found


def expected_key_count(prompt):
    m = re.search(r"exactly\s+(?:the\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
                  r"\s+keys", prompt, re.I)
    if not m:
        return None
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
    v = m.group(1).lower()
    return int(v) if v.isdigit() else words[v]


_ITEM = re.compile(r"^\s*(\d{1,2})[.)]\s+\**`([A-Za-z_][\w.-]*)`\**\s*(?:[—–:-]+|\()?\s*(.*)$")


def report_keys(prompt):
    """[(key, description)] from a numbered list of backticked keys, plus keys
    of an example-shape JSON block, in order."""
    keys, desc = [], {}
    lines = prompt.splitlines()
    i = 0
    while i < len(lines):
        m = _ITEM.match(lines[i])
        if m:
            k, d = m.group(2), m.group(3).strip()
            j = i + 1
            while j < len(lines) and lines[j].strip() and not re.match(r"^\s*(\d{1,2})[.)–-]", lines[j]) \
                    and lines[j].startswith((" ", "\t")):
                d += " " + lines[j].strip()
                j += 1
            if k not in desc:
                keys.append(k)
                desc[k] = d
            i = j
            continue
        i += 1
    for block in re.findall(r"```(?:json)?\s*\n(.*?)```", prompt, re.S):
        if "{" not in block:
            continue
        for k in re.findall(r'"([A-Za-z_][\w.-]*)"\s*:', block):
            if k not in desc:
                keys.append(k)
                desc[k] = ""
    return [(k, desc[k]) for k in keys]


_INT_HINT = re.compile(r"\b(count|number of|how many|integer|int\b|total number|amount of|ms\b|millisecond|"
                       r"seconds?\b|duration|latency|elapsed|attempts?|retries|retry count|depth|size\b|port\b|"
                       r"status code|http status|exit code|bytes\b|occurrences|times\b|cents\b)", re.I)
_STR_HINT = re.compile(r"\b(id\b|ids\b|identifier|name\b|named|path|timestamp|time\b|when\b|class|component|"
                       r"worker|service|host|endpoint|url|method|message|reason|thread|lock|key\b|version|"
                       r"intent|trace|string|spelled|exactly as)", re.I)
_INT_SUFFIX = re.compile(r"(_ms|_count|_seconds|_secs|_attempts|_retries|_depth|_size|_bytes|_port|_total|_cents|"
                         r"^count_|_status_code|_http_status)$", re.I)
_STR_SUFFIX = re.compile(r"(_id|_ids|_name|_path|_ts|_at|_time|_timestamp|_component|_class|_worker|_thread|"
                         r"_lock|_service|_host|_url|_method|_message|_reason|^worker$|^host$)$", re.I)


def key_type(key, description):
    """'int', 'str' or 'auto' for a report key (DB-1 type rules)."""
    if re.search(r"_(?:num|number|no)$|^num_", key, re.I):
        return "auto"  # an "order number" is often an identifier like ABC-123: keep the logged value's type
    if re.search(r"_code$", key, re.I) and not re.search(r"status|http|exit", key + " " + (description or ""), re.I):
        # "error code" may be INV_HOLD_TIMEOUT or 1003: take the logged value's own type
        return "auto"
    if _INT_SUFFIX.search(key):
        return "int"
    if _STR_SUFFIX.search(key):
        return "str"
    d = description or ""
    if _INT_HINT.search(d) and not re.search(r"\b(id|identifier|timestamp|name)\b", d, re.I):
        return "int"
    if _STR_HINT.search(d) or _STR_HINT.search(key.replace("_", " ")):
        return "str"
    return "auto"


# ------------------------------------------------------------ smoke commands
_CMD = re.compile(r"(?:^|[`\n$>]\s*)((?:python3?|py)\s+[\w./-]{1,200}\.py(?:[ \t]+[^`\n]*)?)", re.M)


def example_commands(prompt):
    """Concrete example invocations written in the task (inline code or blocks)."""
    out = []
    for m in _CMD.finditer(prompt):
        cmd = m.group(1).strip().rstrip(".,;:")
        if any(t in cmd for t in ("<", "[", "...", "…")):
            continue
        if cmd not in out:
            out.append(cmd)
    return out


_SIG = re.compile(r"(?:\*\*|^\s*(?:\d{1,2}[.)]|[*-])\s+)`([a-z][\w-]*(?: +(?:--[a-z][\w-]*|[A-Z][A-Z0-9_]*|YYYY-MM-DD|"
                  r"D\d|\[[^\]`]+\]))*)`", re.M)


def command_signatures(prompt, program):
    """Commands the task describes as bold/list-item code signatures, e.g.
    **`avg --category NAME`** -> 'python3 prog.py avg --category NAME'."""
    out = []
    for m in _SIG.finditer(prompt):
        sig = m.group(1).strip()
        if "[" in sig or sig.split()[0] in ("python", "python3", "py"):
            continue
        cmd = "python3 %s %s" % (program, sig)
        if cmd not in out:
            out.append(cmd)
    return out


def signatures(prompt):
    return re.findall(r"\bdef\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", prompt)


def checklist(prompt):
    """Numbered/bulleted requirement lines, for the pinned requirements list."""
    items = []
    for line in prompt.splitlines():
        s = line.strip()
        if re.match(r"^(\d{1,2}[.)]|[*-])\s+\S", s) and len(s) > 6:
            items.append(s[:300])
    return items[:40]


def dumps_report(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def requirement_items(prompt, limit=14):
    """The numbered requirements of a task ("1. **`count`** - prints ..."), one string each
    with continuation lines folded in; hidden placeholders ("7-12. *(hidden)*") are skipped."""
    text = prompt or ""
    m = re.search(r"^#+\s*requirements\b.*$", text, re.I | re.M)
    if m:
        text = text[m.end():]
    items, cur = [], None
    for line in text.split("\n"):
        if re.match(r"^#+\s", line) and items:
            break
        h = re.match(r"^\s{0,3}(\d{1,2})\.\s+(.*)$", line)
        if h:
            cur = [h.group(1), h.group(2).strip()]
            items.append(cur)
        elif cur is not None and line.strip() and line.startswith((" ", "\t")):
            cur[1] += " " + line.strip()
        elif not line.strip():
            cur = None
    out = []
    for n, body in items:
        if re.search(r"\(hidden\)", body, re.I):
            continue
        out.append("%s. %s" % (n, body if len(body) <= 420 else body[:417] + "..."))
    return out[:limit]
