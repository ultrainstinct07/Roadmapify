"""``roadmap.toml`` — the plan. Parse, validate and emit.

The plan lives at the PROJECT ROOT, not inside the output directory, because it
is the one file a human edits and reviews: it diffs in a PR, it merges like any
other source file, and putting it next to ``pyproject.toml`` says that plainly.

It contains **no status field anywhere**. That is not an oversight, it is the
design: a hand-written ``status = "done"`` is exactly the thing that goes stale
and turns the file into a confident lie. Status is derived from evidence on
every run (see ``status.py``), so the only way to make the plan wrong is to
write a wrong plan.

TOML rather than YAML or JSON: ``tomllib`` is stdlib from 3.11 (``tomli`` is the
one conditional dependency below that), it has no significant whitespace to get
wrong in a merge conflict, and it round-trips comments in the file we emit
because we emit them ourselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as _toml  # type: ignore[no-redef]

from roadmapify.paths import plan_path

SCHEMA_VERSION = 1

#: Deliverable spec kinds. Deliberately tiny — every one of these must be
#: checkable by `roadmap verify` without executing repo code, or it is not a
#: deliverable, it is a wish.
DELIVERABLE_KINDS = ("file", "glob", "symbol", "test", "cmd")

_ID_LINE_RE = re.compile(r'^\s*id\s*=\s*"([^"]+)"', re.MULTILINE)
_PHASE_ID_RE = re.compile(r"^P-\d+$")
_TASK_ID_RE = re.compile(r"^T-[0-9a-z]{2,6}$")


@dataclass(frozen=True)
class Goal:
    label: str
    statement: str = ""
    why: str = ""
    archetype: str = "generic"
    created: str = ""
    success_criteria: tuple[str, ...] = ()
    #: Provenance only. Excluded from equality so a parsed plan compares equal
    #: to the plan it was emitted from — which is what the round-trip test
    #: asserts, and what makes 'the graph is derived' a checkable claim.
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class PhaseSpec:
    id: str
    label: str
    order: int
    intent: str = ""
    #: A literal runnable command or observable outcome. This is what stops a
    #: phase from being a vague bucket — if you cannot write `ships`, the phase
    #: is not a phase.
    ships: str = ""
    exit_criteria: str = ""
    demo: str = ""
    provisional: bool = False
    line: int = field(default=0, compare=False)

    @property
    def milestone_id(self) -> str:
        """Milestones are 1:1 with phases and auto-derived, so the id is derivable too."""
        return "M-" + self.id.split("-", 1)[1]


@dataclass(frozen=True)
class TaskSpec:
    id: str
    label: str
    phase: str
    intent: str = ""
    depends_on: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    branch: str = ""
    #: True for tasks beyond the next phase. They are visible in `roadmap tree`
    #: so the COMPLETE path exists (the user asked for a path to a finished
    #: app), but excluded from ready lists, from verify, and from both health
    #: denominators — an unreviewed guess must never be able to make the
    #: roadmap lie about itself. `roadmap expand` promotes them.
    provisional: bool = False
    origin: str = "template"  # template | human | llm | agent
    line: int = field(default=0, compare=False)


@dataclass(frozen=True)
class Plan:
    goal: Goal
    phases: tuple[PhaseSpec, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()
    schema: int = SCHEMA_VERSION

    def phase(self, pid: str) -> "PhaseSpec | None":
        return next((p for p in self.phases if p.id == pid), None)

    def task(self, tid: str) -> "TaskSpec | None":
        return next((t for t in self.tasks if t.id == tid), None)

    def tasks_of(self, pid: str) -> tuple[TaskSpec, ...]:
        return tuple(t for t in self.tasks if t.phase == pid)

    def ordered_phases(self) -> tuple[PhaseSpec, ...]:
        return tuple(sorted(self.phases, key=lambda p: (p.order, p.id)))


# ── deliverables ──────────────────────────────────────────────────────────────

def split_deliverable(spec: str) -> tuple[str, str]:
    """``"file:a/b.py"`` -> ``("file", "a/b.py")``. A bare path defaults to ``file``."""
    kind, sep, rest = spec.partition(":")
    if sep and kind in DELIVERABLE_KINDS:
        return kind, rest.strip()
    return "file", spec.strip()


# ── parsing ───────────────────────────────────────────────────────────────────

def _line_index(text: str) -> dict[str, int]:
    """Map each ``id = "X"`` to its 1-based line number.

    ``tomllib`` discards position information, and ``source_location`` is part of
    the node contract we share with graphify — so we recover it with a post-hoc
    scan of the raw text. It is exact for the files we emit (one `id =` per
    table) and harmless when it misses.
    """
    out: dict[str, int] = {}
    for m in _ID_LINE_RE.finditer(text):
        out.setdefault(m.group(1), text.count("\n", 0, m.start()) + 1)
    return out


def _s(v, default: str = "") -> str:
    return v.strip() if isinstance(v, str) else default


def _tuple(v) -> tuple[str, ...]:
    if isinstance(v, str):
        return (v,)
    if isinstance(v, (list, tuple)):
        return tuple(str(x).strip() for x in v if str(x).strip())
    return ()


def parse_plan(text: str) -> Plan:
    """Parse ``roadmap.toml`` text into a :class:`Plan`. Raises ValueError on bad TOML."""
    try:
        data = _toml.loads(text)
    except Exception as exc:  # tomllib raises TOMLDecodeError
        raise ValueError(f"roadmap.toml is not valid TOML: {exc}") from exc

    lines = _line_index(text)
    graw = data.get("goal") or {}
    if not isinstance(graw, dict):
        raise ValueError("[goal] must be a table")

    goal = Goal(
        label=_s(graw.get("label")),
        statement=_s(graw.get("statement")),
        why=_s(graw.get("why")),
        archetype=_s(graw.get("archetype"), "generic"),
        created=_s(graw.get("created")),
        success_criteria=_tuple(graw.get("success_criteria")),
        line=lines.get("G-0", 1),
    )

    phases: list[PhaseSpec] = []
    for i, p in enumerate(data.get("phase") or [], start=1):
        if not isinstance(p, dict):
            continue
        pid = _s(p.get("id")) or f"P-{i}"
        phases.append(PhaseSpec(
            id=pid,
            label=_s(p.get("label")) or pid,
            order=int(p.get("order", i) or i),
            intent=_s(p.get("intent")),
            ships=_s(p.get("ships")),
            exit_criteria=_s(p.get("exit_criteria")),
            demo=_s(p.get("demo")),
            provisional=bool(p.get("provisional", False)),
            line=lines.get(pid, 0),
        ))

    tasks: list[TaskSpec] = []
    for i, t in enumerate(data.get("task") or [], start=1):
        if not isinstance(t, dict):
            continue
        tid = _s(t.get("id")) or f"T-{i:02d}"
        tasks.append(TaskSpec(
            id=tid,
            label=_s(t.get("label")) or tid,
            phase=_s(t.get("phase")),
            intent=_s(t.get("intent")),
            depends_on=_tuple(t.get("depends_on")),
            produces=_tuple(t.get("produces")),
            branch=_s(t.get("branch")),
            provisional=bool(t.get("provisional", False)),
            origin=_s(t.get("origin"), "template"),
            line=lines.get(tid, 0),
        ))

    return Plan(
        goal=goal,
        phases=tuple(phases),
        tasks=tuple(tasks),
        schema=int(data.get("schema", SCHEMA_VERSION) or SCHEMA_VERSION),
    )


def load_plan(root: "str | Path | None" = None) -> "Plan | None":
    p = plan_path(root)
    if not p.is_file():
        return None
    return parse_plan(p.read_text(encoding="utf-8"))


# ── validation ────────────────────────────────────────────────────────────────

def validate_plan(plan: Plan) -> list[str]:
    """Return a list of human-readable errors; empty means valid.

    Every message names the ``roadmap.toml`` line where it can be fixed, because
    the whole point of putting the plan in a hand-edited file is that a human
    fixes it by hand.
    """
    errors: list[str] = []
    if not plan.goal.label:
        errors.append("roadmap.toml:[goal] label is required — a roadmap with no goal is a list")

    seen_p: dict[str, int] = {}
    orders: dict[int, str] = {}
    for p in plan.phases:
        if not _PHASE_ID_RE.match(p.id):
            errors.append(f"roadmap.toml:{p.line}: phase id {p.id!r} must look like 'P-1'")
        if p.id in seen_p:
            errors.append(
                f"roadmap.toml:{p.line}: duplicate phase id {p.id!r} "
                f"(first seen at line {seen_p[p.id]})"
            )
        seen_p[p.id] = p.line
        if p.order in orders and orders[p.order] != p.id:
            errors.append(
                f"roadmap.toml:{p.line}: phase {p.id} reuses order {p.order} "
                f"(already used by {orders[p.order]})"
            )
        orders[p.order] = p.id

    seen_t: dict[str, int] = {}
    for t in plan.tasks:
        if not _TASK_ID_RE.match(t.id):
            errors.append(
                f"roadmap.toml:{t.line}: task id {t.id!r} must look like 'T-07' or 'T-2f9a'"
            )
        if t.id in seen_t:
            # This is the failure mode that a sequential id scheme produces on a
            # branch merge, and it hard-fails the build, so both line numbers are
            # printed to make the merge resolution mechanical.
            errors.append(
                f"roadmap.toml:{t.line}: duplicate task id {t.id!r} "
                f"(first seen at line {seen_t[t.id]}) — ids must be unique; "
                f"tasks added after init should use a content-hashed id like T-2f9a"
            )
        seen_t[t.id] = t.line
        if t.phase and t.phase not in seen_p:
            errors.append(f"roadmap.toml:{t.line}: task {t.id} names unknown phase {t.phase!r}")
        for dep in t.depends_on:
            if dep == t.id:
                errors.append(f"roadmap.toml:{t.line}: task {t.id} depends on itself")
        for spec in t.produces:
            kind, rest = split_deliverable(spec)
            if not rest:
                errors.append(f"roadmap.toml:{t.line}: task {t.id} has an empty {kind} deliverable")

    known = set(seen_t)
    for t in plan.tasks:
        for dep in t.depends_on:
            if dep not in known:
                errors.append(f"roadmap.toml:{t.line}: task {t.id} depends on unknown task {dep!r}")
    return errors


def find_cycles(plan: Plan) -> list[list[str]]:
    """Every ``depends_on`` cycle, as a list of id paths.

    A cycle is a hard build failure rather than a warning: a roadmap with one has
    no 'next task', which is the only question the tool exists to answer.
    """
    graph = {t.id: [d for d in t.depends_on if d != t.id] for t in plan.tasks}
    cycles: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    def walk(node: str, stack: list[str]) -> None:
        colour[node] = GREY
        stack.append(node)
        for nxt in graph.get(node, ()):
            if nxt not in colour:
                continue
            if colour[nxt] == GREY:
                cyc = stack[stack.index(nxt):] + [nxt]
                key = tuple(sorted(cyc))
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    cycles.append(cyc)
            elif colour[nxt] == WHITE:
                walk(nxt, stack)
        stack.pop()
        colour[node] = BLACK

    for node in sorted(graph):
        if colour[node] == WHITE:
            walk(node, [])
    return cycles


# ── emitting ──────────────────────────────────────────────────────────────────

def _q(s: str) -> str:
    """TOML basic string. We emit only these, so the escape set is the full one."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _arr(items) -> str:
    items = list(items)
    if not items:
        return "[]"
    if len(items) == 1:
        return f"[{_q(items[0])}]"
    body = ",\n".join(f"    {_q(i)}" for i in items)
    return "[\n" + body + ",\n]"


def emit_plan(plan: Plan) -> str:
    """Render a :class:`Plan` back to commented TOML. Deterministic and byte-stable."""
    L: list[str] = [
        "# roadmap.toml — the plan.",
        "#",
        "# Hand-edit this file. Everything under roadmap-out/ except journal.jsonl and",
        "# evidence.jsonl is derived from it and is regenerated by `roadmap build`.",
        "#",
        "# There is deliberately NO status field. Status is derived from git evidence on",
        "# every run, so it cannot go stale. Recording that evidence is P-3:",
        "# `roadmap start` / `roadmap done` and the `Roadmap: T-nn` commit trailer all",
        "# arrive with it. Until then nothing reads as done, which is the honest answer.",
        "",
        f"schema = {plan.schema}",
        "",
        "[goal]",
        f"label = {_q(plan.goal.label)}",
    ]
    if plan.goal.statement:
        L.append(f"statement = {_q(plan.goal.statement)}")
    L.append("# why this project exists at all — the first thing lost when context is compacted")
    L.append(f"why = {_q(plan.goal.why)}")
    L.append(f"archetype = {_q(plan.goal.archetype)}")
    if plan.goal.created:
        L.append(f"created = {_q(plan.goal.created)}")
    L.append("# checkable sentences. The last phase is not done until these are true.")
    L.append(f"success_criteria = {_arr(plan.goal.success_criteria)}")

    for p in plan.ordered_phases():
        L += ["", "[[phase]]", f"id = {_q(p.id)}", f"label = {_q(p.label)}",
              f"order = {p.order}"]
        if p.intent:
            L.append(f"intent = {_q(p.intent)}")
        L.append("# a literal command or observable outcome — if you cannot write this,")
        L.append("# the phase is not a phase yet")
        L.append(f"ships = {_q(p.ships)}")
        if p.exit_criteria:
            L.append(f"exit_criteria = {_q(p.exit_criteria)}")
        if p.demo:
            L.append(f"demo = {_q(p.demo)}")
        if p.provisional:
            L.append("provisional = true")

    for t in sorted(plan.tasks, key=lambda t: (t.phase, t.id)):
        L += ["", "[[task]]", f"id = {_q(t.id)}", f"label = {_q(t.label)}",
              f"phase = {_q(t.phase)}"]
        if t.intent:
            L.append(f"intent = {_q(t.intent)}")
        if t.depends_on:
            L.append(f"depends_on = {_arr(t.depends_on)}")
        if t.produces:
            L.append(f"produces = {_arr(t.produces)}")
        if t.branch:
            L.append(f"branch = {_q(t.branch)}")
        if t.provisional:
            L.append("provisional = true")
        if t.origin and t.origin != "template":
            L.append(f"origin = {_q(t.origin)}")

    return "\n".join(L) + "\n"


def with_task(plan: Plan, task: TaskSpec) -> Plan:
    """Return a plan with ``task`` added or replaced."""
    others = tuple(t for t in plan.tasks if t.id != task.id)
    return replace(plan, tasks=others + (task,))
