"""Project the plan and the memory into one graph.

The plan is a DAG and the journal is a set of records about its nodes, but
neither is stored as a graph. This module is where they become one, in
**graphify's node contract** — the same `{nodes, edges, hyperedges}` shape its
extractors emit — so graphify's exporters and query tools read our graph without
a translation layer. The contract is documented in graphify's
``references/extraction-spec.md``; the parts that bind us are:

- ``file_type`` MUST be one of ``code | document | paper | image | rationale |
  concept``. It is the only enumerated field graphify calls mandatory.
- Node ids are ``[a-z0-9_]`` only, derived from the source path with the
  extension dropped. Never an ordinal suffix: an id must be deterministic from
  the entity alone, or the same thing extracted twice becomes two ghost nodes.
- ``relation`` comes from a fixed vocabulary, and every node attribute must be a
  scalar — graphify's GraphML export cannot serialise a nested list or dict.

We add our own ``roadmap_*`` scalar keys alongside. graphify reads nodes with
``.get()`` and copies unknown attributes through, so the extension is safe.

**There is no clock in this module.** The project law is ``(data, now) -> value``
with ``now`` passed in, and the reason for that law is byte-reproducibility;
having no clock input at all is the stronger form of the same guarantee. Delete
``roadmap-out/`` and the regenerated ``graph.json`` is byte-identical, which is
what makes "everything derived is disposable" a checkable claim rather than a
hope.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from roadmapify.paths import GRAPH_FILENAME, JOURNAL_FILENAME, PLAN_FILENAME, ROADMAP_OUT_NAME
from roadmapify.paths import out_path, write_json_atomic
from roadmapify.plan import find_cycles, split_deliverable

#: Both are module constants, deliberately NOT derived from ``ROADMAP_OUT``. If
#: the journal's source path followed the env var, the same plan checked out in a
#: worktree with ``ROADMAP_OUT=roadmap-out-feature`` would project different node
#: ids, and byte-reproducibility would be true only per-machine.
PLAN_SOURCE = PLAN_FILENAME
JOURNAL_SOURCE = f"{ROADMAP_OUT_NAME}/{JOURNAL_FILENAME}"

#: Ordered: the serialiser sorts by this, so the file is stable.
NODE_KINDS = (
    "goal", "phase", "milestone", "task", "deliverable",
    "decision", "constraint", "risk", "question", "note", "rejection",
)

EDGE_KINDS = (
    "serves_goal", "ships", "precedes", "in_phase",
    "depends_on", "produces", "about", "rejected_by", "supersedes",
)

#: graphify's six, and only its six. "rationale" is its own word for the WHY
#: layer — decisions, trade-offs, design intent — which is exactly the journal.
FILE_TYPE = {
    "goal": "concept",
    "phase": "concept",
    "milestone": "concept",
    "task": "document",
    "deliverable": "code",
    "decision": "rationale",
    "constraint": "rationale",
    "risk": "rationale",
    "question": "rationale",
    "note": "rationale",
    "rejection": "rationale",
}

GRAPHIFY_RELATION = {
    "serves_goal": "implements",
    "ships": "implements",
    "precedes": "references",
    "in_phase": "implements",
    "depends_on": "references",
    "produces": "implements",
    "about": "rationale_for",
    "rejected_by": "cites",
    "supersedes": "references",
}

#: Journal kinds that become nodes. Sessions are deliberately absent: they are
#: operational churn that grows without bound and carries no design content, and
#: ``journal.open_sessions()`` already answers every question about them.
RECORD_KINDS = ("decision", "constraint", "risk", "question", "note")

_ID_SAFE = re.compile(r"[^a-z0-9]+")


class CycleError(ValueError):
    """The plan's task DAG has a cycle, so it has no order and no 'next task'."""

    def __init__(self, cycles: "list[list[str]]"):
        self.cycles = cycles
        super().__init__("; ".join(format_cycles(cycles)))


def format_cycles(cycles: "list[list[str]]") -> "list[str]":
    """One line per cycle, naming every hop.

    P-2's exit criterion is "a dependency cycle fails the build naming every
    hop", and the plural is the point: ``graphlib.CycleError`` carries exactly
    one cycle, so fixing a tangled plan through it means one rebuild per cycle.
    ``plan.find_cycles`` finds them all in a single pass, so we report them all.
    """
    return [f"dependency cycle: {' -> '.join(c)}" for c in cycles]


def _slug(text: str) -> str:
    return _ID_SAFE.sub("_", (text or "").lower()).strip("_")


def node_id(ref: str, source_file: str) -> str:
    """graphify's ``{stem}_{entity}`` id: the source path, extension dropped.

    ``("T-09", "roadmap.toml")`` -> ``roadmap_t_09``. Deterministic from the
    reference alone, because graphify treats a non-deterministic id as a bug
    that silently doubles the graph.
    """
    stem = source_file.rsplit(".", 1)[0] if "." in source_file.rsplit("/", 1)[-1] else source_file
    return f"{_slug(stem)}_{_slug(ref)}".strip("_")


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    file_type: str
    source_file: str
    source_location: "str | None" = None
    source_url: "str | None" = None
    captured_at: "str | None" = None
    author: "str | None" = None
    contributor: "str | None" = None
    rationale: str = ""
    kind: str = ""
    ref: str = ""
    phase: str = ""
    order: int = 0
    provisional: bool = False
    origin: str = ""
    trust: str = ""
    superseded: bool = False


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str
    source_file: str
    source_location: "str | None" = None
    confidence: str = "EXTRACTED"
    confidence_score: float = 1.0
    weight: float = 1.0

    @property
    def relation(self) -> str:
        return GRAPHIFY_RELATION[self.kind]


@dataclass(frozen=True)
class Hyperedge:
    id: str
    label: str
    nodes: "tuple[str, ...]"
    relation: str = "participate_in"
    confidence: str = "EXTRACTED"
    confidence_score: float = 1.0
    source_file: str = PLAN_SOURCE


@dataclass(frozen=True)
class Graph:
    nodes: "tuple[Node, ...]" = ()
    edges: "tuple[Edge, ...]" = ()
    hyperedges: "tuple[Hyperedge, ...]" = ()
    cycles: "tuple[tuple[str, ...], ...]" = ()
    _by_id: dict = field(default_factory=dict, compare=False, repr=False)

    def node(self, node_id_: str) -> "Node | None":
        return next((n for n in self.nodes if n.id == node_id_), None)

    def by_ref(self, ref: str) -> "Node | None":
        return next((n for n in self.nodes if n.ref == ref), None)

    def of_kind(self, kind: str) -> "tuple[Node, ...]":
        return tuple(n for n in self.nodes if n.kind == kind)

    def out_edges(self, node_id_: str) -> "tuple[Edge, ...]":
        return tuple(e for e in self.edges if e.source == node_id_)

    def in_edges(self, node_id_: str) -> "tuple[Edge, ...]":
        return tuple(e for e in self.edges if e.target == node_id_)


# ── the DAG primitives ────────────────────────────────────────────────────────
#
# These were private helpers inside export.py, which needed them for the HTML
# page's "frees N" chip. status.py ranks the ready list by the same number, and
# two definitions of "how many tasks does finishing this unblock" would
# eventually disagree in a way nobody notices — so they live here and export.py
# imports them.

def dependents(plan) -> "dict[str, list[str]]":
    """Reverse the dependency edges: task -> the tasks waiting on it."""
    out: "dict[str, list[str]]" = {t.id: [] for t in plan.tasks}
    for t in plan.tasks:
        for dep in t.depends_on:
            if dep in out:
                out[dep].append(t.id)
    return out


def unblocks(plan, deps: "dict[str, list[str]] | None" = None) -> "dict[str, int]":
    """How many tasks transitively wait on each task.

    This is the number worth putting on a card: "finishing this frees four
    others" is a scheduling argument, where a raw dependency count is trivia.
    """
    deps = dependents(plan) if deps is None else deps
    memo: "dict[str, set[str]]" = {}

    def walk(tid: str, seen: "set[str]") -> "set[str]":
        if tid in memo:
            return memo[tid]
        if tid in seen:  # defensive; project() already rejects cycles
            return set()
        acc: "set[str]" = set()
        for nxt in deps.get(tid, ()):
            acc.add(nxt)
            acc |= walk(nxt, seen | {tid})
        memo[tid] = acc
        return acc

    return {t.id: len(walk(t.id, set())) for t in plan.tasks}


def task_dag(plan, *, include_provisional: bool = True) -> "dict[str, tuple[str, ...]]":
    """``{task id: its dependencies}``, ready for ``graphlib.TopologicalSorter``.

    Dependencies naming a task that is not in the plan are dropped rather than
    invented — ``validate_plan`` already reports them, and a graph that conjures
    a node to satisfy an edge is worse than one that omits the edge.
    """
    known = {t.id for t in plan.tasks
             if include_provisional or not t.provisional}
    return {t.id: tuple(sorted(d for d in t.depends_on if d in known))
            for t in plan.tasks if t.id in known}


# ── projection ────────────────────────────────────────────────────────────────

def project(plan, records: "list[dict] | tuple" = (),
            rejections: "list[dict] | tuple" = (), *,
            strict: bool = True) -> Graph:
    """Plan + memory -> one graph. Pure: no clock, no I/O.

    ``strict=True`` raises :class:`CycleError` on a tangled plan. ``roadmap
    tree`` passes ``strict=False``, because being unable to show a broken plan
    is exactly when you most need to look at it; the cycles come back on
    :attr:`Graph.cycles` instead.
    """
    cycles = find_cycles(plan)
    if cycles and strict:
        raise CycleError(cycles)

    nodes: "list[Node]" = []
    edges: "list[Edge]" = []
    hyper: "list[Hyperedge]" = []

    goal_id = node_id("G-0", PLAN_SOURCE)
    nodes.append(Node(
        id=goal_id, label=plan.goal.label, file_type=FILE_TYPE["goal"],
        source_file=PLAN_SOURCE, captured_at=plan.goal.created or None,
        rationale=plan.goal.why, kind="goal", ref="G-0",
    ))

    ordered = plan.ordered_phases()
    for i, ph in enumerate(ordered):
        pid = node_id(ph.id, PLAN_SOURCE)
        nodes.append(Node(
            id=pid, label=ph.label, file_type=FILE_TYPE["phase"],
            source_file=PLAN_SOURCE, source_location=str(ph.line) if ph.line else None,
            rationale=ph.intent, kind="phase", ref=ph.id, phase=ph.id,
            order=ph.order, provisional=ph.provisional,
        ))
        edges.append(Edge(pid, goal_id, "serves_goal", PLAN_SOURCE))

        # The milestone is the phase's `ships` line: the checkable outcome,
        # separated from the phase's name so it can be pointed at on its own.
        if ph.ships:
            mid = node_id(ph.milestone_id, PLAN_SOURCE)
            nodes.append(Node(
                id=mid, label=ph.ships, file_type=FILE_TYPE["milestone"],
                source_file=PLAN_SOURCE, source_location=str(ph.line) if ph.line else None,
                rationale=ph.exit_criteria, kind="milestone", ref=ph.milestone_id,
                phase=ph.id, order=ph.order, provisional=ph.provisional,
            ))
            edges.append(Edge(pid, mid, "ships", PLAN_SOURCE))
        if i:
            edges.append(Edge(node_id(ordered[i - 1].id, PLAN_SOURCE), pid,
                              "precedes", PLAN_SOURCE))

    seen_deliverables: "set[str]" = set()
    for t in sorted(plan.tasks, key=lambda t: (t.phase, t.id)):
        tid = node_id(t.id, PLAN_SOURCE)
        nodes.append(Node(
            id=tid, label=t.label, file_type=FILE_TYPE["task"],
            source_file=PLAN_SOURCE, source_location=str(t.line) if t.line else None,
            rationale=t.intent, kind="task", ref=t.id, phase=t.phase,
            provisional=t.provisional, origin=t.origin,
        ))
        if plan.phase(t.phase):
            edges.append(Edge(tid, node_id(t.phase, PLAN_SOURCE), "in_phase", PLAN_SOURCE))
        for dep in t.depends_on:
            if plan.task(dep):
                edges.append(Edge(tid, node_id(dep, PLAN_SOURCE), "depends_on",
                                  PLAN_SOURCE, str(t.line) if t.line else None))
        for spec in t.produces:
            kind, value = split_deliverable(spec)
            did = f"{node_id('deliverable', PLAN_SOURCE)}_{_slug(spec)}"
            if did not in seen_deliverables:
                seen_deliverables.add(did)
                nodes.append(Node(
                    id=did, label=value or spec,
                    # A deliverable is only "code" when it is; a `cmd:` is not a
                    # file, and mislabelling it breaks graphify's enum.
                    file_type="code" if kind in ("file", "glob", "symbol", "test") else "concept",
                    source_file=PLAN_SOURCE, kind="deliverable", ref=spec,
                    phase=t.phase, provisional=t.provisional,
                ))
            edges.append(Edge(tid, did, "produces", PLAN_SOURCE))

    if len(plan.tasks):
        for ph in ordered:
            members = tuple(node_id(t.id, PLAN_SOURCE)
                            for t in sorted(plan.tasks_of(ph.id), key=lambda t: t.id))
            if len(members) >= 3:
                hyper.append(Hyperedge(
                    id=f"hyper_{_slug(ph.id)}", label=f"{ph.id} {ph.label}",
                    nodes=members))

    _project_memory(nodes, edges, records, rejections)

    nodes.sort(key=lambda n: (NODE_KINDS.index(n.kind), n.id))
    edges.sort(key=lambda e: (EDGE_KINDS.index(e.kind), e.source, e.target))
    ids = {n.id for n in nodes}
    # A dangling endpoint makes graphify's loader invent a ghost node.
    edges = [e for e in edges if e.source in ids and e.target in ids]
    return Graph(tuple(nodes), tuple(edges), tuple(hyper),
                 tuple(tuple(c) for c in cycles))


def _project_memory(nodes, edges, records, rejections) -> None:
    """Journal records and their rejections, and how they attach to the plan."""
    from roadmapify.journal import TRUST_FOREIGN

    superseded: "set[str]" = set()
    for r in records:
        if r.get("supersedes"):
            superseded.add(r["supersedes"])

    plan_refs = {n.ref: n.id for n in nodes}

    for r in records:
        if r.get("kind") not in RECORD_KINDS:
            continue
        rid = node_id(r["id"], JOURNAL_SOURCE)
        nodes.append(Node(
            id=rid, label=r.get("text", ""), file_type=FILE_TYPE[r["kind"]],
            # journal.jsonl is append-only under merge=union, so a line number is
            # not stable across a merge and would break reproducibility.
            source_file=JOURNAL_SOURCE, source_location=None,
            captured_at=r.get("ts"), author=r.get("author"),
            rationale=r.get("why", "") or "", kind=r["kind"], ref=r["id"],
            trust=r.get("trust", ""), superseded=r["id"] in superseded,
        ))
        for target in r.get("about") or ():
            if target in plan_refs:
                edges.append(Edge(rid, plan_refs[target], "about", JOURNAL_SOURCE))
        if r.get("supersedes") and any(x["id"] == r["supersedes"] for x in records):
            edges.append(Edge(rid, node_id(r["supersedes"], JOURNAL_SOURCE),
                              "supersedes", JOURNAL_SOURCE))

    for x in rejections:
        xid = node_id(x["id"], JOURNAL_SOURCE)
        nodes.append(Node(
            id=xid, label=x.get("alt", ""), file_type=FILE_TYPE["rejection"],
            source_file=JOURNAL_SOURCE, captured_at=x.get("ts"),
            author=x.get("author"), rationale=x.get("reason", "") or "",
            kind="rejection", ref=x["id"], trust=x.get("trust", ""),
            superseded=x.get("parent") in superseded,
        ))
        edges.append(Edge(xid, node_id(x["parent"], JOURNAL_SOURCE),
                          "rejected_by", JOURNAL_SOURCE))
    # Foreign records ARE projected, marked. The trust boundary governs
    # enforcement (`roadmap check` will not block on one), not visibility.
    assert TRUST_FOREIGN


# ── serialisation ─────────────────────────────────────────────────────────────

def to_json(graph: Graph) -> dict:
    """graphify's exchange shape. Key order is fixed: the file is byte-compared."""
    return {
        "schema": 1,
        "generator": "roadmapify",
        "cycles": [list(c) for c in graph.cycles],
        "nodes": [{
            "id": n.id, "label": n.label, "file_type": n.file_type,
            "source_file": n.source_file, "source_location": n.source_location,
            "source_url": n.source_url, "captured_at": n.captured_at,
            "author": n.author, "contributor": n.contributor,
            "rationale": n.rationale,
            "roadmap_kind": n.kind, "roadmap_id": n.ref, "roadmap_phase": n.phase,
            "roadmap_order": n.order, "roadmap_provisional": n.provisional,
            "roadmap_origin": n.origin, "roadmap_trust": n.trust,
            "roadmap_superseded": n.superseded,
        } for n in graph.nodes],
        "edges": [{
            "source": e.source, "target": e.target, "relation": e.relation,
            "confidence": e.confidence, "confidence_score": e.confidence_score,
            "source_file": e.source_file, "source_location": e.source_location,
            "weight": e.weight, "roadmap_edge": e.kind,
        } for e in graph.edges],
        "hyperedges": [{
            "id": h.id, "label": h.label, "nodes": list(h.nodes),
            "relation": h.relation, "confidence": h.confidence,
            "confidence_score": h.confidence_score, "source_file": h.source_file,
        } for h in graph.hyperedges],
        "input_tokens": 0,
        "output_tokens": 0,
    }


def write_graph(graph: Graph, root=None):
    """Write ``graph.json``. The caller holds ``derive_lock``."""
    path = out_path(GRAPH_FILENAME, root=root)
    write_json_atomic(path, to_json(graph))
    return path


def load_graph(root=None) -> "dict | None":
    path = out_path(GRAPH_FILENAME, root=root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
