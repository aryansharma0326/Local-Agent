"""Independent verification of the agent's work.

The harness never takes a tool's "OK" (or the model's "success") on trust.
A snapshot of the workspace is taken before the task starts; afterwards the
disk is inspected directly to confirm that the intended change really
happened and that nothing else changed.
"""

import hashlib
import os
import stat
import unicodedata

IGNORED_DIR = ".agent_sessions"
MAX_ENTRIES = 20000            # stop snapshotting absurdly large workspaces
MAX_HASH_BYTES = 8 * 1024 * 1024  # larger files are compared by size + mtime


def _nfc(s):
    return unicodedata.normalize("NFC", s)


def _fingerprint(path):
    """A value that changes if the entry's type or content changes."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode):
        try:
            return "link:" + os.readlink(path)
        except OSError:
            return "link:?"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if not stat.S_ISREG(st.st_mode):
        return "special"  # FIFO, socket, device: never opened
    if st.st_size > MAX_HASH_BYTES:
        return "big:%d:%d" % (st.st_size, st.st_mtime_ns)
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError:
        return "unreadable:%d:%d" % (st.st_size, st.st_mtime_ns)


def snapshot(root):
    """Map of NFC-normalized relative path -> fingerprint for every entry
    (files, directories, symlinks, hidden entries) under root. Returns
    (entries, complete)."""
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath == root and IGNORED_DIR in dirnames:
            dirnames.remove(IGNORED_DIR)  # the agent's own last-resort session store
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = _nfc(os.path.relpath(full, root).replace(os.sep, "/"))
            entries[rel] = _fingerprint(full)
            if len(entries) >= MAX_ENTRIES:
                return entries, False
    return entries, True


def check_write(full, rel, content):
    """Read the file back from disk and compare it byte-for-byte."""
    try:
        st = os.lstat(full)
    except OSError:
        return "%s does not exist after writing" % rel
    if not stat.S_ISREG(st.st_mode):
        return "%s is not a regular file after writing" % rel
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError as e:
        return "%s could not be read back (%s)" % (rel, e.strerror or type(e).__name__)
    want = content if isinstance(content, bytes) else content.encode("utf-8")
    if data != want:
        return "%s does not contain the intended content (%d bytes on disk, %d expected)" % (
            rel, len(data), len(want))
    return None


def check_delete(full, rel):
    """Confirm the path is really gone."""
    if os.path.lexists(full):
        return "%s still exists after deleting" % rel
    return None


def check_workspace(root, before, complete, changed_rel=None, expect=None):
    """Compare the workspace with the starting snapshot. Only `changed_rel`
    may differ: it must be present afterwards (expect="present", a write) or
    absent (expect="absent", a delete). A written file's newly created parent
    directories are allowed. Returns a list of problems (empty = verified)."""
    after, _ = snapshot(root)
    target = _nfc(changed_rel) if changed_rel else None
    allowed_new = set()
    if target and expect == "present":
        parts = target.split("/")
        allowed_new = set("/".join(parts[:i]) for i in range(1, len(parts)))
        if target not in after:
            return ["%s is missing from the workspace" % target]
    if target and expect == "absent" and target in after:
        return ["%s is still present in the workspace" % target]
    problems = []
    for rel, fp in before.items():
        if rel == target:
            continue
        if rel not in after:
            problems.append("%s was removed" % rel)
        elif after[rel] != fp:
            problems.append("%s was modified" % rel)
    if complete:
        for rel in after:
            if rel not in before and rel != target and rel not in allowed_new:
                problems.append("%s was created unexpectedly" % rel)
    return problems
