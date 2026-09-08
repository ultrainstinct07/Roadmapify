"""End-to-end behaviour, including the exit-code contract.

Exit codes are part of the interface: a hook, a CI job or an agent has to act on
`roadmap check` without parsing prose. 0 clear · 1 error · 3 already rejected ·
4 violates a constraint.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from roadmapify import cli, journal
from roadmapify.cli import (EXIT_CONSTRAINT, EXIT_ERROR, EXIT_NOT_YET, EXIT_OK,
                            EXIT_REJECTED)
from roadmapify.paths import BRIEF_FILENAME, out_path, plan_path


# ── init ──────────────────────────────────────────────────────────────────────

def test_init_works_with_no_git_repo_at_all(project, run):
    """The flagship flow is 'start a new project', which is exactly the state
    with no repo and no commits."""
    assert not (project / ".git").exists()
    code, out = run("init", "sync notion to markdown", "--root", str(project),
                    "--why", "I keep losing notes")
    assert code == EXIT_OK
    assert plan_path(project).is_file()
    assert out_path(BRIEF_FILENAME, root=project).is_file()
    assert "I keep losing notes" in out_path(BRIEF_FILENAME, root=project).read_text()


def test_init_refuses_to_overwrite_without_force(project, run):
    run("init", "a thing", "--root", str(project))
    before = plan_path(project).read_text()
    code, _ = run("init", "a different thing", "--root", str(project))
    assert code == EXIT_ERROR
    assert plan_path(project).read_text() == before
    code, _ = run("init", "a different thing", "--root", str(project), "--force")
    assert code == EXIT_OK
    assert "a different thing" in plan_path(project).read_text()


def test_init_wires_git_metadata_idempotently(project, run):
    run("init", "a thing", "--root", str(project))
    ignore = (project / ".gitignore").read_text()
    attrs = (project / ".gitattributes").read_text()
    assert "roadmap-out/*" in ignore
    assert "!roadmap-out/journal.jsonl" in ignore, "the memory must stay tracked"
    assert "merge=union" in attrs, "union merge is what makes concurrent branches safe"
    run("init", "a thing", "--root", str(project), "--force")
    assert (project / ".gitignore").read_text().count(">>> roadmapify >>>") == 1


def test_init_preserves_an_existing_gitignore(project, run):
    (project / ".gitignore").write_text("*.pyc\n__pycache__/\n")
    run("init", "a thing", "--root", str(project))
    assert "*.pyc" in (project / ".gitignore").read_text()


def test_init_nags_when_the_why_is_missing(project, run):
    _, out = run("init", "a thing", "--root", str(project))
    assert "no --why recorded" in out


def test_init_without_a_goal_is_an_error(project, run):
    code, _ = run("init", "--root", str(project))
    assert code == EXIT_ERROR


# ── note ──────────────────────────────────────────────────────────────────────

def test_note_records_a_decision_with_first_class_rejections(seeded, run):
    code, out = run("note", "use sqlite", "--root", str(seeded),
                    "--rejected", "flat json: no partial reads",
                    "--why", "we need range queries")
    assert code == EXIT_OK
    assert "flat json" in out and "no partial reads" in out
    xs = journal.rejections(root=seeded)
    assert any(x["alt"] == "flat json" for x in xs)


def test_note_only_needs_text(seeded, run):
    assert run("note", "just a thought", "--root", str(seeded))[0] == EXIT_OK


def test_note_rejects_an_unknown_supersedes_target(seeded, run):
    code, _ = run("note", "reversing", "--root", str(seeded), "--supersedes", "D-nope")
    assert code == EXIT_ERROR


def test_note_refreshes_the_brief(seeded, run):
    run("note", "a memorable thing", "--root", str(seeded))
    assert "a memorable thing" in out_path(BRIEF_FILENAME, root=seeded).read_text()


@pytest.mark.parametrize("spec,ok", [("90d", True), ("6w", True), ("2026-12-01", True),
                                     ("soon", False), ("", False)])
def test_review_in_accepts_durations_and_dates(seeded, run, spec, ok):
    code, _ = run("note", "x", "--root", str(seeded), *(["--review-in", spec] if spec else []))
    assert (code == EXIT_OK) is (ok or not spec)


# ── check: the gate ───────────────────────────────────────────────────────────

def test_check_blocks_a_restatement_of_a_rejection(seeded, run):
    code, out = run("check", "add a status field to roadmap.toml so reads are cheap",
                    "--root", str(seeded))
    assert code == EXIT_REJECTED
    assert "REJECTED" in out
    assert "goes stale the day you write it" in out, "the reason is mandatory"
    assert "--supersedes" in out, "there must always be an escape hatch"


def test_check_blocks_a_constraint_violation(seeded, run):
    code, out = run("check", "call the network at import time to warm the cache",
                    "--root", str(seeded))
    assert code == EXIT_CONSTRAINT
    assert "VIOLATES CONSTRAINT" in out


def test_check_clears_an_unrelated_proposal(seeded, run):
    code, out = run("check", "add a --verbose flag", "--root", str(seeded))
    assert code == EXIT_OK and "CLEAR" in out


def test_note_warns_when_a_rejection_alt_is_a_bare_path(seeded, run):
    """`check` scores against the short alt, so the alt's shape decides what the
    gate blocks. A path made of this project's own nouns matches anything that
    mentions them (risk R-4ga5), and recording it is the only cheap moment to
    say so."""
    code, out = run("note", "the plan file lives at the project root",
                    "--rejected", "roadmap-out/plan.toml: it buries the hand-edited file",
                    "--root", str(seeded))
    assert code == EXIT_OK
    assert "reads as a name, not a proposal" in out


def test_note_does_not_warn_about_a_proposal_shaped_alt(seeded, run):
    code, out = run("note", "the plan file lives at the project root",
                    "--rejected", "putting the plan file inside the output directory: "
                                  "it invites people to edit the derived ones",
                    "--root", str(seeded))
    assert code == EXIT_OK
    assert "reads as a name" not in out


def test_check_blocks_a_library_a_constraint_bans_by_name(seeded, run):
    """The single most obviously forbidden proposal a project has is a library
    its constraints name. Those bans are written as one word ("no networkx"),
    and one-word prohibitions used to be discarded outright — so the gate
    cleared the one proposal it most obviously exists to stop."""
    from roadmapify import journal
    journal.append(journal.record(
        "constraint", "base install has exactly one conditional dependency",
        ts="2026-01-04T00:00:00Z", author="test",
        why="we parse one TOML file. No networkx (stdlib graphlib), no rapidfuzz",
    ), seeded)
    # rare_tokens needs a real corpus before it will call any term distinctive,
    # and that corpus is the enforceable set: rejections and constraints.
    for i, text in enumerate((
        "the plan file lives at the project root, never inside the output dir",
        "records are content hashed so a union merge stays safe",
        "every render function takes now as an argument",
        "the brief is capped so an agent can afford to read it",
    )):
        journal.append(journal.record("constraint", text, author="test",
                                      ts=f"2026-01-1{i}T00:00:00Z"), seeded)

    code, out = run("check", "use networkx for the task DAG", "--root", str(seeded))
    assert code == EXIT_CONSTRAINT
    assert "networkx" in out, "the screen must name the clause it matched"

    code, _ = run("check", "use stdlib graphlib for the task DAG", "--root", str(seeded))
    assert code == EXIT_OK, "the prescribed alternative must not be blocked"


def test_check_surfaces_related_items_without_blocking(seeded, run):
    """Block on near-certainty, surface on overlap. A bare CLEAR throws away the
    one thing the caller came for."""
    code, out = run("check", "read the status of each task from the evidence log",
                    "--root", str(seeded))
    assert code == EXIT_OK
    assert "related, worth reading" in out


def test_a_superseded_rejection_stops_blocking(seeded, run):
    code, _ = run("check", "add a status field to roadmap.toml", "--root", str(seeded))
    assert code == EXIT_REJECTED
    parent = next(r for r in journal.load(seeded) if r["kind"] == "decision")
    run("note", "reversing: we measured and deriving is too slow at 10k tasks",
        "--root", str(seeded), "--supersedes", parent["id"])
    code, _ = run("check", "add a status field to roadmap.toml", "--root", str(seeded))
    assert code == EXIT_OK, "a reversed decision must stop gating"


def test_foreign_records_do_not_block_until_accepted(project, run):
    """A `Decision:` trailer in someone else's merged commit is untrusted input.
    It must not be able to veto a legitimate approach on their say-so."""
    journal.append(journal.record(
        "decision", "never use sqlite here", author="stranger",
        trust=journal.TRUST_FOREIGN, rejected=["sqlite: too slow"]), project)
    code, _ = run("check", "use sqlite for the cache", "--root", str(project))
    assert code == EXIT_OK, "an unvetted foreign record must not gate"
    rec = journal.load(project)[0]
    run("doctor", "--root", str(project), "--accept", rec["id"])
    code, _ = run("check", "use sqlite for the cache", "--root", str(project))
    assert code == EXIT_REJECTED, "once accepted, it gates like any other record"


def test_check_without_an_argument_is_an_error(seeded, run):
    assert run("check", "--root", str(seeded))[0] == EXIT_ERROR


# ── why ───────────────────────────────────────────────────────────────────────

def test_why_shows_the_reversal_trail(seeded, run):
    parent = next(r for r in journal.load(seeded) if r["kind"] == "decision")
    run("note", "reversing that", "--root", str(seeded), "--supersedes", parent["id"])
    _, out = run("why", parent["id"], "--root", str(seeded))
    assert "SUPERSEDED" in out
    assert "reversed by" in out, "finding a dead rule without its killer is the bug"
    assert "reversing that" in out


def test_why_on_free_text_finds_the_decision(seeded, run):
    _, out = run("why", "derived status", "--root", str(seeded))
    assert "status is derived from evidence" in out


def test_why_on_nothing_known_says_so(seeded, run):
    _, out = run("why", "quantum tunnelling", "--root", str(seeded))
    assert "nothing recorded" in out


# ── sessions ──────────────────────────────────────────────────────────────────

def test_resume_recovers_an_unclosed_session(seeded, run):
    run("note", "wiring the parser", "--kind", "session_open", "--root", str(seeded))
    code, out = run("resume", "--root", str(seeded))
    assert code == EXIT_OK
    assert "UNCLOSED SESSION" in out and "wiring the parser" in out
    assert "resumed as" in out


def test_resume_lists_all_open_sessions_rather_than_guessing(seeded, run):
    """Two agents in two worktrees is the normal case; 'the open one' is not a
    thing."""
    run("note", "session a", "--kind", "session_open", "--root", str(seeded))
    run("note", "session b", "--kind", "session_open", "--root", str(seeded))
    _, out = run("resume", "--root", str(seeded))
    assert "2 unclosed sessions" in out
    assert "session a" in out and "session b" in out
    assert "roadmap resume <id>" in out


def test_resume_writes_nothing_when_sessions_are_disabled(seeded, run, monkeypatch):
    """`resume` writes on what reads like a read path — CI must be able to opt out."""
    run("note", "s", "--kind", "session_open", "--root", str(seeded))
    monkeypatch.setenv("ROADMAP_NO_SESSION", "1")
    before = len(journal.load(seeded))
    _, out = run("resume", "--root", str(seeded))
    assert len(journal.load(seeded)) == before
    assert "resumed as" not in out


def test_checkpoint_closes_every_open_session(seeded, run):
    run("note", "s", "--kind", "session_open", "--root", str(seeded))
    assert run("checkpoint", "done for now", "--root", str(seeded))[0] == EXIT_OK
    assert journal.open_sessions(root=seeded) == []


def test_checkpoint_auto_never_fails(tmp_path, run):
    """It runs from a git hook. A hook that can fail is a hook that breaks commits."""
    code, _ = run("checkpoint", "--auto", "--root", str(tmp_path / "does-not-exist"))
    assert code == EXIT_OK


# ── dispatch ──────────────────────────────────────────────────────────────────

def test_help_and_version(run):
    assert run()[0] == EXIT_OK
    assert "roadmap check" in run("--help")[1]
    assert run("--version")[0] == EXIT_OK


def test_unknown_command_suggests_a_near_miss(run, capsys):
    code, _ = run("chekc")
    assert code == EXIT_ERROR
    assert "did you mean `check`" in capsys.readouterr().err


def test_build_is_an_alias_for_brief(seeded, run):
    """The always-on block and the skill's fresh-clone path both name `build`;
    it must never dead-end."""
    code, out = run("build", "--root", str(seeded))
    assert code == EXIT_OK and Path(out.strip()).is_file()


def test_doctor_reports_a_dependency_cycle(seeded, run):
    text = plan_path(seeded).read_text()
    text = text.replace('id = "T-01"\nlabel = "Package layout and console entry point"',
                        'id = "T-01"\ndepends_on = ["T-03"]\n'
                        'label = "Package layout and console entry point"')
    plan_path(seeded).write_text(text)
    _, out = run("doctor", "--root", str(seeded))
    assert "dependency cycle" in out


def test_doctor_is_clean_on_a_healthy_project(seeded, run):
    _, out = run("doctor", "--root", str(seeded))
    assert "nothing to report" in out


# ── install ───────────────────────────────────────────────────────────────────

def test_install_writes_the_block_into_existing_agent_files(project, run):
    (project / "CLAUDE.md").write_text("## graphify\n\nSomeone else's block.\n")
    (project / "AGENTS.md").write_text("# House rules\n")
    code, out = run("install", "--root", str(project))
    assert code == EXIT_OK
    claude = (project / "CLAUDE.md").read_text()
    assert "roadmap check" in claude
    assert "Someone else's block." in claude, "another tool's block must survive"
    assert "roadmap check" in (project / "AGENTS.md").read_text()


def test_install_is_idempotent(project, run):
    (project / "CLAUDE.md").write_text("# x\n")
    run("install", "--root", str(project))
    first = (project / "CLAUDE.md").read_text()
    run("install", "--root", str(project))
    assert (project / "CLAUDE.md").read_text() == first


def test_install_creates_claude_md_when_no_agent_file_exists(project, run):
    """A project with neither file must not silently get no always-on layer —
    that is the one failure that makes the whole design inert."""
    run("install", "--root", str(project))
    assert (project / "CLAUDE.md").is_file()
    assert not (project / "AGENTS.md").exists()


def test_uninstall_removes_only_our_block(project, run):
    (project / "CLAUDE.md").write_text("## graphify\n\nkeep me\n")
    run("install", "--root", str(project))
    run("install", "--root", str(project), "--uninstall")
    text = (project / "CLAUDE.md").read_text()
    assert "keep me" in text and "roadmap check" not in text


# ── commands that have not shipped ────────────────────────────────────────────

def test_unbuilt_commands_answer_for_themselves(run):
    """The always-on block names the whole surface on purpose — rewriting an
    agent's instructions every phase is how they stop being trusted. So an
    unshipped command must not fall through to 'unknown command', which reads as
    a broken install and sends the agent back to guessing."""
    from roadmapify.cli import EXIT_NOT_YET, NOT_YET

    for name, (phase, _) in NOT_YET.items():
        code, out = run(name)
        assert code == EXIT_NOT_YET, name
        assert phase in out and "not built yet" in out, name
        assert "what works today" in out, f"{name} must say what to do instead"


def test_the_always_on_block_only_names_real_or_announced_commands(project, run):
    """A rule telling an agent to run a command that errors is worse than no rule."""
    import re

    from roadmapify.cli import COMMANDS
    from roadmapify.install import always_on_text

    named = set(re.findall(r"`roadmap ([a-z-]+)", always_on_text()))
    named |= set(re.findall(r"run `roadmap ([a-z-]+)", always_on_text()))
    assert named, "the block should name commands"
    missing = named - set(COMMANDS)
    assert not missing, f"the always-on block names commands that do not dispatch: {missing}"


def test_exit_codes_are_all_distinct():
    from roadmapify.cli import (EXIT_CONSTRAINT, EXIT_ERROR, EXIT_NOT_YET, EXIT_OK,
                                EXIT_REJECTED)
    codes = [EXIT_OK, EXIT_ERROR, EXIT_NOT_YET, EXIT_REJECTED, EXIT_CONSTRAINT]
    assert len(set(codes)) == len(codes)


# ── the command surface is described in four places ───────────────────────────

def _readme() -> str:
    return (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def test_every_shipped_command_is_documented_in_help_and_the_readme():
    """The surface is written out by hand in HELP, in the README table and in
    the not-yet footer. `export` had already gone missing from the footer and
    `checkpoint` from the README — four copies of one list drift, and the list
    is what an agent reads to find out what it can run."""
    shipped = [n for n in cli.COMMANDS if n not in cli.NOT_YET]
    readme = _readme()
    for name in shipped:
        assert f"roadmap {name}" in cli.HELP, f"{name} is not in `roadmap --help`"
        assert f"roadmap {name}" in readme, f"{name} is not in README.md"


def test_the_readme_documents_no_command_that_does_not_exist():
    """A table row for a verb that does not dispatch is worse than an omission:
    it sends someone to a command that answers 'unknown command'."""
    named = {n for n in re.findall(r"`roadmap ([a-z][a-z-]*)", _readme())
             if not n.startswith("-")}
    unknown = sorted(n for n in named if n not in cli.COMMANDS)
    assert not unknown, f"README.md documents {unknown}, which do not dispatch"


def test_the_unbuilt_footer_names_the_commands_that_work(seeded, run):
    """It is the text an agent sees at the exact moment it reached for something
    that does not exist yet, so it is the one list that must never be stale."""
    code, out = run("verify", "--root", str(seeded))
    assert code == EXIT_NOT_YET
    for name in ("init", "note", "check", "export"):
        assert name in out
    for name in cli.NOT_YET:
        assert f"· {name} ·" not in out, f"{name} has not shipped but is listed as working"


# ── next / tree / expand ──────────────────────────────────────────────────────

def test_next_names_the_active_phase_its_gate_the_ready_tasks_and_the_critical_path(
        seeded, run):
    """The NOT_YET table promised these four things by name before the command
    existed, and that text had already shipped to users."""
    code, out = run("next", "--root", str(seeded))
    assert code == EXIT_OK
    assert "P-1" in out
    assert "ships" in out and "gate" in out
    assert "ready now" in out
    assert "critical path" in out


def test_next_says_when_no_evidence_has_been_recorded(seeded, run):
    """With an empty evidence log every task reads unstarted. A screen that does
    not say so looks like a project that has achieved nothing."""
    code, out = run("next", "--root", str(seeded))
    assert code == EXIT_OK
    assert "no evidence recorded" in out


def test_next_writes_nothing_at_all(seeded, run):
    """`next` runs from hooks and CI. A read command that writes is one that can
    lose data under contention."""
    before = {f.name: f.read_bytes() for f in seeded.rglob("*") if f.is_file()}
    run("next", "--root", str(seeded))
    after = {f.name: f.read_bytes() for f in seeded.rglob("*") if f.is_file()}
    assert before == after


def test_tree_works_on_a_fresh_init_with_no_journal_and_no_git(project, run):
    """P-2's declared demo, and the hardest starting tier: no memory, no repo."""
    assert not (project / ".git").exists()
    code, _ = run("init", "a thing that syncs notes", "--root", str(project))
    assert code == EXIT_OK
    code, out = run("tree", "--root", str(project))
    assert code == EXIT_OK
    assert "P-1" in out


def test_tree_marks_provisional_tasks_in_plain_text_not_only_colour(seeded, run):
    """Dimming is an ANSI escape. Piped to a file, read by an agent, or under
    NO_COLOR it is invisible — so "provisional tasks dimmed" has to survive as
    text or the promise is empty."""
    code, out = run("tree", "--root", str(seeded))
    assert code == EXIT_OK
    assert "prov" in out
    assert "provisional" in out


def test_tree_lists_every_task_exactly_once(seeded, run):
    from roadmapify.plan import load_plan
    plan = load_plan(seeded)
    _, out = run("tree", "--root", str(seeded))
    for t in plan.tasks:
        assert out.count(f"{t.id}  ") == 1, t.id


def test_tree_hide_provisional_shows_only_the_load_bearing_set(seeded, run):
    from roadmapify.plan import load_plan
    plan = load_plan(seeded)
    _, out = run("tree", "--hide-provisional", "--root", str(seeded))
    for t in plan.tasks:
        if t.provisional:
            assert f"{t.id}  " not in out


def test_expand_promotes_a_provisional_phase_and_all_of_its_tasks(seeded, run):
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    code, out = run("expand", target, "--root", str(seeded))
    assert code == EXIT_OK
    after = load_plan(seeded)
    assert after.phase(target).provisional is False
    assert not any(t.provisional for t in after.tasks_of(target))


def test_expand_never_writes_a_status_field(seeded, run):
    """The central invariant of the whole tool. `expand` is the first command
    that writes the plan, so it is the first that could break it."""
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    run("expand", target, "--root", str(seeded))
    assert not re.search(r"^\s*status\s*=", plan_path(seeded).read_text(), re.M)


def test_expand_does_not_add_remove_or_reorder_phases(seeded, run):
    from roadmapify.plan import load_plan
    before = [(p.id, p.order) for p in load_plan(seeded).ordered_phases()]
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    run("expand", target, "--root", str(seeded))
    assert [(p.id, p.order) for p in load_plan(seeded).ordered_phases()] == before


def test_expand_changes_only_the_provisional_lines(seeded, run):
    from roadmapify.plan import load_plan
    from collections import Counter
    before = Counter(plan_path(seeded).read_text().splitlines())
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    n_promoted = 1 + len(load_plan(seeded).tasks_of(target))
    run("expand", target, "--root", str(seeded))
    after = Counter(plan_path(seeded).read_text().splitlines())

    delta = before - after
    assert delta == Counter({"provisional = true": n_promoted}), delta
    assert not (after - before), "expand must not add a line"


def test_expand_refuses_to_rewrite_a_file_it_cannot_reproduce(seeded, run, capsys):
    """roadmap.toml is the one tracked source file this tool mutates, and its own
    header tells you to hand-edit it. Silently eating a reviewer's comment is
    data loss with no undo outside git."""
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    original = plan_path(seeded).read_text()
    plan_path(seeded).write_text("# NOTE: hand written\n" + original, encoding="utf-8")

    code, _ = run("expand", target, "--root", str(seeded))

    assert code == EXIT_ERROR
    assert "# NOTE: hand written" in plan_path(seeded).read_text(), "nothing was written"
    assert "refusing to rewrite" in capsys.readouterr().err


def test_expand_force_writes_anyway(seeded, run):
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    plan_path(seeded).write_text("# NOTE: hand written\n" + plan_path(seeded).read_text(),
                                 encoding="utf-8")
    code, _ = run("expand", target, "--force", "--root", str(seeded))
    assert code == EXIT_OK
    assert load_plan(seeded).phase(target).provisional is False


def test_expand_dry_run_prints_the_change_and_writes_nothing(seeded, run):
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    before = plan_path(seeded).read_bytes()
    code, out = run("expand", target, "--dry-run", "--root", str(seeded))
    assert code == EXIT_OK
    assert "nothing was written" in out
    assert plan_path(seeded).read_bytes() == before


def test_expand_is_idempotent(seeded, run):
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    run("expand", target, "--root", str(seeded))
    once = plan_path(seeded).read_bytes()
    code, _ = run("expand", target, "--root", str(seeded))
    assert code == EXIT_OK
    assert plan_path(seeded).read_bytes() == once


def test_expand_says_it_only_cleared_a_flag(seeded, run):
    """Offline expand cannot invent task detail. A screen implying otherwise
    sells a capability that does not ship until P-9."""
    from roadmapify.plan import load_plan
    target = next(p.id for p in load_plan(seeded).ordered_phases() if p.provisional)
    _, out = run("expand", target, "--root", str(seeded))
    assert "only clears the provisional flag" in out
    assert "no status was written" in out


def test_expand_llm_is_announced_as_a_later_phase_rather_than_ignored(seeded, run):
    """P-9's `ships` names `roadmap expand P-3 --llm` literally, so the flag has
    to answer for itself — and the text is what distinguishes it from argparse's
    own exit 2 on a bad flag."""
    code, out = run("expand", "P-3", "--llm", "--root", str(seeded))
    assert code == EXIT_NOT_YET
    assert "P-9" in out


def test_expand_without_a_phase_is_an_error(seeded, run, capsys):
    assert run("expand", "--root", str(seeded))[0] == EXIT_ERROR


def test_expand_on_an_unknown_phase_is_an_error(seeded, run, capsys):
    assert run("expand", "P-99", "--root", str(seeded))[0] == EXIT_ERROR


def test_a_dependency_cycle_fails_next_naming_every_hop(seeded, run, capsys):
    """P-2's exit criterion, verbatim, reached through the command people run."""
    text = plan_path(seeded).read_text()
    marker = '[[task]]\nid = "T-01"'
    assert marker in text
    text = text.replace(marker, '[[task]]\ndepends_on = ["T-02"]\nid = "T-01"', 1)
    plan_path(seeded).write_text(text, encoding="utf-8")

    code, _ = run("next", "--root", str(seeded))
    err = capsys.readouterr().err
    assert code == EXIT_ERROR
    assert "cycle" in err


def test_no_shipped_command_is_also_announced_as_unbuilt():
    """The stub table used to be spread LAST into COMMANDS, so implementing a
    command without deleting its NOT_YET entry silently reinstalled the stub —
    with exit 2 as the only symptom."""
    for name in cli.NOT_YET:
        assert cli.COMMANDS[name].__qualname__.startswith("cmd_not_yet"), name
