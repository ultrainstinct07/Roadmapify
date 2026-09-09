# Project review and enhancement proposal

**Implementation update:** the defects below describe the pre-fix review baseline. The original 18 cases are now regression tests, and production workflow/visual/goal-host implementations are documented in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) and [GOAL_MODE.md](GOAL_MODE.md). `reviews/reproduce_review.py` now runs the regression suite; a successful exit no longer means the defects remain present.

Reviewed 2026-09-09 against commit `2e6a9bc`, using the source checkout and Python 3.13.14.

Roadmapify has a useful central idea: keep the goal, decisions, rejected approaches, and evidence beside the repository so the next developer or agent can recover the reasoning behind the work. The offline CLI, append-only memory, and explicit distinction between a claim and an observation give it a coherent foundation.

The largest opportunity is to make that foundation consistent across the whole workflow. Today several interfaces disagree about which rules are live, what evidence proves, and whether information is current. Adding more commands before resolving those disagreements would make the product harder to trust.

My recommended positioning is: **repository-local decision memory and evidence-backed handoffs for long-running software work.** Phase generation helps users get started; reliable recovery and explanation are the enduring value.

**User-directed extension:** the product now explicitly includes a visual goal workspace with node diagrams, agent execution, and graphify integration. [GOAL_MODE.md](GOAL_MODE.md) defines this capability and its execution contract; [the interactive preview](reviews/goal-mode.html) explores it using the current roadmap. This supersedes the earlier diagram-free direction. The execution controller and provider adapter are proposed work, not implemented automation.

This document contains an assessment and proposed implementation sequence. The production code and phase plan have not been changed. Recommendations are proposals, not new project constraints. The review baseline was recorded as journal note `N-yt6j`.

## Evidence and limits

The review covered all Python production modules, the roadmap, README, architecture document, changelog, packaged agent instructions, package configuration, and test coverage. It included source tracing, selected detailed test inspection, the full existing test run, CLI exercises, graph queries, a read-only git sync, and isolated reproductions.

| Check | Observed result |
|---|---|
| `python3 -m pytest tests/ -q` | **367 passed in 3.99 seconds** |
| `python3 reviews/reproduce_review.py` | **18 defect observations reproduced** in temporary fixtures |
| Source-checkout verification | 20 tasks; 35 distinct deliverables; **31 present, 4 missing**, 0 unverifiable, 0 unresolvable |
| Completion claims | **0 claimed, 0 contradicted**; one task declares no deliverables |
| `python3 -m roadmapify next` | P-1 active; T-01 ready; one evidence record in the current log |
| `python3 -m roadmapify sync --dry-run` | 10 commits observed; two attributable records; eight commits without recognized task attribution |
| `python3 -m roadmapify doctor` | Reports nothing to address despite several review findings |
| `python3 -m roadmapify moderate` | Exit 2: announced but not implemented |
| Ruff | Not available in the current interpreter; lint was not run |

The four missing deliverables belong to planned work: `roadmapify/score.py`, `tests/test_score.py`, `roadmapify/references/verify.md`, and `tests/test_verify_graph.py`. Their absence does not contradict a completion claim. Verification checks presence, not behavioral correctness.

The installed `/home/void999/.local/bin/roadmap` is older than the checkout: it reports `next` and `verify` as unbuilt. Both installations report version `0.1.0`. Use **`python3 -m roadmapify` from this repository** to reproduce this review. The global installation was left untouched.

The sync dry run misleadingly says “nothing to add” because `added` remains empty in dry-run mode; it does not compare the candidate set with the existing log for that message. No new git evidence was persisted during this review.

The reproduction script asserts the currently observed defects. Its successful exit is evidence that the defects exist, not an application correctness test. After fixing a case, move the desired behavior into `tests/` and retire its reproduction assertion.

No network research, live MCP transport test, built-wheel installation, browser interaction test, cross-platform execution, or large-repository benchmark was performed. Security findings below describe concrete local data-flow failures; the hook example was parsed as syntax and never executed. Git history changes were modeled as pure fixtures, without changing any refs.

## What is already valuable

- **A narrow dependency footprint.** The base package declares only conditional `tomli`; graph traversal, serialization, parsing, and matching use the standard library.
- **A durable source/derived split.** TOML plus two JSONL logs are inspectable and reviewable. Rebuilding disposable views is a sound direction.
- **Reasons attached to rejections.** A rejection has an addressable identity and can explain why an approach was ruled out. This is more useful than a flat list of past notes.
- **Explicit uncertainty.** `done*`, evidence confidence, provisional tasks, weak presence, and separate verification verdicts show that the design takes uncertainty seriously.
- **A constrained verifier.** Reading files and parsing syntax without running declared commands is an appropriate boundary for a tool agents are instructed to invoke automatically.
- **Useful tests around past failures.** Torn-write recovery, output escaping, literal git arguments, packaging entry points, and deterministic rendering all have concrete guards.
- **Offline presentation.** A self-contained HTML export with dependency selection is useful for review and sharing without introducing a service requirement.

These properties should survive the enhancements. A database server, graph parsing dependency stack, automatic ref mutation, and a mandatory model provider would work against the recorded project direction.

## Actual capability map

These are implementation observations, not claims that the roadmap phases have met all their exit criteria.

| Area | Present implementation | Material gap |
|---|---|---|
| Memory and checking | Notes, rejections, constraints, supersession, matching, brief | Live-state semantics differ across views; polarity errors; identity collisions |
| Planning | Parse/emit, two archetypes, validation, provisional phases, DAG scheduling | Generated work is mostly generic; remaining-path calculation is wrong after progress |
| Git evidence | Read-only facts, trailers, trunk reachability, branch mapping helper, sync | Start/done/map CLI stubs; no invalidation or reconciliation; hashed task IDs unsupported |
| Verification | File, glob, Python test-presence inspection; JSON report | Overconfident summary; symlink containment; symbol bridge not implemented |
| Hooks | Marked post-commit and post-checkout scripts | Refresh views only; no evidence capture; path interpolation; worktree/custom-hook handling incomplete |
| Agent integration | Always-on blocks, skill, references, session helpers | Session-start flow incomplete; checkpoint closes all sessions; Cursor references differ from installed paths |
| MCP | Six read handlers and record_note; optional stdio adapter | Divergent checking logic; stale cache; trust labels lost; adapter not exercised live here |
| HTML | Static plan and memory with dependency interaction | No evidence/status snapshot; active means non-provisional; lifecycle and validation gaps |
| Moderation / scoring / LLM | Announced in roadmap | Moderation and LLM are stubs; scoring module absent |

The README and architecture narrative lag this map. In particular, the architecture document still says the evidence log does not exist, and the README lists implemented git sync and verification among future work. The documentation tests check symbol existence and command names; they do not validate these claims.

## Findings to address first

Priority 1 means the behavior can undermine a central trust, safety, or correctness promise. Priority 2 means an important workflow or integration defect. The priority labels are review judgments, not vulnerability scores.

### F01 — Priority 1: resolve live memory once, including trust on transitions

**Locations:** `journal.py:254`, `cli.py:313`, `render.py:95`, `export.py:431`, `project.py:358`.

Three reproduced cases show inconsistent authority:

1. Superseding a local constraint stops `check` enforcing it, but BRIEF and HTML still display the old constraint as current.
2. Accepting a foreign constraint makes `check` enforce it, but BRIEF still places it under “not enforced.”
3. An **unaccepted foreign note with `supersedes=<local constraint>` disables that local constraint**. Supersession IDs are collected before checking whether the transition itself is trusted. Acceptance metadata is also collected without establishing the authority of its author.

The current project demonstrates the first case: superseded execution/git constraints remain in its brief beside their replacements. This also wastes the context budget and explains why retiring constraints does not reliably shorten the brief as its footer suggests.

**Recommended change:** create one pure resolution of record identity, acceptance, supersession, and effective authority, then use it in checking, rendering, export, graph projection, and doctor. Retain the historical records, but distinguish history from instructions currently in force. Acceptance and retirement must be authorized transitions themselves.

**Acceptance:** a shared matrix covers local/foreign, accepted/unaccepted, current/superseded, and chained transitions across every surface. A foreign transition never changes local enforcement by itself. Reproduction cases: `memory_lifecycle`, `foreign_supersession`, `acceptance_display`.

### F02 — Priority 1: evidence consumers trust fields that the producer is supposed to protect

**Locations:** `status.py:122`, `status.py:158`, `gitsync.py:612`.

`records_from()` downgrades foreign commits, but `status_of()` accepts a foreign `merge` record without checking trust, commit identity, or provenance. The minimal dictionary `{"kind":"merge","task":"T-01","trust":"foreign"}` produces `done`, even without an evidence ID, timestamp, or commit ref.

Because the evidence log is tracked and merged, it is an input boundary, not just the private output of a trusted writer. A forged record can also unblock dependent work. Separately, matching a commit email to local identity is provenance information; it is not authentication. If `mine` is empty, the producer currently treats every author as local.

**Recommended change:** validate records at ingestion and again at the derivation boundary. Define which observations may support each status, which trust transitions are permitted, and when evidence is insufficient. Bind completion evidence to an observed commit and a documented trunk policy. Do not let a producer-only check carry the entire trust guarantee.

**Acceptance:** malformed, unknown-origin, and unaccepted foreign records cannot derive completion or unblock dependencies. Empty local identity produces explicit uncertainty. Reproduction: `foreign_evidence`.

### F03 — Priority 1: append-only historical observations remain completion evidence forever

**Locations:** `gitsync.py:612`, `gitsync.py:699`, `cli.py:852`, `status.py:158`.

Sync appends records from current history but does not invalidate old observations. Once a merge is recorded, a later observation in which that commit is absent still leaves the task `done`. The status consumer understands a `revert` record, but the git producer does not emit one. An ordinary code revert also leaves the original tagged commit reachable.

`next` derives from the log, not from a fresh git read. The existing hooks refresh the brief and graph without syncing evidence. Thus “derived on every run” does not mean “derived from current repository state.” Plan edits can also change what a task promises while old evidence continues to apply to its ID.

**Recommended change:** distinguish immutable observations from their present applicability. Reconcile reachability, reverts, plan revisions, and observation completeness. Show the observed refs and freshness. When history is shallow, truncated, or unavailable, report uncertainty rather than withdrawing evidence just because a commit was not visible. Respect the no-network promise: remote-tracking refs describe locally observed remote state, not the current server.

**Acceptance:** replayed history changes and changed deliverables cannot silently preserve unsupported completion; shallow histories never create false reversals. Reproduction: `stale_evidence`. This belongs with the existing T-14 reconciliation work, but is broader than patch-ID matching.

### F04 — Priority 1: verification reports corroboration when nothing can be checked

**Locations:** `verify.py:166`, `verify.py:447`, `render.py:633`.

A claimed task whose only deliverable is `cmd:example --self-test` gets the correct task finding `uncheckable_claim`, but the report verdict is `corroborated`. The screen also says every declared deliverable is on disk. The same summary problem affects claims with no declarations and mixed present/unverifiable deliverables.

**Recommended change:** aggregate the actual findings and coverage. “No contradiction found,” “some presence corroborated,” and “all declared presence checks succeeded” are different answers. Keep the current non-execution boundary and preserve machine compatibility when evolving the report contract.

**Acceptance:** an entirely uncheckable claim can never receive a positive corroboration summary. Mixed reports name what was checked and what remains unknown. Reproduction: `uncheckable_report`.

### F05 — Priority 1: MCP enforces a different policy and loses retrieval trust labels

**Locations:** `cli.py:300`, `serve.py:99`, `traverse.py:124`, `traverse.py:199`.

The MCP handler duplicates the CLI checker but omits its distinctive-term and plan-vocabulary logic. On this checkout, **“use networkx for graph operations” gives CLI exit 4 and MCP `CLEAR`**. This is a regression in a host-independent product contract.

The graph preserves `roadmap_trust` and `roadmap_superseded`, but the text returned by explain/query/path does not consistently carry these attributes. A foreign constraint can therefore be presented as an ordinary constraint to an agent even when the brief would quarantine it.

**Recommended change:** compute one structured check result and adapt it to CLI and MCP. Include effective trust and lifecycle on every retrieved memory item, including neighboring nodes. Treat textual memory as quoted project data, not transport-level instructions. Control-character sanitization alone does not establish trust.

**Acceptance:** the same fixtures yield identical policy verdicts, relevant matches, and trust state through both interfaces. Reproductions: `mcp_parity`, `trust_in_retrieval`.

### F06 — Priority 1: hook paths are interpolated into Python source

**Location:** `hooks.py:26`.

The generated hook embeds `$_ROOT` inside `Path('...')` within a Python `-c` string. A directory containing an apostrophe breaks the hook; a specially constructed directory name can change the parsed Python statements. Output redirection hides ordinary failures.

**Recommended change:** pass the root as an argument or environment value and keep the program text fixed. A packaged hook entry point would make execution, diagnostics, and timeout behavior easier to exercise.

**Acceptance:** spaces, apostrophes, backslashes, and newlines in the root are treated only as path data. The installed hook actually refreshes a fixture, and opting out does not prevent unrelated hook content from running. Reproduction: `hook_path`; syntax inspection only, no injected statement was executed.

### F07 — Priority 1: the verifier follows symlinked ancestors outside the project

**Locations:** `verify.py:222`, `verify.py:257`, `verify.py:306`.

Lexical anchoring rejects `..` and absolute paths, but does not contain ancestor symlinks. If `linked/` points outside the project, `test:linked/test_external.py` is parsed and reported as an ordinary non-weak test file. `glob:linked/*.py` also scans the external directory because the symlink is the walk's starting point.

**Recommended change:** explicitly define and enforce symlink traversal policy for every path component and glob prefix. Reject or mark external targets unresolvable before reading them. Checking only `path.is_symlink()` on the final file is insufficient. Consider filesystem changes between validation and opening as part of the implementation.

**Acceptance:** fixture paths through external ancestor links never read or enumerate the target; supported internal links have explicit, consistent semantics. Reproduction: `symlink_ancestor`.

### F08 — Priority 1: record identity does not guarantee merge-safe memory

**Locations:** `journal.py:88`, `journal.py:193`, `journal.py:209`, `gitsync.py:558`.

Journal identity hashes only text, timestamp, and author. Different reasons, rejected alternatives, targets, or trust metadata can therefore share an ID. Independently created branches cannot use the local append collision check to coordinate. After a union merge, `load()` silently keeps the first record, so changing line order changes which rationale survives.

The four-character journal prefix has only 20 bits of space. Local extension reduces some collisions but does not solve independent branch collisions or concurrent appenders. Evidence IDs also omit fields that vary by observer, including trust; the stated byte-identical record guarantee needs a clearer observer model.

**Recommended change:** use a sufficiently strong canonical identity over the immutable record payload, retain convenient short display prefixes, and diagnose conflicting records rather than dropping one. Separate immutable git facts from observer-specific acceptance. Plan a compatible migration for existing IDs and rejection references.

**Acceptance:** union order and concurrent writers cannot discard distinct decisions or alter their reasons. Duplicate identical records are idempotent. Reproduction: `record_collision`.

### F09 — Priority 1: derived graph and MCP brief freshness are based on existence

**Locations:** `cli.py:671`, `serve.py:34`, `serve.py:146`, `traverse.py:353`.

Once graph.json exists, graph readers use it without checking whether the plan or journal changed. The MCP brief behaves similarly. A hand edit to the explicitly hand-edited plan can therefore be absent from the next agent query. Hooks are optional and do not fire on uncommitted edits.

**Recommended change:** associate derived artifacts with source fingerprints and renderer/schema version. Refresh or derive in memory when the inputs differ. Publish a coherent generation of the brief and graph; writer locking alone cannot make unlocked readers see a transactional multi-file snapshot. Surface failures and stale results explicitly.

**Acceptance:** hand-editing a task or appending a record externally is visible on the next read; lock contention cannot silently claim successful refresh. Reproduction: `stale_views`.

### F10 — Priority 1: valid hashed task IDs cannot acquire git attribution

**Locations:** `plan.py:46`, `plan.py:239`, `gitsync.py:72`, `gitsync.py:313`.

The plan accepts and recommends IDs such as `T-2f9a` for branch-safe task creation. Git parsing recognizes only digits. A valid hashed ID is ignored in both trailers and branch names, so the recommended ID form cannot participate in the evidence workflow.

**Recommended change:** share identifier grammar and canonicalization between plan parsing, journal references, CLI, branch mapping, and trailers. Preserve existing numeric shorthand without coercing a hashed identifier into a number.

**Acceptance:** every accepted task ID round-trips through a trailer and explicit branch mapping. Reproduction: `hashed_task_id`.

### F11–F15 — Priority 2: finish the everyday workflow contracts

| Finding | Evidence and impact | Recommended acceptance criterion |
|---|---|---|
| F11: Remaining path | `status.py:291` removes finished nodes from the original longest chain. If another chain becomes longest, it is ignored; `render.py:500` can fall back to displaying the entirely finished chain. Repro: `remaining_chain`. | Compute the longest unfinished dependency chain anew. Label unit-task-count scheduling honestly; it is not a duration estimate. |
| F12: Session ownership | `cli.py:465` leaves the resumed ancestor open; `cli.py:508` closes every open session, including unrelated agents. Repro: `session_scope`. Existing tests deliberately assert global close, so this needs a product-contract change. | A checkpoint closes an explicitly selected or owned session. Session lineage and worktree identity distinguish recovery from unrelated work. |
| F13: JSON flag | `cli.py:300` accepts `check --json` and ignores it. Repro: `json_flag`. Already tracked as T-27. | Valid structured output on every verdict, with no mixed prose; CLI and MCP share the same result. |
| F14: Graph identity | `project.py:323` slugs deliverable specs. `file:a-b.py` and `file:a_b.py` collapse into one node. Repro: `graph_collision`. | Preserve distinct deliverables with collision-resistant IDs while keeping readable labels and compatibility aliases. |
| F15: Proposal polarity | The checker rejects “avoid bidirectional sync” when bidirectional sync is rejected. Repro: `negative_proposal`. Related risks already exist in the journal. | Benchmark affirmative, negative, quoted, hypothetical, historical, and mixed proposals. Ambiguity should surface reasons for review, not claim semantic certainty. A single negation-word exception would be too weak. |

## Additional review observations

These are source-inspection findings or design gaps, separate from the 18 reproduction cases.

- **Brief content and budget:** `refresh_brief()` does not supply a task snapshot, current ready work, verification summary, or evidence freshness. A fresh session gets the goal and phase outline but still needs other commands to answer “where am I now?” `_cap_brief()` removes lines from the end of chronologically ordered decisions, so it trims newer decisions first while describing them as older. It can split a decision from its explanation. Keep complete records and prioritize intentionally. The baseline brief was already over budget with only 41 journal records.
- **Entry-point failures:** `__main__.main()` does not turn invalid TOML, cycle errors, or many filesystem errors into a concise actionable CLI error. `doctor` can fail before it reaches its diagnostics. The schema integer is parsed but not checked against supported versions; a task with no phase can validate and fall outside phase reporting. Validate the entire input shape instead of silently skipping or coercing malformed tables.
- **Write containment and corruption visibility:** atomic replacement deliberately follows destination symlinks. Automatic derived writes should have a documented boundary. `read_jsonl()` recovers useful data, but unreadable or corrupted logs can become silently empty/partial. Doctor should report recovery and corruption instead of allowing missing memory to look like a clean project.
- **Hook installation:** `_find_git_dir()` finds a linked worktree's administrative directory but does not resolve its common hooks directory. Installation also ignores `core.hooksPath`. Existing hooks in another language or hooks that exit early may never run the appended shell block. The skip branch exits the whole script, potentially skipping neighboring content. Current tests primarily inspect generated text.
- **Agent skill installation:** the Cursor rule retains links to `references/...`, while the installer writes `roadmapify-references/...`. Its test checks that the rule contains words, not that references resolve. The shipped note screen advertises automatic `Decision:`/`Rejected:` trailer capture, but the hook currently only refreshes derived views.
- **Git edge cases:** trunk selection prefers `origin/main` over local main and eventually treats the current branch as trunk if conventional names are absent. This needs an explicit configurable interpretation, not an assumption that every feature branch is an integration branch. File parsing assumes 40-character object IDs and strips path whitespace. Detached commits, partial reads, and timeout budgets need integration coverage.
- **HTML:** it does not consume a status/evidence snapshot, so “active tasks” means non-provisional rather than currently active. Deliverable chips have a second parser, already tracked as T-28. Export does not perform the same plan validation as `next`; malformed dependencies can break its JavaScript traversal. Escape JSON embedded in HTML attributes as well as ordinary text, especially when bypassing validation. At narrow widths, the fixed 310px card minimum merits browser testing. Selection needs an exposed accessible state; keyboard activation alone does not communicate which task is selected.
- **Graph explanations:** a generic shortest path can travel through phase and goal nodes, so it should not be read as a dependency chain. The current CLI path from T-01 to T-13 goes through the goal. Traversal text maps several distinct roadmap edges into generic relations and can show arrows opposite the underlying edge when walking undirected. Offer relation/direction filters and retain the original edge meaning.
- **Query budgets and scale:** `tokens_used` counts whitespace-separated words, not model tokens; the first node can exceed the requested budget, and traversal finishes before output is limited. Recursive DFS/cycle/dependent walkers and repeated full scans need bounded-size benchmarks before claiming large-repository support.
- **Test isolation:** the `run` fixture calls `monkeypatch.undo()` after each command, removing the shared home/environment patches as well as stdout. Later commands in the same test may run without the intended sandbox. Use a scoped patch context so the fixture-level isolation survives all calls.

## A stronger version of the idea

The highest-value loop is:

1. Recover the goal, current task, applicable decisions, and blockers.
2. Check an intended approach against current project memory.
3. Work and record the reasoning that changed.
4. Observe repository evidence and inspect declared deliverables.
5. Leave a scoped handoff containing the next concrete action and unresolved uncertainty.

The product should make this loop useful in a small existing project before expanding the number of integrations or templates. The current repository is an informative dogfood example: substantial implementation exists, yet `next` still recommends the package skeleton because evidence attribution has barely started. Preserving honesty is correct; helping users connect existing work to evidence is the missing experience.

### 1. Make recovery task-focused

Build a context view around an existing task or session. Include the north-star goal, applicable current constraints, nearby decisions and their reasons, real blockers, evidence provenance/freshness, and the next action. Include what changed since that session's last observed state.

Keep BRIEF as the broad orientation layer. A task-focused packet should supplement it and link to the full protected constraints/rejections, not silently discard them to meet a budget. Remove retired constraints from the active section while retaining history.

**Measure:** give a fresh reader or agent only the generated context and ask it to identify the goal, current work, blocker, a rejected approach and its reason, and the next action. Record accuracy and context size over a fixed set of projects. This tests the project's first success criterion directly.

### 2. Explain progress through separate evidence dimensions

Present progress, artifact presence, behavioral validation, and freshness separately. A task may have a commit on the integration branch, present files, no observed passing test result, and stale evidence. That is a useful answer without collapsing everything into a green done badge.

An explanation for a task should identify the supporting commit, attribution method, observed trunk ref, plan revision, and any invalidating observation. A claim can remain visible while its confidence is explicitly limited.

**Measure:** every derived completion shown to the user has a traceable basis; every unknown state names the missing observation. No field is manually set to done in the plan.

### 3. Help existing repositories adopt evidence honestly

Use current file-overlap proposals to produce a review queue of candidate task/commit associations. Show why an association was suggested and ambiguous alternatives. When a human accepts an association, record that declaration and provenance; do not silently upgrade the inference to verified completion. Starting a task should print the task ID and trailer guidance while respecting the no-ref-write rule.

For changed plans, show which existing evidence no longer addresses the revised deliverables. This reduces “the implementation exists but the tool thinks nothing happened” without manufacturing history.

**Measure:** a user can connect representative historical work to the plan without rewriting git history, and can explain the difference between an accepted attribution and proof of completion.

### 4. Make checking explainable and calibratable

Use one check result containing the verdict, record identity, matched clause, reason, trust, lifecycle, and related items. Preserve the existing two-floor contract. Evaluate it against a curated corpus of genuine proposal/rejection pairs, common false positives, and paraphrases.

An offline matcher can retrieve useful evidence without pretending to fully understand intent. Where polarity or scope is ambiguous, show the relevant rule and uncertainty. Later LLM refinement can offer advisory interpretation, with the offline result and authority remaining explicit.

**Measure:** track false blocks, missed explicit violations, and CLI/MCP disagreement independently. Set targets after measuring a labeled baseline; the existing numeric thresholds are not evidence of near-certainty.

### 5. Improve plan quality before adding many archetypes

The current generic and CLI templates are useful starting checklists, but task content barely depends on the goal. Ask for the first real input/output example, success criteria, hard constraints, existing deliverables, and the first demonstrable slice. Keep intake lightweight and offline.

Review the generated dependencies rather than equating listed order with technical dependency. Highlight tasks with no checkable output and phases with no concrete demo. Keep later work provisional until reviewed. First improve the two existing archetypes; more categories can follow actual usage.

**Measure:** fewer promoted tasks with missing deliverables, fewer unnecessary sequential dependencies, and a new user reaching one demonstrable outcome with little plan rewriting.

### 6. Extend verification through externally produced reports

After fixing current presence semantics, consider reading test/build reports that an external runner already produced. Record their commit, producer, format version, and observation time; treat locally editable reports as claims unless their provenance justifies more.

This is distinct from executing `cmd:` or `test:` strings. The verifier should continue to execute nothing. Start with a documented minimal report contract and fixtures; expand formats only in response to a real integration need. This proposal passed the project check while surfacing the existing execution constraint as relevant.

**Measure:** the tool distinguishes “file exists,” “a report says tests passed,” and “this report applies to the current revision,” without running any plan-supplied instruction.

### 7. Add impact and drift explanations before a single health score

Use the existing graph to answer which tasks and decisions are affected by a changed deliverable or superseded decision. Keep ownership ambiguity explicit when multiple tasks produce one file. Distinguish task-dependency paths from generic graph connections.

When T-13 scoring is implemented, display numerator, denominator, unknowns, provisional exclusions, and evidence freshness. Coverage and agreement answer different questions; a single percentage can conceal both missing evidence and weak verification. Calibrate the recorded weights and warning threshold through dogfooding.

**Measure:** a reader can explain why a score changed and identify the underlying evidence without opening source code.

## Suggested implementation sequence

This is a sequence within the existing phase skeleton, not a replacement roadmap. Every architectural direction above was checked through the source checkout's `roadmap check`; all 11 checked approaches returned exit 0. No new dependency is needed for the first four increments.

| Increment | Scope | Existing roadmap anchors | Exit evidence |
|---|---|---|---|
| 1. Consistent memory | Shared live-memory resolution; trusted transitions; checker parity; trust-preserving retrieval; real check JSON | T-02, T-05, T-06, T-22, T-27 | Lifecycle and CLI/MCP matrices pass; retired rules disappear from active views |
| 2. Reliable observations | Evidence validation and identity; unified task IDs; invalidation and freshness; truthful verification summary; path/hook boundaries | T-01, T-02, T-10, T-12, T-14, T-16, T-19 | Invalid or stale evidence cannot claim completion; hooks and verifier pass boundary tests |
| 3. Usable daily loop | Scoped start/resume/checkpoint; evidence onboarding; recomputed remaining path; coherent context refresh | T-07, T-10, T-11, T-15, T-21 | Two concurrent sessions survive independent checkpoints; fresh-session recovery exercise succeeds |
| 4. Honest release surface | Current README/changelog/architecture; interpreter/build diagnostics; packaged skill links; wheel and transport smoke tests | T-08, T-20, T-22, T-25, T-28 | Clean installation matches source capabilities; docs' first example works end to end |
| 5. Deeper planning insight | Task context, plan-quality checks, report ingestion, impact explanations, calibrated coverage/agreement | T-04, T-13, T-17 | Pilot projects can explain progress and unknowns from generated outputs |
| 6. Optional breadth | Report-only moderation, then optional LLM refinement and additional templates as usage warrants | T-18, T-23 | Core offline workflow remains complete; each addition demonstrates a measured benefit |

Ship small corrections continuously; the first two increments are not a reason to batch all fixes into one large change. Keep an evidence or compatibility migration separate from presentation fixes when it makes review clearer.

## Validation strategy for the next release

The test suite is fast and useful, but several important tests assert an output exists rather than that it is correct. For example, the MCP rejection test accepts either `REJECTED` or `CLEAR`; the architecture drift guard checks names but permits stale architectural statements. Hook tests mostly check installed text. Passing these tests therefore cannot establish the whole product promise.

Add tests at the boundaries that currently disagree:

- One canonical set of memory cases exercised through CLI, MCP, brief, graph, and HTML.
- Evidence replay cases for two observers, duplicates, conflicting IDs, plan changes, reverts, disappearing refs, and incomplete history.
- Actual generated-hook execution in controlled fixtures, including unusual directory names, custom hooks paths, linked worktrees, and neighboring content.
- Verification summaries for wholly uncheckable and mixed claims, along with ancestor-symlink and corrupted-input cases.
- A two-session workflow and a recovery exercise using only generated context.
- Clean-wheel CLI and packaged-reference checks; a real stdio MCP handshake/tool call for each supported SDK line.
- Python-version and platform checks matching the published support range, plus bounded graph/history-size benchmarks.

Preserve the read-only git guard and fixed-clock rendering tests. Improve guards so they inspect actual invocation paths and generated scripts; the absence of a suspicious import is not proof that no generated program can execute data.

The next milestone should be demonstrated by a fresh installation completing the recovery → check → observe → explain → handoff loop with consistent outputs. The current verification result remains **31 present, 4 missing, and no completion claims**; the 367 passing tests and 18 reproduced defects describe different aspects of that baseline.
