"""Regression tests for the bugs found by the adversarial E2E suite (tests/adversarial)."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402,F401
import csvtools  # noqa: E402
import guards  # noqa: E402
import harness  # noqa: E402
import router  # noqa: E402
import taskspec  # noqa: E402
from sandbox import Sandbox  # noqa: E402


class StatementTurns(unittest.TestCase):
    """CX-12/15/17/18: first-turn statements that contain a file-like verb."""

    STATEMENTS = [
        "Update: my sister's birthday is on the 14th of June.",   # held-out phrasings
        "FYI: my gym closes at 9 PM on Sundays.",
        "Heads up: the team offsite moved to Thursday.",
        "Note: my locker code is 4417.",
        "Read receipts are turned on for my phone.",                # round 2: verb-like nouns
        "List prices at my store went up by 5 percent this month.",
        "Check-in time at my hotel is 3 PM.",
        "I store my recipes in recipes.txt on my laptop.",          # first person, names a file
        "The new status for record 104 should be 'shipped'. Don't change anything yet.",
        "My total budget for the Lisbon trip is 1200 euros.",
        "I always copy my lecture notes on Mondays.",
        "My shopping list for tomorrow has eggs, rice and spinach.",
        "I read about 20 pages of my novel every night.",
        "My dentist appointment has been updated to 4 PM on Monday.",
        "Correction: my preferred drink is coffee now, replacing tea.",
        "My project code name is Cedar. Remember it for this conversation.",
    ]
    REQUESTS = [
        "I would appreciate a summary of a.txt and b.txt in sum.txt.",
        "We need a file plan.txt that says launch on Monday.",
        "My notes file todo.txt should have three lines: a, b, c.",
        "We should delete draft.txt.",
        "Update: delete draft.txt.",
        "Remove rows that are cancelled.",
        "My notes.txt needs to be deleted.",
        "Count the rows in data.csv.",
        "Read sales.csv and tell me the total of the revenue column.",
        "What is the average?",
        "Delete it.",
        "Multiply that by 2.",
        "Now apply that change in records.csv and save the result as updated_records.csv.",
        "Discard old.txt.",
        "Note that draft.txt is obsolete — delete it.",
        "In orders.csv, remove every row whose status is cancelled.",
        "I need the total of the revenue column.",
    ]

    def test_statements(self):
        for s in self.STATEMENTS:
            self.assertTrue(harness.is_statement(s), s)

    def test_requests(self):
        for s in self.REQUESTS:
            self.assertFalse(harness.is_statement(s), s)


class CsvCompare(unittest.TestCase):
    def test_non_numeric_cells_never_match_numeric_order(self):  # CV-A06
        self.assertFalse(csvtools.compare("N/A", "gt", "100"))
        self.assertFalse(csvtools.compare("", "gt", "100"))
        self.assertTrue(csvtools.compare("150", "gt", "100"))

    def test_dates_compare_as_dates(self):  # CV-A17
        self.assertFalse(csvtools.compare("10/2/2026", "lt", "March 1, 2026"))
        self.assertTrue(csvtools.compare("12/1/2025", "lt", "2026-03-01"))
        self.assertTrue(csvtools.compare("2026-01-05", "ge", "2026-01-05"))

    def test_ids_match_exactly_first(self):  # CV-A16
        t = csvtools.Table("id,name\n007,bond\n7,seven\n70,seventy\n", "items.csv")
        rows = t.matching_id(0, "007")
        self.assertEqual([f[1] for _, f in rows], ["bond"])


class ReportKeyTypes(unittest.TestCase):
    def test_types(self):  # DB-1/2/3
        kt = taskspec.key_type
        self.assertEqual(kt("orders_error_code", "the error code the orders service logged"), "auto")
        self.assertEqual(kt("payment_amount_cents", "the amount, in cents, of the payment intent"), "int")
        self.assertEqual(kt("gateway_status", "the HTTP status code the gateway returned"), "int")
        self.assertEqual(kt("exit_code", "the exit code"), "int")


class ReportChecks(unittest.TestCase):
    def test_root_cause_and_lock_checks(self):  # DB-1/DB-3
        import json
        import glob
        import report
        sys.path.insert(0, os.path.join(helpers.ROOT, "tests", "adversarial", "cp3"))
        import gen_incident
        ws = tempfile.mkdtemp()
        try:
            key, prompt = gen_incident.write(14, ws)
            r = report.Report(Sandbox(ws), "REPORT.json", taskspec.report_keys(prompt), 12,
                              sorted(glob.glob(os.path.join(ws, "logs", "*.log"))), anchors=["ORD-77341"])
            r.set(dict((k, key[k]) for k in ("gateway_request_id", "upstream_trace_id", "hold_id", "worker")))
            out = r.set({"root_cause_component": "HoldService"})
            self.assertIn("CHECK", out)
            self.assertIn(key["root_cause_component"], out)
            r.set({"root_cause_component": key["root_cause_component"]})
            self.assertTrue(r.check("root_cause_component").startswith("ok"))
            self.assertIn(key["lock_name"], r.set({"lock_name": key["root_cause_component"]}))
            r.set({"lock_name": key["lock_name"]})
            self.assertTrue(r.check("lock_name").startswith("ok"), r.check("lock_name"))
        finally:
            shutil.rmtree(ws)


class SummaryGuards(unittest.TestCase):
    def test_source_description_is_not_a_limit(self):  # SM-A02
        p = ("The notes in alpha.txt and beta.txt are each under 40 words. Combine them into summary/combined.txt "
             "as a summary of 50 to 80 words.")
        self.assertEqual(guards.parse_word_limit(p), 80)
        self.assertEqual(guards.parse_word_limit("Summarize a.txt into c.txt. Keep it under 50 words."), 49)

    def test_bullets_per_source(self):  # SM-A01
        p = ("Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create reports/b.txt with exactly one "
             "bullet point per source file (three bullets in total), each bullet under 25 words.")
        self.assertEqual(guards.parse_bullet_count(p), 3)
        self.assertEqual(guards.parse_bullet_count("Use a.txt, b.txt and c.txt to write out.txt with one bullet "
                                                   "point per source file."), 3)

    def test_minimum_words_and_inline_bullets(self):  # SM-A02, SM-A01
        p = ("The notes in alpha.txt and beta.txt are each under 40 words. Combine them into summary/combined.txt "
             "as a summary of 50 to 80 words.")
        self.assertEqual(guards.parse_word_min(p), 50)
        self.assertIsNone(guards.parse_word_min("in under 100 words"))
        self.assertEqual(guards.split_inline_bullets("- a.txt: one. - b.txt: two."), "- a.txt: one.\n- b.txt: two.")

    def test_per_item_limits_and_trim_keeps_lines(self):  # SM-A01
        p = ("Use a.txt, b.txt and c.txt to create r.txt with one bullet point per source file, each bullet under "
             "25 words.")
        self.assertIsNone(guards.parse_word_limit(p))
        self.assertEqual(guards.parse_item_word_limit(p), 24)
        self.assertEqual(guards.trim_to_words("- a b c.\n- d e f.\n- g h i.", 8), "- a b c.\n- d e f.")

    def test_requested_totals_are_supported(self):  # SM-A03
        src = "lasted 12 minutes\nlasted 18 minutes\nlasted 15 minutes\n"
        self.assertEqual(guards.unsupported_numbers("45 minutes in total", src, "state the total minutes"), [])
        self.assertEqual(guards.unsupported_numbers("45 minutes", src, "summarize"), ["45"])

    def test_requested_durations_are_supported(self):  # SM-B05 (Kaggle vLLM run)
        src = "09:02 UTC - alert\n09:44 UTC - cause found\n10:05 UTC - closed\n"
        ask = "a summary that says how many minutes the incident lasted"
        self.assertEqual(guards.unsupported_numbers("It lasted 63 minutes (09:02 to 10:05).", src, ask), [])
        self.assertEqual(guards.unsupported_numbers("It lasted 71 minutes.", src, ask), ["71"])
        self.assertEqual(guards.unsupported_numbers("It lasted 63 minutes.", src, "summarize"), ["63"])
        self.assertEqual(guards.clock_span(src), ("09:02", "10:05", 63))
        self.assertIsNone(guards.clock_span("no times here"))


class AnswerNumbers(unittest.TestCase):
    def test_invented_numbers(self):  # RQ-A08
        f = "| Notebook | Blue | 12 |\n| Notebook (damaged) | Blue | 3 |\n"
        self.assertEqual(guards.answer_unsupported_numbers("There are 15 blue notebooks.", f), ["15"])
        self.assertEqual(guards.answer_unsupported_numbers("There are 12 blue notebooks.", f), [])
        self.assertEqual(guards.answer_unsupported_numbers("There are 3 rows.", "a\nb\n"), [])   # single digits ignored
        self.assertEqual(guards.answer_unsupported_numbers("Costs $1,200.50 on 09:05", "price 1200.50 at 9:05"), [])
        self.assertEqual(guards.answer_unsupported_numbers("Due 2026-11-03", "due November 3, 2026 (2026-11-03)"), [])
        self.assertEqual(guards.answer_unsupported_numbers("Raj's test score is 44.", "id,name,a,b\n2,Raj,30,44\n"), [])
        self.assertEqual(guards.answer_unsupported_numbers("Total 1,200.50", "a,amount\n1,\"1,200.50\"\n"), [])


class Router(unittest.TestCase):
    def test_literal_create_stays_simple(self):  # CR-A04
        ws = tempfile.mkdtemp()
        try:
            p = "Create notes.txt containing: implement parser.py and fix the failing unit tests"
            self.assertEqual(router.classify(p, ws), "simple")
        finally:
            shutil.rmtree(ws)


class DeleteRollback(unittest.TestCase):
    def test_undo_deletes(self):  # DL-A12
        ws = tempfile.mkdtemp()
        try:
            with open(os.path.join(ws, "a.log"), "w") as f:
                f.write("a\n")
            sb = Sandbox(ws)
            sb.delete_file("a.log")
            self.assertFalse(os.path.exists(os.path.join(ws, "a.log")))
            self.assertEqual(sb.undo_deletes(), ["a.log"])
            with open(os.path.join(ws, "a.log")) as f:
                self.assertEqual(f.read(), "a\n")
        finally:
            shutil.rmtree(ws)


class Requirements(unittest.TestCase):
    def test_requirement_items(self):  # PG-D/PG-E
        p = ("## Requirements\n\n1. **`count`** — prints the count.\n2. **`total`** — prints\n   the sum.\n\n"
             "7–12. *(hidden)*\n")
        self.assertEqual(taskspec.requirement_items(p), ["1. **`count`** — prints the count.",
                                                         "2. **`total`** — prints the sum."])


class Round2(unittest.TestCase):
    def test_word_limit_phrasings(self):  # SM-B01/02/03
        p1 = "Use a.txt, b.txt and c.txt to create r.txt as 3 bullet points of at most 15 words each."
        self.assertIsNone(guards.parse_word_limit(p1))
        self.assertEqual(guards.parse_item_word_limit(p1), 15)
        p2 = "Summarize a.txt and b.txt into d.txt in exactly 3 sentences, each under 20 words."
        self.assertIsNone(guards.parse_word_limit(p2))
        self.assertEqual((guards.parse_item_word_limit(p2), guards.item_unit(p2)), (19, "sentence"))
        p3 = "Combine a.txt and b.txt into s.txt as a summary between 60 and 90 words long."
        self.assertEqual((guards.parse_word_limit(p3), guards.parse_word_min(p3)), (90, 60))

    def test_csv_day_first_or_and(self):  # CV-B01/02/03
        t = csvtools.Table("date,event\n05/03/2026,prep\n15/03/2026,review\n20/02/2026,kickoff\n"
                           "01/04/2026,release\n", "e.csv")
        self.assertEqual(csvtools.delete_rows(t, "date", "less_than", "10/3/2026")[0],
                         "date,event\n15/03/2026,review\n01/04/2026,release\n")
        t = csvtools.Table("id,status\n1,cancelled\n2,shipped\n3,refunded\n", "o.csv")
        self.assertEqual(csvtools.delete_rows(t, "status", "equals", "", ["cancelled", "refunded"])[0],
                         "id,status\n2,shipped\n")
        self.assertEqual(csvtools.delete_rows(t, "status", "equals", "cancelled or refunded")[0],
                         "id,status\n2,shipped\n")
        t = csvtools.Table("sku,qty,status\nA,0,pending\nB,0,shipped\nC,5,pending\n", "s.csv")
        self.assertEqual(csvtools.delete_rows(t, "qty", "equals", "0", None, "status", "equals", "pending")[0],
                         "sku,qty,status\nB,0,shipped\nC,5,pending\n")
        t = csvtools.Table("date,e\n3/15/2026,a\n12/1/2025,b\n", "u.csv")  # US dates still month-first
        self.assertEqual(csvtools.delete_rows(t, "date", "less_than", "March 1, 2026")[0], "date,e\n3/15/2026,a\n")

    def test_operator_direction_guard(self):  # CV-B01: 'before' sent as greater_or_equal
        o = harness.Harness.__new__(harness.Harness)
        o.prompt = "In events.csv, remove every row whose date is before 10 March 2026."
        self.assertIn("less_than", o._operator_contradiction({"operator": "greater_or_equal"}) or "")
        o = harness.Harness.__new__(harness.Harness)
        o.prompt = "In events.csv, remove every row whose date is before 10 March 2026."
        self.assertIsNone(o._operator_contradiction({"operator": "less_than"}))
        o.prompt = "In grades.csv, remove every row whose score is at least 50."
        self.assertIsNone(o._operator_contradiction({"operator": "greater_or_equal"}))

    def test_number_keys_keep_logged_type(self):  # DB-B02
        self.assertEqual(taskspec.key_type("order_number", "the order number exactly as logged"), "auto")

    def test_root_cause_skips_framework_frames(self):  # DB-B01
        import glob
        import report
        sys.path.insert(0, os.path.join(helpers.ROOT, "tests", "adversarial", "cp3"))
        import gen_incident
        ws = tempfile.mkdtemp()
        try:
            key, prompt = gen_incident.write(21, ws, variant="pool")
            r = report.Report(Sandbox(ws), "REPORT.json", taskspec.report_keys(prompt), 12,
                              sorted(glob.glob(os.path.join(ws, "logs", "*.log"))), anchors=["ORD-77341"])
            r.set(dict((k, key[k]) for k in ("gateway_request_id", "upstream_trace_id", "hold_id", "worker")))
            self.assertEqual(r.cause_candidates()[0], "SupplierFeedClient")
            self.assertTrue(r.set({"root_cause_component": "SupplierFeedClient"}).split("\n")[0].endswith(
                "ok (found verbatim in the logs)"))
            self.assertIn("CHECK", r.set({"root_cause_component": "CouponBatchLoader"}))
        finally:
            shutil.rmtree(ws)


class Round3Debug(unittest.TestCase):
    def _report(self, ws, seed, variant=None):
        import glob
        import report
        sys.path.insert(0, os.path.join(helpers.ROOT, "tests", "adversarial", "cp3"))
        import gen_incident
        key, prompt = gen_incident.write(seed, ws, variant=variant)
        r = report.Report(Sandbox(ws), "REPORT.json", taskspec.report_keys(prompt), 12,
                          sorted(glob.glob(os.path.join(ws, "logs", "*.log"))), anchors=["ORD-77341"])
        return r, key

    def test_timestamp_from_the_named_event_line(self):  # item 13
        import re
        ws = tempfile.mkdtemp()
        try:
            r, key = self._report(ws, 21, "pool")
            r.set(dict(key))
            self.assertTrue(r.check("hold_queued_at").startswith("ok"))
            log = open(os.path.join(ws, "logs", "inventory.log")).read()
            created = re.search(r"(\S+ \S+) INFO  \[[^]]+\] c.s.i.HoldService - hold=%s status=CREATED"
                                % key["hold_id"], log).group(1)
            out = r.set({"hold_queued_at": created})
            self.assertIn(key["hold_queued_at"], out)
        finally:
            shutil.rmtree(ws)

    def test_near_miss_identifier(self):  # item 12
        import report
        self.assertEqual(report._edit_distance("SkuIndexRebuilders", "SkuIndexRebuilder", 2), 1)
        ws = tempfile.mkdtemp()
        try:
            r, key = self._report(ws, 14)
            r.set(dict(key))
            for k in key:
                self.assertTrue(r.check(k).startswith("ok"), (k, r.check(k)))
        finally:
            shutil.rmtree(ws)


class Round3Programming(unittest.TestCase):
    def test_transient_endpoint_failure_is_retried(self):  # item 14
        import types
        import deep
        from errors import AgentError  # noqa: F401
        from llm import LLMError
        calls, waits = [], []

        def complete(msgs, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise LLMError("The language model request failed (connection failure (SSLV3_ALERT_BAD_RECORD_MAC)).")
            return "REPLY"
        fake = types.SimpleNamespace(llm=types.SimpleNamespace(complete=complete),
                                     tools=types.SimpleNamespace(schemas=lambda: []),
                                     _TRANSIENT=deep.DeepAgent._TRANSIENT)
        out = deep.DeepAgent._call_model(fake, [], 10, 1, 60, sleep=waits.append)
        self.assertEqual((out, len(calls), waits), ("REPLY", 2, [5]))

        def bad(msgs, **kw):
            raise LLMError("The language model request failed (HTTP 401).")
        fake.llm = types.SimpleNamespace(complete=bad)
        with self.assertRaises(LLMError):
            deep.DeepAgent._call_model(fake, [], 10, 1, 60, sleep=waits.append)
        self.assertEqual(waits, [5])  # a non-transient error is not retried

    def test_run_command_allowlist(self):  # item 16
        import deeptools
        ws, scratch = tempfile.mkdtemp(), tempfile.mkdtemp()
        try:
            with open(os.path.join(ws, "p.py"), "w") as f:
                f.write("print('ok')\n")
            t = deeptools.DeepTools(Sandbox(ws), scratch, "programming", ["p.py"], set())
            self.assertIn("ok", t.call("run_command", {"command": "python3 p.py"}))
            for bad in ("ls", "python3 p.py | cat", "python3 p.py && rm -rf ~", "python3 -m pip install x",
                        "python3 p.py > x.txt", "bash -c 'python3 p.py'"):
                self.assertIn("refused", t.call("run_command", {"command": bad}), bad)
        finally:
            shutil.rmtree(ws)
            shutil.rmtree(scratch)


if __name__ == "__main__":
    unittest.main()
