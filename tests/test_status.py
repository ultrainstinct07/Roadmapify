"""Status is derived, never stored.

The floor these tests defend is the honest one: with no evidence, nothing reads
as done. Every richer answer available to P-2 would have been a fabricated
status source — a stored field (there is none), the filesystem (that is
`roadmap verify`, P-5), or a journal record *about* a task (a decision is not a
completion).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from roadmapify import status
from roadmapify.plan import Goal, Plan, PhaseSpec, TaskSpec, load_plan
from roadmapify.project import CycleError

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _plan(tasks, phases=None):
    phases = phases or (PhaseSpec(id="P-1", label="one", order=1, ships="it ships"),)
    return Plan(schema=1, goal=Goal(label="g", why="w", archetype="cli"),
                phases=tuple(phases), tasks=tuple(tasks))


def _ev(task, kind, **kw):
    return {"id": f"E-{task}-{kind}", "task": task, "kind": kind,
            "ts": "2026-01-01T00:00:00Z", **kw}


def test_with_no_evidence_only_tasks_without_dependencies_are_ready():
    """The honest floor. Anything richer on an empty evidence log is invented."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",))])
    s = status.snapshot(p, [], now=NOW)
    assert s.ready == ("T-01",)
    assert s.task("T-02").status == status.BLOCKED
    assert s.task("T-02").blocked_by == ("T-01",)
    assert not any(t.status in status.DONE_STATES for t in s.tasks)
    assert s.evidence_count == 0


def test_load_evidence_returns_empty_when_the_file_does_not_exist(project):
    """Today's reality, asserted rather than assumed."""
    assert status.load_evidence(project) == []


def test_a_merge_makes_a_task_done_and_unblocks_its_dependents():
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",))])
    s = status.snapshot(p, [_ev("T-01", "merge", ref="abc")], now=NOW)
    assert s.task("T-01").status == status.DONE
    assert s.ready == ("T-02",)


def test_a_claim_alone_never_reaches_done():
    """P-3's exit criterion in miniature: a self-report is a claim, not a fact."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1")])
    s = status.snapshot(p, [_ev("T-01", "claim")], now=NOW)
    assert s.task("T-01").status == status.DONE_CLAIMED
    assert s.task("T-01").label == "done*"


def test_a_claim_still_unblocks_a_dependent():
    """Refusing to unblock until the merge lands would leave everything blocked
    whenever evidence is thin, which is every project on day one."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",))])
    s = status.snapshot(p, [_ev("T-01", "claim")], now=NOW)
    assert s.ready == ("T-02",)


def test_status_does_not_depend_on_evidence_order():
    """merge=union reorders the log's lines, so a status that flipped when two
    records swapped places would be worse than no status."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1")])
    ev = [_ev("T-01", "commit"), _ev("T-01", "merge", ref="abc"), _ev("T-01", "claim")]
    first = status.status_of(p, ev, now=NOW)["T-01"].status
    assert first == status.status_of(p, list(reversed(ev)), now=NOW)["T-01"].status
    assert first == status.DONE


def test_a_revert_cancels_the_merge_it_names():
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1")])
    ev = [_ev("T-01", "merge", ref="abc"), _ev("T-01", "revert", ref="abc")]
    assert status.status_of(p, ev, now=NOW)["T-01"].status != status.DONE


def test_evidence_dated_after_now_is_ignored():
    """Clock skew is not evidence. This is the only reason `now` is load-bearing
    here rather than decorative."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1")])
    future = (NOW + timedelta(days=2)).isoformat().replace("+00:00", "Z")
    s = status.snapshot(p, [_ev("T-01", "merge", ref="a", ts=future)], now=NOW)
    assert s.task("T-01").status != status.DONE
    assert s.evidence_count == 0


@pytest.mark.parametrize("bad", [
    {"task": "T-99", "kind": "merge"},
    {"task": "T-01", "kind": "teleported"},
    {"kind": "merge"},
    "not a record",
])
def test_unusable_evidence_is_dropped(bad):
    """Evidence is observed, not trusted: a stale trailer naming a deleted task
    must not resurrect it, and an unknown kind cannot move the lattice."""
    assert status.normalize_evidence(bad, known_tasks={"T-01"}, now=NOW) is None


def test_provisional_tasks_get_a_status_but_never_appear_in_ready():
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1", provisional=True)])
    s = status.snapshot(p, [], now=NOW)
    assert s.task("T-01").status == status.READY
    assert s.task("T-01").provisional is True
    assert s.ready == ()


def test_a_provisional_task_with_a_commit_reads_active_rather_than_hidden():
    """The plan is behind the work. Swallowing that into a PROVISIONAL status is
    how a roadmap goes quietly stale."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1", provisional=True)])
    s = status.snapshot(p, [_ev("T-01", "commit")], now=NOW)
    assert s.task("T-01").status == status.ACTIVE
    assert s.task("T-01").provisional is True


def test_provisional_tasks_are_not_in_the_phase_denominator():
    """An unreviewed guess must not move a health score in either direction."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", provisional=True)])
    assert status.snapshot(p, [], now=NOW).phase("P-1").total == 1


def test_the_active_phase_is_the_first_with_unfinished_work():
    phases = (PhaseSpec(id="P-1", label="one", order=1, ships="a"),
              PhaseSpec(id="P-2", label="two", order=2, ships="b"))
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-2")], phases)
    assert status.snapshot(p, [], now=NOW).active_phase == "P-1"
    moved = status.snapshot(p, [_ev("T-01", "merge", ref="x")], now=NOW)
    assert moved.active_phase == "P-2"


def test_a_phase_with_no_tasks_is_never_the_active_phase():
    phases = (PhaseSpec(id="P-1", label="empty", order=1, ships="a"),
              PhaseSpec(id="P-2", label="real", order=2, ships="b"))
    p = _plan([TaskSpec(id="T-02", label="b", phase="P-2")], phases)
    assert status.snapshot(p, [], now=NOW).active_phase == "P-2"


def test_a_phase_whose_remaining_work_is_all_provisional_asks_to_be_expanded():
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1", provisional=True)])
    assert status.snapshot(p, [], now=NOW).needs_expand is True


def test_the_critical_path_is_the_longest_chain_when_nothing_has_a_duration():
    """Unit weights are the only honest reading of a plan with no estimates."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",)),
               TaskSpec(id="T-03", label="c", phase="P-1", depends_on=("T-02",)),
               TaskSpec(id="T-04", label="d", phase="P-1")])
    assert status.critical_path(p) == ("T-01", "T-02", "T-03")


def test_the_critical_path_breaks_ties_on_the_smallest_id():
    """Two equal chains must never swap between runs: the derived layer is
    byte-compared, so a non-deterministic path breaks reproducibility."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",)),
               TaskSpec(id="T-03", label="c", phase="P-1", depends_on=("T-01",))])
    assert status.critical_path(p) == ("T-01", "T-02")
    assert status.critical_path(p) == status.critical_path(p)


def test_the_critical_path_excludes_provisional_tasks_by_default():
    """A chain of unreviewed guesses must never claim to be the critical path."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", provisional=True,
                        depends_on=("T-01",)),
               TaskSpec(id="T-03", label="c", phase="P-1", provisional=True,
                        depends_on=("T-02",))])
    assert status.critical_path(p) == ("T-01",)
    assert len(status.critical_path(p, include_provisional=True)) == 3


def test_the_remaining_path_drops_what_is_already_done():
    """`next` prints the path ahead, not the path behind."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1"),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",))])
    s = status.snapshot(p, [_ev("T-01", "merge", ref="x")], now=NOW)
    assert s.remaining_path == ("T-02",)


def test_ready_tasks_are_ordered_by_how_many_tasks_they_unblock():
    """"Finishing this frees four others" is a scheduling argument; a raw
    dependency count is trivia."""
    p = _plan([TaskSpec(id="T-01", label="lonely", phase="P-1"),
               TaskSpec(id="T-02", label="hub", phase="P-1"),
               TaskSpec(id="T-03", label="c", phase="P-1", depends_on=("T-02",)),
               TaskSpec(id="T-04", label="d", phase="P-1", depends_on=("T-02",))])
    assert status.snapshot(p, [], now=NOW).ready[0] == "T-02"


def test_a_cycle_fails_the_snapshot_naming_every_hop():
    """The same guarantee `build` gives, reached through `next`."""
    p = _plan([TaskSpec(id="T-01", label="a", phase="P-1", depends_on=("T-02",)),
               TaskSpec(id="T-02", label="b", phase="P-1", depends_on=("T-01",))])
    with pytest.raises(CycleError) as exc:
        status.snapshot(p, [], now=NOW)
    assert "T-01" in str(exc.value) and "T-02" in str(exc.value)


def test_nothing_in_status_reads_the_clock():
    """`now` is passed in so the derived layer is byte-stable; a hidden clock
    read is exactly what makes 'the graph is derived' an uncheckable claim."""
    from pathlib import Path
    src = (Path(status.__file__)).read_text(encoding="utf-8")
    assert "datetime.now" not in src and "time.time" not in src


def test_the_snapshot_is_identical_for_identical_inputs(seeded):
    p = load_plan(seeded)
    assert status.snapshot(p, [], now=NOW) == status.snapshot(p, [], now=NOW)
