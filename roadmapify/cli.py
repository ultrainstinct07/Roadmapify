"""Command dispatch.

One ``cmd_<name>(argv) -> int`` per subcommand, plus a table. Exit codes are part
of the contract, not an afterthought — ``roadmap check`` returning 3 for "already
rejected" and 4 for "violates a constraint" is what lets a hook, a CI job or an
agent act on the answer without parsing prose.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from roadmapify import gitsync, journal, render, templates, textmatch, verify
from roadmapify.paths import (
    BRIEF_FILENAME,
    LockBusy,
    derive_lock,
    ensure_marked_block,
    out_dir,
    out_path,
    plan_path,
    project_root,
    write_text_atomic,
)
from roadmapify import status
from roadmapify.plan import (Goal, emit_plan, find_cycles, load_plan, parse_plan,
                             promote_phase, validate_plan)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_YET = 2          # the command is real but has not shipped yet
EXIT_REJECTED = 3
EXIT_CONSTRAINT = 4
#: A task whose derived status says done has a deliverable that is not on disk.
#: Distinct from 3 and 4, which are provenance-typed: each renders the journal
#: record it matched and both mean "read the reason and do not re-propose". A
#: contradiction has no record to show and the opposite remediation — do the
#: work, or fix the `produces` line.
EXIT_CONTRADICTED = 5

_GITIGNORE_START = "# >>> roadmapify >>>"
_GITIGNORE_END = "# <<< roadmapify <<<"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(msg: str) -> int:
    print(render.red("error") + f" {msg}", file=sys.stderr)
    return EXIT_ERROR


# ── shared: re-derive BRIEF.md ────────────────────────────────────────────────

def refresh_brief(root: Path, *, now: "datetime | None" = None,
                  wait: float = 5.0) -> "Path | None":
    """Re-render BRIEF.md (and graph.json) under the derive lock.

    Returns the BRIEF path, or None if the lock is busy. Callers that are hooks
    pass ``wait=0``: on contention they skip entirely, which is correct because
    the next hook fire re-derives from the same durable sources. A CLI run waits,
    then degrades to read-only rather than writing a brief that disagrees with
    what the other process is writing.
    """
    now = now or _now()
    try:
        with derive_lock(root, wait=wait):
            plan = load_plan(root)
            records = journal.load(root)
            rejections = journal.rejections(records)
            text = render.brief_md(
                plan, records, rejections, now=now,
                open_sessions=journal.open_sessions(records),
            )
            path = out_path(BRIEF_FILENAME, root=root)
            write_text_atomic(path, text)
            from roadmapify import project as _project
            if plan is not None:
                g = _project.project(plan, records, rejections)
                _project.write_graph(g, root)
            return path
    except LockBusy:
        return None


def _counts(records: list[dict], rejections: list[dict]) -> dict:
    return {
        "decisions": sum(1 for r in records if r["kind"] == "decision"),
        "rejections": len(rejections),
        "constraints": sum(1 for r in records if r["kind"] == "constraint"),
        "open questions": sum(1 for r in records if r["kind"] == "question"),
    }


# ── init ──────────────────────────────────────────────────────────────────────

def cmd_init(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap init")
    p.add_argument("goal", nargs="?", help="the one-line goal")
    p.add_argument("--why", default="", help="why this project exists at all")
    p.add_argument("--statement", default="", help="2-4 sentences: what shipping looks like")
    p.add_argument("--archetype", default="auto",
                   choices=["auto", *sorted(templates.ARCHETYPES)])
    p.add_argument("--criteria", action="append", default=[],
                   help="a checkable 'done means' sentence; repeatable")
    p.add_argument("--root", default=None)
    p.add_argument("--force", action="store_true", help="overwrite an existing roadmap.toml")
    a = p.parse_args(argv)

    if not a.goal:
        return _err('a goal is required:  roadmap init "<what you are building>"')

    root = Path(a.root).resolve() if a.root else Path.cwd().resolve()
    target = plan_path(root)
    if target.exists() and not a.force:
        return _err(f"{target} already exists — refusing to overwrite it.\n"
                    f"        `roadmap brief` shows what is in it; --force replaces it.")

    archetype = a.archetype if a.archetype != "auto" else templates.detect_archetype(root)
    now = _now()
    ts = journal.now_iso()
    goal = Goal(
        label=journal.sanitize(a.goal, limit=300),
        statement=journal.sanitize(a.statement, limit=1200),
        why=journal.sanitize(a.why, limit=1200),
        archetype=archetype,
        created=ts,
        success_criteria=tuple(journal.sanitize(c, limit=300) for c in a.criteria),
    )
    plan = templates.skeleton(archetype, goal)

    errors = validate_plan(plan)
    if errors:  # a template that cannot validate is a bug in us, not in the user
        return _err("the generated plan is invalid:\n  " + "\n  ".join(errors))

    out_dir(root).mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, emit_plan(plan))

    arch = templates.ARCHETYPES[archetype]
    seeded = 0
    for text in arch.constraints:
        journal.append(journal.record("constraint", text, ts=ts), root)
        seeded += 1
    for text in arch.risks:
        journal.append(journal.record("risk", text, ts=ts), root)
        seeded += 1
    if goal.why:
        journal.append(journal.record(
            "note", f"project started: {goal.label}", ts=ts, why=goal.why), root)
        seeded += 1

    ignore = ensure_marked_block(
        root / ".gitignore",
        "# Derived and disposable. The two append-only logs below are the durable\n"
        "# memory and ARE tracked — they carry decisions and observed git facts.\n"
        f"{out_dir(root).name}/*\n"
        f"!{out_dir(root).name}/journal.jsonl\n"
        f"!{out_dir(root).name}/evidence.jsonl\n",
        start=_GITIGNORE_START, end=_GITIGNORE_END)
    attrs = ensure_marked_block(
        root / ".gitattributes",
        "# Append-only logs with content-hashed ids: git's built-in union driver\n"
        "# merges two branches' records without a conflict and without needing a\n"
        "# custom driver installed. This works in a fresh clone.\n"
        f"{out_dir(root).name}/journal.jsonl  merge=union\n"
        f"{out_dir(root).name}/evidence.jsonl merge=union\n",
        start=_GITIGNORE_START, end=_GITIGNORE_END)

    brief = refresh_brief(root, now=now)

    n_active = sum(1 for t in plan.tasks if not t.provisional)
    print(f"detected archetype: {render.bold(archetype)}  ({arch.summary})")
    print()
    print(f"wrote {render.cyan(str(target.relative_to(root)))}"
          f"            {len(plan.phases)} phases, {len(plan.tasks)} tasks")
    window = ", ".join(p.id for p in plan.ordered_phases()[:templates.ACTIVE_PHASE_WINDOW])
    beyond = plan.ordered_phases()[templates.ACTIVE_PHASE_WINDOW:]
    print(f"      {n_active} active ({window}) · "
          f"{len(plan.tasks) - n_active} provisional"
          f"{f' ({beyond[0].id}+)' if beyond else ''}")
    if seeded:
        print(f"seeded {out_dir(root).name}/journal.jsonl    {seeded} records")
    if brief:
        print(f"wrote {out_dir(root).name}/{BRIEF_FILENAME}")
    print(f"{ignore:>9} .gitignore        {out_dir(root).name}/* with 2 negations")
    print(f"{attrs:>9} .gitattributes    merge=union on the 2 append-only logs")
    print()
    print(render.dim("Provisional tasks are visible in `roadmap tree` but excluded from"))
    print(render.dim("ready lists, verify's denominators and the health scores until"))
    print(render.dim("`roadmap expand`."))
    print(render.dim("The whole path exists; only the near part is load-bearing."))
    if not goal.why:
        print()
        print(render.yellow("no --why recorded.") + " It is the first thing lost to context")
        print("compaction and the hardest to reconstruct. Add it now:")
        print(render.dim(f'  roadmap note "why this project exists" --kind note'))
    return EXIT_OK


# ── note ──────────────────────────────────────────────────────────────────────

def cmd_note(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap note")
    p.add_argument("text", nargs="?")
    p.add_argument("--kind", default="decision", choices=list(journal.KINDS))
    p.add_argument("--rejected", action="append", default=[],
                   metavar='"<alt>: <why not>"',
                   help="an alternative this rules out; repeatable")
    p.add_argument("--why", default=None)
    p.add_argument("--about", default=None, help="comma-separated node ids, e.g. T-07,P-2")
    p.add_argument("--supersedes", default=None, metavar="ID")
    p.add_argument("--review-in", dest="review_in", default=None, metavar="90d")
    p.add_argument("--reverse-if", dest="reverse_if", default=None)
    p.add_argument("--trigger-kind", dest="trigger_kind", default="manual",
                   choices=["dep", "file", "date", "manual"])
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)

    if not a.text:
        return _err('nothing to record:  roadmap note "<what you decided>"')

    root = Path(a.root).resolve() if a.root else project_root()
    review_by = None
    if a.review_in:
        review_by = _review_date(a.review_in)
        if review_by is None:
            return _err(f"--review-in {a.review_in!r} must look like '90d', '6w' or '2026-12-01'")

    existing = journal.load(root)
    if a.supersedes and not any(r["id"] == a.supersedes for r in existing):
        return _err(f"--supersedes {a.supersedes}: no record with that id.\n"
                    f"        `roadmap brief` lists them.")

    rec = journal.record(
        a.kind, a.text,
        why=a.why,
        rejected=a.rejected,
        about=[s.strip() for s in (a.about or "").split(",") if s.strip()] or None,
        supersedes=a.supersedes,
        review_by=review_by,
        reversal_trigger=a.reverse_if,
        trigger_kind=a.trigger_kind if a.reverse_if else None,
    )
    rec = journal.append(rec, root)

    records = journal.load(root)
    rejections = journal.rejections(records)
    mine = [x for x in rejections if x["parent"] == rec["id"]]
    refresh_brief(root)
    print(render.note_screen(rec, mine, _counts(records, rejections)))
    for line in _alt_warnings(mine):
        print(line)
    return EXIT_OK


def _alt_warnings(rejections: list) -> list[str]:
    """Warn when a rejection's ``alt`` is a bare path rather than a proposal.

    ``check`` scores a proposal against the short ``alt``, so the alt's shape
    decides what the gate blocks. An alt like "roadmap-out/plan.toml" tokenises
    to nothing but this project's own nouns, and then any sentence naming the
    plan file scores against it — a measured 0.712, above the blocking floor
    (risk R-4ga5). A proposal-shaped alt does not have that problem, and this is
    the only point where anyone is in a position to fix it cheaply.
    """
    out: list[str] = []
    for x in rejections:
        alt = x.get("alt", "")
        if "/" in alt or (alt and " " not in alt.strip()):
            out.append("")
            out.append(render.yellow(f'heads up: the alt {alt!r} reads as a name, not a proposal.'))
            out.append(render.dim("  `check` scores against this label, so a bare path made of this"))
            out.append(render.dim("  project's own words will match almost anything that mentions them."))
            out.append(render.dim('  Phrase it as the thing someone would propose ("putting the plan'))
            out.append(render.dim('  file inside the output directory") and it blocks what it means to.'))
    return out


def _review_date(spec: str) -> "str | None":
    spec = spec.strip().lower()
    try:
        return datetime.strptime(spec, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass
    units = {"d": 1, "w": 7, "m": 30, "y": 365}
    if len(spec) > 1 and spec[-1] in units and spec[:-1].isdigit():
        from datetime import timedelta
        days = int(spec[:-1]) * units[spec[-1]]
        return (_now() + timedelta(days=days)).date().isoformat()
    return None


# ── check ─────────────────────────────────────────────────────────────────────

def cmd_check(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap check")
    p.add_argument("approach", nargs="?")
    p.add_argument("--root", default=None)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if not a.approach:
        return _err('nothing to check:  roadmap check "<the approach you are about to take>"')

    root = Path(a.root).resolve() if a.root else project_root()
    records = journal.load(root)
    superseded = journal.superseded_ids(records)
    # `doctor --accept X` writes a note carrying {"accepted": "X"}; the value is
    # the id being promoted, not the note's own id.
    accepted = {r["accepted"] for r in records if r.get("accepted")}

    # Foreign records — captured from other authors' commit trailers — are
    # NEVER part of the blocking set until explicitly accepted. BRIEF.md feeds
    # an agent's context, so an unvetted trailer must not be able to veto a
    # legitimate approach on somebody else's say-so.
    def enforceable(r: dict) -> bool:
        return (r.get("trust") != journal.TRUST_FOREIGN or r["id"] in accepted) \
            and r["id"] not in superseded

    rejections = [x for x in journal.rejections(records)
                  if x["parent"] not in superseded
                  and (x.get("trust") != journal.TRUST_FOREIGN or x["parent"] in accepted)]
    constraints = [r for r in records if r["kind"] == "constraint" and enforceable(r)]

    # Words that appear in most records — this project's own vocabulary —
    # inflate every score and make every proposal look related to everything.
    # They are only discoverable from the corpus, so drop them from the query
    # before scoring anything.
    corpus = [f"{x['alt']} {x.get('reason', '')}" for x in rejections] + \
             [f"{r['text']} {r.get('why', '')}" for r in constraints]
    ubiquitous = textmatch.corpus_stopwords(corpus)
    approach = textmatch.strip_tokens(a.approach, ubiquitous) or a.approach

    # The mirror of `ubiquitous`: terms this project names rarely. A
    # constraint's one-word bans ("no networkx") are only enforceable for these,
    # because a word used elsewhere is a category, not an artefact.
    #
    # And never a word the PLAN builds with. Corpus frequency cannot separate
    # "networkx" from "branch" — in a sixty-record corpus both are rare — so
    # C-dzha's "no branch, checkout, ..." blocked `bind a branch to a task, and
    # explain the mapping`, which is this CLI's own shipped description of
    # `roadmap map`. The plan's labels and intents say what we are building;
    # nothing in them can be banned by a single word.
    distinctive = textmatch.distinctive_terms(corpus)
    plan = load_plan(root)
    if plan is not None:
        distinctive -= textmatch.vocabulary(
            [plan.goal.label, plan.goal.statement or "", plan.goal.why]
            + [f"{x.label} {x.intent or ''} {x.ships or ''} "
               f"{x.exit_criteria or ''} {x.demo or ''}" for x in plan.phases]
            + [f"{t.label} {t.intent or ''}" for t in plan.tasks])

    rej_scored = textmatch.rank(
        approach,
        [(x["id"], x["alt"], f"{x.get('reason', '')} {x['parent_text']}") for x in rejections],
    )
    con_hits = {r["id"]: textmatch.constraint_score(approach, r["text"], r.get("why", ""),
                                                    distinctive=distinctive)
                for r in constraints}
    con_scored = sorted(((k, v[0]) for k, v in con_hits.items()), key=lambda kv: -kv[1])
    by_id = {x["id"]: x for x in rejections}
    by_id.update({r["id"]: r for r in constraints})

    top_rej = rej_scored[0] if rej_scored else None
    top_con = con_scored[0] if con_scored else None

    if top_rej and top_rej[1] >= textmatch.MATCH_FLOOR:
        print(render.check_screen(a.approach, "REJECTED", by_id[top_rej[0]],
                                  score=top_rej[1]))
        return EXIT_REJECTED
    if top_con and top_con[1] >= textmatch.MATCH_FLOOR:
        print(render.check_screen(a.approach, "VIOLATES_CONSTRAINT", by_id[top_con[0]],
                                  score=top_con[1], clause=con_hits[top_con[0]][1]))
        return EXIT_CONSTRAINT

    # Nothing is certain enough to block. Surface what is topically related
    # anyway: blocking on near-certainty while SURFACING on overlap is what keeps
    # the gate worth consulting. A bare "CLEAR" throws away the one thing the
    # agent came here for.
    full_text = {x["id"]: f"{x['alt']} {x.get('reason', '')} {x['parent_text']}"
                 for x in rejections}
    full_text.update({r["id"]: f"{r['text']} {r.get('why', '')}" for r in constraints})
    related = [(by_id[k], sc) for k, sc in
               sorted(rej_scored + con_scored, key=lambda kv: -kv[1])
               if sc >= textmatch.RELATED_FLOOR
               and textmatch.shared(approach, full_text[k]) >= textmatch.MIN_SHARED_TO_SURFACE
               ][:3]
    print(render.check_screen(a.approach, "CLEAR", None, related=related))
    return EXIT_OK


# ── why ───────────────────────────────────────────────────────────────────────

def cmd_why(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap why")
    p.add_argument("target", nargs="*", help="a record id, or free text")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    target = " ".join(a.target).strip()
    if not target:
        return _err('roadmap why <id | "free text">')

    root = Path(a.root).resolve() if a.root else project_root()
    records = journal.load(root)
    rejections = journal.rejections(records)
    superseded = journal.superseded_ids(records)

    exact = [r for r in records if r["id"] == target]
    if exact:
        matches = exact + [r for r in records if r.get("supersedes") == target]
    else:
        # Free text: everything above the floor, plus anything explicitly `about`
        # a node the query names.
        scored = textmatch.best(target, [(r["id"], f"{r['text']} {r.get('why', '')}")
                                         for r in records])
        keep = {rid for rid, s in scored if s >= textmatch.MATCH_FLOOR}
        keep |= {r["id"] for r in records if target in (r.get("about") or [])}
        matches = [r for r in records if r["id"] in keep]

    # Always pull in whatever reversed a matched record. A decision you find by
    # searching is worthless if the thing that overturned it is one query away —
    # that is precisely how an agent re-applies a dead rule.
    matched_ids = {r["id"] for r in matches}
    matches += [r for r in records
                if r.get("supersedes") in matched_ids and r["id"] not in matched_ids]
    matches.sort(key=lambda r: (r.get("ts", ""), r["id"]))

    reversed_by = {r["supersedes"]: r["id"] for r in records if r.get("supersedes")}
    print(render.why_screen(target, matches, rejections, superseded,
                            reversed_by=reversed_by))
    return EXIT_OK


# ── brief / build ─────────────────────────────────────────────────────────────

def cmd_brief(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap brief")
    p.add_argument("--root", default=None)
    p.add_argument("--print", dest="show", action="store_true", help="print it, not just the path")
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    path = refresh_brief(root)
    if path is None:
        print(render.yellow("another roadmap process holds the lock — nothing written."),
              file=sys.stderr)
        return EXIT_ERROR
    if a.show:
        print(path.read_text(encoding="utf-8"))
    else:
        print(str(path))
    return EXIT_OK


# ── sessions ──────────────────────────────────────────────────────────────────

def _session_disabled() -> bool:
    """`roadmap resume` writes on what reads like a read path. Let CI opt out."""
    return os.environ.get("ROADMAP_NO_SESSION", "").lower() in ("1", "true", "yes")


def cmd_resume(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap resume")
    p.add_argument("session", nargs="?", help="which unclosed session (default: the oldest)")
    p.add_argument("--root", default=None)
    p.add_argument("--list", action="store_true")
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()

    open_ = journal.open_sessions(root=root)
    if not open_:
        print(render.resume_screen(None, None, now=_now()))
        return EXIT_OK

    if a.list or (len(open_) > 1 and not a.session):
        # Two agents in two worktrees is the normal case, so never silently
        # assume "the open one".
        print(render.yellow(f"{len(open_)} unclosed sessions:"))
        for s in open_:
            print(f"  {render.bold(s['id'])}  {render.ago(s.get('ts', ''), _now())} ago"
                  f"  {s.get('branch') or '—'}  {s.get('text', '')[:60]}")
        if a.list:
            return EXIT_OK
        print()
        print(render.dim("pick one:  roadmap resume <id>"))
        return EXIT_OK

    target = next((s for s in open_ if s["id"] == a.session), None) if a.session else open_[0]
    if target is None:
        return _err(f"no unclosed session {a.session!r}")

    new_id = None
    if not _session_disabled():
        ts = journal.now_iso()
        rec = journal.record("session_open", f"resumed from {target['id']}", ts=ts,
                             extra={"resumed_from": target["id"]})
        rec["id"] = journal.new_session_id(ts, rec["text"])
        journal.append(rec, root)
        new_id = rec["id"]
        refresh_brief(root)
    print(render.resume_screen(target, new_id, now=_now()))
    return EXIT_OK


def cmd_checkpoint(argv: list[str]) -> int:
    """Close the open session. ``--auto`` is the hook path: silent and never fails."""
    p = argparse.ArgumentParser(prog="roadmap checkpoint")
    p.add_argument("text", nargs="?", default="session ended")
    p.add_argument("--auto", action="store_true", help="hook mode: quiet, always exit 0")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    try:
        root = Path(a.root).resolve() if a.root else project_root()
        open_ = journal.open_sessions(root=root)
        if not open_:
            if not a.auto:
                print(render.dim("no open session to close."))
            return EXIT_OK
        for s in open_:
            rec = journal.record("session_close", a.text, extra={"session": s["id"]})
            journal.append(rec, root)
        refresh_brief(root, wait=0.0)
        if not a.auto:
            print(f"closed {', '.join(s['id'] for s in open_)}")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - a hook must never break the commit
        if a.auto:
            return EXIT_OK
        return _err(str(exc))


# ── doctor ────────────────────────────────────────────────────────────────────

def cmd_doctor(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap doctor")
    p.add_argument("--root", default=None)
    p.add_argument("--accept", metavar="ID", default=None,
                   help="promote a foreign (commit-trailer) record into the enforced set")
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    records = journal.load(root)

    if a.accept:
        target = next((r for r in records if r["id"] == a.accept), None)
        if target is None:
            return _err(f"no record {a.accept!r}")
        journal.append(journal.record(
            "note", f"accepted {a.accept} into the enforced set",
            extra={"accepted": a.accept}), root)
        refresh_brief(root)
        print(f"{a.accept} is now enforced by `roadmap check`.")
        return EXIT_OK

    problems: list[str] = []
    plan = load_plan(root)
    if plan is None:
        problems.append("no roadmap.toml — run `roadmap init \"<goal>\"`")
    else:
        problems += validate_plan(plan)
        for cyc in find_cycles(plan):
            problems.append("dependency cycle: " + " -> ".join(cyc))
        if not plan.goal.why:
            problems.append("[goal] has no `why` — the first thing lost to compaction")

    for s in journal.open_sessions(records):
        problems.append(f"unclosed session {s['id']} "
                        f"({render.ago(s.get('ts', ''), _now())} ago) — `roadmap resume`")
    today = _now().date().isoformat()
    for r in records:
        if r.get("review_by") and r["review_by"] <= today:
            problems.append(f"{r['id']} was due for review on {r['review_by']}: {r['text'][:60]}")
    foreign = [r for r in records if r.get("trust") == journal.TRUST_FOREIGN]
    if foreign:
        problems.append(f"{len(foreign)} record(s) captured from other authors' commit "
                        f"trailers are informational only — `roadmap doctor --accept <id>` "
                        f"to enforce one")

    print(f"roadmapify doctor · {root}")
    print(f"  plan      {'ok' if plan and not validate_plan(plan) else 'see below'}")
    print(f"  journal   {len(records)} records, {len(journal.rejections(records))} rejections")
    print()
    if not problems:
        print(render.green("nothing to report."))
        return EXIT_OK
    for prob in problems:
        print(f"  {render.yellow('!')} {prob}")
    return EXIT_OK


# ── install ──────────────────────────────────────────────────────────────────

def cmd_install(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap install")
    p.add_argument("--root", default=None)
    p.add_argument("--uninstall", action="store_true")
    p.add_argument("--skill", action="store_true",
                   help="also install the skill (default platform: claude)")
    p.add_argument("--platform", default=None,
                   choices=["claude", "cursor", "agents"],
                   help="install the skill for one host")
    p.add_argument("--project", action="store_true",
                   help="install the skill into the project tree, not $HOME")
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()

    from roadmapify import install as _install

    if a.uninstall:
        if a.platform or a.skill:
            platform = a.platform or "claude"
            for target, state in _install.uninstall_skill(
                    platform, project=a.project, root=root).items():
                print(f"  {target:<48} {state}")
        for target, state in _install.uninstall_always_on(root).items():
            print(f"  {target:<48} {state}")
        return EXIT_OK

    results = _install.install_always_on(root)
    for target, state in results.items():
        print(f"  {target:<48} {state}  (always-on block)")

    if a.platform or a.skill:
        platform = a.platform or "claude"
        try:
            skill_results = _install.install_skill(
                platform, project=a.project or (platform == "cursor"), root=root)
        except (ValueError, RuntimeError) as exc:
            return _err(str(exc))
        for target, state in skill_results.items():
            print(f"  {target:<48} {state}  (skill)")
    else:
        print()
        print(render.dim("Agents now read the roadmap rules with no trigger and no skill."))
        print(render.dim("Add the skill:  roadmap install --skill"))
        print(render.dim("                roadmap install --platform cursor"))
    return EXIT_OK


# ── export ───────────────────────────────────────────────────────────────────

def cmd_export(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap export")
    p.add_argument("format", nargs="?", default="html", choices=["html"])
    p.add_argument("--root", default=None)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()

    plan = load_plan(root)
    if plan is None:
        return _err('no roadmap.toml here — run `roadmap init "<goal>"` first')

    from roadmapify import export as _export

    records = journal.load(root)
    html = _export.to_html(plan, records, journal.rejections(records),
                           now=_now(), project=root.name)
    out = Path(a.out) if a.out else out_path("roadmap.html", root=root)
    write_text_atomic(out, html)
    print(str(out))
    print(render.dim(f"  {len(plan.tasks)} tasks · {len(records)} records · "
                     f"{len(html) // 1024} KB · opens offline"))
    return EXIT_OK


# ── graph query surface ───────────────────────────────────────────────────────

def _load_or_build_graph(root: Path) -> "dict | None":
    """Prefer the on-disk graph; rebuild once if missing so a fresh clone works."""
    from roadmapify import project as _project
    from roadmapify import traverse as _traverse

    g = _traverse.ensure_graph(root)
    if g is not None:
        return g
    # No graph yet — derive it (also refreshes BRIEF).
    refresh_brief(root)
    return _traverse.ensure_graph(root)


def cmd_explain(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap explain")
    p.add_argument("target", nargs="*", help="a node id, or free text")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    target = " ".join(a.target).strip()
    if not target:
        return _err('roadmap explain <id | "text">')
    root = Path(a.root).resolve() if a.root else project_root()
    from roadmapify import traverse as _traverse
    g = _load_or_build_graph(root)
    if g is None:
        return _err("no graph — run `roadmap build` first")
    print(_traverse.explain_screen(_traverse.explain(g, target), target))
    return EXIT_OK


def cmd_path(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap path")
    p.add_argument("source", nargs="?", help="start node id or text")
    p.add_argument("target", nargs="?", help="end node id or text")
    p.add_argument("--root", default=None)
    p.add_argument("--max-hops", type=int, default=12)
    a = p.parse_args(argv)
    if not a.source or not a.target:
        return _err('roadmap path <source> <target>')
    root = Path(a.root).resolve() if a.root else project_root()
    from roadmapify import traverse as _traverse
    g = _load_or_build_graph(root)
    if g is None:
        return _err("no graph — run `roadmap build` first")
    hops = _traverse.shortest_path(g, a.source, a.target, max_hops=a.max_hops)
    print(_traverse.path_screen(hops, a.source, a.target))
    return EXIT_OK if hops is not None else EXIT_ERROR


def cmd_query(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap query")
    p.add_argument("question", nargs="*", help="plain-language question")
    p.add_argument("--root", default=None)
    p.add_argument("--dfs", action="store_true", help="depth-first instead of BFS")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--budget", type=int, default=2000)
    a = p.parse_args(argv)
    question = " ".join(a.question).strip()
    if not question:
        return _err('roadmap query "<question>"')
    root = Path(a.root).resolve() if a.root else project_root()
    from roadmapify import traverse as _traverse
    g = _load_or_build_graph(root)
    if g is None:
        return _err("no graph — run `roadmap build` first")
    result = _traverse.query(
        g, question, mode="dfs" if a.dfs else "bfs",
        depth=a.depth, budget=a.budget,
    )
    print(_traverse.query_screen(result))
    return EXIT_OK


# ── hooks ─────────────────────────────────────────────────────────────────────

def cmd_hook(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap hook")
    p.add_argument("action", nargs="?", default="status",
                   choices=["install", "uninstall", "status"])
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    from roadmapify import hooks as _hooks

    if a.action == "install":
        try:
            results = _hooks.install(root)
        except FileNotFoundError as exc:
            return _err(str(exc))
        for name, state in results.items():
            print(f"  {name:<14} {state}")
        print()
        print(render.dim("hooks refresh BRIEF.md + graph.json after commit/checkout."))
        print(render.dim("they never write a git ref. set ROADMAP_SKIP_HOOK=1 to opt out."))
        return EXIT_OK
    if a.action == "uninstall":
        results = _hooks.uninstall(root)
        for name, state in results.items():
            print(f"  {name:<14} {state}")
        return EXIT_OK
    # status
    results = _hooks.status(root)
    for name, state in results.items():
        print(f"  {name:<14} {state}")
    return EXIT_OK


# ── not yet built ─────────────────────────────────────────────────────────────

#: Distinct from 0 (clear), 1 (error), 3 (rejected) and 4 (constraint), so a
#: caller can tell "this command does not exist yet" from "this command failed".

#: The always-on block and the README name the whole command surface, because
#: that text has to be stable — rewriting an agent's instructions every phase is
#: how those instructions stop being trusted. So the commands that have not
#: shipped answer for themselves instead of falling through to
#: "unknown command", which reads to an agent as a broken install and sends it
#: back to guessing.

# ── the plan screens ──────────────────────────────────────────────────────────

def _plan_or_fail(root):
    """(plan, snapshot) or (None, exit code).

    A plan with a cycle has no order and therefore no 'next task', which is the
    only question these three commands exist to answer — so a cycle fails them
    all, naming every hop. That is P-2's exit criterion.
    """
    plan = load_plan(root)
    if plan is None:
        return None, None, _err('no roadmap.toml here — run `roadmap init "<goal>"` first')
    errors, cycles = validate_plan(plan), find_cycles(plan)
    if errors or cycles:
        print(render.plan_errors_screen(errors, cycles), file=sys.stderr)
        return None, None, EXIT_ERROR
    snap = status.snapshot(plan, status.load_evidence(root), now=_now())
    return plan, snap, EXIT_OK


def cmd_next(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap next")
    p.add_argument("--phase", default=None, metavar="P-n")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    plan, snap, code = _plan_or_fail(root)
    if plan is None:
        return code
    if a.phase and plan.phase(a.phase) is None:
        return _err(f"no phase {a.phase!r} in this plan.")
    print(render.next_screen(plan, snap, now=_now(), phase=a.phase))
    return EXIT_OK


def cmd_tree(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap tree")
    p.add_argument("--phase", default=None, metavar="P-n")
    p.add_argument("--hide-provisional", dest="hide_provisional", action="store_true")
    p.add_argument("--deps", action="store_true", help="show each task's dependencies")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    plan, snap, code = _plan_or_fail(root)
    if plan is None:
        return code
    if a.phase and plan.phase(a.phase) is None:
        return _err(f"no phase {a.phase!r} in this plan.")
    print(render.tree_screen(plan, snap, now=_now(), only_phase=a.phase,
                             hide_provisional=a.hide_provisional, show_deps=a.deps))
    return EXIT_OK


def _lost_lines(disk: str, emitted: str) -> "list[str]":
    """Lines the round trip cannot reproduce — hand-written comments, unknown
    keys, a hand ordering. ``difflib`` is stdlib; nothing new is pulled in."""
    import difflib
    return [ln[1:] for ln in difflib.unified_diff(
        disk.splitlines(), emitted.splitlines(), n=0, lineterm="")
        if ln.startswith("-") and not ln.startswith("---")]


def cmd_sync(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap sync")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)
    root = Path(a.root).resolve() if a.root else project_root()
    plan = load_plan(root)
    if plan is None:
        return _err('no roadmap.toml here — run `roadmap init "<goal>"` first')

    observed = gitsync.facts(root)
    records = ()
    added = []
    dropped = ()
    proposals = {}
    if observed.ok:
        mine = gitsync.identity_email(observed.identity)
        records = gitsync.records_from(observed, plan, mine=mine)
        dropped = gitsync.dropped_ids(observed, plan)
        proposals = gitsync.proposals(observed, plan)
        if not a.dry_run:
            added = gitsync.append_new(root, records)
            refresh_brief(root)
    print(render.sync_screen(observed, records, added, dropped=dropped,
                             proposals=proposals, dry_run=a.dry_run, now=_now()))
    return EXIT_OK if observed.ok else EXIT_ERROR


def cmd_verify(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap verify")
    p.add_argument("task", nargs="?", metavar="T-nn",
                   help="one task (default: every load-bearing task)")
    p.add_argument("--phase", default=None, metavar="P-n")
    p.add_argument("--claimed-only", dest="claimed_only", action="store_true",
                   help="only tasks evidence says are done")
    p.add_argument("--json", action="store_true",
                   help="the whole report on stdout, machine-readable")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)

    root = Path(a.root).resolve() if a.root else project_root()
    plan, snap, code = _plan_or_fail(root)
    if plan is None:
        return code
    if a.task and plan.phase(a.task) is not None:
        return _err(f"{a.task} is a phase — try `roadmap verify --phase {a.task}`")
    if a.task and plan.task(a.task) is None:
        return _err(f"no task {a.task!r} in this plan.")
    if a.phase and plan.phase(a.phase) is None:
        return _err(f"no phase {a.phase!r} in this plan.")

    report = verify.verify(plan, snap, root, task=a.task or "",
                           phase=a.phase or "", claimed_only=a.claimed_only)
    now = _now()
    # No refresh_brief. Four shipped cmd_ bodies call it, so anyone starting
    # from a copy silently turns a read command into a writer.
    print(render.verify_json(report, now=now) if a.json
          else render.verify_screen(plan, report, now=now))
    return EXIT_CONTRADICTED if report.contradicted else EXIT_OK


def cmd_expand(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="roadmap expand")
    p.add_argument("phase", nargs="?", metavar="P-n")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="rewrite even if hand edits would be lost")
    p.add_argument("--llm", action="store_true")
    p.add_argument("--root", default=None)
    a = p.parse_args(argv)

    if a.llm:
        # P-9's `ships` names `roadmap expand P-3 --llm` literally, so a promised
        # capability answers for itself rather than being silently ignored.
        print(f"{render.yellow('not built yet')} — `roadmap expand --llm` ships in "
              f"{render.bold('P-9')}.")
        print("  it will: refine the tasks inside this fixed phase skeleton")
        print()
        print(render.dim("`roadmap expand <P-n>` promotes them offline today."))
        return EXIT_NOT_YET

    if not a.phase:
        return _err('which phase?  roadmap expand P-3')
    root = Path(a.root).resolve() if a.root else project_root()
    plan, snap, code = _plan_or_fail(root)
    if plan is None:
        return code
    ph = plan.phase(a.phase)
    if ph is None:
        return _err(f"no phase {a.phase!r} in this plan.")

    was = len([t for t in plan.tasks if not t.provisional])
    promoted = tuple(sorted(t.id for t in plan.tasks_of(ph.id) if t.provisional))
    after = promote_phase(plan, ph.id)
    emitted = emit_plan(after)
    path = plan_path(root)
    disk = path.read_text(encoding="utf-8")

    # The faithfulness check. emit_plan reproduces a FIXED set of comments and
    # sorts tasks; it round-trips a generated file byte for byte and silently
    # eats anything else. roadmap.toml's own header says "Hand-edit this file",
    # so writing blind here is data loss with no undo outside git.
    faithful = emit_plan(parse_plan(disk)) == disk
    if not faithful and not a.force:
        print(render.rewrite_refusal_screen(
            _lost_lines(disk, emit_plan(parse_plan(disk))),
            f"roadmap expand {ph.id}"), file=sys.stderr)
        return EXIT_ERROR

    if a.dry_run:
        print(render.expand_screen(after, after.phase(ph.id), promoted, wrote=False,
                                   brief=False,
                                   load_bearing=len([t for t in after.tasks
                                                     if not t.provisional]), was=was))
        print()
        print(render.dim("--dry-run: nothing was written."))
        return EXIT_OK

    warnings = tuple(
        f"{p2.id} is still provisional, so the load-bearing set now has a gap. "
        "That is allowed; the health denominators will show it."
        for p2 in after.ordered_phases()
        if p2.provisional and p2.order < ph.order)

    write_text_atomic(path, emitted)
    brief = refresh_brief(root) is not None
    print(render.expand_screen(after, after.phase(ph.id), promoted, wrote=True,
                               brief=brief,
                               load_bearing=len([t for t in after.tasks
                                                 if not t.provisional]),
                               was=was, warnings=warnings))
    return EXIT_OK


NOT_YET = {
    "start": ("P-3", "open a session and print the branch name and commit trailer"),
    "done": ("P-3", "record a self-report, pending merge evidence"),
    "map": ("P-3", "bind a branch to a task, and explain the mapping"),
    "moderate": ("P-6", "branch hygiene findings with the exact git command for each"),
}


def cmd_not_yet(name: str):
    def run(argv: list[str]) -> int:
        phase, what = NOT_YET[name]
        print(f"{render.yellow('not built yet')} — `roadmap {name}` ships in {render.bold(phase)}.")
        print(f"  it will: {what}")
        print()
        # Derived, not hand-written: this list had already lost `export` and
        # `build`, and it is the text an agent reads every time it reaches for a
        # verb that has not shipped. Five more phases will each add commands.
        shipped = " · ".join(n for n in COMMANDS if n not in NOT_YET)
        for i, chunk in enumerate(render.wrap(shipped, 58)):
            head = "what works today:  " if i == 0 else "                   "
            print(render.dim(head + chunk))
        return EXIT_NOT_YET
    return run


# ── dispatch ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "init": cmd_init,
    "note": cmd_note,
    "check": cmd_check,
    "why": cmd_why,
    "brief": cmd_brief,
    # `build` re-derives everything. In this phase the derived artifacts are
    # BRIEF.md and graph.json, but the name is already in the always-on block
    # and the skill's fresh-clone path, so it must never dead-end.
    "build": cmd_brief,
    "resume": cmd_resume,
    "checkpoint": cmd_checkpoint,
    "doctor": cmd_doctor,
    "install": cmd_install,
    "export": cmd_export,
    "next": cmd_next,
    "tree": cmd_tree,
    "verify": cmd_verify,
    "sync": cmd_sync,
    "expand": cmd_expand,
    "explain": cmd_explain,
    "path": cmd_path,
    "query": cmd_query,
    "hook": cmd_hook,
}

# setdefault, not a dict spread. The spread sat LAST in the literal, so
# implementing a command and forgetting to delete its NOT_YET entry silently
# reinstalled the stub, with exit 2 as the only symptom. Five more phases each
# add commands this way.
for _name in NOT_YET:
    COMMANDS.setdefault(_name, cmd_not_yet(_name))

HELP = """roadmap - a phased build plan with memory that survives context loss

  roadmap init "<goal>" [--why "..."] [--archetype auto|generic|cli]
                        write roadmap.toml with the complete phased path
  roadmap note "<text>" [--kind decision|constraint|risk|note|question]
                        [--rejected "<alt>: <why not>"] [--why ...] [--about T-07]
                        [--supersedes ID] [--review-in 90d] [--reverse-if "..."]
                        record memory. Only the text is required.
  roadmap check "<approach>"
                        is this already ruled out?  exit 0 clear · 3 rejected
                        · 4 violates a constraint
  roadmap why <id | "text">
                        what was decided about this, and what it reversed
  roadmap next [--phase P-n]
                        the active phase, its gate, the ready tasks and the
                        critical path
  roadmap tree [--phase P-n] [--hide-provisional] [--deps]
                        the whole plan; provisional tasks are marked `prov`
  roadmap sync [--dry-run]
                        read git and record what it says about each task.
                        Reads only: it never writes a git ref
  roadmap verify [T-nn] [--phase P-n] [--claimed-only] [--json]
                        each deliverable: present · missing · unverifiable ·
                        unresolvable.  exit 5 if a task claiming done is
                        missing one. Nothing is ever executed
  roadmap expand <P-n> [--dry-run] [--force]
                        promote a provisional phase into the load-bearing set.
                        Offline, it only clears the flag
  roadmap brief [--print]
                        re-render roadmap-out/BRIEF.md  (alias: roadmap build)
  roadmap explain <id | "text">
                        one node: source, degree, neighbors tagged EXTRACTED/INFERRED
  roadmap path <a> <b>
                        shortest path between two nodes
  roadmap query "<question>"
                        scoped subgraph for a plain-language question
  roadmap hook install|status|uninstall
                        keep BRIEF.md + graph.json current after commit/checkout
  roadmap resume [<id>] [--list]
                        recover an interrupted session
  roadmap checkpoint ["text"] [--auto]
                        close the open session
  roadmap doctor [--accept ID]
                        problems worth knowing about
  roadmap install [--uninstall] [--skill] [--platform claude|cursor|agents] [--project]
                        always-on block into CLAUDE.md / AGENTS.md; optional skill
  roadmap export [html] [--out PATH]
                        one self-contained page: the path, and the memory

Every command takes --root to work on another directory.
Env: ROADMAP_OUT (output dir) · ROADMAP_NO_SESSION=1 (never write session records)
     NO_COLOR · ROADMAP_AUTHOR
"""


def dispatch(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return EXIT_OK
    if argv[0] in ("-v", "--version", "version"):
        from roadmapify import __version__
        print(__version__)
        return EXIT_OK
    cmd, rest = argv[0], argv[1:]
    fn = COMMANDS.get(cmd)
    if fn is None:
        near = textmatch.nearest(cmd, COMMANDS)
        hint = f"  did you mean `{near}`?" if near else ""
        print(f"unknown command {cmd!r}.{hint}\n\n{HELP}", file=sys.stderr)
        return EXIT_ERROR
    return fn(rest)
