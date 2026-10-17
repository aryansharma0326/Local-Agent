"""Task classification. Heuristics only (no model call): the CP1/CP2 harness
is the default, and a prompt goes to the long-horizon CP3 loop only on
strong signals, so a misroute can never take a file/CSV/session task away
from the proven path.

  programming  write/fix a program (code deliverable + implement/tool/fix wording)
  debugging    investigate logs and write a JSON findings report
  simple       everything else (create/delete/read/summarize/CSV/session)
"""

import os
import re

_CODE_EXT = r"(?:py|js|ts|sh|rb|go|java|c|cpp|rs|pl|php)"
_CODE_FILE = re.compile(r"[\w./-]{1,200}\." + _CODE_EXT + r"\b")
_JSON_FILE = re.compile(r"[\w./-]{1,200}\.json\b", re.I)
_LITERAL = re.compile(r"\b(containing\s*:|with the (?:content|contents|text)\b|should contain\b|"
                      r"that says\b|the file should contain|with the following content)", re.I)

_PROG = [
    (re.compile(r"\bimplement\b[^\n]{0,40}`?[\w./-]{1,200}\." + _CODE_EXT + r"`?", re.I), 3),
    (re.compile(r"#\s*task\s*:\s*implement", re.I), 3),
    (re.compile(r"\bcommand[- ]line (?:tool|program|utility|interface)\b", re.I), 2),
    (re.compile(r"\b(?:write|create|build|develop)\s+(?:a|an)\s+(?:small\s+|simple\s+)?(?:python\s*3?\s+)?"
                r"(?:program|script|function|class|module|cli|tool|solution)\b", re.I), 2),
    (re.compile(r"\bdef\s+\w+\s*\(", re.I), 2),
    (re.compile(r"\b(?:fix|repair|debug)\b[^\n]{0,80}\b(?:tests?|bug|failing|fails|broken)\b", re.I), 2),
    (re.compile(r"\b(?:failing|fails)\b[^\n]{0,60}\btests?\b", re.I), 1),
    (re.compile(r"\bleetcode\b", re.I), 2),
    (re.compile(r"\bstandard library only\b", re.I), 1),
    (re.compile(r"^##\s*(?:requirements|invocation)\b", re.I | re.M), 1),
    (re.compile(r"\bunit tests?\b|\bpytest\b|\bunittest\b", re.I), 1),
]
_DEBUG = [
    (re.compile(r"\bREPORT\.json\b"), 3),
    (re.compile(r"\broot[ _-]cause\b", re.I), 2),
    (re.compile(r"\bincident\b", re.I), 1),
    (re.compile(r"\blogs?/|\.log\b", re.I), 1),
    (re.compile(r"\btrace(?:[ _-]id)?\b|\brequest id\b|\breq=", re.I), 1),
    (re.compile(r"\bfollow (?:this|the|one) (?:specific )?(?:failed )?request\b", re.I), 2),
    (re.compile(r"\bfinal deliverable\b", re.I), 1),
]


def _score(rules, text):
    return sum(w for rx, w in rules if rx.search(text))


def _task_md_matches(prompt, root):
    """The CP3 workspace holds a copy of the prompt as TASK.md."""
    p = os.path.join(root, "TASK.md")
    try:
        if not os.path.isfile(p) or os.path.getsize(p) > 200000:
            return False
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError:
        return False
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    a, b = norm(body), norm(prompt)
    return bool(a) and (a == b or (len(b) > 200 and (b[:200] in a or a[:200] in b)))


def _has_logs(root):
    try:
        for dp, dns, fns in os.walk(root):
            if dp[len(root):].count(os.sep) > 2:
                dns[:] = []
                continue
            if any(f.endswith((".log", ".jsonl")) for f in fns):
                return True
    except OSError:
        pass
    return False


def classify(prompt, root):
    text = prompt or ""
    prog = _score(_PROG, text)
    debug = _score(_DEBUG, text)
    literal = bool(_LITERAL.search(text))
    code_deliv = bool(_CODE_FILE.search(text)) or bool(re.search(r"\bdef\s+\w+\s*\(", text))
    json_deliv = bool(_JSON_FILE.search(text))
    cp3 = _task_md_matches(text, root)
    if cp3:
        prog += 2
        debug += 2 if json_deliv else 0
    if json_deliv and debug >= 4 and (cp3 or _has_logs(root) or "REPORT.json" in text) and debug >= prog:
        return "debugging"
    # A literal create ("Create notes.txt containing: implement parser.py ...") is file work whatever its
    # content says; a real programming task arrives with its TASK.md (cp3) or without literal-content wording.
    if code_deliv and prog >= 3 and not (literal and not cp3):
        return "programming"
    return "simple"
