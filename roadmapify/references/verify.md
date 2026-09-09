# Verification and graphify context

`roadmap verify [T-ID] --json` reports artifact presence, missing artifacts,
unknown checks and invalid specs. It never executes a command or test named by
roadmap.toml. An uncheckable or partially checkable claim is not corroborated.

Use `roadmap verify --code-graph /path/to/graph.json` to inspect `symbol:` specs
against an existing graphify export. Both `nodes`/`links` and `nodes`/`edges`
formats are accepted. A symbol needs a unique exact ID or `qualified_name`;
display labels and basenames are not identities. Indexed symbols are weak
context with unknown freshness, never proof that code runs or fulfills a task.

`roadmap context T-ID --code-graph graph.json --source-root /original/checkout`
associates declared files with exact normalized graph source paths. Explicitly
set the source root when graphify used absolute paths from another checkout.
The bridge preserves original relation direction, confidence and source location.
Its limits are 10 MB, 10,000 nodes and 50,000 edges.

Current git support, artifact presence, host-reported checks and human goal
acceptance are separate observations. Symlink ancestors are rejected before
traversal; leaf symlink targets are not inspected. A shallow or unavailable git
history makes missing historical support unknown, not a fabricated reversal.
