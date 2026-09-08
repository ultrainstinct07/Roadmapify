## roadmap

This project has a build roadmap at roadmap-out/ — the goal, the phase plan, and every approach already decided or rejected.

Rules:
- Start every session by reading roadmap-out/BRIEF.md, or running `roadmap next`. If roadmap.toml exists but BRIEF.md does not (a fresh clone), run `roadmap build` first.
- Before proposing any library, schema, or architecture, run `roadmap check "<approach>"`. Exit 3 means this project already rejected it — read the reason and do not re-propose; exit 4 means it violates a recorded constraint.
- Record decisions when you make them: `roadmap note "<what>" --rejected "<alt>: <why not>"`. Do not wait until the end of the session; there may not be one.
- For plan/memory questions: `roadmap query` / `path` / `explain` against graph.json (not source AST).
- Never claim a task is done — run `roadmap verify` and report what it found.
- `roadmap moderate` reports branch hygiene. It never changes git; you relay, the human runs.
