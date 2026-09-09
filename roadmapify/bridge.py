"""Bounded read-only graphify JSON bridge. Source associations are context, not proof."""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath

MAX_BYTES = 10_000_000
MAX_NODES = 10_000
MAX_EDGES = 50_000


def validate(graph):
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise ValueError("expected a graph object with nodes")
    edges = graph.get("links", graph.get("edges"))
    if not isinstance(edges, list):
        raise ValueError("expected edges or links")
    nodes = graph["nodes"]
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ValueError("code graph exceeds 10,000 nodes / 50,000 edges")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"] or node["id"] in ids:
            raise ValueError("code nodes need unique nonempty string IDs")
        for key in ("label", "source_file", "qualified_name"):
            if node.get(key) is not None and not isinstance(node[key], str):
                raise ValueError(f"node {key} must be text")
        ids.add(node["id"])
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("source"), str) or not isinstance(edge.get("target"), str) or edge["source"] not in ids or edge["target"] not in ids:
            raise ValueError("code edge has an unresolved endpoint")
    return {**graph, "nodes": nodes, "edges": edges}


def load(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("code graph exceeds 10 MB")
    return validate(json.loads(data))


def source_path(value, source_root=""):
    if not isinstance(value, str):
        return None
    value = value.replace("\\", "/")
    root = str(source_root).replace("\\", "/").rstrip("/")
    if root and value.startswith(root + "/"):
        value = value[len(root) + 1:]
    path = PurePosixPath(value)
    if path.is_absolute() or ":" in value or ".." in path.parts:
        return None
    return str(path) if value else None


def freshness(graph, revision=""):
    meta = graph.get("graph") if isinstance(graph.get("graph"), dict) else {}
    recorded = graph.get("source_revision") or meta.get("source_revision")
    return "unknown" if not recorded or not revision else "current" if recorded == revision else "stale"


def context(graph, task, *, source_root="", revision=""):
    from roadmapify.plan import split_deliverable
    files = {source_path(value.split("::")[0], source_root)
             for kind, value in map(split_deliverable, task.produces) if kind in ("file", "test")}
    files.discard(None)
    symbols = {value for kind, value in map(split_deliverable, task.produces) if kind == "symbol"}
    nodes = [n for n in graph["nodes"] if source_path(n.get("source_file"), source_root) in files
             or n["id"] in symbols or n.get("qualified_name") in symbols]
    ids = {n["id"] for n in nodes}
    return {"freshness": freshness(graph, revision), "association": "source or qualified identity; context only",
            "nodes": nodes, "edges": [e for e in graph["edges"] if e["source"] in ids and e["target"] in ids],
            "total_nodes": len(graph["nodes"]), "unassociated_nodes": len(graph["nodes"]) - len(nodes)}


def symbol(graph, value):
    matches = [n for n in graph["nodes"] if n["id"] == value or n.get("qualified_name") == value]
    return matches[0] if len(matches) == 1 else None
