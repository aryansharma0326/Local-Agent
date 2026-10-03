"""Workspace sandbox: path validation and the only filesystem primitives the
agent is allowed to use. Every path coming from the model goes through here.
"""

import os
import stat
import tempfile
import unicodedata

from errors import AgentError

SUBMISSION_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# The agent's own code is never a valid target, even if the workspace is the
# submission directory itself.
PROTECTED = [os.path.join(SUBMISSION_DIR, "agent"), os.path.join(SUBMISSION_DIR, "run_agent.sh")]


RESERVED_DIR = ".agent_sessions"  # last-resort session store inside a workspace


def _inside(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives, mixed abs/rel
        return False


def display_name(name):
    """Make a filesystem name safe to show/serialize (undecodable bytes become U+FFFD)."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


class Sandbox:
    def __init__(self, root=None):
        self.root = os.path.realpath(root or os.getcwd())

    # ------------------------------------------------------------------ paths
    def normalize(self, raw):
        """Validate a model-supplied relative path and return it normalized
        ('a/b/c.txt'). Raises AgentError on anything suspicious."""
        if not isinstance(raw, str):
            raise AgentError("Invalid path: not a string.")
        p = raw.strip().strip("`'\"").strip()
        if not p:
            raise AgentError("Invalid path: empty.")
        if "\x00" in p:
            raise AgentError("Invalid path: contains a NUL byte.")
        p = p.replace("\\", "/")
        if (p.startswith("/") or p == "~" or p.startswith("~/")
                or (len(p) >= 2 and p[0].isalpha() and p[1] == ":" and (len(p) == 2 or p[2] == "/"))):
            raise AgentError("Refusing absolute path '%s': paths must be inside the workspace." % raw)
        parts = []
        for part in p.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise AgentError("Refusing path '%s': it escapes the workspace." % raw)
            parts.append(part)
        if not parts:
            raise AgentError("Invalid path '%s'." % raw)
        if parts[0] == RESERVED_DIR:
            raise AgentError("Refusing path '%s': it is reserved for the agent's own session data." % raw)
        return "/".join(parts)

    def _check_real(self, real, rel):
        if not _inside(real, self.root):
            raise AgentError("Refusing path '%s': it resolves outside the workspace." % rel)
        if _inside(real, SUBMISSION_DIR) and (not _inside(self.root, SUBMISSION_DIR)
                                              or any(_inside(real, p) for p in PROTECTED)):
            raise AgentError("Refusing path '%s': it points into the agent's own directory." % rel)

    def resolve(self, raw):
        """Return (relative_path, absolute_path) where absolute_path has its
        parent directory fully resolved (symlinks followed) and verified to be
        inside the workspace. The final component is NOT followed, so callers
        can inspect it with lstat."""
        rel = self.normalize(raw)
        head, name = os.path.split(rel)
        parent = os.path.realpath(os.path.join(self.root, head)) if head else self.root
        self._check_real(parent, rel)
        full = os.path.join(parent, name)
        self._check_real(full, rel)
        if not os.path.lexists(full):
            # Tolerate Unicode normalization differences (e.g. NFD vs NFC 'é').
            alt = self._normalization_match(parent, name)
            if alt is not None:
                full = os.path.join(parent, alt)
                rel = (head + "/" if head else "") + alt
        return rel, full

    @staticmethod
    def _normalization_match(parent, name):
        want = unicodedata.normalize("NFC", name)
        if want == name and name.isascii():
            return None
        try:
            entries = os.listdir(parent)
        except OSError:
            return None
        hits = [e for e in entries if unicodedata.normalize("NFC", e) == want]
        return hits[0] if len(hits) == 1 else None

    # ------------------------------------------------------------- operations
    def create_file(self, raw, content):
        rel, full = self.resolve(raw)
        # Create parent directories one level at a time, re-checking each.
        head = os.path.dirname(rel)
        cur = self.root
        if head:
            for part in head.split("/"):
                nxt = os.path.join(cur, part)
                if os.path.lexists(nxt):
                    real = os.path.realpath(nxt)
                    self._check_real(real, rel)
                    if not os.path.isdir(real):
                        raise AgentError("Cannot create %s: '%s' exists and is not a directory."
                                         % (rel, os.path.relpath(nxt, self.root)))
                    cur = real
                else:
                    os.mkdir(nxt)
                    cur = nxt
        full = os.path.join(cur, os.path.basename(rel))
        self._check_real(os.path.realpath(cur), rel)
        if os.path.islink(full):
            raise AgentError("Refusing to write %s: it is a symbolic link." % rel)
        if os.path.isdir(full):
            raise AgentError("Cannot create %s: a directory with that name exists." % rel)
        mode = None
        if os.path.lexists(full):
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode):
                raise AgentError("Cannot write %s: it is not a regular file." % rel)
            if not os.access(full, os.W_OK):
                raise AgentError("Permission denied: %s is read-only." % rel)
            mode = stat.S_IMODE(st.st_mode)
        else:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        # Write a new file and atomically move it into place: never modifies a
        # shared inode (hard links), never leaves a half-written file.
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=".agent-tmp-", dir=cur)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, mode)
            os.replace(tmp, full)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return rel

    def delete_file(self, raw):
        rel, full = self.resolve(raw)
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            raise AgentError("Could not find %s." % rel)
        if stat.S_ISDIR(st.st_mode):
            raise AgentError("Refusing to delete %s: it is a directory, not a file." % rel)
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            raise AgentError("Refusing to delete %s: it is not a regular file." % rel)
        os.unlink(full)  # unlinks a symlink itself, never its target
        return rel

    def read_file(self, raw):
        """Read a file as text. Returns (relative_path, text)."""
        rel, data = self.read_bytes(raw)
        return rel, decode_text(data, rel)

    def read_bytes(self, raw):
        """Read a file's exact bytes. Returns (relative_path, bytes)."""
        rel, full = self.resolve(raw)
        if not os.path.lexists(full):
            alt = self._unique_basename_match(os.path.basename(rel))
            if alt is None:
                raise AgentError("Could not find %s." % rel)
            rel, full = self.resolve(alt)
        real = os.path.realpath(full)
        self._check_real(real, rel)
        if os.path.isdir(real):
            raise AgentError("Cannot read %s: it is a directory, not a file." % rel)
        if not os.path.isfile(real):  # FIFOs, devices, sockets: never open (could block)
            raise AgentError("Cannot read %s: it is not a regular file." % rel)
        with open(real, "rb") as f:
            return rel, f.read()

    def exists_file(self, raw):
        try:
            _, full = self.resolve(raw)
        except AgentError:
            return False
        return os.path.isfile(full)

    # -------------------------------------------------------------- listing
    def listing(self, limit=200):
        """Relative paths of files in the workspace (hidden entries skipped)."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), self.root).replace(os.sep, "/")
                out.append(display_name(rel))
                if len(out) >= limit:
                    return out
        return out

    def files_named(self, name, limit=5000):
        """All workspace files whose basename equals `name` (NFC, case-sensitive)."""
        want = unicodedata.normalize("NFC", name)
        return [p for p in self.listing(limit=limit)
                if unicodedata.normalize("NFC", p.rsplit("/", 1)[-1]) == want]

    def _unique_basename_match(self, name):
        matches = self.files_named(name)
        return matches[0] if len(matches) == 1 else None


def decode_text(data, rel):
    """Decode file bytes: UTF-8 (BOM stripped), UTF-16 with BOM; refuse binary."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if b"\x00" in data[:8192]:
        raise AgentError("Cannot read %s: it appears to be a binary file, not text." % rel)
    return data.decode("utf-8", errors="replace")
