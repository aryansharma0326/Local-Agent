"""Offline tests: run the real run_agent.sh against a local mock model
server that replays scripted tool calls. They exercise the harness loop,
tool schemas, guardrails, sandboxing, the stdout contract and failure
handling without needing the Qwen endpoint.

Usage: python3 tests/test_offline.py
"""

import json
import os
import shutil
import sys

sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import ROOT, Runner, Workspace, assert_contract, expect, run_agent  # noqa: E402
from mock_server import MockServer  # noqa: E402

SERVER = MockServer()
ENV = {"OPENAI_BASE_URL": SERVER.base_url, "MODEL": "Qwen/Qwen3-14B", "QWEN_API_KEY": "test-key"}


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False)


def ok(message="Done."):
    return call("finish", status="success", message=message)


def fail(message):
    return call("finish", status="error", message=message)


def go(files, prompt, replies, env=None):
    """Run one agent invocation; returns (workspace, result, snapshot_before)."""
    ws = Workspace(files)
    before = ws.snapshot()
    SERVER.reset(replies)
    r = run_agent(ws, prompt, env or ENV)
    assert_contract(r)
    return ws, r, before


def unchanged_except(ws, before, changed):
    after = ws.snapshot()
    for k, v in before.items():
        if k in changed:
            continue
        expect(after.get(k) == v, "unrelated entry %s was modified or removed" % k)
    for k in after:
        if k not in before and k not in changed:
            expect(False, "unexpected new entry %s" % k)


def observations():
    """Text of every observation the harness sent back to the model."""
    out = []
    for req in SERVER.requests:
        msgs = req["body"]["messages"]
        if msgs[-1]["role"] == "user":
            out.append(msgs[-1]["content"])
    return out


# ------------------------------------------------------------------ create
def t_create_simple():
    ws, r, b = go({"other.txt": "keep"}, "Create foo.txt containing hello",
                  [call("write_file", path="foo.txt", content="hello"), ok("Created foo.txt.")])
    expect(r.status == "success" and r.message == "Created foo.txt.", r.stdout)
    expect(ws.read("foo.txt") == "hello", repr(ws.read("foo.txt")))
    unchanged_except(ws, b, {"foo.txt"})


def t_create_nested():
    ws, r, b = go({}, "Create foo.txt inside the abc/def directory. The file should contain: foo",
                  [call("write_file", path="abc/def/foo.txt", content="foo"), ok("")])
    expect(r.status == "success" and ws.read("abc/def/foo.txt") == "foo", r.message)
    expect(r.message == "Created abc/def/foo.txt.", r.message)  # default message when model gives none


def t_create_empty():
    ws, r, _ = go({}, "Make an empty file named ready.flag in the current directory.",
                  [call("write_file", path="ready.flag", content=""), ok()])
    expect(r.status == "success" and ws.read("ready.flag") == "", r.message)


def t_create_multiline():
    ws, r, _ = go({}, "Create data/colors.txt with three lines: red, green, blue",
                  [call("write_file", path="data/colors.txt", content="red\ngreen\nblue"), ok()])
    expect(ws.read("data/colors.txt") == "red\ngreen\nblue", repr(ws.read("data/colors.txt")))


def t_create_quotes_restored():
    # Model dropped the final period inside quotes; the guard restores the literal span.
    prompt = 'Write "Meeting moved to Friday at 3 PM." to announcements/update.txt.'
    ws, r, _ = go({}, prompt, [call("write_file", path="announcements/update.txt",
                                    content="Meeting moved to Friday at 3 PM"), ok()])
    expect(ws.read("announcements/update.txt") == "Meeting moved to Friday at 3 PM.",
           repr(ws.read("announcements/update.txt")))


def t_create_inner_quotes():
    content = 'She said "hi" and left \\ {braces} \'single\''
    ws, r, _ = go({}, "Create q.txt containing: " + content,
                  [call("write_file", path="q.txt", content=content), ok('Created "q.txt".')])
    expect(ws.read("q.txt") == content, repr(ws.read("q.txt")))
    expect(json.loads(r.stdout)["message"] == 'Created "q.txt".', r.stdout)


def t_create_unicode():
    content = "Café ☕ — naïve 日本語 🚀"
    ws, r, _ = go({}, "Create ü/uni.txt containing " + content,
                  [call("write_file", path="ü/uni.txt", content=content), ok("Created ü/uni.txt.")])
    expect(ws.read("ü/uni.txt") == content, repr(ws.read("ü/uni.txt")))
    expect("ü/uni.txt" in r.message, r.message)


def t_create_overwrites_only_target():
    ws, r, b = go({"a.txt": "old", "b.txt": "B"}, "Create a.txt containing new",
                  [call("write_file", path="a.txt", content="new"), ok()])
    expect(ws.read("a.txt") == "new", ws.read("a.txt"))
    unchanged_except(ws, b, {"a.txt"})


def t_think_and_fences_tolerated():
    reply = "<think>let me see</think>\n```json\n" + call("write_file", path="f.txt", content="x") + "\n```"
    ws, r, _ = go({}, "Create f.txt containing x", [reply, "Sure. " + ok()])
    expect(r.status == "success" and ws.read("f.txt") == "x", r.message)


def t_openai_style_call_accepted():
    reply = json.dumps({"name": "write_file", "arguments": json.dumps({"path": "f.txt", "content": "x"})})
    ws, r, _ = go({}, "Create f.txt containing x", [reply, ok()])
    expect(r.status == "success" and ws.read("f.txt") == "x", r.stdout)


def t_second_mutation_blocked():
    ws, r, b = go({"keep.txt": "k"}, "Create a.txt containing 1",
                  [call("write_file", path="a.txt", content="1"),
                   call("delete_file", path="keep.txt"), ok()])
    expect(r.status == "success" and ws.read("keep.txt") == "k", r.stdout)
    expect(any("only one change" in o for o in observations()), "no rejection observation")
    unchanged_except(ws, b, {"a.txt"})


def t_loop_ends_after_mutation_without_finish():
    # Model writes, then keeps producing garbage: the harness still reports the completed write.
    ws, r, _ = go({}, "Create a.txt containing 1",
                  [call("write_file", path="a.txt", content="1")] + ["???"] * 3)
    expect(ws.read("a.txt") == "1", "file missing")
    assert_contract(r)


# ------------------------------------------------------------------ delete
def t_delete_simple():
    ws, r, b = go({"todo-old.md": "x", "todo.md": "y"}, "I no longer need todo-old.md. Delete it.",
                  [call("delete_file", path="todo-old.md"), ok("Deleted todo-old.md.")])
    expect(r.status == "success" and not ws.exists("todo-old.md"), r.message)
    unchanged_except(ws, b, {"todo-old.md"})


def t_delete_similar_name_blocked():
    # Model tries the wrong (similar) file first: guard rejects, model corrects itself.
    ws, r, b = go({"todo-old.md": "x", "todo.md": "y"}, "I no longer need todo-old.md. Delete it.",
                  [call("delete_file", path="todo.md"), call("delete_file", path="todo-old.md"), ok()])
    expect(r.status == "success" and ws.exists("todo.md") and not ws.exists("todo-old.md"), r.stdout)
    unchanged_except(ws, b, {"todo-old.md"})


def t_delete_nested_siblings_kept():
    files = {"archive/2024/january.log": "j", "archive/2024/february.log": "f", "archive/2023/january.log": "o"}
    ws, r, b = go(files, "Delete archive/2024/january.log, but leave the other log files unchanged.",
                  [call("delete_file", path="archive/2024/january.log"), ok()])
    expect(r.status == "success" and not ws.exists("archive/2024/january.log"), r.message)
    expect(os.path.isdir(ws.path("archive/2024")), "parent directory removed")
    unchanged_except(ws, b, {"archive/2024/january.log"})


def t_delete_missing():
    ws, r, b = go({"a.txt": "a"}, "Delete notes/old-draft.txt.",
                  [call("delete_file", path="notes/old-draft.txt"),
                   fail("Could not find notes/old-draft.txt.")])
    expect(r.status == "error" and r.code != 0, r.stdout)
    expect("notes/old-draft.txt" in r.message, r.message)
    expect(any("Could not find notes/old-draft.txt" in o for o in observations()), "tool error not observed")
    unchanged_except(ws, b, set())


def t_delete_missing_model_claims_success():
    # Delete failed, model wrongly says success twice: harness refuses to report success.
    ws, r, b = go({"a.txt": "a"}, "Delete notes/old-draft.txt.",
                  [call("delete_file", path="notes/old-draft.txt"), ok("Deleted."), ok("Deleted.")])
    expect(r.status == "error" and "notes/old-draft.txt" in r.message, r.stdout)


def t_delete_directory_refused():
    ws, r, b = go({"records/a.csv": "a"}, "Delete records.",
                  [call("delete_file", path="records"), fail("records is a directory.")])
    expect(r.status == "error" and ws.exists("records/a.csv"), r.stdout)
    unchanged_except(ws, b, set())


def t_delete_glob_refused():
    ws, r, b = go({"x.log": "1", "y.log": "2"}, "Delete *.log",
                  [call("delete_file", path="*.log"), fail("Wildcards are not supported.")])
    expect(r.status == "error", r.stdout)
    unchanged_except(ws, b, set())


def t_delete_unsupported_broad():
    ws, r, b = go({"x.log": "1"}, "Delete all the log files",
                  [call("delete_file", path="x.log"), fail("Deleting multiple files is not supported.")])
    expect(r.status == "error" and r.code != 0 and ws.exists("x.log"), r.stdout)
    unchanged_except(ws, b, set())


def t_delete_symlink_removes_link_only():
    outside = Workspace({"precious.txt": "keep me"})
    ws = Workspace({"a.txt": "a"})
    os.symlink(outside.path("precious.txt"), ws.path("link.txt"))
    SERVER.reset([call("delete_file", path="link.txt"), ok()])
    r = run_agent(ws, "Delete link.txt", ENV)
    assert_contract(r)
    expect(outside.read("precious.txt") == "keep me", "symlink target was modified")
    expect(not ws.exists("link.txt"), "link not removed")


# -------------------------------------------------------------- security
def t_traversal_create():
    ws, r, b = go({}, "Create ../escape.txt containing x",
                  [call("write_file", path="../escape.txt", content="x"), fail("Path escapes workspace.")])
    expect(r.status == "error", r.stdout)
    expect(not os.path.exists(os.path.join(os.path.dirname(ws.dir), "escape.txt")), "escaped!")
    unchanged_except(ws, b, set())


def t_traversal_delete():
    outside = Workspace({"victim.txt": "v"})
    rel = os.path.relpath(outside.path("victim.txt"), Workspace().dir)
    ws, r, b = go({"a.txt": "a"}, "Delete %s" % rel, [call("delete_file", path=rel), ok(), ok()])
    expect(r.status == "error" and outside.exists("victim.txt"), r.stdout)


def t_absolute_path():
    outside = Workspace({"victim.txt": "v"})
    ws, r, _ = go({}, "Delete %s" % outside.path("victim.txt"),
                  [call("delete_file", path=outside.path("victim.txt")), fail("Absolute paths are not allowed.")])
    expect(r.status == "error" and outside.exists("victim.txt"), r.stdout)


def t_symlink_dir_escape():
    outside = Workspace({"secret.txt": "s"})
    ws = Workspace({})
    os.symlink(outside.dir, ws.path("linkdir"))
    for p, tool, args in (("linkdir/new.txt", "write_file", {"content": "x"}),
                          ("linkdir/secret.txt", "delete_file", {}),
                          ("linkdir/secret.txt", "read_file", {})):
        SERVER.reset([call(tool, path=p, **args), ok("done"), ok("done")])
        r = run_agent(ws, "Use %s on %s" % (tool, p), ENV)
        assert_contract(r)
        expect(r.status == "error", "%s via symlinked dir was allowed: %s" % (tool, r.stdout))
    expect(outside.read("secret.txt") == "s" and not outside.exists("new.txt"), "outside modified")


def t_create_through_symlink_file_refused():
    outside = Workspace({"t.txt": "orig"})
    ws = Workspace({})
    os.symlink(outside.path("t.txt"), ws.path("t.txt"))
    SERVER.reset([call("write_file", path="t.txt", content="pwned"), fail("refused")])
    r = run_agent(ws, "Create t.txt containing pwned", ENV)
    assert_contract(r)
    expect(outside.read("t.txt") == "orig", "wrote through symlink")


def t_hallucinated_delete_rejected():
    ws, r, b = go({"important.txt": "x"}, "Delete the draft file.",
                  [call("delete_file", path="important.txt"), call("delete_file", path="important.txt"),
                   fail("No draft file was named.")])
    expect(r.status == "error" and ws.exists("important.txt"), r.stdout)
    unchanged_except(ws, b, set())


def t_hallucinated_write_rejected():
    ws, r, b = go({}, "Create notes.txt containing hi",
                  [call("write_file", path="other.txt", content="hi"),
                   call("write_file", path="notes.txt", content="hi"), ok()])
    expect(r.status == "success" and ws.read("notes.txt") == "hi" and not ws.exists("other.txt"), r.stdout)


# ------------------------------------------------------------ read/answer
def t_read_answer():
    files = {"inventory.txt": "blue notebooks: 42\nred pens: 7\n"}
    ws, r, b = go(files, "Read inventory.txt. How many blue notebooks are in stock?",
                  [call("read_file", path="inventory.txt"),
                   "<think>count</think>" + ok("There are 42 blue notebooks in stock.")])
    expect(r.status == "success" and "42" in r.message, r.message)
    unchanged_except(ws, b, set())
    obs = observations()[1]
    expect("===== FILE: inventory.txt =====" in obs and "blue notebooks: 42" in obs, "file not observed")
    expect(SERVER.requests[0]["auth"] == "Bearer test-key", "auth header missing")
    expect(SERVER.requests[1]["body"]["chat_template_kwargs"]["enable_thinking"] is True,
           "answer turn should use thinking")


def t_read_via_list_files():
    files = {"docs/deep/schedule.txt": "Final: May 3"}
    ws, r, b = go(files, "According to schedule.txt, when is the final?",
                  [call("list_files"), call("read_file", path="docs/deep/schedule.txt"), ok("May 3.")])
    expect(r.status == "success" and "May 3" in r.message, r.stdout)
    expect("docs/deep/schedule.txt" in observations()[1], "listing not observed")


def t_read_missing():
    ws, r, _ = go({}, "According to schedule.txt, when is the final?",
                  [call("read_file", path="schedule.txt"), fail("Could not find schedule.txt.")])
    expect(r.status == "error" and "schedule.txt" in r.message, r.stdout)


def t_read_unicode_answer_json():
    files = {"notes.txt": "Responsable: José Ñúñez — «déploiement» le 3 mai"}
    ws, r, b = go(files, "According to notes.txt who is responsible?",
                  [call("read_file", path="notes.txt"), ok('José Ñúñez is responsible; he said "ok"\nand left.')])
    expect("José Ñúñez" in r.message and '"ok"' in r.message, r.message)
    unchanged_except(ws, b, set())


def t_answer_without_reading_rejected():
    ws, r, _ = go({"f.txt": "x"}, "According to f.txt what is x?",
                  [ok("x is 5"), call("read_file", path="f.txt"), ok("x is x")])
    expect(r.status == "success" and r.message == "x is x", r.stdout)
    expect(any("no operation was performed" in o for o in observations()), "no rejection")


# -------------------------------------------------------------- summarize
SUM_FILES = {"logs/start.txt": "09:00 outage began. Café API down.",
             "logs/update.txt": "10:00 cause: expired TLS certificate.",
             "logs/resolution.txt": "11:00 certificate renewed; service restored."}
SUM_SRC = ["logs/start.txt", "logs/update.txt", "logs/resolution.txt"]
SUM_OUT = "reports/incident_summary.txt"
SUM_PROMPT = ("Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create "
              "reports/incident_summary.txt containing a summary with less than 100 words of the "
              "incident timeline, its cause, and its resolution.")


def t_summary_basic():
    text = "Outage began 09:00; cause was an expired TLS certificate; renewed and restored by 11:00."
    ws, r, b = go(SUM_FILES, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC), call("write_file", path=SUM_OUT, content=text), ok()])
    expect(r.status == "success", r.message)
    expect(ws.read(SUM_OUT) == text, ws.read(SUM_OUT))
    unchanged_except(ws, b, {"reports/", SUM_OUT})
    obs = observations()[1]
    for p in SUM_SRC:
        expect("===== FILE: %s =====" % p in obs, "%s not observed" % p)


def t_summary_must_read_all_sources():
    ws, r, _ = go(SUM_FILES, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC[:2]),
                   call("write_file", path=SUM_OUT, content="partial"),
                   call("read_file", path="logs/resolution.txt"),
                   call("write_file", path=SUM_OUT, content="complete summary."), ok()])
    expect(r.status == "success" and ws.read(SUM_OUT) == "complete summary.", r.stdout)
    expect(any("logs/resolution.txt, which you have not read" in o for o in observations()), "no rejection")


def t_summary_word_limit_retry():
    long = " ".join(["word%d." % i for i in range(150)])
    ws, r, _ = go(SUM_FILES, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC), call("write_file", path=SUM_OUT, content=long),
                   call("write_file", path=SUM_OUT, content="Short fixed summary."), ok()])
    expect(ws.read(SUM_OUT) == "Short fixed summary.", ws.read(SUM_OUT))
    expect(any("allows at most 99" in o for o in observations()), "limit not reported to model")


def t_summary_word_limit_trim():
    long = " ".join(["word%d." % i for i in range(150)])
    ws, r, _ = go(SUM_FILES, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC)] + [call("write_file", path=SUM_OUT, content=long)] * 3 + [ok()])
    n = len(ws.read(SUM_OUT).split())
    expect(r.status == "success" and 0 < n < 100, "summary has %d words" % n)


def t_summary_cannot_overwrite_source():
    ws, r, b = go(SUM_FILES, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC), call("write_file", path="logs/start.txt", content="x"),
                   call("write_file", path=SUM_OUT, content="ok summary"), ok()])
    expect(ws.read("logs/start.txt") == SUM_FILES["logs/start.txt"], "source overwritten")
    unchanged_except(ws, b, {"reports/", SUM_OUT})


def t_summary_finish_without_writing_rejected():
    ws, r, _ = go(SUM_FILES, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC), ok("Summary: ..."),
                   call("write_file", path=SUM_OUT, content="the summary"), ok()])
    expect(r.status == "success" and ws.exists(SUM_OUT), r.stdout)
    expect(any("have not written it yet" in o for o in observations()), "no rejection")


def t_summary_missing_source():
    files = dict(SUM_FILES)
    del files["logs/update.txt"]
    ws, r, b = go(files, SUM_PROMPT,
                  [call("read_files", paths=SUM_SRC), fail("Could not find logs/update.txt.")])
    expect(r.status == "error" and "logs/update.txt" in r.message, r.stdout)
    expect(not ws.exists("reports"), "output created despite error")


# ---------------------------------------------------- robustness / failures
def t_malformed_then_repaired():
    ws, r, _ = go({}, "Create ok.txt containing yes",
                  ["Sure! I'll create the file for you.", call("write_file", path="ok.txt", content="yes"), ok()])
    expect(r.status == "success" and ws.read("ok.txt") == "yes", r.stdout)


def t_malformed_repeatedly():
    ws, r, b = go({"a": "a"}, "Create ok.txt containing yes", ["garbage", "{not json", "still no"])
    expect(r.status == "error" and r.code != 0, r.stdout)
    unchanged_except(ws, b, set())


def t_unknown_tool():
    ws, r, b = go({"a": "a"}, "Run rm -rf /", [call("shell", command="rm -rf /"),
                                                call("shell", command="rm -rf /"),
                                                call("shell", command="rm -rf /")])
    expect(r.status == "error", r.stdout)
    unchanged_except(ws, b, set())


def t_schema_violations():
    ws, r, _ = go({}, "Create a.txt containing 1",
                  [call("write_file", path=["a.txt"], content="1"),
                   call("write_file", path="a.txt", content="1", mode="x"),
                   call("write_file", path="a.txt", content="1"), ok()])
    expect(r.status == "success" and ws.read("a.txt") == "1", r.stdout)
    obs = " ".join(observations())
    expect("must be a string" in obs and "unknown argument" in obs, "schema errors not reported")


def t_step_limit():
    ws, r, _ = go({"a.txt": "a"}, "Create b.txt containing 1", [call("list_files")] * 20)
    expect(r.status == "error" and not ws.exists("b.txt"), r.stdout)
    expect(len(SERVER.requests) <= 12, "made %d calls" % len(SERVER.requests))


def t_http_500():
    ws, r, _ = go({}, "Create a.txt containing 1", [{"status": 500}] * 5)
    expect(r.status == "error" and "model" in r.message.lower(), r.stdout)


def t_non_json_http_body():
    ws, r, _ = go({}, "Create a.txt containing 1", [{"raw": "<html>oops"}] * 5)
    expect(r.status == "error", r.stdout)


def t_api_unreachable():
    env = dict(ENV, OPENAI_BASE_URL="http://127.0.0.1:9/v1")
    ws, r, _ = go({}, "Create a.txt containing 1", [], env=env)
    expect(r.status == "error" and not ws.exists("a.txt"), r.stdout)


def t_missing_prompt():
    ws = Workspace({})
    SERVER.reset([])
    for prompt in (None, "", "   "):
        r = run_agent(ws, prompt, ENV)
        assert_contract(r)
        expect(r.status == "error" and r.code != 0, r.stdout)


def t_missing_env():
    ws, r, _ = go({}, "Create a.txt containing 1", [], env=dict(ENV, QWEN_API_KEY=None))
    expect(r.status == "error" and "QWEN_API_KEY" in r.message, r.stdout)


def t_request_shape():
    go({}, "Create a.txt containing 1", [call("write_file", path="a.txt", content="1"), ok()])
    req = SERVER.requests[0]
    expect(req["path"] == "/v1/chat/completions", req["path"])
    expect(req["body"]["model"] == "Qwen/Qwen3-14B", req["body"])
    expect(req["body"]["temperature"] == 0.0, "temperature")
    ua = req["user_agent"] or ""
    expect(ua and not ua.lower().startswith("python-urllib"),
           "default urllib User-Agent is blocked by Cloudflare (403/1010): %r" % ua)
    system = req["body"]["messages"][0]["content"]
    for tool in ("list_files", "read_file", "read_files", "write_file", "delete_file", "finish"):
        expect('"name": "%s"' % tool in system, "tool %s missing from system prompt" % tool)


def t_no_stdout_noise_on_success():
    ws, r, _ = go({}, "Create a.txt containing 1", [call("write_file", path="a.txt", content="1"), ok()])
    expect(r.stdout.count("\n") == 1 and r.stdout.startswith("{"), repr(r.stdout))


# ------------------------------------------- unattended / sandboxed running
def _minimal_env():
    """What a locked-down grader sandbox might provide: no HOME, no locale,
    no user config, just PATH and the three required variables."""
    py_dir = os.path.dirname(shutil.which("python3"))
    return {"PATH": ":".join(dict.fromkeys([py_dir, "/usr/bin", "/bin"]))}


def t_unattended_stdin_open_does_not_hang():
    # stdin is an open pipe that is never written to or closed.
    ws = Workspace({})
    SERVER.reset([call("write_file", path="a.txt", content="1"), ok()])
    r = run_agent(ws, "Create a.txt containing 1", ENV, timeout=30, keep_stdin_open=True)
    assert_contract(r)
    expect(r.status == "success" and ws.read("a.txt") == "1", r.stdout)


def t_unattended_clarifying_question_rejected():
    # The model tries to ask the user instead of acting; nobody can answer, so it must act.
    ws, r, _ = go({"notes/a.txt": "x"}, "Delete notes/a.txt.",
                  [ok("Could you confirm which file you want deleted?"),
                   call("delete_file", path="notes/a.txt"), ok("Deleted notes/a.txt.")])
    expect(r.status == "success" and not ws.exists("notes/a.txt"), r.stdout)
    expect(any("nobody can answer" in o for o in observations()), "question not rejected")
    system = SERVER.requests[0]["body"]["messages"][0]["content"]
    expect("unattended" in system and "Never ask" in system, "system prompt lacks unattended rule")


def t_sandbox_minimal_environment():
    ws = Workspace({"logs/a.txt": "Café opened at 9.", "logs/b.txt": "Closed at 5."})
    SERVER.reset([call("read_files", paths=["logs/a.txt", "logs/b.txt"]),
                  call("write_file", path="out/s.txt", content="Café open 9 to 5."), ok()])
    r = run_agent(ws, "Summarize logs/a.txt and logs/b.txt into out/s.txt", ENV, base_env=_minimal_env())
    assert_contract(r)
    expect(r.status == "success" and ws.read("out/s.txt") == "Café open 9 to 5.", r.stdout + r.stderr[-500:])


def t_sandbox_readonly_submission_all_four_tasks():
    """Copy the submission to a read-only location and run the four fixed
    task types from a separate workspace: nothing may be written into the
    submission and every task must complete unattended."""
    sub = Workspace({})
    for item in ("run_agent.sh", "agent"):
        src = os.path.join(ROOT, item)
        dst = sub.path(item)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
    for dp, dns, fns in os.walk(sub.dir):
        for n in fns:
            os.chmod(os.path.join(dp, n), 0o555 if n.endswith(".sh") else 0o444)
    for dp, dns, fns in os.walk(sub.dir, topdown=False):
        os.chmod(dp, 0o555)
    before = sub.snapshot()
    runs = [
        ({}, "Create notes/hello.txt containing hi",
         [call("write_file", path="notes/hello.txt", content="hi"), ok()],
         lambda ws: ws.read("notes/hello.txt") == "hi"),
        ({"old.txt": "x", "keep.txt": "k"}, "Delete old.txt",
         [call("delete_file", path="old.txt"), ok()],
         lambda ws: not ws.exists("old.txt") and ws.exists("keep.txt")),
        ({"facts.txt": "The answer is 42."}, "According to facts.txt, what is the answer?",
         [call("read_file", path="facts.txt"), ok("The answer is 42.")], None),
        ({"a.txt": "A", "b.txt": "B"}, "Summarize a.txt and b.txt into sum.txt",
         [call("read_files", paths=["a.txt", "b.txt"]), call("write_file", path="sum.txt", content="A and B."), ok()],
         lambda ws: ws.read("sum.txt") == "A and B."),
    ]
    try:
        for files, prompt, replies, check in runs:
            ws = Workspace(files)
            SERVER.reset(replies)
            r = run_agent(ws, prompt, ENV, base_env=_minimal_env(),
                          run_agent_path=sub.path("run_agent.sh"), keep_stdin_open=True, timeout=30)
            assert_contract(r)
            expect(r.status == "success", "%r failed: %s %s" % (prompt, r.stdout, r.stderr[-500:]))
            expect(check is None or check(ws), "%r: wrong filesystem result" % prompt)
        expect(sub.snapshot() == before, "agent wrote into its own (read-only) submission directory")
    finally:
        for dp, dns, fns in os.walk(sub.dir):
            os.chmod(dp, 0o755)


def main():
    runner = Runner()
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            runner.case(name[2:], fn)
    SERVER.close()
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
