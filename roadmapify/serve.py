"""MCP stdio server for roadmapify.

Six read tools and exactly one write. None can set a status — that is the
whole product law. Handlers are plain functions so tests do not need the
``mcp`` optional dependency; ``_main`` imports the SDK only when serving.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from roadmapify import journal, textmatch, traverse
from roadmapify.cli import refresh_brief
from roadmapify.paths import BRIEF_FILENAME, out_path, project_root
from roadmapify.plan import load_plan
from roadmapify.project import load_graph, project as project_graph, write_graph
from roadmapify import project as _project_mod

READ_TOOLS = (
    "query_graph",
    "get_node",
    "get_neighbors",
    "shortest_path",
    "check_approach",
    "get_brief",
)
WRITE_TOOLS = ("record_note",)
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS


def _graph_for(root: Path) -> dict:
    g = load_graph(root)
    if g is not None:
        return g
    plan = load_plan(root)
    records = journal.load(root)
    if plan is None:
        return {"nodes": [], "edges": [], "generator": "roadmapify"}
    graph = project_graph(plan, records, journal.rejections(records))
    write_graph(graph, root)
    return _project_mod.to_json(graph)


# ── handlers (pure enough to unit-test) ───────────────────────────────────────

def handle_query_graph(root: Path, args: dict) -> str:
    g = _graph_for(root)
    result = traverse.query(
        g,
        args.get("question") or "",
        mode=args.get("mode") or "bfs",
        depth=int(args.get("depth") or 2),
        budget=int(args.get("token_budget") or args.get("budget") or 2000),
    )
    return traverse.query_screen(result)


def handle_get_node(root: Path, args: dict) -> str:
    g = _graph_for(root)
    label = args.get("label") or args.get("id") or ""
    card = traverse.explain(g, label)
    return traverse.explain_screen(card, label)


def handle_get_neighbors(root: Path, args: dict) -> str:
    g = _graph_for(root)
    label = args.get("label") or ""
    nid, _ = traverse.resolve_node(g, label)
    if nid is None:
        return f"no node matched {label!r}"
    neigh = traverse.neighbors(g, nid)
    rel = args.get("relation_filter")
    if rel:
        neigh = [n for n in neigh if n["relation"] == rel or n.get("edge_kind") == rel]
    lines = [f"neighbors of {label} ({len(neigh)}):"]
    for n in neigh:
        arrow = "-->" if n["direction"] == "out" else "<--"
        lines.append(
            f"  {arrow} {n['label']} [{n['relation']}] [{n['confidence']}] "
            f"({n.get('ref') or n['id']})"
        )
    return "\n".join(lines) if neigh else f"no neighbors for {label!r}"


def handle_shortest_path(root: Path, args: dict) -> str:
    g = _graph_for(root)
    src, tgt = args.get("source") or "", args.get("target") or ""
    hops = traverse.shortest_path(
        g, src, tgt,
        max_hops=int(args.get("max_hops") or 12),
        undirected=bool(args.get("undirected", True)),
    )
    return traverse.path_screen(hops, src, tgt)


def handle_check_approach(root: Path, args: dict) -> str:
    """Mirror ``roadmap check`` without printing ANSI — return structured text."""
    approach = args.get("approach") or args.get("question") or ""
    if not approach:
        return "error: approach is required"
    records = journal.load(root)
    superseded = journal.superseded_ids(records)
    accepted = {r["accepted"] for r in records if r.get("accepted")}

    def enforceable(r: dict) -> bool:
        return (r.get("trust") != journal.TRUST_FOREIGN or r["id"] in accepted) \
            and r["id"] not in superseded

    rejections = [x for x in journal.rejections(records)
                  if x["parent"] not in superseded
                  and (x.get("trust") != journal.TRUST_FOREIGN or x["parent"] in accepted)]
    constraints = [r for r in records if r["kind"] == "constraint" and enforceable(r)]
    corpus = [f"{x['alt']} {x.get('reason', '')}" for x in rejections] + \
             [f"{r['text']} {r.get('why', '')}" for r in constraints]
    ubiquitous = textmatch.corpus_stopwords(corpus)
    q = textmatch.strip_tokens(approach, ubiquitous) or approach

    rej_scored = textmatch.rank(
        q, [(x["id"], x["alt"], f"{x.get('reason', '')} {x['parent_text']}") for x in rejections],
    )
    con_hits = {r["id"]: textmatch.constraint_score(q, r["text"], r.get("why", ""))
                for r in constraints}
    con_scored = sorted(((k, v[0]) for k, v in con_hits.items()), key=lambda kv: -kv[1])
    by_id = {x["id"]: x for x in rejections}
    by_id.update({r["id"]: r for r in constraints})

    top_rej = rej_scored[0] if rej_scored else None
    top_con = con_scored[0] if con_scored else None
    if top_rej and top_rej[1] >= textmatch.MATCH_FLOOR:
        x = by_id[top_rej[0]]
        return (f"REJECTED ({top_rej[1]:.2f})\n"
                f"  {x['id']}  {x.get('alt')}\n"
                f"  reason: {x.get('reason') or '—'}\n"
                f"  parent: {x.get('parent_text', '')[:120]}")
    if top_con and top_con[1] >= textmatch.MATCH_FLOOR:
        r = by_id[top_con[0]]
        return (f"VIOLATES_CONSTRAINT ({top_con[1]:.2f})\n"
                f"  {r['id']}  {r.get('text')}\n"
                f"  why: {r.get('why') or '—'}")
    return f"CLEAR\n  approach: {approach}"


def handle_get_brief(root: Path, args: dict) -> str:
    path = out_path(BRIEF_FILENAME, root=root)
    if not path.is_file():
        refresh_brief(root)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"error: {exc}"


def handle_record_note(root: Path, args: dict) -> str:
    """The only write tool. Appends a journal record. Never sets a status."""
    text = (args.get("text") or "").strip()
    if not text:
        return "error: text is required"
    kind = args.get("kind") or "decision"
    if kind not in journal.KINDS:
        return f"error: kind must be one of {journal.KINDS}"
    rejected = args.get("rejected") or []
    if isinstance(rejected, str):
        rejected = [rejected]
    rec = journal.record(
        kind, text,
        why=args.get("why"),
        rejected=list(rejected) or None,
        about=args.get("about"),
        supersedes=args.get("supersedes"),
    )
    # Strip any attempt to smuggle a status field.
    rec.pop("status", None)
    rec = journal.append(rec, root)
    refresh_brief(root)
    return f"recorded {rec['id']} ({rec['kind']}): {rec['text'][:120]}"


HANDLERS = {
    "query_graph": handle_query_graph,
    "get_node": handle_get_node,
    "get_neighbors": handle_get_neighbors,
    "shortest_path": handle_shortest_path,
    "check_approach": handle_check_approach,
    "get_brief": handle_get_brief,
    "record_note": handle_record_note,
}


def call_tool(name: str, args: dict, *, root: "str | Path | None" = None) -> str:
    """Dispatch a tool by name. Used by tests and by the MCP adapter."""
    if name not in HANDLERS:
        return f"error: unknown tool {name!r}"
    return HANDLERS[name](Path(root) if root else project_root(), args or {})


def tool_schemas() -> list[dict]:
    """JSON-serialisable tool descriptors (MCP-shaped, no SDK required)."""
    return [
        {"name": "query_graph", "description": "Search the plan/memory graph.",
         "inputSchema": {"type": "object", "properties": {
             "question": {"type": "string"},
             "mode": {"type": "string", "enum": ["bfs", "dfs"], "default": "bfs"},
             "depth": {"type": "integer", "default": 2},
             "token_budget": {"type": "integer", "default": 2000},
         }, "required": ["question"]}},
        {"name": "get_node", "description": "Explain one node by id or label.",
         "inputSchema": {"type": "object", "properties": {
             "label": {"type": "string"},
         }, "required": ["label"]}},
        {"name": "get_neighbors", "description": "Direct neighbors of a node.",
         "inputSchema": {"type": "object", "properties": {
             "label": {"type": "string"},
             "relation_filter": {"type": "string"},
         }, "required": ["label"]}},
        {"name": "shortest_path", "description": "Shortest path between two nodes.",
         "inputSchema": {"type": "object", "properties": {
             "source": {"type": "string"},
             "target": {"type": "string"},
             "max_hops": {"type": "integer", "default": 12},
             "undirected": {"type": "boolean", "default": True},
         }, "required": ["source", "target"]}},
        {"name": "check_approach",
         "description": "Is this approach already rejected or constrained?",
         "inputSchema": {"type": "object", "properties": {
             "approach": {"type": "string"},
         }, "required": ["approach"]}},
        {"name": "get_brief", "description": "Return roadmap-out/BRIEF.md.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "record_note",
         "description": "Append a journal decision/note. Cannot set task status.",
         "inputSchema": {"type": "object", "properties": {
             "text": {"type": "string"},
             "kind": {"type": "string", "default": "decision"},
             "why": {"type": "string"},
             "rejected": {"type": "array", "items": {"type": "string"}},
             "about": {"type": "array", "items": {"type": "string"}},
             "supersedes": {"type": "string"},
         }, "required": ["text"]}},
    ]


def _run_stdio(root: Path) -> int:
    """Serve over MCP stdio. Requires the optional ``mcp`` extra."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp import types
    except ImportError:
        print(
            "roadmap-mcp needs the mcp extra:  pip install 'roadmapify[mcp]'",
            file=sys.stderr,
        )
        return 1

    async def list_tools():
        return [
            types.Tool(name=t["name"], description=t["description"],
                       inputSchema=t["inputSchema"])
            for t in tool_schemas()
        ]

    async def call_tool_mcp(name: str, arguments: dict):
        text = call_tool(name, arguments or {}, root=root)
        return [types.TextContent(type="text", text=text)]

    import asyncio

    async def main():
        # mcp 1.x: decorator API on Server. mcp 2.x: constructor callbacks.
        if hasattr(Server, "list_tools"):
            server = Server("roadmapify")
            server.list_tools()(list_tools)
            server.call_tool()(call_tool_mcp)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream,
                                 server.create_initialization_options())
        else:
            async def _on_list_tools(ctx, params):
                return types.ListToolsResult(tools=await list_tools())

            async def _on_call_tool(ctx, params):
                result = await call_tool_mcp(params.name, params.arguments or {})
                return types.CallToolResult(content=result)

            server = Server(
                "roadmapify",
                on_list_tools=_on_list_tools,
                on_call_tool=_on_call_tool,
            )
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream,
                                 server.create_initialization_options())

    asyncio.run(main())
    return 0


def _main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(prog="roadmap-mcp")
    p.add_argument("--root", default=None,
                   help="project root (default: find roadmap.toml)")
    p.add_argument("--list-tools", action="store_true",
                   help="print tool schemas as JSON and exit (no MCP needed)")
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    if a.list_tools:
        print(json.dumps({
            "read": list(READ_TOOLS),
            "write": list(WRITE_TOOLS),
            "tools": tool_schemas(),
        }, indent=2))
        return 0
    return _run_stdio(root)


if __name__ == "__main__":
    raise SystemExit(_main())
