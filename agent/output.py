"""The only code that writes to the real stdout.

install() is called first thing in main.py: it keeps a handle on the real
stdout and points sys.stdout at stderr, so a stray print() anywhere can never
corrupt the JSON result. emit() writes exactly one JSON object, once, from
any thread (main loop, signal handler or watchdog).
"""

import json
import os
import sys
import threading

MAX_MESSAGE = 6000

_real_fd = None
_lock = threading.Lock()
_emitted = False


def install():
    global _real_fd
    if _real_fd is None:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            _real_fd = os.dup(1)  # survives later changes to sys.stdout
        except OSError:
            _real_fd = 1
        sys.stdout = sys.stderr


def clean_text(value):
    """Any value -> a str that is always valid UTF-8 (no lone surrogates)."""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    elif isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
    else:
        try:
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        except Exception:
            text = repr(value)
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        text = "".join("�" if 0xD800 <= ord(c) <= 0xDFFF else c for c in text)
    return text


def render(status, message):
    status = "success" if status == "success" else "error"
    msg = clean_text(message).strip()
    if len(msg) > MAX_MESSAGE:
        msg = msg[:MAX_MESSAGE - 3].rstrip() + "..."
    if not msg:
        msg = "Done." if status == "success" else "The request could not be completed."
    return json.dumps({"status": status, "message": msg}, ensure_ascii=False) + "\n"


def emitted():
    return _emitted


def emit(status, message):
    """Write the single JSON result. Returns the exit code (0/1). Later calls
    are ignored (they still return the code of the first emission's status)."""
    global _emitted
    with _lock:
        code = 0 if status == "success" else 1
        if _emitted:
            return code
        data = render(status, message).encode("utf-8", "replace")
        fd = _real_fd if _real_fd is not None else 1
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
        except OSError:
            pass
        _emitted = True
        return code
