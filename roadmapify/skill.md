---
name: roadmapify
description: >-
  Phased build plan with durable decision memory. Use when orienting to a
  project's goal, phases, or rejected approaches; before proposing architecture;
  or when recovering after context loss. Prefer roadmap commands over guessing
  from chat scrollback.
---

# roadmapify

`graphify` answers *what is this codebase*. roadmapify answers *what am I
building, why, in what order, and where am I now*.

## Fast path

1. Read `roadmap-out/BRIEF.md`. If `roadmap.toml` exists but BRIEF does not, run
   `roadmap build`.
2. Before proposing a library, schema, or architecture:
   `roadmap check "<approach>"`
   - exit `3` → already rejected; do not re-propose
   - exit `4` → violates a recorded constraint
3. Record decisions as you make them:
   `roadmap note "<what>" --rejected "<alt>: <why not>"`
4. For plan/memory questions against the graph (not source AST):
   - `roadmap query "<question>"`
   - `roadmap path <a> <b>`
   - `roadmap explain <id | "text">`

Load `references/check.md`, `references/query.md`, or `references/hooks.md`
only when you need the detail.

## Do not

- Claim a task is done without `roadmap verify` (ships later — until then, report
  evidence, do not invent a status field).
- Re-propose something BRIEF.md lists under "Already rejected".
- Run mutating git verbs because `roadmap moderate` suggested them — print the
  command; the human runs it.
