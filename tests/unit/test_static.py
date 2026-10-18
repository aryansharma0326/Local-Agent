"""ST-01..ST-09: static and contract checks on the submission tree."""

import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers  # noqa: E402

ROOT = helpers.ROOT
AGENT = helpers.AGENT
STDLIB = set("""abc argparse ast base64 binascii bisect builtins calendar codecs collections concurrent contextlib copy
csv ctypes dataclasses datetime decimal difflib email enum errno fcntl fnmatch fractions functools gc getpass glob
gzip hashlib heapq hmac html http importlib inspect io ipaddress itertools json keyword locale logging math mimetypes
multiprocessing numbers operator os pathlib pickle platform pprint queue random re select shlex shutil signal socket
ssl stat statistics string struct subprocess sys tempfile textwrap threading time timeit traceback types typing
unicodedata unittest urllib uuid warnings weakref zipfile zlib""".split())


def files(ext, root=ROOT):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".git", "__pycache__", "dist", "reports", ".agent_state")]
        out += [os.path.join(dp, f) for f in fns if f.endswith(ext)]
    return out


def agent_modules():
    return [os.path.join(AGENT, f) for f in sorted(os.listdir(AGENT)) if f.endswith(".py")]


class Static(unittest.TestCase):
    def test_st01_entry_point(self):
        p = os.path.join(ROOT, "run_agent.sh")
        self.assertTrue(os.path.isfile(p))
        self.assertTrue(os.stat(p).st_mode & stat.S_IXUSR, "run_agent.sh is not executable")
        with open(p, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"#!/usr/bin/env bash\n"))
        for f in files(".sh") + files(".py"):
            with open(f, "rb") as fh:
                self.assertNotIn(b"\r", fh.read(), "CR found in %s" % f)

    def test_st02_bash_syntax_and_bash3(self):
        p = os.path.join(ROOT, "run_agent.sh")
        self.assertEqual(subprocess.call(["bash", "-n", p]), 0)
        if shutil.which("shellcheck"):
            self.assertEqual(subprocess.call(["shellcheck", "-S", "error", p]), 0)
        for f in files(".sh"):
            src = open(f).read()
            for bad in ("declare -A", ",,}", "^^}", "mapfile", "readarray", "readlink -f", "realpath"):
                self.assertNotIn(bad, src, "%s uses bash-4/GNU-only %r" % (f, bad))

    def test_st03_compiles_and_py38_syntax(self):
        for f in agent_modules():
            src = open(f, encoding="utf-8").read()
            compile(src, f, "exec")
            tree = ast.parse(src, f, feature_version=(3, 8))  # rejects 3.9+ grammar (match, parenthesized ctx...)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("removeprefix", "removesuffix", "is_relative_to"):
                    self.fail("%s uses 3.9+ API %s" % (f, node.attr))
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and isinstance(
                        getattr(node, "left", None), ast.Name) and node.left.id in ("str", "int", "list", "dict"):
                    self.fail("%s uses a runtime X | Y type union" % f)
                if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and \
                        node.value.id in ("list", "dict", "tuple", "set") and isinstance(
                            getattr(node, "ctx", None), ast.Load):
                    # builtin generics (list[int]) need 3.9 at runtime
                    self.fail("%s uses builtin generic %s[...]" % (f, node.value.id))
            for mod in ("zoneinfo", "graphlib", "tomllib"):
                self.assertNotIn("import " + mod, src)
        if shutil.which("python3.8"):
            for f in agent_modules():
                self.assertEqual(subprocess.call(["python3.8", "-m", "py_compile", f]), 0, f)

    def test_st04_stdlib_only(self):
        local = set(os.path.splitext(f)[0] for f in os.listdir(AGENT) if f.endswith(".py"))
        known = getattr(sys, "stdlib_module_names", None) or STDLIB
        for f in agent_modules():
            for node in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module.split(".")[0]]
                for n in names:
                    self.assertTrue(n in local or n in known, "%s imports non-stdlib %s" % (f, n))

    def test_st05_no_stdlib_shadowing(self):
        known = getattr(sys, "stdlib_module_names", None) or STDLIB
        for f in os.listdir(AGENT):
            if f.endswith(".py"):
                self.assertNotIn(f[:-3], known, "agent/%s shadows a stdlib module" % f)

    def test_st06_no_secrets(self):
        key = os.environ.get("QWEN_API_KEY", "").strip()
        kf = os.path.expanduser("~/.qwen_key")
        if not key and os.path.exists(kf):
            key = open(kf).read().strip()
        for f in files(".py") + files(".sh") + files(".md") + files(".json") + files(".txt"):
            src = open(f, encoding="utf-8", errors="replace").read()
            self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{10,}", src), f)
            self.assertIsNone(re.search(r"Bearer [A-Za-z0-9]{12,}", src), f)
            if key and len(key) > 8:
                self.assertNotIn(key, src, "the API key appears in %s" % f)

    def test_st07_no_agent_frameworks(self):
        pat = re.compile(r"^\s*(?:import|from)\s+(anthropic|claude_agent_sdk|langchain|llama_index|autogen|crewai|"
                         r"openai|smolagents)\b", re.M)
        for f in agent_modules():
            src = open(f, encoding="utf-8").read()
            self.assertIsNone(pat.search(src), f)
            self.assertIsNone(re.search(r"[\"'](claude|codex|aider)[\"']", src), "%s shells out to an agent" % f)

    def test_st08_no_hardcoded_task_solutions(self):
        for f in agent_modules():
            src = open(f, encoding="utf-8").read()
            for s in ("ORD-77341", "tally", "transactions.csv", "gw-", "hold-", "ReservationLedger"):
                self.assertFalse(s in src, "%s mentions task-specific %r" % (os.path.basename(f), s))

    def test_st09_readme_sections(self):
        text = open(os.path.join(ROOT, "readme.md"), encoding="utf-8").read().lower()
        for want in ("folder structure", "tested", "environment variables", "tests", "design decisions",
                     "session", "known limitations"):
            self.assertIn(want, text, "readme lacks a %r section" % want)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "requirements.txt")))


if __name__ == "__main__":
    unittest.main()
