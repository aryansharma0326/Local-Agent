"""End-to-end Checkpoint 2 tests: multi-turn sessions and CSV tasks.

Every turn is a separate `bash run_agent.sh "$PROMPT" "$SESSION_ID"` process
in the same workspace, exactly as the grader runs it. A scripted mock model
replays tool calls; the tests check what the harness sends the model (is the
earlier conversation there? is another session's history absent?), the
resulting files, and the stdout contract.

Usage: python3 tests/test_sessions.py [name-substring ...]
"""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Runner, Workspace, assert_contract, expect, run_agent  # noqa: E402
from mock_server import MockServer  # noqa: E402

SERVER = MockServer()
ENV = {"OPENAI_BASE_URL": SERVER.base_url, "MODEL": "Qwen/Qwen3-14B", "QWEN_API_KEY": "test-key"}

SALES = "month,revenue\nJanuary,120\nFebruary,180\n"
RECORDS = "ID,name,status\n101,Ann,open\n104,Bob,pending\n107,Cy,open\n"
ORDERS = "order_id,item,status,amount\n1,pen,shipped,3.50\n2,ink,cancelled,12\n3,pad,cancelled,7.25\n4,cap,open,1\n"
CANDIDATES = "id,name,interview_score,test_score\n1,Ana,41,37.5\n2,Raj,30,44\n"


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False)


def ok(message="Done."):
    return call("finish", status="success", message=message)


def fail(message):
    return call("finish", status="error", message=message)


def turn(ws, prompt, session_id, replies, env=None, extra=None):
    """One grader call. session_id=None omits the second argument."""
    SERVER.reset(replies)
    args = ([str(session_id)] if session_id is not None else []) + (extra or [])
    r = run_agent(ws, prompt, env or ENV, timeout=60, extra_args=args)
    assert_contract(r)
    return r


def first_message():
    """The first user message the harness sent the model in the latest turn."""
    return SERVER.requests[0]["body"]["messages"][1]["content"]


def saw(text):
    return any(text in req["body"]["messages"][-1]["content"] for req in SERVER.requests)


def raw(ws, rel):
    with open(ws.path(rel), "rb") as f:
        return f.read()


# ======================================================================
# The assignment's three-turn example
# ======================================================================
def t_assignment_three_turn_example():
    ws = Workspace({"sales.csv": SALES})
    r1 = turn(ws, "Read sales.csv and tell me the total of the revenue column.", 1001,
              [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"),
               ok("The total of the revenue column is 300.")])
    expect(r1.status == "success" and "300" in r1.message, r1.stdout)
    expect("Conversation so far" not in first_message(), "first turn should have no history")

    r2 = turn(ws, "What is the average?", 1001,
              [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"),
               ok("The average revenue is 150.")])
    expect(r2.status == "success" and "150" in r2.message, r2.stdout)
    sent = first_message()
    expect("Read sales.csv and tell me the total of the revenue column." in sent, "turn 1 prompt missing")
    expect("csv_aggregate" in sent and '"column": "revenue"' in sent and "300" in sent, "turn 1 action/result missing")

    r3 = turn(ws, "Save a copy of the table as reports/sales_copy.csv.", 1001,
              [call("copy_file", source_path="sales.csv", destination_path="reports/sales_copy.csv"), ok()])
    expect(r3.status == "success", r3.stdout)
    expect(raw(ws, "reports/sales_copy.csv") == SALES.encode(), "copy differs from the original")
    expect(raw(ws, "sales.csv") == SALES.encode(), "sales.csv was modified")
    expect("What is the average?" in first_message() and "150" in first_message(), "turn 2 missing from history")
    expect(sorted(os.listdir(ws.dir)) == ["reports", "sales.csv"], "stray files: %r" % os.listdir(ws.dir))


# ======================================================================
# Session isolation and persistence
# ======================================================================
def t_different_session_ids_are_separate():
    ws = Workspace({"sales.csv": SALES, "stock.csv": "item,qty\na,5\nb,7\n"})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 1001,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    turn(ws, "Read stock.csv and tell me the total of the qty column.", 2002,
         [call("csv_aggregate", path="stock.csv", column="qty", operation="sum"), ok("12")])
    expect("sales.csv and tell me" not in first_message(), "session 2002 saw session 1001's history")

    turn(ws, "What is the average?", 2002,
         [call("csv_aggregate", path="stock.csv", column="qty", operation="average"), ok("6")])
    sent = first_message()
    expect("stock.csv and tell me" in sent and "sales.csv and tell me" not in sent, "histories mixed")

    turn(ws, "What is the average?", 1001,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")])
    sent = first_message()
    expect("sales.csv and tell me" in sent and "stock.csv and tell me" not in sent, "histories mixed")


def t_same_id_in_another_workspace_is_separate():
    ws1, ws2 = Workspace({"sales.csv": SALES}), Workspace({"sales.csv": SALES})
    turn(ws1, "Read sales.csv and tell me the total of the revenue column.", 1001,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    turn(ws2, "What is the average?", 1001, [fail("No earlier context.")])
    expect("Conversation so far" not in first_message(), "history leaked across workspaces")


def t_no_session_id_is_stateless():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 1001,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", None,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    expect("Conversation so far" not in first_message(), "a call without a session id got history")
    turn(ws, "What is the average?", 1001,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")])
    expect(first_message().count("[Turn ") == 1, "the stateless call was recorded in session 1001")


def t_failed_turns_are_remembered_too():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "Read missing.csv and tell me the total of the revenue column.", 7,
             [call("csv_aggregate", path="missing.csv", column="revenue", operation="sum"),
              fail("Could not find missing.csv.")])
    expect(r.status == "error", r.stdout)
    turn(ws, "Sorry, I meant sales.csv.", 7,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    sent = first_message()
    expect("missing.csv" in sent and "Reply (error)" in sent, "the failed turn is not in the history")


def t_history_survives_api_failure_turn():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "Read sales.csv and tell me the total of the revenue column.", 8, [{"status": 500}] * 5)
    expect(r.status == "error", r.stdout)
    turn(ws, "Try again.", 8, [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    expect("Read sales.csv and tell me the total" in first_message(), "prompt of the failed turn was lost")


def t_long_conversation_keeps_working():
    ws = Workspace({"sales.csv": SALES})
    for i in range(8):
        r = turn(ws, "Question number %d about sales.csv revenue total?" % i, 55,
                 [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
        expect(r.status == "success", r.stdout)
    sent = first_message()
    expect(all("Question number %d" % i in sent for i in range(7)), "an earlier turn is missing")
    expect("[Turn 7]" in sent and "[Turn 8]" not in sent, "wrong number of turns in history")


def t_earlier_instruction_and_value_are_available():
    ws = Workspace({})
    turn(ws, "Remember that the project code name is Bluebird and outputs go in the out folder.", 31,
         [ok("Noted: code name Bluebird, outputs in out/.")])
    r = turn(ws, "Create a file named codename.txt there containing the code name.", 31,
             [call("write_file", path="out/codename.txt", content="Bluebird"), ok()])
    expect(r.status == "success" and ws.read("out/codename.txt") == "Bluebird", r.stdout)
    expect("Bluebird" in first_message(), "earlier instruction not passed to the model")


# ======================================================================
# Argument handling
# ======================================================================
def t_non_numeric_second_argument_is_not_a_session():
    ws = Workspace({})
    turn(ws, "Create a.txt containing", "hello", [call("write_file", path="a.txt", content="hello"), ok()])
    expect("Create a.txt containing hello" in first_message(), "arguments were not joined into the prompt")


def t_session_id_with_whitespace_and_leading_zeros():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", " 0042 ",
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    turn(ws, "What is the average?", "0042",
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")])
    expect("Conversation so far" in first_message(), "whitespace around the id broke the session")
    turn(ws, "What is the average?", "42", [fail("no context")])
    expect("Conversation so far" not in first_message(), "'42' and '0042' must be different sessions")


def t_missing_prompt_with_session_id():
    ws = Workspace({})
    r = turn(ws, "", 1001, [])
    expect(r.status == "error" and r.code != 0, r.stdout)


def t_checkpoint1_tasks_work_with_a_session_id():
    ws = Workspace({"old.txt": "o", "facts.txt": "The answer is 42.", "a.txt": "A", "b.txt": "B"})
    r = turn(ws, "Create notes/n.txt containing hi", 5, [call("write_file", path="notes/n.txt", content="hi"), ok()])
    expect(r.status == "success" and ws.read("notes/n.txt") == "hi", r.stdout)
    r = turn(ws, "Delete old.txt", 5, [call("delete_file", path="old.txt"), ok()])
    expect(r.status == "success" and not ws.exists("old.txt"), r.stdout)
    r = turn(ws, "According to facts.txt, what is the answer?", 5, [call("read_file", path="facts.txt"), ok("42")])
    expect(r.status == "success" and r.message == "42", r.stdout)
    r = turn(ws, "Summarize a.txt and b.txt into s.txt", 5,
             [call("read_files", paths=["a.txt", "b.txt"]), call("write_file", path="s.txt", content="A and B."), ok()])
    expect(r.status == "success" and ws.read("s.txt") == "A and B.", r.stdout)


# ======================================================================
# CSV operations from the assignment
# ======================================================================
def t_csv_total_inventory():
    ws = Workspace({"inventory.csv": "item,quantity\nbolt,40\nnut,25\nwasher,35\n"})
    r = turn(ws, "Read inventory.csv and tell me the total of the quantity column.", 1,
             [call("csv_aggregate", path="inventory.csv", column="quantity", operation="sum"), ok("The total is 100.")])
    expect(r.status == "success" and "100" in r.message, r.stdout)
    expect(raw(ws, "inventory.csv") == b"item,quantity\nbolt,40\nnut,25\nwasher,35\n", "source modified")


def t_csv_update_row_by_id_and_save_as():
    ws = Workspace({"records.csv": RECORDS})
    r = turn(ws, "In records.csv, change the status of the row with ID 104 to complete, and save the result "
                 "as updated_records.csv.", 1,
             [call("csv_update_cell", path="records.csv", id_column="ID", id_value="104", column="status",
                   new_value="complete", output_path="updated_records.csv"), ok()])
    expect(r.status == "success", r.stdout)
    expect(ws.read("updated_records.csv") == "ID,name,status\n101,Ann,open\n104,Bob,complete\n107,Cy,open\n",
           repr(ws.read("updated_records.csv")))
    expect(ws.read("records.csv") == RECORDS, "records.csv must stay unchanged")


def t_csv_update_forgot_output_path_is_corrected():
    ws = Workspace({"records.csv": RECORDS})
    r = turn(ws, "In records.csv, change the status of the row with ID 104 to complete, and save the result "
                 "as updated_records.csv.", 1,
             [call("csv_update_cell", path="records.csv", id_value=104, column="status", new_value="complete"),
              call("csv_update_cell", path="records.csv", id_value=104, column="status", new_value="complete",
                   output_path="updated_records.csv"), ok()])
    expect(r.status == "success" and ws.read("records.csv") == RECORDS, "original was overwritten")
    expect("104,Bob,complete" in ws.read("updated_records.csv"), r.stdout)
    expect(saw("asks to save the result as updated_records.csv"), "in-place overwrite was not refused")


def t_csv_update_in_place_when_requested():
    ws = Workspace({"records.csv": RECORDS, "other.csv": "a\n1\n"})
    r = turn(ws, "In records.csv, set the name of the row with ID 107 to Cyrus and save the file.", 1,
             [call("csv_update_cell", path="records.csv", id_column="ID", id_value=107, column="name",
                   new_value="Cyrus"), ok()])
    expect(r.status == "success", r.stdout)
    expect(ws.read("records.csv") == "ID,name,status\n101,Ann,open\n104,Bob,pending\n107,Cyrus,open\n",
           repr(ws.read("records.csv")))
    expect(ws.read("other.csv") == "a\n1\n", "unrelated file changed")


def t_csv_remove_rows_and_save_as():
    ws = Workspace({"orders.csv": ORDERS})
    r = turn(ws, "In orders.csv, remove every row whose status is cancelled, and save the result as "
                 "active_orders.csv.", 1,
             [call("csv_delete_rows", path="orders.csv", column="status", operator="equals", value="cancelled",
                   output_path="active_orders.csv"), ok()])
    expect(r.status == "success", r.stdout)
    expect(ws.read("active_orders.csv") == "order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1\n",
           repr(ws.read("active_orders.csv")))
    expect(ws.read("orders.csv") == ORDERS, "orders.csv must stay unchanged")


def t_csv_row_sum_candidates():
    ws = Workspace({"candidates.csv": CANDIDATES})
    r = turn(ws, "Read candidates.csv. For the row whose id is 1, add the values in the interview_score and "
                 "test_score columns. What is the result?", 1,
             [call("csv_row_sum", path="candidates.csv", id_column="id", id_value=1,
                   columns=["interview_score", "test_score"]), ok("The result is 78.5.")])
    expect(r.status == "success" and "78.5" in r.message, r.stdout)
    expect(ws.read("candidates.csv") == CANDIDATES, "source modified")


def t_csv_question_about_values():
    ws = Workspace({"candidates.csv": CANDIDATES})
    r = turn(ws, "According to candidates.csv, what is Raj's test score?", 1,
             [call("read_file", path="candidates.csv"), ok("Raj's test score is 44.")])
    expect(r.status == "success" and "44" in r.message and saw("2,Raj,30,44"), r.stdout)


def t_computed_value_is_added_when_the_model_omits_it():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "Read sales.csv and tell me the total of the revenue column.", 1,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"),
              ok("I have calculated the total.")])
    expect(r.status == "success" and "300" in r.message, "the computed total is not in the message: " + r.stdout)


def t_computed_value_not_duplicated_when_present():
    ws = Workspace({"sales.csv": "m,revenue\na,1200\nb,34.5\n"})
    r = turn(ws, "Total of revenue in sales.csv?", 1,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("The total is 1,234.5.")])
    expect(r.message == "The total is 1,234.5.", r.message)


def t_csv_wrong_column_name_is_self_corrected():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "What is the total of the income column in sales.csv?", 1,
             [call("csv_aggregate", path="sales.csv", column="income", operation="sum"),
              call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    expect(r.status == "success" and saw("Its columns are: month, revenue"), r.stdout)


def t_csv_missing_file_and_missing_id_are_errors():
    ws = Workspace({"records.csv": RECORDS})
    r = turn(ws, "Total the revenue column of nope.csv", 1,
             [call("csv_aggregate", path="nope.csv", column="revenue", operation="sum"), fail("Could not find nope.csv.")])
    expect(r.status == "error" and "nope.csv" in r.message, r.stdout)
    r = turn(ws, "In records.csv, change the status of the row with ID 999 to done and save as out.csv.", 2,
             [call("csv_update_cell", path="records.csv", id_column="ID", id_value=999, column="status",
                   new_value="done", output_path="out.csv"), fail("No row has ID 999.")])
    expect(r.status == "error" and not ws.exists("out.csv") and ws.read("records.csv") == RECORDS, r.stdout)


def t_csv_manual_write_fallback_stays_valid():
    # A model that rewrites the table by hand (in a code fence) still produces a clean CSV file.
    ws = Workspace({"orders.csv": ORDERS})
    body = "order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1\n"
    r = turn(ws, "In orders.csv remove cancelled rows and save the result as active.csv", 1,
             [call("read_file", path="orders.csv"), call("write_file", path="active.csv", content="```csv\n" + body + "```"), ok()])
    expect(r.status == "success" and ws.read("active.csv").strip() == body.strip(), repr(ws.read("active.csv")))


def t_csv_semicolon_and_crlf_preserved():
    text = "id;qty\r\n1;5\r\n2;6\r\n"
    ws = Workspace({})
    ws.write("eu.csv", text)
    r = turn(ws, "In eu.csv set qty of the row with id 2 to 9 and save the file.", 1,
             [call("csv_update_cell", path="eu.csv", id_column="id", id_value=2, column="qty", new_value=9), ok()])
    expect(raw(ws, "eu.csv") == b"id;qty\r\n1;5\r\n2;9\r\n", repr(raw(ws, "eu.csv")))


# ======================================================================
# Follow-ups that depend on earlier turns
# ======================================================================
def t_followup_edit_of_file_named_earlier():
    ws = Workspace({"records.csv": RECORDS})
    turn(ws, "In records.csv, change the status of the row with ID 104 to complete, and save the result as "
             "updated_records.csv.", 9,
         [call("csv_update_cell", path="records.csv", id_column="ID", id_value=104, column="status",
               new_value="complete", output_path="updated_records.csv"), ok()])
    r = turn(ws, "Now do the same for ID 107 in the updated file.", 9,
             [call("csv_update_cell", path="updated_records.csv", id_column="ID", id_value=107, column="status",
                   new_value="complete"), ok()])
    expect(r.status == "success", r.stdout)
    expect(ws.read("updated_records.csv") == "ID,name,status\n101,Ann,open\n104,Bob,complete\n107,Cy,complete\n",
           repr(ws.read("updated_records.csv")))
    expect(ws.read("records.csv") == RECORDS, "original changed")


def t_followup_edit_may_not_invent_a_new_file():
    ws = Workspace({"records.csv": RECORDS})
    turn(ws, "In records.csv, change the status of the row with ID 104 to complete, and save the result as "
             "updated_records.csv.", 9,
         [call("csv_update_cell", path="records.csv", id_column="ID", id_value=104, column="status",
               new_value="complete", output_path="updated_records.csv"), ok()])
    r = turn(ws, "Now do the same for ID 107 in the updated file.", 9,
             [call("csv_update_cell", path="updated_records.csv", id_column="ID", id_value=107, column="status",
                   new_value="complete", output_path="updated_records_v2.csv"),
              call("csv_update_cell", path="updated_records.csv", id_column="ID", id_value=107, column="status",
                   new_value="complete"), ok()])
    expect(r.status == "success" and not ws.exists("updated_records_v2.csv"), "an invented file was created")
    expect("107,Cy,complete" in ws.read("updated_records.csv"), repr(ws.read("updated_records.csv")))
    expect(saw("do not invent"), "invented output name was not refused")


def t_intermediate_results_are_not_appended_to_a_complete_answer():
    ws = Workspace({"candidates.csv": CANDIDATES})
    r = turn(ws, "Who has the higher test score in candidates.csv?", 9,
             [call("csv_aggregate", path="candidates.csv", column="test_score", operation="max"),
              call("csv_aggregate", path="candidates.csv", column="test_score", operation="min"),
              ok("Raj has the higher test score, 44.")])
    expect(r.message == "Raj has the higher test score, 44.", r.message)
    expect(saw("name=Raj"), "max result did not include the matching row")


def t_followup_delete_of_file_named_earlier():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Save a copy of sales.csv as reports/sales_copy.csv.", 9,
         [call("copy_file", source_path="sales.csv", destination_path="reports/sales_copy.csv"), ok()])
    r = turn(ws, "Actually, delete that copy.", 9, [call("delete_file", path="reports/sales_copy.csv"), ok()])
    expect(r.status == "success" and not ws.exists("reports/sales_copy.csv") and ws.exists("sales.csv"), r.stdout)


def t_followup_cannot_delete_file_never_mentioned():
    ws = Workspace({"sales.csv": SALES, "secret.csv": "x\n1\n"})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 9,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    r = turn(ws, "Delete it.", 9, [call("delete_file", path="secret.csv"), call("delete_file", path="sales.csv"), ok()])
    expect(ws.exists("secret.csv"), "deleted a file that was never mentioned in the conversation")
    expect(not ws.exists("sales.csv"), "the file referred to as 'it' was not deleted")


def t_followup_copy_with_model_chosen_name():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 9,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    r = turn(ws, "Make a backup copy of it.", 9,
             [call("copy_file", source_path="sales.csv", destination_path="sales_backup.csv"), ok("Saved sales_backup.csv.")])
    expect(r.status == "success" and raw(ws, "sales_backup.csv") == SALES.encode(), r.stdout)


def t_followup_answer_from_memory_is_allowed():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 9,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("The total is 300.")])
    r = turn(ws, "What was that number again?", 9, [ok("The total was 300.")])
    expect(r.status == "success" and "300" in r.message, r.stdout)


def t_mental_arithmetic_is_sent_back_to_the_calculator():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "Read sales.csv and tell me the total of the revenue column.", 9,
             [call("read_file", path="sales.csv"), ok("The total is 310."),
              call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("The total is 300.")])
    expect(r.status == "success" and "300" in r.message and "310" not in r.message, r.stdout)
    expect(saw("do not calculate by hand"), "hand calculation was not rejected")


def t_followup_guess_from_memory_is_sent_back_to_the_calculator():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 9,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    r = turn(ws, "What is the average?", 9,
             [ok("The average is 140."),
              call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("The average is 150.")])
    expect(r.status == "success" and "150" in r.message, r.stdout)


def t_hand_typed_copy_is_made_byte_exact():
    text = "month,revenue\r\nJanuary,120\r\nFebruary,180\r\n"
    ws = Workspace({})
    ws.write("sales.csv", text)
    r = turn(ws, "Save a copy of sales.csv as reports/sales_copy.csv.", 9,
             [call("read_file", path="sales.csv"),
              call("write_file", path="reports/sales_copy.csv", content="month,revenue\nJanuary,120\nFebruary,180"), ok()])
    expect(r.status == "success" and raw(ws, "reports/sales_copy.csv") == text.encode(),
           repr(raw(ws, "reports/sales_copy.csv")))


def t_hand_written_csv_gets_final_newline():
    ws = Workspace({"orders.csv": ORDERS})
    r = turn(ws, "In orders.csv remove cancelled rows and save the result as active.csv", 9,
             [call("read_file", path="orders.csv"),
              call("write_file", path="active.csv", content="order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1"), ok()])
    expect(ws.read("active.csv") == "order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1\n",
           repr(ws.read("active.csv")))


def t_first_turn_cannot_claim_success_without_acting():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "What is the total of the revenue column in sales.csv?", 9, [ok("It is 999."), ok("It is 999.")])
    expect(r.status == "error", "accepted an answer that was never computed: " + r.stdout)


# ======================================================================
# Safety rules that must survive the new features
# ======================================================================
def t_question_cannot_modify_the_csv():
    ws = Workspace({"records.csv": RECORDS})
    r = turn(ws, "According to records.csv, what is the status of ID 104?", 1,
             [call("csv_update_cell", path="records.csv", id_column="ID", id_value=104, column="status",
                   new_value="hacked"), call("read_file", path="records.csv"), ok("pending")])
    expect(ws.read("records.csv") == RECORDS, "a question modified the file")
    expect(saw("only asks a question"), "in-place edit was not refused")


def t_two_different_files_cannot_be_changed_in_one_turn():
    ws = Workspace({"records.csv": RECORDS, "orders.csv": ORDERS})
    r = turn(ws, "In records.csv, change the status of ID 104 to complete and save the file.", 1,
             [call("csv_update_cell", path="records.csv", id_column="ID", id_value=104, column="status", new_value="complete"),
              call("csv_delete_rows", path="orders.csv", column="status", value="cancelled"), ok()])
    expect(ws.read("orders.csv") == ORDERS, "a second file was changed")
    expect("104,Bob,complete" in ws.read("records.csv"), r.stdout)


def t_same_file_can_be_edited_twice_in_one_turn():
    ws = Workspace({"records.csv": RECORDS})
    r = turn(ws, "In records.csv, change the status of ID 104 to complete and the name of ID 101 to Anna, "
                 "and save the result as fixed.csv.", 1,
             [call("csv_update_cell", path="records.csv", id_column="ID", id_value=104, column="status",
                   new_value="complete", output_path="fixed.csv"),
              call("csv_update_cell", path="fixed.csv", id_column="ID", id_value=101, column="name",
                   new_value="Anna", output_path="fixed.csv"), ok()])
    expect(r.status == "success", r.stdout)
    expect(ws.read("fixed.csv") == "ID,name,status\n101,Anna,open\n104,Bob,complete\n107,Cy,open\n",
           repr(ws.read("fixed.csv")))
    expect(ws.read("records.csv") == RECORDS, "original changed")


def t_copy_cannot_escape_or_overwrite_unnamed_file():
    ws = Workspace({"sales.csv": SALES, "important.csv": "keep\n"})
    r = turn(ws, "Save a copy of sales.csv as backup/sales.csv", 1,
             [call("copy_file", source_path="sales.csv", destination_path="../outside.csv"),
              call("copy_file", source_path="sales.csv", destination_path="important.csv"),
              call("copy_file", source_path="sales.csv", destination_path="backup/sales.csv"), ok()])
    expect(ws.read("important.csv") == "keep\n", "copy overwrote an unrelated file")
    expect(not os.path.exists(os.path.join(os.path.dirname(ws.dir), "outside.csv")), "escaped the workspace")
    expect(raw(ws, "backup/sales.csv") == SALES.encode(), r.stdout)


def t_session_store_is_outside_the_workspace():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 1,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    expect(os.listdir(ws.dir) == ["sales.csv"], "session data leaked into the workspace: %r" % os.listdir(ws.dir))


def t_stdout_contract_on_every_turn():
    ws = Workspace({"sales.csv": SALES})
    for prompt, replies in (("Total of revenue in sales.csv?", [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")]),
                            ("And the average?", [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")]),
                            ("Delete nothing.csv", [call("delete_file", path="nothing.csv"), fail("Could not find nothing.csv.")])):
        r = turn(ws, prompt, 3, replies)
        expect(r.stdout.count("\n") == 1 and r.stdout.startswith("{"), repr(r.stdout))


# ======================================================================
# MORE EDGE CASES: odd CSV files, workspace changes, session-store faults
# ======================================================================
BOM = b"\xef\xbb\xbf"


def t_csv_bom_preserved_on_update_and_delete():
    ws = Workspace({})
    ws.write("bom.csv", BOM + RECORDS.encode())
    turn(ws, "In bom.csv change the status of ID 104 to complete and save the result as bom_updated.csv.", 1,
         [call("csv_update_cell", path="bom.csv", id_column="ID", id_value=104, column="status", new_value="complete",
               output_path="bom_updated.csv"), ok()])
    expect(raw(ws, "bom_updated.csv") == BOM + RECORDS.replace("104,Bob,pending", "104,Bob,complete").encode(),
           repr(raw(ws, "bom_updated.csv")))
    turn(ws, "In bom.csv remove every row whose status is open and save the result as bom_open.csv.", 2,
         [call("csv_delete_rows", path="bom.csv", column="status", value="open", output_path="bom_open.csv"), ok()])
    expect(raw(ws, "bom_open.csv") == BOM + b"ID,name,status\n104,Bob,pending\n", repr(raw(ws, "bom_open.csv")))
    expect(raw(ws, "bom.csv") == BOM + RECORDS.encode(), "source changed")


def t_csv_bom_header_is_matched():
    ws = Workspace({})
    ws.write("b.csv", BOM + b"amount,label\n5,a\n7,b\n")
    r = turn(ws, "Total of the amount column in b.csv?", 1,
             [call("csv_aggregate", path="b.csv", column="amount", operation="sum"), ok("12")])
    expect(r.status == "success" and "12" in r.message and saw("= 12"), r.stdout)


def t_csv_created_in_one_turn_and_used_in_the_next():
    ws = Workspace({})
    turn(ws, "Create scores.csv with columns name and score and rows Ann 90 and Bob 75.", 4,
         [call("write_file", path="scores.csv", content="name,score\nAnn,90\nBob,75\n"), ok()])
    r = turn(ws, "What is the average score?", 4,
             [call("csv_aggregate", path="scores.csv", column="score", operation="average"), ok("82.5")])
    expect(r.status == "success" and "82.5" in r.message, r.stdout)
    expect("scores.csv" in first_message(), "the file created in turn 1 is not in the history")


def t_previous_result_saved_to_a_file():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 4,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("The total is 300.")])
    r = turn(ws, "Save that total to reports/total.txt.", 4,
             [call("write_file", path="reports/total.txt", content="300"), ok()])
    expect(r.status == "success" and ws.read("reports/total.txt") == "300", r.stdout)


def t_workspace_changes_between_turns_are_seen():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 4,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    ws.write("sales.csv", SALES + "March,100\n")  # someone edits the file between turns
    ws.write("new_data.csv", "a\n1\n")
    r = turn(ws, "What is the total now?", 4,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("400")])
    expect("400" in r.message and "new_data.csv" in first_message() and saw("= 400"), r.stdout)


def t_corrupt_session_store_does_not_break_the_turn():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", 4,
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    store = os.environ.get("AGENT_SESSION_DIR") or __import__("common").SESSION_DIR
    for f in os.listdir(store):
        if f.endswith("_4.json"):
            with open(os.path.join(store, f), "w") as fh:
                fh.write("{broken")
    r = turn(ws, "What is the average?", 4,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")])
    expect(r.status == "success" and "Conversation so far" not in first_message(), r.stdout)
    turn(ws, "And the total?", 4, [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    expect("What is the average?" in first_message(), "history did not recover after the corrupt file")


def t_unwritable_session_dir_falls_back():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))
    import session as session_mod
    ws = Workspace({"sales.csv": SALES, "blocker": "not a directory"})
    env = dict(ENV, AGENT_SESSION_DIR=ws.path("blocker") + "/sub")  # cannot be created
    try:
        turn(ws, "Read sales.csv and tell me the total of the revenue column.", 123456789,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")], env=env)
        turn(ws, "What is the average?", 123456789,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")], env=env)
        expect("Read sales.csv and tell me the total" in first_message(), "history lost with an unwritable store")
    finally:
        name = session_mod._file_name(ws.dir, "123456789")
        for d in session_mod._candidates(ws.dir):
            try:
                os.unlink(os.path.join(d, name))
            except OSError:
                pass


def t_history_keeps_only_the_last_30_turns():
    ws = Workspace({"sales.csv": SALES})
    for i in range(32):
        turn(ws, "Question #%02d: total revenue in sales.csv?" % i, 77,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    sent = first_message()
    expect("Question #00" not in sent and "Question #01" in sent and "Question #30" in sent, "wrong history window")
    expect(sent.count("[Turn ") == 30, "history has %d turns" % sent.count("[Turn "))


def t_session_ids_zero_and_leading_zero_differ():
    ws = Workspace({"sales.csv": SALES})
    turn(ws, "Read sales.csv and tell me the total of the revenue column.", "0",
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    turn(ws, "What is the average?", "00", [fail("no context")])
    expect("Conversation so far" not in first_message(), "'0' and '00' shared history")
    turn(ws, "What is the average?", "0",
         [call("csv_aggregate", path="sales.csv", column="revenue", operation="average"), ok("150")])
    expect("Conversation so far" in first_message(), "session 0 lost its history")


def t_history_turns_are_in_order():
    ws = Workspace({"sales.csv": SALES})
    for word in ("alpha", "bravo", "charlie"):
        turn(ws, "Turn %s: total revenue in sales.csv?" % word, 66,
             [call("csv_aggregate", path="sales.csv", column="revenue", operation="sum"), ok("300")])
    sent = first_message()
    expect(sent.index("Turn alpha") < sent.index("Turn bravo") < sent.index("Turn charlie"), "turns out of order")


def t_csv_tools_refuse_paths_outside_the_workspace():
    ws = Workspace({"orders.csv": ORDERS})
    r = turn(ws, "Total of the amount column in ../orders.csv", 1,
             [call("csv_aggregate", path="../orders.csv", column="amount", operation="sum"), fail("Path refused.")])
    expect(r.status == "error" and saw("escapes the workspace"), r.stdout)
    r = turn(ws, "In orders.csv remove cancelled rows and save the result as ../active.csv", 2,
             [call("csv_delete_rows", path="orders.csv", column="status", value="cancelled", output_path="../active.csv"),
              fail("Path refused.")])
    expect(r.status == "error" and not os.path.exists(os.path.join(os.path.dirname(ws.dir), "active.csv")), r.stdout)
    expect(ws.read("orders.csv") == ORDERS, "source changed")


def t_csv_output_cannot_overwrite_an_unrelated_file():
    ws = Workspace({"orders.csv": ORDERS, "other.csv": "keep\n"})
    r = turn(ws, "In orders.csv remove cancelled rows and save the result as active.csv", 1,
             [call("csv_delete_rows", path="orders.csv", column="status", value="cancelled", output_path="other.csv"),
              call("csv_delete_rows", path="orders.csv", column="status", value="cancelled", output_path="active.csv"), ok()])
    expect(ws.read("other.csv") == "keep\n" and ws.exists("active.csv"), r.stdout)


def t_copy_onto_a_directory_fails_cleanly():
    ws = Workspace({"sales.csv": SALES, "reports/x.txt": "x"})
    r = turn(ws, "Save a copy of sales.csv as reports", 1,
             [call("copy_file", source_path="sales.csv", destination_path="reports"), fail("reports is a directory.")])
    expect(r.status == "error" and ws.read("reports/x.txt") == "x", r.stdout)


def t_csv_remove_rows_in_place_when_no_output_named():
    ws = Workspace({"orders.csv": ORDERS})
    r = turn(ws, "Remove every cancelled order from orders.csv.", 1,
             [call("csv_delete_rows", path="orders.csv", column="status", value="cancelled"), ok()])
    expect(r.status == "success" and ws.read("orders.csv") == "order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1\n",
           repr(ws.read("orders.csv")))


def t_csv_question_cannot_remove_rows():
    ws = Workspace({"orders.csv": ORDERS})
    r = turn(ws, "How many orders in orders.csv are cancelled?", 1,
             [call("csv_delete_rows", path="orders.csv", column="status", value="cancelled"),
              call("csv_aggregate", path="orders.csv", column="order_id", operation="count", where_column="status",
                   where_value="cancelled"), ok("2 orders are cancelled.")])
    expect(ws.read("orders.csv") == ORDERS and "2" in r.message, r.stdout)


def t_csv_tool_on_binary_or_empty_file():
    ws = Workspace({"empty.csv": "", "header_only.csv": "a,b\n"})
    ws.write("blob.csv", b"\x00\x01\x02")
    for path, needle in (("blob.csv", "binary"), ("empty.csv", "empty"), ("header_only.csv", "no numeric values")):
        r = turn(ws, "Total of column a in %s?" % path, 1,
                 [call("csv_aggregate", path=path, column="a", operation="sum"), fail("cannot")])
        expect(r.status == "error" and saw(needle), "%s: %s" % (path, r.stdout))


def t_csv_filtered_total():
    ws = Workspace({"orders.csv": ORDERS})
    r = turn(ws, "What is the total amount of the cancelled orders in orders.csv?", 1,
             [call("csv_aggregate", path="orders.csv", column="amount", operation="sum", where_column="status",
                   where_value="cancelled"), ok("19.25")])
    expect("19.25" in r.message and saw("where status = cancelled"), r.stdout)


def t_csv_update_without_id_column_uses_id():
    ws = Workspace({"records.csv": RECORDS})
    r = turn(ws, "In records.csv set the name of ID 101 to Anna and save as named.csv.", 1,
             [call("csv_update_cell", path="records.csv", id_value=101, column="name", new_value="Anna",
                   output_path="named.csv"), ok()])
    expect("101,Anna,open" in ws.read("named.csv"), r.stdout)


def t_tool_and_arg_aliases_for_csv():
    ws = Workspace({"sales.csv": SALES})
    r = turn(ws, "Total of revenue in sales.csv?", 1,
             [json.dumps({"tool": "csv_sum", "args": {"file": "sales.csv", "col": "revenue", "op": "total"}}), ok("300")])
    expect(r.status == "success" and "300" in r.message, r.stdout)
    r = turn(ws, "Copy sales.csv to b/sales.csv", 2,
             [json.dumps({"tool": "cp", "args": {"src": "sales.csv", "dst": "b/sales.csv"}}), ok()])
    expect(raw(ws, "b/sales.csv") == SALES.encode(), r.stdout)


def main():
    names = sys.argv[1:]
    runner = Runner()
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn) and (not names or any(n in name for n in names)):
            runner.case(name[2:], fn)
    SERVER.close()
    return runner.summary()


if __name__ == "__main__":
    sys.exit(main())
