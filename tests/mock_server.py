"""A tiny fake OpenAI-compatible /chat/completions server for offline tests.

Tests push scripted replies onto `MockServer.replies`; each request pops one.
A reply is either a string (assistant content), or a dict with any of:
  {"content": s}                   -> normal assistant message
  {"tool_calls": [...]}            -> native OpenAI tool calls (content null)
  {"status": 500, "body": "..."}   -> HTTP error (optional body)
  {"headers": {...}}               -> extra response headers (e.g. Retry-After)
  {"raw": "not json"}              -> raw HTTP 200 body
  {"empty_choices": True}          -> {"choices": []}
  {"sleep": 5}                     -> delay before answering
  {"reject_field": "name", ...}    -> HTTP 400 if the request body has that field,
                                      otherwise the rest of the reply
Every request (path, headers, body) is recorded in `MockServer.requests`.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        srv = self.server.owner
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        srv.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                             "user_agent": self.headers.get("User-Agent"), "body": body})
        reply = srv.replies.pop(0) if srv.replies else {"status": 500}
        if isinstance(reply, str):
            reply = {"content": reply}
        if reply.get("sleep"):
            time.sleep(reply["sleep"])
        if reply.get("reject_field") and reply["reject_field"] in body:
            srv.replies.insert(0, {k: v for k, v in reply.items() if k != "reject_field"})
            reply = {"status": 400, "body": '{"error":"unsupported field"}'}
        if not self.path.endswith("/chat/completions"):
            reply = {"status": 404}
        headers = reply.get("headers", {})
        if "status" in reply:
            self._send(reply["status"], reply.get("body", '{"error":"mock"}').encode("utf-8"), headers)
        elif "raw" in reply:
            self._send(200, reply["raw"].encode("utf-8"), headers)
        elif reply.get("empty_choices"):
            self._send(200, b'{"choices": []}', headers)
        else:
            message = {"role": "assistant", "content": reply.get("content")}
            if "tool_calls" in reply:
                message["tool_calls"] = reply["tool_calls"]
            payload = {"id": "mock", "object": "chat.completion", "model": body.get("model"),
                       "choices": [{"index": 0, "finish_reason": reply.get("finish_reason", "stop"), "message": message}]}
            self._send(200, json.dumps(payload).encode("utf-8"), headers)

    def _send(self, code, data, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self.wfile.write(data)


class MockServer:
    def __init__(self):
        self.replies = []
        self.requests = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.owner = self
        self.port = self.httpd.server_address[1]
        self.base_url = "http://127.0.0.1:%d/v1" % self.port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reset(self, replies):
        self.replies = list(replies)
        self.requests = []

    def close(self):
        self.httpd.shutdown()
