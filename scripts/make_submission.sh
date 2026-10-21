#!/usr/bin/env bash
# Build dist/hw1_submission.zip from an explicit include list, after the
# pre-package checks (PKG-2): LF-only *.sh/*.py, run_agent.sh executable,
# bash -n, every agent module compiles (without writing bytecode), secrets scan.
# Excluded: .git, __pycache__, .agent_state, .env*, venvs, dist/, tests/reports/, docs/.
# Bash 3.2 compatible.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT" || exit 1
fail() { echo "make_submission: $*" >&2; exit 1; }

# --- pre-package checks
for f in $(find . -path ./.git -prune -o \( -name '*.sh' -o -name '*.py' \) -print); do
    if LC_ALL=C grep -q "$(printf '\r')" "$f"; then fail "CR line ending in $f"; fi
done
[ -x run_agent.sh ] || fail "run_agent.sh is not executable"
bash -n run_agent.sh || fail "bash -n run_agent.sh failed"
if command -v shellcheck >/dev/null 2>&1; then shellcheck -S error run_agent.sh || fail "shellcheck failed"; fi
python3 -B - <<'EOF' || fail "an agent module does not compile"
import os, sys
for f in sorted(os.listdir("agent")):
    if f.endswith(".py"):
        compile(open(os.path.join("agent", f), encoding="utf-8").read(), f, "exec")
EOF
KEYVAL="${QWEN_API_KEY:-}"
if [ -z "$KEYVAL" ] && [ -f "$HOME/.qwen_key" ]; then KEYVAL="$(cat "$HOME/.qwen_key")"; fi
python3 -B - "$KEYVAL" <<'EOF' || fail "secrets scan failed"
import os, re, sys
key = sys.argv[1].strip()
bad = []
for dp, dns, fns in os.walk("."):
    dns[:] = [d for d in dns if d not in (".git", "dist", "__pycache__", ".agent_state", "reports")]
    for fn in fns:
        p = os.path.join(dp, fn)
        try:
            s = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if re.search(r"sk-[A-Za-z0-9]{20,}", s) or re.search(r"Bearer [A-Za-z0-9]{24,}", s) or (key and len(key) > 8 and key in s):
            bad.append(p)
if bad:
    print("possible secrets in: " + ", ".join(bad))
    sys.exit(1)
EOF

# --- build the archive (python zipfile: no dependency on the zip tool)
mkdir -p dist
rm -f dist/hw1_submission.zip
python3 -B - <<'EOF' || fail "zip failed"
import os, stat, zipfile
INCLUDE = ["run_agent.sh", "readme.md", "requirements.txt", "AUDIT.md", "PLAN.md", ".gitignore",
           "agent", "tests", "scripts"]
SKIP_DIRS = {".git", "__pycache__", ".agent_state", ".agent_sessions", "dist", "reports", "venv", ".venv", "env"}
def skip_file(name):
    return name.endswith(".pyc") or name == ".DS_Store" or name.startswith(".env")
with zipfile.ZipFile("dist/hw1_submission.zip", "w", zipfile.ZIP_DEFLATED) as z:
    def add(path):
        info = zipfile.ZipInfo.from_file(path, os.path.join("hw1_submission", path))
        mode = os.stat(path).st_mode
        info.external_attr = (stat.S_IMODE(mode) | stat.S_IFREG) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(path, "rb") as f:
            z.writestr(info, f.read())
    for item in INCLUDE:
        if os.path.isfile(item):
            add(item)
        elif os.path.isdir(item):
            for dp, dns, fns in os.walk(item):
                dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
                for fn in sorted(fns):
                    if not skip_file(fn):
                        add(os.path.join(dp, fn))
print("wrote dist/hw1_submission.zip (%d entries)" % len(zipfile.ZipFile("dist/hw1_submission.zip").namelist()))
EOF
