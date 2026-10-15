"""UT-O01..O07 (output), UT-L01..L08 (LLM client), router and config."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import output  # noqa: E402
import router  # noqa: E402
from mock_server import MockServer  # noqa: E402

PY = sys.executable


def run_py(code, env=None, timeout=30):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run([PY, "-c", code], cwd=helpers.AGENT, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


class Output(unittest.TestCase):
    def test_o01_special_characters_round_trip(self):
        msg = 'quote " back\\slash\nnew\ttab Zoë 日本 \U0001f600'
        out = output.render("success", msg)
        self.assertEqual(json.loads(out), {"status": "success", "message": msg})
        self.assertTrue(out.endswith("}\n") and out.count("\n") == 1)

    def test_o02_lone_surrogate(self):
        out = output.render("success", "bad \udcff name")
        out.encode("utf-8")  # must not raise
        self.assertIn("�", json.loads(out)["message"])

    def test_o03_emit_once_and_o04_stray_print(self):
        code, out, err = run_py("import output; output.install(); print('stray'); "
                                "output.emit('success','one'); output.emit('error','two'); print('after')")
        self.assertEqual(out, '{"status": "success", "message": "one"}\n')
        self.assertIn("stray", err)
        self.assertIn("after", err)

    def test_o05_coercion(self):
        self.assertEqual(json.loads(output.render("success", 42))["message"], "42")
        self.assertEqual(json.loads(output.render("error", None))["message"], "The request could not be completed.")
        self.assertEqual(json.loads(output.render("success", {"a": 1}))["message"], '{"a": 1}')
        self.assertEqual(len(json.loads(output.render("success", "x" * 10000))["message"]), output.MAX_MESSAGE)

    def test_o06_exception_in_loop_gives_error_json(self):
        code, out, err = run_py(
            "import sys; sys.argv=['main.py','Create a.txt containing 1']; import main\n"
            "def boom(*a, **k): raise RuntimeError('kaboom')\n"
            "main.run = boom\n"
            "sys.exit(main.main())", env={"QWEN_API_KEY": "k"})
        self.assertEqual(code, 1)
        obj = json.loads(out)
        self.assertEqual(obj["status"], "error")
        self.assertIn("Internal error", obj["message"])

    def test_o07_sigterm_during_long_call(self):
        srv = MockServer()
        srv.reset([{"sleep": 30, "content": "x"}])
        ws = tempfile.mkdtemp()
        env = dict(os.environ, OPENAI_BASE_URL=srv.base_url, QWEN_API_KEY="k", MODEL="m")
        p = subprocess.Popen(["bash", os.path.join(helpers.ROOT, "run_agent.sh"), "Create a.txt containing 1"],
                             cwd=ws, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1.5)
        t0 = time.monotonic()
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=10)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertNotEqual(p.returncode, 0)
        obj = json.loads(out.decode())
        self.assertEqual(obj["status"], "error")
        self.assertIn("SIGTERM", obj["message"])
        srv.close()


class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = MockServer()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("OPENAI_BASE_URL", "MODEL", "QWEN_API_KEY", "OPENAI_API_KEY")}
        os.environ.update(OPENAI_BASE_URL=self.srv.base_url, MODEL="Qwen/Qwen3-14B", QWEN_API_KEY="secret-key-123")
        config.set_task_kind("simple")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_l01_url_building(self):
        for base, want in (("https://h/v1", "https://h/v1/chat/completions"),
                           ("https://h/v1/", "https://h/v1/chat/completions")):
            os.environ["OPENAI_BASE_URL"] = base
            self.assertEqual(llm.LLM().url, want)

    def test_l02_body_shape_and_l08_redaction(self):
        self.srv.reset(["hello"])
        c = llm.LLM()
        r = c.complete([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {
            "name": "f", "parameters": {"type": "object", "properties": {}}}}])
        self.assertEqual(r.content, "hello")
        body = self.srv.requests[0]["body"]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(body["model"], "Qwen/Qwen3-14B")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(self.srv.requests[0]["auth"], "Bearer secret-key-123")
        self.assertEqual(self.srv.requests[0]["user_agent"], "hw1-agent/1.0")
        self.assertEqual(c._redact("x secret-key-123 y"), "x [REDACTED] y")

    def test_l03_max_tokens_shrinks_with_prompt(self):
        c = llm.LLM()
        small = c.max_tokens_for([{"role": "user", "content": "x" * 300}], None, 8000)
        big = c.max_tokens_for([{"role": "user", "content": "x" * 100000}], None, 8000)
        self.assertEqual(small, 8000)
        self.assertLess(big, 8000)
        est = llm.estimate_tokens([{"role": "user", "content": "x" * 100000}])
        self.assertLessEqual(est + big, config.CONTEXT_TOKENS)
        self.assertIsNone(c.max_tokens_for([{"role": "user", "content": "x" * 130000}], None, 8000))

    def test_l04_tool_call_parsing(self):
        p = llm.parse_text_tool_calls
        a = p('<tool_call>{"name": "grep", "arguments": {"pattern": "x"}}</tool_call>')
        self.assertEqual((a[0]["name"], a[0]["arguments"]), ("grep", {"pattern": "x"}))
        b = p('```json\n{"name": "read_file", "arguments": {"path": "a.txt",}}\n```')
        self.assertEqual(b[0]["arguments"], {"path": "a.txt"})
        c = p("{'name': 'notes', 'arguments': {'text': 'hi'}}")
        self.assertEqual(c[0]["arguments"], {"text": "hi"})
        d = p('<tool_call>{"name": "a", "arguments": {}}</tool_call>\n<tool_call>{"name": "b", "arguments": '
              '"{\\"k\\": 1}"}</tool_call>')
        self.assertEqual([x["name"] for x in d], ["a", "b"])
        self.assertEqual(d[1]["arguments"], {"k": 1})
        # native tool_calls with a string argument payload
        self.srv.reset([{"tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "grep", "arguments": '{"pattern": "ORD-1"}'}}]}])
        r = llm.LLM().complete([{"role": "user", "content": "x"}])
        self.assertTrue(r.native)
        self.assertEqual(r.tool_calls[0]["arguments"], {"pattern": "ORD-1"})

    def test_l05_think_stripping(self):
        self.assertEqual(llm.strip_think("<think>a</think>b"), "b")
        self.assertEqual(llm.strip_think("b<think>never closed"), "b")

    def test_l06_counter_counts_successes_only(self):
        self.srv.reset([{"status": 500}, "ok", {"empty_choices": True}, "ok2"])
        c = llm.LLM()
        c.complete([{"role": "user", "content": "x"}])
        self.assertEqual(c.calls_used, 1)
        c.complete([{"role": "user", "content": "x"}])
        self.assertEqual(c.calls_used, 2)
        self.assertEqual(c.attempts, 4)
        old = config.CALL_CAP
        try:
            config.CALL_CAP = 2
            with self.assertRaises(llm.BudgetExhausted):
                c.complete([{"role": "user", "content": "x"}])
        finally:
            config.CALL_CAP = old
        self.assertLessEqual(config.CALL_CAP, 110)

    def test_l07_backoff_honours_retry_after_and_deadline(self):
        self.srv.reset([{"status": 429, "headers": {"Retry-After": "1"}}, "ok"])
        t0 = time.monotonic()
        llm.LLM().complete([{"role": "user", "content": "x"}])
        self.assertGreaterEqual(time.monotonic() - t0, 0.9)
        # a Retry-After beyond the deadline gives up instead of sleeping
        old = config.BUDGET
        try:
            config.BUDGET = config.elapsed() + 20
            self.srv.reset([{"status": 503, "headers": {"Retry-After": "30"}}] * 3)
            t0 = time.monotonic()
            with self.assertRaises(llm.LLMError):
                llm.LLM().complete([{"role": "user", "content": "x"}])
            self.assertLess(time.monotonic() - t0, 5)
        finally:
            config.BUDGET = old

    def test_fatal_statuses_fail_fast(self):
        for code in (401, 403, 404):
            self.srv.reset([{"status": code, "body": "<html>blocked</html>"}] * 5)
            t0 = time.monotonic()
            with self.assertRaises(llm.LLMError):
                llm.LLM().complete([{"role": "user", "content": "x"}])
            self.assertLess(time.monotonic() - t0, 2)
            self.assertEqual(len(self.srv.requests), 1)

    def test_html_400_is_retried_as_transient(self):
        page = '<!DOCTYPE HTML><html><body><p>Error code: 400</p><p>Bad request syntax</p></body></html>'
        self.srv.reset([{"status": 400, "body": page}] * 3 + ["recovered"])
        r = llm.LLM().complete([{"role": "user", "content": "x"}])
        self.assertEqual(r.content, "recovered")
        self.assertEqual(len(self.srv.requests), 4)

    def test_context_length_400_shrinks(self):
        self.srv.reset([{"status": 400, "body": '{"error": "maximum context length is 40960 tokens"}'}, "fine"])
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"},
                {"role": "user", "content": "y" * 20000}]
        r = llm.LLM().complete(msgs, max_tokens=1000)
        self.assertEqual(r.content, "fine")
        a, b = self.srv.requests
        self.assertLess(len(json.dumps(b["body"])), len(json.dumps(a["body"])))

    def test_missing_key_rules(self):
        os.environ.pop("QWEN_API_KEY")
        os.environ["OPENAI_BASE_URL"] = "https://example.invalid/v1"
        with self.assertRaisesRegex(llm.LLMError, "QWEN_API_KEY"):
            llm.LLM()
        os.environ["OPENAI_API_KEY"] = "fallback"
        self.assertEqual(llm.LLM().key, "fallback")
        os.environ.pop("OPENAI_API_KEY")
        os.environ["OPENAI_BASE_URL"] = "http://localhost:11434/v1"
        self.assertEqual(llm.LLM().key, "")  # local dev backend may run without a key

    def test_defaults(self):
        for k in ("OPENAI_BASE_URL", "MODEL"):
            os.environ.pop(k, None)
        c = llm.LLM()
        self.assertEqual(c.url, "https://llm.montii.me/v1/chat/completions")
        self.assertEqual(c.model, "Qwen/Qwen3-14B")


class Routing(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()

    def test_cp1_cp2_prompts_stay_simple(self):
        for p in ["Create foo.txt inside the abc directory. The file should contain: foo",
                  "Create script.py containing: print('hi')",
                  "Read app.log. What error code was logged at 14:05?",
                  "Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create reports/incident_summary.txt "
                  "containing a summary with less than 100 words of the incident timeline, its cause, and its resolution.",
                  "In records.csv, change the status of the row with ID 104 to complete, and save the result as updated_records.csv.",
                  "Delete notes/old-draft.txt.", "What is the average?",
                  "Create config.json containing {\"debug\": true}"]:
            self.assertEqual(router.classify(p, self.ws), "simple", p)

    def test_cp3_prompts(self):
        import prog_variants
        import gen_logs
        self.assertEqual(router.classify(prog_variants.prompt("OFFICIAL"), self.ws), "programming")
        _, _, dbg = gen_logs.generate(1, set(), "ORD-77341", ["gateway", "orders", "inventory", "payments"], 0)
        self.assertEqual(router.classify(dbg, self.ws), "debugging")
        self.assertEqual(router.classify("Implement `solution.py` with def two_sum(nums, target) returning indices.",
                                         self.ws), "programming")
        self.assertEqual(router.classify("`buggy_stats.py` fails its tests in `test_stats.py`; fix it without "
                                         "editing the tests.", self.ws), "programming")


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(helpers.TESTS, "fixtures"))
    unittest.main()
