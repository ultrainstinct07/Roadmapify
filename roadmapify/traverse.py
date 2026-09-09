"""Traverse ``graph.json`` without networkx.

``roadmap explain`` / ``path`` / ``query`` all land here. The graph is
roadmapify's projection of the plan+journal into graphify's exchange shape
(``nodes`` + ``edges``). Adjacency is built once per call. Token Jaccard from
``textmatch`` seeds queries — the same matcher the check gate already trusts.

Nodes carry both a graphify id (``roadmap_t_01``) and a human ref
(``roadmap_id`` = ``T-01``). Resolvers accept either.
"""

from __future__ import annotations

from collections import deque

from roadmapify import textmatch
from roadmapify.project import load_graph


def _edges(graph: dict) -> list[dict]:
    return list(graph.get("edges") or graph.get("links") or [])


def _nodes_by_id(graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in graph.get("nodes", []) if isinstance(n.get("id"), str)}


def _ref_index(graph: dict) -> dict[str, str]:
    """Map human refs (T-01, D-xxxx, goal/G-0) and labels → node id."""
    out: dict[str, str] = {}
    for n in graph.get("nodes", []):
        nid = n.get("id")
        if not isinstance(nid, str):
            continue
        out[nid] = nid
        ref = n.get("roadmap_id") or ""
        if ref:
            out[ref] = nid
            out[ref.lower()] = nid
        label = (n.get("label") or "").strip().lower()
        if label and label not in out:
            out[label] = nid
    return out


def _node_text(n: dict) -> str:
    return " ".join(str(x) for x in (
        n.get("label", ""),
        n.get("rationale", ""),
        n.get("roadmap_id", ""),
        n.get("roadmap_kind", ""),
    ) if x)


def _adjacency(graph: dict, *, undirected: bool = True
               ) -> dict[str, list[tuple[str, dict]]]:
    """``node_id -> [(neighbor_id, edge), ...]``."""
    by_id = _nodes_by_id(graph)
    out_adj: dict[str, list[tuple[str, dict]]] = {nid: [] for nid in by_id}
    for e in _edges(graph):
        src, tgt = e.get("source"), e.get("target")
        if src not in out_adj or tgt not in out_adj:
            continue
        out_adj[src].append((tgt, e))
        if undirected:
            out_adj[tgt].append((src, e))
    return out_adj


def resolve_node(graph: dict, query: str) -> "tuple[str | None, list[tuple[str, float]]]":
    """Resolve ``query`` to a node id. Exact id/ref wins; otherwise best label match."""
    by_id = _nodes_by_id(graph)
    refs = _ref_index(graph)
    q = (query or "").strip()
    if q in refs:
        return refs[q], [(refs[q], 1.0)]
    if q.lower() in refs:
        return refs[q.lower()], [(refs[q.lower()], 1.0)]
    scored = textmatch.best(q, [(nid, _node_text(n)) for nid, n in by_id.items()])
    if scored and scored[0][1] >= textmatch.RELATED_FLOOR:
        return scored[0][0], scored
    return None, scored


def neighbors(graph: dict, nid: str) -> list[dict]:
    """Direct neighbors with direction and confidence tags."""
    by_id = _nodes_by_id(graph)
    if nid not in by_id:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for e in _edges(graph):
        src, tgt = e.get("source"), e.get("target")
        if src == nid and tgt in by_id:
            key = ("out", tgt, e.get("relation", ""))
            if key not in seen:
                seen.add(key)
                out.append({
                    "direction": "out",
                    "id": tgt,
                    "ref": by_id[tgt].get("roadmap_id") or tgt,
                    "label": by_id[tgt].get("label", tgt),
                    "trust": by_id[tgt].get("roadmap_trust", ""),
                    "superseded": by_id[tgt].get("roadmap_superseded", False),
                    "relation": e.get("relation", ""),
                    "edge_kind": e.get("roadmap_edge", ""),
                    "confidence": e.get("confidence", "EXTRACTED"),
                })
        elif tgt == nid and src in by_id:
            key = ("in", src, e.get("relation", ""))
            if key not in seen:
                seen.add(key)
                out.append({
                    "direction": "in",
                    "id": src,
                    "ref": by_id[src].get("roadmap_id") or src,
                    "label": by_id[src].get("label", src),
                    "trust": by_id[src].get("roadmap_trust", ""),
                    "superseded": by_id[src].get("roadmap_superseded", False),
                    "relation": e.get("relation", ""),
                    "edge_kind": e.get("roadmap_edge", ""),
                    "confidence": e.get("confidence", "EXTRACTED"),
                })
    out.sort(key=lambda r: (r["direction"], r["relation"], r["id"]))
    return out


def explain(graph: dict, query: str) -> "dict | None":
    """Node card for ``explain``. Returns None if nothing matches."""
    nid, scored = resolve_node(graph, query)
    if nid is None:
        return None
    by_id = _nodes_by_id(graph)
    node = by_id[nid]
    neigh = neighbors(graph, nid)
    return {
        "id": nid,
        "ref": node.get("roadmap_id") or nid,
        "label": node.get("label", nid),
        "source_file": node.get("source_file", ""),
        "source_location": node.get("source_location"),
        "community": node.get("roadmap_phase") or node.get("roadmap_kind"),
        "community_name": node.get("roadmap_phase") or node.get("roadmap_kind"),
        "degree": len(neigh),
        "file_type": node.get("file_type"),
        "kind": node.get("roadmap_kind"),
        "trust": node.get("roadmap_trust"),
        "superseded": node.get("roadmap_superseded", False),
        "why": node.get("rationale") or None,
        "intent": node.get("rationale") if node.get("roadmap_kind") == "task" else None,
        "neighbors": neigh,
        "runner_up": scored[1] if len(scored) > 1 else None,
    }


def shortest_path(graph: dict, source: str, target: str, *,
                  max_hops: int = 12, undirected: bool = True
                  ) -> "list[dict] | None":
    """BFS shortest path. Returns hop dicts, or None if no path."""
    src_id, _ = resolve_node(graph, source)
    tgt_id, _ = resolve_node(graph, target)
    if src_id is None or tgt_id is None:
        return None
    by_id = _nodes_by_id(graph)
    if src_id == tgt_id:
        n = by_id[src_id]
        return [{"id": src_id, "ref": n.get("roadmap_id") or src_id,
                 "label": n.get("label", src_id),
                 "relation": None, "confidence": None}]

    out_adj = _adjacency(graph, undirected=undirected)
    parent: dict[str, tuple[str | None, dict | None]] = {src_id: (None, None)}
    q: deque[tuple[str, int]] = deque([(src_id, 0)])
    while q:
        cur, depth = q.popleft()
        if cur == tgt_id:
            break
        if depth >= max_hops:
            continue
        for nxt, edge in out_adj.get(cur, []):
            if nxt not in parent:
                parent[nxt] = (cur, edge)
                q.append((nxt, depth + 1))
    if tgt_id not in parent:
        return None

    hops: list[dict] = []
    cur: "str | None" = tgt_id
    while cur is not None:
        pred, edge = parent[cur]
        n = by_id[cur]
        hops.append({
            "id": cur,
            "ref": n.get("roadmap_id") or cur,
            "label": n.get("label", cur),
            "trust": n.get("roadmap_trust", ""),
            "superseded": n.get("roadmap_superseded", False),
            "relation": (edge or {}).get("relation") if edge else None,
            "confidence": (edge or {}).get("confidence") if edge else None,
            "from": pred,
        })
        cur = pred
    hops.reverse()
    return hops


def query(graph: dict, question: str, *, mode: str = "bfs", depth: int = 2,
          budget: int = 2000) -> dict:
    """Seed by token Jaccard, then BFS/DFS expand. Cap output at ``budget`` tokens."""
    by_id = _nodes_by_id(graph)
    scored = textmatch.best(question, [(nid, _node_text(n)) for nid, n in by_id.items()])
    seeds = [nid for nid, sc in scored if sc >= textmatch.RELATED_FLOOR][:5]
    if not seeds and scored:
        seeds = [scored[0][0]]

    out_adj = _adjacency(graph, undirected=True)
    visited: dict[str, int] = {}
    edge_hits: list[dict] = []

    if mode == "dfs":
        def walk(nid: str, d: int) -> None:
            if nid in visited and visited[nid] <= d:
                return
            visited[nid] = d
            if d >= depth:
                return
            for nxt, edge in out_adj.get(nid, []):
                edge_hits.append({
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "relation": edge.get("relation"),
                    "confidence": edge.get("confidence", "EXTRACTED"),
                })
                walk(nxt, d + 1)
        for s in seeds:
            walk(s, 0)
    else:
        q: deque[tuple[str, int]] = deque((s, 0) for s in seeds)
        for s in seeds:
            visited[s] = 0
        while q:
            cur, d = q.popleft()
            if d >= depth:
                continue
            for nxt, edge in out_adj.get(cur, []):
                edge_hits.append({
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "relation": edge.get("relation"),
                    "confidence": edge.get("confidence", "EXTRACTED"),
                })
                if nxt not in visited:
                    visited[nxt] = d + 1
                    q.append((nxt, d + 1))

    seen_e: set[tuple] = set()
    unique_edges: list[dict] = []
    for e in edge_hits:
        key = (e["source"], e["target"], e["relation"])
        if key not in seen_e:
            seen_e.add(key)
            unique_edges.append(e)

    lines: list[str] = []
    used = 0
    seed_set = set(seeds)
    ordered = sorted(visited, key=lambda n: (0 if n in seed_set else 1, visited[n], n))
    included: list[str] = []
    for nid in ordered:
        n = by_id[nid]
        ref = n.get("roadmap_id") or nid
        line = (f"NODE {ref}  {n.get('label', '')}  "
                f"[src={n.get('source_file', '')} {n.get('source_location') or ''}] "
                f"kind={n.get('roadmap_kind', '')} {authority(n)}")
        cost = max(1, len(line.split()))
        if used + cost > budget and included:
            break
        lines.append(line)
        included.append(nid)
        used += cost

    incl = set(included)
    edge_lines: list[str] = []
    for e in unique_edges:
        if e["source"] not in incl or e["target"] not in incl:
            continue
        line = (f"EDGE {e['source']} --{e['relation']}--> {e['target']} "
                f"[{e.get('confidence', 'EXTRACTED')}]")
        cost = max(1, len(line.split()))
        if used + cost > budget:
            break
        edge_lines.append(line)
        used += cost

    return {
        "question": question,
        "seeds": [by_id[s].get("roadmap_id") or s for s in seeds],
        "seed_scores": [(by_id[nid].get("roadmap_id") or nid, sc)
                        for nid, sc in scored if nid in seed_set][:5],
        "nodes": [by_id[n] for n in included],
        "edges": [e for e in unique_edges if e["source"] in incl and e["target"] in incl],
        "text": "\n".join(lines + edge_lines),
        "tokens_used": used,
    }


# ── screen formatters (pure: data -> str) ─────────────────────────────────────

def explain_screen(card: "dict | None", query: str) -> str:
    if card is None:
        return f"no node matched {query!r}"
    loc = card.get("source_location") or ""
    src = f"{card.get('source_file', '')} {loc}".strip()
    lines = [
        f"Node: {card['label']}",
        f"  Id:         {card.get('ref') or card['id']}",
        f"  Source:     {src or '—'}",
        f"  Community:  {card.get('community_name') or card.get('community') or '—'}",
        f"  Degree:     {card['degree']}",
    ]
    if card.get("kind"):
        lines.append(f"  Kind:       {card['kind']}")
    if card.get("trust"):
        trust = card["trust"]
        lines.append(f"  Trust:      {trust}" + (" · not enforced" if trust in ("foreign", "conflict") else ""))
    if card.get("superseded"):
        lines.append("  Authority:  historical · superseded · not enforced")
    if card.get("intent"):
        lines.append(f"  Intent:     {card['intent']}")
    elif card.get("why"):
        lines.append(f"  Why:        {card['why']}")
    lines.append("")
    lines.append(f"Connections ({card['degree']}):")
    for n in card["neighbors"][:40]:
        arrow = "-->" if n["direction"] == "out" else "<--"
        lines.append(f"  {arrow} {n['label']} [{n['relation']}] [{n['confidence']}]")
        lines.append(f"       ({n.get('ref') or n['id']}) {authority(n)}")
    if card["degree"] > 40:
        lines.append(f"  … {card['degree'] - 40} more")
    return "\n".join(lines)


def path_screen(hops: "list[dict] | None", source: str, target: str) -> str:
    if hops is None:
        return f"no path between {source!r} and {target!r}"
    if len(hops) == 1:
        return f"Same node: {hops[0].get('ref') or hops[0]['id']}  {hops[0]['label']}"
    n_hops = len(hops) - 1
    lines = [f"Shortest path ({n_hops} hop{'s' if n_hops != 1 else ''}):"]
    lines.append(f"  {hops[0]['label']} ({hops[0].get('ref') or hops[0]['id']})")
    for h in hops[1:]:
        rel = h.get("relation") or "related"
        conf = h.get("confidence") or "EXTRACTED"
        lines.append(
            f"    --{rel}[{conf}]--> {h['label']} ({h.get('ref') or h['id']}) {authority(h)}"
        )
    return "\n".join(lines)


def query_screen(result: dict) -> str:
    seeds = ", ".join(result.get("seeds") or []) or "—"
    header = (f"query: {result['question']!r}\n"
              f"seeds: {seeds}  ·  ~{result.get('tokens_used', 0)} tokens")
    body = result.get("text") or "(empty subgraph)"
    return f"{header}\n\n{body}"


def ensure_graph(root=None) -> "dict | None":
    """Load graph.json, or None if it has not been built yet."""
    from roadmapify import journal, project
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    if plan is None:
        return None
    records = journal.load(root)
    return project.to_json(project.project(plan, records, journal.rejections(records)))


def authority(node):
    trust = node.get("trust") or node.get("roadmap_trust") or ""
    retired = node.get("superseded") or node.get("roadmap_superseded")
    return ("[historical; not enforced]" if retired else
            "[" + trust + "; not enforced]" if trust in ("foreign", "conflict") else
            "[" + trust + "]" if trust else "")
