"""Runtime-contract E2E tests [NO KEY]: CT-01..CT-12, FX-03..FX-05, GS-06 and
U7 (the API key never appears in stdout/stderr), using the real run_agent.sh.

Usage: python3 tests/test_contract.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from common import ROOT, Runner, Workspace, assert_contract, expect, run_agent  # noqa: E402
from mock_server import MockServer  # noqa: E402

SERVER = MockServer()
KEY = "sk-test-SECRET-1234567890"
ENV = {"OPENAI_BASE_URL": SERVER.base_url, "MODEL": "Qwen/Qwen3-14B", "QWEN_API_KEY": KEY}


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False)


def ok(msg="Done."):
    return call("finish", status="success", message=msg)


def no_key_leak(r):
    expect(KEY not in r.stdout and KEY not in r.stderr, "API key leaked into the output")


def go(ws, prompt, replies, env=None, **kw):
    SERVER.reset(replies)
    r = run_agent(ws, prompt, env or ENV, **kw)
    assert_contract(r)
    no_key_leak(r)
    return r


def t_ct01_no_args():
    ws = Workspace({})
    r = run_agent(ws, None, ENV)
    assert_contract(r)
    expect(r.status == "error" and r.code == 1, r.stdout)


def t_ct02_empty_and_whitespace_prompt():
    for p in ("", "   \n\t "):
        r = go(Workspace({}), p, [])
        expect(r.status == "error" and r.code == 1 and not SERVER.requests, r.stdout)


def t_ct03_missing_key_non_local():
    r = go(Workspace({}), "Create a.txt containing 1", [],
           env={"OPENAI_BASE_URL": "https://example.invalid/v1", "MODEL": "m", "QWEN_API_KEY": None,
                "OPENAI_API_KEY": None})
    expect(r.status == "error" and "QWEN_API_KEY" in r.message and r.elapsed < 3, r.stdout)


def t_ct04_openai_api_key_fallback():
    ws = Workspace({})
    r = go(ws, "Create a.txt containing 1", [call("write_file", path="a.txt", content="1"), ok()],
           env={"OPENAI_BASE_URL": SERVER.base_url, "MODEL": "Qwen/Qwen3-14B", "QWEN_API_KEY": None,
                "OPENAI_API_KEY": KEY})
    expect(r.status == "success" and SERVER.requests[0]["auth"] == "Bearer " + KEY, r.stdout)


def t_ct06_prompt_received_intact():
    weird = 'Create a.txt containing 1. "q" \'s\' $HOME `whoami` \\n back\\slash\nline2 \U0001f600 Zoë ' + "z" * 20000
    ws = Workspace({})
    r = go(ws, weird, [call("write_file", path="a.txt", content="1"), ok()])
    first = SERVER.requests[0]["body"]["messages"][1]["content"]
    expect(weird in first, "prompt was altered in transit")


def t_ct08_submission_path_with_spaces_and_symlink():
    tmp = tempfile.mkdtemp()
    sub = os.path.join(tmp, "sub with space é")
    shutil.copytree(ROOT, sub, ignore=shutil.ignore_patterns(".git", "tests", "dist", "__pycache__", ".agent_state"))
    link_dir = os.path.join(tmp, "bin")
    os.makedirs(link_dir)
    link = os.path.join(link_dir, "agent.sh")
    os.symlink(os.path.join(sub, "run_agent.sh"), link)
    try:
        ws = Workspace({})
        r = go(ws, "Create a.txt containing 1", [call("write_file", path="a.txt", content="1"), ok()],
               run_agent_path=link)
        expect(r.status == "success" and ws.read("a.txt") == "1", r.stdout + r.stderr[-500:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_ct10_workspace_shadowing_modules():
    files = {"csv.py": "raise SystemExit('shadowed csv')", "json.py": "raise SystemExit('shadowed json')",
             "tools.py": "raise SystemExit('shadowed tools')", "agent/main.py": "raise SystemExit('x')",
             "config.py": "raise SystemExit('shadowed config')", "data.csv": "a,b\n1,2\n"}
    ws = Workspace(files)
    r = go(ws, "Read data.csv and tell me the total of the b column.",
           [call("csv_aggregate", path="data.csv", column="b", operation="sum"), ok("The total is 2.")])
    expect(r.status == "success" and "2" in r.message, r.stdout)


def t_ct11_sanitized_env_utf8():
    tmp = tempfile.mkdtemp()
    base = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": tmp, "LANG": "C"}
    ws = Workspace({"team.txt": "Design lead: Zoë Müller\n"})
    SERVER.reset([call("read_file", path="team.txt"), ok("The design lead is Zoë Müller.")])
    r = run_agent(ws, "Who leads design according to team.txt?", ENV, base_env=base)
    assert_contract(r)
    expect(r.status == "success" and "Zoë Müller" in r.message, r.stdout)
    shutil.rmtree(tmp, ignore_errors=True)


def t_ct12_home_unset_with_session():
    base = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    ws = Workspace({"s.csv": "m,r\na,1\nb,2\n"})
    for prompt, reply in (("Read s.csv and tell me the total of the r column.",
                           [call("csv_aggregate", path="s.csv", column="r", operation="sum"), ok("3")]),
                          ("What is the average?", [call("csv_aggregate", path="s.csv", column="r", operation="average"),
                                                    ok("1.5")])):
        SERVER.reset(reply)
        r = run_agent(ws, prompt, dict(ENV, AGENT_SESSION_DIR=None), base_env=base, extra_args=["31337"])
        assert_contract(r)
        expect(r.status == "success", r.stdout + r.stderr[-400:])
    expect("Conversation so far" in SERVER.requests[0]["body"]["messages"][1]["content"], "history lost without HOME")


def t_gs06_invoked_with_sh():
    ws = Workspace({})
    SERVER.reset([call("write_file", path="a.txt", content="1"), ok()])
    env = dict(os.environ, **ENV)
    p = subprocess.run(["sh", os.path.join(ROOT, "run_agent.sh"), "Create a.txt containing 1"], cwd=ws.dir, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    obj = json.loads(p.stdout.decode())
    expect(obj["status"] == "success" and p.returncode == 0 and ws.read("a.txt") == "1", p.stdout.decode())


def t_fx03_connection_refused():
    r = go(Workspace({}), "Create a.txt containing 1", [],
           env=dict(ENV, OPENAI_BASE_URL="http://127.0.0.1:9/v1"), timeout=200)
    expect(r.status == "error" and r.code == 1 and r.elapsed < 170, r.stdout)


def t_fx04_hanging_endpoint_times_out():
    r = go(Workspace({}), "Create a.txt containing 1", [{"sleep": 30, "content": "x"}] * 4,
           env=dict(ENV, AGENT_CALL_TIMEOUT="2", AGENT_TIME_BUDGET="15"), timeout=60)
    expect(r.status == "error" and r.elapsed < 25, "%s in %.1fs" % (r.stdout, r.elapsed))


def t_fx05_html_403_fast():
    r = go(Workspace({}), "Create a.txt containing 1",
           [{"status": 403, "body": "<html><body>Cloudflare error 1010</body></html>"}] * 5)
    expect(r.status == "error" and "403" in r.message and r.elapsed < 5 and len(SERVER.requests) == 1, r.stdout)


def t_fx07_fx08_bad_bodies_retried():
    ws = Workspace({})
    r = go(ws, "Create a.txt containing 1",
           [{"raw": "not json"}, {"empty_choices": True}, call("write_file", path="a.txt", content="1"), ok()])
    expect(r.status == "success" and ws.read("a.txt") == "1", r.stdout)


def t_fx17_fx18_think_and_fences_cleaned():
    ws = Workspace({"f.txt": "The room is B12.\n"})
    r = go(ws, "According to f.txt, which room?",
           [call("read_file", path="f.txt"), "<think>hmm</think>```json\n" + ok("The room is B12.") + "\n```"])
    expect(r.status == "success" and r.message == "The room is B12.", r.stdout)


def t_u6_nothing_written_outside_workspace():
    tmp = tempfile.mkdtemp()
    ws_dir = os.path.join(tmp, "ws")
    os.makedirs(ws_dir)
    sentinel = os.path.join(tmp, "sentinel")
    os.makedirs(sentinel)
    open(os.path.join(sentinel, "keep.txt"), "w").close()
    ws = Workspace({})
    ws.dir = ws_dir
    r = go(ws, "Create ../sentinel/evil.txt containing x",
           [call("write_file", path="../sentinel/evil.txt", content="x"), call("finish", status="error", message="no")])
    expect(r.status == "error" and os.listdir(sentinel) == ["keep.txt"], r.stdout)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    runner = Runner()
    for n in sorted(k for k in globals() if k.startswith("t_")):
        if len(sys.argv) > 1 and not any(a in n for a in sys.argv[1:]):
            continue
        runner.case(n[2:], globals()[n])
    SERVER.close()
    sys.exit(runner.summary())
