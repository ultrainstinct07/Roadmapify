"""project.py — plan + journal → graphify-shaped graph.json."""

from __future__ import annotations

from roadmapify import journal, project
from roadmapify.paths import GRAPH_FILENAME, out_path
from roadmapify.plan import load_plan


def test_project_emits_nodes_and_edges(seeded):
    plan = load_plan(seeded)
    records = journal.load(seeded)
    g = project.project(plan, records, journal.rejections(records))
    assert g.nodes
    refs = {n.ref for n in g.nodes}
    assert "G-0" in refs
    assert any(r.startswith("P-") for r in refs)
    assert any(r.startswith("T-") for r in refs)
    assert any(n.kind == "rejection" for n in g.nodes) or any(
        n.kind == "decision" for n in g.nodes
    )
    assert g.edges
    assert all(e.confidence == "EXTRACTED" for e in g.edges)


def test_write_graph_is_readable(seeded):
    plan = load_plan(seeded)
    records = journal.load(seeded)
    g = project.project(plan, records, journal.rejections(records))
    path = project.write_graph(g, seeded)
    assert path == out_path(GRAPH_FILENAME, root=seeded)
    data = project.load_graph(seeded)
    assert data is not None
    assert "nodes" in data and "edges" in data
    assert data.get("generator") == "roadmapify"
    assert len(data["nodes"]) >= 3


def test_depends_on_edges_are_extracted(seeded):
    plan = load_plan(seeded)
    g = project.project(plan, (), ())
    deps = [e for e in g.edges if e.kind == "depends_on"]
    assert deps, "template tasks are chained inside a phase"


def test_to_json_uses_graphify_relation_vocab(seeded):
    plan = load_plan(seeded)
    data = project.to_json(project.project(plan, (), ()))
    relations = {e["relation"] for e in data["edges"]}
    # Must stay inside graphify's open relation set (+ our mapped names).
    allowed = {
        "implements", "references", "rationale_for", "cites",
        "calls", "conceptually_related_to", "shares_data_with",
        "semantically_similar_to",
    }
    assert relations <= allowed


def test_rejection_nodes_link_to_parent(seeded):
    plan = load_plan(seeded)
    records = journal.load(seeded)
    g = project.project(plan, records, journal.rejections(records))
    rejects = [e for e in g.edges if e.kind == "rejected_by"]
    assert rejects
    ids = {n.id for n in g.nodes}
    for e in rejects:
        assert e.source in ids and e.target in ids
