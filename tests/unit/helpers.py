"""Shared setup for unit tests: import agent modules from the submission."""
import os
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENT = os.path.join(ROOT, "agent")
TESTS = os.path.join(ROOT, "tests")
for p in (AGENT, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)
