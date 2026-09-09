# Implementation status — 2026-09-09

The review recommendations have moved into production code. The original review in PROJECT_REVIEW.md describes the pre-fix baseline; it is retained as the rationale and acceptance criteria.

## Review defects

All 18 original cases have desired-behavior regressions in `tests/test_review_regressions.py`:

| Case | Implemented repair |
| --- | --- |
| Retired memory | Shared effective-memory rules remove retired constraints from active brief/export enforcement |
| Foreign supersession | An unaccepted foreign transition cannot retire local authority |
| Acceptance display | Locally accepted foreign rules appear in the enforced set |
| CLI/MCP parity | One structured checking engine supplies both interfaces |
| Stale views | Graph reads derive from current plan and journal; MCP brief uses current inputs |
| Uncheckable claim | Unknown/partial support cannot receive a corroborated summary |
| Foreign completion | Evidence admission requires local provenance and required identity/timestamp/reference fields |
| Historical completion | Current git and plan support are reconciled; missing/truncated support is stale/unknown |
| Hashed task IDs | Branch/trailer attribution supports the task-ID grammar accepted by the plan |
| Record collision | Full record content is hashed; ambiguous legacy collisions are preserved and quarantined |
| Remaining path | Recomputed over the unfinished dependency graph |
| Symlink ancestors | Rejected before directory traversal; leaf targets are not inspected |
| Hook path | Root passed as argv data; skipping the marked block preserves unrelated hook content |
| JSON flag | `check --json` emits the structured result |
| Graph identity collision | Arbitrary deliverable identities retain an exact-content digest |
| Retrieval trust | Trust and historical status remain visible in graph retrieval |
| Explicit avoidance | Simple negative proposals are distinguished; mixed positive proposals remain checked |
| Session scope | Resume closes its ancestor; checkpoint requires an explicit session when several are open |

Additional tests cover acceptance revocation, foreign session-close records, current-plan observation identity, missing local git identity, exact graphify matching and real hook execution with quoted/newline paths.

## Product improvements

| Improvement | Production entry point |
| --- | --- |
| Task-focused recovery | `roadmap context [T-ID]` |
| Explainable progress | Context separates git observations, artifacts, host-reported checks and uncertainty; coverage has an explicit denominator |
| Existing-project onboarding | `roadmap onboard`, followed by reviewed exact commit association |
| One checking engine | `checking.check`, CLI/MCP JSON results and shared effective-memory projection |
| Better offline planning | Representative input/output flags, acceptance guidance, proposed deliverables and doctor review findings |
| Impact analysis | `roadmap impact <node>` |
| Visual roadmap and diagrams | `roadmap workspace`, with optional `--serve` controls |
| Graphify bridge | `bridge.py`, workspace import, context and verifier integration |
| Bounded agent execution | `roadmap goal` and `execution.drive`, through an existing local agent host |

Agent provider selection is not automatic. The controller, queue, local host API, persistence and reconciliation are implemented and integration-tested. A real host must connect using [the documented protocol](roadmapify/references/goal-host.md). Stop requests require that host's acknowledgment.

Task completion remains evidence-derived. Passing tests and present artifacts do not mark every roadmap phase complete.

## Validation

- Full test suite: **434 passed**, including the 18 original review regressions.
- Source `roadmap verify --json`: **46 distinct deliverables present, 0 missing**, across 26 reviewed tasks. No completion claims are admitted; verdict: `nothing_claimed`.
- Browser checks: goal and dependency diagrams, Graphify fixture import with exact matches, live refresh, and start/pause/resume/cancel controls. The test run was cancelled before any host claimed work.
- Wheel build and isolated installation/export checked; the packaged workspace and host documentation are included.

Use `python3 -m roadmapify workspace --serve` from this checkout to run the updated source. An older globally installed `roadmap` executable may still use its installed version.
