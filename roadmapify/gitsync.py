"""Read git. Never write it.

This is the ONLY module in the package that spawns a process, and it spawns
exactly one binary — the literal string ``git`` — with a subcommand drawn from a
read-only allowlist and every other argv element a string literal written here.
No argv is ever built from a plan file, a branch name or any other input: a
lookup happens in memory against output already read. That eliminates argument
injection by construction rather than mitigating it, which matters because
``git --no-pager log --format=%H -n 1 "--output=/tmp/PWNED"`` returns 0 and
writes the file, and a pull request can legitimately create a branch called
``-dashlead``. ``--end-of-options`` is in every argv as documentation in code.

It is a PRODUCER of evidence records and nothing else. ``status.py`` already
defines the contract and consumes it; nothing here derives a status, and
``status.py`` needs no change. Everything after ``facts()`` is pure, so the
whole mapping and record layer is testable without a repository.

What it never does: write a ref, run a mutating subcommand, run a command a
tracked file named, or emit an ``inferred`` record. File-overlap guesses are
computed, printed as proposals, and never stored — a stored guess is a stored
status wearing a different hat.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from roadmapify import journal, textmatch
from roadmapify.paths import EVIDENCE_FILENAME, append_jsonl, out_path, read_jsonl

CLI_TIMEOUT = 10.0
HOOK_TIMEOUT = 2.0
MAX_COMMITS = 20_000

#: Subcommands this module may invoke. Read-only, every one of them.
GIT_READ_SUBCOMMANDS = frozenset({
    "log", "rev-parse", "rev-list", "for-each-ref", "symbolic-ref", "var"})

#: Config knobs that can turn a read into a process spawn. Neutralised on the
#: command line, which beats the repo's own .git/config.
_SAFE_CONFIG = (
    "-c", "log.showSignature=false",
    "-c", "core.quotePath=false",
    "-c", "diff.external=",
    "-c", "core.fsmonitor=",
    "-c", "gc.auto=0",
)

#: A Roadmap trailer anywhere in the message, not only in the final block.
TRAILER_LINE_RE = re.compile(r"^[ \t]*Roadmap:[ \t]*(.+)$", re.MULTILINE)
_TASK_RE = re.compile(r"\bT-(\d{1,6})\b", re.IGNORECASE)

REASON_OK = ""
REASON_NO_GIT = "no-git"
REASON_NOT_A_REPO = "not-a-repo"
REASON_EMPTY = "empty"
REASON_ELSEWHERE = "elsewhere"
REASON_TIMEOUT = "timeout"
REASON_PARSE = "parse"
REASON_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: "tuple[str, ...]"
    ts: str
    author: str
    email: str
    trailers: "tuple[str, ...]"
    message: str

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass(frozen=True)
class Ref:
    name: str
    sha: str


@dataclass(frozen=True)
class Facts:
    ok: bool = False
    reason: str = REASON_NOT_A_REPO
    toplevel: str = ""
    bare: bool = False
    shallow: bool = False
    detached: bool = False
    branch: str = ""
    identity: str = ""
    commits: "tuple[Commit, ...]" = ()
    refs: "tuple[Ref, ...]" = ()
    files: "dict[str, tuple[str, ...]]" = field(default_factory=dict)
    plan_birth: str = ""
    truncated: bool = False

    def commit(self, sha: str) -> "Commit | None":
        return next((c for c in self.commits if c.sha == sha), None)


# ── the environment ───────────────────────────────────────────────────────────

def scrubbed_env(root: "str | Path") -> "dict[str, str]":
    """Drop every GIT_* key, then add back only what we mean.

    Dropping the whole prefix makes GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE,
    GIT_EXTERNAL_DIFF, GIT_SSH*, GIT_ASKPASS and anything git adds next year
    absent BY CONSTRUCTION. A blocklist is a list you forget to update. This
    matters most from a git hook, where git itself sets GIT_DIR — and ``-C`` does
    not beat GIT_DIR.

    The user's config files are DELIBERATELY NOT nulled. With
    ``GIT_CONFIG_GLOBAL=/dev/null`` git falls back to a gecos+hostname identity,
    every commit then reads as a stranger's, and the trust boundary quarantines
    the entire history. The execution-capable knobs are neutralised individually
    with ``-c`` instead, which leaves user.email intact.

    ``GIT_CEILING_DIRECTORIES`` is root's PARENT: with the ceiling at root, a
    plan directory nested inside someone else's repository still discovers the
    outer repo.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("PAGER", None)
    resolved = Path(root).resolve()
    env.update({
        "LC_ALL": "C",
        "TZ": "UTC",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CEILING_DIRECTORIES": str(resolved.parent),
    })
    return env


# ── the invocations ───────────────────────────────────────────────────────────
#
# Every element of every argv below is a string literal. Nothing is interpolated,
# nothing is splatted from a variable, and the subcommand is always the first
# element after the fixed -c block. tests/test_gitsync.py enforces all three by
# walking this module's AST.

def _run(argv: "list[str]", root: "str | Path", timeout: float):
    return subprocess.run(argv, cwd=str(root), env=scrubbed_env(root),
                          timeout=timeout, capture_output=True, check=False,
                          shell=False)


def _identity(root, *, timeout: float):
    # Read-only, has no write form, and returns 0 even outside a repository —
    # which makes it the git-is-present probe as well.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "var", "GIT_AUTHOR_IDENT"], root, timeout)


def _probe_flags(root, *, timeout: float):
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "rev-parse", "--is-inside-work-tree", "--is-bare-repository",
                 "--is-shallow-repository"], root, timeout)


def _toplevel(root, *, timeout: float):
    # Skipped when the repo is bare: --show-toplevel is fatal there, and a bare
    # repo would otherwise read as no repo at all.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "rev-parse", "--show-toplevel"], root, timeout)


def _head_branch(root, *, timeout: float):
    # `rev-parse --abbrev-ref HEAD` returns the literal string "HEAD" when
    # detached and must never be used: it would record a branch named HEAD.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "symbolic-ref", "--short", "-q", "HEAD"], root, timeout)


def _refs(root, *, timeout: float):
    # for-each-ref does NOT understand %x00 — it prints those four characters
    # literally. Its escape is %00; `git log --format` is the opposite. Reusing
    # one vocabulary in the other produces silently unparsable output.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "for-each-ref",
                 "--format=%(refname:short)%00%(objectname)%00%(symref)",
                 "refs/heads", "refs/remotes"], root, timeout)


def _history(root, *, timeout: float):
    # NUL is the only safe separator: a commit message may carry RS, US or any
    # other byte you might pick, but git refuses a NUL outright.
    # --branches --remotes, never --all: --all pulls in refs/tags, refs/notes,
    # refs/stash and any refs/pull/* a fetch config created.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "log", "-z", "--no-color", "--no-decorate", "--no-notes",
                 "--format=%H%x00%P%x00%aI%x00%an%x00%ae%x00"
                 "%(trailers:key=Roadmap,valueonly,separator=%x2C)%x00%B",
                 "--branches", "--remotes", "--max-count=20000",
                 "--end-of-options"], root, timeout)


def _files(root, *, timeout: float):
    # Feeds only the coverage denominator and the printed proposals. Never a
    # record.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "log", "--format=%x00%H%x00", "--name-only", "-z",
                 "--no-renames", "--no-color", "--diff-merges=first-parent",
                 "--branches", "--remotes", "--max-count=20000",
                 "--end-of-options"], root, timeout)


def _plan_birth(root, *, timeout: float):
    # --reverse WITHOUT --max-count: git applies --max-count BEFORE --reverse,
    # so asking for one reversed commit returns the NEWEST one touching the
    # file. Take the first line of the full reversed list instead.
    return _run(["git", "--no-pager", "-c", "log.showSignature=false",
                 "-c", "core.quotePath=false", "-c", "diff.external=",
                 "-c", "core.fsmonitor=", "-c", "gc.auto=0",
                 "rev-list", "--reverse", "--branches", "--remotes",
                 "--end-of-options", "--", "roadmap.toml"], root, timeout)


# ── parsing (pure) ────────────────────────────────────────────────────────────

def parse_history(blob: bytes) -> "tuple[tuple[Commit, ...], str]":
    """(commits, reason). A partial parse is worse than none, so a field count
    that is not a multiple of seven returns nothing with reason 'parse'."""
    if not blob:
        return (), REASON_OK
    parts = blob.split(b"\x00")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) % 7:
        return (), REASON_PARSE
    out = []
    for i in range(0, len(parts), 7):
        f = [p.decode("utf-8", "replace") for p in parts[i:i + 7]]
        sha, parents, ts, author, email, trailers, message = f
        out.append(Commit(
            sha=sha.strip(), parents=tuple(parents.split()) if parents else (),
            ts=ts.strip(), author=author, email=email,
            trailers=tuple(t.strip() for t in trailers.split(",") if t.strip()),
            message=message))
    return tuple(out), REASON_OK


def parse_files(blob: bytes) -> "dict[str, tuple[str, ...]]":
    """sha -> the paths that commit touched. Framing is
    ``"" , <sha>, "" , <file>, <file>, ...`` repeating; the first filename of
    each record carries a leading newline."""
    out: "dict[str, tuple[str, ...]]" = {}
    if not blob:
        return out
    tokens = [t.decode("utf-8", "replace") for t in blob.split(b"\x00")]
    sha = ""
    files: "list[str]" = []
    for tok in tokens:
        if len(tok) == 40 and all(c in "0123456789abcdef" for c in tok):
            if sha:
                out[sha] = tuple(files)
            sha, files = tok, []
            continue
        name = tok.lstrip("\n").strip()
        if name and sha:
            files.append(name)
    if sha:
        out[sha] = tuple(files)
    return out


def parse_refs(blob: bytes) -> "tuple[Ref, ...]":
    """Symbolic refs are dropped: `%(refname:short)` renders
    refs/remotes/origin/HEAD as the bare string "origin", which would otherwise
    appear as a phantom branch pointing at the trunk."""
    out = []
    for line in blob.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        parts = line.split("\x00")
        if len(parts) < 2:
            continue
        name, sha = parts[0].strip(), parts[1].strip()
        symref = parts[2].strip() if len(parts) > 2 else ""
        if symref or not name or not sha:
            continue
        out.append(Ref(name=name, sha=sha))
    return tuple(out)


def task_ids(text: str) -> "tuple[str, ...]":
    """Every T-nn in `text`, normalised. `t-9` -> ('T-09',)."""
    seen = []
    for m in _TASK_RE.finditer(text or ""):
        n = m.group(1).lstrip("0") or "0"
        tid = f"T-{int(n):02d}"
        if tid not in seen:
            seen.append(tid)
    return tuple(seen)


def trailer_tasks(c: Commit) -> "tuple[tuple[str, ...], bool]":
    """(task ids, found_only_outside_the_final_block).

    The union of git's own trailer parser and a raw regex over the message: a
    blank line between a Roadmap: line and the final trailer block makes git's
    parser skip it, and that shape occurs in this repo's own history.
    """
    from_git = tuple(t for v in c.trailers for t in task_ids(v))
    from_raw: "list[str]" = []
    for m in TRAILER_LINE_RE.finditer(c.message or ""):
        for t in task_ids(m.group(1)):
            if t not in from_raw:
                from_raw.append(t)
    union = list(from_git)
    for t in from_raw:
        if t not in union:
            union.append(t)
    return tuple(union), bool(from_raw) and not from_git


# ── the commit graph (pure, no spawn) ─────────────────────────────────────────

def reachable(commits, tips: "tuple[str, ...]") -> "frozenset[str]":
    by = {c.sha: c for c in commits}
    seen: "set[str]" = set()
    stack = [t for t in tips if t in by]
    while stack:
        sha = stack.pop()
        if sha in seen:
            continue
        seen.add(sha)
        stack.extend(p for p in by[sha].parents if p in by and p not in seen)
    return frozenset(seen)


def descendants(commits, root_sha: str) -> "frozenset[str]":
    by = {c.sha: c for c in commits}
    if root_sha not in by:
        return frozenset()
    kids: "dict[str, list[str]]" = {}
    for c in commits:
        for p in c.parents:
            kids.setdefault(p, []).append(c.sha)
    seen, stack = set(), [root_sha]
    while stack:
        sha = stack.pop()
        if sha in seen:
            continue
        seen.add(sha)
        stack.extend(k for k in kids.get(sha, ()) if k not in seen)
    return frozenset(seen)


def merge_payload(commits, merge_sha: str) -> "frozenset[str]":
    """What a merge brought in: reachable(second parent) - reachable(first).
    Zero extra spawns — the parent graph is already in hand."""
    by = {c.sha: c for c in commits}
    c = by.get(merge_sha)
    if c is None or len(c.parents) < 2:
        return frozenset()
    return reachable(commits, c.parents[1:]) - reachable(commits, c.parents[:1])


def trunk_ref(facts: Facts) -> str:
    """origin/main, else main, else master, else the current branch.

    This repo needs the whole chain on day one: the trunk is origin/main while
    the local branch is master and there is no local main. The usual fix,
    `git remote set-head`, writes a ref and is forbidden.
    """
    names = {r.name for r in facts.refs}
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if candidate in names:
            return candidate
    return facts.branch or ""


# ── reading ───────────────────────────────────────────────────────────────────

def identity_email(identity: str) -> str:
    """The email out of `git var GIT_AUTHOR_IDENT`.

    That returns "Name <email> 1788931308 +0000" — the trailing timestamp is
    part of the value, so comparing the whole string to a commit's %ae never
    matches and every commit would read as a stranger's.
    """
    start, end = identity.find("<"), identity.rfind(">")
    return identity[start + 1:end].strip() if 0 <= start < end else ""


def probe(root: "str | Path", *, timeout: float = CLI_TIMEOUT) -> Facts:
    """Repo shape only. Never raises; every failure becomes a typed reason."""
    root = Path(root)
    try:
        ident = _identity(root, timeout=timeout)
    except FileNotFoundError:
        return Facts(reason=REASON_NO_GIT)
    except subprocess.TimeoutExpired:
        return Facts(reason=REASON_TIMEOUT)
    except OSError:
        return Facts(reason=REASON_NO_GIT)
    identity = ident.stdout.decode("utf-8", "replace").strip() if ident.returncode == 0 else ""

    try:
        flags = _probe_flags(root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Facts(reason=REASON_TIMEOUT, identity=identity)
    if flags.returncode != 0:
        return Facts(reason=REASON_NOT_A_REPO, identity=identity)
    lines = flags.stdout.decode("utf-8", "replace").split()
    inside = lines[0] == "true" if lines else False
    bare = len(lines) > 1 and lines[1] == "true"
    shallow = len(lines) > 2 and lines[2] == "true"
    if not inside and not bare:
        return Facts(reason=REASON_NOT_A_REPO, identity=identity)

    toplevel = ""
    if not bare:
        try:
            top = _toplevel(root, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Facts(reason=REASON_TIMEOUT, identity=identity)
        if top.returncode != 0:
            return Facts(reason=REASON_NOT_A_REPO, identity=identity)
        toplevel = top.stdout.decode("utf-8", "replace").strip()
        if toplevel and Path(toplevel).resolve() != root.resolve():
            # project_root() prefers the nearest roadmap.toml and only falls back
            # to .git, so in a monorepo these legitimately differ — and syncing
            # would attribute a sibling project's commits to this plan.
            return Facts(reason=REASON_ELSEWHERE, toplevel=toplevel,
                         identity=identity, bare=bare, shallow=shallow)

    try:
        head = _head_branch(root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Facts(reason=REASON_TIMEOUT, identity=identity)
    branch = head.stdout.decode("utf-8", "replace").strip() if head.returncode == 0 else ""
    return Facts(ok=True, reason=REASON_OK, toplevel=toplevel, bare=bare,
                 shallow=shallow, detached=not branch, branch=branch,
                 identity=identity)


def facts(root: "str | Path", *, timeout: float = CLI_TIMEOUT,
          with_files: bool = True) -> Facts:
    """probe() plus refs, history, plan birth and optionally per-commit files."""
    from dataclasses import replace as _replace

    base = probe(root, timeout=timeout)
    if not base.ok:
        return base
    root = Path(root)
    try:
        refs = parse_refs(_refs(root, timeout=timeout).stdout)
        hist = _history(root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _replace(base, ok=False, reason=REASON_TIMEOUT)

    if hist.returncode != 0:
        # `git log` returns 128 on a repo with zero commits, which is
        # indistinguishable from not-a-repo — which is why it is never a probe.
        text = hist.stderr.decode("utf-8", "replace")
        reason = REASON_EMPTY if "does not have any commits" in text else REASON_UNREADABLE
        return _replace(base, ok=(reason == REASON_EMPTY), reason=reason, refs=refs)

    commits, reason = parse_history(hist.stdout)
    if reason:
        return _replace(base, ok=False, reason=reason, refs=refs)

    files: "dict[str, tuple[str, ...]]" = {}
    if with_files and not base.bare:
        try:
            files = parse_files(_files(root, timeout=timeout).stdout)
        except subprocess.TimeoutExpired:
            return _replace(base, ok=False, reason=REASON_TIMEOUT)

    birth = ""
    try:
        out = _plan_birth(root, timeout=timeout)
        if out.returncode == 0:
            first = out.stdout.decode("utf-8", "replace").split()
            birth = first[0] if first else ""
    except subprocess.TimeoutExpired:
        return _replace(base, ok=False, reason=REASON_TIMEOUT)

    return _replace(base, refs=refs, commits=commits, files=files,
                    plan_birth=birth, truncated=len(commits) >= MAX_COMMITS,
                    reason=REASON_EMPTY if not commits else REASON_OK)


# ── mapping ───────────────────────────────────────────────────────────────────

#: A branch must beat the runner-up by this much before the matcher will name a
#: task. A tie is not a mapping; it is two candidates and no answer.
MAPPING_MARGIN = 0.15


@dataclass(frozen=True)
class Mapping:
    branch: str
    task: str = ""
    confidence: str = ""
    score: float = 0.0
    runner_up: str = ""
    runner_score: float = 0.0
    margin: float = 0.0
    why: str = ""


def map_branch(name: str, plan, *, allow_matcher: bool = False) -> Mapping:
    """Bind a branch to a task, or refuse and say why.

    An explicit `T-nn` in the branch name is `declared` and needs no matcher. A
    fuzzy match is `mapped` and, because a wrong mapping silently attributes work
    to the wrong task and corrupts every number derived from it, it must clear
    the blocking floor AND beat its runner-up by a margin before it is offered
    at all.
    """
    ids = task_ids(name or "")
    known = {t.id: t for t in plan.tasks}
    for tid in ids:
        if tid in known:
            return Mapping(branch=name, task=tid, confidence="declared", score=1.0,
                           why="the branch name names the task")
    if ids:
        return Mapping(branch=name, why=f"{ids[0]} is not a task in this plan")
    if not allow_matcher:
        return Mapping(branch=name, why="no task id in the branch name")

    scored = textmatch.rank(name or "", [(t.id, t.label, t.intent or "")
                                         for t in plan.tasks])
    if not scored:
        return Mapping(branch=name, why="the plan has no tasks")
    top, top_score = scored[0]
    second, second_score = scored[1] if len(scored) > 1 else ("", 0.0)
    margin = top_score - second_score
    if top_score < textmatch.MATCH_FLOOR:
        return Mapping(branch=name, runner_up=top, runner_score=top_score,
                       why=f"best match {top} scored {top_score:.2f}, "
                           f"below the {textmatch.MATCH_FLOOR:.2f} floor")
    if margin < MAPPING_MARGIN:
        return Mapping(branch=name, runner_up=second, runner_score=second_score,
                       score=top_score, margin=margin,
                       why=f"{top} and {second} are within {margin:.2f} — "
                           "too close to choose between")
    return Mapping(branch=name, task=top, confidence="mapped", score=top_score,
                   runner_up=second, runner_score=second_score, margin=margin,
                   why=f"matched {top_score:.2f}, clear of {second or 'nothing'} "
                       f"by {margin:.2f}")


# ── records ───────────────────────────────────────────────────────────────────

def evidence_id(kind: str, task: str, ref: str, confidence: str) -> str:
    """Content-hashed on what identifies the OBSERVATION, never on when it was
    observed.

    Two developers who sync the same commit on two branches must produce the
    byte-identical line, or git's union merge leaves both and every count
    doubles. That is also what makes `roadmap sync` idempotent: a second run
    recomputes the same ids and appends nothing.
    """
    digest = hashlib.sha256(
        f"{kind}|{task}|{ref}|{confidence}".encode("utf-8")).digest()
    body = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"E-{body[:6]}"


def make(kind: str, task: str, *, ts: str, confidence: str = "declared",
         ref: str = "", branch: str = "", author: str = "",
         trust: str = journal.TRUST_LOCAL, merged_into: str = "",
         note: str = "") -> dict:
    """One evidence record in the shape status.py already consumes."""
    return {
        "id": evidence_id(kind, task, ref, confidence),
        "ts": ts,
        "kind": kind,
        "task": task,
        "confidence": confidence,
        "ref": ref,
        "branch": branch,
        "author": journal.sanitize(author, limit=120),
        "trust": trust,
        "files": [],
        "merged_into": merged_into,
        "note": journal.sanitize(note, limit=200),
    }


def records_from(facts_: Facts, plan, *, mine: str = "") -> "tuple[dict, ...]":
    """Every DECLARED record this history supports, sorted by (ts, id).

    Pure given `facts_`. Never emits `inferred` — a file-overlap guess is a
    proposal, not a record. A commit by another author lands as trust:foreign
    and is downgraded to `commit`, never `merge` or `claim`: an unvetted trailer
    from a pull request must not be able to flip a task to done.
    """
    known = {t.id for t in plan.tasks}
    trunk = trunk_ref(facts_)
    trunk_sha = next((r.sha for r in facts_.refs if r.name == trunk), "")
    on_trunk = reachable(facts_.commits, (trunk_sha,)) if trunk_sha else frozenset()

    out: "list[dict]" = []
    for c in facts_.commits:
        tasks, outside = trailer_tasks(c)
        if not tasks:
            continue
        foreign = bool(mine) and c.email and c.email != mine
        trust = journal.TRUST_FOREIGN if foreign else journal.TRUST_LOCAL
        for tid in tasks:
            if tid not in known:
                continue
            kind = "merge" if (c.sha in on_trunk and not foreign) else "commit"
            note = "trailer outside the final block" if outside else ""
            out.append(make(kind, tid, ts=c.ts, confidence="declared",
                            ref=c.sha, author=c.email, trust=trust,
                            merged_into=trunk if kind == "merge" else "",
                            note=note))
    return tuple(sorted(out, key=lambda r: (r["ts"], r["id"])))


def dropped_ids(facts_: Facts, plan) -> "tuple[tuple[str, str], ...]":
    """(sha, task id) pairs a trailer names that the plan does not have.

    normalize_evidence would drop these silently, so sync names them instead: a
    trailer pointing at a deleted task is a typo worth seeing.
    """
    known = {t.id for t in plan.tasks}
    out = []
    for c in facts_.commits:
        for tid in trailer_tasks(c)[0]:
            if tid not in known:
                out.append((c.sha, tid))
    return tuple(out)


def proposals(facts_: Facts, plan) -> "dict[str, tuple[str, ...]]":
    """sha -> task ids whose declared files this commit UNIQUELY touched.

    Printed, never stored. A guess that is usually right is exactly the confident
    claim this project exists to abolish, and persisting one would make it
    indistinguishable from observed fact on the next run.
    """
    from roadmapify.plan import split_deliverable

    owners: "dict[str, set[str]]" = {}
    for t in plan.tasks:
        for spec in t.produces:
            kind, value = split_deliverable(spec)
            if kind in ("file", "test") and value:
                owners.setdefault(value, set()).add(t.id)
    unique = {path: next(iter(ids)) for path, ids in owners.items() if len(ids) == 1}

    out: "dict[str, tuple[str, ...]]" = {}
    for sha, touched in facts_.files.items():
        hit = []
        for path in touched:
            tid = unique.get(path)
            if tid and tid not in hit:
                hit.append(tid)
        if hit:
            out[sha] = tuple(hit)
    return out


# ── the log ───────────────────────────────────────────────────────────────────

def load_evidence(root=None) -> "list[dict]":
    """read_jsonl, deduped on id — union merge can leave the same record twice."""
    seen: "dict[str, dict]" = {}
    for rec in read_jsonl(out_path(EVIDENCE_FILENAME, root=root)):
        if isinstance(rec, dict) and rec.get("id"):
            seen.setdefault(rec["id"], rec)
    return list(seen.values())


def append_new(root, records) -> "list[dict]":
    """Append only records whose id is not already present.

    The only writer in this module, which is what makes `roadmap sync`
    idempotent: running it twice adds nothing the second time.
    """
    path = out_path(EVIDENCE_FILENAME, root=root)
    have = {r["id"] for r in load_evidence(root)}
    added = []
    for rec in records:
        if rec["id"] in have:
            continue
        append_jsonl(path, rec)
        have.add(rec["id"])
        added.append(rec)
    return added
