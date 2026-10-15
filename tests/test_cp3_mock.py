"""Offline end-to-end tests for the CP3 loop [NO KEY]: the real run_agent.sh
against a scripted mock model that replays ideal (or pathological) native tool
calls. Covers the mock twins of the live CP3 cases and fault injection
(FX-11, FX-13, FX-14, FX-16, FX-19..23, DB-11..13, PG-7/8).

Usage: python3 tests/test_cp3_mock.py
"""

import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import gen_logs  # noqa: E402
import gen_transactions  # noqa: E402
import prog_variants  # noqa: E402
from common import Runner, Workspace, assert_contract, expect, run_agent  # noqa: E402
from mock_server import MockServer  # noqa: E402

SERVER = MockServer()
ENV = {"OPENAI_BASE_URL": SERVER.base_url, "MODEL": "Qwen/Qwen3-14B", "QWEN_API_KEY": "test-key"}
REF = open(os.path.join(HERE, "fixtures", "reference", "tally_ref.py")).read()
_n = [0]


def tc(name, **args):
    _n[0] += 1
    return {"tool_calls": [{"id": "call_%d" % _n[0], "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}


def multi(*calls):
    out = []
    for name, args in calls:
        _n[0] += 1
        out.append({"id": "call_%d" % _n[0], "type": "function", "function": {"name": name, "arguments": json.dumps(args)}})
    return {"tool_calls": out}


def debug_ws(seed=1, scen=(), hidden=6):
    logs, key, prompt = gen_logs.generate(seed, set(scen), "ORD-77341", ["gateway", "orders", "inventory", "payments"],
                                          hidden)
    files = dict(("logs/%s.log" % n, t) for n, t in logs.items())
    files["TASK.md"] = prompt
    return Workspace(files), key, prompt, logs


def prog_ws(variant="OFFICIAL", rows=3000, seed=1):
    lines = gen_transactions.generate(rows, seed)
    prompt = prog_variants.prompt(variant)
    return Workspace({"transactions.csv": "\n".join(lines) + "\n", "TASK.md": prompt}), prompt


def go(ws, prompt, replies, env=None, timeout=330):
    SERVER.reset(replies)
    r = run_agent(ws, prompt, dict(ENV, **(env or {})), timeout=timeout, extra_args=["9001"])
    assert_contract(r)
    return r


def request_checks(max_log_fraction=0.5, logs=None):
    for q in SERVER.requests:
        b = q["body"]
        expect(b["chat_template_kwargs"] == {"enable_thinking": False}, "thinking not disabled")
        expect(b["model"] == "Qwen/Qwen3-14B", "model name")
        expect(q["auth"] == "Bearer test-key", "auth header")
        expect(q["user_agent"] == "hw1-agent/1.0", "user agent")
        chars = sum(len(m.get("content") or "") + sum(len(t["function"]["arguments"]) for t in m.get("tool_calls") or [])
                    for m in b["messages"]) + len(json.dumps(b.get("tools") or []))
        est = chars // 3 + 16 * len(b["messages"])
        expect(est + b["max_tokens"] <= 40960, "request exceeds the 40,960-token budget (%d + %d)" % (est, b["max_tokens"]))
        if logs:
            text = json.dumps(b["messages"])
            for name, body in logs.items():
                lines = body.splitlines()
                shown = sum(1 for l in lines[::50] if l and json.dumps(l)[1:-1] in text)
                expect(shown < max_log_fraction * len(lines[::50]), "most of %s.log was sent to the model" % name)


def sole_files(ws, allowed):
    names = set()
    for dp, dns, fns in os.walk(ws.dir):
        for f in fns:
            names.add(os.path.relpath(os.path.join(dp, f), ws.dir))
    extra = names - set(allowed)
    expect(not extra, "unexpected files left in the workspace: %s" % sorted(extra))


# ------------------------------------------------------------ debugging twins
def t_db_twin_ideal_calls():
    ws, key, prompt, logs = debug_ws(1, ("retry", "decoy_ids"))
    before = ws.snapshot()
    r = go(ws, prompt, [multi(("grep", {"pattern": "ORD-77341"}), ("notes", {"text": "anchor found"})),
                        tc("report_set", values=dict(list(key.items())[:6])),
                        tc("report_set", values=dict(list(key.items())[6:])),
                        tc("finish", message="root cause %s" % key["root_cause_component"])])
    expect(r.status == "success" and r.code == 0, r.stdout)
    got = json.loads(ws.read("REPORT.json"))
    expect(got == key and list(got) == list(key), "report differs: %r" % got)
    expect(all(type(got[k]) == type(v) for k, v in key.items()), "types differ")
    request_checks(logs=logs)
    after = ws.snapshot()
    expect(all(after.get(k) == v for k, v in before.items()), "logs/TASK.md changed")
    sole_files(ws, list(before) + ["REPORT.json"])
    first = SERVER.requests[0]["body"]["messages"][1]["content"]
    expect("ANCHOR SEARCH" in first and key["gateway_request_id"] in first, "anchor pre-fetch missing")


def t_db10_generality_renamed_services_and_deliverable():
    logs, key, prompt = gen_logs.generate(4, {"json_lines", "mixed_ts"}, "ORD-55120", ["edge", "cart", "pricing", "dbproxy"], 6)
    prompt = prompt.replace("REPORT.json", "FINDINGS.json")
    files = dict(("logs/%s.log" % n, t) for n, t in logs.items())
    ws = Workspace(files)
    r = go(ws, prompt, [tc("report_set", values=key), tc("finish", message="done")])
    expect(r.status == "success", r.stdout)
    expect(ws.exists("FINDINGS.json") and not ws.exists("REPORT.json"), "wrong deliverable name")
    got = json.loads(ws.read("FINDINGS.json"))
    expect(got == key, "values differ: %r" % got)
    expect("task kind: debugging" in r.stderr, "not routed to debugging")
    obs = "\n".join(m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool")
    expect("UNLINKED" not in obs and "NOT FOUND" not in obs, "correct values were flagged: %s" % obs[:400])


def t_db_placeholder_written_first_and_types_coerced():
    ws, key, prompt, _ = debug_ws(2)
    r = go(ws, prompt, [tc("report_set", values={"gateway_status": "502", "worker_queue_depth": str(key["worker_queue_depth"]),
                                                 "worker": key["worker"]}),
                        tc("finish", message="partial"), tc("finish", message="partial"), tc("finish", message="partial")])
    expect(r.status == "success", r.stdout)
    got = json.loads(ws.read("REPORT.json"))
    expect(len(got) == 12 and got["gateway_status"] == 502 and isinstance(got["worker_queue_depth"], int), got)
    expect(got["gateway_request_id"] == "" and got["orders_timeout_ms"] == 0, "placeholders not type-correct")
    obs = [m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool"]
    expect(any("not finished yet" in o for o in obs), "premature finish was not pushed back")


def t_db_kill_test_budget():
    # DB-12: a tiny time budget and a hanging model: the report still exists, complete and typed.
    ws, key, prompt, _ = debug_ws(3)
    r = go(ws, prompt, [{"sleep": 40, "content": "x"}] * 3, env={"AGENT_TIME_BUDGET": "25"}, timeout=60)
    expect(r.elapsed < 40, "took %.1fs" % r.elapsed)
    got = json.loads(ws.read("REPORT.json"))
    expect(list(got) == list(key), "keys %r" % list(got))
    expect(isinstance(got["gateway_status"], int) and isinstance(got["hold_id"], str), "types")


def t_db_text_tool_calls_and_unknown_tool():
    # FX-11: Hermes <tool_call> text is executed; FX-12: unknown tool lists the valid ones.
    ws, key, prompt, _ = debug_ws(1)
    text_call = '<tool_call>\n{"name": "report_set", "arguments": {"values": {"worker": "%s"}}}\n</tool_call>' % key["worker"]
    r = go(ws, prompt, [tc("frobnicate", x=1), {"content": text_call},
                        tc("finish", message="x"), tc("finish", message="x"), tc("finish", message="x")])
    got = json.loads(ws.read("REPORT.json"))
    expect(got["worker"] == key["worker"], "text tool call not executed")
    obs = [m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool"]
    expect(any("unknown tool 'frobnicate'" in o and "report_set" in o for o in obs), "unknown tool not explained")


def t_db_repetition_is_bounded():
    # FX-13: the identical call forever -> nudges, then a forced wrap-up, never an endless loop.
    ws, key, prompt, _ = debug_ws(1)
    r = go(ws, prompt, [tc("grep", pattern="ORD-77341")] * 30)
    expect(len(SERVER.requests) <= 9, "made %d calls" % len(SERVER.requests))
    obs = [m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool"]
    expect(any("exact call" in o for o in obs), "no repetition nudge")
    expect(json.loads(ws.read("REPORT.json")) is not None, "report missing")


def t_call_cap_is_enforced():
    # FX-14/FX-22: a model that never finishes stops by the call cap (<= 110, wrap-up before).
    ws, key, prompt, _ = debug_ws(1)
    replies = [tc("read_lines", path="logs/gateway.log", start=i + 1, count=1) for i in range(130)]
    r = go(ws, prompt, replies, timeout=330)
    n = len(SERVER.requests)
    expect(n <= 110, "made %d calls" % n)
    expect(r.code in (0, 1) and r.status in ("success", "error"), r.stdout)
    expect("calls_used=" in r.stderr, "calls_used not logged")


# ---------------------------------------------------------- programming twins
def t_pg_twin_reference_program():
    ws, prompt = prog_ws()
    before = ws.snapshot()
    r = go(ws, prompt, [tc("write_file", path="tally.py", content=REF), tc("finish", message="done")])
    expect(r.status == "success", r.stdout)
    expect(ws.read("tally.py") == REF, "deliverable differs")
    res = prog_variants.grade("OFFICIAL", ws.dir)
    bad = [x for x in res if not x[1]]
    expect(not bad, "graded failures: %s" % bad[:3])
    expect("HARNESS CHECK" in json.dumps(SERVER.requests[-1]["body"]["messages"]), "harness check not run")
    first = SERVER.requests[0]["body"]["messages"][1]["content"]
    expect("DATA PROFILE" in first and "repeated header lines" in first, "data profile missing")
    after = ws.snapshot()
    expect(after["transactions.csv"] == before["transactions.csv"] and after["TASK.md"] == before["TASK.md"],
           "inputs changed")
    sole_files(ws, ["transactions.csv", "TASK.md", "tally.py"])
    request_checks()


def t_pg_best_version_restored_and_finish_gate():
    ws, prompt = prog_ws()
    broken = "def main(:\n    pass\n"
    r = go(ws, prompt, [tc("write_file", path="tally.py", content=REF), tc("write_file", path="tally.py", content=broken),
                        tc("finish", message="a"), tc("finish", message="b"), tc("finish", message="c")])
    expect(r.status == "success", r.stdout)
    expect(ws.read("tally.py") == REF, "best (compiling) version was not restored")
    obs = [m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool"]
    expect(any("SyntaxError" in o for o in obs), "compile error not reported")


def t_pg_hygiene_and_sandbox():
    # FX-19/20/21: escapes refused; scratch writes allowed; input tampering restored; stray files removed.
    ws, prompt = prog_ws()
    before = ws.snapshot()
    r = go(ws, prompt, [
        tc("write_file", path="../escape.txt", content="x"),
        tc("write_file", path="helper_test.py", content="x"),
        tc("write_file", path="$SCRATCH/fixture.csv", content="date,description,category,amount\n"),
        tc("run_command", command="curl http://example.com"),
        tc("run_command", command="rm -rf ~"),
        tc("run_command", command="echo junk > stray.txt; echo x >> transactions.csv; mkdir -p __pycache__"),
        tc("write_file", path="tally.py", content=REF),
        tc("finish", message="done")])
    expect(r.status == "success", r.stdout)
    obs = "\n".join(m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool")
    for want in ("outside the workspace", "only the deliverable", "scratch", "network tools are not allowed",
                 "deleting outside the workspace is not allowed"):
        expect(want in obs or want.replace("outside the workspace", "escapes the workspace") in obs, "missing: %s" % want)
    expect(not os.path.exists(os.path.join(os.path.dirname(ws.dir), "escape.txt")), "escaped write")
    after = ws.snapshot()
    expect(after["transactions.csv"] == before["transactions.csv"], "input data not restored")
    sole_files(ws, ["transactions.csv", "TASK.md", "tally.py"])


def t_pg_truncated_write_not_executed():
    # FX-16: finish_reason=length with a (partial) tool call: not executed, resend requested.
    ws, prompt = prog_ws()
    cut = tc("write_file", path="tally.py", content="import sys\nprint(")
    cut["finish_reason"] = "length"
    r = go(ws, prompt, [cut, tc("write_file", path="tally.py", content=REF), tc("finish", message="ok")])
    obs = [m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool"]
    expect(any("NOT executed" in o for o in obs), "truncated call was executed")
    expect(ws.read("tally.py") == REF, "final file")


def t_pg_raw_file_block_fallback():
    # LLM-15: a program sent as 'FILE: path' + fenced block is written by the harness.
    ws, prompt = prog_ws()
    r = go(ws, prompt, [{"content": "FILE: tally.py\n```python\n%s```" % REF}, tc("finish", message="ok")])
    expect(ws.exists("tally.py") and "def run(argv)" in ws.read("tally.py"), "raw block not written")


def t_pg_no_deliverable_is_error():
    ws, prompt = prog_ws()
    r = go(ws, prompt, [tc("finish", message="nothing")] * 4)
    expect(r.status == "error" and r.code == 1 and "tally.py" in r.message, r.stdout)


def t_generality_fix_failing_script():
    # PG-10 twin: fix a script so its tests pass, without editing the tests.
    buggy = "def mean(xs):\n    return sum(xs) / (len(xs) + 1)\n"
    tests = ("import unittest\nfrom buggy_stats import mean\n\nclass T(unittest.TestCase):\n"
             "    def test_mean(self):\n        self.assertEqual(mean([1, 2, 3]), 2)\n\nif __name__ == '__main__':\n"
             "    unittest.main()\n")
    prompt = "`buggy_stats.py` fails its tests in `test_stats.py`. Fix the bug in buggy_stats.py without editing the tests."
    ws = Workspace({"buggy_stats.py": buggy, "test_stats.py": tests})
    r = go(ws, prompt, [tc("edit_file", path="test_stats.py", old="2)", new="1.5)"),
                        tc("edit_file", path="buggy_stats.py", old="(len(xs) + 1)", new="len(xs)"),
                        tc("finish", message="fixed")])
    expect(r.status == "success", r.stdout)
    expect(ws.read("test_stats.py") == tests, "tests were modified")
    expect("len(xs)\n" in ws.read("buggy_stats.py"), "fix not applied")
    obs = "\n".join(m["content"] for q in SERVER.requests for m in q["body"]["messages"] if m["role"] == "tool")
    expect("must not be modified" in obs, "test edit not refused")
    expect("unittest" in obs, "tests were not run by the harness check")


if __name__ == "__main__":
    runner = Runner()
    names = [n for n in sorted(globals()) if n.startswith("t_")]
    only = sys.argv[1:]
    for n in names:
        if only and not any(o in n for o in only):
            continue
        runner.case(n[2:], globals()[n])
    SERVER.close()
    sys.exit(runner.summary())
