"""Deterministic guardrails the harness applies to the model's tool calls:
parsing, anti-hallucination path checks, literal-content preservation, and
word-limit parsing/enforcement. Pure functions, no I/O."""

import ast
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

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
_PATH_TOKEN = re.compile(r"[\w\-./\\]{0,200}\w\.[A-Za-z0-9]{1,10}\b|[\w\-]{1,100}(?:/[\w\-.]{1,100}){1,20}")
_PATHS_CACHE = {}


def nfc(s):
    return unicodedata.normalize("NFC", s)


def norm_path(p):
    p = nfc(p.strip().strip("`'\"").replace("\\", "/"))
    while p.startswith("./"):
        p = p[2:]
    return p


def prompt_paths(prompt):
    """Path-like tokens mentioned in the request (memoized: called often)."""
    hit = _PATHS_CACHE.get(prompt)
    if hit is not None:
        return list(hit)
    out = _prompt_paths(prompt)
    if len(_PATHS_CACHE) > 64:
        _PATHS_CACHE.clear()
    _PATHS_CACHE[prompt] = tuple(out)
    return list(out)


def _prompt_paths(prompt):
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
_NUM_NC = _NUM.replace("(", "(?:", 1)
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
        (r"\b(?:about|around|approximately|roughly|~)\s*" + _NUM + u, 0),
        (unit.rstrip("s?").rstrip("(") + r"\s+(?:limit|count)\s+(?:of|is)\s+" + _NUM, 0),
        (unit.rstrip("s?").rstrip("(") + r"\s+count\s+" + _UNDER + r"\s*" + _NUM, -1),
    ]


_WORD_RULES = _limit_rules(r"words?")
_CHAR_RULES = _limit_rules(r"(?:characters?|chars?)")


def _to_int(s):
    s = s.lower().replace(",", "")
    return int(s) if s.isdigit() else _NUM_WORDS[s]


_DESCRIBES_SOURCE = re.compile(r"\b(?:are|is|were|was)\s+(?:each\s+|all\s+|both\s+)?(?:about\s+|around\s+)?"
                               r"(?:under|less|fewer|below|over|more|at\s+most|roughly|approximately|about|around|"
                               r"\d)", re.I)


def _source_sentence_spans(t):
    """Character spans of sentences that describe the inputs ("The notes are each under 40 words.")
    rather than the requested output; limits found there are not limits on the output."""
    spans, pos = [], 0
    for sent in re.split(r"(?<=[.!?])\s+", t):
        start = t.find(sent, pos)
        pos = start + len(sent)
        if _DESCRIBES_SOURCE.search(sent) and not re.search(
                r"\b(summar\w*|write|create|save|produce|make|into|output|combine|should|must|keep)\b", sent):
            spans.append((start, pos))
    return spans


_ITEM = r"(?:bullet(?:\s+point)?|point|line|sentence|item|entry|paragraph)s?"
_LIMIT_WORDS = (r"(?:(?:at\s+most|no\s+more\s+than|up\s+to|under|below|less\s+than|fewer\s+than|max(?:imum)?(?:\s+of)?|"
                r"within|about|around|roughly)\s+)?")


def _per_item_spans(t):
    """Limit phrases that apply to each bullet/line/sentence, not the total:
    'each bullet under 25 words', '3 bullet points of at most 15 words each',
    '3 sentences, each under 20 words'."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"\b(?:each|every|per)\s+" + _ITEM + r"\b[^.;]{0,40}", t)]
    spans += [(m.start(), m.end()) for m in re.finditer(_LIMIT_WORDS + _NUM + r"\s+words?\s+(?:each|apiece|per\s+"
                                                         + _ITEM + r")\b", t)]
    for m in re.finditer(r"\b" + _ITEM + r"\b[^.;]{0,25}?,?\s*each\s+" + _LIMIT_WORDS + _NUM + r"\s+words?", t):
        spans.append((t.index("each", m.start()), m.end()))
    return spans


def item_unit(text):
    """'sentence' when a per-item limit is about sentences, else 'line'."""
    t = nfc(text or "").lower()
    for a, b in _per_item_spans(t):
        ctx = t[max(0, a - 40):b]
        if re.search(r"sentences?\b", ctx) and not re.search(r"bullet|line|point", ctx):
            return "sentence"
    return "line"


def parse_item_word_limit(text):
    """'each bullet under 25 words' -> 24 (a limit on every bullet/line), else None."""
    t = nfc(text or "").lower()
    for a, b in _per_item_spans(t):
        piece = re.sub(r"\s+(?:each|apiece)\b.*$", "", t[a:b]) if not t[a:b].startswith(("each", "every", "per")) \
            else t[a:b]
        n = _parse_limit_raw(piece, _WORD_RULES) or _parse_limit_raw(re.sub(r"^(?:each|every|per)\s+(?:"
                                                                            + _ITEM + r"\s+)?", "", piece) + "",
                                                                     _WORD_RULES)
        if not n:
            m = re.search(_NUM + r"\s+words?", piece)
            n = _to_int(m.group(1)) if m else None
        if n:
            return n
    return None


def _parse_limit(text, rules):
    t = nfc(text or "").lower()
    return _parse_limit_raw(t, rules, _source_sentence_spans(t) + _per_item_spans(t))


def _parse_limit_raw(t, rules, skip=()):
    best = None
    minimums = set()
    unit = rules[1][0].rsplit(r"\s+", 1)[-1]
    for m in re.finditer(_NUM_NC + r"\s*(?:to|-|\u2013|and)\s*" + _NUM + r"\s+" + unit, t):  # "50 to 80 words"
        if not any(a <= m.start() < b for a, b in skip):
            n = _to_int(m.group(1))
            best = n if best is None or n < best else best
            minimums.add(m.start(1))
    for pat, adj in rules:
        for m in re.finditer(pat, t):
            if any(a <= m.start() < b for a, b in skip):
                continue
            if adj is None:
                minimums.add(m.start(1))
                continue
            if m.start(1) in minimums:
                continue
            n = _to_int(m.group(1)) + adj
            if n > 0 and (best is None or n < best):
                best = n
    return best


def parse_word_min(text):
    """'at least 50 words' / '50 to 80 words' -> 50, else None (limits describing the sources are ignored)."""
    t = nfc(text or "").lower()
    skip = _source_sentence_spans(t)
    for pat in (r"at\s+least\s+" + _NUM + r"\s+words?", r"\b" + _NUM + r"\s*(?:to|-|\u2013)\s*" + _NUM_NC + r"\s+words?",
                r"\bbetween\s+" + _NUM + r"\s+and\s+" + _NUM_NC + r"\s+words?",
                r"(?:no\s+(?:fewer|less)\s+than|minimum\s+of)\s+" + _NUM + r"\s+words?"):
        for m in re.finditer(pat, t):
            if not any(a <= m.start() < b for a, b in skip):
                return _to_int(m.group(1))
    return None


def split_inline_bullets(text):
    """'- a. - b. - c.' on one line -> one bullet per line (the model sometimes runs them together)."""
    out = []
    for line in (text or "").split("\n"):
        if _BULLET_LINE.match(line) and re.search(r"\s[-*\u2022]\s+\S", line[2:]):
            parts = re.split(r"(?<=[.!?;:)])\s+(?=[-*\u2022]\s+\S)", line)
            if len(parts) > 1:
                out.extend(p.strip() for p in parts)
                continue
        out.append(line)
    return "\n".join(out)


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
    pieces = re.split(r"((?<=[.!?])\s+)", text.strip())  # keep the separators: bullets stay on their own lines
    out, n = [], 0
    for i in range(0, len(pieces), 2):
        w = count_words(pieces[i])
        if n + w > limit:
            break
        if out:
            out.append(pieces[i - 1])
        out.append(pieces[i])
        n += w
    if out and n >= limit * 0.5:
        return "".join(out)
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


# ------------------------------------------- sentence / bullet constraints
_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "a single": 1, "single": 1}
_CNT = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|a single|single)"


def _cnt(s):
    s = s.lower()
    return int(s) if s.isdigit() else _COUNT_WORDS[s]


def parse_bullet_count(text):
    """'exactly 3 bullet points' / 'in three bullets' / '5 bullet points' -> 3/3/5, else None.
    'one bullet per source file' -> the number of source files named (all paths but the output)."""
    t = nfc(text or "")
    m = re.search(r"\b" + _CNT + r"\s+(?:bullet(?:ed)?(?:\s+points?)?|bullets|points)\s+in\s+total\b", t, re.I)
    if m:
        return _cnt(m.group(1))
    if re.search(r"\b(?:one|a|1)\s+(?:bullet(?:\s+point)?|point)\s+(?:per|for\s+each|for\s+every)\s+"
                 r"(?:source\s+)?(?:file|source|log|document)\b", t, re.I):
        n = len(prompt_paths(t)) - 1
        return n if n > 0 else None
    m = re.search(r"\b(?:exactly\s+|in\s+|as\s+|with\s+|of\s+|using\s+)?" + _CNT + r"\s+(?:short\s+|concise\s+)?"
                  r"(?:bullet(?:ed)?(?:\s+points?)?|bullets|points)\b", nfc(text or ""), re.I)
    return _cnt(m.group(1)) if m else None


def parse_sentence_count(text):
    """'in 2 sentences' / 'exactly three sentences' / 'one sentence' -> n, else None.
    'at most N sentences' is a maximum and is handled by parse_sentence_max."""
    t = nfc(text or "")
    if re.search(r"\b(?:at most|no more than|up to|maximum of|fewer than|less than|under)\s+" + _CNT +
                 r"\s+sentences?\b", t, re.I):
        return None
    m = re.search(r"\b" + _CNT + r"(?:-|\s+)sentences?\b", t, re.I)
    return _cnt(m.group(1)) if m else None


def parse_sentence_max(text):
    m = re.search(r"\b(at most|no more than|up to|maximum of|fewer than|less than|under)\s+" + _CNT +
                  r"\s+sentences?\b", nfc(text or ""), re.I)
    if not m:
        return None
    n = _cnt(m.group(2))
    return n - 1 if m.group(1).lower() in ("fewer than", "less than", "under") else n


_BULLET_LINE = re.compile(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+\S")


def count_bullets(text):
    return sum(1 for l in (text or "").split("\n") if _BULLET_LINE.match(l))


def count_sentences(text):
    t = re.sub(r"\b(?:e\.g|i\.e|etc|vs|approx|Mr|Mrs|Ms|Dr|No)\.", "_", text or "", flags=re.I)
    t = re.sub(r"(\d)\.(\d)", r"\1_\2", t)
    parts = [p for p in re.split(r"(?<=[.!?])\s+|\n\s*\n", t.strip()) if re.search(r"\w", p)]
    return len(parts)


_NUM_TOKEN = re.compile(r"\d[\d,]*(?:[.:]\d+)*")


def _norm_num(tok):
    tok = tok.replace(",", "")
    parts = re.split(r"([.:])", tok)
    out = []
    for p in parts:
        if p in (".", ":"):
            out.append(p)
        else:
            out.append(p.lstrip("0") or "0")
    s = "".join(out)
    if "." in s and ":" not in s:
        s = s.rstrip("0").rstrip(".") if re.fullmatch(r"\d+\.\d+", s) else s
    return s


_DERIVED_ASK = re.compile(r"\b(total|sum|combined|altogether|in all|overall|average|mean|difference|how many|"
                          r"count of|number of)\b", re.I)


def _derived_values(sources):
    """Totals a summary may legitimately state: sums of up to three source numbers and
    the total / average of all of them (only for a modest set of numbers)."""
    from itertools import combinations
    vals = []
    for tok in _NUM_TOKEN.findall(sources or ""):
        if ":" in tok or tok.count(".") > 1:
            continue
        try:
            v = Decimal(tok.replace(",", ""))
        except InvalidOperation:
            continue
        if v not in vals:
            vals.append(v)
    out = set()
    if not vals or len(vals) > 40:
        return out
    for k in (2, 3):
        for combo in combinations(vals, k):
            out.add(_norm_num(str(sum(combo))))
    out.add(_norm_num(str(sum(vals))))
    out.add(_norm_num(str((sum(vals) / len(vals)).quantize(Decimal("0.01")))))
    return out


def answer_unsupported_numbers(message, corpus):
    """Multi-digit numbers an answer states that occur nowhere in `corpus` (the files read,
    the question, the conversation and tool results). Single digits are ignored: small
    counts ("3 contractors") are routinely stated without being written in the file."""
    # CSV rows ("2,Raj,30,44") would otherwise read as thousands-separated numbers (3044): also match the
    # comma-split values.
    corpus = (corpus or "") + "\n" + (corpus or "").replace(",", " ")
    return [tok for tok in unsupported_numbers(message, corpus)
            if not re.fullmatch(r"\d", tok.replace(",", ""))]


def unsupported_numbers(summary, sources, prompt=""):
    """Numbers/times/dates in the summary that never occur in the sources
    (after normalising leading zeros and thousands separators). When the request asks
    for a total/sum/average, sums of the source numbers count as supported."""
    have = set()
    if _DERIVED_ASK.search(prompt or ""):
        have |= _derived_values(sources)
    for tok in _NUM_TOKEN.findall(sources or ""):
        have.add(_norm_num(tok))
        for piece in re.split(r"[.:]", tok.replace(",", "")):
            have.add(piece.lstrip("0") or "0")
    bad = []
    for tok in _NUM_TOKEN.findall(summary or ""):
        n = _norm_num(tok)
        if n not in have and tok not in bad:
            bad.append(tok)
    return bad
