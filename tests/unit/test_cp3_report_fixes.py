"""Regressions from the hw1-3 grading report: requirement 9 (formatted amounts), requirement 11
(exit status of a missing file) and the backslash loop that left tally.py not compiling."""

import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402,F401

import deep  # noqa: E402
import deeptools  # noqa: E402
import taskspec  # noqa: E402
from sandbox import Sandbox  # noqa: E402

TASK = """# Task: implement `tally.py`

```
python3 tally.py <command> [arguments] [--file PATH] [--json]
```

## Requirements

1. **`count`** - `python3 tally.py count` prints the number of valid transactions, e.g. `39876`.
2. **`total`** - `python3 tally.py total` prints the sum of all amounts, e.g. `1834512.07`.
9. **formatted amounts** - an amount may contain thousands separators and a leading dollar sign,
   e.g. `"1,234.50"` and `"$1,000"`.
11. **errors** - an unknown command prints `error: unknown command: NAME` to stderr and exits with
    status 1; a missing file prints `error: file not found: PATH` to stderr and exits with status 2.
"""

class Parsing(unittest.TestCase):
    def test_full_requirement_text_is_kept(self):
        item = next(i for i in taskspec.checklist(TASK) if i.startswith("9."))
        self.assertIn('"1,234.50"', item)
        self.assertIn('"$1,000"', item)

    def test_error_contracts(self):
        rules = taskspec.error_contracts(TASK)
        self.assertEqual(rules["missing_file"], {"status": 2, "message": "error: file not found: PATH"})
        self.assertEqual(rules["unknown_command"], {"status": 1, "message": "error: unknown command: NAME"})

    def test_error_contract_wordings(self):
        for text in ("Exit with status 2 if the file does not exist.",
                     "If the CSV file does not exist, print an error to stderr and exit with status 2.",
                     "A missing file exits with code 2."):
            self.assertEqual(taskspec.error_contracts(text)["missing_file"]["status"], 2, text)
        self.assertEqual(taskspec.error_contracts("Nothing about errors."), {})

    def test_message_for_a_template(self):
        self.assertEqual(taskspec.error_message_for("error: file not found: PATH", "x.csv"),
                         ("error: file not found: x.csv", True))
        self.assertEqual(taskspec.error_message_for("error: file not found: <path>", "x.csv"),
                         ("error: file not found: x.csv", True))
        self.assertEqual(taskspec.error_message_for("error: file not found: a/b.csv", "x.csv"),
                         ("error: file not found", False))

    def test_input_examples(self):
        self.assertEqual(taskspec.input_value_examples(TASK), ["1,234.50", "$1,000"])
        self.assertEqual(taskspec.plain_amount("$1,234.50"), "1234.50")
        self.assertEqual(taskspec.plain_amount("-$3.50"), "-3.50")
        for text in ("All money values are printed with no thousands separator, e.g. `1234.50`.",
                     "A row with the amount `$4.00` is invalid.", "Amounts like `1,234.00` are rejected."):
            self.assertEqual(taskspec.input_value_examples(text), [], text)


class EditBackslashes(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="edit_ws_")
        self.scratch = tempfile.mkdtemp(prefix="edit_sc_")
        self.tools = deeptools.DeepTools(Sandbox(self.ws), self.scratch, "programming", ["p.py"], set())

    def tearDown(self):
        shutil.rmtree(self.ws)
        shutil.rmtree(self.scratch)

    def read(self):
        with open(os.path.join(self.ws, "p.py")) as f:
            return f.read()

    def test_doubled_backslashes_in_old_match_the_file(self):  # 10 of 20 runs looped on this
        with open(os.path.join(self.ws, "p.py"), "w") as f:
            f.write('x = s.replace("\\", "")\n')
        out = self.tools.call("edit_file", {"path": "p.py", "old": '.replace("\\\\", "")', "new": '.replace(",", "")'})
        self.assertTrue(out.startswith("OK"), out)
        self.assertEqual(self.read(), 'x = s.replace(",", "")\n')

    def test_halved_backslashes_in_old_match_the_file(self):
        with open(os.path.join(self.ws, "p.py"), "w") as f:
            f.write('x = s.replace("\\\\", "")\n')
        out = self.tools.call("edit_file", {"path": "p.py", "old": '.replace("\\", "")', "new": '.replace(",", "")'})
        self.assertTrue(out.startswith("OK"), out)

    def test_exact_match_still_wins_and_a_miss_is_still_an_error(self):
        with open(os.path.join(self.ws, "p.py"), "w") as f:
            f.write('a = "\\\\"\nb = "\\\\\\\\"\n')
        out = self.tools.call("edit_file", {"path": "p.py", "old": 'b = "\\\\\\\\"', "new": "b = 1"})
        self.assertTrue(out.startswith("OK"), out)
        self.assertIn('a = "\\\\"', self.read())
        self.assertIn("not found", self.tools.call("edit_file", {"path": "p.py", "old": 'zz\\\\', "new": "y"}))


class Literal(unittest.TestCase):
    def test_pattern_only_touches_a_lone_backslash_string(self):
        sub = lambda s: deep._LONE_BACKSLASH.sub(lambda m: m.group(1) + "\\\\" + m.group(1), s)  # noqa: E731
        self.assertEqual(sub('.replace("$", "").replace("\\", "")'), '.replace("$", "").replace("\\\\", "")')
        self.assertEqual(sub("x.split('\\', 1)"), "x.split('\\\\', 1)")
        self.assertEqual(sub('s = "a\\"b"'), 's = "a\\"b"')


if __name__ == "__main__":
    unittest.main()
