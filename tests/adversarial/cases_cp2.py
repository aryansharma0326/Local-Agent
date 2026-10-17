"""Checkpoint 2 adversarial conversations: context, session isolation, CSV.

Every conversation runs in one fresh workspace with a fresh, empty session
store. Turns run in order; each turn is a new ``run_agent.sh PROMPT SESSION``
process. A conversation passes only if every turn passes.
"""

from checks import (absent, answer_all_upper, answer_has, answer_lacks, answer_no_uppercase_only,
                    answer_only_number, csv_rows, file_bytes, file_has, file_text, same_bytes_as)

CASES = []


def T(sid, prompt, expect="success", checks=()):
    return dict(sid=str(sid), prompt=prompt, expect=expect, checks=list(checks))


def conv(id, category, tag, attack, files, turns, changes=()):
    CASES.append(dict(id=id, cp="cp2", group=category, tag=tag, attack=attack, files=files, turns=turns,
                      changes=list(changes)))


SALES = "month,revenue\nJanuary,120\nFebruary,180\n"
SALES4 = "month,revenue\nJanuary,120\nFebruary,180\nMarch,90\nApril,210\n"

# ============================================================ grader regressions
# The four failed hidden cases from the hw1-2 grading report, verbatim.
conv("GR-01", "context", "grader-known", "hidden_memory_recall from the grading report (first turn only states a fact).",
     {}, [T(6101, "My project code name is Cedar. Remember it for this conversation."),
          T(6101, "What is my project code name?", checks=[answer_has("cedar")])])

conv("GR-02", "context", "grader-known", "hidden_memory_correction from the grading report.",
     {}, [T(6303, "My preferred drink is tea. Remember that."),
          T(6303, "Correction: my preferred drink is coffee now, replacing tea."),
          T(6303, "What is my current preferred drink?", checks=[answer_has("coffee")])])

conv("GR-03", "isolation", "grader-known", "hidden_session_separation from the grading report.",
     {}, [T(6404, "My project code name is Harbor. Remember it for this conversation."),
          T(6405, "My project code name is Meadow. Remember it for this conversation."),
          T(6404, "What is my project code name?", checks=[answer_has("harbor"), answer_lacks("meadow")]),
          T(6405, "What is my project code name?", checks=[answer_has("meadow"), answer_lacks("harbor")])])

conv("GR-04", "isolation", "grader-known", "hidden_session_separation_after_update from the grading report.",
     {}, [T(6506, "My meeting day is Tuesday. Remember it for this conversation."),
          T(6507, "My meeting day is Wednesday. Remember it for this conversation."),
          T(6506, "My meeting has moved to Friday. Replace the earlier day with Friday."),
          T(6507, "What is my current meeting day?", checks=[answer_has("wednesday"), answer_lacks("friday")]),
          T(6506, "What is my current meeting day?", checks=[answer_has("friday")])])

conv("GR-05", "csv", "grader-known", "The assignment's own three-turn example (total, average, copy).",
     {"sales.csv": SALES},
     [T(1001, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("300")]),
      T(1001, "What is the average?", checks=[answer_has("150")]),
      T(1001, "Save a copy of the table as reports/sales_copy.csv.",
        checks=[same_bytes_as("reports/sales_copy.csv", SALES)])],
     changes=["reports/"])

# ===================================================================== context
conv("CX-01", "context", "grader-like", "Reworded statement-only first turn ('keep that in mind'), alphanumeric value.",
     {}, [T(7101, "I'm working on a project code-named Orion-7. Keep that in mind."),
          T(7101, "What's the code name of my project?", checks=[answer_has("orion-7")])])

conv("CX-02", "context", "grader-like", "Two corrections: tea -> coffee -> back to tea.",
     {}, [T(7202, "My preferred drink is tea. Remember that."),
          T(7202, "Correction: my preferred drink is coffee now, replacing tea."),
          T(7202, "Actually, scratch that. Go back to tea; coffee is no longer my preference."),
          T(7202, "What is my current preferred drink?", checks=[answer_has("tea"),
                                                                 answer_lacks("re:\\bis coffee\\b")])])

conv("CX-03", "context", "stress", "A statement that contains a file extension (.csv) looks like file work.",
     {}, [T(7303, "My favorite file format is .csv and my lucky number is 42. Remember both."),
          T(7303, "What is my favorite file format, and what is my lucky number?",
            checks=[answer_has("csv", "42")])])

conv("CX-04", "context", "grader-like", "A remembered folder is used later as the target of a create.",
     {}, [T(7404, "Remember that my reports folder is reports/2026."),
          T(7404, "Create summary.txt in my reports folder containing: all good",
            checks=[file_text("reports/2026/summary.txt", "all good")])],
     changes=["reports/"])

conv("CX-05", "context", "stress", "A standing output-format instruction from turn 1 must hold on turns 2 and 3.",
     {"sales.csv": SALES},
     [T(7505, "From now on in this conversation, answer with only the number and no other words."),
      T(7505, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_only_number("300")]),
      T(7505, "What is the average?", checks=[answer_only_number("150")])])

conv("CX-06", "context", "grader-like", "'That new file' in turn 3 must resolve to the file saved in turn 2.",
     {"sales.csv": SALES4},
     [T(7606, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("600")]),
      T(7606, "Remove the rows where revenue is below 150 and save the result as big_months.csv.",
        checks=[csv_rows("big_months.csv", [["month", "revenue"], ["February", "180"], ["April", "210"]])]),
      T(7606, "What is the total of the revenue column in that new file?", checks=[answer_has("390")])],
     changes=["big_months.csv"])

conv("CX-07", "context", "stress", "After turn 1 the no-op success guard is off; 'Discard' may 'succeed' with no "
     "action.",
     {"sales.csv": SALES, "old.txt": "obsolete\n", "new.txt": "current\n"},
     [T(7707, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("300")]),
      T(7707, "Discard old.txt.", checks=[absent("old.txt")])],
     changes=["old.txt"])

_bg = ("Some background for this conversation: I manage the logistics team at a mid-sized retailer, we ship out "
       "of two warehouses in Ohio and Texas, our busiest season is November, and I usually work late on "
       "Thursdays. Most importantly, my employee badge number is 48213-QX; please remember it.")
conv("CX-08", "context", "stress", "Fact sits past character 200 of turn 1, then 9 more turns (history keeps 8 "
     "verbatim and cuts older turns to 200 characters).",
     {"numbers.csv": "label,value\na,5\nb,7\nc,11\n"},
     [T(7808, _bg),
      T(7808, "Read numbers.csv and tell me the total of the value column.", checks=[answer_has("23")]),
      T(7808, "What is the average?", checks=[answer_has(("7.67", "7.666", "7.7"))]),
      T(7808, "My favorite color is green. Remember that too."),
      T(7808, "For the row whose label is b, add the value column to 10. What is the result?",
        checks=[answer_has("17")]),
      T(7808, "Which warehouse states did I mention?", checks=[answer_has("ohio", "texas")]),
      T(7808, "What is the largest value in the value column?", checks=[answer_has("11")]),
      T(7808, "Thanks. Nothing else about the file for now."),
      T(7808, "What is my favorite color?", checks=[answer_has("green")]),
      T(7808, "What is my busiest season?", checks=[answer_has("november")]),
      T(7808, "What is my employee badge number?", checks=[answer_has("48213-qx")])])

conv("CX-09", "context", "grader-like", "'That file' and 'it' refer back to the file created in turn 1.",
     {"notes/keep.txt": "keep\n"},
     [T(7909, "Create notes/todo.txt containing: buy milk", checks=[file_text("notes/todo.txt", "buy milk")]),
      T(7909, "What does that file say?", checks=[answer_has("buy milk")]),
      T(7909, "Delete it.", checks=[absent("notes/todo.txt")])],
     changes=["notes/todo.txt"])

conv("CX-10", "context", "grader-like", "A computed result is reused twice: multiplied, then written to a file.",
     {"candidates.csv": "id,name,interview_score,test_score\n1,Ana,42.5,38\n2,Ben,40,45\n"},
     [T(8010, "Read candidates.csv. For the row whose id is 1, add the values in the interview_score and "
              "test_score columns. What is the result?", checks=[answer_has("80.5")]),
      T(8010, "Multiply that by 2.", checks=[answer_has("161")]),
      T(8010, "Write that number to result.txt.", checks=[file_has("result.txt", "re:\\A\\s*161(\\.0+)?\\s*\\Z")])],
     changes=["result.txt"])

ORDERS = ("order_id,customer,status,amount\n101,Ana,shipped,20.00\n102,Ben,cancelled,15.50\n"
          "103,Cy,pending,9.99\n104,Di,cancelled,42.00\n")
conv("CX-11", "context", "stress", "A turn-1 instruction about where to save CSVs must redirect a later save.",
     {"orders.csv": ORDERS},
     [T(8111, "For the rest of this conversation, whenever I ask you to save a CSV file, put it inside the "
              "exports folder."),
      T(8111, "In orders.csv, remove every row whose status is cancelled, and save the result as "
              "active_orders.csv.",
        checks=[csv_rows("exports/active_orders.csv", [["order_id", "customer", "status", "amount"],
                                                        ["101", "Ana", "shipped", "20.00"],
                                                        ["103", "Cy", "pending", "9.99"]]),
                absent("active_orders.csv")])],
     changes=["exports/"])

RECORDS = "id,name,status\n103,Lee,open\n104,Kim,open\n105,Max,open\n"
conv("CX-12", "context", "grader-like", "Turn 1 states a value without acting; turn 2 applies it.",
     {"records.csv": RECORDS},
     [T(8212, "The new status for record 104 should be 'shipped'. Don't change anything yet."),
      T(8212, "Now apply that change in records.csv and save the result as updated_records.csv.",
        checks=[csv_rows("updated_records.csv", [["id", "name", "status"], ["103", "Lee", "open"],
                                                 ["104", "Kim", "shipped"], ["105", "Max", "open"]])])],
     changes=["updated_records.csv"])

conv("CX-13", "context", "grader-like", "Memory of several details; nothing should be written to disk.",
     {}, [T(8313, "My meeting with Dana is on Thursday at 3 PM in room 4B. Please remember these details."),
          T(8313, "Which room is my meeting in, and at what time?", checks=[answer_has("4b", ("3 pm", "3pm",
                                                                                                "15:00"))])])

conv("CX-14", "context", "grader-like", "'The table' in turn 3 must mean sales.csv even after another file was read "
     "in turn 2.",
     {"sales.csv": SALES, "notes.txt": "Deadline: November 14\n"},
     [T(8414, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("300")]),
      T(8414, "Read notes.txt and tell me the deadline.", checks=[answer_has("november 14")]),
      T(8414, "Save a copy of the table as reports/sales_copy.csv.",
        checks=[same_bytes_as("reports/sales_copy.csv", SALES)])],
     changes=["reports/"])

# =================================================================== isolation
conv("IS-01", "isolation", "grader-like", "A third session that never stated a day must not borrow one.",
     {}, [T(6606, "My meeting day is Tuesday. Remember it for this conversation."),
          T(6607, "My meeting day is Wednesday. Remember it for this conversation."),
          T(6606, "My meeting has moved to Friday. Replace the earlier day with Friday."),
          T(6608, "What is my current meeting day?", expect="either",
            checks=[answer_lacks("tuesday", "wednesday", "friday")]),
          T(6607, "What is my current meeting day?", checks=[answer_has("wednesday")]),
          T(6606, "What is my current meeting day?", checks=[answer_has("friday")])])

conv("IS-02", "isolation", "grader-like", "Session B's bare follow-up must not use session A's file and column.",
     {"sales.csv": SALES},
     [T(8101, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("300")]),
      T(8102, "What is the average?", expect="either", checks=[answer_lacks("150")])])

conv("IS-03", "isolation", "stress", "A style instruction must stay in its own session.",
     {"note.txt": "Server room: floor seven\n"},
     [T(8201, "From now on, reply only in uppercase letters."),
      T(8202, "Read note.txt. Which floor is the server room on?",
        checks=[answer_has("seven"), answer_no_uppercase_only()]),
      T(8201, "Read note.txt. Which floor is the server room on?",
        checks=[answer_has("seven"), answer_all_upper()])])

conv("IS-04", "isolation", "grader-like", "'The file we discussed' in a new session must not resolve to session A's "
     "file.",
     {"a.txt": "alpha\n", "b.txt": "beta\n"},
     [T(8301, "Read a.txt and tell me what it says.", checks=[answer_has("alpha")]),
      T(8302, "Delete the file we discussed.", expect="either")])

conv("IS-05", "isolation", "grader-like", "Interleaved corrections in two sessions.",
     {}, [T(8401, "My preferred drink is tea. Remember that."),
          T(8402, "My preferred drink is tea. Remember that."),
          T(8401, "Correction: my preferred drink is coffee now, replacing tea."),
          T(8402, "What is my current preferred drink?", checks=[answer_has("tea"), answer_lacks("coffee")]),
          T(8401, "What is my current preferred drink?", checks=[answer_has("coffee")])])

conv("IS-06", "isolation", "stress", "Session ids '7' and '07' are different strings.",
     {}, [T("7", "My locker number is 12. Remember it."),
          T("07", "My locker number is 98. Remember it."),
          T("7", "What is my locker number?", checks=[answer_has("re:\\b12\\b"), answer_lacks("98")]),
          T("07", "What is my locker number?", checks=[answer_has("98"), answer_lacks("re:\\b12\\b")])])

conv("IS-07", "isolation", "stress", "Two ids above 2^53 that differ by one (float conversion would merge them).",
     {}, [T("9007199254740993", "My project code name is Harbor. Remember it for this conversation."),
          T("9007199254740992", "My project code name is Meadow. Remember it for this conversation."),
          T("9007199254740993", "What is my project code name?", checks=[answer_has("harbor"),
                                                                           answer_lacks("meadow")]),
          T("9007199254740992", "What is my project code name?", checks=[answer_has("meadow"),
                                                                           answer_lacks("harbor")])])

conv("IS-08", "isolation", "grader-like", "Two sessions each set up a file and column, then ask a bare follow-up.",
     {"north.csv": "store,units\nN1,100\nN2,200\n", "south.csv": "store,units\nS1,10\nS2,30\n"},
     [T(8601, "Read north.csv and tell me the total of the units column.", checks=[answer_has("300")]),
      T(8602, "Read south.csv and tell me the total of the units column.", checks=[answer_has("40")]),
      T(8601, "What is the average?", checks=[answer_has("150"), answer_lacks("re:\\b20\\b")]),
      T(8602, "What is the average?", checks=[answer_has("re:\\b20\\b"), answer_lacks("150")])])

COSTS = "item,amount\nrent,1000\npower,250\n"
conv("IS-09", "isolation", "grader-like", "'The table' in session A must be A's table after session B read another.",
     {"sales.csv": SALES, "costs.csv": COSTS},
     [T(8801, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("300")]),
      T(8802, "Read costs.csv and tell me the total of the amount column.", checks=[answer_has("1250")]),
      T(8801, "Save a copy of the table as reports/copy.csv.", checks=[same_bytes_as("reports/copy.csv", SALES)])],
     changes=["reports/"])

conv("IS-10", "isolation", "grader-like", "A correction in session A must not touch the same fact in session B, "
     "with a CSV task in between.",
     {"sales.csv": SALES},
     [T(8901, "My manager's name is Alice. Remember it."),
      T(8902, "My manager's name is Alice. Remember it."),
      T(8901, "Read sales.csv and tell me the total of the revenue column.", checks=[answer_has("300")]),
      T(8901, "Update: my manager is now Bob, not Alice."),
      T(8902, "Who is my manager?", checks=[answer_has("alice"), answer_lacks("bob")]),
      T(8901, "Who is my manager?", checks=[answer_has("bob")])])

# ========================================================================= csv
conv("CV-A01", "csv", "grader-like", "Quoted currency amounts with thousands separators and a quoted name.",
     {"payments.csv": 'id,customer,amount\n1,Acme,"$1,200.50"\n2,Globex,$899.99\n3,"Initech, LLC","$2,000.00"\n'},
     [T(9001, "Read payments.csv and tell me the total of the amount column.",
        checks=[answer_has(("4100.49", "4,100.49"))])])

conv("CV-A02", "csv", "stress", "Accounting-style negative (25.00) and a plain negative.",
     {"ledger.csv": "date,amount\n2026-01-02,100.00\n2026-01-03,(25.00)\n2026-01-04,-10.50\n2026-01-05,40\n"},
     [T(9002, "Read ledger.csv and tell me the total of the amount column.",
        checks=[answer_has(("104.5",))])])

conv("CV-A03", "csv", "grader-like", "Update by ID when a 'paid' column (ends in 'id') precedes 'order_id' and holds "
     "the value 104 in another row.",
     {"orders.csv": "invoice,paid,order_id,status\nINV-1,104,101,pending\nINV-2,50,104,pending\n"
                    "INV-3,75,105,pending\n"},
     [T(9003, "In orders.csv, change the status of the order with ID 104 to complete, and save the result as "
              "updated_orders.csv.",
        checks=[csv_rows("updated_orders.csv", [["invoice", "paid", "order_id", "status"],
                                                ["INV-1", "104", "101", "pending"],
                                                ["INV-2", "50", "104", "complete"],
                                                ["INV-3", "75", "105", "pending"]])])],
     changes=["updated_orders.csv"])

conv("CV-A04", "csv", "grader-like", "String IDs where one ID is a prefix of another (A-104 vs A-1040).",
     {"records.csv": "id,name,status\nA-103,Lee,open\nA-104,Kim,open\nA-1040,Max,open\n"},
     [T(9004, "In records.csv, change the status of the row with ID A-104 to complete, and save the result as "
              "updated_records.csv.",
        checks=[csv_rows("updated_records.csv", [["id", "name", "status"], ["A-103", "Lee", "open"],
                                                 ["A-104", "Kim", "complete"], ["A-1040", "Max", "open"]])])],
     changes=["updated_records.csv"])

conv("CV-A05", "csv", "grader-like", "Condition value in mixed case and with padding spaces.",
     {"orders.csv": "id,item,status\n1,pen,cancelled\n2,ink,Cancelled\n3,pad,shipped\n4,cap, CANCELLED \n"
                    "5,mug,pending\n"},
     [T(9005, "In orders.csv, remove every row whose status is cancelled, and save the result as "
              "active_orders.csv.",
        checks=[csv_rows("active_orders.csv", [["id", "item", "status"], ["3", "pad", "shipped"],
                                               ["5", "mug", "pending"]])])],
     changes=["active_orders.csv"])

conv("CV-A06", "csv", "stress", "Numeric condition over a column with N/A and blank cells (string ordering of "
     "'N/A' > '100').",
     {"stock.csv": "item,quantity\na,150\nb,N/A\nc,90\nd,101\ne,\nf,100\n"},
     [T(9006, "In stock.csv, remove every row whose quantity is greater than 100, and save the result as "
              "low_stock.csv.",
        checks=[csv_rows("low_stock.csv", [["item", "quantity"], ["b", "N/A"], ["c", "90"], ["e", ""],
                                           ["f", "100"]])])],
     changes=["low_stock.csv"])

conv("CV-A07", "csv", "stress", "Blank cell in the averaged column (average of the 3 values, not of 4 rows).",
     {"scores.csv": "name,score\nA,80\nB,\nC,90\nD,70\n"},
     [T(9007, "Read scores.csv and tell me the average of the score column.", checks=[answer_has("re:\\b80\\b"),
                                                                                       answer_lacks("re:\\b60\\b")])])

conv("CV-A08", "csv", "grader-like", "Row picked by a quoted name containing a comma.",
     {"staff.csv": 'id,name,q1,q2\n1,"Lee, Ann",10.5,4.25\n2,Ann Lee,3,3\n'},
     [T(9008, 'Read staff.csv. For the row whose name is "Lee, Ann", add the values in the q1 and q2 columns. '
              'What is the result?', checks=[answer_has("14.75")])])

DATA_BOM = b'\xef\xbb\xbfid,comment,amount\r\n1,"multi\r\nline note",5\r\n2,"has ""quotes"", too",7\r\n'
conv("CV-A09", "csv", "stress", "Copy of a CSV with a BOM, CRLF endings, quoted newlines and escaped quotes.",
     {"data.csv": DATA_BOM},
     [T(9009, "Save a copy of data.csv as backup/data_copy.csv.",
        checks=[same_bytes_as("backup/data_copy.csv", DATA_BOM)])],
     changes=["backup/"])

conv("CV-A10", "csv", "grader-like", "Remove rows with no output path: the file is changed in place.",
     {"tasks.csv": "id,task,status\n1,write,done\n2,test,open\n3,ship,done\n4,review,open\n"},
     [T(9010, "In tasks.csv, remove every row whose status is done.",
        checks=[csv_rows("tasks.csv", [["id", "task", "status"], ["2", "test", "open"], ["4", "review", "open"]])])],
     changes=["tasks.csv"])

conv("CV-A11", "csv", "grader-like", "Binary floating point: 0.1 + 0.2 must print as 0.3.",
     {"nums.csv": "x\n0.1\n0.2\n"},
     [T(9011, "Read nums.csv and tell me the total of the x column.",
        checks=[answer_has("0.3"), answer_lacks("0.30000000000000004")])])

conv("CV-A12", "csv", "grader-like", "New value contains a comma and must be quoted in the output.",
     {"records.csv": "id,name,note\n1,Lee,ok\n3,Kim,pending\n"},
     [T(9012, 'In records.csv, change the note of the row with ID 3 to "late, call back" and save the result as '
              'records_updated.csv.',
        checks=[csv_rows("records_updated.csv", [["id", "name", "note"], ["1", "Lee", "ok"],
                                                 ["3", "Kim", "late, call back"]])])],
     changes=["records_updated.csv"])

_big = "id,region,units\n" + "".join("%d,%s,%d\n" % (i, "NSEW"[i % 4], (i * 37) % 500) for i in range(1, 40001))
_big_total = sum((i * 37) % 500 for i in range(1, 40001))
conv("CV-A13", "csv", "grader-like", "40,000-row file far beyond the context window.",
     {"big.csv": _big},
     [T(9013, "Read big.csv and tell me the total of the units column.",
        checks=[answer_has((str(_big_total), "{:,}".format(_big_total)))]),
      T(9013, "What is the average?", checks=[answer_has(("%.2f" % (_big_total / 40000.0),
                                                          str(round(_big_total / 40000.0, 1))))])])

conv("CV-A14", "csv", "stress", "Column 'qty' must not resolve to 'returned_qty' (and vice versa).",
     {"items.csv": "item,returned_qty,qty\npen,1,10\nink,2,20\npad,0,5\n"},
     [T(9014, "Read items.csv and tell me the total of the qty column.", checks=[answer_has("35"),
                                                                                answer_lacks("re:\\b3\\b")])])

conv("CV-A15", "csv", "grader-like", "Semicolon-delimited file.",
     {"eu.csv": "product;price;stock\nchair;12.50;4\ntable;80.00;2\nlamp;19.99;11\n"},
     [T(9015, "Read eu.csv and tell me the total of the stock column.", checks=[answer_has("17")])])

conv("CV-A16", "csv", "stress", "IDs '007', '7' and '70' are different strings; only '007' changes.",
     {"items.csv": "id,name,status\n007,bond,open\n7,seven,open\n70,seventy,open\n"},
     [T(9016, "In items.csv, change the status of the row with ID 007 to done, and save the result as out.csv.",
        checks=[csv_rows("out.csv", [["id", "name", "status"], ["007", "bond", "done"], ["7", "seven", "open"],
                                     ["70", "seventy", "open"]])])],
     changes=["out.csv"])

conv("CV-A17", "csv", "stress", "Date condition over US-format dates (string compare gets it wrong).",
     {"events.csv": "date,event\n3/15/2026,launch\n12/1/2025,planning\n1/5/2026,hiring\n10/2/2026,review\n"},
     [T(9017, "In events.csv, remove every row whose date is before March 1, 2026, and save the result as "
              "recent.csv.",
        checks=[csv_rows("recent.csv", [["date", "event"], ["3/15/2026", "launch"], ["10/2/2026", "review"]])])],
     changes=["recent.csv"])

conv("CV-A18", "csv", "grader-like", "Update then filter 'that file' across turns; the filter must start from the "
     "updated copy.",
     {"orders.csv": ORDERS},
     [T(9018, "In orders.csv, change the status of the row with order_id 104 to complete, and save the result as "
              "updated.csv.",
        checks=[file_has("updated.csv", "104,Di,complete")]),
      T(9018, "Now remove the cancelled rows from that file and save it as final.csv.",
        checks=[csv_rows("final.csv", [["order_id", "customer", "status", "amount"],
                                       ["101", "Ana", "shipped", "20.00"], ["103", "Cy", "pending", "9.99"],
                                       ["104", "Di", "complete", "42.00"]])])],
     changes=["updated.csv", "final.csv"])

conv("CV-A19", "csv", "grader-like", "Row sum over currency-formatted cells.",
     {"invoice.csv": "id,item,net,tax\n2,Chair,$80.00,$6.40\n3,Desk,$250.00,$20.00\n"},
     [T(9019, "Read invoice.csv. For the row whose id is 3, add the values in the net and tax columns. What is the "
              "result?", checks=[answer_has("270")])])

conv("CV-A20", "csv", "grader-like", "Boundary: 'at least 50' must remove the row equal to 50.",
     {"grades.csv": "student,score\nA,49\nB,50\nC,51\nD,12\n"},
     [T(9020, "In grades.csv, remove every row whose score is at least 50, and save the result as low.csv.",
        checks=[csv_rows("low.csv", [["student", "score"], ["A", "49"], ["D", "12"]])])],
     changes=["low.csv"])

conv("CV-A21", "csv", "grader-like", "Average over quoted values with thousands separators.",
     {"sales.csv": 'region,revenue\nNorth,"1,000"\nSouth,"2,500"\nEast,500\n'},
     [T(9021, "Read sales.csv and tell me the average of the revenue column.",
        checks=[answer_has(("1333.33", "1,333.33"))])])

conv("CV-A22", "csv", "stress", "Header cells padded with spaces and in title case.",
     {"report.csv": "Month , Revenue \nJan,10\nFeb,20\n"},
     [T(9022, "Read report.csv and tell me the total of the revenue column.", checks=[answer_has("30")])])

conv("CV-A23", "csv", "grader-like", "Row sum where the selected row is identified by a non-id column.",
     {"candidates.csv": "id,name,interview_score,test_score\n1,Ana,42,38\n2,Ben,40,45\n3,Ana Maria,10,10\n"},
     [T(9023, "Read candidates.csv. For Ben's row, add the values in the interview_score and test_score columns. "
              "What is the result?", checks=[answer_has("85")])])

conv("CV-A24", "csv", "grader-like", "Copy to a new nested folder after an in-place update in the same session.",
     {"records.csv": RECORDS},
     [T(9024, "In records.csv, change the status of the row with ID 105 to closed.",
        checks=[csv_rows("records.csv", [["id", "name", "status"], ["103", "Lee", "open"], ["104", "Kim", "open"],
                                         ["105", "Max", "closed"]])]),
      T(9024, "Save a copy of the table as archive/2026/records_copy.csv.",
        checks=[csv_rows("archive/2026/records_copy.csv", [["id", "name", "status"], ["103", "Lee", "open"],
                                                            ["104", "Kim", "open"], ["105", "Max", "closed"]])])],
     changes=["records.csv", "archive/"])

# ------------------------------------------- statement turns with file-like verbs
# harness._about_files() treats any prompt containing a verb from _FILE_ACTION
# (total, copy, list, read, update, change, ...) as file work, so a first-turn
# statement that happens to contain one of those words is refused.
conv("CX-15", "context", "grader-like", "First-turn statement containing 'total' (a file-action verb).",
     {}, [T(8515, "My total budget for the Lisbon trip is 1200 euros."),
          T(8515, "What is my budget for the Lisbon trip?", checks=[answer_has("1200")])])

conv("CX-16", "context", "grader-like", "First-turn statement containing 'copy'.",
     {}, [T(8516, "I always copy my lecture notes on Mondays."),
          T(8516, "On which day do I copy my lecture notes?", checks=[answer_has("monday")])])

conv("CX-17", "context", "grader-like", "First-turn statement containing 'list'.",
     {}, [T(8517, "My shopping list for tomorrow has eggs, rice and spinach."),
          T(8517, "What is on my shopping list?", checks=[answer_has("eggs", "rice", "spinach")])])

conv("CX-18", "context", "grader-like", "First-turn statement containing 'read'.",
     {}, [T(8518, "I read about 20 pages of my novel every night."),
          T(8518, "How many pages do I read each night?", checks=[answer_has("20")])])

conv("CX-19", "context", "grader-like", "First-turn statement containing 'updated'; later correction in the same "
     "style.",
     {}, [T(8519, "My dentist appointment has been updated to 4 PM on Monday."),
          T(8519, "Change of plans: the dentist moved it to 10 AM on Tuesday."),
          T(8519, "When is my dentist appointment now?", checks=[answer_has(("10 am", "10am", "10:00"), "tuesday")])])


# ======================================================================= round 2
# Statement turns that open with a verb-like noun ("Read receipts", "Check-in",
# "List prices", "Report cards") slip past is_statement(); plus CSV corners.

conv("CX-B01", "context", "grader-like", "Statement opening with 'Read' (read receipts).",
     {}, [T(9101, "Read receipts are turned on for my phone."),
          T(9101, "Are read receipts on or off for my phone?", checks=[answer_has(("on", "enabled", "yes"))])])

conv("CX-B02", "context", "grader-like", "Statement opening with 'Check-in'.",
     {}, [T(9102, "Check-in time at my hotel is 3 PM."),
          T(9102, "What time is check-in at my hotel?", checks=[answer_has(("3 pm", "3pm", "15:00"))])])

conv("CX-B03", "context", "grader-like", "Statement opening with 'List'.",
     {}, [T(9103, "List prices at my store went up by 5 percent this month."),
          T(9103, "By how much did list prices at my store go up this month?",
            checks=[answer_has(("5 percent", "5%", "five percent"))])])

conv("CX-B04", "context", "grader-like", "Statement opening with 'Report'.",
     {}, [T(9104, "Report cards come out on Friday."),
          T(9104, "When do report cards come out?", checks=[answer_has("friday")])])

conv("CX-B05", "context", "grader-like", "Statement that names a file and uses 'store' (no such file exists).",
     {}, [T(9105, "I store my recipes in recipes.txt on my laptop."),
          T(9105, "Where do I store my recipes?", checks=[answer_has("recipes.txt")])])

conv("CX-B06", "context", "grader-like", "Statement with a rhetorical question mark.",
     {}, [T(9106, "My dog's name is Rex, isn't that cute?"),
          T(9106, "What is my dog's name?", checks=[answer_has("rex")])])

_trip = ("Here are my travel details for the conference: my outbound flight AZ-612 leaves Boston at 07:40 on May 3; "
         "my hotel is the Casa Lumen on Rua Augusta; my seat is 14C; the hotel confirmation code is QX-88213; my "
         "return flight AZ-619 leaves on May 11; and my travel insurance policy number is TP-55-0391-K.")
conv("CX-B07", "context", "stress", "Six facts in one long first turn, then 9 more turns; the session keeps older "
     "turns clipped to 200 characters, and the policy number sits past that.",
     {"numbers.csv": "label,value\na,5\nb,7\nc,11\n"},
     [T(9107, _trip),
      T(9107, "Read numbers.csv and tell me the total of the value column.", checks=[answer_has("23")]),
      T(9107, "What is the average?", checks=[answer_has(("7.67", "7.666", "7.7"))]),
      T(9107, "My favorite color is green."),
      T(9107, "What is the largest value in the value column?", checks=[answer_has("11")]),
      T(9107, "I will also visit Porto for a day."),
      T(9107, "For the row whose label is a, add the value column to 10. What is the result?",
        checks=[answer_has("15")]),
      T(9107, "Thanks, that's all for the numbers."),
      T(9107, "What is my favorite color?", checks=[answer_has("green")]),
      T(9107, "What is my seat number?", checks=[answer_has("14c")]),
      T(9107, "What is my travel insurance policy number?", checks=[answer_has("tp-55-0391-k")])])

conv("CX-B08", "context", "grader-like", "A standing column instruction ('use the cost column from now on') must drive "
     "a later bare 'What is the total?'.",
     {"items.csv": "item,price,cost\npen,3,1\nink,10,4\npad,6,2\n"},
     [T(9108, "In items.csv, use the cost column from now on."),
      T(9108, "What is the total?", checks=[answer_has("re:\\b7\\b"), answer_lacks("re:\\b19\\b")])])

conv("DL-B02", "context", "grader-like", "'Now delete it.' must delete the file read in the previous turn, nothing else.",
     {"notes/a.txt": "alpha\n", "notes/b.txt": "beta\n"},
     [T(9109, "Read notes/a.txt and tell me what it says.", checks=[answer_has("alpha")]),
      T(9109, "Now delete it.", checks=[absent("notes/a.txt")])],
     changes=["notes/a.txt"])

conv("IS-B01", "isolation", "grader-like", "Two sessions each derive a new file; 'that new file' and 'the average' "
     "must stay in their own session.",
     {"north.csv": "store,units\nN1,100\nN2,200\nN3,300\n", "south.csv": "store,units\nS1,10\nS2,30\n"},
     [T(9201, "In north.csv, remove every row whose units is below 150, and save the result as a_big.csv.",
        checks=[csv_rows("a_big.csv", [["store", "units"], ["N2", "200"], ["N3", "300"]])]),
      T(9202, "Save a copy of south.csv as b_copy.csv.", checks=[same_bytes_as("b_copy.csv",
                                                                                "store,units\nS1,10\nS2,30\n")]),
      T(9201, "What is the total of the units column in that new file?", checks=[answer_has("500"),
                                                                                  answer_lacks("re:\\b40\\b")]),
      T(9202, "What is the average of the units column in that copy?", checks=[answer_has("re:\\b20\\b"),
                                                                                 answer_lacks("250")])],
     changes=["a_big.csv", "b_copy.csv"])

conv("CV-B01", "csv", "stress", "Day/month/year dates (stated in the prompt): '05/03/2026' is 5 March, but the date "
     "parser reads slash dates month-first.",
     {"events.csv": "date,event\n05/03/2026,prep\n15/03/2026,review\n20/02/2026,kickoff\n01/04/2026,release\n"},
     [T(9301, "In events.csv the dates are day/month/year. Remove every row whose date is before 10 March 2026, and "
              "save the result as later.csv.",
        checks=[csv_rows("later.csv", [["date", "event"], ["15/03/2026", "review"], ["01/04/2026", "release"]])])],
     changes=["later.csv"])

conv("CV-B02", "csv", "grader-like", "Two values in one condition ('cancelled or refunded').",
     {"orders.csv": "id,status\n1,cancelled\n2,shipped\n3,refunded\n4,pending\n5,Refunded\n"},
     [T(9302, "In orders.csv, remove every row whose status is cancelled or refunded, and save the result as "
              "kept.csv.",
        checks=[csv_rows("kept.csv", [["id", "status"], ["2", "shipped"], ["4", "pending"]])])],
     changes=["kept.csv"])

conv("CV-B03", "csv", "stress", "AND condition across two columns; the delete tool takes one condition.",
     {"stock.csv": "sku,qty,status\nA,0,pending\nB,0,shipped\nC,5,pending\nD,0,pending\n"},
     [T(9303, "In stock.csv, remove every row where qty is 0 and status is pending, and save the result as "
              "clean.csv.",
        checks=[csv_rows("clean.csv", [["sku", "qty", "status"], ["B", "0", "shipped"], ["C", "5", "pending"]])])],
     changes=["clean.csv"])

conv("CV-B04", "csv", "grader-like", "Header with symbols: 'Revenue ($)'.",
     {"q1.csv": "Month,Revenue ($),Cost ($)\nJan,1200,800\nFeb,1500,900\nMar,1300,850\n"},
     [T(9304, "Read q1.csv and tell me the total of the Revenue ($) column.", checks=[answer_has(("4000", "4,000"))])])

conv("CV-B05", "csv", "stress", "Header-only CSV: the total must be 0 (or a clear 'no rows' error), never invented.",
     {"empty.csv": "id,amount\n"},
     [T(9305, "Read empty.csv and tell me the total of the amount column.", expect="either",
        checks=[answer_has(("re:\\b0(\\.0+)?\\b", "no rows", "no data", "empty", "no values"))])])

conv("CV-B06", "csv", "grader-like", "Tab-separated file: update by ID, tabs preserved.",
     {"items.tsv": "id\tname\tstatus\n1\tpen\topen\n2\tink\topen\n"},
     [T(9306, "In items.tsv, change the status of the row with ID 2 to done, and save the result as out.tsv.",
        checks=[file_bytes("out.tsv", b"id\tname\tstatus\n1\tpen\topen\n2\tink\tdone\n")])],
     changes=["out.tsv"])

conv("CV-B07", "csv", "stress", "New value contains a double quote; the output must quote it correctly.",
     {"products.csv": "id,name,note\n1,tablet,ok\n2,phone,ok\n"},
     [T(9307, 'In products.csv, change the note of the row with ID 2 to: 5" screen. Save the result as '
              'products_out.csv.',
        checks=[csv_rows("products_out.csv", [["id", "name", "note"], ["1", "tablet", "ok"],
                                              ["2", "phone", '5" screen']])])],
     changes=["products_out.csv"])

conv("CV-B08", "csv", "grader-like", "Implicit follow-up 'What about February?' after a January lookup.",
     {"monthly.csv": "month,revenue,cost\nJanuary,120,80\nFebruary,180,95\nMarch,90,60\n"},
     [T(9308, "Read monthly.csv. What was the revenue in January?", checks=[answer_has("120")]),
      T(9308, "What about February?", checks=[answer_has("180"), answer_lacks("re:\\b95\\b")])])
