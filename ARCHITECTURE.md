# Architecture

roadmapify is a CLI plus an agent skill. The CLI is the product; the skill and the
git hooks exist to make an agent use it without being told.

## The one idea

**Nothing stores a status.** A roadmap rots because someone types `status = done`,
walks away for three weeks, and the file becomes a confident lie. So `roadmap.toml`
has no status field anywhere. Status is *derived* on every run from three plain-text
sources, and the derived layer is disposable:

```
roadmap.toml              SOURCE   the plan            tracked, hand-edited
roadmap-out/journal.jsonl SOURCE   the memory          tracked, append-only, merge=union
roadmap-out/evidence.jsonl SOURCE  observed git facts  tracked, append-only, merge=union
        │
        └── project() ──► graph.json ──► BRIEF.md · ROADMAP.md · MODERATION.md
                          (all derived, gitignored, deletable at any time)
```

Delete everything derived and `roadmap build` reproduces it byte-identically.
`tests/test_plan.py::test_round_trip_is_byte_stable` is the proof for the plan half.

## Pipeline

```
load_plan() → journal.load() → sync() → project() → derive() → render.* → export.*
```

Stages pass plain dicts and frozen dataclasses. No stage has side effects outside
`roadmap-out/`, and every render function is pure: `(data, now) -> str`, with `now`
always passed in. That is what makes the golden tests byte-stable.

## Modules

`tests/test_architecture_doc.py` imports every symbol named in this table, so the
table cannot drift from the code.

| Module | Entry point(s) | Input → Output |
|--------|----------------|----------------|
| `paths.py` | `ROADMAP_OUT`, `project_root`, `out_path`, `plan_path`, `write_text_atomic`, `write_json_atomic`, `append_jsonl`, `read_jsonl`, `derive_lock`, `LockBusy`, `ensure_marked_block` | the output-dir name, atomic writes, the append-only log primitives, and the lock that keeps the derived set mutually consistent |
| `plan.py` | `parse_plan`, `emit_plan`, `load_plan`, `validate_plan`, `find_cycles`, `split_deliverable`, `Plan`, `Goal`, `PhaseSpec`, `TaskSpec` | `roadmap.toml` text ↔ a `Plan`; validation errors carry the line to fix |
| `templates.py` | `ARCHETYPES`, `skeleton`, `detect_archetype`, `Archetype`, `ACTIVE_PHASE_WINDOW` | (archetype, goal) → a complete `Plan`, offline, deterministic |
| `journal.py` | `KINDS`, `record`, `append`, `load`, `rejections`, `open_sessions`, `superseded_ids`, `make_id`, `new_session_id`, `sanitize`, `parse_rejection` | the memory log: content-hashed records, first-class rejections, the crash flag |
| `textmatch.py` | `tokens`, `similarity`, `coverage`, `proposal_score`, `constraint_score`, `prohibited_phrases`, `prohibited_terms`, `rare_tokens`, `distinctive_terms`, `rank`, `best`, `nearest`, `edit_distance`, `MATCH_FLOOR`, `RELATED_FLOOR`, `SINGLE_TERM_WEIGHT` | deterministic matching for the `check` gate and (later) branch↔task mapping |
| `project.py` | `project`, `write_graph`, `load_graph`, `to_json`, `Graph`, `Node`, `Edge`, `Hyperedge`, `CycleError`, `dependents`, `unblocks`, `task_dag`, `node_id` | plan + journal → graphify-shaped `graph.json` (no networkx) |
| `traverse.py` | `explain`, `shortest_path`, `query`, `explain_screen`, `path_screen`, `query_screen`, `ensure_graph`, `resolve_node` | stdlib BFS/DFS over `graph.json` |
| `hooks.py` | `install`, `uninstall`, `status`, `HOOK_START`, `HOOK_END` | marked post-commit / post-checkout scripts that refresh BRIEF.md; never write a git ref |
| `serve.py` | `call_tool`, `tool_schemas`, `HANDLERS`, `READ_TOOLS`, `WRITE_TOOLS`, `_main` | MCP stdio: six reads + `record_note`; none can set a status |
| `render.py` | `brief_md`, `check_screen`, `why_screen`, `note_screen`, `resume_screen`, `ago`, `wrap`, `BRIEF_CHAR_CAP` | data + `now` → text. Pure |
| `export.py` | `to_html`, `CSS`, `JS` | plan + memory → one self-contained HTML page. No network requests: the first project it serves is air-gapped |
| `install.py` | `install_always_on`, `uninstall_always_on`, `always_on_text`, `ALWAYS_ON_TARGETS`, `install_skill`, `uninstall_skill`, `PLATFORMS` | always-on block + skill for claude / cursor / agents |
| `cli.py` | `dispatch`, `COMMANDS`, `NOT_YET`, `refresh_brief`, `EXIT_REJECTED`, `EXIT_CONSTRAINT`, `EXIT_NOT_YET` | argv → exit code |
| `__main__.py` | `main` | console entry point |

Not yet built (see the build plan): `status.py`, `gitsync.py`,
`verify.py`, `moderate.py`, `llm.py`, and the session hook half of `install.py`.

## Exit codes

`roadmap check` is meant to be called by hooks, CI and agents, so its result is a
code, not prose.

| Code | Meaning |
|------|---------|
| 0 | clear — nothing recorded rules this out |
| 1 | error |
| 2 | the command is real but has not shipped yet — it names its phase |
| 3 | already rejected by a recorded decision |
| 4 | violates a recorded constraint |

## Two floors, not one

`textmatch.MATCH_FLOOR` (0.60) blocks. `textmatch.RELATED_FLOOR` (0.22) merely
*surfaces* an item as "related, worth reading". Blocking on near-certainty while
surfacing on overlap is the whole reason the gate stays worth consulting: a wrongly
blocked legitimate proposal teaches an agent to route around it, which leaves the
project worse off than having no gate at all.

## The trust boundary

BRIEF.md is injected into an agent's context at session start. Records captured from
commit trailers therefore carry `author` and `trust: local|foreign`. Foreign records
render in a separate "recorded by others (not enforced)" section — never as project
law — and `roadmap check` will not block on one until `roadmap doctor --accept <id>`.
Control and ANSI characters are stripped on ingest, not at render.

## Concurrency

`_atomic_replace` makes each file individually atomic, but nothing makes the derived
*set* mutually consistent. `derive_lock` wraps the whole derive+write sequence:
`wait=0` for hooks (skip on contention — they are idempotent and the next run catches
up) and ~5 s for the CLI (then degrade to read-only rather than write blind). flock
where available, an `O_EXCL` lockfile with a staleness reaper otherwise. flock is
unreliable on NFS.

Torn writes are handled on the **read** side: `append_jsonl` is a single
`write(2)` under `O_APPEND` with no probe, and `read_jsonl` recovers a record that a
crash remnant was concatenated onto. An earlier probe-then-write version raced.

## Testing

One file per module under `tests/`. Pure unit tests: no network, no writes outside
`tmp_path`. `conftest.py`'s autouse `_sandbox_home` gives every test a throwaway
`HOME` and a fixed `ROADMAP_AUTHOR`, so content-hashed ids are identical on every
machine and nothing can reach the developer's real config — `roadmap install`
writes a project `CLAUDE.md` / `AGENTS.md` today, and P-7's session hook will
write into `~/.claude/`.

```bash
python3 -m pytest tests/ -q
```
