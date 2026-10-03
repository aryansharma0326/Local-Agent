"""Deterministic guardrails the harness applies to the model's tool calls:
parsing, anti-hallucination path checks, literal-content preservation, and
word-limit parsing/enforcement. Pure functions, no I/O."""

import ast
import json
import re
import unicodedata

# ------------------------------------------------------------ JSON parsing
def _loads_lenient(chunk):
    """Parse one object: strict JSON first, then common slips (raw control
    characters, trailing commas, Python-style quotes/True/None). Literal
    parsing only; nothing is ever executed."""
    for attempt in (lambda c: json.loads(c), lambda c: json.loads(c, strict=False),
                    lambda c: json.loads(re.sub(r",\s*([}\]])", r"\1", c), strict=False),
                    lambda c: ast.literal_eval(c),
                    lambda c: ast.literal_eval(re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False",
                                               re.sub(r"\bnull\b", "None", c)))),
                    ):
        try:
            return attempt(chunk)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
    return None


def extract_json(text):
    """Return the first balanced JSON object in text as a dict, or None."""
    if not text:
        return None
    start = text.find("{")  # any code fence around the object is simply skipped
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    obj = _loads_lenient(text[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    return None


def parse_tool_call(text):
    """Accept {"tool": name, "args": {...}} and common variants. Returns
    (name, args) or raises ValueError with a message for the model."""
    obj = extract_json(text)
    if obj is None:
        raise ValueError("your reply did not contain a JSON object")
    if isinstance(obj.get("function"), dict):  # OpenAI-style nesting
        obj = obj["function"]
    name = obj.get("tool", obj.get("name", obj.get("action")))
    args = obj.get("args", obj.get("arguments", obj.get("parameters")))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            raise ValueError("'args' must be a JSON object")
    if args is None:
        args = {k: v for k, v in obj.items() if k not in ("tool", "name", "action")}
    if not isinstance(name, str) or not name:
        raise ValueError('missing "tool" field')
    if not isinstance(args, dict):
        raise ValueError("'args' must be a JSON object")
    return name, args


# ------------------------------------------------------------------- paths
_PATH_TOKEN = re.compile(r"[\w\-./\\]*\w\.[A-Za-z0-9]{1,10}\b|[\w\-]+(?:/[\w\-.]+)+")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def norm_path(p):
    p = nfc(p.strip().strip("`'\"").replace("\\", "/"))
    while p.startswith("./"):
        p = p[2:]
    return p


def prompt_paths(prompt):
    """Path-like tokens mentioned in the request."""
    out = []
    for m in _PATH_TOKEN.finditer(nfc(prompt)):
        tok = norm_path(m.group(0)).rstrip(".")
        if tok and tok not in out:
            out.append(tok)
    return out


def _token_re(word):
    """Whole-token match: not glued to letters, digits, '_', '-' or a '.ext'."""
    return r"(?<![\w.\-])" + re.escape(word) + r"(?![\w\-]|\.\w)"


def name_mentioned(path, prompt):
    """Loose check: the file's name (without extension) appears in the request."""
    base = norm_path(path).rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base[1:] else base
    return bool(stem) and stem.lower() in nfc(prompt).lower()


def exact_name_mentioned(path, prompt):
    """Strict check for destructive operations: the full file name appears in
    the request as a whole token (so 'todo.md' does not match 'todo-old.md')."""
    base = norm_path(path).rsplit("/", 1)[-1]
    return bool(base) and re.search(_token_re(base), nfc(prompt), re.I) is not None


def stem_mentioned(path, prompt):
    """The file name without its extension appears as a whole token
    ('Delete the README' names README.md)."""
    base = norm_path(path).rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base[1:] else base
    return bool(stem) and re.search(_token_re(stem), nfc(prompt), re.I) is not None


def dir_score(path, prompt):
    """How many of the path's directory names the request mentions."""
    parts = norm_path(path).split("/")[:-1]
    low = nfc(prompt)
    return sum(1 for d in parts if re.search(_token_re(d), low, re.I))


def conflicting_prompt_path(path, prompt, content=""):
    """If the request spells out a full path (with directories) for this file
    name but the model chose a different location, return the request's path.
    Paths that only appear inside the file content are ignored."""
    want = norm_path(path)
    base = want.rsplit("/", 1)[-1].lower()
    candidates = [t for t in prompt_paths(prompt)
                  if "/" in t and t.rsplit("/", 1)[-1].lower() == base and t not in (content or "")]
    if not candidates or any(t.lower() == want.lower() for t in candidates):
        return None
    return candidates[0]


# ------------------------------------------------- literal content fidelity
_QUOTE_PAIRS = [('"', '"'), ("\u201c", "\u201d"), ("'", "'"), ("\u2018", "\u2019"), ("`", "`")]
_JSON_ESCAPES = {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\f": "\\f", "\b": "\\b"}


def _loose(s):
    s = re.sub("[\"'\u201c\u201d\u2018\u2019`]", "", nfc(s))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip(".!?;:,")


def _quoted_spans(prompt):
    spans = []
    for o, c in _QUOTE_PAIRS:
        if o == c:  # same char both sides: require word boundaries around it
            pat = r"(?:(?<=\s)|^|(?<=:))" + re.escape(o) + r"(.+?)" + re.escape(c) + r"(?=\s|$|[.,;:!?)])"
        else:
            pat = re.escape(o) + r"(.+?)" + re.escape(c)
        for m in re.finditer(pat, prompt, re.S):
            spans.append(m.group(1))
    return spans


def _colon_span(prompt):
    m = re.search(r"\b(?:contain(?:s|ing)?|contents?(?: is| are| should be)?|text|reads?|says?)\s*:\s*(.+)$",
                  prompt, re.S | re.I)
    return m.group(1).strip() if m else None


def _reescape(content):
    """Undo JSON escapes the model applied by mistake, e.g. 'C:\new' written
    unescaped in JSON arrives as 'C:' + newline + 'ew'."""
    return "".join(_JSON_ESCAPES.get(ch, ch) for ch in content)


def _wrap(lead, span, trail):
    """Re-attach whitespace the model put around the content, unless the
    literal span from the request already carries its own."""
    return (lead if span == span.lstrip() else "") + span + (trail if span == span.rstrip() else "")


def content_between(prompt, start, end):
    """The exact request text from the first `start` through the last `end`
    (inclusive), or None. Lets the model reference long literal content
    instead of retyping it."""
    if not start or not end:
        return None
    i = prompt.find(start)
    if i < 0:
        return None
    j = prompt.rfind(end)
    if j < 0 or j + len(end) <= i:
        return None
    return prompt[i:max(j + len(end), i + len(start))]


def literal_candidates(prompt):
    """Passages of the request that are likely literal file content: quoted
    text, a 'containing: ...' tail, and the text after 'containing'/'with the
    text'/'that says' to the end of the request."""
    out = list(_quoted_spans(prompt))
    span = _colon_span(prompt)
    if span:
        out.append(span)
    m = re.search(r"\b(?:containing|contains|with (?:the )?(?:text|content|contents|words?|line)|that says|saying)"
                  r"\s*:?\s*(.+)$", prompt, re.S | re.I)
    if m:
        out.append(m.group(1).strip())
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def salvage_truncated_write(reply, prompt):
    """A reply cut off by the token limit while writing a file (typically a
    model stuck repeating long content). If its path is readable and exactly
    one literal passage of the request starts with the partial content,
    return a complete write_file call using that passage; else None."""
    if not re.search(r'"tool"\s*:\s*"write_file"', reply or ""):
        return None
    pm = re.search(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"', reply)
    cm = re.search(r'"(?:content|content_start)"\s*:\s*"((?:[^"\\]|\\.)*)', reply, re.S)
    if not pm or not cm:
        return None
    try:
        path = json.loads('"%s"' % pm.group(1))
        partial = cm.group(1)
        partial = partial[:len(partial) - 1] if partial.endswith("\\") else partial
        partial = json.loads('"%s"' % partial)
    except ValueError:
        return None
    head = partial.strip()[:60]
    if len(head) < 8:
        return None
    # A looping model overshoots, so only the beginning has to match.
    hits = [c for c in literal_candidates(prompt) if c.startswith(head)]
    if len(hits) != 1:
        return None
    return "write_file", {"path": path, "content": hits[0]}


def fix_content(content, prompt):
    """If the model slightly altered literal content (dropped punctuation,
    changed quotes/whitespace, broke backslashes), restore the exact span from
    the prompt. Text inside explicit quotes is authoritative; a 'containing:
    ...' tail is used only when the model's content does not appear verbatim.
    Leading/trailing whitespace the model chose (e.g. a requested trailing
    newline) is preserved."""
    if content == "" or not content.strip():
        return content
    core = content.strip()
    lead = content[:len(content) - len(content.lstrip())]
    trail = content[len(content.rstrip()):]
    variants = [core]
    if any(ch in core for ch in "\f\b") or re.search(r"[A-Za-z]:\\", prompt):
        variants.append(_reescape(core))
    targets = [_loose(v) for v in variants if _loose(v)]
    if not targets:
        return content
    for span in _quoted_spans(prompt):
        if _loose(span) in targets:
            return _wrap(lead, span, trail)
    if core in prompt:
        return content
    for v in variants[1:]:
        if v in prompt:  # the repaired text appears verbatim in the request
            return _wrap(lead, v, trail)
    span = _colon_span(prompt)
    if span is not None and _loose(span) in targets:
        return _wrap(lead, span, trail)
    return content


# ------------------------------------------------------- word/char limits
_NUM_WORDS = {
    "ten": 10, "fifteen": 15, "twenty": 20, "twenty-five": 25, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "seventy-five": 75, "eighty": 80, "ninety": 90,
    "one hundred": 100, "a hundred": 100, "hundred": 100, "one hundred fifty": 150,
    "one hundred and fifty": 150, "two hundred": 200, "two hundred fifty": 250,
    "three hundred": 300, "five hundred": 500,
}
_NUM = (r"(\d{1,3}(?:,\d{3})+|\d{1,5}|"
        + "|".join(sorted((re.escape(k) for k in _NUM_WORDS), key=len, reverse=True)) + r")")
_MAX_WORDS = (r"(?:at\s+most|no\s+more\s+than|not\s+more\s+than|maximum\s+of|a\s+max(?:imum)?\s+of|"
              r"max(?:imum)?\.?|up\s+to|not\s+(?:to\s+)?exceed(?:ing)?|within|no\s+longer\s+than|"
              r"limit(?:ed)?\s+(?:of|to)|<=|\u2264)")
_UNDER = r"(?:(?:less|fewer|shorter)\s+than|under|below|<)"


def _limit_rules(unit):
    u = r"\s+" + unit
    return [
        (r"at\s+least\s+" + _NUM + u, None),  # a minimum, never a maximum
        (_UNDER + r"\s*" + _NUM + u, -1),
        (_MAX_WORDS + r"\s*" + _NUM + u, 0),
        (_NUM + u + r"\s+(?:or\s+(?:less|fewer)|at\s+most|max(?:imum)?|limit)", 0),
        (_NUM + r"-" + unit.rstrip("s?").rstrip("(") + r"\b", 0),
        (r"\bin\s+" + _NUM + u, 0),
        (unit.rstrip("s?").rstrip("(") + r"\s+(?:limit|count)\s+(?:of|is)\s+" + _NUM, 0),
        (unit.rstrip("s?").rstrip("(") + r"\s+count\s+" + _UNDER + r"\s*" + _NUM, -1),
    ]


_WORD_RULES = _limit_rules(r"words?")
_CHAR_RULES = _limit_rules(r"(?:characters?|chars?)")


def _to_int(s):
    s = s.lower().replace(",", "")
    return int(s) if s.isdigit() else _NUM_WORDS[s]


def _parse_limit(text, rules):
    best = None
    t = nfc(text or "").lower()
    minimums = set()
    for pat, adj in rules:
        for m in re.finditer(pat, t):
            if adj is None:
                minimums.add(m.start(1))
                continue
            if m.start(1) in minimums:
                continue
            n = _to_int(m.group(1)) + adj
            if n > 0 and (best is None or n < best):
                best = n
    return best


def parse_word_limit(text):
    """Return the maximum number of words the request allows, or None."""
    return _parse_limit(text, _WORD_RULES)


def parse_char_limit(text):
    """Return the maximum number of characters the request allows, or None."""
    return _parse_limit(text, _CHAR_RULES)


def count_words(text):
    return len(text.split())


def trim_to_words(text, limit):
    """Cut text to at most `limit` words, preferring a sentence boundary."""
    if count_words(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out, n = [], 0
    for s in sentences:
        w = count_words(s)
        if n + w > limit:
            break
        out.append(s)
        n += w
    if out and n >= limit * 0.5:
        return " ".join(out)
    words = text.split()[:limit]
    return " ".join(words).rstrip(",;:") + ("" if words[-1].endswith((".", "!", "?")) else ".")


def trim_to_chars(text, limit):
    """Cut text to at most `limit` characters, at a sentence or word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? "):
        i = cut.rfind(sep)
        if i >= limit * 0.5:
            return cut[:i + 1]
    i = cut.rfind(" ")
    return (cut[:i] if i > 0 else cut).rstrip(",;: ")


def clean_generated(text):
    """Strip 'Here is the summary:' preambles and a wrapping code fence from model prose."""
    text = re.sub(r"^(?:here(?:'s| is) (?:the|a|your) [^\n:]*summary[^\n:]*:)\s*", "", text.strip(),
                  flags=re.I).strip()
    m = re.match(r"^```[\w-]*\n(.*?)\n?```$", text, re.S)
    if m:
        text = m.group(1).strip()
    return text
