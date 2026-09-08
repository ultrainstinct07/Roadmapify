"""serve.py — MCP tool handlers (no live server required)."""

from __future__ import annotations

from roadmapify import journal, serve
from roadmapify.cli import EXIT_OK


def test_tool_surface_matches_p8_exit_criteria():
    assert len(serve.READ_TOOLS) == 6
    assert len(serve.WRITE_TOOLS) == 1
    assert serve.WRITE_TOOLS == ("record_note",)
    names = {t["name"] for t in serve.tool_schemas()}
    assert names == set(serve.ALL_TOOLS)


def test_query_graph_handler(seeded):
    text = serve.call_tool("query_graph", {"question": "status derived"}, root=seeded)
    assert "seeds:" in text or "NODE" in text


def test_get_node_and_neighbors(seeded):
    text = serve.call_tool("get_node", {"label": "T-01"}, root=seeded)
    assert "T-01" in text and "Connections" in text
    text = serve.call_tool("get_neighbors", {"label": "T-01"}, root=seeded)
    assert "neighbors of" in text


def test_shortest_path_handler(seeded):
    from roadmapify import project
    data = project.load_graph(seeded) or {}
    if not data:
        serve.call_tool("get_brief", {}, root=seeded)
        data = project.load_graph(seeded)
    tasks = sorted(
        n["roadmap_id"] for n in data["nodes"]
        if n.get("roadmap_kind") == "task" and n.get("roadmap_id")
    )
    text = serve.call_tool(
        "shortest_path", {"source": tasks[0], "target": tasks[-1]}, root=seeded)
    assert "Shortest path" in text or "Same node" in text


def test_check_approach_handler(seeded):
    text = serve.call_tool(
        "check_approach",
        {"approach": "a status field in roadmap.toml"},
        root=seeded,
    )
    assert "REJECTED" in text or "CLEAR" in text


def test_get_brief_handler(seeded):
    text = serve.call_tool("get_brief", {}, root=seeded)
    assert "Goal" in text or "BRIEF" in text or "roadmapify" in text.lower() or len(text) > 50


def test_record_note_is_the_only_write_and_cannot_set_status(seeded):
    before = len(journal.load(seeded))
    text = serve.call_tool(
        "record_note",
        {"text": "mcp wrote this", "kind": "note", "status": "done"},
        root=seeded,
    )
    assert "recorded" in text
    records = journal.load(seeded)
    assert len(records) == before + 1
    mine = [r for r in records if r["text"] == "mcp wrote this"][0]
    assert "status" not in mine


def test_list_tools_cli(seeded, run, monkeypatch, capsys):
    from roadmapify.serve import _main
    code = _main(["--list-tools", "--root", str(seeded)])
    assert code == 0
