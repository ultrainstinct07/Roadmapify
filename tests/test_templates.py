from __future__ import annotations

from roadmapify import templates
from roadmapify.plan import Goal, find_cycles, validate_plan

GOAL = Goal(label="g", archetype="cli", created="2026-01-01T00:00:00Z")


def test_every_archetype_produces_a_valid_acyclic_plan():
    for name in templates.ARCHETYPES:
        plan = templates.skeleton(name, GOAL)
        assert validate_plan(plan) == [], name
        assert find_cycles(plan) == [], name
        assert plan.phases and plan.tasks


def test_every_phase_declares_what_it_ships():
    """A phase you cannot demo is a bucket, not a phase."""
    for name in templates.ARCHETYPES:
        for p in templates.skeleton(name, GOAL).phases:
            assert p.ships.strip(), f"{name}/{p.id} has no `ships`"


def test_the_complete_path_is_emitted_but_only_the_near_part_is_load_bearing():
    plan = templates.skeleton("cli", GOAL)
    active = [t for t in plan.tasks if not t.provisional]
    provisional = [t for t in plan.tasks if t.provisional]
    assert active and provisional, "both halves must exist"
    assert {t.phase for t in active} == {"P-1", "P-2"}
    assert all(int(t.phase.split("-")[1]) > 2 for t in provisional)
    assert all(p.provisional == (p.order > templates.ACTIVE_PHASE_WINDOW) for p in plan.phases)


def test_skeleton_is_deterministic():
    assert templates.skeleton("cli", GOAL) == templates.skeleton("cli", GOAL)


def test_unknown_archetype_falls_back_rather_than_failing():
    assert templates.skeleton("nonsense", GOAL).phases == \
        templates.skeleton("generic", GOAL).phases


def test_detect_archetype_is_conservative(tmp_path):
    assert templates.detect_archetype(tmp_path) == "generic"
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert templates.detect_archetype(tmp_path) == "generic", "a library is not a CLI"
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\nx = "x:main"\n')
    assert templates.detect_archetype(tmp_path) == "cli"


def test_detect_archetype_survives_a_broken_manifest(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    assert templates.detect_archetype(tmp_path) == "generic"
