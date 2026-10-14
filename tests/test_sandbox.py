"""Unit tests for the sandbox, tool registry, guardrails and LLM helpers.

Usage: python3 tests/test_sandbox.py
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
import unittest

sys.dont_write_bytecode = True
AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")
sys.path.insert(0, AGENT_DIR)

from errors import AgentError  # noqa: E402
import guards  # noqa: E402
import llm  # noqa: E402
import sandbox as sandbox_mod  # noqa: E402
from sandbox import Sandbox  # noqa: E402
import tools  # noqa: E402


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.outside = os.path.realpath(tempfile.mkdtemp())
        self.sb = Sandbox(self.root)

    def tearDown(self):
        for d in (self.root, self.outside):
            for dp, dns, fns in os.walk(d):
                for n in dns:
                    os.chmod(os.path.join(dp, n), 0o755)
            shutil.rmtree(d, ignore_errors=True)

    def p(self, rel):
        return os.path.join(self.root, rel)

    # -- normalize / resolve
    def test_normalize(self):
        self.assertEqual(self.sb.normalize("./a//b/./c.txt"), "a/b/c.txt")
        self.assertEqual(self.sb.normalize("a\\b.txt"), "a/b.txt")
        self.assertEqual(self.sb.normalize("  'x.txt' "), "x.txt")
        self.assertEqual(self.sb.normalize("notes/"), "notes")
        self.assertEqual(self.sb.normalize("my notes/a b.txt"), "my notes/a b.txt")
        self.assertEqual(self.sb.normalize("~draft.txt"), "~draft.txt")
        self.assertEqual(self.sb.normalize("a:b.txt"), "a:b.txt")
        self.assertEqual(self.sb.normalize("日本/ファイル.txt"), "日本/ファイル.txt")

    def test_rejects(self):
        for bad in ["", "  ", "/etc/passwd", "~", "~/x", "../x", "a/../../x", "a/..", "..", "C:/x", "C:",
                    "C:\\x", "\\\\server\\share", "a\x00b", ".", "./", 5, None]:
            with self.assertRaises(AgentError, msg=repr(bad)):
                self.sb.resolve(bad)

    def test_symlink_parent_escape(self):
        os.symlink(self.outside, self.p("out"))
        with self.assertRaises(AgentError):
            self.sb.resolve("out/x.txt")
        with self.assertRaises(AgentError):
            self.sb.create_file("out/sub/x.txt", "x")
        self.assertEqual(os.listdir(self.outside), [])

    def test_symlink_chain_escape(self):
        os.symlink(self.outside, self.p("l1"))
        os.symlink(self.p("l1"), self.p("l2"))
        with self.assertRaises(AgentError):
            self.sb.create_file("l2/x.txt", "x")
        with self.assertRaises(AgentError):
            self.sb.read_file("l2/x.txt")

    def test_symlinked_file_to_outside_cannot_be_read_or_written(self):
        with open(os.path.join(self.outside, "secret"), "w") as f:
            f.write("s")
        os.symlink(os.path.join(self.outside, "secret"), self.p("link"))
        with self.assertRaises(AgentError):
            self.sb.read_file("link")
        with self.assertRaises(AgentError):
            self.sb.create_file("link", "pwned")
        with open(os.path.join(self.outside, "secret")) as f:
            self.assertEqual(f.read(), "s")

    def test_internal_symlink_allowed(self):
        self.sb.create_file("real/f.txt", "v")
        os.symlink(self.p("real"), self.p("alias"))
        self.assertEqual(self.sb.read_file("alias/f.txt")[1], "v")

    def test_protected_agent_code(self):
        sb = Sandbox(sandbox_mod.SUBMISSION_DIR)
        for target in ("run_agent.sh", "agent/main.py", "agent"):
            with self.assertRaises(AgentError, msg=target):
                sb.resolve(target)
        sb.resolve("some_task_file.txt")  # other paths are fine

    def test_submission_dir_refused_from_outside_workspace(self):
        with self.assertRaises(AgentError):
            self.sb._check_real(os.path.join(sandbox_mod.SUBMISSION_DIR, "readme.md"), "readme.md")

    # -- create
    def test_create_read_delete(self):
        self.sb.create_file("a/b/c.txt", "héllo\nworld")
        self.assertEqual(self.sb.read_file("a/b/c.txt"), ("a/b/c.txt", "héllo\nworld"))
        self.sb.delete_file("a/b/c.txt")
        self.assertTrue(os.path.isdir(self.p("a/b")))
        with self.assertRaises(AgentError):
            self.sb.delete_file("a/b/c.txt")
        with self.assertRaises(AgentError):
            self.sb.delete_file("a/b")

    def test_create_exact_bytes(self):
        for content in ["", " ", "\n", "a\r\nb", "tab\there", "emoji 🚀", "no newline"]:
            self.sb.create_file("f.txt", content)
            with open(self.p("f.txt"), "rb") as f:
                self.assertEqual(f.read(), content.encode("utf-8"), repr(content))

    def test_create_parent_is_file(self):
        self.sb.create_file("f", "x")
        with self.assertRaises(AgentError):
            self.sb.create_file("f/g.txt", "y")

    def test_create_target_is_dir(self):
        os.mkdir(self.p("d"))
        with self.assertRaises(AgentError):
            self.sb.create_file("d", "x")

    def test_create_breaks_hard_link(self):
        outside_file = os.path.join(self.outside, "shared")
        with open(outside_file, "w") as f:
            f.write("orig")
        os.link(outside_file, self.p("shared"))
        self.sb.create_file("shared", "new")
        with open(outside_file) as f:
            self.assertEqual(f.read(), "orig")
        with open(self.p("shared")) as f:
            self.assertEqual(f.read(), "new")

    def test_create_readonly_file_refused(self):
        self.sb.create_file("ro.txt", "orig")
        os.chmod(self.p("ro.txt"), 0o444)
        with self.assertRaises(AgentError):
            self.sb.create_file("ro.txt", "new")
        with open(self.p("ro.txt")) as f:
            self.assertEqual(f.read(), "orig")

    def test_create_mode(self):
        self.sb.create_file("new.txt", "x")
        umask = os.umask(0)
        os.umask(umask)
        self.assertEqual(stat.S_IMODE(os.stat(self.p("new.txt")).st_mode), 0o666 & ~umask)
        os.chmod(self.p("new.txt"), 0o640)
        self.sb.create_file("new.txt", "y")
        self.assertEqual(stat.S_IMODE(os.stat(self.p("new.txt")).st_mode), 0o640)

    def test_create_no_temp_leftovers(self):
        self.sb.create_file("x.txt", "x")
        self.assertEqual(os.listdir(self.root), ["x.txt"])

    def test_create_fifo_target_refused(self):
        os.mkfifo(self.p("pipe"))
        with self.assertRaises(AgentError):
            self.sb.create_file("pipe", "x")

    # -- read
    def test_read_basename_fallback(self):
        self.sb.create_file("deep/dir/unique.txt", "u")
        self.assertEqual(self.sb.read_file("unique.txt")[0], "deep/dir/unique.txt")

    def test_read_basename_fallback_ambiguous(self):
        self.sb.create_file("a/same.txt", "1")
        self.sb.create_file("b/same.txt", "2")
        with self.assertRaises(AgentError):
            self.sb.read_file("same.txt")

    def test_read_encodings(self):
        cases = [(b"\xef\xbb\xbfBOM text", "BOM text"), ("Zoë".encode("utf-16"), "Zoë"),
                 ("Caf\xe9".encode("latin-1"), "Caf\xe9"), (b"", "")]
        for data, want in cases:
            with open(self.p("e.txt"), "wb") as f:
                f.write(data)
            self.assertEqual(self.sb.read_file("e.txt")[1], want, repr(data))

    def test_read_binary_refused(self):
        with open(self.p("b.bin"), "wb") as f:
            f.write(b"\x00\x01\x02binary")
        with self.assertRaises(AgentError):
            self.sb.read_file("b.bin")

    def test_read_fifo_and_dir_refused_without_blocking(self):
        os.mkfifo(self.p("pipe"))
        os.mkdir(self.p("dir"))
        t0 = time.monotonic()
        for target in ("pipe", "dir"):
            with self.assertRaises(AgentError):
                self.sb.read_file(target)
        self.assertLess(time.monotonic() - t0, 2)

    def test_read_unicode_normalization(self):
        nfd = unicodedata.normalize("NFD", "résumé.txt")
        with open(self.p(nfd), "w", encoding="utf-8") as f:
            f.write("cv")
        self.assertEqual(self.sb.read_file(unicodedata.normalize("NFC", "résumé.txt"))[1], "cv")

    # -- delete
    def test_delete_symlink_only(self):
        with open(os.path.join(self.outside, "t"), "w") as f:
            f.write("t")
        os.symlink(os.path.join(self.outside, "t"), self.p("l"))
        self.sb.delete_file("l")
        self.assertTrue(os.path.exists(os.path.join(self.outside, "t")))
        self.assertFalse(os.path.lexists(self.p("l")))

    def test_delete_fifo_refused(self):
        os.mkfifo(self.p("pipe"))
        with self.assertRaises(AgentError):
            self.sb.delete_file("pipe")
        self.assertTrue(os.path.exists(self.p("pipe")))

    def test_delete_missing_message(self):
        with self.assertRaisesRegex(AgentError, "Could not find x/y.txt"):
            self.sb.delete_file("x/y.txt")

    # -- listing
    def test_listing(self):
        for rel in ("b.txt", "a/c.txt", ".hidden", ".git/config", "a/.secret"):
            os.makedirs(os.path.dirname(self.p(rel)) or self.root, exist_ok=True)
            open(self.p(rel), "w").close()
        self.assertEqual(self.sb.listing(), ["b.txt", "a/c.txt"])
        self.assertEqual(len(self.sb.listing(limit=1)), 1)
        self.assertEqual(self.sb.files_named("c.txt"), ["a/c.txt"])


class GuardTests(unittest.TestCase):
    def test_extract_json(self):
        self.assertEqual(guards.extract_json('x {"a": "}{"} y'), {"a": "}{"})
        self.assertEqual(guards.extract_json('```json\n{"a":1}\n```'), {"a": 1})
        self.assertIsNone(guards.extract_json("no json"))
        self.assertIsNone(guards.extract_json(""))
        self.assertIsNone(guards.extract_json('{"unterminated": '))
        self.assertEqual(guards.extract_json('{"a":"line1\nline2"}'), {"a": "line1\nline2"})
        self.assertEqual(guards.extract_json('{"a":{"b":[1,{"c":2}]}} {"z":1}'), {"a": {"b": [1, {"c": 2}]}})
        self.assertEqual(guards.extract_json('[1,2] {"k": "v"}'), {"k": "v"})
        self.assertEqual(guards.extract_json('{bad} {"good": true}'), {"good": True})

    def test_parse_tool_call(self):
        self.assertEqual(guards.parse_tool_call('{"tool":"read_file","args":{"path":"a"}}'), ("read_file", {"path": "a"}))
        self.assertEqual(guards.parse_tool_call('{"name":"finish","arguments":"{\\"status\\":\\"error\\"}"}'),
                         ("finish", {"status": "error"}))
        self.assertEqual(guards.parse_tool_call('{"function":{"name":"list_files","arguments":{}}}'), ("list_files", {}))
        self.assertEqual(guards.parse_tool_call('{"action":"delete_file","path":"x"}'), ("delete_file", {"path": "x"}))
        self.assertEqual(guards.parse_tool_call('{"tool":"write_file","path":"a","content":"b"}'),
                         ("write_file", {"path": "a", "content": "b"}))
        for bad in ["no json here", '{"args":{}}', '{"tool":"x","args":"not json"}', '{"tool":"x","args":[1]}',
                    '{"tool": 5}']:
            with self.assertRaises(ValueError, msg=bad):
                guards.parse_tool_call(bad)

    def test_name_checks(self):
        self.assertTrue(guards.exact_name_mentioned("todo-old.md", "Delete todo-old.md."))
        self.assertFalse(guards.exact_name_mentioned("todo.md", "Delete todo-old.md."))
        self.assertFalse(guards.exact_name_mentioned("old.md", "Delete todo-old.md."))
        self.assertFalse(guards.exact_name_mentioned("report.txt", "Delete report.txt.bak"))
        self.assertTrue(guards.exact_name_mentioned("records/duplicate.csv", "get rid of duplicate.csv in records"))
        self.assertTrue(guards.exact_name_mentioned("Notes.TXT", "delete notes.txt"))
        self.assertTrue(guards.exact_name_mentioned("a.txt", "Delete 'a.txt'"))
        self.assertTrue(guards.exact_name_mentioned(unicodedata.normalize("NFD", "café.txt"), "Delete café.txt"))
        self.assertTrue(guards.name_mentioned("abc/foo.txt", "Create foo.txt inside abc"))
        self.assertTrue(guards.name_mentioned(".gitignore", "create .gitignore"))
        self.assertFalse(guards.name_mentioned("other.txt", "Create notes.txt"))

    def test_stem_and_dir_score(self):
        self.assertTrue(guards.stem_mentioned("README.md", "Delete the README"))
        self.assertFalse(guards.stem_mentioned("todo.md", "Delete todo-old.md"))
        self.assertEqual(guards.dir_score("records/duplicate.csv", "duplicate.csv in the records directory"), 1)
        self.assertEqual(guards.dir_score("duplicate.csv", "duplicate.csv in the records directory"), 0)
        self.assertEqual(guards.dir_score("archive/2023/x.log", "Delete archive/2024/x.log"), 1)

    def test_conflicting_prompt_path(self):
        p = "Delete archive/2024/january.log please"
        self.assertEqual(guards.conflicting_prompt_path("january.log", p), "archive/2024/january.log")
        self.assertIsNone(guards.conflicting_prompt_path("archive/2024/january.log", p))
        self.assertIsNone(guards.conflicting_prompt_path("./Archive/2024/January.log", p))
        self.assertIsNone(guards.conflicting_prompt_path("x.txt", p))
        self.assertIsNone(guards.conflicting_prompt_path("readme.txt", "Create readme.txt: see docs/readme.txt",
                                                         content="see docs/readme.txt"))

    def test_prompt_paths(self):
        self.assertEqual(guards.prompt_paths("Use logs/a.txt, logs/b.txt, and c.md to create r/out.txt."),
                         ["logs/a.txt", "logs/b.txt", "c.md", "r/out.txt"])

    def test_fix_content(self):
        p = 'Write "Meeting moved to Friday at 3 PM." to a/b.txt.'
        self.assertEqual(guards.fix_content("Meeting moved to Friday at 3 PM", p), "Meeting moved to Friday at 3 PM.")
        self.assertEqual(guards.fix_content("red\ngreen\nblue", "lines: red, green, blue"), "red\ngreen\nblue")
        self.assertEqual(guards.fix_content("foo", "The file should contain: foo"), "foo")
        self.assertEqual(guards.fix_content("hello\n", 'containing "hello" plus a newline'), "hello\n")
        self.assertEqual(guards.fix_content("C:\new\folder", r"Create p.txt containing C:\new\folder"), r"C:\new\folder")
        self.assertEqual(guards.fix_content("It's done", "Write \u201cIt\u2019s done.\u201d to s.txt"), "It\u2019s done.")
        self.assertEqual(guards.fix_content("", 'Create "x"'), "")
        self.assertEqual(guards.fix_content("   ", "Create x"), "   ")
        self.assertEqual(guards.fix_content("totally different", 'Write "abc" to x'), "totally different")
        self.assertEqual(guards.fix_content("line one\nline two", 'Create x with "line one line two"'),
                         "line one line two")

    def test_fix_content_whitespace_inside_quotes(self):
        p = 'Create indent.txt containing "    four spaces"'
        self.assertEqual(guards.fix_content("    four spaces", p), "    four spaces")
        self.assertEqual(guards.fix_content("four spaces", p), "    four spaces")
        self.assertEqual(guards.fix_content("four spaces  ", 'Write "four spaces  " to x'), "four spaces  ")

    def test_content_between(self):
        body = ("abc123 " * 200).strip()
        p = "Create long.txt containing " + body
        self.assertEqual(guards.content_between(p, "abc123 abc123", "abc123 abc123"), body)
        self.assertEqual(guards.content_between("say: Hello big world. Thanks", "Hello", "world."), "Hello big world.")
        self.assertEqual(guards.content_between("x: one", "one", "one"), "one")
        for start, end in (("missing", "world"), ("Hello", "missing"), ("", "x"), ("x", "")):
            self.assertIsNone(guards.content_between("say: Hello world", start, end))
        self.assertIsNone(guards.content_between("b then a", "then a", "b"))

    def test_salvage_truncated_write(self):
        body = ("abc123 " * 50).strip()
        prompt = "Create long.txt containing " + body
        cut = '{"tool": "write_file", "args": {"path": "long.txt", "content": "abc123 abc123 abc123 abc123 abc1'
        self.assertEqual(guards.salvage_truncated_write(cut, prompt), ("write_file", {"path": "long.txt", "content": body}))
        cut2 = cut.replace('"content"', '"content_start"')
        self.assertEqual(guards.salvage_truncated_write(cut2, prompt)[1]["content"], body)
        self.assertIsNone(guards.salvage_truncated_write(cut.replace("abc", "xyz"), prompt))
        self.assertIsNone(guards.salvage_truncated_write('{"tool": "delete_file", "args": {"path": "a"', prompt))
        self.assertIsNone(guards.salvage_truncated_write('{"tool": "write_file", "args": {"content": "abc123 abc123', prompt))
        quoted = 'Write "Line one, then more text here" to q.txt'
        self.assertEqual(guards.salvage_truncated_write(
            '{"tool": "write_file", "args": {"path": "q.txt", "content": "Line one, then mo', quoted)[1]["content"],
            "Line one, then more text here")

    def test_word_limit(self):
        cases = {
            "a summary with less than 100 words": 99, "no more than 50 words": 50, "under fifty words": 49,
            "a 75-word summary": 75, "80 words or fewer": 80, "shorter than 30 words": 29, "below 25 words": 24,
            "word limit of 40": 40, "max 60 words": 60, "60 words max": 60, "no longer than 20 words": 20,
            "within 45 words": 45, "\u2264 30 words": 30, "<100 words": 99, "at most 1,000 words": 1000,
            "a maximum of 35 words": 35, "not exceeding 70 words": 70, "in 3 sentences, under 90 words": 89,
            "under 100 words but at most 80 words": 80, "a short summary in 3 sentences": None,
            "at least 50 words": None, "a summary": None, "50 characters": None,
        }
        for text, want in cases.items():
            self.assertEqual(guards.parse_word_limit(text), want, text)

    def test_char_limit(self):
        cases = {"under 280 characters": 279, "at most 140 chars": 140, "a 200-character summary": 200,
                 "no more than 500 characters": 500, "under 100 words": None, "at least 50 characters": None}
        for text, want in cases.items():
            self.assertEqual(guards.parse_char_limit(text), want, text)

    def test_trim(self):
        text = "One two three. Four five six. Seven eight nine ten."
        self.assertEqual(guards.trim_to_words(text, 6), "One two three. Four five six.")
        self.assertEqual(guards.trim_to_words(text, 100), text)
        self.assertLessEqual(len(guards.trim_to_words("a " * 50, 10).split()), 10)
        self.assertLessEqual(len(guards.trim_to_words("one-long-sentence " * 30, 7).split()), 7)
        self.assertEqual(guards.trim_to_chars("Short.", 50), "Short.")
        out = guards.trim_to_chars("First sentence here. Second sentence is longer than that.", 30)
        self.assertEqual(out, "First sentence here.")
        self.assertLessEqual(len(guards.trim_to_chars("word " * 100, 33)), 33)

    def test_clean_generated(self):
        self.assertEqual(guards.clean_generated("```\nbody\n```"), "body")
        self.assertEqual(guards.clean_generated("```markdown\n- a\n- b\n```"), "- a\n- b")
        self.assertEqual(guards.clean_generated("Here is a concise summary of the logs:\nText."), "Text.")
        self.assertEqual(guards.clean_generated("Summary: kept as is."), "Summary: kept as is.")


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.reg = tools.build_tools(Sandbox(self.root))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_schema_validation(self):
        reg = self.reg
        self.assertEqual(reg["write_file"].validate({"path": "a", "content": ""}), {"path": "a", "content": ""})
        # content is optional in the schema (content_start/content_end may replace it); the harness
        # rejects a write_file that has neither (see tests/test_edge_cases.py)
        self.assertEqual(reg["write_file"].validate({"path": "a"}), {"path": "a"})
        with self.assertRaises(ValueError):
            reg["delete_file"].validate({"path": "a", "recursive": True})
        with self.assertRaises(ValueError):
            reg["finish"].validate({"status": "maybe", "message": "x"})
        with self.assertRaises(ValueError):
            reg["read_files"].validate({"paths": []})
        with self.assertRaises(ValueError):
            reg["read_files"].validate({"paths": ["a", 3]})
        with self.assertRaises(ValueError):
            reg["write_file"].validate({"path": 5, "content": "x"})
        self.assertEqual(reg["read_files"].validate({"paths": "a.txt"}), {"paths": ["a.txt"]})
        self.assertEqual(reg["finish"].validate({"status": "success"}), {"status": "success"})

    def test_aliases_and_coercion(self):
        reg = self.reg
        self.assertEqual(reg["write_file"].validate({"file_path": "a", "text": "t"}), {"path": "a", "content": "t"})
        self.assertEqual(reg["write_file"].validate({"path": "a", "content": ["x", "y"]}), {"path": "a", "content": "x\ny"})
        self.assertEqual(reg["write_file"].validate({"path": "a", "content": 42}), {"path": "a", "content": "42"})
        self.assertEqual(reg["read_files"].validate({"files": ["a", "b"]}), {"paths": ["a", "b"]})
        self.assertEqual(reg["read_files"].validate({"path": "a"}), {"paths": ["a"]})
        self.assertEqual(reg["finish"].validate({"status": "OK", "answer": 7}), {"status": "success", "message": "7"})
        self.assertEqual(reg["finish"].validate({"status": "Failed", "reason": "x"}), {"status": "error", "message": "x"})
        for alias, canon in (("cat", "read_file"), ("rm", "delete_file"), ("create_file", "write_file"),
                             ("final_answer", "finish"), ("ls", "list_files")):
            self.assertEqual(tools.TOOL_ALIASES[alias], canon)

    def test_schemas_are_json(self):
        for t in self.reg.values():
            s = json.loads(json.dumps(t.schema()))
            self.assertEqual(s["parameters"]["additionalProperties"], False)

    def test_read_files_caps_and_dedupes(self):
        for i in range(3):
            with open(os.path.join(self.root, "f%d.txt" % i), "w") as f:
                f.write("head%d " % i + "z" * 60000)
        out = self.reg["read_files"].handler({"paths": ["f0.txt", "f1.txt", "f2.txt", "./f0.txt"]}).obs
        self.assertEqual(out.count("===== FILE: f0.txt ====="), 1)
        for i in range(3):
            self.assertIn("head%d" % i, out)
        self.assertLess(len(out), tools.MAX_TOTAL_CHARS + 1000)

    def test_list_files_empty(self):
        self.assertIn("empty", self.reg["list_files"].handler({}).obs)


class LLMHelperTests(unittest.TestCase):
    def test_strip_think(self):
        self.assertEqual(llm.strip_think("<think>x</think>ans"), "ans")
        self.assertEqual(llm.strip_think("<THINK>x</THINK> ans "), "ans")
        self.assertEqual(llm.strip_think("<think>never closed"), "")
        self.assertEqual(llm.strip_think("pre <think>a</think> mid <think>b</think> post"), "pre  mid  post")
        self.assertEqual(llm.strip_think(None), "")

    def test_url_building_and_env(self):
        saved = {k: os.environ.get(k) for k in ("OPENAI_BASE_URL", "MODEL", "QWEN_API_KEY", "OPENAI_API_KEY")}
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            os.environ.update(MODEL="m", QWEN_API_KEY="k")
            for base, want in (("https://h/v1", "https://h/v1/chat/completions"),
                               ("https://h/v1/", "https://h/v1/chat/completions"),
                               ("https://h/v1/chat/completions", "https://h/v1/chat/completions")):
                os.environ["OPENAI_BASE_URL"] = base
                self.assertEqual(llm.LLM().url, want)
            del os.environ["QWEN_API_KEY"]
            with self.assertRaisesRegex(AgentError, "QWEN_API_KEY"):
                llm.LLM()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_budget_never_above_default(self):
        import config
        self.assertLessEqual(config.BUDGET_SIMPLE + config.WATCHDOG_SLACK, 175.0)
        self.assertLessEqual(config.BUDGET_DEEP + config.WATCHDOG_SLACK, 295.0)



class LenientParsingTests(unittest.TestCase):
    def test_trailing_commas(self):
        self.assertEqual(guards.extract_json('{"tool": "x", "args": {"a": 1,},}'), {"tool": "x", "args": {"a": 1}})
        self.assertEqual(guards.extract_json('{"a": [1, 2,],}'), {"a": [1, 2]})

    def test_python_style_literals(self):
        self.assertEqual(guards.extract_json("{'tool': 'x', 'args': {'ok': True, 'v': None}}"),
                         {"tool": "x", "args": {"ok": True, "v": None}})
        self.assertEqual(guards.extract_json("{'tool': 'x', 'flag': true, 'none': null}"),
                         {"tool": "x", "flag": True, "none": None})

    def test_code_is_never_executed(self):
        marker = os.path.join(tempfile.gettempdir(), "agent_pwned_marker")
        for payload in ['{__import__("os").system("touch %s")}' % marker,
                        "{'a': open('%s', 'w')}" % marker,
                        "{'a': (lambda: 1)()}", "{'a': [x for x in range(3)]}"]:
            self.assertIsNone(guards.extract_json(payload), payload)
        self.assertFalse(os.path.exists(marker))

    def test_strict_json_still_preferred(self):
        self.assertEqual(guards.extract_json('{"content": "a,}"}'), {"content": "a,}"})
        self.assertEqual(guards.extract_json('{"content": "True and None"}'), {"content": "True and None"})

if __name__ == "__main__":
    unittest.main(verbosity=1)
