"""Workspace change audit (LOOP-13 / SAFE-5).

At start: snapshot every entry (type, size, mtime, sha256) and back up the
bytes of regular files (up to a total cap) into a temp dir OUTSIDE the
workspace. At the end: any pre-existing file that was modified or deleted
but is not an allowed target is restored from the backup; new files that
are neither allowed targets nor created deliberately by the agent (scratch
output of commands, __pycache__) are removed. Every restore is logged.
"""

import hashlib
import os
import shutil
import stat
import tempfile

from config import log

BACKUP_CAP = 50 * 1024 * 1024
MAX_ENTRIES = 20000
SKIP_DIRS = (".agent_sessions", ".git")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root):
    """{relpath: (kind, size, sha_or_target)} for files, links and dirs."""
    out = {}
    for dp, dns, fns in os.walk(root):
        if dp == root:
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for name in dns + fns:
            full = os.path.join(dp, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                try:
                    out[rel] = ("link", 0, os.readlink(full))
                except OSError:
                    out[rel] = ("link", 0, "?")
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", 0, "")
            elif stat.S_ISREG(st.st_mode):
                try:
                    out[rel] = ("file", st.st_size, _sha(full))
                except OSError:
                    out[rel] = ("file", st.st_size, "unreadable")
            else:
                out[rel] = ("special", 0, "")
            if len(out) >= MAX_ENTRIES:
                return out
    return out


class ChangeAudit:
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.before = snapshot(self.root)
        self.backup_dir = tempfile.mkdtemp(prefix="hw1_backup_")
        self.backed_up = set()
        total = 0
        for rel, (kind, size, _) in sorted(self.before.items(), key=lambda kv: kv[1][1]):
            if kind != "file" or total + size > BACKUP_CAP:
                continue
            dst = os.path.join(self.backup_dir, rel)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(self.root, rel), dst)
                self.backed_up.add(rel)
                total += size
            except OSError:
                pass

    def changed(self):
        """(modified_or_deleted, created) relative paths since the start."""
        after = snapshot(self.root)
        mod = [r for r, v in self.before.items() if v[0] != "dir" and after.get(r) != v]
        new = [r for r in after if r not in self.before]
        return mod, new, after

    def finalize(self, allowed, keep_new):
        """Restore unexpected modifications/deletions and remove unexpected new
        entries. `allowed`: paths that may change. `keep_new(rel)`: True for a new
        file the agent created on purpose. Returns a list of actions taken."""
        actions = []
        mod, new, after = self.changed()
        for rel in mod:
            if rel in allowed:
                continue
            if rel in self.backed_up:
                dst = os.path.join(self.root, rel)
                try:
                    if os.path.lexists(dst) and (os.path.isdir(dst) and not os.path.islink(dst)):
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.lexists(dst):
                        os.unlink(dst)
                    shutil.copy2(os.path.join(self.backup_dir, rel), dst)
                    actions.append("restored %s" % rel)
                except OSError as e:
                    log("could not restore %s: %s" % (rel, e))
            else:
                log("WARNING: %s changed and has no backup" % rel)
        # new entries: deepest first so emptied directories can go too
        for rel in sorted(new, key=lambda r: -r.count("/")):
            full = os.path.join(self.root, rel)
            kind = after[rel][0]
            name = rel.rsplit("/", 1)[-1]
            if kind == "dir":
                if name == "__pycache__" or not any(r.startswith(rel + "/") and keep_new(r) for r in new):
                    if name == "__pycache__" or (rel not in allowed and not keep_new(rel)):
                        try:
                            if name == "__pycache__":
                                shutil.rmtree(full)
                                actions.append("removed %s/" % rel)
                            elif not os.listdir(full):
                                os.rmdir(full)
                                actions.append("removed empty %s/" % rel)
                        except OSError:
                            pass
                continue
            if "/__pycache__/" in "/" + rel or name.endswith(".pyc"):
                try:
                    os.unlink(full)
                except OSError:
                    pass
                continue
            if rel in allowed or keep_new(rel):
                continue
            try:
                os.unlink(full)
                actions.append("removed stray %s" % rel)
            except OSError:
                pass
        for a in actions:
            log("change audit: %s" % a)
        return actions

    def cleanup(self):
        shutil.rmtree(self.backup_dir, ignore_errors=True)
