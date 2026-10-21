"""Client for an OpenAI-compatible /chat/completions endpoint (Qwen3-14B),
standard library only.

Contract (see docs/CLAUDE_CODE_PROMPT.md §4):
  * every request carries chat_template_kwargs.enable_thinking = false;
  * explicit User-Agent (Cloudflare rejects urllib's default with 403/1010);
  * retries with exponential backoff + jitter on transient failures, honouring
    Retry-After, never sleeping past the run deadline; 401/403/404 fail fast;
  * a context-length 400 shrinks the request and retries;
  * max_tokens is computed so prompt + output fit in 40,960 tokens;
  * only successful completions are counted (calls_used), capped at
    config.CALL_CAP (< the grader's 120);
  * tool calls are taken from native `tool_calls`, or recovered from text
    (<tool_call> blocks, fenced JSON, bare JSON objects).
"""

import json
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

import config
from config import log, remaining  # noqa: F401  (re-exported for older modules)
from errors import AgentError

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529, 530)
FATAL_STATUS = (401, 403, 404)
MAX_ATTEMPTS = 5          # per completion, for transient failures
RATE_ATTEMPTS = 8         # 429/503/529 and proxy glitches: the shared endpoint is busy, wait longer
_CONTEXT_400 = re.compile(r"context|maximum|max_tokens|too long|tokens", re.I)


class LLMError(AgentError):
    """The model endpoint could not be used (after retries)."""


class BudgetExhausted(AgentError):
    """The run's model-call cap or deadline has been reached."""


def strip_think(text):
    """Remove <think>...</think> blocks, including an unterminated one."""
    text = _THINK_RE.sub("", text or "")
    low = text.lower()
    if "<think>" in low:
        text = text[:low.index("<think>")]
    text = re.sub(r"</?think>", "", text, flags=re.I)
    return text.strip()


# Logs, ids, hex and timestamps tokenize densely (~2.5 chars/token measured on the course
# endpoint); a context-length 400 recalibrates this from the server's own count.
CHARS_PER_TOKEN = [2.5]


def estimate_tokens(messages, tools=None):
    """Conservative token estimate: chars / CHARS_PER_TOKEN plus per-message overhead."""
    chars = 0
    for m in messages:
        chars += len(m.get("content") or "") + 24
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            chars += len(fn.get("name") or "") + len(fn.get("arguments") or "") + 40
    if tools:
        chars += len(json.dumps(tools, ensure_ascii=False))
    return int(chars / CHARS_PER_TOKEN[0]) + 16


# ------------------------------------------------------------ tool-call parsing
def _loads_lenient(text):
    import guards  # local import: guards has no dependency on llm
    return guards._loads_lenient(text)


def iter_json_objects(text):
    """Yield (start, end, obj) for every balanced top-level {...} that parses."""
    i, n = 0, len(text or "")
    while i < n:
        start = text.find("{", i)
        if start < 0:
            return
        depth, in_str, esc, end = 0, False, False, None
        for j in range(start, n):
            c = text[j]
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
                    end = j + 1
                    break
        if end is None:
            return
        obj = _loads_lenient(text[start:end])
        if isinstance(obj, dict):
            yield start, end, obj
            i = end
        else:
            i = start + 1


def _call_from_obj(obj):
    """{"name":..,"arguments":..} / {"tool":..,"args":..} / {"function":{..}} -> (name, args) or None."""
    if isinstance(obj.get("function"), dict):
        obj = obj["function"]
    name = obj.get("name", obj.get("tool"))
    if not isinstance(name, str) or not name.strip():
        return None
    args = obj.get("arguments", obj.get("args", obj.get("parameters")))
    if args is None:
        args = {}
    return name.strip(), args


def _normalize_args(args):
    """Arguments may arrive as a dict or as a JSON string. Returns (dict, error)."""
    if isinstance(args, dict):
        return args, None
    if isinstance(args, str):
        s = args.strip()
        if not s:
            return {}, None
        m = re.match(r"^```[\w-]*\s*\n?(.*?)\n?```$", s, re.S)
        if m:
            s = m.group(1).strip()
        obj = _loads_lenient(s)
        if isinstance(obj, dict):
            return obj, None
        # take the outermost object if there is junk around it
        for _, _, o in iter_json_objects(s):
            return o, None
        return None, "arguments are not valid JSON: %s" % s[:200]
    return None, "arguments must be a JSON object"


def parse_text_tool_calls(text):
    """Recover tool calls written as text: Hermes <tool_call> blocks, fenced
    ```json blocks or bare JSON objects. Returns a list of call dicts."""
    calls = []
    if not text:
        return calls
    blocks = re.findall(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", text, re.S | re.I)
    if not blocks:
        blocks = re.findall(r"```(?:json|tool_call)?\s*\n(.*?)```", text, re.S)
    sources = blocks if blocks else [text]
    for src in sources:
        for _, _, obj in iter_json_objects(src):
            got = _call_from_obj(obj)
            if got is None:
                continue
            args, err = _normalize_args(got[1])
            calls.append({"id": "call_t%d" % len(calls), "name": got[0], "arguments": args,
                          "error": err, "raw": json.dumps(got[1], ensure_ascii=False)
                          if not isinstance(got[1], str) else got[1]})
        if not blocks and calls:
            break
    return calls


class Reply:
    def __init__(self, content, tool_calls, finish_reason, usage, raw_content=""):
        self.content = content          # assistant text, <think> removed
        self.tool_calls = tool_calls    # list of {"id","name","arguments","error","raw"}
        self.finish_reason = finish_reason
        self.usage = usage or {}
        self.raw_content = raw_content
        self.native = False

    @property
    def truncated(self):
        return self.finish_reason == "length"


class LLM:
    def __init__(self):
        base = config.base_url().rstrip("/")
        self.url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        self.model = config.model()
        self.key = config.api_key()
        if not self.key and not config.is_local_url(base):
            raise LLMError("No API key: set QWEN_API_KEY (or OPENAI_API_KEY) for %s." % base)
        self.calls_used = 0
        self.attempts = 0
        self.last_finish_reason = None
        self.tokens_per_sec = None   # observed decode speed, for time-aware max_tokens
        self.last_request = None

    # ---------------------------------------------------------------- HTTP
    def _post(self, body, timeout):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": config.USER_AGENT}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(self.url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", "replace")
        if text.lstrip().startswith("<"):
            raise ValueError("HTML body where JSON was expected")
        return json.loads(text)

    def _redact(self, text):
        if self.key and text:
            text = text.replace(self.key, "[REDACTED]")
        return text

    def _sleep(self, seconds, why):
        seconds = max(0.0, min(seconds, remaining() - 8.0))
        if seconds > 0:
            log("retrying in %.1fs (%s)" % (seconds, why))
            time.sleep(seconds)

    def max_tokens_for(self, messages, tools, desired, minimum=256):
        room = config.CONTEXT_TOKENS - estimate_tokens(messages, tools) - config.CONTEXT_SAFETY
        return min(desired, room) if room >= minimum else None

    @staticmethod
    def _shrink(messages):
        """Halve the longest message after the first two (system + task)."""
        msgs = [dict(m) for m in messages]
        idx = [i for i in range(2, len(msgs)) if len(msgs[i].get("content") or "") > 400]
        if not idx:
            idx = [i for i in range(len(msgs)) if len(msgs[i].get("content") or "") > 400]
        if not idx:
            return None
        i = max(idx, key=lambda k: len(msgs[k]["content"]))
        c = msgs[i]["content"]
        keep = len(c) // 4
        msgs[i]["content"] = c[:keep] + "\n...[truncated to fit the context window]...\n" + c[-keep:]
        return msgs

    # ---------------------------------------------------------------- main API
    def complete(self, messages, tools=None, max_tokens=1024, temperature=None, timeout_cap=None,
                 min_tokens=256, retry_timeouts=True):
        """One chat completion with retries. Returns a Reply; raises LLMError or
        BudgetExhausted."""
        if self.calls_used >= config.CALL_CAP:
            raise BudgetExhausted("Model-call cap reached (%d calls)." % self.calls_used)
        msgs = [dict(m) for m in messages]
        if config.NO_THINK_SUFFIX:
            for m in reversed(msgs):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    m["content"] = m["content"] + "\n/no_think"
                    break
        mt = self.max_tokens_for(msgs, tools, max_tokens, min(min_tokens, max_tokens))
        while mt is None:
            smaller = self._shrink(msgs)
            if smaller is None:
                raise LLMError("The request does not fit in the model's context window.")
            msgs = smaller
            mt = self.max_tokens_for(msgs, tools, max_tokens, min(min_tokens, max_tokens))
        temp = config.TEMPERATURE if config.TEMPERATURE is not None else (
            0.2 if temperature is None else temperature)
        body = {"model": self.model, "messages": msgs, "temperature": temp, "top_p": 0.8,
                "max_tokens": mt, "stream": False,
                "chat_template_kwargs": {"enable_thinking": False}}
        if config.REASONING_NONE:
            body["reasoning_effort"] = "none"
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        attempt, context_shrinks, last_err = 0, 0, "no attempt was made"
        cap = timeout_cap or config.CALL_TIMEOUT_CAP
        while True:
            budget = remaining() - 4.0
            if budget < 6.0:
                raise LLMError("The language model request failed (%s; out of time)." % last_err)
            attempt += 1
            self.attempts += 1
            timeout = min(cap, budget)
            self.last_request = body
            t0 = time.monotonic()
            try:
                resp = self._post(body, timeout)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = self._redact(e.read().decode("utf-8", "replace"))[:400]
                except Exception:
                    pass
                log("HTTP %s from model endpoint: %s" % (e.code, detail.replace("\n", " ")[:300]))
                if e.code == 401:
                    raise LLMError("HTTP 401 from the model endpoint: the API key was rejected "
                                   "(check QWEN_API_KEY).")
                if e.code == 403:
                    raise LLMError("HTTP 403 from the model endpoint: access denied%s." % (
                        " by its firewall" if ("1010" in detail or "<html" in detail.lower()) else ""))
                if e.code == 404:
                    raise LLMError("HTTP 404 from the model endpoint: check OPENAI_BASE_URL (%s) "
                                   "and MODEL (%s)." % (self.url, self.model))
                if e.code == 400 and _CONTEXT_400.search(detail) and context_shrinks < 4:
                    context_shrinks += 1
                    last_err = "context length exceeded"
                    m = re.search(r"prompt contains at least (\d+) input tokens", detail) or \
                        re.search(r"(\d{4,6}) (?:input|prompt) tokens", detail)
                    if m:
                        chars = estimate_tokens(body["messages"], tools) * CHARS_PER_TOKEN[0]
                        ratio = chars / max(1, int(m.group(1)))
                        CHARS_PER_TOKEN[0] = max(1.5, min(CHARS_PER_TOKEN[0], ratio * 0.92))
                        log("recalibrated estimate: %.2f chars/token" % CHARS_PER_TOKEN[0])
                    else:
                        CHARS_PER_TOKEN[0] = max(1.5, CHARS_PER_TOKEN[0] * 0.85)
                    msgs2 = self._shrink(body["messages"]) or body["messages"]  # the server said: too big
                    want = max(256, min(body["max_tokens"], 1024))
                    for _ in range(40):
                        if self.max_tokens_for(msgs2, tools, want, 256) is not None:
                            break
                        smaller = self._shrink(msgs2)
                        if smaller is None:
                            break
                        msgs2 = smaller
                    body["messages"] = msgs2
                    body["max_tokens"] = self.max_tokens_for(msgs2, tools, want, 128) or 128
                    continue
                last_err = "HTTP %s" % e.code
                if e.code == 530 or "1033" in detail:
                    last_err = "HTTP %s: the model server is offline or unreachable" % e.code
                proxy_glitch = e.code == 400 and detail.lstrip().lower().startswith(("<!doctype", "<html"))
                if proxy_glitch:
                    last_err = "HTTP 400 from a proxy in front of the model (HTML error page)"
                busy = proxy_glitch or e.code in (429, 503, 529)
                limit = RATE_ATTEMPTS if busy else MAX_ATTEMPTS
                if (e.code in RETRY_STATUS or proxy_glitch or (e.code == 400 and attempt < 2)) and attempt < limit:
                    wait = min(16.0, 1.0 * (2 ** min(attempt - 1, 4))) * (0.75 + random.random() / 2)
                    try:
                        ra = e.headers.get("Retry-After") if e.headers else None
                        if ra is not None:
                            wait = min(30.0, float(ra))
                    except (TypeError, ValueError):
                        pass
                    if wait > remaining() - 10:
                        raise LLMError("The language model request failed (%s)." % last_err)
                    self._sleep(wait, last_err)
                    continue
                raise LLMError("The language model request failed (%s)." % last_err)
            except (socket.timeout, TimeoutError) as e:
                last_err = "request timeout: no response after %.0fs" % (time.monotonic() - t0)
                log("model request failed: %s" % last_err)
                if remaining() < 20 or not retry_timeouts:
                    raise LLMError("The language model request failed (%s)." % last_err)
                self._sleep(1.0, last_err)
                continue
            except (urllib.error.URLError, ConnectionError, ssl.SSLError, OSError) as e:
                reason = getattr(e, "reason", e)
                last_err = "connection failure (%s)" % (reason,)
                log("model request failed: %r" % (e,))
                if isinstance(reason, (socket.timeout, TimeoutError)) and (remaining() < 20 or not retry_timeouts):
                    raise LLMError("The language model request failed (%s)." % last_err)
                if attempt >= MAX_ATTEMPTS:
                    raise LLMError("The language model request failed (%s)." % last_err)
                self._sleep(min(8.0, 1.0 * (2 ** min(attempt - 1, 3))) * (0.75 + random.random() / 2), last_err)
                continue
            except ValueError as e:
                last_err = "invalid response body (%s)" % e
                log("model returned an unusable body: %s" % e)
                if attempt >= MAX_ATTEMPTS:
                    raise LLMError("The language model request failed (%s)." % last_err)
                self._sleep(1.0, last_err)
                continue

            try:
                choice = resp["choices"][0]
                msg = choice.get("message") or {}
            except (KeyError, IndexError, TypeError, AttributeError):
                last_err = "response without choices"
                log("unexpected response shape: %s" % self._redact(str(resp))[:300])
                if attempt >= MAX_ATTEMPTS:
                    raise LLMError("The language model request failed (%s)." % last_err)
                self._sleep(1.0, last_err)
                continue

            dt = time.monotonic() - t0
            self.calls_used += 1
            self.slowest_call = max(getattr(self, "slowest_call", 0.0), dt)
            usage = resp.get("usage") or {}
            ct = usage.get("completion_tokens")
            if isinstance(ct, int) and ct >= 50 and dt > 0.5:
                rate = ct / dt
                self.tokens_per_sec = rate if self.tokens_per_sec is None else (
                    0.5 * self.tokens_per_sec + 0.5 * rate)
            log("calls_used=%d model call ok in %.1fs (finish=%s, completion_tokens=%s)"
                % (self.calls_used, dt, choice.get("finish_reason"), ct))
            return self._to_reply(msg, choice.get("finish_reason"), usage)

    def _to_reply(self, msg, finish_reason, usage):
        raw = msg.get("content") or ""
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)
        content = strip_think(raw)
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            args, err = _normalize_args(fn.get("arguments"))
            calls.append({"id": tc.get("id") or "call_n%d" % i, "name": name.strip(), "arguments": args,
                          "error": err, "raw": fn.get("arguments") if isinstance(fn.get("arguments"), str)
                          else json.dumps(fn.get("arguments"), ensure_ascii=False)})
        native = bool(calls)
        if not calls and content:
            calls = parse_text_tool_calls(content)
        self.last_finish_reason = finish_reason
        r = Reply(content, calls, finish_reason, usage, raw)
        r.native = native
        return r

    def time_tokens(self, seconds):
        """How many tokens can be generated in `seconds` at the observed speed."""
        rate = self.tokens_per_sec or 25.0
        return int(max(0.0, seconds) * rate)

    # ------------------------------------------------- CP1/CP2 harness interface
    def chat(self, messages, max_tokens=1024, thinking=False, temperature=0.0, call_cap=120.0):
        """Text-protocol call used by harness.py. Thinking is always disabled
        (the argument is accepted for compatibility and ignored). A native
        tool call is re-expressed as the harness's {"tool","args"} JSON."""
        reply = self.complete(messages, max_tokens=max_tokens, temperature=temperature,
                              timeout_cap=min(call_cap, config.CALL_TIMEOUT_CAP), retry_timeouts=False)
        if reply.native and reply.tool_calls and not reply.content:
            c = reply.tool_calls[0]
            if c["arguments"] is not None:
                return json.dumps({"tool": c["name"], "args": c["arguments"]}, ensure_ascii=False)
        return reply.content
