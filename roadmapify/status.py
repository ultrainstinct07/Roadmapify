"""Derive status. Never store it.

``roadmap.toml`` has no status field, so this module answers "what is done, what
is ready, what is the path ahead" from two inputs and nothing else: the plan's
dependency DAG, and an append-only log of **observed** evidence.

The evidence log is the seam. P-2 ships the consumer; P-3's ``gitsync.py``
becomes nothing but a producer that appends records to
``roadmap-out/evidence.jsonl``, and nothing in this file changes when it lands.
That is what makes P-3's exit criterion — "task status flips from evidence
alone, never from a stored field" — a property of this design rather than of
code not yet written.

Today that log does not exist, so ``load_evidence`` returns ``[]`` and nothing
reads as done. **That is the output, not an apology.** P-1 is in fact finished,
and ``roadmap next`` says it is not, because no evidence says otherwise and
nothing here may invent some. The alternatives were all worse: reading a stored
field (there is none, by design), reading the filesystem for deliverables (real
evidence, but at a different confidence, and it is ``roadmap verify`` in P-5),
or treating a journal record *about* a task as a claim that it finished.

stdlib ``graphlib``. networkx is a recorded constraint violation, not a
preference.
"""

from __future__ import annotations

import graphlib
from dataclasses import dataclass
from datetime import datetime

from roadmapify.paths import EVIDENCE_FILENAME, out_path, read_jsonl
from roadmapify.project import CycleError, task_dag, unblocks
from roadmapify.plan import find_cycles

BLOCKED = "blocked"
READY = "ready"
ACTIVE = "active"
DONE_CLAIMED = "done_claimed"
DONE = "done"

#: Ascending. `provisional` is deliberately NOT a member: it is an orthogonal
#: flag, because a provisional task still has a real readiness, and a provisional
#: task carrying a commit is a signal the plan is behind the work — which a
#: PROVISIONAL status would swallow.
STATUSES = (BLOCKED, READY, ACTIVE, DONE_CLAIMED, DONE)

#: What satisfies a dependency. Requiring DONE would leave everything blocked
#: forever whenever evidence is thin, which is exactly today; `next` prints the
#: difference on its own line instead of refusing to move.
DONE_STATES = (DONE_CLAIMED, DONE)

#: Display only. The project's vocabulary is `done*` (roadmap.toml's P-4 gate:
#: "a squash-merge ... upgrades done* to done"), but an asterisk inside a data
#: value is presentation leaking into the model. The plain word `done` has to
#: mean verified, or the star reads as decoration.
LABEL = {DONE_CLAIMED: "done*"}

PHASE_STATUSES = ("not_started", "in_progress", "done")

#: Only `kind` moves a task through the lattice.
EVIDENCE_KINDS = ("start", "commit", "branch", "claim", "merge", "revert")

#: HOW the task was identified, not how likely it is that the work happened.
#: `declared` = a `Roadmap: T-09` trailer or an explicit `roadmap done`.
#: `mapped` = a branch name matched a task. `inferred` = touched files matched
#: `produces`. This is the number P-3's agreement line reports.
EVIDENCE_CONFIDENCE = ("declared", "mapped", "inferred")


@dataclass(frozen=True)
class TaskStatus:
    id: str
    status: str
    provisional: bool
    blocked_by: "tuple[str, ...]" = ()
    unblocks: int = 0
    evidence: "tuple[str, ...]" = ()
    last_seen: str = ""

    @property
    def label(self) -> str:
        return LABEL.get(self.status, self.status)


@dataclass(frozen=True)
class PhaseStatus:
    id: str
    status: str
    done: int
    total: int
    provisional: int
    ships: str = ""
    exit_criteria: str = ""


@dataclass(frozen=True)
class Snapshot:
    tasks: "tuple[TaskStatus, ...]" = ()
    phases: "tuple[PhaseStatus, ...]" = ()
    active_phase: str = ""
    ready: "tuple[str, ...]" = ()
    critical_path: "tuple[str, ...]" = ()
    remaining_path: "tuple[str, ...]" = ()
    evidence_count: int = 0
    needs_expand: bool = False

    def task(self, tid: str) -> "TaskStatus | None":
        return next((t for t in self.tasks if t.id == tid), None)

    def phase(self, pid: str) -> "PhaseStatus | None":
        return next((p for p in self.phases if p.id == pid), None)


# ── evidence ──────────────────────────────────────────────────────────────────

def load_evidence(root=None) -> "list[dict]":
    """Read the observed-facts log. Absent today; ``read_jsonl`` returns ``[]``."""
    return read_jsonl(out_path(EVIDENCE_FILENAME, root=root))


def normalize_evidence(rec: dict, *, known_tasks: "set[str] | None" = None,
                       now: "datetime | None" = None) -> "dict | None":
    """Admit one evidence record, or ``None``.

    Evidence is *observed*, not trusted. A record naming a task the plan does not
    have must not resurrect it, an unknown kind cannot move the lattice, and a
    timestamp in the future is clock skew rather than proof — which is the only
    reason ``now`` is load-bearing here.
    """
    if not isinstance(rec, dict):
        return None
    kind, task = rec.get("kind"), rec.get("task")
    if kind not in EVIDENCE_KINDS or not task:
        return None
    if known_tasks is not None and task not in known_tasks:
        return None
    ts = rec.get("ts") or ""
    if now is not None and ts:
        from roadmapify.render import parse_ts
        seen = parse_ts(ts)
        if seen is not None and seen.tzinfo and seen > now:
            return None
    return rec


# ── derivation ────────────────────────────────────────────────────────────────

def _order(plan) -> "list[str]":
    """Topological order, dependencies first. Cycles fail naming every hop."""
    cycles = find_cycles(plan)
    if cycles:
        raise CycleError(cycles)
    return list(graphlib.TopologicalSorter(
        {k: set(v) for k, v in task_dag(plan).items()}).static_order())


def status_of(plan, evidence: "list[dict] | tuple" = (), *,
              now: "datetime | None" = None) -> "dict[str, TaskStatus]":
    """Every task's derived status.

    Order-independent by construction: ``merge=union`` reorders the log's lines
    on any merge, and a status that flipped because two records swapped places
    would be worse than no status at all. Rules 1-3 are set membership; only
    readiness walks the DAG, in topological order so dependencies decide first.
    """
    known = {t.id for t in plan.tasks}
    admitted = [e for e in (normalize_evidence(r, known_tasks=known, now=now)
                            for r in evidence) if e]

    reverted = {r.get("ref") for r in admitted if r["kind"] == "revert"}
    by_task: "dict[str, list[dict]]" = {t: [] for t in known}
    for r in admitted:
        by_task[r["task"]].append(r)

    free = unblocks(plan)
    out: "dict[str, TaskStatus]" = {}
    for tid in _order(plan):
        task = plan.task(tid)
        if task is None:
            continue
        mine = by_task.get(tid, [])
        kinds = {r["kind"] for r in mine if not (r["kind"] == "merge"
                                                and r.get("ref") in reverted)}
        if "merge" in kinds:
            state, unmet = DONE, ()
        elif "claim" in kinds:
            state, unmet = DONE_CLAIMED, ()
        elif kinds & {"start", "commit", "branch"}:
            state, unmet = ACTIVE, ()
        else:
            unmet = tuple(sorted(
                d for d in task.depends_on
                if d in known and (d not in out or out[d].status not in DONE_STATES)))
            state = BLOCKED if unmet else READY
        out[tid] = TaskStatus(
            id=tid, status=state, provisional=task.provisional,
            blocked_by=unmet, unblocks=free.get(tid, 0),
            evidence=tuple(sorted(r.get("id", "") for r in mine if r.get("id"))),
            last_seen=max((r.get("ts", "") for r in mine), default=""),
        )
    return out


def ready_tasks(plan, statuses: dict, *, phase: str = "",
                limit: "int | None" = None) -> tuple:
    """Ready, load-bearing tasks, best-first.

    Ordered by how many tasks each one unblocks: "finishing this frees four
    others" is a scheduling argument, where a raw dependency count is trivia.
    Provisional tasks never appear — they are an unreviewed guess, and letting
    one into the ready list is how a roadmap starts lying about itself.
    """
    order = {p.id: p.order for p in plan.phases}
    picked = [t for t in plan.tasks
              if not t.provisional
              and statuses.get(t.id) is not None
              and statuses[t.id].status == READY
              and (not phase or t.phase == phase)]
    picked.sort(key=lambda t: (-statuses[t.id].unblocks, order.get(t.phase, 0), t.id))
    return tuple(picked[:limit] if limit else picked)


def active_phase(plan, statuses: dict):
    """The first phase with work left. A phase with no tasks cannot be active."""
    for ph in plan.ordered_phases():
        tasks = plan.tasks_of(ph.id)
        if tasks and any(statuses[t.id].status not in DONE_STATES
                         for t in tasks if t.id in statuses):
            return ph
    return None


def phase_status(plan, ph, statuses: dict) -> PhaseStatus:
    """Counts over NON-provisional tasks: an unreviewed guess must not move a
    health score in either direction."""
    tasks = plan.tasks_of(ph.id)
    load_bearing = [t for t in tasks if not t.provisional]
    done = sum(1 for t in load_bearing
               if t.id in statuses and statuses[t.id].status in DONE_STATES)
    total = len(load_bearing)
    if total and done == total:
        state = "done"
    elif done or any(t.id in statuses and statuses[t.id].status == ACTIVE
                     for t in load_bearing):
        state = "in_progress"
    else:
        state = "not_started"
    return PhaseStatus(id=ph.id, status=state, done=done, total=total,
                       provisional=len(tasks) - total,
                       ships=ph.ships, exit_criteria=ph.exit_criteria)


def _longest(dag: "dict[str, tuple[str, ...]]") -> "tuple[str, ...]":
    """Longest chain by task count.

    Every task has unit cost, because the plan declares no estimates and
    inventing them would be the same class of lie as a stored status. Both
    tie-breaks resolve to the lexicographically smallest id: two equal chains
    must never swap between runs, since the derived layer is byte-compared.
    """
    if not dag:
        return ()
    try:
        order = list(graphlib.TopologicalSorter({k: set(v) for k, v in dag.items()}
                                                ).static_order())
    except graphlib.CycleError as exc:  # pragma: no cover - find_cycles runs first
        raise CycleError([list(exc.args[1])]) from exc
    depth: "dict[str, int]" = {}
    parent: "dict[str, str | None]" = {}
    for n in order:
        best_d, best_p = 0, None
        for d in sorted(dag.get(n, ())):
            if depth.get(d, 0) > best_d:
                best_d, best_p = depth[d], d
        depth[n], parent[n] = best_d + 1, best_p
    end = min(depth, key=lambda n: (-depth[n], n))
    chain = [end]
    while parent.get(chain[-1]):
        chain.append(parent[chain[-1]])
    return tuple(reversed(chain))


def critical_path(plan, *, include_provisional: bool = False) -> "tuple[str, ...]":
    """The longest dependency chain. Provisional tasks are excluded by default:
    the template chains them within their own phase, so a run of unreviewed
    guesses could otherwise outrank the real work and claim to be the path."""
    return _longest(task_dag(plan, include_provisional=include_provisional))


def remaining_path(plan, statuses: dict, *,
                   include_provisional: bool = False) -> "tuple[str, ...]":
    """The critical path with finished work dropped — the path ahead."""
    return tuple(t for t in critical_path(plan, include_provisional=include_provisional)
                 if t not in statuses or statuses[t].status not in DONE_STATES)


def snapshot(plan, evidence: "list[dict] | tuple" = (), *,
             now: "datetime | None" = None) -> Snapshot:
    """Everything `roadmap next` and `roadmap tree` need, derived in one pass.

    ``now`` is load-bearing (it is the clock-skew guard) but never reaches the
    output: the render layer receives ``now`` separately, so nothing here can
    embed a timestamp and make the derived files differ between runs.
    """
    statuses = status_of(plan, evidence, now=now)
    ph = active_phase(plan, statuses)
    ready = tuple(t.id for t in ready_tasks(plan, statuses))
    needs_expand = bool(
        ph and not any(t for t in plan.tasks_of(ph.id)
                       if not t.provisional and statuses[t.id].status not in DONE_STATES))
    return Snapshot(
        tasks=tuple(statuses[t.id] for t in sorted(plan.tasks, key=lambda t: t.id)
                    if t.id in statuses),
        phases=tuple(phase_status(plan, p, statuses) for p in plan.ordered_phases()),
        active_phase=ph.id if ph else "",
        ready=ready,
        critical_path=critical_path(plan),
        remaining_path=remaining_path(plan, statuses),
        evidence_count=len([e for e in evidence
                            if normalize_evidence(e, known_tasks={t.id for t in plan.tasks},
                                                  now=now)]),
        needs_expand=needs_expand,
    )
