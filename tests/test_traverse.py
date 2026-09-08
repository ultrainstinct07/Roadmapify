"""traverse.py — explain / path / query over graph.json."""

from __future__ import annotations

from roadmapify import journal, project, traverse
from roadmapify.cli import EXIT_ERROR, EXIT_OK
from roadmapify.plan import load_plan


def _graph(seeded):
    plan = load_plan(seeded)
    records = journal.load(seeded)
    return project.to_json(project.project(plan, records, journal.rejections(records)))


def test_explain_by_ref(seeded):
    g = _graph(seeded)
    card = traverse.explain(g, "T-01")
    assert card is not None
    assert card["ref"] == "T-01"
    assert card["degree"] >= 1


def test_explain_rejection(seeded):
    g = _graph(seeded)
    xs = [n for n in g["nodes"] if n.get("roadmap_kind") == "rejection"]
    assert xs
    card = traverse.explain(g, xs[0]["roadmap_id"])
    assert card is not None
    assert card["kind"] == "rejection"


def test_path_along_task_chain(seeded):
    g = _graph(seeded)
    tasks = sorted(
        n["roadmap_id"] for n in g["nodes"]
        if n.get("roadmap_kind") == "task" and n.get("roadmap_id")
    )
    assert len(tasks) >= 2
    hops = traverse.shortest_path(g, tasks[0], tasks[-1])
    assert hops is not None
    assert hops[0]["ref"] == tasks[0]
    assert hops[-1]["ref"] == tasks[-1]
    assert len(hops) >= 2


def test_path_unknown_is_none(seeded):
    g = _graph(seeded)
    assert traverse.shortest_path(g, "T-01", "T-nope") is None


def test_query_returns_seeded_subgraph(seeded):
    g = _graph(seeded)
    result = traverse.query(g, "status derived evidence", depth=2, budget=500)
    assert result["seeds"]
    assert result["text"]
    assert result["tokens_used"] > 0


def test_cli_explain_path_query(seeded, run):
    code, _ = run("build", "--root", str(seeded))
    assert code == EXIT_OK
    code, out = run("explain", "T-01", "--root", str(seeded))
    assert code == EXIT_OK
    assert "T-01" in out and "Connections" in out

    data = project.load_graph(seeded)
    tasks = sorted(
        n["roadmap_id"] for n in data["nodes"]
        if n.get("roadmap_kind") == "task" and n.get("roadmap_id")
    )
    code, out = run("path", tasks[0], tasks[-1], "--root", str(seeded))
    assert code == EXIT_OK
    assert "Shortest path" in out or "Same node" in out

    code, out = run("query", "package skeleton", "--root", str(seeded))
    assert code == EXIT_OK
    assert "NODE" in out or "seeds:" in out


def test_path_no_match_exits_error(seeded, run):
    run("build", "--root", str(seeded))
    code, _ = run("path", "T-01", "T-zzzz", "--root", str(seeded))
    assert code == EXIT_ERROR
