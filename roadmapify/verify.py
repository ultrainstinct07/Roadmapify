"""Claim versus fact.

Every other module in this package answers from the plan and the journal. This
one is the only thing that looks at the working tree.

Its verdicts are ``present``, ``missing``, ``unverifiable`` and ``unresolvable``,
and collapsing any of them into another is the lie P-5 exists to stop.
``missing`` is an accusation, and is only ever reached by a single ``exists()``
against a path that provably lies inside the project root. ``unverifiable``
means no method exists and none ever will. ``unresolvable`` means the plan has a
bug, with a line number.

Nothing here executes: no subprocess, no shell, no pytest run, no import of
repository code. ``ast.parse`` is reading, not importing. That is a recorded
constraint, not a preference — ``roadmap.toml`` arrives through a pull request
or a clone, and the always-on block tells every agent to run this command
unprompted, so an execution path here is arbitrary code execution on checkout.

And nothing here writes. In particular nothing is ever appended to
``evidence.jsonl``: if presence became an evidence record then ``touch
roadmapify/verify.py`` would be a way to mark work done, and a
filesystem-shaped stored status would arrive through the back door. The import
is one-way — verify imports status; status never imports verify.
"""

from __future__ import annotations

import ast
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from roadmapify import journal, status
from roadmapify.paths import ROADMAP_OUT_NAME
from roadmapify.plan import DELIVERABLE_KINDS, split_deliverable
from roadmapify.textmatch import nearest

PRESENT = "present"
MISSING = "missing"
UNVERIFIABLE = "unverifiable"
UNRESOLVABLE = "unresolvable"
VERDICTS = (PRESENT, MISSING, UNVERIFIABLE, UNRESOLVABLE)

#: HOW an answer was obtained. A verdict without a method is a claim without
#: provenance, which is how `present` starts being read as `done`.
METHODS = ("stat", "glob", "graph", "none", "spec")

CONTRADICTED = "contradicted"
UNCHECKABLE = "uncheckable_claim"
CORROBORATED = "corroborated"
UNCLAIMED_WORK = "unclaimed_work"
FINDINGS = (CONTRADICTED, UNCHECKABLE, CORROBORATED, UNCLAIMED_WORK)

#: Screen words. The finding names are the machine surface; these are the column.
FINDING_WORDS = {
    CONTRADICTED: "CONTRADICTED", UNCHECKABLE: "unchecked claim",
    CORROBORATED: "corroborated", UNCLAIMED_WORK: "unclaimed",
}

NOTHING_CLAIMED = "nothing_claimed"
REPORT_VERDICTS = (CONTRADICTED, CORROBORATED, NOTHING_CLAIMED)

JSON_SCHEMA = 1

#: `done` and `done_claimed` are distinguished LOUDLY in the screen — a
#: merge-verified task with an absent file is a worse failure than a self-report
#: — and deliberately not in the code: one condition, one exit code, so a CI
#: caller branches without parsing prose.
LABEL_DONE = {"done_claimed": "done*", "done": "done"}

#: Directories a glob never descends into. Reading .gitignore properly is a
#: bigger job than T-16; this is the honest stdlib approximation, and the screen
#: says so. Without it `glob:**/plan.py` matches BOTH roadmapify/plan.py and
#: build/lib/roadmapify/plan.py on this very repo — so a deleted source file
#: would read `present`, the worst verdict this command can emit.
PRUNE_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".tox", ".nox", ".venv", "venv", "node_modules", "build", "dist",
    ".eggs", ".ruff_cache", "site-packages", ROADMAP_OUT_NAME,
})

GLOB_MATCH_CAP = 200
GLOB_CANDIDATE_CAP = 20_000
AST_BYTE_CAP = 1_048_576
SPEC_DISPLAY_CAP = 120


@dataclass(frozen=True)
class Resolution:
    """One deliverable spec, resolved. ``detail`` is NEVER empty."""
    spec: str
    display: str        # sanitize(spec) — what a cmd: row prints, so a human can run it
    shown: str          # sanitize(value) — the readable path column
    kind: str
    value: str
    verdict: str
    method: str
    detail: str
    weak: bool = False
    size: int = -1
    matches: int = 0
    declared_by: "tuple[str, ...]" = ()
    line: int = 0


@dataclass(frozen=True)
class TaskVerification:
    id: str
    label: str
    phase: str
    provisional: bool
    status: str
    claimed: bool
    counted: bool
    line: int = 0
    specs: "tuple[str, ...]" = ()
    evidence: "tuple[str, ...]" = ()
    last_seen: str = ""
    finding: str = ""
    contradicted_by: "tuple[str, ...]" = ()

    @property
    def undeclared(self) -> bool:
        return not self.specs


@dataclass(frozen=True)
class Report:
    goal: str
    root: str
    scope: str = ""
    resolutions: "tuple[Resolution, ...]" = ()
    tasks: "tuple[TaskVerification, ...]" = ()
    phases: "tuple[str, ...]" = ()
    declarations: int = 0
    skipped_provisional: int = 0
    evidence_count: int = 0

    def resolution(self, spec: str) -> "Resolution | None":
        return next((r for r in self.resolutions if r.spec == spec), None)

    def task(self, tid: str) -> "TaskVerification | None":
        return next((t for t in self.tasks if t.id == tid), None)

    def counts(self) -> "dict[str, int]":
        """Verdict counts are over DISTINCT resolutions, never per task — a
        report whose header does not add up is one that contradicts itself."""
        by = {v: sum(1 for r in self.resolutions if r.verdict == v) for v in VERDICTS}
        by.update(
            tasks=len(self.tasks),
            declarations=self.declarations,
            deliverables=len(self.resolutions),
            weak=sum(1 for r in self.resolutions if r.weak and r.verdict == PRESENT),
            undeclared=sum(1 for t in self.tasks if t.undeclared),
            claimed=sum(1 for t in self.tasks if t.claimed),
            contradicted=len(self.contradicted),
        )
        return by

    @property
    def contradicted(self) -> "tuple[TaskVerification, ...]":
        return tuple(t for t in self.tasks if t.finding == CONTRADICTED)

    @property
    def verdict(self) -> str:
        """The machine-readable answer to "was anything actually checked",
        which an exit code alone cannot give."""
        if self.contradicted:
            return CONTRADICTED
        claimed = [t for t in self.tasks if t.claimed]
        if claimed:
            if all(t.finding == CORROBORATED for t in claimed):
                return CORROBORATED
            return UNCHECKABLE
        return NOTHING_CLAIMED


# ── anchoring ─────────────────────────────────────────────────────────────────

def anchor(root: "str | Path", value: str) -> "Path | None":
    """Project-relative path, or ``None`` if the spec cannot be safely anchored.

    Refuses LEXICALLY and before any syscall: an absolute path, a leading ``~``,
    any ``..`` component, and any value whose normpath'd join escapes root. A
    hostile ``produces`` value is therefore never stat-ed at all.

    Deliberately does NOT call ``.resolve()``. Resolving would follow a symlink
    out of the project before we had decided the spec was safe to touch, and
    would treat a legitimate in-repo symlink as an escape and produce a false
    ``missing``.
    """
    value = (value or "").strip()
    if not value or value.startswith("~") or os.path.isabs(value):
        return None
    if "\x00" in value:
        return None
    parts = PurePosixPath(value.replace("\\", "/")).parts
    if any(p == ".." for p in parts):
        return None
    # The ROOT is resolved (it is ours, and a relative root like "." would
    # otherwise fail every prefix check). The VALUE never is — see above.
    base = str(Path(root).resolve())
    joined = os.path.normpath(os.path.join(base, *[p for p in parts if p != "."]))
    if joined != base and not joined.startswith(base + os.sep):
        return None
    path = Path(joined)
    for parent in reversed(path.parents):
        if str(parent) == base or not str(parent).startswith(base + os.sep):
            continue
        if parent.is_symlink():
            return None
    return path


def _typo_kind(value: str) -> "str | None":
    """A prefix that is a near-miss for a real deliverable kind.

    ``notes:2026.md`` is a legal filename, so a blanket "unknown prefix means
    unresolvable" rule would mislabel a real deliverable. Only a prefix within
    one edit of a real kind is called a typo.
    """
    head, sep, _ = value.partition(":")
    if not sep or not head or "/" in head or " " in head:
        return None
    if head in DELIVERABLE_KINDS:
        return None
    return nearest(head.lower(), DELIVERABLE_KINDS)


def _count_tests(path: Path) -> "int | None":
    """Test functions in a Python file, or None if it could not be parsed.

    ``ast.parse`` is reading, not importing: no module-level code runs.
    """
    try:
        if path.is_symlink() or path.stat().st_size > AST_BYTE_CAP:
            return None
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, SyntaxError, RecursionError):
        return None
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test_"):
            n += 1
    for node in getattr(tree, "body", ()):
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            n += 1
    return n


def _match(pat: "list[str]", parts: "list[str]") -> bool:
    """``**`` matches zero or more whole components; everything else is
    fnmatchcase per component. Case-sensitive on purpose: an answer that
    differs between macOS and Linux is not a derived answer."""
    if not pat:
        return not parts
    if pat[0] == "**":
        return any(_match(pat[1:], parts[i:]) for i in range(len(parts) + 1))
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], pat[0]) and _match(pat[1:], parts[1:])


def _glob(root: Path, value: str) -> "tuple[str, str, int, str]":
    """(verdict, method, matches, detail) for a glob spec."""
    parts = [p for p in PurePosixPath(value.replace("\\", "/")).parts if p != "."]
    if not parts:
        return UNRESOLVABLE, "spec", 0, "empty pattern"
    if all(p in ("*", "**") for p in parts):
        return UNRESOLVABLE, "spec", 0, "matches the whole tree — narrow it"

    magic = set("*?[")
    literals = {p for p in parts if p != "**" and not (magic & set(p))}
    base: "list[str]" = []
    for p in parts:
        if p in literals:
            base.append(p)
        else:
            break
    rest = parts[len(base):]
    start = root.joinpath(*base) if base else root
    if not rest:
        if start.is_symlink() or anchor(root, str(start.relative_to(root))) is None:
            return UNRESOLVABLE, "spec", 0, "symlink traversal refused"
        if start.exists():
            return PRESENT, "glob", 1, "1 match"
        return MISSING, "glob", 0, "no matches"
    if start.is_symlink() or anchor(root, str(start.relative_to(root))) is None:
        return UNRESOLVABLE, "spec", 0, "symlink traversal refused"
    if not start.is_dir():
        return MISSING, "glob", 0, "no matches"

    seen = matches = 0
    for dirpath, dirnames, filenames in os.walk(start, topdown=True):
        # `or d in literals` is the whole point: `glob:build/*.whl` must still
        # resolve inside build/. A prune list without it trades a false positive
        # for a false negative on a legitimate deliverable.
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS or d in literals]
        seen += len(dirnames) + len(filenames)
        rel_dir = os.path.relpath(dirpath, start)
        prefix = [] if rel_dir == "." else rel_dir.split(os.sep)
        for name in list(dirnames) + list(filenames):
            if _match(list(rest), prefix + [name]):
                matches += 1
                if matches >= GLOB_MATCH_CAP:
                    return PRESENT, "glob", matches, f"{GLOB_MATCH_CAP}+ matches"
        if seen > GLOB_CANDIDATE_CAP and not matches:
            return (UNVERIFIABLE, "glob", 0,
                    f"gave up after {GLOB_CANDIDATE_CAP:,} paths — narrow the pattern")
    if matches:
        return PRESENT, "glob", matches, f"{matches} match" + ("es" if matches > 1 else "")
    return MISSING, "glob", 0, "no matches"


# ── resolution ────────────────────────────────────────────────────────────────

def resolve_spec(spec: str, root: "str | Path", *, line: int = 0,
                 code_graph: "dict | None" = None) -> Resolution:
    """One deliverable spec -> one :class:`Resolution`.

    The only function in the package that touches the filesystem on behalf of a
    plan value. Never executes, never imports, never leaves the project root.

    ``code_graph`` is T-17's seam, present from day one so the sanctioned
    mechanism is already in the signature: a graphify ``graph.json`` that
    graphify wrote out of band, read with stdlib json. It forecloses the
    alternatives by leaving them nowhere to go.
    """
    root = Path(root)
    display = journal.sanitize(spec, limit=SPEC_DISPLAY_CAP)
    kind, value = split_deliverable(spec)
    shown = display if kind == "cmd" else journal.sanitize(value, limit=SPEC_DISPLAY_CAP)

    def r(verdict, method, detail, **kw):
        return Resolution(spec=spec, display=display, shown=shown, kind=kind, value=value,
                          verdict=verdict, method=method, detail=detail,
                          line=line, **kw)

    where = f" — roadmap.toml:{line}" if line else ""

    if kind == "cmd":
        # Never `missing`: calling a cmd missing accuses the author of not doing
        # work they may well have done. The text is printed so a human can run
        # it — the tool reports, the human runs.
        return r(UNVERIFIABLE, "none", "would have to run it")

    if kind == "symbol":
        if code_graph is None:
            return r(UNVERIFIABLE, "none", "no code graph — the bridge is T-17")
        from roadmapify import bridge
        try:
            graph = bridge.validate(code_graph)
        except ValueError as exc:
            return r(UNRESOLVABLE, "graph", str(exc))
        matched = bridge.symbol(graph, value)
        if matched is None:
            return r(UNVERIFIABLE, "graph", "no unique qualified symbol match")
        return r(PRESENT, "graph", "symbol indexed · graph freshness unknown · context only", weak=True)

    if kind == "glob":
        if anchor(root, value.split("*")[0].split("?")[0] or ".") is None:
            return r(UNRESOLVABLE, "spec", f"escapes the project root{where}")
        verdict, method, matches, detail = _glob(root, value)
        return r(verdict, method, detail, matches=matches, weak=(verdict == PRESENT))

    # file: and test: both stat a path. A test: value may be a pytest node id.
    path_value, sep, node = (value.partition("::") if kind == "test"
                             else (value, "", ""))
    typo = _typo_kind(path_value)
    if typo:
        return r(UNRESOLVABLE, "spec",
                 f"unknown deliverable kind '{path_value.partition(':')[0]}' — "
                 f"did you mean '{typo}:'?{where}")

    path = anchor(root, path_value)
    if path is None:
        return r(UNRESOLVABLE, "spec", f"escapes the project root{where}")

    if path.is_symlink():
        return r(UNVERIFIABLE, "stat", "symlink target not followed", weak=True)
    if not path.exists():
        if ":" in path_value:
            return r(MISSING, "stat",
                     f"no such path — '{path_value.partition(':')[0]}' is not a "
                     f"deliverable kind; a bare path was assumed")
        return r(MISSING, "stat", "no such path")

    link = "symlink · " if path.is_symlink() else ""
    if path.is_dir():
        return r(PRESENT, "stat", f"{link}directory", weak=True)

    try:
        size = path.stat().st_size
    except OSError:
        size = -1

    if kind == "test":
        # The detail ALWAYS begins "not run". Whether the test passes is
        # unverifiable permanently and by design, and `present` on a test file
        # is the single most likely misreading of the whole screen.
        if size == 0:
            detail, weak = "not run · EMPTY", True
        elif path.suffix == ".py":
            n = _count_tests(path)
            if n is None:
                detail, weak = "not run · not Python", True
            elif n == 0:
                detail, weak = "not run · NO TESTS", True
            else:
                detail, weak = f"not run · {n} tests", False
        else:
            detail, weak = "not run", False
        if sep:
            detail, weak = detail + " · node not checked", True
        return r(PRESENT, "stat", link + detail, weak=weak or bool(link), size=size)

    if size == 0:
        return r(PRESENT, "stat", f"{link}file · EMPTY", weak=True, size=size)
    return r(PRESENT, "stat", f"{link}file", weak=bool(link), size=size)


def resolve(plan, root: "str | Path", *, specs: "tuple[str, ...] | None" = None,
            code_graph: "dict | None" = None) -> "tuple[Resolution, ...]":
    """Every distinct spec resolved ONCE and attributed to every task declaring it.

    Per-task resolution would count one missing file twice and the header counts
    would not add up.
    """
    order: "list[str]" = []
    by_spec: "dict[str, list[str]]" = {}
    lines: "dict[str, int]" = {}
    wanted = None if specs is None else set(specs)
    for t in sorted(plan.tasks, key=lambda t: (t.phase, t.id)):
        for spec in t.produces:
            if wanted is not None and spec not in wanted:
                continue
            if spec not in by_spec:
                by_spec[spec] = []
                order.append(spec)
                lines[spec] = t.line or 0
            by_spec[spec].append(t.id)
    return tuple(
        Resolution(**{**resolve_spec(s, root, line=lines[s], code_graph=code_graph).__dict__,
                      "declared_by": tuple(by_spec[s])})
        for s in order)


def in_scope(plan, snapshot, *, task: str = "", phase: str = "",
             claimed_only: bool = False) -> tuple:
    """The tasks verify looks at.

    A task named explicitly is ALWAYS checked, provisional or not — asking about
    a task by name must answer, not skip. A provisional task whose derived
    status says done is checked and marked, because the plan being behind the
    work must never be swallowed; it never enters a denominator.
    """
    if task:
        t = plan.task(task)
        return (t,) if t else ()
    out = []
    for t in sorted(plan.tasks, key=lambda t: (t.phase, t.id)):
        if phase and t.phase != phase:
            continue
        st = snapshot.task(t.id)
        claimed = bool(st and st.status in status.DONE_STATES)
        if t.provisional and not claimed:
            continue
        if claimed_only and not claimed:
            continue
        out.append(t)
    return tuple(out)


def verify(plan, snapshot, root: "str | Path", *, task: str = "", phase: str = "",
           claimed_only: bool = False, code_graph: "dict | None" = None) -> Report:
    """Resolve, then join against the snapshot.

    Reads statuses; never derives one, never writes one.
    """
    root = Path(root)
    scoped = in_scope(plan, snapshot, task=task, phase=phase, claimed_only=claimed_only)
    specs = tuple(dict.fromkeys(s for t in scoped for s in t.produces))
    resolutions = resolve(plan, root, specs=specs, code_graph=code_graph)
    by_spec = {r.spec: r for r in resolutions}

    tasks = []
    for t in scoped:
        st = snapshot.task(t.id)
        claimed = bool(st and st.status in status.DONE_STATES)
        mine = [by_spec[s] for s in t.produces if s in by_spec]
        missing = tuple(r.spec for r in mine if r.verdict == MISSING)
        present = [r for r in mine if r.verdict == PRESENT]
        if claimed and missing:
            finding = CONTRADICTED
        elif claimed and not present and not missing:
            finding = UNCHECKABLE
        elif claimed and len(present) == len(mine) and not any(r.weak for r in mine):
            finding = CORROBORATED
        elif claimed:
            finding = UNCHECKABLE
        elif not claimed and mine and present and not missing:
            finding = UNCLAIMED_WORK
        else:
            finding = ""
        tasks.append(TaskVerification(
            id=t.id, label=t.label, phase=t.phase, provisional=t.provisional,
            status=st.status if st else "", claimed=claimed,
            counted=not t.provisional, line=t.line or 0,
            specs=tuple(t.produces),
            evidence=tuple(st.evidence) if st else (),
            last_seen=st.last_seen if st else "",
            finding=finding, contradicted_by=missing))

    scope = task or phase or ("claimed" if claimed_only else "")
    return Report(
        goal=plan.goal.label, root=str(root), scope=scope,
        resolutions=resolutions, tasks=tuple(tasks),
        phases=tuple(dict.fromkeys(t.phase for t in tasks)),
        declarations=sum(len(t.produces) for t in scoped),
        skipped_provisional=sum(1 for t in plan.tasks
                                if t.provisional and t not in scoped),
        evidence_count=snapshot.evidence_count)
