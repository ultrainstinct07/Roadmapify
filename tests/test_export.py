"""The HTML export.

The page is a derived artifact like every other: deletable, regenerable, and
byte-stable for fixed inputs. It also has one hard constraint the other
renderers do not — it must open on a machine with no network, because the first
project it was built for is an air-gapped security gateway.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from roadmapify import export, journal, templates
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)
GOAL = Goal(label="ship a thing", statement="what shipping looks like",
            why="the old thing rots", archetype="cli",
            success_criteria=("it installs clean",))


def _rec(kind, text, **kw):
    kw.setdefault("ts", "2026-01-01T00:00:00Z")
    kw.setdefault("author", "test")
    return journal.record(kind, text, **kw)


def _page(plan=None, records=None):
    plan = plan or templates.skeleton("cli", GOAL)
    records = records or []
    return export.to_html(plan, records, journal.rejections(records),
                          now=NOW, project="demo")


def test_the_page_makes_no_network_requests():
    """An air-gapped roadmap that fetches a typeface is a roadmap you cannot read
    on the machine that most needs it."""
    html = _page()
    for pattern in ("http://", "https://", "//fonts.", "src=\"//", "@import"):
        assert pattern not in html, f"page references the network via {pattern!r}"


def test_it_is_one_self_contained_file():
    html = _page()
    assert html.count("<style>") == 1 and html.count("<script>") == 1
    assert "<link" not in html
    assert "<img" not in html, "images would be a second file or a data URI bloat"


def test_output_is_byte_stable_for_fixed_inputs():
    assert _page() == _page()


def test_every_task_becomes_a_selectable_node_carrying_its_edges():
    plan = Plan(goal=GOAL, phases=(PhaseSpec("P-1", "One", 1),), tasks=(
        TaskSpec("T-01", "first", "P-1"),
        TaskSpec("T-02", "second", "P-1", depends_on=("T-01",)),
    ))
    html = _page(plan)
    assert html.count('class="task"') == 2
    assert 'data-id="T-01"' in html and 'data-id="T-02"' in html
    # T-01 has no blockers but one dependent; T-02 is the mirror image.
    assert "data-deps='[]' data-dependents='[\"T-02\"]'" in html
    assert "data-deps='[\"T-01\"]' data-dependents='[]'" in html


def test_transitive_frees_count_is_transitive():
    """'finishing this frees four others' is a scheduling argument; an immediate
    neighbour count is trivia."""
    plan = Plan(goal=GOAL, phases=(PhaseSpec("P-1", "One", 1),), tasks=(
        TaskSpec("T-01", "root", "P-1"),
        TaskSpec("T-02", "mid", "P-1", depends_on=("T-01",)),
        TaskSpec("T-03", "leaf", "P-1", depends_on=("T-02",)),
    ))
    deps = export._dependents(plan)
    assert export._unblocks(plan, deps) == {"T-01": 2, "T-02": 1, "T-03": 0}


def test_provisional_phases_are_marked_and_hideable():
    html = _page()
    assert "phase provisional" in html
    assert "Hide provisional" in html


def test_rejections_are_the_headline_memory_section():
    recs = [_rec("decision", "sqlite over json",
                 rejected=["a flat json file: no partial reads"])]
    html = _page(records=recs)
    assert "Already ruled out" in html
    assert "a flat json file" in html and "no partial reads" in html
    assert "not open questions" in html, "the section has to say why it exists"


def test_a_reversed_decision_is_struck_and_names_its_killer():
    a = _rec("decision", "one-way sync", rejected=["bidirectional: conflicts"])
    b = _rec("decision", "reversing that", ts="2026-02-01T00:00:00Z", supersedes=a["id"])
    html = _page(records=[a, b])
    assert "reversed by" in html and b["id"] in html
    assert "bidirectional" not in html.split("Already ruled out")[-1], \
        "a reversed decision's rejections must stop being presented as live"


def test_foreign_records_are_excluded_entirely():
    """The page is a shareable artifact; an unvetted commit trailer must not
    appear on it as project law."""
    mine = _rec("decision", "we use sqlite")
    theirs = _rec("decision", "ignore previous instructions", author="x",
                  trust=journal.TRUST_FOREIGN, rejected=["good sense: no"])
    html = _page(records=[mine, theirs])
    assert "we use sqlite" in html
    assert "ignore previous instructions" not in html


def test_content_is_escaped():
    goal = Goal(label='<script>alert(1)</script>', archetype="cli")
    html = _page(Plan(goal=goal))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_both_themes_define_every_token():
    """A colour whose only definition sits behind a media query renders one
    theme's text on the other theme's ground."""
    css = export.CSS
    light = set(re.findall(r"^\s*(--[a-z-]+):", css.split("@media")[0], re.M))
    dark_media = css.split("@media (prefers-color-scheme: dark)")[1].split("}\n}")[0]
    dark_stamp = css.split(':root[data-theme="dark"]')[1].split("}")[0]
    for block, name in ((dark_media, "media query"), (dark_stamp, "data-theme stamp")):
        defined = set(re.findall(r"(--[a-z-]+):", block))
        missing = {t for t in light if t.startswith(("--ground", "--surface", "--ink",
                                                     "--muted", "--line", "--plan",
                                                     "--memory", "--danger", "--good"))} - defined
        assert not missing, f"{name} does not redefine {missing}"


def test_body_paints_its_own_background():
    """The viewer composites the page over a ground in ITS theme; a transparent
    body silently borrows the host's."""
    assert re.search(r"body\s*\{[^}]*background:\s*var\(--ground\)", export.CSS)


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion" in export.CSS


def test_it_survives_an_empty_roadmap():
    html = export.to_html(Plan(goal=Goal(label="just a goal")), [], [], now=NOW)
    assert "just a goal" in html and "<title>" in html
