"""Verify answers claim-versus-fact.

Its verdicts are present, missing, unverifiable and unresolvable, and collapsing
any of them into another is the lie P-5 exists to stop. `missing` is an
accusation; `unverifiable` means no method exists and none ever will;
`unresolvable` means the plan has a bug with a line number.

FIXTURE HAZARD, confirmed by reading conftest and not inferred: the `run`
fixture calls monkeypatch.undo() in its finally block, and pytest's
function-scoped `monkeypatch` is ONE instance shared with the autouse
`_sandbox_home` and `_no_color` fixtures. So after a single run() call
ROADMAP_AUTHOR is gone, Path.home is the developer's real home and NO_COLOR is
unset. Seed evidence with append_jsonl BEFORE calling run(), and never assert on
colour afterwards.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from roadmapify import render, status, verify
from roadmapify.cli import EXIT_CONTRADICTED, EXIT_ERROR, EXIT_OK
from roadmapify.paths import EVIDENCE_FILENAME, append_jsonl, out_path
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec, load_plan, plan_path

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _plan(tasks, phases=None):
    phases = phases or (PhaseSpec(id="P-1", label="one", order=1, ships="it ships"),)
    return Plan(schema=1, goal=Goal(label="g", why="w", archetype="cli"),
                phases=tuple(phases), tasks=tuple(tasks))


def _ev(task, kind, **kw):
    """ts is always in the past — status drops future records as clock skew."""
    return {"id": f"E-{task}-{kind}", "task": task, "kind": kind,
            "ts": "2026-01-01T00:00:00Z", **kw}


def _report(root, tasks, evidence=(), **kw):
    plan = _plan(tasks)
    snap = status.snapshot(plan, list(evidence), now=NOW)
    return plan, verify.verify(plan, snap, root, **kw)


# ── resolution ────────────────────────────────────────────────────────────────

def test_a_bare_path_deliverable_is_resolved_under_the_root_it_was_handed(project):
    """If verify anchored against the cwd instead of the root it was given, every
    answer would depend on which directory you were standing in — and the whole
    suite runs with --root because nothing here ever chdirs."""
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert verify.resolve_spec("a.py", project).verdict == verify.PRESENT
    assert verify.resolve_spec("a.py", project / "nope").verdict == verify.MISSING


@pytest.mark.parametrize("spec", [
    "file:a.py", "test:tests/t.py", "glob:*.py", "symbol:mod.fn", "cmd:make build",
])
def test_every_verdict_carries_a_method_and_a_non_empty_detail(project, spec):
    """The design's central invariant. A verdict with no method is a claim with
    no provenance, and provenance is the only thing stopping `present` from
    being read as `verified`."""
    r = verify.resolve_spec(spec, project)
    assert r.verdict in verify.VERDICTS
    assert r.method in verify.METHODS
    assert r.detail, "a verdict with no detail is a claim with no provenance"


def test_a_file_that_is_absent_is_missing_and_one_that_is_there_is_present(project):
    """Both directions in one test, so a resolver returning a constant fails."""
    (project / "here.py").write_text("x\n", encoding="utf-8")
    assert verify.resolve_spec("file:here.py", project).verdict == verify.PRESENT
    assert verify.resolve_spec("file:gone.py", project).verdict == verify.MISSING


def test_an_empty_file_is_present_but_the_row_says_EMPTY(project):
    """A 0-byte file satisfies exists(). A row that hides that is exactly how
    `touch roadmapify/verify.py` becomes a way to finish work."""
    (project / "empty.py").write_text("", encoding="utf-8")
    r = verify.resolve_spec("file:empty.py", project)
    assert r.verdict == verify.PRESENT and r.weak
    assert "EMPTY" in r.detail


def test_a_directory_is_present_but_weak_and_the_row_says_directory(project):
    """A directory deliverable is legitimate, but a directory is not the same
    evidence as a file."""
    (project / "pkg").mkdir()
    r = verify.resolve_spec("file:pkg", project)
    assert r.verdict == verify.PRESENT and r.weak
    assert "directory" in r.detail


def test_a_test_deliverable_never_reports_passing_and_always_says_not_run(project):
    """Executing repo code is a recorded constraint, so "passes" is permanently
    unavailable. This is the most likely place an implementation breaks project
    law while looking like an improvement."""
    (project / "t.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    r = verify.resolve_spec("test:t.py", project)
    assert r.verdict == verify.PRESENT
    assert r.detail.startswith("not run")
    assert "pass" not in r.detail.replace("not run", "")


def test_a_test_file_with_no_test_functions_is_present_but_weak(project):
    """The ast tier's whole job: separating a real test file from an empty
    placeholder WITHOUT ever changing the verdict."""
    (project / "t.py").write_text("x = 1\n", encoding="utf-8")
    r = verify.resolve_spec("test:t.py", project)
    assert r.verdict == verify.PRESENT and r.weak
    assert "NO TESTS" in r.detail


def test_a_test_file_that_is_not_python_degrades_to_a_shorter_answer(project):
    """A SyntaxError must cost the test count, never the verdict — otherwise the
    tool looks capable on its own repo and inert on the projects it serves."""
    (project / "t.py").write_text("this is (not python\n", encoding="utf-8")
    r = verify.resolve_spec("test:t.py", project)
    assert r.verdict == verify.PRESENT
    assert "not Python" in r.detail


def test_a_pytest_node_id_stats_the_file_half_and_says_the_node_was_not_checked(project):
    """`test:tests/t.py::test_foo` is an ordinary spec. Stat-ing the whole string
    emits a false `missing` — an accusation about a valid deliverable."""
    (project / "t.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    r = verify.resolve_spec("test:t.py::test_a", project)
    assert r.verdict == verify.PRESENT
    assert "node not checked" in r.detail and r.weak


def test_a_cmd_deliverable_is_unverifiable_rather_than_missing(project):
    """Reporting a cmd missing accuses the author of not doing work they may
    well have done. The text is printed so a human can run it themselves."""
    r = verify.resolve_spec("cmd:make build", project)
    assert r.verdict == verify.UNVERIFIABLE
    assert r.method == "none"
    assert "make build" in r.shown


def test_a_symbol_deliverable_is_unverifiable_and_names_the_task_that_would_help(project):
    """An `unverifiable` with no reason teaches the reader that verify is noise."""
    r = verify.resolve_spec("symbol:mod.fn", project)
    assert r.verdict == verify.UNVERIFIABLE
    assert "T-17" in r.detail


@pytest.mark.parametrize("spec,kind", [
    ("FILE:a.py", "file"), ("tests:a.py", "test"), ("glo:*.py", "glob"),
])
def test_an_unknown_kind_prefix_that_is_a_near_miss_is_unresolvable(project, spec, kind):
    """split_deliverable turns these into a file literally named that, and
    validate_plan rejects only an empty value. `missing` would send someone
    hunting for a file that never existed."""
    r = verify.resolve_spec(spec, project, line=42)
    assert r.verdict == verify.UNRESOLVABLE
    assert kind in r.detail and "42" in r.detail


def test_a_legal_filename_containing_a_colon_is_resolved_as_a_path(project):
    """`notes:2026.md` is a legal filename. This is the test that stops the typo
    rule from becoming its own false accusation."""
    r = verify.resolve_spec("notes:2026.md", project)
    assert r.verdict == verify.MISSING, "a path, not a typo"
    assert "not a deliverable kind" in r.detail


@pytest.mark.parametrize("spec", [
    "file:/etc/passwd", "file:../../etc/passwd", "file:~/x", "glob:/**",
])
def test_a_deliverable_that_escapes_the_root_is_refused_before_anything_is_stat_ed(
        project, spec, monkeypatch):
    """Two properties in one test: verify never stats outside the root, and it
    never accuses a path it deliberately refused to look at."""
    seen = []
    real = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: (seen.append(self), real(self))[1])

    r = verify.resolve_spec(spec, project, line=7)

    assert r.verdict == verify.UNRESOLVABLE
    assert "escapes the project root" in r.detail
    for path in seen:
        assert str(project) in str(path), f"stat-ed outside the root: {path}"


def test_a_glob_reports_its_match_count_rather_than_a_bare_present(project):
    """"2 matches" is a strictly weaker claim than "the file is there"."""
    (project / "a.py").write_text("x\n", encoding="utf-8")
    (project / "b.py").write_text("x\n", encoding="utf-8")
    r = verify.resolve_spec("glob:*.py", project)
    assert r.verdict == verify.PRESENT and r.weak
    assert r.matches == 2 and "2 matches" in r.detail


def test_a_glob_does_not_match_inside_a_generated_directory(project):
    """Measured on the real repo: `**/plan.py` matches both roadmapify/plan.py
    and build/lib/roadmapify/plan.py, and build/ is gitignored. Without pruning,
    a deleted source file reads `present` — the worst verdict this can emit."""
    (project / "build" / "lib").mkdir(parents=True)
    (project / "build" / "lib" / "x.py").write_text("x\n", encoding="utf-8")
    assert verify.resolve_spec("glob:**/x.py", project).verdict == verify.MISSING


def test_a_glob_that_deliberately_names_a_pruned_directory_still_matches_there(project):
    """`glob:build/*.whl` is a legitimate deliverable. A prune list that eats it
    trades a false positive for a false negative, and a wrongly-contradicted
    task is what teaches agents to delete `produces` lines."""
    (project / "build").mkdir()
    (project / "build" / "pkg.whl").write_text("x\n", encoding="utf-8")
    assert verify.resolve_spec("glob:build/*.whl", project).verdict == verify.PRESENT


def test_a_glob_that_would_match_the_whole_tree_is_unresolvable_not_missing(project):
    """`glob:**` is a plan bug, not an absent file."""
    r = verify.resolve_spec("glob:**", project)
    assert r.verdict == verify.UNRESOLVABLE
    assert "narrow it" in r.detail


def test_a_glob_that_exhausts_the_candidate_cap_is_unverifiable_not_missing(
        project, monkeypatch):
    """An unfinished search has not earned an accusation. Also the test that
    proves verify cannot hang."""
    monkeypatch.setattr(verify, "GLOB_CANDIDATE_CAP", 1)
    for i in range(5):
        (project / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    (project / "sub").mkdir()
    (project / "sub" / "deep.txt").write_text("x\n", encoding="utf-8")
    r = verify.resolve_spec("glob:**/*.nomatch", project)
    assert r.verdict == verify.UNVERIFIABLE
    assert "gave up" in r.detail


# ── the join, and the exit code ───────────────────────────────────────────────

def test_a_task_claiming_done_whose_deliverable_is_absent_is_contradicted(project):
    """P-5's exit criterion, verbatim, at unit level."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:gone.py",))]
    _, report = _report(project, tasks, [_ev("T-01", "claim")])
    t = report.task("T-01")
    assert t.finding == verify.CONTRADICTED
    assert t.contradicted_by == ("file:gone.py",)
    assert report.contradicted


def test_a_merge_backed_done_is_contradicted_just_as_loudly_as_a_self_report(project):
    """Stronger evidence must not buy immunity: a merge proves a branch landed,
    not that a file exists. One condition, one exit code."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:gone.py",))]
    _, report = _report(project, tasks, [_ev("T-01", "merge", ref="abc")])
    assert report.task("T-01").status == status.DONE
    assert report.task("T-01").finding == verify.CONTRADICTED


def test_a_claim_with_every_deliverable_present_is_corroborated_never_verified(project):
    """Asserts the word. "Verified" on a stat would be a more convincing lie than
    the stored status field this project exists to avoid, because it would carry
    the tool's authority."""
    (project / "a.py").write_text("x\n", encoding="utf-8")
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:a.py",))]
    _, report = _report(project, tasks, [_ev("T-01", "claim")])
    assert report.task("T-01").finding == verify.CORROBORATED
    assert report.verdict == verify.CORROBORATED
    assert "verified" not in " ".join(verify.FINDINGS)


def test_a_missing_deliverable_nobody_claims_is_reported_but_never_contradicts(project):
    """Absence is the normal state of unstarted work. The negative control
    without which the exit-criterion test passes on a verify that contradicts
    everything."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:gone.py",))]
    _, report = _report(project, tasks)
    assert report.resolution("file:gone.py").verdict == verify.MISSING
    assert not report.contradicted
    assert report.verdict == verify.NOTHING_CLAIMED


@pytest.mark.parametrize("spec", ["cmd:make", "symbol:m.f", "FILE:a.py"])
def test_an_uncheckable_deliverable_never_contradicts_a_claim(project, spec):
    """"We could not check it" must never render as "you lied". This is the test
    that fails if anyone later wires a --strict into the exit code."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=(spec,))]
    _, report = _report(project, tasks, [_ev("T-01", "claim")])
    assert not report.contradicted


def test_a_claim_on_a_task_with_nothing_checkable_is_reported_as_a_hole(project):
    """Such a task can be neither corroborated nor contradicted; staying silent
    would let verify imply it covered that task. It is the cheapest route around
    this gate."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("cmd:make",))]
    _, report = _report(project, tasks, [_ev("T-01", "claim")])
    assert report.task("T-01").finding == verify.UNCHECKABLE


def test_a_task_whose_deliverables_are_present_but_unclaimed_is_unclaimed_work(project):
    """The only finding with signal on the real repo today. The phrasing must
    state the fact without implying the work is done."""
    (project / "a.py").write_text("x\n", encoding="utf-8")
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:a.py",))]
    _, report = _report(project, tasks)
    assert report.task("T-01").finding == verify.UNCLAIMED_WORK


def test_a_task_that_declares_nothing_contributes_zero_to_every_count(project):
    """The highest-value test here. Most load-bearing tasks on a fresh init
    declare nothing. If "nothing to check" rolled up as `present`, verify would
    report a clean board for a plan it never checked — the confident lie the
    whole project exists to prevent."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1")]
    _, report = _report(project, tasks)
    c = report.counts()
    assert report.task("T-01").undeclared is True
    assert c["undeclared"] == 1
    assert c["present"] == c["missing"] == c["deliverables"] == 0
    assert report.task("T-01").finding == ""


def test_the_same_deliverable_declared_twice_is_resolved_once_and_counted_once(
        project, monkeypatch):
    """Per-task resolution would double-count one missing file and the header
    would stop adding up, which is how a report contradicts itself."""
    calls = []
    real = verify.resolve_spec
    monkeypatch.setattr(verify, "resolve_spec",
                        lambda s, r, **kw: (calls.append(s), real(s, r, **kw))[1])
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:a.py",)),
             TaskSpec(id="T-02", label="b", phase="P-1", produces=("file:a.py",))]
    _, report = _report(project, tasks)
    assert calls.count("file:a.py") == 1
    assert len(report.resolutions) == 1
    assert report.declarations == 2
    assert report.resolution("file:a.py").declared_by == ("T-01", "T-02")


# ── scope ─────────────────────────────────────────────────────────────────────

def test_provisional_tasks_are_in_no_denominator_and_the_count_is_reported(project):
    """Pins a promise printed to users since P-1 and written as law twice in
    source, with no test behind it. An invisible exclusion is indistinguishable
    from a bug."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:a.py",)),
             TaskSpec(id="T-02", label="b", phase="P-1", provisional=True,
                      produces=("file:b.py",))]
    _, report = _report(project, tasks)
    assert report.task("T-02") is None
    assert report.skipped_provisional == 1
    assert report.counts()["tasks"] == 1


def test_a_provisional_task_claiming_done_with_an_absent_file_is_still_contradicted(
        project):
    """The plan being behind the work must not be swallowed. Reading "excluded
    from verify" as "skip entirely" would hide the loudest contradiction there
    is."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", provisional=True,
                      produces=("file:gone.py",))]
    _, report = _report(project, tasks, [_ev("T-01", "claim")])
    assert report.task("T-01").finding == verify.CONTRADICTED
    assert report.task("T-01").counted is False, "never in a denominator"


def test_naming_a_provisional_task_explicitly_verifies_it_rather_than_skipping(project):
    """`roadmap verify T-16` returning an empty report would read as a broken
    install. Asking about a task by name must answer."""
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", provisional=True,
                      produces=("file:gone.py",))]
    _, report = _report(project, tasks, task="T-01")
    assert report.task("T-01") is not None


# ── purity, safety and the write ban ──────────────────────────────────────────

def test_verify_writes_nothing_at_all(seeded, run):
    """The hazard is one keystroke away: four shipped cmd_ bodies call
    refresh_brief. This is also what stops verify's findings ever being appended
    to evidence.jsonl — the back door a filesystem-shaped stored status would
    arrive through."""
    before = {p: p.read_bytes() for p in seeded.rglob("*") if p.is_file()}
    run("verify", "--root", str(seeded))
    after = {p: p.read_bytes() for p in seeded.rglob("*") if p.is_file()}
    assert before == after


def test_the_package_contains_no_process_spawning_primitive():
    """The cheapest possible enforcement of the whole no-execution policy, and
    the same trick P-6's T-19 will use for mutating git verbs. It lives here so
    it fails in the review that would introduce the first one."""
    import ast as _ast

    BANNED_MODULES = {"subprocess", "commands", "pty"}
    # `compile` is deliberately absent: re.compile is everywhere and harmless,
    # and the dangerous builtin is only reachable through eval/exec, which are
    # banned. A guard with a false positive gets deleted, not fixed.
    BANNED_CALLS = {"system", "popen", "spawn", "spawnv", "execv", "execve",
                    "import_module", "eval", "exec"}
    pkg = Path(verify.__file__).parent
    for mod in sorted(pkg.glob("*.py")):
        tree = _ast.parse(mod.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in BANNED_MODULES, \
                        f"{mod.name} imports {alias.name}"
            elif isinstance(node, _ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in BANNED_MODULES, \
                    f"{mod.name} imports from {node.module}"
            elif isinstance(node, _ast.Call):
                fn = node.func
                name = (fn.attr if isinstance(fn, _ast.Attribute)
                        else fn.id if isinstance(fn, _ast.Name) else "")
                assert name not in BANNED_CALLS, f"{mod.name} calls {name}()"


def test_nothing_in_verify_reads_the_clock():
    """verify.py is the first module with filesystem I/O, so it is the most
    likely place a hidden clock read lands."""
    src = Path(verify.__file__).read_text(encoding="utf-8")
    assert "datetime.now" not in src and "time.time" not in src


def test_the_report_is_identical_for_identical_inputs(project):
    """Verify is the first stage whose input is the working tree, so
    byte-stability has to be asserted at the Report level."""
    (project / "a.py").write_text("x\n", encoding="utf-8")
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:a.py",))]
    _, first = _report(project, tasks)
    _, second = _report(project, tasks)
    assert first == second


def test_a_deliverable_spec_cannot_smuggle_control_characters_into_the_screen(project):
    """roadmap.toml is an unsanitised, PR-editable input, and `cmd:` text is the
    most attacker-shaped string in it. verify is the command that echoes it."""
    hostile = "cmd:clear\x1b[2J\x1b[H rm -rf /"
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=(hostile,))]
    plan, report = _report(project, tasks)
    screen = render.verify_screen(plan, report, now=NOW)
    assert "\x1b" not in screen
    body = render.verify_json(report, now=NOW)
    assert "\x1b" not in body, "raw ESC must never reach the JSON either"


def test_every_verdict_survives_being_piped_as_plain_text(project):
    """_NO_COLOR is true for every pipe, every agent and every test; a dimmed
    marker is invisible to the primary caller."""
    (project / "a.py").write_text("x\n", encoding="utf-8")
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1",
                      produces=("file:a.py", "file:gone.py", "cmd:make"))]
    plan, report = _report(project, tasks)
    screen = render.verify_screen(plan, report, now=NOW)
    for word in (verify.PRESENT, verify.MISSING, verify.UNVERIFIABLE):
        assert word in screen


# ── end to end, through cli.dispatch ──────────────────────────────────────────

def test_the_demo_a_claim_on_a_file_that_does_not_exist_is_contradicted(seeded, run):
    """P-5's demo end to end. The claim record is hand-written because
    `roadmap done` is still a P-3 stub — without that, exit 5 is unreachable."""
    tid = load_plan(seeded).tasks[0].id
    append_jsonl(out_path(EVIDENCE_FILENAME, root=seeded),
                 {"id": "E-1", "task": tid, "kind": "claim",
                  "ts": "2026-01-05T00:00:00Z"})

    code, out = run("verify", "--root", str(seeded))

    assert code == EXIT_CONTRADICTED
    assert "CONTRADICTED" in out and tid in out


def test_verify_exits_zero_when_nothing_claims_done(seeded, run):
    """A full board of unclaimed work is not a failure."""
    code, out = run("verify", "--root", str(seeded))
    assert code == EXIT_OK


def test_verify_says_so_when_no_evidence_has_been_recorded(seeded, run):
    """With an empty log nothing is claimed, so "0 contradictions" alone would be
    a clean bill of health for a check that could not have run."""
    code, out = run("verify", "--root", str(seeded))
    assert "no evidence recorded" in out
    assert "roadmap sync" in out


def test_verify_never_names_roadmap_done_anywhere_in_its_output(seeded, run):
    """Every other screen teaches the next verb. This one must not: the next verb
    after "all deliverables present" is the one that launders a stat into a
    claim."""
    _, out = run("verify", "--root", str(seeded))
    assert "roadmap done" not in out


def test_verify_without_a_roadmap_toml_exits_one_not_five(project, run, capsys):
    """"Verify could not run" and "the plan is lying" must never be one code."""
    code, _ = run("verify", "--root", str(project))
    assert code == EXIT_ERROR
    assert "roadmap init" in capsys.readouterr().err


def test_a_dependency_cycle_fails_verify_naming_every_hop(seeded, run, capsys):
    """A plan with a cycle has no derived status, so it has no claim to
    contradict — verify inherits P-2's exit criterion."""
    text = plan_path(seeded).read_text()
    marker = '[[task]]\nid = "T-01"'
    assert marker in text
    plan_path(seeded).write_text(
        text.replace(marker, '[[task]]\ndepends_on = ["T-02"]\nid = "T-01"', 1),
        encoding="utf-8")

    code, _ = run("verify", "--root", str(seeded))

    assert code == EXIT_ERROR
    assert "cycle" in capsys.readouterr().err


def test_an_unknown_task_or_phase_argument_is_an_error(seeded, run, capsys):
    """A typo'd scope that silently checks everything is worse than an error."""
    assert run("verify", "T-99", "--root", str(seeded))[0] == EXIT_ERROR
    assert run("verify", "--phase", "P-99", "--root", str(seeded))[0] == EXIT_ERROR


def test_passing_a_phase_id_as_the_positional_suggests_the_phase_flag(seeded, run,
                                                                     capsys):
    """`roadmap verify P-1` is the obvious mistake, and "no task 'P-1'" is a dead
    end when the right command is one flag away."""
    code, _ = run("verify", "P-1", "--root", str(seeded))
    assert code == EXIT_ERROR
    assert "--phase P-1" in capsys.readouterr().err


def test_the_json_report_is_byte_identical_for_identical_inputs(seeded, run):
    """The machine surface is the product for this command."""
    _, first = run("verify", "--json", "--root", str(seeded))
    _, second = run("verify", "--json", "--root", str(seeded))
    assert first == second


def test_the_json_verdict_distinguishes_corroborated_from_nothing_claimed(seeded, run):
    """Exit 0 alone cannot say whether anything was actually checked."""
    import json
    _, out = run("verify", "--json", "--root", str(seeded))
    assert json.loads(out)["verdict"] == verify.NOTHING_CLAIMED


def test_every_json_test_deliverable_carries_passes_unverifiable(project, run):
    """A note string can be dropped by a consumer; a field cannot."""
    (project / "t.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    tasks = [TaskSpec(id="T-01", label="a", phase="P-1", produces=("test:t.py",))]
    _, report = _report(project, tasks)
    import json
    obj = json.loads(render.verify_json(report, now=NOW))
    assert obj["deliverables"][0]["passes"] == "unverifiable"


def test_json_and_the_screen_are_never_printed_together(seeded, run):
    """--json must be parseable."""
    _, out = run("verify", "--json", "--root", str(seeded))
    assert out.lstrip().startswith("{")
    assert "present means the named path exists" not in out
