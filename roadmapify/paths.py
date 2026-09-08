"""Single source of truth for the roadmapify output directory, atomic writes, and the derive lock.

The output directory is ``roadmap-out`` by default and overridable with the
``ROADMAP_OUT`` env var (worktrees or shared-output setups). It accepts a
relative name (``"roadmap-out-feature"``) or an absolute path
(``"/shared/roadmap-out"``). The value is read once at import time, matching
graphify's ``GRAPHIFY_OUT`` — set it before the process starts and every reader
honours it.

Ported from graphify/paths.py. graphify learned the hard way (#1423) that
duplicating this constant across modules leaves some guards silently ignoring
the override, so it lives here and nowhere else.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROADMAP_OUT = os.environ.get("ROADMAP_OUT", "roadmap-out")
ROADMAP_OUT_NAME = os.path.basename(os.path.normpath(ROADMAP_OUT))

PLAN_FILENAME = "roadmap.toml"
JOURNAL_FILENAME = "journal.jsonl"
EVIDENCE_FILENAME = "evidence.jsonl"
GRAPH_FILENAME = "graph.json"
BRIEF_FILENAME = "BRIEF.md"
ROADMAP_MD_FILENAME = "ROADMAP.md"
MODERATION_FILENAME = "MODERATION.md"
STATE_FILENAME = ".roadmap_state.json"
LOCK_FILENAME = ".roadmap.lock"
NEEDS_BUILD_FILENAME = ".needs_build"
VERSION_FILENAME = ".roadmap_version"


# ── project root ──────────────────────────────────────────────────────────────

def project_root(start: "str | Path | None" = None) -> Path:
    """Locate the project root: the nearest ancestor holding roadmap.toml.

    Falls back to the nearest ancestor holding ``.git``, then to ``start``
    itself. Never raises and never touches git — `roadmap init` has to work in a
    directory that is not a repo at all, and every read command has to work from
    a subdirectory.
    """
    here = Path(start or Path.cwd()).resolve()
    candidates = [here, *here.parents]
    for d in candidates:
        if (d / PLAN_FILENAME).is_file():
            return d
    for d in candidates:
        if (d / ".git").exists():
            return d
    return here


def out_dir(root: "str | Path | None" = None) -> Path:
    """Return the output directory for ``root`` (honouring an absolute ROADMAP_OUT)."""
    if os.path.isabs(ROADMAP_OUT):
        return Path(ROADMAP_OUT)
    return Path(root or project_root()) / ROADMAP_OUT


def out_path(*parts: str, root: "str | Path | None" = None) -> Path:
    return out_dir(root).joinpath(*parts)


def plan_path(root: "str | Path | None" = None) -> Path:
    return Path(root or project_root()) / PLAN_FILENAME


# ── atomic writes ─────────────────────────────────────────────────────────────

def _atomic_replace(path: "str | Path", write_fn) -> None:
    """Atomically replace ``path`` with content written by ``write_fn(f)``.

    Writes a temp file in the SAME directory, then ``os.replace``s it into place
    (an atomic rename on one filesystem). A process kill, OOM, or ENOSPC
    mid-write leaves the previous file intact. This is NOT a power-loss
    durability guarantee: there is no fsync, so an OS/hardware crash right after
    the rename can still expose unflushed bytes on some filesystems.

    A symlinked destination is resolved first so the write goes THROUGH the link
    to its target rather than replacing the link with a regular file.
    """
    real = Path(os.path.realpath(str(path)))
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=".rmap-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            write_fn(f)
        # mkstemp creates 0600; match the destination's existing mode (or the
        # umask default) so a replace never silently tightens a previously
        # group-readable output to owner-only. Best-effort.
        try:
            mode = stat.S_IMODE(os.stat(real).st_mode)
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text_atomic(path: "str | Path", text: str) -> None:
    _atomic_replace(path, lambda f: f.write(text))


def write_json_atomic(path: "str | Path", obj, *, indent: "int | None" = 2,
                      ensure_ascii: bool = False) -> None:
    _atomic_replace(
        path,
        lambda f: (json.dump(obj, f, indent=indent, ensure_ascii=ensure_ascii,
                             sort_keys=False), f.write("\n")),
    )


# ── append-only logs ──────────────────────────────────────────────────────────

def append_jsonl(path: "str | Path", record: dict) -> None:
    """Append one JSON record as a single line. One syscall, no read, no race.

    An earlier version probed the file's last byte first and healed a missing
    trailing newline before writing. That was wrong twice over: the probe is a
    stat+open+seek on the hot path (post-commit has a sub-100 ms budget), and it
    races — two appenders can interleave between the probe and the write and
    produce a spurious blank line or, worse, a split record.

    Torn writes are handled on the READ side instead, by
    :func:`read_jsonl`, which recovers a record concatenated onto a crash
    remnant. That is strictly better: the write path stays a single atomic
    ``write(2)`` under ``O_APPEND``, and recovery costs nothing until something
    is actually broken.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
    try:
        data = (line + "\n").encode("utf-8")
        while data:
            written = os.write(fd, data)
            data = data[written:]
    finally:
        os.close(fd)


#: How many nested ``{`` positions to try when recovering a torn line. A record
#: is JSON, so a genuine remnant + record pair needs exactly one retry; the cap
#: stops a line of pathological garbage from costing O(n) parses.
_TORN_RECOVERY_ATTEMPTS = 8


def _recover_line(line: str) -> "dict | None":
    """Recover the record from a line a crash remnant was concatenated onto.

    A process killed mid-append leaves a partial line with no newline. The next
    append lands directly behind it, producing e.g.
    ``{"id":"b","te{"id":"c",...}``. The remnant is genuinely lost — it was never
    fully written — but the record behind it is intact and must not be lost too.
    """
    for n, i in enumerate(idx for idx, ch in enumerate(line) if ch == "{"):
        if n == 0:
            continue  # position 0 was already tried by the caller
        if n > _TORN_RECOVERY_ATTEMPTS:
            return None
        try:
            rec = json.loads(line[i:])
        except ValueError:
            continue
        if isinstance(rec, dict):
            return rec
    return None


def read_jsonl(path: "str | Path") -> list[dict]:
    """Read a JSONL file, recovering or skipping damaged lines rather than failing.

    The log is the durable memory. Losing access to ALL of it because one line is
    bad — a torn write, a botched union merge, a stray editor save — is the worst
    failure this tool has, so nothing here raises.
    """
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict] = []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            rec = _recover_line(line)
            if rec is None:
                continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# ── the derive lock ───────────────────────────────────────────────────────────

class LockBusy(Exception):
    """Raised when the derive lock could not be acquired within the deadline."""


@contextmanager
def derive_lock(root: "str | Path | None" = None, *, wait: float = 5.0):
    """Exclusive lock around the whole derive+write sequence.

    ``_atomic_replace`` makes each file individually atomic, but nothing makes
    the SET {graph.json, BRIEF.md, ROADMAP.md, .roadmap_state.json} mutually
    consistent — a post-commit hook racing a ``roadmap sync`` can otherwise
    leave a BRIEF describing a graph that no longer exists, and a
    read-modify-write of the sync watermark can drop a commit range forever.

    ``wait=0`` is the hook's mode: fail immediately on contention. Hooks are
    idempotent, so skipping is correct — the next run catches up. The CLI waits,
    then the caller degrades to read-only rather than writing blind.

    flock is unreliable on NFS; on Windows there is no fcntl, so we fall back to
    an O_EXCL lockfile carrying pid and mtime with a staleness reaper.
    """
    lock_path = out_path(LOCK_FILENAME, root=root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        yield from _lockfile_fallback(lock_path, wait)
        return

    fh = open(lock_path, "a+")
    deadline = time.monotonic() + max(0.0, wait)
    try:
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise LockBusy(f"another roadmap process holds {lock_path}") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()


_STALE_LOCK_SECONDS = 60.0


def _lockfile_fallback(lock_path: Path, wait: float):
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if age > _STALE_LOCK_SECONDS:
                # The holder died without cleaning up. Reap and retry.
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockBusy(f"another roadmap process holds {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


# ── idempotent marked blocks in shared files ──────────────────────────────────

def ensure_marked_block(path: "str | Path", body: str, *,
                        start: str, end: str) -> str:
    """Insert or update a roadmapify-owned block in a file we do not own.

    ``.gitignore``, ``.gitattributes``, ``CLAUDE.md`` and git hooks all belong to
    the user; we only ever own the lines between our two markers. Re-running an
    install REPLACES that span rather than appending a second copy, and anything
    outside it is preserved byte for byte.

    Returns "created", "updated" or "unchanged" so callers can report honestly
    instead of claiming to have written something they did not.
    """
    p = Path(path)
    block = f"{start}\n{body.strip()}\n{end}\n"
    try:
        original = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        original = ""

    if start in original and end in original:
        head, _, rest = original.partition(start)
        _, _, tail = rest.partition(end)
        new = head.rstrip("\n") + ("\n\n" if head.strip() else "") + block + tail.lstrip("\n")
        if new == original:
            return "unchanged"
        write_text_atomic(p, new)
        return "updated"

    new = (original.rstrip("\n") + "\n\n" if original.strip() else "") + block
    if new == original:
        return "unchanged"
    write_text_atomic(p, new)
    return "created" if not original.strip() else "updated"
