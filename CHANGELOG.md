# Changelog

## 0.1.0 (unreleased)

P-1 "It remembers", the first of nine phases. The memory layer, shipped before any
graph, git sync or phase logic.

- Feature: `roadmap init` writes a `roadmap.toml` carrying the complete phased path
  from an offline archetype skeleton — no API key, no network. Later phases are
  emitted `provisional` so the whole path exists without an unreviewed guess being
  able to make the roadmap lie about itself.
- Feature: `roadmap note` records decisions, constraints, risks, notes and questions
  to an append-only JSONL log with content-hashed ids. Rejections are first-class
  records with their own ids, not strings buried in a field — which is what makes
  them citable and enforceable.
- Feature: `roadmap check "<approach>"` exits 3 if the project already rejected the
  approach and 4 if it violates a recorded constraint, always printing the reason,
  the date, and the supersession escape hatch. Below the blocking floor it still
  *surfaces* related records rather than returning a bare "clear".
- Feature: `roadmap why` traces a record and always pulls in whatever reversed it —
  finding a dead rule without its killer is how an agent re-applies it.
- Feature: `roadmap resume` / `checkpoint`. A `session_open` with no matching close
  is the crash flag; SIGKILL, a closed tab and a mid-task compaction all leave one.
  Multiple open sessions are listed, never guessed between.
- Feature: `roadmap export html` renders the whole roadmap — the phased path and
  the memory beside it — as one self-contained page. It makes **zero network
  requests**: system font stacks, no CDN, no font host, so it opens on an
  air-gapped machine, survives being emailed, and still works in five years. The
  dependency graph is interactive rather than drawn: selecting a task lights its
  transitive blockers and dependents and dims the rest.
- Feature: `roadmap install` writes the always-on block into `CLAUDE.md` and
  `AGENTS.md` between markers, so another tool's block survives untouched. It
  creates `CLAUDE.md` when a project has neither file — otherwise a project
  silently gets no always-on layer, which is the one failure that makes the whole
  design inert.
- Feature: commands that have not shipped yet answer for themselves with exit 2
  and name the phase that brings them, instead of falling through to "unknown
  command". The always-on block names the full surface on purpose — rewriting an
  agent's standing instructions every phase is how those instructions stop being
  trusted.
- Feature: `roadmap doctor`, including `--accept` for promoting a commit-trailer
  record into the enforced set.
- Feature: `roadmap init` appends `merge=union` for both append-only logs to
  `.gitattributes` and the derived-output rules to `.gitignore`, both between
  markers so a re-run replaces rather than duplicates and hand-written neighbours
  survive.
- Security: records captured from other authors carry `trust: foreign`, render in a
  separate "recorded by others (not enforced)" section of BRIEF.md, and cannot gate
  `roadmap check` until explicitly accepted. BRIEF.md is injected into an agent's
  context, so an unvetted commit trailer must not read as project law. ANSI and
  control characters are stripped on ingest.
- Fix: `append_jsonl` is a single `write(2)` under `O_APPEND` with no pre-read. The
  first version probed the file's trailing byte to heal a torn write and raced under
  concurrent appenders, occasionally splitting a record. Torn-write recovery moved to
  the read side, where it costs nothing until something is actually broken.
- Fix: the token folder now folds `cache`/`caching`/`cached` to one stem. Without the
  final silent-`e` strip the two most common forms never matched each other.
- Fix: the single-word discount in `proposal_score` applies to both scoring paths.
  Applied only to coverage, the general-similarity fallback routed around it.
- Fix: `check` scores against a rejection's short `alt` label, not its full text.
  Scoring the whole record buried "bidirectional sync" under the paragraph of
  reasoning attached to it, so a near-verbatim re-proposal scored 0.40 and sailed
  through the gate.
- Fix: constraints phrased as prohibitions have the forbidden phrase extracted
  ("no interactive OAuth" -> "interactive OAuth") and matched like a rejection's
  `alt`. A constraint's own words include the alternative it *prescribes*, which
  otherwise dilutes the ban into noise. Prohibitions are read from the `why` too,
  since rules are usually stated abstractly and the banned things named in the
  reason.
- Fix: surfacing requires a minimum count of shared tokens, not just a score. One
  shared word out of four scores ~0.24, which sits just above any usefully low
  floor — so a single incidental overlap made every proposal look related to
  everything.
- Fix: tokens appearing in more than 40% of the records are dropped before
  scoring. The global stoplist can only hold words that are uninformative in
  English; the words that actually ruin matching are the project's own vocabulary.
