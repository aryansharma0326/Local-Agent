"""Held-out black-box cases (written after both branches were final; neither was tuned on them).

  AGENT_UNDER_TEST=/path/run_agent.sh python3 heldout.py [--reps N] [name-substring ...]
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
saved_argv, sys.argv = sys.argv, ["x"]
import test_live as T  # noqa: E402
sys.argv = saved_argv
from common import Runner, expect  # noqa: E402

H = []


def conv(name, files, turns):
    H.append((name, files, turns))


say, file_equals, kept, both = T.say, T.file_equals, T.kept, T.both


def lacks(*words):
    def check(ws, r):
        for w in words:
            expect(w.lower() not in r.message.lower(), "leaked %r: %r" % (w, r.message))
    return check


def file_has(rel, *words):
    def check(ws, r):
        expect(ws.exists(rel), "%s not created (msg=%r)" % (rel, r.message))
        txt = ws.read(rel)
        for w in words:
            expect(w.lower() in txt.lower(), "%s lacks %r: %r" % (rel, w, txt))
    return check


def gone(rel):
    return lambda ws, r: expect(not ws.exists(rel), "%s still exists" % rel)


def present(rel):
    return lambda ws, r: expect(ws.exists(rel), "%s missing" % rel)


def no_new_files(*allowed):
    def check(ws, r):
        extra = [p for p in ws.snapshot() if not p.endswith("/") and p not in allowed and not p.startswith("unrelated/")]
        expect(not extra, "unexpected files: %s" % extra)
    return check


SENT = {"unrelated/sentinel.bin": b"\x00\x01", "unrelated/empty/.keep": ""}


def w(**files):
    d = dict(SENT)
    d.update({k.replace("__", "/").replace("_DOT_", "."): v for k, v in files.items()})
    return d


# ===================================================== CP2: first-turn statements (fresh wording)
conv("st_sort_verb", w(), [
    (9101, "I usually sort my mail on Sunday evenings.", None),
    (9101, "When do I sort my mail?", say("sunday")),
])
conv("st_search_noun", w(), [
    (9102, "Search results for my thesis topic were mostly from 2019.", None),
    (9102, "What year were most of my search results from?", say("2019")),
])
conv("st_average_noun", w(), [
    (9103, "Average rainfall in my town is about 40 inches a year.", None),
    (9103, "How much rain does my town get in a year?", say("40")),
])
conv("st_copy_noun", w(), [
    (9104, "Copy editing for my essay is finished and it took 3 hours.", None),
    (9104, "How long did the copy editing take?", say(("3", "three"))),
])
conv("st_we_need", w(), [
    (9105, "We need to leave for the airport by 6:30 tomorrow morning.", None),
    (9105, "By what time do we need to leave?", say("6:30")),
])
conv("st_count_noun", w(), [
    (9106, "The final count for the bake sale was 212 cookies.", None),
    (9106, "How many cookies did the bake sale have?", say("212")),
])
conv("st_moved", w(), [
    (9107, "My dentist appointment got moved to Thursday at 3 PM.", None),
    (9107, "When is my dentist appointment now?", say("thursday")),
])
conv("st_statement_with_file_we_keep", w(**{"budget_DOT_csv": "item,amount\nrent,900\n"}), [
    (9108, "We keep the household budget in budget.csv.", None),
    (9108, "Where do we keep the household budget?", say("budget.csv")),
])
conv("st_no_stray_files", w(), [
    (9109, "Update: my sister's birthday is on the 14th of June.", no_new_files()),
    (9109, "When is my sister's birthday?", both(say("14"), say("june"))),
])
conv("st_correction_numbers", w(), [
    (9110, "My flight number is UA 908.", None),
    (9110, "Sorry, I got that wrong: it is UA 980.", None),
    (9110, "What is my flight number?", both(say("980"), lacks("908"))),
])
conv("st_isolation_new_session", w(), [
    (9111, "My locker code is 4417.", None),
    (9112, "What is my locker code?", lacks("4417"), "either"),
    (9111, "What is my locker code?", say("4417")),
])
conv("st_fact_then_file", w(), [
    (9113, "My manager's name is Priya Raman.", None),
    (9113, "Create memo.txt containing a one-line note addressed to my manager by name.",
     file_has("memo.txt", "Priya")),
])
conv("st_two_facts_separate_turns_mixed_csv", w(**{"sales_DOT_csv": "region,amount\nEast,100\nWest,250\n"}), [
    (9114, "I'm preparing the quarterly review for Helen.", None),
    (9114, "What is the total of the amount column in sales.csv?", say("350")),
    (9114, "Who am I preparing the quarterly review for?", say("helen")),
])

# ===================================================== real requests worded like statements
conv("req_we_need_file", w(), [
    (9201, "We need a file plan.txt that says: launch on Friday.", file_has("plan.txt", "launch on friday")),
])
conv("req_my_file_should_have", w(), [
    (9202, "My notes file todo.txt should have one line: call the dentist.", file_has("todo.txt", "call the dentist")),
])
conv("req_file_should_contain", w(), [
    (9203, "The file greeting.txt should contain: Hello, team.", file_has("greeting.txt", "hello, team")),
])
conv("req_i_want_gone", w(**{"old_DOT_log": "x\n", "keep_DOT_log": "y\n"}), [
    (9204, "I want old.log gone.", both(gone("old.log"), present("keep.log"))),
])
conv("req_followup_imperative_after_fact", w(), [
    (9205, "My favourite city is Kyoto.", None),
    (9205, "Put it in city.txt.", file_has("city.txt", "kyoto")),
])

conv("req2_appreciate_summary", w(**{
        "a_DOT_txt": "The office moves to the 4th floor on June 2. Desks are packed on May 30.\n",
        "b_DOT_txt": "Parking passes for the new building are handed out on June 3 at reception.\n"}), [
    (9211, "I would appreciate a summary of a.txt and b.txt in sum.txt.", file_has("sum.txt", "june")),
])
conv("req2_love_a_copy", w(**{"data_DOT_csv": "k,v\na,1\n"}), [
    (9212, "I'd love a copy of data.csv as backup.csv.", file_equals("backup.csv", "k,v\na,1\n")),
])
conv("req2_we_should_delete", w(**{"draft_DOT_txt": "d\n", "final_DOT_txt": "f\n"}), [
    (9213, "We should delete draft.txt.", both(gone("draft.txt"), present("final.txt"))),
])
conv("req2_could_use_file", w(), [
    (9214, "I could use a file named shopping.txt listing eggs, rice and spinach.",
     file_has("shopping.txt", "eggs", "rice", "spinach")),
])

# ===================================================== CSV (fresh wording / data)
ORD = ("order_id,customer,status,amount\n1,Ann,shipped,40\n2,Bo,cancelled,15\n3,Cy,returned,22\n"
       "4,Di,shipped,60\n5,Ed,pending,N/A\n")
conv("csv_or_condition", w(**{"orders_DOT_csv": ORD}), [
    (9301, "In orders.csv, remove the rows whose status is cancelled or returned, and save the result as active.csv.",
     both(file_equals("active.csv", "order_id,customer,status,amount\n1,Ann,shipped,40\n4,Di,shipped,60\n"
                                    "5,Ed,pending,N/A\n"), kept("orders.csv", ORD))),
])
conv("csv_numeric_with_na_kept", w(**{"orders_DOT_csv": ORD}), [
    (9302, "From orders.csv, delete every row with an amount less than 30 and save it as big.csv.",
     file_equals("big.csv", "order_id,customer,status,amount\n1,Ann,shipped,40\n4,Di,shipped,60\n"
                            "5,Ed,pending,N/A\n")),
])
conv("csv_total_skips_na", w(**{"orders_DOT_csv": ORD}), [
    (9303, "What is the total of the amount column in orders.csv?", say("137")),
])
TASKS = "task,owner\nT-09,Kim\nT-090,Raj\nT-9,Sam\n"
conv("csv_exact_id", w(**{"tasks_DOT_csv": TASKS}), [
    (9304, "In tasks.csv, change the owner of task T-09 to Lee and save the result as tasks_new.csv.",
     file_equals("tasks_new.csv", "task,owner\nT-09,Lee\nT-090,Raj\nT-9,Sam\n")),
])
SALE = "id,region,amount\n1,East,100\n2,West,250\n3,East,50\n4,North,75\n"
conv("csv_chain_new_file", w(**{"sales_DOT_csv": SALE}), [
    (9305, "Read sales.csv and tell me the total of the amount column.", say("475")),
    (9305, "Now remove the rows where region is West and save it as no_west.csv.",
     file_equals("no_west.csv", "id,region,amount\n1,East,100\n3,East,50\n4,North,75\n")),
    (9305, "What is the total of the amount column in that new file?", say("225")),
    (9305, "And what was the total in the original?", say("475")),
])

# ===================================================== CP1 (fresh wording)
conv("cp1_create_with_code_words", w(), [
    (None, "Create todo.txt containing: refactor utils.py and write tests for main.py",
     both(file_has("todo.txt", "refactor utils.py and write tests for main.py"), no_new_files("todo.txt"))),
])
conv("cp1_delete_one_missing", w(**{"x_DOT_txt": "x\n"}), [
    (None, "Delete x.txt and y.txt.",
     lambda ws, r: expect((r.status == "error" and ws.exists("x.txt")) or (r.status == "success" and not ws.exists("x.txt")),
                          "half-applied: status=%s x.txt exists=%s" % (r.status, ws.exists("x.txt"))), "either"),
])
conv("cp1_read_altogether", w(**{"inventory_DOT_txt": "Supply closet\npens: 14\npencils: 9\nerasers: 30\n"}), [
    (None, "According to inventory.txt, how many pens and pencils are there altogether?", say("23")),
])
conv("cp1_read_single_value", w(**{"inventory_DOT_txt": "Supply closet\npens: 14\npencils: 9\nerasers: 30\n"}), [
    (None, "Read inventory.txt. How many erasers are there?", both(say("30"), lacks("53"))),
])
conv("cp1_summary_two_bullets", w(**{
        "notes__mon_DOT_txt": "Monday: the build server crashed at 09:10 and was restored by 09:45 after a disk cleanup.\n",
        "notes__tue_DOT_txt": "Tuesday: the team agreed to add disk alerts at 80 percent usage; Omar owns the change.\n"}), [
    (None, "Summarize notes/mon.txt and notes/tue.txt into digest.txt in exactly 2 bullet points.",
     lambda ws, r: expect(ws.exists("digest.txt") and sum(1 for l in ws.read("digest.txt").splitlines()
                                                           if l.strip().startswith(("-", "*", "•"))) == 2,
                          "digest.txt: %r" % (ws.read("digest.txt") if ws.exists("digest.txt") else None))),
])
conv("cp1_summary_word_limit_with_source_desc", w(**{
        "a_DOT_txt": "The library opens at 8 AM on weekdays and 10 AM on weekends.\n",
        "b_DOT_txt": "Late fees are 25 cents per day, capped at 5 dollars per item.\n"}), [
    (None, "The two notes a.txt and b.txt are short. Summarize both into lib.txt in 30 to 50 words.",
     lambda ws, r: expect(ws.exists("lib.txt") and 20 <= len(ws.read("lib.txt").split()) <= 50,
                          "lib.txt words=%s" % (len(ws.read("lib.txt").split()) if ws.exists("lib.txt") else None))),
])


def execute(name, files, turns):
    """Like test_live.execute_conversation, but a session of None runs without a session argument (CP1)."""
    orig = T.run_agent

    def run(ws, prompt, env, timeout=200, extra_args=None, **kw):
        return orig(ws, prompt, env, timeout=timeout, extra_args=[a for a in (extra_args or []) if a != "None"], **kw)
    T.run_agent = run
    try:
        return T.execute_conversation(name, files, turns)
    finally:
        T.run_agent = orig


def main():
    args = sys.argv[1:]
    reps = 1
    if args[:1] == ["--reps"]:
        reps, args = int(args[1]), args[2:]
    T.ENV = T.load_env()
    chosen = [c for c in H if not args or any(a in c[0] for a in args)]
    print("Held-out: %d conversations x %d reps, agent=%s" % (len(chosen), reps, os.environ.get("AGENT_UNDER_TEST")),
          flush=True)
    runner = Runner()
    with ThreadPoolExecutor(max_workers=int(os.environ.get("LIVE_WORKERS", "1"))) as pool:
        jobs = [(c[0], [pool.submit(execute, *c) for _ in range(reps)]) for c in chosen]
        for name, futs in jobs:
            def outcome(futs=futs):
                for i, f in enumerate(futs, 1):
                    for line in f.result():
                        print("      [rep %d] %s" % (i, line), flush=True)
            runner.case(name, outcome)
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
