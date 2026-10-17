"""UT-V01..V05 (verification), UT-T01..T07 (tools), CP3 spec parsing, report
validation, profiling and the change audit."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402
sys.path.insert(0, os.path.join(helpers.TESTS, "fixtures"))
import deeptools  # noqa: E402
import gen_logs  # noqa: E402
import gen_transactions  # noqa: E402
import guards  # noqa: E402
import profiling  # noqa: E402
import prog_variants  # noqa: E402
import taskspec  # noqa: E402
from report import Report  # noqa: E402
from sandbox import Sandbox  # noqa: E402
from workspace import ChangeAudit  # noqa: E402

CP3_DEBUG = gen_logs.generate(1, set(), "ORD-77341", ["gateway", "orders", "inventory", "payments"], 0)[2]
CP3_PROG = prog_variants.prompt("OFFICIAL")


class Verification(unittest.TestCase):
    def test_v01_limit_parsers(self):
        self.assertEqual(guards.parse_word_limit("a summary with less than 100 words"), 99)
        self.assertEqual(guards.parse_word_limit("at most 50 words"), 50)
        self.assertEqual(guards.parse_word_limit("under 80 words"), 79)
        self.assertEqual(guards.parse_word_limit("about 100 words"), 100)
        self.assertIsNone(guards.parse_word_limit("at least 50 words"))
        self.assertEqual(guards.parse_sentence_count("summarize it in 3 sentences"), 3)
        self.assertEqual(guards.parse_bullet_count("exactly 3 bullet points"), 3)
        self.assertEqual(guards.count_bullets("- a\n- b\n* c\nnot"), 3)
        self.assertEqual(guards.count_sentences("One. Two at 10.5 kg! Three?"), 3)

    def test_v02_numbers_subset(self):
        src = "Alerts at 09:12. Rollback 10:05. Fixed by 10:20. Budget $45,000."
        self.assertEqual(guards.unsupported_numbers("At 9:12 alerts; rollback 10:05; fixed 10:20; $45,000.", src), [])
        self.assertEqual(guards.unsupported_numbers("Fixed by 11:30 for $40,000.", src), ["11:30", "40,000"])

    def test_v03_deliverables(self):
        self.assertEqual(taskspec.deliverables(CP3_PROG), ["tally.py"])
        self.assertEqual(taskspec.deliverables(CP3_DEBUG)[0], "REPORT.json")
        self.assertIn("reports/x.txt", taskspec.deliverables("Summarize a.txt and save it to reports/x.txt."))
        self.assertIn("active_orders.csv", taskspec.deliverables(
            "In orders.csv, remove every cancelled row and save the result as active_orders.csv."))
        self.assertIn("buggy_stats.py", taskspec.deliverables("`buggy_stats.py` fails its tests in `test_stats.py`."))

    def test_v03b_report_keys_and_types(self):
        keys = taskspec.report_keys(CP3_DEBUG)
        names = [k for k, _ in keys]
        self.assertEqual(names[:6], ["gateway_request_id", "upstream_trace_id", "hold_id", "worker",
                                     "root_cause_component", "payment_intent_id"])
        self.assertEqual(taskspec.expected_key_count(CP3_DEBUG), 12)
        types = dict((k, taskspec.key_type(k, d)) for k, d in keys)
        self.assertTrue(all(types[k] == "str" for k in names[:6]), types)
        full = gen_logs.generate(1, set(), "ORD-77341", ["gateway", "orders", "inventory", "payments"], 6)[2]
        t = dict((k, taskspec.key_type(k, d)) for k, d in taskspec.report_keys(full))
        self.assertEqual(t["gateway_status"], "int")
        self.assertEqual(t["orders_timeout_ms"], "int")
        self.assertEqual(t["worker_queue_depth"], "int")
        self.assertEqual(t["hold_queued_at"], "str")

    def test_v04b_key_value_prefix_is_stripped(self):
        # Live miss: the model wrote "sched=s-20410" / "upload=up-7731" instead of the bare IDs.
        ws = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(ws, "logs"))
            p = os.path.join(ws, "logs", "runner.log")
            with open(p, "w") as f:
                f.write("2026-09-20 02:10:00 sched=s-20410 tenant=acme-17 run=run-88c1 url=http://h/x\n")
            keys = [("schedule_id", "the scheduler's ID (`sched=`) of the failed run"),
                    ("tenant", "the tenant"), ("link", "the url")]
            r = Report(Sandbox(ws), "REPORT.json", keys, 3, [p], [])
            r.set({"schedule_id": "sched=s-20410", "tenant": "acme-17", "link": "http://h/x"})
            self.assertEqual(r.values["schedule_id"], "s-20410")
            self.assertEqual(r.values["tenant"], "acme-17")
            self.assertEqual(r.values["link"], "http://h/x")   # no '=' -> untouched
            r.set({"schedule_id": "sched=s-99999"})            # bare value not in the logs -> kept as written
            self.assertEqual(r.values["schedule_id"], "sched=s-99999")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_v04_report_validator(self):
        ws = tempfile.mkdtemp()
        try:
            logs, key, prompt = gen_logs.generate(3, {"decoy_class"}, "ORD-77341",
                                                  ["gateway", "orders", "inventory", "payments"], 6)
            os.makedirs(os.path.join(ws, "logs"))
            ev = []
            for n, t in logs.items():
                p = os.path.join(ws, "logs", n + ".log")
                with open(p, "w") as f:
                    f.write(t)
                ev.append(p)
            r = Report(Sandbox(ws), "REPORT.json", taskspec.report_keys(prompt), 12, ev, ["ORD-77341"])
            r.write()
            data = json.load(open(os.path.join(ws, "REPORT.json")))
            self.assertEqual(len(data), 12)
            self.assertEqual(data["gateway_status"], 0)           # type-correct placeholder
            self.assertIn("extra_key: REJECTED", r.set({"extra_key": "x"}))
            out = r.set({"gateway_status": "502", "worker_queue_depth": 3.0})
            self.assertEqual(r.values["gateway_status"], 502)     # "502" -> 502
            self.assertIsInstance(r.values["worker_queue_depth"], int)
            self.assertIn("NOT FOUND", r.set({"hold_id": "hold-000000"}))
            self.assertIn("NOT VERBATIM", r.set({"root_cause_component": key["root_cause_component"].upper()}))
            r.set(dict((k, key[k]) for k in ("gateway_request_id", "upstream_trace_id", "hold_id", "worker")))
            self.assertEqual(r.check("worker"), "ok (found verbatim in the logs)")
            decoy = key["root_cause_component"][0].lower() + key["root_cause_component"][1:]
            self.assertIn("UNLINKED", r.set({"root_cause_component": decoy}))
            r.set({"root_cause_component": key["root_cause_component"]})
            self.assertTrue(r.check("root_cause_component").startswith("ok"))
            self.assertTrue(r.file_ok())
        finally:
            shutil.rmtree(ws)

    def test_v05_change_audit_restores(self):
        ws = tempfile.mkdtemp()
        try:
            for n, c in (("data.csv", "a,b\n1,2\n"), ("TASK.md", "task"), ("keep/x.txt", "x")):
                os.makedirs(os.path.dirname(os.path.join(ws, n)) or ws, exist_ok=True)
                with open(os.path.join(ws, n), "w") as f:
                    f.write(c)
            a = ChangeAudit(ws)
            with open(os.path.join(ws, "data.csv"), "w") as f:
                f.write("corrupted")
            os.unlink(os.path.join(ws, "keep/x.txt"))
            with open(os.path.join(ws, "tally.py"), "w") as f:
                f.write("print(1)")
            with open(os.path.join(ws, "scratch_test.py"), "w") as f:
                f.write("junk")
            os.makedirs(os.path.join(ws, "__pycache__"))
            open(os.path.join(ws, "__pycache__", "m.pyc"), "w").close()
            acts = a.finalize({"tally.py"}, lambda r: r == "tally.py")
            self.assertEqual(open(os.path.join(ws, "data.csv")).read(), "a,b\n1,2\n")
            self.assertEqual(open(os.path.join(ws, "keep/x.txt")).read(), "x")
            self.assertTrue(os.path.exists(os.path.join(ws, "tally.py")))
            self.assertFalse(os.path.exists(os.path.join(ws, "scratch_test.py")))
            self.assertFalse(os.path.exists(os.path.join(ws, "__pycache__")))
            self.assertTrue(any("restored data.csv" in x for x in acts))
            a.cleanup()
            self.assertFalse(os.path.exists(a.backup_dir))
        finally:
            shutil.rmtree(ws)


class Tools(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp())
        self.scratch = os.path.realpath(tempfile.mkdtemp())
        with open(os.path.join(self.ws, "app.log"), "w") as f:
            f.write("ok ORD-77341 a\nORD-773410 decoy\nORD-77341-retry x\nERROR order=ORD-77341 failed\n"
                    + "".join("noise %d\n" % i for i in range(3000)))
        with open(os.path.join(self.ws, "data.csv"), "w") as f:
            f.write("a\n1\n")
        self.t = deeptools.DeepTools(Sandbox(self.ws), self.scratch, "programming", ["tool.py"], {"data.csv"})

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)
        shutil.rmtree(self.scratch, ignore_errors=True)

    def test_t01_timeout_kills_process_group(self):
        t0 = time.monotonic()
        out = self.t.call("run_command", {"command": "sleep 999 & sleep 999; echo never", "timeout": 1})
        self.assertLess(time.monotonic() - t0, 8)
        self.assertIn("TIMED OUT", out)
        ps = subprocess.run(["pgrep", "-f", "sleep 999"], stdout=subprocess.PIPE).stdout.decode().split()
        self.assertEqual(ps, [], "grandchild survived")

    def test_t02_output_captured_not_leaked(self):
        out = self.t.call("run_command", {"command": "echo hello; echo err >&2"})
        self.assertIn("stdout:\nhello", out)
        self.assertIn("stderr:\nerr", out)

    def test_t03_blocklist(self):
        for cmd in ("curl http://x", "wget x", "pip install requests", "rm -rf /", "rm -rf ~", "sudo ls",
                    "python3 -m pip install x", "nc -l 80"):
            self.assertIn("refused", self.t.call("run_command", {"command": cmd}), cmd)

    def test_t04_stdin_is_devnull(self):
        out = self.t.call("run_python", {"code": "try:\n    input()\nexcept EOFError:\n    print('EOF')", "timeout": 5})
        self.assertIn("EOF", out)

    def test_t05_grep_word_boundary(self):
        out = self.t.call("grep", {"pattern": "ORD-77341", "path": "app.log"})
        self.assertIn("2 matching line(s)", out)
        self.assertNotIn("ORD-773410", out.split("identifiers")[0])
        out = self.t.call("grep", {"pattern": "noise", "path": "app.log", "max_matches": 10})
        self.assertIn("3000 matching line(s)", out)
        out = self.t.call("grep", {"pattern": "ORD-77341", "path": "app.log", "context": 1})
        self.assertIn("app.log:2-", out)

    def test_t06_truncation_head_and_tail(self):
        text = "".join("line %d\n" % i for i in range(5000))
        out = deeptools.truncate(text, 2000)
        self.assertTrue(out.startswith("line 0"))
        self.assertTrue(out.rstrip().endswith("line 4999"))
        self.assertIn("truncated", out)

    def test_t07_write_guard_and_scratch(self):
        self.assertIn("must not be modified", self.t.call("write_file", {"path": "data.csv", "content": "x"}))
        self.assertIn("only the deliverable", self.t.call("write_file", {"path": "helper.py", "content": "x"}))
        self.assertIn("scratch", self.t.call("write_file", {"path": "$SCRATCH/t.csv", "content": "a\n"}))
        self.assertTrue(os.path.exists(os.path.join(self.scratch, "t.csv")))
        self.assertIn("OK: wrote tool.py", self.t.call("write_file", {"path": "tool.py", "content": "x = 1\n"}))
        self.assertIn("outside the workspace", self.t.call("write_file", {"path": "/etc/x", "content": "x"}))
        self.assertIn("escapes the workspace", self.t.call("write_file", {"path": "../x", "content": "x"}))

    def test_edit_file_tolerance(self):
        self.t.call("write_file", {"path": "tool.py", "content": "def f():\n    if x:\n        return 2\n"})
        out = self.t.call("edit_file", {"path": "tool.py", "old": "if x:\n    return 2", "new": "if x:\n    return 3"})
        self.assertIn("OK: edited", out)
        self.assertEqual(open(os.path.join(self.ws, "tool.py")).read(), "def f():\n    if x:\n        return 3\n")
        self.assertIn("ALREADY APPLIED", self.t.call("edit_file", {"path": "tool.py", "old": "return 2",
                                                                    "new": "return 3"}))

    def test_unknown_tool_and_secret_env(self):
        self.assertIn("Valid tools", self.t.call("nope", {}))
        os.environ["QWEN_API_KEY"] = "sekrit"
        t = deeptools.DeepTools(Sandbox(self.ws), self.scratch, "programming", ["tool.py"], set())
        self.assertNotIn("sekrit", t.call("run_command", {"command": "env"}))


class Profiling(unittest.TestCase):
    def test_csv_profile_reports_anomalies(self):
        d = tempfile.mkdtemp()
        try:
            lines = gen_transactions.generate(4000, 5)
            p = os.path.join(d, "t.csv")
            with open(p, "w") as f:
                f.write("\n".join(lines) + "\n")
            facts = {}
            t0 = time.monotonic()
            text = profiling.csv_profile(p, "t.csv", facts)
            self.assertLess(time.monotonic() - t0, 2)
            self.assertIn("repeated header lines: 1", text)
            self.assertIn("wrong number of fields", text)
            self.assertIn("NOT numeric", text)
            self.assertTrue(facts["dates"] and facts["categories"]["category"])
            self.assertIn("food <- ", text)
        finally:
            shutil.rmtree(d)

    def test_log_stats(self):
        d = tempfile.mkdtemp()
        try:
            logs, _, _ = gen_logs.generate(2, set(), "ORD-77341", ["g", "o", "i", "p"], 0)
            p = os.path.join(d, "i.log")
            with open(p, "w") as f:
                f.write(logs["i"])
            s = profiling.log_stats(p, "i.log")
            self.assertIn("timestamps: ISO-8601", s)
            self.assertIn("levels:", s)
        finally:
            shutil.rmtree(d)

    def test_example_commands(self):
        cmds = taskspec.example_commands(CP3_PROG)
        self.assertIn("python3 tally.py count", cmds)
        self.assertIn("python3 tally.py range --from YYYY-MM-DD --to YYYY-MM-DD", cmds)
        self.assertFalse(any("<command>" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
