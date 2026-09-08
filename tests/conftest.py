"""Shared fixtures.

``_sandbox_home`` is autouse and non-negotiable. `roadmap install` writes a
project ``CLAUDE.md`` / ``AGENTS.md`` today and P-7's session hook will write
into ``~/.claude/``; a test that reaches the developer's real config once is a
test that has already done the damage, and the fixture has to predate the code
that would do it. graphify learned this the hard way (its #2168).

It also pins ``ROADMAP_AUTHOR=test``, which is what makes the content-hashed
record ids identical on every machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _sandbox_home(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    for var in ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "ROADMAP_OUT",
                "ROADMAP_NO_SESSION", "ROADMAP_AUTHOR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Deterministic author so content-hashed ids are stable across machines.
    monkeypatch.setenv("ROADMAP_AUTHOR", "test")
    yield home


@pytest.fixture
def project(tmp_path):
    """An empty project directory with no git repo — the hardest starting tier."""
    d = tmp_path / "proj"
    d.mkdir()
    return d


@pytest.fixture
def seeded(project):
    """A project with a plan and a few memory records."""
    from roadmapify import journal, templates
    from roadmapify.paths import write_text_atomic
    from roadmapify.plan import Goal, emit_plan, plan_path

    goal = Goal(label="ship a thing", why="because the old thing rots",
                archetype="cli", created="2026-01-01T00:00:00Z",
                success_criteria=("it installs from a clean machine",))
    plan = templates.skeleton("cli", goal)
    write_text_atomic(plan_path(project), emit_plan(plan))
    journal.append(journal.record(
        "decision", "status is derived from evidence, never stored",
        ts="2026-01-02T00:00:00Z", author="test",
        why="a hand-edited status field is the exact thing that rots",
        rejected=["a status field in roadmap.toml: it goes stale the day you write it"],
    ), project)
    journal.append(journal.record(
        "constraint", "no network calls at import time — the CLI must start offline",
        ts="2026-01-03T00:00:00Z", author="test",
    ), project)
    return project


@pytest.fixture
def run(monkeypatch):
    """Invoke the CLI in-process and return (exit_code, stdout)."""
    import io
    import sys

    from roadmapify.cli import dispatch

    def _run(*argv: str) -> "tuple[int, str]":
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        try:
            code = dispatch(list(argv))
        finally:
            monkeypatch.undo()
        return code, buf.getvalue()

    return _run


@pytest.fixture(autouse=True)
def _no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    import roadmapify.render as render
    monkeypatch.setattr(render, "_NO_COLOR", True)
