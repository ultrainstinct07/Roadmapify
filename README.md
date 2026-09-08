# roadmapify

A phased build plan with memory that survives context loss.

`graphify` answers *what is this codebase*. roadmapify answers the other question:
**what am I building, why, in what order, and where am I now** — and keeps answering
it after the chat window is compacted, after the machine reboots, three weeks later
on a different laptop.

```bash
roadmap init "a CLI that syncs Notion pages to markdown" \
  --why "I keep losing notes between Notion and my repo"

roadmap note "sync is one-way, Notion to markdown, never back" \
  --rejected "bidirectional sync: conflict resolution across two schemas is its own product"

roadmap check "add bidirectional sync so edits flow back to Notion"
# REJECTED — this project already ruled this out.        (exit 3)
```

That third command is the point. An agent — or you, in six weeks — is about to
re-propose something the project already rejected, and the reason is one exit code
away instead of buried in a chat log nobody has.

## Nothing stores a status

Roadmap tools rot because they store claims. Someone types `status = done`, walks
away, and the file becomes a confident lie.

`roadmap.toml` has no status field. Status is *derived* from git evidence on every
run, so the only way to make the plan wrong is to write a wrong plan. Three
plain-text files are the truth:

| File | | |
|---|---|---|
| `roadmap.toml` | tracked, hand-edited | the plan: goal, phases, tasks, deliverables |
| `roadmap-out/journal.jsonl` | tracked, append-only | the memory: decisions, rejections, constraints |
| `roadmap-out/evidence.jsonl` | tracked, append-only | observed git facts |

Everything else under `roadmap-out/` — the graph, `BRIEF.md`, `ROADMAP.md` — is
derived, gitignored and disposable. Delete it and `roadmap build` reproduces it
byte-identically.

The two append-only logs use git's built-in `merge=union` and content-hashed record
ids, so two branches that each record a decision merge without a conflict and
without needing a custom merge driver installed.

## Install

```bash
uv tool install roadmapify
```

Base install has **one** conditional dependency (`tomli`, on Python < 3.11 only).
No tree-sitter, no networkx, no network calls. Everything works with no API key.

## Commands

| | |
|---|---|
| `roadmap init "<goal>" --why "..."` | write `roadmap.toml` with the complete phased path |
| `roadmap note "<text>" --rejected "<alt>: <why not>"` | record memory. Only the text is required |
| `roadmap check "<approach>"` | is this already ruled out? `0` clear · `3` rejected · `4` violates a constraint |
| `roadmap why <id \| "text">` | what was decided, and what reversed it |
| `roadmap brief` | re-render `roadmap-out/BRIEF.md` (alias: `roadmap build`) |
| `roadmap explain <id \| "text">` | one node: source, degree, neighbors tagged EXTRACTED/INFERRED |
| `roadmap path <a> <b>` | shortest path between two plan/memory nodes |
| `roadmap query "<question>"` | scoped subgraph for a plain-language question |
| `roadmap hook install` | keep BRIEF.md + graph.json current after commit/checkout |
| `roadmap resume` | recover an interrupted session |
| `roadmap checkpoint` | close the open session |
| `roadmap doctor` | problems worth knowing about |
| `roadmap export html` | the whole roadmap as one self-contained page — opens offline |
| `roadmap install` | write the always-on block into `CLAUDE.md` / `AGENTS.md`; `--skill` / `--platform` adds the skill |

`roadmap --help` for the full surface. Every command takes `--root`.

## Status

**P-1 of 9 — "It remembers" — is complete and usable today.** The memory layer
ships before any graph, git sync or phase logic, deliberately: it is the part that
pays for itself immediately, and the journal starts accumulating real content that
the later phases are tested against.

Still to come: P-2 `roadmap next` / ready lists, P-3 git sync and derived task
status, P-5 `roadmap verify`, P-6 `roadmap moderate` (branch hygiene,
report-only — it never writes a git ref), and P-9 the optional LLM refinement
pass. Hooks, the skill, MCP, and query/path/explain already ship.

A command that has not shipped answers for itself: it names the phase that brings it
and exits 2, rather than reading as a broken install.

## Design notes

See [ARCHITECTURE.md](ARCHITECTURE.md) — the module table there is imported by
`tests/test_architecture_doc.py`, so it cannot drift from the code.
