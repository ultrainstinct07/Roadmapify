from __future__ import annotations

import pytest

from roadmapify import templates
from roadmapify.plan import (
    Goal,
    Plan,
    PhaseSpec,
    TaskSpec,
    emit_plan,
    find_cycles,
    parse_plan,
    split_deliverable,
    validate_plan,
)

GOAL = Goal(label="ship a thing", why="because", archetype="cli",
            created="2026-01-01T00:00:00Z", success_criteria=("it installs",))


def test_round_trip_is_byte_stable():
    """`roadmap build` claims its output is derived; this is the proof for the plan."""
    plan = templates.skeleton("cli", GOAL)
    text = emit_plan(plan)
    assert parse_plan(text) == plan
    assert emit_plan(parse_plan(text)) == text


def test_quotes_and_newlines_survive_the_round_trip():
    goal = Goal(label='a "quoted" goal', why="line one\nline two\ttabbed",
                statement="back\\slash", archetype="generic")
    plan = Plan(goal=goal)
    assert parse_plan(emit_plan(plan)).goal == goal


def test_there_is_no_status_field_anywhere():
    """The central design invariant: a hand-written status is exactly what rots.

    The emitted file may TALK about status in its header comment; what it must
    never do is provide a key someone can set.
    """
    plan = templates.skeleton("cli", GOAL)
    text = emit_plan(plan)
    assignments = [ln.split("=")[0].strip() for ln in text.splitlines()
                   if "=" in ln and not ln.lstrip().startswith("#")]
    assert "status" not in assignments
    assert not any(hasattr(t, "status") for t in plan.tasks)
    assert not any(hasattr(p, "status") for p in plan.phases)


def test_source_locations_are_recovered_from_the_raw_text():
    """tomllib discards positions; validate errors have to name a line to be fixable."""
    plan = parse_plan(emit_plan(templates.skeleton("cli", GOAL)))
    assert all(p.line > 0 for p in plan.phases)
    assert all(t.line > 0 for t in plan.tasks)


def test_duplicate_task_id_names_both_lines():
    """This is the failure a sequential id scheme produces on a branch merge."""
    text = emit_plan(Plan(goal=GOAL, phases=(PhaseSpec("P-1", "One", 1),), tasks=(
        TaskSpec("T-01", "a", "P-1"), TaskSpec("T-01", "b", "P-1"))))
    # emit dedups by construction, so build the collision the way a merge does
    text = text.replace('label = "b"', 'label = "b"')
    doubled = text + '\n[[task]]\nid = "T-01"\nlabel = "c"\nphase = "P-1"\n'
    errors = validate_plan(parse_plan(doubled))
    dupes = [e for e in errors if "duplicate task id" in e]
    assert dupes, errors
    assert "first seen at line" in dupes[0]
    assert "content-hashed" in dupes[0], "the error must say how to avoid recurring"


def test_validate_catches_unknown_references():
    plan = Plan(goal=GOAL, phases=(PhaseSpec("P-1", "One", 1),),
                tasks=(TaskSpec("T-01", "a", "P-9", depends_on=("T-99",)),))
    errors = " ".join(validate_plan(plan))
    assert "unknown phase" in errors and "unknown task" in errors


def test_validate_requires_a_goal():
    assert any("label is required" in e for e in validate_plan(Plan(goal=Goal(label=""))))


def test_bad_toml_raises_a_readable_error():
    with pytest.raises(ValueError, match="not valid TOML"):
        parse_plan("[goal\nlabel =")


def test_find_cycles_reports_every_hop():
    plan = Plan(goal=GOAL, phases=(PhaseSpec("P-1", "One", 1),), tasks=(
        TaskSpec("T-01", "a", "P-1", depends_on=("T-03",)),
        TaskSpec("T-02", "b", "P-1", depends_on=("T-01",)),
        TaskSpec("T-03", "c", "P-1", depends_on=("T-02",)),
    ))
    cycles = find_cycles(plan)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"T-01", "T-02", "T-03"}


def test_no_cycle_on_a_diamond():
    plan = Plan(goal=GOAL, phases=(PhaseSpec("P-1", "One", 1),), tasks=(
        TaskSpec("T-01", "a", "P-1"),
        TaskSpec("T-02", "b", "P-1", depends_on=("T-01",)),
        TaskSpec("T-03", "c", "P-1", depends_on=("T-01",)),
        TaskSpec("T-04", "d", "P-1", depends_on=("T-02", "T-03")),
    ))
    assert find_cycles(plan) == []


def test_split_deliverable():
    assert split_deliverable("file:a/b.py") == ("file", "a/b.py")
    assert split_deliverable("symbol:Auth.login") == ("symbol", "Auth.login")
    assert split_deliverable("a/b.py") == ("file", "a/b.py")
    # An unknown prefix is a path, not a kind — "C:/x" must not become kind "C".
    assert split_deliverable("C:/x") == ("file", "C:/x")


def test_empty_plan_parses():
    assert parse_plan("schema = 1\n[goal]\nlabel = \"x\"\n").goal.label == "x"


def test_a_deliverable_spec_with_leading_whitespace_still_names_its_kind():
    """`_tuple`'s scalar branch does not strip, so `produces = "  file: a.py"`
    reaches split_deliverable whole. Without the strip the kind prefix becomes
    part of the path and verify reports a missing file named 'file: a.py' — an
    accusation about a file nobody ever meant to create."""
    assert split_deliverable("  file: a.py") == ("file", "a.py")
    assert split_deliverable("notes:2026.md") == ("file", "notes:2026.md")
