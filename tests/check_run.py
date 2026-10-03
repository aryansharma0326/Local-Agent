"""Run the agent and show what ACTUALLY changed on disk, next to what it claimed.

Run it from the task workspace (the directory the agent should work in), with
OPENAI_BASE_URL, MODEL and QWEN_API_KEY set as for run_agent.sh:

    cd /path/to/workspace
    python3 /path/to/submission/tests/check_run.py "Delete notes/old-draft.txt."

It snapshots every file (SHA-256) before and after the run and prints the
difference, so you never have to take the agent's JSON on trust.
Exit code: 0 if the agent's exit code matches its JSON status, else 1.
"""

import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

import verify  # noqa: E402


def preview(path, limit=300):
    try:
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except OSError as e:
        return "<unreadable: %s>" % e
    text = data[:limit].decode("utf-8", "replace")
    return repr(text) + (" ..." if len(data) > limit else "")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    workspace = os.path.realpath(os.getcwd())
    if os.path.commonpath([workspace, ROOT]) == ROOT:
        sys.exit("Run this from a task workspace, not from inside the submission directory.")
    before, _ = verify.snapshot(workspace)
    proc = subprocess.run(["bash", os.path.join(ROOT, "run_agent.sh")] + sys.argv[1:],
                          stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    after, _ = verify.snapshot(workspace)
    out = proc.stdout.decode("utf-8", "replace").strip()

    print("\n=== What the agent CLAIMED")
    print("stdout   :", out)
    print("exit code:", proc.returncode)
    try:
        status = json.loads(out).get("status")
    except ValueError:
        status = None
        print("!! stdout is not a single JSON object")

    created = sorted(k for k in after if k not in before)
    deleted = sorted(k for k in before if k not in after)
    modified = sorted(k for k in after if k in before and after[k] != before[k])
    print("\n=== What ACTUALLY changed on disk (%s)" % workspace)
    for label, items in (("CREATED ", created), ("DELETED ", deleted), ("MODIFIED", modified)):
        for rel in items:
            line = "%s %s" % (label, rel + ("/" if (after.get(rel) or before.get(rel)) == "dir" else ""))
            if label != "DELETED " and after[rel].startswith("sha256:"):
                full = os.path.join(workspace, rel)
                line += "  (%d bytes) content: %s" % (os.path.getsize(full), preview(full))
            print(line)
    if not (created or deleted or modified):
        print("nothing changed")
    print("unchanged entries: %d" % sum(1 for k in before if after.get(k) == before[k]))

    consistent = (proc.returncode == 0) == (status == "success")
    if not consistent:
        print("\n!! exit code and JSON status disagree")
    return 0 if consistent else 1


if __name__ == "__main__":
    sys.exit(main())
