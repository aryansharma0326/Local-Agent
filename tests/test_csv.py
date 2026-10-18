"""Unit tests for Checkpoint 2 building blocks: the deterministic CSV
engine, the CSV/copy tools, the session store and argument parsing.

Usage: python3 tests/test_csv.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from decimal import Decimal

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent"))

import csvtools  # noqa: E402
from csvtools import Table  # noqa: E402
from errors import AgentError  # noqa: E402
import main as agent_main  # noqa: E402
from sandbox import Sandbox  # noqa: E402
import session  # noqa: E402
import tools  # noqa: E402
import verify  # noqa: E402

SALES = "month,revenue\nJanuary,120\nFebruary,180\n"
RECORDS = "ID,name,status\n101,Ann,open\n104,Bob,pending\n107,Cy,open\n"
ORDERS = "order_id,item,status,amount\n1,pen,shipped,3.50\n2,ink,cancelled,12\n3,pad,Cancelled,7.25\n4,cap,open,1\n"
CANDIDATES = "id,name,interview_score,test_score\n1,Ana,41,37.5\n2,Raj,30,44\n"


class Numbers(unittest.TestCase):
    def test_parse_number(self):
        ok = {"120": "120", " 7 ": "7", "1,200": "1200", "1,234,567.89": "1234567.89", "$3.50": "3.5",
              "45%": "45", "(12)": "-12", "-0.5": "-0.5", ".5": "0.5", "1e3": "1000", "+4": "4", "€9": "9"}
        for text, want in ok.items():
            self.assertEqual(csvtools.parse_number(text), Decimal(want), text)
        for bad in ["", "  ", "abc", "12abc", "1,2,3", "N/A", "--5", "1.2.3", None]:
            self.assertIsNone(csvtools.parse_number(bad), repr(bad))

    def test_fmt_number(self):
        cases = {"300": "300", "300.0": "300", "150.50": "150.5", "0.30": "0.3", "-0.0": "0",
                 "1E+3": "1000", "33.33333333333333": "33.3333333333"}
        for raw, want in cases.items():
            self.assertEqual(csvtools.fmt_number(Decimal(raw)), want, raw)
        self.assertEqual(csvtools.fmt_number(Decimal("0.1") + Decimal("0.2")), "0.3")
        self.assertEqual(csvtools.describe_number(Decimal("150.336")), "150.336 (rounded to 2 decimals: 150.34)")
        self.assertEqual(csvtools.describe_number(Decimal("150")), "150")


class Parsing(unittest.TestCase):
    def test_split_records(self):
        self.assertEqual(csvtools.split_records("a,b\n1,2\n"), ["a,b\n", "1,2\n"])
        self.assertEqual(csvtools.split_records("a,b\r\n1,2"), ["a,b\r\n", "1,2"])
        self.assertEqual(csvtools.split_records('a,b\n"x\ny",2\n'), ["a,b\n", '"x\ny",2\n'])
        self.assertEqual(csvtools.split_records(""), [])

    def test_table_basics(self):
        t = Table(SALES)
        self.assertEqual(t.header, ["month", "revenue"])
        self.assertEqual(len(t.data), 2)
        self.assertEqual(t.text(), SALES)

    def test_delimiters_and_header_whitespace(self):
        self.assertEqual(Table("a;b\n1;2\n").delimiter, ";")
        self.assertEqual(Table("a\tb\n1\t2\n").delimiter, "\t")
        self.assertEqual(Table("a|b\n1|2\n").delimiter, "|")
        self.assertEqual(Table("only\n1\n").delimiter, ",")
        self.assertEqual(Table(" id , name \n1,x\n").header, ["id", "name"])

    def test_column_lookup(self):
        t = Table("Order ID,Unit Price,status\n1,2,x\n")
        self.assertEqual(t.column("status"), 2)
        self.assertEqual(t.column("STATUS"), 2)
        self.assertEqual(t.column("unit_price"), 1)
        self.assertEqual(t.column("order id"), 0)
        self.assertEqual(t.column("id", role="id"), 0)
        with self.assertRaisesRegex(AgentError, "Its columns are: Order ID, Unit Price, status"):
            t.column("price")
        with self.assertRaises(AgentError):
            t.column("id")  # ambiguous-ish names are only resolved for the id role

    def test_empty_and_blank_lines(self):
        with self.assertRaises(AgentError):
            Table("")
        with self.assertRaises(AgentError):
            Table("\n\n")
        t = Table("a,b\n\n1,2\n\n3,4\n")
        self.assertEqual(len(t.data), 2)


class Aggregates(unittest.TestCase):
    def test_assignment_example(self):
        t = Table(SALES, "sales.csv")
        self.assertEqual(csvtools.aggregate(t, "revenue", "sum")[0], "300")
        self.assertEqual(csvtools.aggregate(t, "revenue", "average")[0], "150")
        self.assertIn("sum of revenue in sales.csv over 2 value(s) = 300", csvtools.aggregate(t, "revenue", "sum")[1])

    def test_operations(self):
        t = Table(ORDERS)
        self.assertEqual(csvtools.aggregate(t, "amount", "sum")[0], "23.75")
        self.assertEqual(csvtools.aggregate(t, "amount", "average")[0], "5.9375")
        self.assertEqual(csvtools.aggregate(t, "amount", "min")[0], "1")
        self.assertEqual(csvtools.aggregate(t, "amount", "max")[0], "12")
        self.assertEqual(csvtools.aggregate(t, "amount", "count")[0], "4")
        self.assertIn("rounded to 2 decimals: 5.94", csvtools.aggregate(t, "amount", "average")[1])
        self.assertIn("found in 1 row(s): {order_id=2, item=ink, status=cancelled, amount=12}",
                      csvtools.aggregate(t, "amount", "max")[1])
        self.assertNotIn("found in", csvtools.aggregate(t, "amount", "sum")[1])

    def test_exact_decimal_arithmetic(self):
        t = Table("v\n0.1\n0.2\n")
        self.assertEqual(csvtools.aggregate(t, "v", "sum")[0], "0.3")
        t = Table("v\n1\n1\n2\n")
        self.assertEqual(csvtools.aggregate(t, "v", "average")[0], "1.3333333333")

    def test_blanks_formats_and_non_numeric(self):
        t = Table('item,price\na,"1,200"\nb,$3.50\nc,\nd,n/a\ne, 10 \n')
        value, sentence = csvtools.aggregate(t, "price", "sum")
        self.assertEqual(value, "1213.5")
        self.assertIn("over 3 value(s)", sentence)
        self.assertIn("1 non-numeric cell(s) ignored", sentence)
        self.assertEqual(csvtools.aggregate(t, "price", "average")[0], "404.5")

    def test_where_filter(self):
        t = Table(ORDERS)
        self.assertEqual(csvtools.aggregate(t, "amount", "sum", "status", "cancelled")[0], "19.25")
        with self.assertRaises(AgentError):
            csvtools.aggregate(t, "amount", "sum", "status", "nonexistent")

    def test_errors(self):
        with self.assertRaisesRegex(AgentError, "no numeric values"):
            csvtools.aggregate(Table(ORDERS), "item", "sum")
        with self.assertRaisesRegex(AgentError, "no column 'price'"):
            csvtools.aggregate(Table(ORDERS), "price", "sum")
        with self.assertRaises(AgentError):
            csvtools.aggregate(Table("a,b\n"), "a", "sum")  # header only

    def test_row_sum(self):
        t = Table(CANDIDATES, "candidates.csv")
        value, sentence = csvtools.row_sum(t, "id", "1", ["interview_score", "test_score"])
        self.assertEqual(value, "78.5")
        self.assertIn("interview_score=41, test_score=37.5; sum = 78.5", sentence)
        self.assertEqual(csvtools.row_sum(t, "ID", 2, ["Interview Score", "test score"])[0], "74")
        with self.assertRaisesRegex(AgentError, "No row"):
            csvtools.row_sum(t, "id", "9", ["test_score"])
        with self.assertRaisesRegex(AgentError, "not a number"):
            csvtools.row_sum(t, "id", "1", ["name"])
        with self.assertRaisesRegex(AgentError, "ambiguous"):
            csvtools.row_sum(Table("id,a\n1,2\n1,3\n"), "id", "1", ["a"])


class Updates(unittest.TestCase):
    def test_update_only_touches_one_cell(self):
        new, sentence = csvtools.update_cell(Table(RECORDS), "ID", "104", "status", "complete")
        self.assertEqual(new, "ID,name,status\n101,Ann,open\n104,Bob,complete\n107,Cy,open\n")
        self.assertIn("was 'pending'", sentence)

    def test_other_rows_byte_identical(self):
        text = 'id,note,qty\r\n1,"keeps, comma",5\r\n2,"needless quotes",6\r\n3,"multi\nline",7'
        new, _ = csvtools.update_cell(Table(text), "id", 2, "qty", 60)
        self.assertEqual(new, 'id,note,qty\r\n1,"keeps, comma",5\r\n2,needless quotes,60\r\n3,"multi\nline",7')

    def test_value_needing_quotes_and_numeric_id(self):
        new, _ = csvtools.update_cell(Table(RECORDS), "id", "104.0", "name", 'Bob "B", Jr')
        self.assertIn('104,"Bob ""B"", Jr",pending\n', new)

    def test_quote_all_style_preserved(self):
        text = '"id","v"\n"1","a"\n"2","b"\n'
        new, _ = csvtools.update_cell(Table(text), "id", "2", "v", "z")
        self.assertEqual(new, '"id","v"\n"1","a"\n"2","z"\n')

    def test_semicolon_file(self):
        new, _ = csvtools.update_cell(Table("id;v\n1;a\n"), "id", "1", "v", "b;c")
        self.assertEqual(new, 'id;v\n1;"b;c"\n')

    def test_short_row_is_padded(self):
        new, _ = csvtools.update_cell(Table("id,a,b\n1,x\n"), "id", "1", "b", "y")
        self.assertEqual(new, "id,a,b\n1,x,y\n")

    def test_errors(self):
        with self.assertRaisesRegex(AgentError, "No row"):
            csvtools.update_cell(Table(RECORDS), "ID", "999", "status", "x")
        with self.assertRaisesRegex(AgentError, "no column"):
            csvtools.update_cell(Table(RECORDS), "ID", "104", "state", "x")


class Deletes(unittest.TestCase):
    def test_remove_matching_case_insensitive(self):
        new, removed, kept, _ = csvtools.delete_rows(Table(ORDERS), "status", "equals", "cancelled")
        self.assertEqual((removed, kept), (2, 2))
        self.assertEqual(new, "order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1\n")

    def test_operators(self):
        t = lambda: Table(ORDERS)
        self.assertEqual(csvtools.delete_rows(t(), "amount", "greater_than", 5)[1], 2)
        self.assertEqual(csvtools.delete_rows(t(), "amount", ">=", "3.5")[1], 3)
        self.assertEqual(csvtools.delete_rows(t(), "amount", "less than", "3.5")[1], 1)
        self.assertEqual(csvtools.delete_rows(t(), "amount", "<=", 1)[1], 1)
        self.assertEqual(csvtools.delete_rows(t(), "status", "not_equals", "open")[1], 3)
        self.assertEqual(csvtools.delete_rows(t(), "item", "contains", "P")[1], 3)
        self.assertEqual(csvtools.delete_rows(Table("a,b\n1,\n2,x\n"), "b", "is_empty", "")[1], 1)
        self.assertEqual(csvtools.delete_rows(t(), "amount", "equals", "12.0")[1], 1)
        with self.assertRaisesRegex(AgentError, "Unknown operator"):
            csvtools.delete_rows(t(), "amount", "roughly", 1)

    def test_nothing_matches_is_identical(self):
        new, removed, kept, _ = csvtools.delete_rows(Table(ORDERS), "status", "equals", "returned")
        self.assertEqual((new, removed, kept), (ORDERS, 0, 4))

    def test_all_rows_removed_keeps_header(self):
        new, removed, kept, _ = csvtools.delete_rows(Table(SALES), "revenue", "greater_than", 0)
        self.assertEqual((new, removed, kept), ("month,revenue\n", 2, 0))

    def test_no_trailing_newline_preserved(self):
        new = csvtools.delete_rows(Table("a\n1\n2\n3"), "a", "equals", 3)[0]
        self.assertEqual(new, "a\n1\n2")
        new = csvtools.delete_rows(Table("a\n1\n2\n3"), "a", "equals", 1)[0]
        self.assertEqual(new, "a\n2\n3")

    def test_crlf_and_quoted_rows_untouched(self):
        text = 'id,t\r\n1,"a, b"\r\n2,x\r\n3,"q ""z"""\r\n'
        self.assertEqual(csvtools.delete_rows(Table(text), "id", "equals", 2)[0],
                         'id,t\r\n1,"a, b"\r\n3,"q ""z"""\r\n')


class CsvTools(unittest.TestCase):
    """The registered tools, run against a real sandbox directory."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.sb = Sandbox(self.root)
        self.reg = tools.build_tools(self.sb)
        for name, text in (("sales.csv", SALES), ("records.csv", RECORDS), ("orders.csv", ORDERS)):
            with open(os.path.join(self.root, name), "w", newline="") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def read(self, rel):
        with open(os.path.join(self.root, rel), "rb") as f:
            return f.read()

    def test_copy_is_byte_identical(self):
        blob = b"a,b\r\n\xff\xfe,\x00binary\n"
        with open(os.path.join(self.root, "odd.csv"), "wb") as f:
            f.write(blob)
        res = self.reg["copy_file"].handler({"source_path": "odd.csv", "destination_path": "out/deep/copy.csv"})
        self.assertEqual(self.read("out/deep/copy.csv"), blob)
        self.assertEqual(self.read("odd.csv"), blob)
        self.assertEqual(res.wrote, ("out/deep/copy.csv", blob))
        with self.assertRaises(AgentError):
            self.reg["copy_file"].handler({"source_path": "odd.csv", "destination_path": "./odd.csv"})
        with self.assertRaises(AgentError):
            self.reg["copy_file"].handler({"source_path": "missing.csv", "destination_path": "x.csv"})

    def test_aggregate_tool(self):
        args = self.reg["csv_aggregate"].validate({"path": "sales.csv", "column": "revenue", "operation": "Total"})
        res = self.reg["csv_aggregate"].handler(args)
        self.assertEqual(res.computed[0], "300")
        self.assertEqual(res.read, ["sales.csv"])
        self.assertIsNone(res.wrote)
        args = self.reg["csv_aggregate"].validate({"file": "sales.csv", "col": "revenue", "op": "mean"})
        self.assertEqual(self.reg["csv_aggregate"].handler(args).computed[0], "150")

    def test_update_tool_with_and_without_output(self):
        args = self.reg["csv_update_cell"].validate(
            {"path": "records.csv", "id_column": "ID", "id_value": 104, "column": "status",
             "new_value": "complete", "output_path": "updated_records.csv"})
        res = self.reg["csv_update_cell"].handler(args)
        self.assertEqual(self.read("records.csv").decode(), RECORDS)
        self.assertIn("104,Bob,complete", self.read("updated_records.csv").decode())
        self.assertEqual(res.wrote[0], "updated_records.csv")
        args = self.reg["csv_update_cell"].validate({"path": "records.csv", "id": "101", "column": "name", "value": "Zed"})
        self.reg["csv_update_cell"].handler(args)
        self.assertIn("101,Zed,open", self.read("records.csv").decode())

    def test_delete_rows_tool(self):
        args = self.reg["csv_delete_rows"].validate(
            {"path": "orders.csv", "column": "status", "value": "cancelled", "output_path": "active_orders.csv"})
        res = self.reg["csv_delete_rows"].handler(args)
        self.assertEqual(self.read("active_orders.csv").decode(),
                         "order_id,item,status,amount\n1,pen,shipped,3.50\n4,cap,open,1\n")
        self.assertEqual(self.read("orders.csv").decode(), ORDERS)
        self.assertIn("removed 2 of 4", res.obs)

    def test_schema_hides_internal_keys(self):
        for t in self.reg.values():
            text = json.dumps(t.schema())
            self.assertNotIn('"aliases"', text)
            self.assertNotIn('"scalar"', text)

    def test_scalar_coercion(self):
        v = self.reg["csv_update_cell"].validate
        self.assertEqual(v({"path": "a", "id_value": 104, "column": "c", "new_value": 3.0})["new_value"], "3")
        self.assertEqual(v({"path": "a", "id_value": 1.5, "column": "c", "new_value": True})["new_value"], "true")
        with self.assertRaises(ValueError):
            v({"path": "a", "id_value": [1], "column": "c", "new_value": "x"})


class ArgParsing(unittest.TestCase):
    def test_shapes(self):
        # CLI-2: argv[1] is the whole prompt, argv[2] the session id, the rest is ignored.
        p = agent_main.parse_args
        self.assertEqual(p(["Read a.csv", "1001"]), ("Read a.csv", "1001", []))
        self.assertEqual(p(["Read a.csv", " 7 "]), ("Read a.csv", "7", []))
        self.assertEqual(p(["Read a.csv"]), ("Read a.csv", None, []))
        self.assertEqual(p(["Read a.csv", ""]), ("Read a.csv", None, []))
        self.assertEqual(p(["Read a.csv", "abc"]), ("Read a.csv", "abc", []))
        self.assertEqual(p(["Set", "x", "to", "5"]), ("Set", "x", ["to", "5"]))
        self.assertEqual(p([]), ("", None, []))
        self.assertEqual(p(["", "1001"]), ("", "1001", []))


class ColumnMatching(unittest.TestCase):
    def test_cv03_exact_case_space_prefix_and_ambiguity(self):
        t = Table("Unit Price,qty_on_hand,quantity_sold\n1.5,3,4\n")
        self.assertEqual(t.column("unit_price"), 0)
        self.assertEqual(t.column("UNIT PRICE"), 0)
        self.assertEqual(t.column("qty"), 1)                       # unique prefix
        with self.assertRaisesRegex(AgentError, "no column"):
            t.column("q")                                          # too short / ambiguous prefix
        t2 = Table("a_b,ab\n1,2\n")
        with self.assertRaisesRegex(AgentError, "ambiguous"):
            t2.column("A B")


class MoreCsvEdgeCases(unittest.TestCase):
    def test_duplicate_column_names_use_first_exact_match(self):
        t = Table("score,score,name\n1,10,a\n2,20,b\n")
        self.assertEqual(csvtools.aggregate(t, "score", "sum")[0], "3")

    def test_quoted_header_with_delimiter(self):
        t = Table('"Price, USD",item\n"1,200",a\n3,b\n')
        self.assertEqual(t.header, ["Price, USD", "item"])
        self.assertEqual(csvtools.aggregate(t, "price, usd", "sum")[0], "1203")

    def test_negative_and_parenthesized_values(self):
        t = Table("v\n-5\n(3)\n10\n+2\n")
        self.assertEqual(csvtools.aggregate(t, "v", "sum")[0], "4")
        self.assertEqual(csvtools.aggregate(t, "v", "min")[0], "-5")

    def test_whitespace_only_cells_are_blank(self):
        t = Table("v\n1\n   \n3\n")
        value, sentence = csvtools.aggregate(t, "v", "average")
        self.assertEqual(value, "2")
        self.assertNotIn("non-numeric", sentence)

    def test_rows_with_extra_fields_are_preserved(self):
        text = "id,a\n1,x,EXTRA\n2,y\n"
        self.assertEqual(csvtools.update_cell(Table(text), "id", 2, "a", "z")[0], "id,a\n1,x,EXTRA\n2,z\n")
        self.assertEqual(csvtools.update_cell(Table(text), "id", 1, "a", "q")[0], "id,a\n1,q,EXTRA\n2,y\n")

    def test_leading_zero_ids_match_numerically(self):
        self.assertIn("0104,Bob,done", csvtools.update_cell(Table("ID,name,s\n0104,Bob,x\n"), "ID", "104", "s", "done")[0])

    def test_update_to_empty_and_multiline_values(self):
        self.assertEqual(csvtools.update_cell(Table("id,a\n1,x\n"), "id", 1, "a", "")[0], "id,a\n1,\n")
        self.assertEqual(csvtools.update_cell(Table("id,a\n1,x\n"), "id", 1, "a", "two\nlines")[0], 'id,a\n1,"two\nlines"\n')

    def test_unicode_headers_and_values(self):
        t = Table("produit,quantité\ncafé,3\nthé,4\n")
        self.assertEqual(csvtools.aggregate(t, "quantité", "sum")[0], "7")
        self.assertIn("thé,4", csvtools.update_cell(t, "produit", "café", "quantité", 9)[0])
        self.assertIn("café,9", csvtools.update_cell(t, "produit", "CAFÉ", "quantité", 9)[0])

    def test_large_table_is_fast_and_exact(self):
        text = "id,v\n" + "".join("%d,%d.25\n" % (i, i) for i in range(20000))
        t0 = time.monotonic()
        value = csvtools.aggregate(Table(text), "v", "sum")[0]
        self.assertEqual(value, "199995000")  # sum(0..19999) + 20000 * 0.25
        self.assertLess(time.monotonic() - t0, 5)

    def test_delete_matches_trimmed_case_insensitive_text(self):
        t = Table("id,s\n1, Cancelled \n2,CANCELLED\n3,cancel\n")
        self.assertEqual(csvtools.delete_rows(t, "s", "equals", "cancelled")[1], 2)

    def test_tab_separated(self):
        t = Table("id\tqty\n1\t5\n2\t6\n")
        self.assertEqual(csvtools.aggregate(t, "qty", "sum")[0], "11")
        self.assertEqual(csvtools.update_cell(t, "id", 2, "qty", 9)[0], "id\tqty\n1\t5\n2\t9\n")

    def test_numeric_conditions_with_thousands_separators(self):
        t = Table('id,amt\n1,"1,200"\n2,800\n3,"2,500.50"\n')
        self.assertEqual(csvtools.delete_rows(t, "amt", "greater_than", "1000")[1], 2)

    def test_where_filter_with_numeric_value(self):
        t = Table("year,sales\n2023,5\n2024,7\n2024.0,1\n")
        self.assertEqual(csvtools.aggregate(t, "sales", "sum", "year", 2024)[0], "8")

    def test_text_ordering_fallback(self):
        t = Table("id,grade\n1,A\n2,C\n3,B\n")
        self.assertEqual(csvtools.delete_rows(t, "grade", "greater_than", "B")[1], 1)

    def test_bom_preserved_by_tool(self):
        root = os.path.realpath(tempfile.mkdtemp())
        try:
            with open(os.path.join(root, "b.csv"), "wb") as f:
                f.write(b"\xef\xbb\xbfid,v\n1,a\n")
            reg = tools.build_tools(Sandbox(root))
            res = reg["csv_update_cell"].handler({"path": "b.csv", "id_value": "1", "column": "v", "new_value": "b"})
            with open(os.path.join(root, "b.csv"), "rb") as f:
                self.assertEqual(f.read(), b"\xef\xbb\xbfid,v\n1,b\n")
            self.assertEqual(res.wrote[1], b"\xef\xbb\xbfid,v\n1,b\n")
        finally:
            shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    unittest.main(verbosity=1)
