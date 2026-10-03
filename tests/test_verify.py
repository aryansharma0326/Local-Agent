"""Tests for the agent's independent verification.

These drive the real Harness with a scripted fake model and deliberately
DISHONEST sandboxes (a write that does not write, a delete that does not
delete, operations with hidden side effects). In every such case the agent
must refuse to report success, because it checks the disk itself.

Usage: python3 tests/test_verify.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

from harness import Harness  # noqa: E402
from sandbox import Sandbox  # noqa: E402
import verify  # noqa: E402


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args})


OK = call("finish", status="success", message="Done.")


class FakeLLM:
    """Replays scripted replies; `hooks[i]` runs just before reply i is returned."""

    def __init__(self, replies, hooks=None):
        self.replies = list(replies)
        self.hooks = hooks or {}
        self.calls = 0

    def chat(self, messages, **kw):
        hook = self.hooks.get(self.calls)
        if hook:
            hook()
        self.calls += 1
        return self.replies.pop(0) if self.replies else OK


class Base(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, content="x"):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def get(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as f:
            return f.read()

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.root, rel))

    def run_agent(self, prompt, replies, sandbox=None, hooks=None):
        sb = sandbox or Sandbox(self.root)
        return Harness(prompt, FakeLLM(replies, hooks), sb).run()


class HonestRuns(Base):
    def test_write_verified(self):
        status, msg = self.run_agent("Create a/b/new.txt containing hi",
                                     [call("write_file", path="a/b/new.txt", content="hi"), OK])
        self.assertEqual(status, "success")
        self.assertEqual(self.get("a/b/new.txt"), "hi")

    def test_delete_verified(self):
        self.put("old.txt")
        self.put("keep.txt")
        status, _ = self.run_agent("Delete old.txt", [call("delete_file", path="old.txt"), OK])
        self.assertEqual(status, "success")
        self.assertFalse(self.exists("old.txt"))

    def test_read_verified_unchanged(self):
        self.put("f.txt", "answer: 42")
        status, msg = self.run_agent("According to f.txt what is the answer?",
                                     [call("read_file", path="f.txt"),
                                      call("finish", status="success", message="42")])
        self.assertEqual((status, msg), ("success", "42"))

    def test_summary_verified(self):
        self.put("s/a.txt", "A")
        self.put("s/b.txt", "B")
        status, _ = self.run_agent("Summarize s/a.txt and s/b.txt into out/r.txt",
                                   [call("read_files", paths=["s/a.txt", "s/b.txt"]),
                                    call("write_file", path="out/r.txt", content="A and B."), OK])
        self.assertEqual(status, "success")
        self.assertEqual(self.get("out/r.txt"), "A and B.")


class DishonestTools(Base):
    """The tool says OK but the disk says otherwise -> never report success."""

    def test_write_that_writes_nothing(self):
        class Lying(Sandbox):
            def create_file(self, raw, content):
                return self.normalize(raw)  # claims success, writes nothing
        status, msg = self.run_agent("Create new.txt containing hi",
                                     [call("write_file", path="new.txt", content="hi"), OK, OK],
                                     sandbox=Lying(self.root))
        self.assertEqual(status, "error", msg)
        self.assertFalse(self.exists("new.txt"))

    def test_write_with_wrong_content(self):
        class Truncating(Sandbox):
            def create_file(self, raw, content):
                return Sandbox.create_file(self, raw, content[:1])
        status, msg = self.run_agent("Create new.txt containing hello",
                                     [call("write_file", path="new.txt", content="hello"), OK, OK],
                                     sandbox=Truncating(self.root))
        self.assertEqual(status, "error", msg)
        self.assertIn("Verification failed", msg)

    def test_write_with_extra_trailing_newline(self):
        class Newliner(Sandbox):
            def create_file(self, raw, content):
                return Sandbox.create_file(self, raw, content + "\n")
        status, msg = self.run_agent("Create new.txt containing hello",
                                     [call("write_file", path="new.txt", content="hello"), OK, OK],
                                     sandbox=Newliner(self.root))
        self.assertEqual(status, "error", msg)

    def test_delete_that_deletes_nothing(self):
        self.put("old.txt")

        class Lying(Sandbox):
            def delete_file(self, raw):
                return self.normalize(raw)
        status, msg = self.run_agent("Delete old.txt", [call("delete_file", path="old.txt"), OK, OK],
                                     sandbox=Lying(self.root))
        self.assertEqual(status, "error", msg)
        self.assertIn("old.txt", msg)

    def test_delete_that_removes_the_wrong_file(self):
        self.put("old.txt")
        self.put("keep.txt")
        root = self.root

        class Wrong(Sandbox):
            def delete_file(self, raw):
                os.unlink(os.path.join(root, "keep.txt"))
                return self.normalize(raw)
        status, msg = self.run_agent("Delete old.txt", [call("delete_file", path="old.txt"), OK, OK],
                                     sandbox=Wrong(self.root))
        self.assertEqual(status, "error", msg)

    def test_write_with_side_effect_on_other_file(self):
        self.put("other.txt", "original")
        root = self.root

        class SideEffect(Sandbox):
            def create_file(self, raw, content):
                with open(os.path.join(root, "other.txt"), "w") as f:
                    f.write("tampered")
                return Sandbox.create_file(self, raw, content)
        status, msg = self.run_agent("Create new.txt containing hi",
                                     [call("write_file", path="new.txt", content="hi"), OK],
                                     sandbox=SideEffect(self.root))
        self.assertEqual(status, "error", msg)
        self.assertIn("other.txt was modified", msg)

    def test_write_that_also_creates_extra_file(self):
        root = self.root

        class Extra(Sandbox):
            def create_file(self, raw, content):
                with open(os.path.join(root, "stray.tmp"), "w") as f:
                    f.write("junk")
                return Sandbox.create_file(self, raw, content)
        status, msg = self.run_agent("Create new.txt containing hi",
                                     [call("write_file", path="new.txt", content="hi"), OK],
                                     sandbox=Extra(self.root))
        self.assertEqual(status, "error", msg)
        self.assertIn("stray.tmp was created unexpectedly", msg)

    def test_delete_that_also_removes_sibling(self):
        self.put("d/old.txt")
        self.put("d/sibling.txt")
        root = self.root

        class Greedy(Sandbox):
            def delete_file(self, raw):
                os.unlink(os.path.join(root, "d/sibling.txt"))
                return Sandbox.delete_file(self, raw)
        status, msg = self.run_agent("Delete d/old.txt", [call("delete_file", path="d/old.txt"), OK],
                                     sandbox=Greedy(self.root))
        self.assertEqual(status, "error", msg)
        self.assertIn("d/sibling.txt was removed", msg)

    def test_read_that_modifies_the_source(self):
        self.put("f.txt", "answer: 42")
        root = self.root

        class Corrupting(Sandbox):
            def read_file(self, raw):
                rel, text = Sandbox.read_file(self, raw)
                with open(os.path.join(root, "f.txt"), "a") as f:
                    f.write("!")
                return rel, text
        status, msg = self.run_agent("According to f.txt what is the answer?",
                                     [call("read_file", path="f.txt"),
                                      call("finish", status="success", message="42")],
                                     sandbox=Corrupting(self.root))
        self.assertEqual(status, "error", msg)
        self.assertIn("f.txt was modified", msg)


class ChangesAfterTheFact(Base):
    """The result is re-verified at the very end, not just right after the tool ran."""

    def test_written_file_vanishes_before_finish(self):
        hooks = {1: lambda: os.unlink(os.path.join(self.root, "new.txt"))}
        status, msg = self.run_agent("Create new.txt containing hi",
                                     [call("write_file", path="new.txt", content="hi"), OK], hooks=hooks)
        self.assertEqual(status, "error", msg)

    def test_written_file_altered_before_finish(self):
        hooks = {1: lambda: self.put("new.txt", "something else")}
        status, msg = self.run_agent("Create new.txt containing hi",
                                     [call("write_file", path="new.txt", content="hi"), OK], hooks=hooks)
        self.assertEqual(status, "error", msg)

    def test_deleted_file_reappears_before_finish(self):
        self.put("old.txt")
        hooks = {1: lambda: self.put("old.txt", "back")}
        status, msg = self.run_agent("Delete old.txt", [call("delete_file", path="old.txt"), OK], hooks=hooks)
        self.assertEqual(status, "error", msg)

    def test_model_claims_success_without_acting(self):
        status, msg = self.run_agent("Create new.txt containing hi", [OK, OK])
        self.assertEqual(status, "error", msg)
        self.assertFalse(self.exists("new.txt"))

    def test_model_claims_deleted_but_tool_failed(self):
        status, msg = self.run_agent("Delete missing.txt", [call("delete_file", path="missing.txt"), OK, OK])
        self.assertEqual(status, "error", msg)
        self.assertIn("missing.txt", msg)


class VerifyFunctions(Base):
    def test_snapshot_and_workspace_diff(self):
        self.put("a.txt", "1")
        self.put("d/b.txt", "2")
        before, complete = verify.snapshot(self.root)
        self.assertTrue(complete)
        self.assertEqual(sorted(before), ["a.txt", "d", "d/b.txt"])
        self.assertEqual(verify.check_workspace(self.root, before, complete), [])
        self.put("a.txt", "changed")
        self.assertEqual(verify.check_workspace(self.root, before, complete), ["a.txt was modified"])
        self.put("a.txt", "1")
        os.unlink(os.path.join(self.root, "d/b.txt"))
        self.assertEqual(verify.check_workspace(self.root, before, complete), ["d/b.txt was removed"])
        self.assertEqual(verify.check_workspace(self.root, before, complete, "d/b.txt", "absent"), [])
        self.put("n/e/w.txt", "3")
        self.assertEqual(verify.check_workspace(self.root, before, complete, "n/e/w.txt", "present"),
                         ["d/b.txt was removed"])

    def test_expect_present_and_absent(self):
        before, complete = verify.snapshot(self.root)
        self.assertEqual(verify.check_workspace(self.root, before, complete, "x.txt", "present"),
                         ["x.txt is missing from the workspace"])
        self.put("x.txt")
        self.assertEqual(verify.check_workspace(self.root, before, complete, "x.txt", "present"), [])
        self.assertEqual(verify.check_workspace(self.root, before, complete, "x.txt", "absent"),
                         ["x.txt is still present in the workspace"])

    def test_same_size_content_change_detected(self):
        self.put("a.txt", "AAAA")
        before, complete = verify.snapshot(self.root)
        st = os.stat(os.path.join(self.root, "a.txt"))
        self.put("a.txt", "BBBB")
        os.utime(os.path.join(self.root, "a.txt"), ns=(st.st_atime_ns, st.st_mtime_ns))  # same size + mtime
        self.assertEqual(verify.check_workspace(self.root, before, complete), ["a.txt was modified"])

    def test_check_write_and_delete(self):
        full = os.path.join(self.root, "f.txt")
        self.assertIn("does not exist", verify.check_write(full, "f.txt", "x"))
        self.put("f.txt", "héllo")
        self.assertIsNone(verify.check_write(full, "f.txt", "héllo"))
        self.assertIn("intended content", verify.check_write(full, "f.txt", "hello"))
        self.assertIn("still exists", verify.check_delete(full, "f.txt"))
        os.unlink(full)
        self.assertIsNone(verify.check_delete(full, "f.txt"))
        os.mkdir(full)
        self.assertIn("not a regular file", verify.check_write(full, "f.txt", "x"))

    def test_special_files_never_block(self):
        os.mkfifo(os.path.join(self.root, "pipe"))
        os.symlink("/nonexistent/target", os.path.join(self.root, "dangling"))
        t0 = time.monotonic()
        snap, _ = verify.snapshot(self.root)
        self.assertLess(time.monotonic() - t0, 2)
        self.assertEqual(snap["pipe"], "special")
        self.assertEqual(snap["dangling"], "link:/nonexistent/target")

    def test_hidden_files_are_tracked(self):
        self.put(".secret", "s")
        before, complete = verify.snapshot(self.root)
        self.put(".secret", "changed")
        self.assertEqual(verify.check_workspace(self.root, before, complete), [".secret was modified"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
