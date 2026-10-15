"""Long-horizon agent loop for CP3 tasks: programming and debugging.

Native tool calling over a pinned context:
  [system prompt + playbook] [task + harness pre-fetch] [rolling tool exchanges]
  + a PINNED STATE block (notes ledger, current deliverable / report values)
    appended to the newest message on every call.
Old tool outputs are compacted to one-line summaries and the oldest
exchanges dropped when the context budget (40,960 tokens) gets tight.

The harness, not the model, decides success:
  programming - the deliverable exists, compiles, and the best version (by
                the harness's smoke runs of the task's example commands) is
                what is left on disk;
  debugging   - the report file always exists with every key (placeholders
                first), and values are validated against the logs.
Wrap-up starts when time or model calls run low; it needs no model call.
"""

import json
import os
import re
import shutil
import tempfile

import config
import profiling
import prompts
import taskspec
from config import log
from deeptools import DeepTools, run_process, truncate
from errors import AgentError
from llm import BudgetExhausted, LLMError, estimate_tokens
from report import Report
from workspace import ChangeAudit

WRAP_SECONDS = 45.0
WRITE_TOOLS = ("write_file", "append_file", "edit_file")
_DROPPED = "(older steps were removed"


class DeepAgent:
    def __init__(self, prompt, llm, sandbox, kind, history=None, context=None, set_fallback=None):
        self.prompt = prompt
        self.llm = llm
        self.sb = sandbox
        self.root = sandbox.root
        self.kind = kind
        self.history = history or []
        self.set_fallback = set_fallback or (lambda s, m: None)
        self.scratch = os.path.realpath(tempfile.mkdtemp(prefix="hw1_scratch_"))
        self.audit = ChangeAudit(self.root)
        self.files_before = sorted(r for r, v in self.audit.before.items() if v[0] == "file")
        self.deliverables = self._deliverables()
        self.primary = self.deliverables[0] if self.deliverables else None
        protected = set(self.files_before) - set(self.deliverables)
        self.report = None
        if kind == "debugging":
            keys = taskspec.report_keys(prompt)
            self.report = Report(sandbox, self.primary, keys, taskspec.expected_key_count(prompt),
                                 self._evidence_files(), self._anchor_ids())
        self.tools = DeepTools(sandbox, self.scratch, kind, self.deliverables, protected, self.report)
        self.tools.on_write = self._after_write
        self.facts = {}
        self.smoke_cmds = []
        self.versions = []           # [(score, content, label)] of the primary deliverable
        self.last_smoke = None
        self.actions = []
        self.ex = []                 # messages after [system, task]
        self.sigs = {}
        self.last_error = None
        self.finish_rejects = 0
        self.no_tool_streak = 0
        self.force_wrap = False
        self.final_message = None

    # ---------------------------------------------------------------- setup
    def _deliverables(self):
        found = taskspec.deliverables(self.prompt)
        if self.kind == "debugging":
            js = [d for d in found if d.lower().endswith(".json")]
            return js[:1] or ["REPORT.json"]
        code = [d for d in found if re.search(r"\.(py|js|ts|sh|rb|go|java|c|cpp|rs|pl|php)$", d, re.I)]
        if not code and taskspec.signatures(self.prompt):
            code = ["solution.py"]
        return code or found[:1] or ["solution.py"]

    def _anchor_ids(self):
        """Identifiers the task names (e.g. an order id): the start of the chain."""
        toks = []
        for t in re.findall(r"`([^`\n]{3,60})`", self.prompt) + re.findall(r"\b[A-Z][A-Z0-9]{1,9}-\d{3,}\b", self.prompt):
            t = t.strip()
            if re.search(r"\d", t) and profiling.looks_like_id(t) and t not in toks and not re.search(
                    r"\.(json|log|py|csv|md|txt)$", t) and not re.fullmatch(r"[\d.:-]+", t):
                toks.append(t)
        return toks[:6]

    def _evidence_files(self):
        out = []
        for rel in self.files_before:
            if rel in self.deliverables or rel == "TASK.md":
                continue
            full = os.path.join(self.root, rel)
            if rel.lower().endswith((".log", ".jsonl", ".txt", ".json", ".out", ".err")) or "log" in rel.lower():
                out.append(full)
        return out

    def _listing(self):
        lines = []
        for rel in self.files_before[:150]:
            try:
                size = os.path.getsize(os.path.join(self.root, rel))
            except OSError:
                size = -1
            lines.append("  %s (%d bytes)" % (rel, size))
        if len(self.files_before) > 150:
            lines.append("  ... %d more" % (len(self.files_before) - 150))
        return "\n".join(lines) or "  (empty)"

    def _data_files(self):
        named = []
        for rel in self.files_before:
            base = rel.rsplit("/", 1)[-1]
            if rel.lower().endswith((".csv", ".tsv")) and (base in self.prompt or rel in self.prompt):
                named.append(rel)
        if not named:
            named = [r for r in self.files_before if r.lower().endswith((".csv", ".tsv"))][:2]
        return named[:3]

    def _first_programming(self):
        data = []
        for rel in self._data_files():
            facts = {}
            data.append(profiling.csv_profile(os.path.join(self.root, rel), rel, facts))
            self.facts[rel] = facts
        code = []
        for rel in self.files_before:
            if rel.lower().endswith((".py", ".js", ".sh")) and (rel.rsplit("/", 1)[-1] in self.prompt
                                                                or rel in self.deliverables):
                try:
                    with open(os.path.join(self.root, rel), "r", encoding="utf-8", errors="replace") as f:
                        src = f.read()
                except OSError:
                    continue
                code.append("EXISTING FILE %s:\n```\n%s\n```" % (rel, truncate(src, 12000)))
        self.smoke_cmds = self._smoke_commands()
        cmds = ""
        if self.smoke_cmds:
            cmds = ("HARNESS CHECKS: after every write/edit the harness compiles %s and runs:\n%s\n" % (
                self.primary, "\n".join("  $ " + c for c in self.smoke_cmds)))
        amap = self._arg_map()
        if amap:
            cmds = amap + "\n" + cmds
        return prompts.FIRST_PROGRAMMING.format(
            prompt=self.prompt, deliverables=", ".join(self.deliverables), scratch=self.scratch,
            checklist="\n".join("  " + c for c in taskspec.checklist(self.prompt)) or "  (see task)",
            listing=self._listing(),
            data=("DATA PROFILE (computed by the harness):\n" + "\n\n".join(data) + "\n") if data else "",
            commands=("\n".join(code) + "\n\n" if code else "") + cmds)

    def _all_examples(self):
        """Example invocations written in the task plus command signatures whose
        command word has no example yet."""
        ex = taskspec.example_commands(self.prompt)
        if self.primary and self.primary.endswith(".py"):
            have = set(c.split()[2] for c in ex if len(c.split()) >= 3)
            for c in taskspec.command_signatures(self.prompt, self.primary):
                if c.split()[2] not in have:
                    ex.append(c)
                    have.add(c.split()[2])
        return ex

    def _arg_map(self):
        """An explicit argument map derived from the task text: which --options
        take a value (and how to read them) and which arguments are positional."""
        opts = []
        for m in re.finditer(r"(?<![\w-])(--[a-z][\w-]*)(?:(?:[ =])(\[?[A-Z][A-Z0-9_]*\]?|YYYY-MM-DD|D\d|<[^>\s]+>))?",
                             self.prompt):
            name, ph = m.group(1), m.group(2)
            found = [o for o in opts if o[0] == name]
            if not found:
                opts.append([name, ph])
            elif ph and not found[0][1]:
                found[0][1] = ph
        if not opts:
            return ""
        values = [o for o in opts if o[1]]
        flags = [o[0] for o in opts if not o[1]]
        lines = ["ARGUMENT MAP (derived from the task text; follow it exactly):"]
        if values:
            lines.append("  options that take a value - read ONLY from opts: " + ", ".join(
                '%s %s -> opts.get("%s")' % (n, ph.strip("[]<>"), n) for n, ph in values))
        if flags:
            lines.append("  flags without a value - put exactly these in FLAGS: FLAGS = {%s}; read as opts.get(\"%s\") is True"
                         % (", ".join('"%s"' % f for f in flags), flags[0]))
        seen = []
        for c in self._all_examples():
            toks = c.split()
            if len(toks) < 3 or toks[2].startswith("-"):
                continue
            cmd, rest, pos, used, i = toks[2], toks[3:], [], [], 0
            while i < len(rest):
                t = rest[i]
                if t.startswith("--"):
                    used.append(t.split("=")[0])
                    i += 2 if (t.split("=")[0] in [v[0] for v in values] and "=" not in t) else 1
                    continue
                pos.append(t)
                i += 1
            desc = "  command %r: " % cmd
            parts = []
            if pos:
                parts.append("positionals " + ", ".join("%s = pos[%d]" % (p, k) for k, p in enumerate(pos)))
            if used:
                parts.append("options " + ", ".join('opts.get("%s")' % u for u in used))
            desc += "; ".join(parts) if parts else "no arguments"
            if desc not in seen:
                seen.append(desc)
        lines.extend(seen)
        lines.append("  Options never appear in pos; never compare pos items with \"--...\" strings.")
        return "\n".join(lines)

    def _first_debugging(self):
        stats = []
        for p in self.report.evidence[:8]:
            stats.append(profiling.log_stats(p, os.path.relpath(p, self.root)))
        anchors = []
        for t in self.report.anchors[:4]:
            rx = profiling.compile_pattern(t)
            text, n = profiling.grep_files(self.report.evidence, rx, self.root, 0, 25)
            anchors.append("$ grep %s  ->  %s" % (t, text))
            # one hop further: the request-scoped ids on those lines (all their lines show status/time)
            lines = [l for l in text.split("\n") if re.match(r"\S+:\d+: ", l)]
            hop = []
            for l in lines:
                for k, v in re.findall(r"\b(req|request_id|request|rid|req_id)=([\w.:-]+)", l):
                    if v not in hop:
                        hop.append(v)
            for v in hop[:6]:
                text2, _ = profiling.grep_files(self.report.evidence, profiling.compile_pattern(v), self.root, 0, 8)
                anchors.append("$ grep %s  ->  %s" % (v, text2))
        if len(anchors) > 2:
            anchors.append("(If the order has several requests, use the one whose status and time match the incident; "
                           "an earlier or retried attempt with another status is not the failed request.)")
        keys = "\n".join("  %s (%s)%s" % (k, self.report.types[k], (" - " + self.report.desc[k][:160])
                                         if self.report.desc.get(k) else "") for k in self.report.order)
        return prompts.FIRST_DEBUGGING.format(
            prompt=self.prompt, deliverable=self.primary, keys=keys, listing=self._listing(),
            stats=truncate("\n".join(stats), 5000), anchors=truncate("\n\n".join(anchors) or "(no identifiers)", 9000))

    def _system(self):
        minutes = max(1, int(config.remaining() // 60))
        if self.kind == "debugging":
            return prompts.DEBUGGING.format(minutes=minutes, deliverable=self.primary,
                                            keys=", ".join(self.report.order) or "(see task)")
        return prompts.PROGRAMMING.format(minutes=minutes, deliverables=", ".join(self.deliverables),
                                          main=self.primary)

    # --------------------------------------------------------- smoke tests
    def _smoke_commands(self):
        cmds = []
        for c in self._all_examples():
            filled = self._fill(c)
            if filled and filled not in cmds:
                cmds.append(filled)
        tests = [r for r in self.files_before if re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", r)]
        if not any(re.search(r"\btest", c) for c in cmds) and tests and self.primary and \
                self.primary.endswith(".py") and self.primary not in tests:
            cmds.append("python3 -m unittest -q " + " ".join(t[:-3].replace("/", ".") for t in tests[:3]))
        mod = self.primary[:-3] if self.primary and self.primary.endswith(".py") and "/" not in self.primary else None
        if mod and re.fullmatch(r"[A-Za-z_]\w*", mod):
            for name, _ in taskspec.signatures(self.prompt)[:2]:
                cmds.append("python3 -c \"import %s; print(callable(getattr(%s, '%s', None)))\"" % (mod, mod, name))
        return cmds[:12]

    def _probes(self):
        """Edge-case variants of the examples: unmatched filter value, large N,
        missing --file. Any traceback/timeout there is a bug whatever the spec says."""
        out = []
        for c in self._all_examples():
            toks = c.split()
            optidx = [i for i, t in enumerate(toks) if t.startswith("--") and i + 1 < len(toks)
                      and not toks[i + 1].startswith("--")]
            if len(optidx) >= 2:
                for i in optidx:
                    alt = self._fill(" ".join(toks[:i] + toks[i + 2:]))
                    if alt not in out and alt not in self.smoke_cmds:
                        out.append(alt)
            for i, t in enumerate(toks):
                core = t.strip("'\"")
                alt = None
                if re.fullmatch(r"(NAME|CATEGORY|TEXT|WORD|QUERY|VALUE|X|STR|TERM)", core):
                    alt = toks[:i] + ["zzz-no-such-value"] + toks[i + 1:]
                elif re.fullmatch(r"(N|K|NUM|COUNT|LIMIT)", core):
                    alt = toks[:i] + ["1000"] + toks[i + 1:]
                if alt:
                    filled = self._fill(" ".join(alt))
                    if filled not in out and filled not in self.smoke_cmds:
                        out.append(filled)
        if re.search(r"--file\b", self.prompt) and self.smoke_cmds:
            base = self.smoke_cmds[0].split()
            if len(base) >= 3:
                out.append(" ".join(base[:3] + ["--file", "no_such_file.csv"]))
        return out[:8]

    def _fill(self, cmd):
        """Replace placeholders in an example command with values from the data."""
        data_rel = next(iter(self.facts), None)
        facts = self.facts.get(data_rel, {}) if data_rel else {}
        dates = facts.get("dates") or []
        cats = facts.get("categories") or {}
        toks = cmd.split()
        out, date_i, prev = [], 0, ""
        for t in toks:
            core = t.strip("'\"")
            if re.fullmatch(r"(YYYY-MM-DD|D[12]|DATE\d?|START|END|FROM|TO)", core):
                if dates:
                    pick = dates[0] if date_i == 0 else dates[min(len(dates) - 1, max(1, len(dates) // 3))]
                else:
                    pick = "2026-01-01" if date_i == 0 else "2026-12-31"
                out.append(pick)
                date_i += 1
            elif re.fullmatch(r"(N|K|NUM|COUNT|LIMIT)", core):
                out.append("3")
            elif re.fullmatch(r"(PATH|FILE|CSV)", core):
                out.append(data_rel or core)
            elif re.fullmatch(r"(NAME|CATEGORY|TEXT|WORD|QUERY|VALUE|X|STR|TERM)", core):
                col = prev.lstrip("-").lower()
                vals = cats.get(col) or (list(cats.values())[0] if cats else [])
                out.append(vals[0].upper() if vals else "test")
            else:
                out.append(t)
            prev = core
        return " ".join(out)

    def _compile_error(self, rel):
        full = os.path.join(self.root, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError as e:
            return "cannot read %s: %s" % (rel, e)
        if not rel.endswith(".py"):
            return None
        try:
            compile(src, rel, "exec")
        except SyntaxError as e:
            return "SyntaxError in %s line %s: %s\n    %s" % (rel, e.lineno, e.msg, (e.text or "").rstrip())
        except (ValueError, TypeError) as e:
            return "cannot compile %s: %s" % (rel, e)
        return None

    def _permutations(self, cmd):
        """Generic CLI property checks when the task says options may appear
        anywhere: the same command with options moved/reordered must print the
        same output."""
        if not re.search(r"options? (?:may|can) (?:appear|be given|come) (?:anywhere|in any order)|any order",
                         self.prompt, re.I):
            return []
        toks = cmd.split()
        if len(toks) < 3:
            return []
        head, rest = toks[:3], toks[3:]   # python3 prog.py <command>
        opts, pos, i = [], [], 0
        while i < len(rest):
            if rest[i].startswith("--") and "=" not in rest[i] and i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                opts.append([rest[i], rest[i + 1]])
                i += 2
            else:
                (opts if rest[i].startswith("--") else pos).append([rest[i]] if rest[i].startswith("--") else rest[i])
                i += 1
        out = []
        if opts and pos:
            out.append(" ".join(head + [t for o in opts for t in o] + pos))
        if len(opts) >= 2:
            out.append(" ".join(head + pos + [t for o in reversed(opts) for t in o]))
        return [c for c in out if c != cmd]

    def _smoke(self):
        """Compile the primary deliverable and run the example commands plus
        generic consistency checks. Returns (score, report_text)."""
        rel = self.primary
        err = self._compile_error(rel)
        if err:
            return 0, "COMPILE FAILED: " + err
        score, lines = 10, ["compiles OK"]
        outputs = {}
        flags = []
        failed_examples = []
        for c in self.smoke_cmds:
            if config.remaining() < WRAP_SECONDS + 10:
                lines.append("(remaining checks skipped: low on time)")
                break
            code, out, errout, dt, timed_out = run_process(c, self.root, 20, self.tools.env)
            ok = (code == 0 and not timed_out and "Traceback" not in errout)
            score += 2 if ok else 0
            outputs[c] = (code, out)
            if not ok:
                failed_examples.append("`%s` %s" % (c, "crashed (traceback)" if "Traceback" in errout else
                                                    "timed out" if timed_out else "exited with status %s%s" % (
                                                        code, (": " + errout.strip().split("\n")[-1][:160])
                                                        if errout.strip() else "")))
            for alt in self._permutations(c):
                c2, o2, e2, _, _ = run_process(alt, self.root, 20, self.tools.env)
                if (c2, o2) != (code, out):
                    flags.append("`%s` printed %r (exit %s) but the same command with options in another position "
                                 "`%s` printed %r (exit %s); options may appear anywhere" % (
                                     c, out.strip()[:80], code, alt, o2.strip()[:80], c2))
            o = out.rstrip("\n").split("\n")
            shown = "\n".join(o[:12]) + ("\n...(%d more lines)" % (len(o) - 12) if len(o) > 12 else "")
            item = "$ %s\n  exit=%s %.2fs%s\n  stdout: %s" % (c, code, dt, " TIMEOUT" if timed_out else "",
                                                            shown.replace("\n", "\n          ") if out else "(empty)")
            if errout.strip():
                item += "\n  stderr: " + truncate(errout.strip(), 800).replace("\n", "\n          ")
            lines.append(item)
        for c in self._probes():
            if config.remaining() < WRAP_SECONDS + 10:
                break
            code, out, errout, dt, timed_out = run_process(c, self.root, 20, self.tools.env)
            if timed_out or "Traceback" in errout:
                flags.append("edge case `%s` %s:\n%s" % (c, "timed out" if timed_out else "crashed with a traceback",
                                                          truncate(errout.strip(), 600)))
            else:
                lines.append("$ %s   (edge probe)\n  exit=%s stdout: %s%s" % (
                    c, code, out.strip()[:120] or "(empty)", ("  stderr: " + errout.strip()[:160]) if errout.strip() else ""))
        # an option that does not change the output was probably ignored
        cmds = list(outputs)
        for a in cmds:
            for b in cmds:
                if a != b and b.startswith(a + " --") and outputs[a] == outputs[b] and outputs[a][0] == 0 \
                        and outputs[a][1].strip():
                    flags.append("`%s` and `%s` printed the same output; is the option ignored?" % (a, b))
        if flags:
            score -= len(flags)
        problems = (self._lint() + ["%s - but the task presents this invocation as valid" % f
                                    for f in failed_examples] + flags)
        score -= len(problems) - len(flags) - len(failed_examples)
        self.last_problems = problems
        diag = self._parse_diagnostics(failed_examples + flags)
        if problems:
            head = ("PROBLEMS TO FIX (most important first; fix them one at a time):\n" +
                    "\n".join("%d. %s" % (i + 1, p) for i, p in enumerate(problems)))
            if diag:
                head += "\n" + diag
        else:
            head = "No problems detected by the harness checks. Verify the outputs against every requirement."
        return score, head + "\n\nDETAILS:\n" + "\n".join(lines)

    def _lint(self):
        """Static checks for programs built on the playbook's argument-parsing
        skeleton (options keyed WITH dashes in opts; positionals in pos)."""
        if not self.primary or not self.primary.endswith(".py"):
            return []
        try:
            with open(os.path.join(self.root, self.primary), "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            return []
        out = []
        # every option and command the task names must at least appear in the program
        names = set(re.findall(r"(?<![\w-])(--[a-z][\w-]*)", self.prompt))
        missing_opts = sorted(n for n in names if not re.search(r"[\"'](?:--)?%s[\"'=]" % re.escape(n[2:]), src))
        cmds = []
        for c in self._all_examples():
            t = c.split()
            if len(t) >= 3 and not t[2].startswith("-") and t[2] not in cmds:
                cmds.append(t[2])
        missing_cmds = [c for c in cmds if not re.search(r"[\"']%s[\"']" % re.escape(c), src)]
        if missing_cmds:
            out.append("BUG (static check): the task's command(s) %s never appear in the program; implement them."
                       % ", ".join(missing_cmds))
        if missing_opts:
            out.append("BUG (static check): the task's option(s) %s are never read by the program (no \"%s\" string "
                       "in the code); implement them (read with opts.get(\"%s\"))." % (
                           ", ".join(missing_opts), missing_opts[0], missing_opts[0]))
        m = re.search(r"\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*=\s*[^\n]*parse_args\(|(\w+)\s*,\s*(\w+)\s*=\s*parse_args\(", src)
        if not m:
            return out
        opts_v, pos_v = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        bad = []
        for rx in (r"\b%s\s*\[[^\]\n]+\]\s*[!=]=\s*[\"']--[\w-]+[\"']" % pos_v,
                   r"[\"']--[\w-]+[\"']\s+(?:not\s+)?in\s+%s\b" % pos_v,
                   r"\b%s\.index\(\s*[\"']--" % pos_v):
            bad += [x.group(0) for x in re.finditer(rx, src)]
        if bad:
            out.append("BUG (static check): the code looks for option strings in `%s` (%s), but parse_args() puts "
                       "every --option into `%s` and only positional arguments into `%s`. Read options with "
                       "%s.get(\"--name\") instead." % (pos_v, "; ".join(sorted(set(bad))[:4]), opts_v, pos_v, opts_v))
        if re.search(r"name, eq, value = a\.partition|opts\[name\]", src):
            nodash = [x.group(0) for x in re.finditer(
                r"\b%s(?:\.get\(\s*|\[\s*)[\"'](?!--)[A-Za-z][\w-]*[\"']|[\"'](?!--)[A-Za-z][\w-]*[\"']\s+(?:not\s+)?in\s+%s\b"
                % (opts_v, opts_v), src)]
            if nodash:
                out.append("BUG (static check): parse_args() stores option keys WITH their dashes (e.g. \"--file\"), "
                           "but the code looks up %s without dashes. Use the dashed names." % "; ".join(
                               sorted(set(nodash))[:4]))
        return out

    def _parse_diagnostics(self, problems):
        """If the program has a parse_args(argv) function, show what it returns for
        the arguments of the commands with problems (a common root cause)."""
        if not problems or not self.primary or not self.primary.endswith(".py"):
            return ""
        try:
            with open(os.path.join(self.root, self.primary), "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            return ""
        m = re.search(r"^def (parse_args|parse_argv|parse_options|parse)\(", src, re.M)
        if not m:
            return ""
        out = []
        for p in problems[:3]:
            cm = re.search(r"`(python3? \S+\.py [^`]*)`", p)
            if not cm:
                continue
            toks = cm.group(1).split()[3:]   # after "python3 prog.py <command>"
            if not toks:
                continue
            code = ("import importlib.util as u, sys\n"
                    "s = u.spec_from_file_location('m', %r)\nm = u.module_from_spec(s)\ns.loader.exec_module(m)\n"
                    "print(repr(m.%s(%r)))" % (os.path.join(self.root, self.primary), m.group(1), toks))
            path = os.path.join(self.scratch, "diag.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            rc, so, se, _, _ = run_process(["python3", "-B", path], self.root, 10, self.tools.env, shell=False)
            res = so.strip() if rc == 0 else (se.strip().split("\n")[-1] if se.strip() else "exit %s" % rc)
            out.append("  %s(%r) -> %s" % (m.group(1), toks, res[:300]))
        if not out:
            return ""
        return ("DIAGNOSTIC: what your %s() returns for the arguments (after the command) of the failing "
                "commands - check that the command code reads the values from where they actually are:\n%s" % (
                    m.group(1), "\n".join(out)))

    def _after_write(self, rel):
        if self.kind != "programming" or rel != self.primary:
            return ""
        score, text = self._smoke()
        try:
            with open(os.path.join(self.root, rel), "r", encoding="utf-8", errors="replace", newline="") as f:
                content = f.read()
        except OSError:
            content = None
        revert = ""
        if content is not None:
            same = [v for v in self.versions[:-1] if v[1] == content]
            if same:
                self.reverts = getattr(self, "reverts", 0) + 1
                revert = ("\n(harness) This change makes the file IDENTICAL to an earlier version (%s), which had "
                          "the same check results. Undoing/redoing an edit does not help: find the real cause first "
                          "(read the CURRENT file, run the failing command with run_command, or try a fix in "
                          "$SCRATCH with run_python)." % same[0][2])
                if self.reverts >= 3:
                    revert += " You keep oscillating: rewrite the whole program cleanly with write_file."
            self.versions.append((score, content, "v%d" % (len(self.versions) + 1)))
            if score >= 10:
                self.set_fallback("success", "Wrote %s." % rel)
        self.last_smoke = (score, text)
        hist = getattr(self, "problem_history", [])
        hist.append(tuple(getattr(self, "last_problems", []) or []))
        self.problem_history = hist
        stuck = (len(hist) >= 3 and hist[-1] and hist[-1] == hist[-2] == hist[-3]) or (
            len(hist) >= 2 and any(p.startswith("BUG (static") for p in hist[-1])
            and any(p.startswith("BUG (static") for p in hist[-2]))
        if stuck and getattr(self, "restarts", 0) < 2 and config.remaining() > 110:
            self.restarts = getattr(self, "restarts", 0) + 1
            self.restart_pending = list(hist[-1])
            log("no progress: scheduling a fresh rewrite (#%d)" % self.restarts)
        elif len(hist) >= 3 and hist[-1] and hist[-1] == hist[-2] == hist[-3]:
            revert += ("\n(harness) Your last edits did not change any check result, so the cause is somewhere "
                       "else. Re-read the CURRENT file from the top (how arguments/options are parsed and looked up, "
                       "how rows are loaded) and fix the real cause; or run a small experiment with run_python.")
        return ("\n\nHARNESS CHECK of %s (version %d):\n%s\nCompare every output above with the requirements "
                "(exact format, values consistent with the data profile).%s" % (rel, len(self.versions), text, revert))

    def _restore_best(self):
        if not self.versions or not self.primary:
            return None
        best_i = max(range(len(self.versions)), key=lambda i: (self.versions[i][0], i))
        cur = self.versions[-1]
        full = os.path.join(self.root, self.primary)
        on_disk = None
        try:
            with open(full, "r", encoding="utf-8", errors="replace", newline="") as f:
                on_disk = f.read()
        except OSError:
            pass
        best = self.versions[best_i]
        if on_disk != cur[1] or best[0] > cur[0]:
            if on_disk is None or best[0] > cur[0] or self._compile_error(self.primary):
                self.sb.create_file(self.primary, best[1])
                log("restored best version %s of %s (score %d > %d)" % (best[2], self.primary, best[0], cur[0]))
                return best[2]
        return None

    # ---------------------------------------------------------------- loop
    def run(self):
        try:
            if self.kind == "debugging":
                self.report.write()
                self.set_fallback("success", "Wrote %s (partial findings)." % self.primary)
                first = self._first_debugging()
            else:
                first = self._first_programming()
            status, message = self._loop(first)
            return self._finalize(status, message)
        finally:
            self._cleanup()

    def on_abort(self):
        """Signal/watchdog path: leave the best deliverable on disk."""
        try:
            if self.kind == "programming":
                self._restore_best()
            elif self.report is not None:
                self.report.write()
        except Exception:
            pass

    def _cleanup(self):
        try:
            allowed = set(self.deliverables) | set(self.tools.created)
            self.audit.finalize(allowed, lambda r: r in self.tools.created or r in self.deliverables)
        except Exception as e:
            log("change audit failed: %s" % e)
        self.audit.cleanup()
        shutil.rmtree(self.scratch, ignore_errors=True)

    def _should_wrap(self):
        if self.force_wrap:
            return "repeated actions"
        if config.remaining() < WRAP_SECONDS:
            return "low on time"
        if config.CALL_CAP - self.llm.calls_used < config.WRAPUP_CALLS_LEFT:
            return "low on model calls"
        return None

    def _desired_tokens(self):
        if self.kind == "debugging":
            return 1024, 256
        written = os.path.exists(os.path.join(self.root, self.primary)) if self.primary else False
        want = 3000 if written and not getattr(self, "want_rewrite", False) else 8000
        by_time = self.llm.time_tokens(config.remaining() - WRAP_SECONDS - 10)
        return max(512, min(want, by_time)), 512

    def _loop(self, first):
        system = {"role": "system", "content": self._system()}
        task = {"role": "user", "content": first}
        while True:
            why = self._should_wrap()
            if why:
                log("wrap-up: %s" % why)
                return self._wrapup(why)
            if getattr(self, "restart_pending", None):
                probs = self.restart_pending
                self.restart_pending = None
                self.ex = [{"role": "user", "content": (
                    "FRESH START (earlier steps were cleared because the edits were not making progress). The "
                    "CURRENT %s in PINNED STATE still has these problems:\n%s\n\nRewrite the COMPLETE program now "
                    "with ONE write_file call: follow the ARGUMENT MAP exactly (options only via opts.get(\"--name\"), "
                    "positionals only via pos) and fix every problem listed. Do not use edit_file for this." % (
                        self.primary, "\n".join("- " + p for p in probs)))}]
                self.want_rewrite = True
            desired, minimum = self._desired_tokens()
            msgs = self._build([system, task], desired)
            self._dump(msgs)
            cap = min(240.0, config.remaining() - 15) if self.kind == "programming" else 90.0
            try:
                reply = self.llm.complete(msgs, tools=self.tools.schemas(), max_tokens=desired,
                                          timeout_cap=cap, min_tokens=minimum)
            except BudgetExhausted as e:
                log(str(e))
                return self._wrapup("model-call cap")
            except LLMError as e:
                if self._usable():
                    log("model error (%s); wrapping up with what exists" % e)
                    return self._wrapup("model error")
                raise
            done = self._handle(reply)
            if done:
                return done

    def _dump(self, msgs):
        """Development aid: AGENT_DEBUG_DUMP=<file outside the workspace> keeps the latest request."""
        path = os.environ.get("AGENT_DEBUG_DUMP")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(msgs, f, ensure_ascii=False, indent=1)
            except OSError:
                pass

    def _usable(self):
        if self.kind == "debugging":
            return self.report is not None and bool(self.report.set_keys)
        return bool(self.versions) and any(v[0] >= 10 for v in self.versions)

    def _handle(self, reply):
        calls = reply.tool_calls
        if any(c["name"] == "write_file" for c in calls):
            self.want_rewrite = False
        if calls:
            self.no_tool_streak = 0
            tcs = []
            for c in calls:
                if c["arguments"] is not None:
                    args_text = json.dumps(c["arguments"], ensure_ascii=False)
                else:
                    args_text = "{}"
                tcs.append({"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": args_text}})
            content = reply.content if reply.native else ""
            self.ex.append({"role": "assistant", "content": content[:2000] if content else "", "tool_calls": tcs})
            finished = None
            for c in calls:
                result = self._exec(c, reply.truncated)
                if result == "FINISH-ACCEPTED":
                    finished = ("success", self.final_message)
                    result = "accepted"
                self.ex.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            return finished
        # ------- no tool call: raw file fallback (LLM-15) or nudge (LOOP-5)
        content = reply.content or ""
        obs = None
        m = re.search(r"FILE:\s*`?([\w./$-]+)`?\s*\n```[\w+-]*\n(.*?)\n?```", content, re.S)
        if m:
            obs = self.tools.call("write_file", {"path": m.group(1), "content": m.group(2) + "\n"})
        elif self.kind == "programming" and not reply.truncated:
            blocks = re.findall(r"```(?:python|py|python3)?\s*\n(.*?)\n?```", content, re.S)
            big = [b for b in blocks if b.count("\n") >= 8]
            if len(big) == 1 and self.primary:
                obs = ("(harness) Your reply contained a program but no tool call; it was written to %s.\n" %
                       self.primary) + self.tools.call("write_file", {"path": self.primary, "content": big[0] + "\n"})
        if obs is None:
            self.no_tool_streak += 1
            if reply.truncated and "```" in content:
                obs = ("Your reply was cut off by the output limit. Write the file in parts: write_file with the "
                       "first part, then append_file with the rest.")
            elif self.no_tool_streak >= 3:
                self.force_wrap = True
                obs = "No tool call again."
            else:
                obs = ("You replied without calling a tool. Act now: call the next tool (or finish if the "
                       "deliverable is complete).")
        shown = content if len(content) <= 1500 else content[:600] + "\n...[long text omitted]..."
        self.ex.append({"role": "assistant", "content": shown or "(empty reply)"})
        self.ex.append({"role": "user", "content": obs})
        return None

    def _exec(self, c, truncated):
        name, args = c["name"], c["arguments"]
        if c.get("error") or args is None:
            hint = (" Your reply was cut off by the output limit; resend it more compactly (write long files in "
                    "parts with write_file + append_file).") if truncated else ""
            return "ERROR: could not parse the arguments of %s (%s). The call was not executed.%s" % (
                name, c.get("error") or "missing", hint)
        if truncated and name in WRITE_TOOLS + ("report_set",):
            return ("ERROR: your reply was cut off by the output limit, so this %s call was NOT executed. Write the "
                    "file in parts: write_file with the first part, then append_file with the rest - or reply "
                    "with a line 'FILE: <path>' followed by one fenced code block." % name)
        if name == "finish":
            return self._finish(args)
        sig = name + json.dumps(args, sort_keys=True, ensure_ascii=False)[:2000]
        self.sigs[sig] = self.sigs.get(sig, 0) + 1
        result = self.tools.call(name, args)
        self.actions.append({"call": "%s %s" % (name, json.dumps(
            {k: (v if not isinstance(v, str) or len(v) < 120 else v[:117] + "...") for k, v in args.items()},
            ensure_ascii=False)[:400]), "result": result[:300], "tool": name})
        if self.sigs[sig] >= 8:
            self.force_wrap = True
        elif self.sigs[sig] >= 2:
            if self.report is not None:
                unset = [k for k in self.report.order if k not in self.report.set_keys]
                result += ("\n(harness) You already made this exact call %d times; the result will not change. Use "
                           "a different pattern (e.g. regex=true with 'ID.*(ERROR|WARN|Caused by)'), read_lines around "
                           "a hit, or set the keys you have confirmed. Keys still unset: %s." % (
                               self.sigs[sig], ", ".join(unset) or "none - call finish"))
            else:
                result += ("\n(harness) You already made this exact call %d times; the result will not change. "
                           "Look at the CURRENT file in PINNED STATE and copy 'old' exactly from it, or fix a "
                           "different problem, or rewrite the file with write_file." % self.sigs[sig])
        if result.startswith("ERROR") and result == self.last_error:
            result += "\n(harness) Same error as last time: change your approach."
        self.last_error = result if result.startswith("ERROR") else None
        return result

    def _finish(self, args):
        msg = str((args or {}).get("message") or "").strip()
        problems = self._deliverable_problems(strict=True)
        unset = self.kind == "debugging" and any(k not in self.report.set_keys for k in self.report.order)
        max_rejects = 4 if unset else 2
        if problems and self.finish_rejects < max_rejects and config.remaining() > WRAP_SECONDS + 20:
            self.finish_rejects += 1
            hint = ""
            if unset:
                hint = ("\nKeep investigating the unset keys (do not give up): grep the identifiers you already "
                        "confirmed together with ERROR/WARN, read_lines around stack traces, count with run_python."
                        + self.report._linked_errors())
            return "ERROR: not finished yet:\n- " + "\n- ".join(problems) + hint
        self.final_message = msg or None
        return "FINISH-ACCEPTED"

    def _deliverable_problems(self, strict):
        out = []
        if self.kind == "debugging":
            return self.report.problems() if strict else []
        for rel in self.deliverables:
            full = os.path.join(self.root, rel)
            if not os.path.isfile(full) or os.path.getsize(full) == 0:
                out.append("%s does not exist yet - write it with write_file" % rel)
                continue
            err = self._compile_error(rel)
            if err:
                out.append(err)
        if not out and strict and getattr(self, "last_problems", None) and self.finish_rejects == 0:
            out.append("the harness checks still report problems; fix them, or call finish again if they are "
                       "expected by the task:\n" + truncate("\n".join(self.last_problems), 1500))
        return out

    # -------------------------------------------------------- context build
    def _pinned(self):
        parts = []
        if self.tools.notes:
            parts.append("NOTES (your findings so far):\n" + "\n".join("- " + n for n in self.tools.notes[-20:]))
        if self.kind == "debugging":
            probs = self.report.problems()
            parts.append("REPORT %s now: %s\nunresolved: %s" % (self.primary, self.report.summary(),
                                                                "; ".join(probs) if probs else "none"))
        elif self.primary:
            full = os.path.join(self.root, self.primary)
            if os.path.isfile(full):
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
                if len(src) <= 24000:
                    parts.append("CURRENT %s (%d lines; this is the exact file on disk):\n```\n%s\n```" % (
                        self.primary, src.count("\n") + 1, src))
                else:
                    outline = [l for l in src.split("\n") if re.match(r"\s*(def|class)\s", l)]
                    parts.append("CURRENT %s is %d lines; outline:\n%s" % (self.primary, src.count("\n") + 1,
                                                                          "\n".join(outline[:80])))
        left = "time left %.0fs, model calls left %d" % (config.remaining(), config.CALL_CAP - self.llm.calls_used)
        parts.append(left)
        return "\n\n[PINNED STATE]\n" + "\n\n".join(parts)

    def _build(self, base, desired):
        ex = [dict(m) for m in self.ex]
        pinned_file = self.kind == "programming"
        # Old write contents are superseded by the pinned current file.
        for m in ex[:-2] if pinned_file else []:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                new_tcs = []
                for tc in m["tool_calls"]:
                    fn = tc["function"]
                    if fn["name"] in ("write_file", "append_file") and len(fn["arguments"]) > 400:
                        try:
                            a = json.loads(fn["arguments"])
                            a["content"] = "<omitted: %d chars; see CURRENT file in PINNED STATE>" % len(
                                a.get("content") or "")
                            fn = dict(fn, arguments=json.dumps(a, ensure_ascii=False))
                        except ValueError:
                            fn = dict(fn, arguments='{"content": "<omitted>"}')
                    new_tcs.append(dict(tc, function=fn))
                m["tool_calls"] = new_tcs
        pinned = self._pinned()
        tools = self.tools.schemas()
        budget = config.CONTEXT_TOKENS - config.CONTEXT_SAFETY - max(desired, 800) - (len(pinned) + 2) // 3
        msgs = base + ex
        guard = 0
        while estimate_tokens(msgs, tools) > budget and guard < 400:
            guard += 1
            changed = False
            for i, m in enumerate(ex[:-3]):
                c = m.get("content") or ""
                if m.get("role") in ("tool", "user") and len(c) > 400 and not c.endswith("[compacted]"):
                    first = c.split("\n", 1)[0][:200]
                    m["content"] = first + " ...[older output compacted]"
                    changed = True
                    break
            if not changed:
                # drop the oldest exchange (an assistant message and its results)
                start = 1 if ex and (ex[0].get("content") or "").startswith(_DROPPED) else 0
                if len(ex) - start <= 3:
                    break
                j = start + 1
                while j < len(ex) and ex[j].get("role") in ("tool", "user"):
                    j += 1
                del ex[start:j]
                if start == 0:
                    ex.insert(0, {"role": "user", "content": _DROPPED + " to fit the context window; see the "
                                                                        "notes in PINNED STATE)"})
            msgs = base + ex
        msgs = [dict(m) for m in msgs]
        last = msgs[-1]
        last["content"] = (last.get("content") or "") + pinned
        return msgs

    # ---------------------------------------------------------- finishing
    def _wrapup(self, why):
        if self.kind == "programming":
            self._restore_best()
        else:
            self.report.write()
        msg = self.final_message
        return ("success" if not self._deliverable_problems(strict=False) else "error",
                msg or "Stopped early (%s)." % why)

    def _finalize(self, status, message):
        if self.kind == "programming":
            restored = self._restore_best()
            problems = self._deliverable_problems(strict=False)
            if problems:
                return "error", "; ".join(problems)[:500]
            base = message or "Wrote %s." % ", ".join(self.deliverables)
            extra = ""
            if self.last_smoke and self.smoke_cmds:
                extra = " (harness checks: %d/%d example commands ran cleanly)" % (
                    self.last_smoke[1].count("\n  exit=0 "), len(self.smoke_cmds))
            if restored:
                extra += " (restored best version %s)" % restored
            return "success", base + extra
        self.report.write()
        if not self.report.file_ok():
            return "error", "Could not write a valid %s." % self.primary
        probs = self.report.problems()
        base = message or "Wrote %s." % self.primary
        if probs:
            base += " Unresolved: " + "; ".join(probs)[:400]
        return "success", base
