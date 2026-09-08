# roadmap query / path / explain

These traverse `roadmap-out/graph.json` — the plan and journal projected into
graphify's node contract. They do **not** parse source code.

```bash
roadmap build                         # refresh BRIEF.md + graph.json
roadmap explain T-01                  # one node + EXTRACTED/INFERRED edges
roadmap path T-01 T-08                # shortest hop list
roadmap query "why no status field"   # seeded BFS subgraph
roadmap query "..." --dfs --budget 800
```

Node ids accept human refs (`T-01`, `D-xxxx`, `X-…`) or graphify ids
(`roadmap_t_01`). Every edge carries `confidence: EXTRACTED | INFERRED`.
