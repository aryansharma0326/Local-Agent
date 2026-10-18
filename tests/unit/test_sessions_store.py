"""UT-S01..S08: the persistent session store (agent/session.py)."""

import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402,F401
import session  # noqa: E402


def _writer(args):
    store, ws, i = args
    os.environ["AGENT_STATE_DIR"] = store
    s = session.Session(ws, "555")
    return s.append(session.make_turn("turn %d" % i, [], "success", "ok"), {})


class SessionStore(unittest.TestCase):
    def setUp(self):
        self.store = os.path.realpath(tempfile.mkdtemp(prefix="st_"))
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="ws_"))
        self.ws2 = os.path.realpath(tempfile.mkdtemp(prefix="ws2_"))
        self.saved = {k: os.environ.get(k) for k in ("AGENT_STATE_DIR", "AGENT_SESSION_DIR", "AGENT_SESSION_SCOPE",
                                                     "HOME", "XDG_STATE_HOME", "TMPDIR")}
        os.environ.pop("AGENT_SESSION_DIR", None)
        os.environ["AGENT_STATE_DIR"] = self.store

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for d in (self.store, self.ws, self.ws2):
            shutil.rmtree(d, ignore_errors=True)

    def t(self, text):
        return session.make_turn(text, [{"call": "csv_aggregate {}", "result": "RESULT: 300", "tool": "csv_aggregate",
                                         "args": {"path": "sales.csv", "column": "revenue", "operation": "sum"}}],
                                 "success", "ok " + text)

    def test_s01_no_session_id_reads_and_writes_nothing(self):
        # main.run() only creates a Session when an id is given; the store stays empty.
        import main
        self.assertEqual(main.parse_args(["hello"])[1], None)
        self.assertEqual(os.listdir(self.store), [])

    def test_s02_round_trip_turns_and_context(self):
        s = session.Session(self.ws, "1001")
        self.assertEqual(s.turns, [])
        ctx = session.turn_context("Read sales.csv and tell me the total of the revenue column.",
                                   self.t("a")["actions"] and [{"tool": "csv_aggregate", "result": "RESULT: 300",
                                                                 "args": {"path": "sales.csv", "column": "revenue",
                                                                          "operation": "sum"}}], "success")
        self.assertTrue(s.append(self.t("a"), ctx))
        s2 = session.Session(self.ws, "1001")
        self.assertEqual([x["prompt"] for x in s2.turns], ["a"])
        self.assertEqual(s2.context["last_csv"]["path"], "sales.csv")
        self.assertEqual(s2.context["last_csv"]["last_column"], "revenue")
        text = session.render(s2.turns, s2.context)
        self.assertIn("Last CSV operation", text)
        self.assertIn("column revenue", text)

    def test_s03_exact_string_ids(self):
        session.Session(self.ws, "7").append(self.t("seven"), {})
        self.assertEqual(session.Session(self.ws, "007").turns, [])
        self.assertEqual(session.session_key("007"), "007")
        self.assertTrue(session.session_key("abc").startswith("h") and len(session.session_key("abc")) == 41)
        session.Session(self.ws, "abc").append(self.t("named"), {})
        self.assertEqual(session.Session(self.ws, "abc").turns[0]["prompt"], "named")
        self.assertTrue(os.path.exists(os.path.join(self.store, "session_7.json")))

    def test_s04_workspace_mismatch_is_stale(self):
        session.Session(self.ws, "1001").append(self.t("old"), {})
        fresh = session.Session(self.ws2, "1001")
        self.assertEqual(fresh.turns, [])
        self.assertTrue(any(f.startswith("session_1001.json.stale-") for f in os.listdir(self.store)))
        # id-only scope keeps history across workspaces
        session.Session(self.ws, "2002").append(self.t("shared"), {})
        os.environ["AGENT_SESSION_SCOPE"] = "id"
        self.assertEqual(session.Session(self.ws2, "2002").turns[0]["prompt"], "shared")

    def test_s05_corrupt_file_backed_up(self):
        with open(os.path.join(self.store, "session_4.json"), "w") as f:
            f.write("{not json")
        s = session.Session(self.ws, "4")
        self.assertEqual(s.turns, [])
        self.assertTrue(any(f.startswith("session_4.json.corrupt-") for f in os.listdir(self.store)))
        self.assertTrue(s.append(self.t("y"), {}))
        self.assertEqual(session.Session(self.ws, "4").turns[0]["prompt"], "y")

    def test_s06_concurrent_writers(self):
        ctx = multiprocessing.get_context("fork") if hasattr(os, "fork") else multiprocessing
        with ctx.Pool(8) as pool:
            res = pool.map(_writer, [(self.store, self.ws, i) for i in range(20)])
        self.assertTrue(all(res))
        with open(os.path.join(self.store, "session_555.json")) as f:
            data = json.load(f)
        self.assertEqual(sorted(t["prompt"] for t in data["turns"]), sorted("turn %d" % i for i in range(20)))

    def test_s07_fallback_locations(self):
        blocker = os.path.join(self.store, "file")
        open(blocker, "w").close()
        os.environ["AGENT_STATE_DIR"] = os.path.join(blocker, "sub")   # cannot be created
        home = tempfile.mkdtemp(prefix="home_")
        try:
            os.environ["HOME"] = home
            os.environ.pop("XDG_STATE_HOME", None)
            d = session.store_dir(self.ws)
            self.assertEqual(d, os.path.join(home, ".local", "state", "hw1_agent", "sessions"))
            home2 = os.path.join(home, "ro")
            os.mkdir(home2)
            os.chmod(home2, 0o500)                                    # HOME unwritable -> next location
            os.environ["HOME"] = home2
            d2 = session.store_dir(self.ws)
            os.chmod(home2, 0o700)
            self.assertNotEqual(d2, d)
            self.assertIsNotNone(d2)
        finally:
            os.chmod(home, 0o700)
            shutil.rmtree(home, ignore_errors=True)
        # a store inside the workspace is never used
        os.environ["AGENT_STATE_DIR"] = os.path.join(self.ws, "state")
        self.assertFalse(session.store_dir(self.ws).startswith(self.ws))

    def test_s07b_everything_unwritable_is_stateless(self):
        orig = session._candidates
        session._candidates = lambda: [os.path.join(self.store, "f", "x")]
        open(os.path.join(self.store, "f"), "w").close()
        try:
            s = session.Session(self.ws, "9")
            self.assertIsNone(s.dir)
            self.assertFalse(s.append(self.t("x"), {}))
        finally:
            session._candidates = orig

    def test_s08_compaction_keeps_standing_instructions_and_csv(self):
        s = session.Session(self.ws, "77")
        s.append(session.make_turn("From now on, reply with only the number.", [], "success", "OK"),
                 session.turn_context("From now on, reply with only the number.", [], "success"))
        for i in range(49):
            s.append(session.make_turn("question %d " % i + "x" * 3000, [], "success", "m" * 3000),
                     session.turn_context("q", [{"tool": "csv_aggregate", "result": "RESULT: 1",
                                                 "args": {"path": "sales.csv", "column": "revenue"}}], "success"))
        s2 = session.Session(self.ws, "77")
        self.assertEqual(len(s2.turns), session.MAX_TURNS)
        text = session.render(s2.turns, s2.context)
        self.assertLess(len(text), 70000)                 # ~23k tokens worst case
        self.assertIn("reply with only the number", text)  # standing instruction survives turn eviction
        self.assertIn("sales.csv", text)

    def test_nothing_written_into_workspace(self):
        session.Session(self.ws, "8").append(self.t("x"), {})
        self.assertEqual(os.listdir(self.ws), [])


if __name__ == "__main__":
    unittest.main()
