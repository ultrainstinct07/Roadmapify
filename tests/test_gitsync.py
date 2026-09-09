"""Reading git without ever writing it.

The guard below is the mechanical half of two recorded constraints: only one
module may spawn a process, it may spawn only the literal binary `git`, only
with a read-only subcommand, and every element of every argv must be a string
literal written in the source. That last rule is what eliminates argument
injection by construction — `git log --format=%H "--output=/tmp/PWNED"` returns
0 and writes the file, and a pull request can create a branch named `-dashlead`.

It replaces the package-wide no-subprocess scan that shipped with verify, which
was correct for P-5 and would have forbidden P-3 outright.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from roadmapify import gitsync
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
PKG = Path(gitsync.__file__).parent
GIT_MODULE = "gitsync.py"

MUTATING = frozenset({
    "branch", "checkout", "switch", "rebase", "merge", "push", "pull", "fetch",
    "reset", "commit", "add", "rm", "mv", "tag", "clean", "stash", "apply", "am",
    "cherry-pick", "revert", "gc", "prune", "update-ref", "init", "clone",
    "worktree", "notes", "config", "filter-branch", "replace", "submodule",
    "remote", "restore", "sparse-checkout", "update-index", "write-tree",
    "commit-tree", "hash-object", "reflog", "repack", "bundle", "daemon",
    "fsck", "send-email", "format-patch", "request-pull"})

BANNED_MODULES = {"subprocess", "commands", "pty", "multiprocessing"}


def _plan(tasks, phases=None):
    phases = phases or (PhaseSpec(id="P-1", label="one", order=1, ships="s"),)
    return Plan(schema=1, goal=Goal(label="g", why="w", archetype="cli"),
                phases=tuple(phases), tasks=tuple(tasks))


def _commit(sha, *, parents=(), ts="2026-01-01T00:00:00+00:00",
            email="dev@example.com", trailers=(), message="a commit"):
    return gitsync.Commit(sha=sha, parents=tuple(parents), ts=ts, author="Dev",
                          email=email, trailers=tuple(trailers), message=message)


# ── the guard ─────────────────────────────────────────────────────────────────

def test_only_the_designated_module_may_spawn_a_process():
    """One spawner, named in one place. This fails in the review that adds a
    second one, which is the only moment it is cheap to stop."""
    for mod in sorted(PKG.rglob("*.py")):
        if mod.name == GIT_MODULE:
            continue
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in BANNED_MODULES, \
                        f"{mod.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in BANNED_MODULES, \
                    f"{mod.name} imports from {node.module}"


def test_no_module_calls_an_execution_primitive():
    """`compile` is deliberately absent — re.compile is everywhere and harmless,
    and the dangerous builtin is reachable only through eval/exec."""
    banned = {"system", "popen", "spawn", "spawnv", "execv", "execve",
              "import_module", "eval", "exec"}
    for mod in sorted(PKG.rglob("*.py")):
        tree = ast.parse(mod.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (fn.attr if isinstance(fn, ast.Attribute)
                        else fn.id if isinstance(fn, ast.Name) else "")
                assert name not in banned, f"{mod.name} calls {name}()"


def _git_argvs() -> "list[list[str]]":
    """Every list literal in gitsync.py whose first element is "git"."""
    tree = ast.parse((PKG / GIT_MODULE).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List) or not node.elts:
            continue
        first = node.elts[0]
        if isinstance(first, ast.Constant) and first.value == "git":
            assert all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                       for e in node.elts), \
                "every element of a git argv must be a string literal"
            found.append([e.value for e in node.elts])
    return found


def test_every_git_argv_is_built_entirely_from_string_literals():
    """No argv is ever built from a plan file, a branch name or any other input.
    That eliminates argument injection rather than mitigating it."""
    argvs = _git_argvs()
    assert len(argvs) >= 6, f"expected the documented invocations, found {len(argvs)}"


def test_every_git_subcommand_is_read_only_and_allowlisted():
    """The mechanical half of "roadmapify never runs a git verb that writes"."""
    for argv in _git_argvs():
        rest = [a for a in argv[1:] if not a.startswith("-")]
        # skip the values of the fixed -c block
        sub = next((a for a in rest if "=" not in a), "")
        assert sub in gitsync.GIT_READ_SUBCOMMANDS, f"{sub!r} is not allowlisted"
        assert sub not in MUTATING, f"{sub!r} writes"


def test_no_git_invocation_uses_a_shell_or_a_patch():
    src = (PKG / GIT_MODULE).read_text(encoding="utf-8")
    assert "shell=True" not in src
    for argv in _git_argvs():
        assert "-p" not in argv and "--patch" not in argv, \
            "a textconv driver turns `git log -p` into a process spawn"


def test_the_plan_filename_literal_matches_the_constant():
    """The argv must use the literal "roadmap.toml" to satisfy the guard, so
    this is what stops the two drifting apart."""
    from roadmapify.paths import PLAN_FILENAME
    assert any("roadmap.toml" in argv for argv in _git_argvs())
    assert PLAN_FILENAME == "roadmap.toml"


# ── parsing ───────────────────────────────────────────────────────────────────

def test_a_field_count_that_is_not_a_multiple_of_seven_yields_nothing():
    """A partial parse is worse than none: half a history reads as a project
    that did half the work."""
    commits, reason = gitsync.parse_history(b"only\x00three\x00fields\x00")
    assert commits == () and reason == gitsync.REASON_PARSE


def test_a_commit_message_containing_a_separator_lookalike_still_parses():
    """NUL is the only safe separator — a message may carry any other byte you
    might pick, but git refuses a NUL outright."""
    blob = b"\x00".join([b"a" * 40, b"", b"2026-01-01T00:00:00+00:00", b"Dev",
                         b"d@e", b"", b"subject\x1e\x1f with control bytes"]) + b"\x00"
    commits, reason = gitsync.parse_history(blob)
    assert reason == gitsync.REASON_OK and len(commits) == 1
    assert "control bytes" in commits[0].message


def test_a_symbolic_ref_is_dropped_from_the_ref_table():
    """`%(refname:short)` renders refs/remotes/origin/HEAD as the bare string
    "origin", which would otherwise appear as a phantom branch on the trunk."""
    blob = b"main\x00abc\x00\norigin\x00abc\x00refs/remotes/origin/main\n"
    names = [r.name for r in gitsync.parse_refs(blob)]
    assert "main" in names and "origin" not in names


@pytest.mark.parametrize("text,want", [
    ("Roadmap: T-01", ("T-01",)),
    ("roadmap: t-9", ("T-09",)),
    ("T-1 and T-01 are the same task", ("T-01",)),
    ("no task here", ()),
])
def test_task_ids_are_normalised_so_one_task_is_one_id(text, want):
    """`T-1` and `T-01` name the same task; two ids would double every count."""
    assert gitsync.task_ids(text) == want


def test_a_trailer_separated_by_a_blank_line_is_still_found():
    """git's own trailer parser skips a Roadmap: line that a blank line
    separates from the final block — and that shape occurs in this repo's own
    history, so the raw message is read too."""
    c = _commit("a" * 40, trailers=(),
                message="Do a thing\n\nRoadmap: T-07\n\nCo-Authored-By: X <x@y>\n")
    tasks, outside = gitsync.trailer_tasks(c)
    assert tasks == ("T-07",) and outside is True


# ── the commit graph ──────────────────────────────────────────────────────────

def test_a_merge_payload_is_what_the_second_parent_brought_in():
    commits = [_commit("m" * 40, parents=["a" * 40, "b" * 40]),
               _commit("a" * 40), _commit("b" * 40)]
    assert gitsync.merge_payload(commits, "m" * 40) == frozenset({"b" * 40})


def test_the_trunk_falls_back_through_the_whole_chain():
    """This repo needs the whole chain on day one: the trunk is origin/main
    while the local branch is master and there is no local main. The usual fix,
    `git remote set-head`, writes a ref and is forbidden."""
    f = gitsync.Facts(ok=True, branch="master",
                      refs=(gitsync.Ref("master", "a"), gitsync.Ref("origin/main", "b")))
    assert gitsync.trunk_ref(f) == "origin/main"
    assert gitsync.trunk_ref(gitsync.Facts(ok=True, branch="wip")) == "wip"


# ── records ───────────────────────────────────────────────────────────────────

def test_the_same_commit_always_produces_the_same_record_id():
    """Two developers who sync the same commit on two branches must produce the
    byte-identical line, or the union merge leaves both and every count
    doubles."""
    a = gitsync.evidence_id("commit", "T-01", "abc", "declared")
    b = gitsync.evidence_id("commit", "T-01", "abc", "declared")
    assert a == b and a.startswith("E-")
    assert a != gitsync.evidence_id("commit", "T-02", "abc", "declared")


def test_a_trailer_becomes_a_declared_record_and_nothing_else_does():
    """`inferred` is never written. A file-overlap guess is a proposal, and a
    stored guess is a stored status wearing a different hat."""
    plan = _plan([TaskSpec(id="T-01", label="a", phase="P-1",
                           produces=("file:a.py",))])
    f = gitsync.Facts(ok=True, commits=(_commit("a" * 40, message="x\n\nRoadmap: T-01\n"),),
                      files={"a" * 40: ("a.py",)})
    recs = gitsync.records_from(f, plan, mine="dev@example.com")
    assert len(recs) == 1
    assert recs[0]["confidence"] == "declared"
    assert all(r["confidence"] != "inferred" for r in recs)


def test_a_commit_by_another_author_is_quarantined_and_downgraded():
    """An unvetted trailer arriving through a pull request must not be able to
    flip a task to done on somebody else's say-so."""
    plan = _plan([TaskSpec(id="T-01", label="a", phase="P-1")])
    f = gitsync.Facts(ok=True, refs=(gitsync.Ref("main", "a" * 40),),
                      commits=(_commit("a" * 40, email="stranger@example.com",
                                       message="x\n\nRoadmap: T-01\n"),))
    rec = gitsync.records_from(f, plan, mine="me@example.com")[0]
    assert rec["trust"] == "foreign"
    assert rec["kind"] == "commit", "never merge — a stranger cannot mark it done"


def test_a_trailer_naming_an_unknown_task_is_reported_not_silently_dropped():
    """normalize_evidence would drop it without a word, and a trailer pointing
    at a deleted task is a typo worth seeing."""
    plan = _plan([TaskSpec(id="T-01", label="a", phase="P-1")])
    f = gitsync.Facts(ok=True, commits=(_commit("a" * 40, message="x\n\nRoadmap: T-99\n"),))
    assert gitsync.records_from(f, plan, mine="dev@example.com") == ()
    assert gitsync.dropped_ids(f, plan) == (("a" * 40, "T-99"),)


def test_file_overlap_proposes_only_where_exactly_one_task_owns_the_file():
    """A file two tasks declare cannot attribute a commit to either."""
    plan = _plan([TaskSpec(id="T-01", label="a", phase="P-1", produces=("file:a.py",)),
                  TaskSpec(id="T-02", label="b", phase="P-1", produces=("file:s.py",)),
                  TaskSpec(id="T-03", label="c", phase="P-1", produces=("file:s.py",))])
    f = gitsync.Facts(ok=True, files={"a" * 40: ("a.py", "s.py")})
    assert gitsync.proposals(f, plan) == {"a" * 40: ("T-01",)}


# ── mapping ───────────────────────────────────────────────────────────────────

def test_a_branch_naming_a_task_is_declared_and_needs_no_matcher():
    plan = _plan([TaskSpec(id="T-12", label="Read-only git plumbing", phase="P-1")])
    m = gitsync.map_branch("feat/T-12-git-plumbing", plan)
    assert m.task == "T-12" and m.confidence == "declared"


def test_a_branch_that_names_no_task_refuses_rather_than_guessing(  ):
    """A wrong mapping silently attributes work to the wrong task and corrupts
    every number derived from it."""
    plan = _plan([TaskSpec(id="T-12", label="Read-only git plumbing", phase="P-1")])
    m = gitsync.map_branch("fix", plan)
    assert m.task == "" and m.why


def test_two_close_candidates_produce_no_mapping_at_all():
    """A tie is not a mapping; it is two candidates and no answer."""
    plan = _plan([TaskSpec(id="T-01", label="git plumbing and branch mapping", phase="P-1"),
                  TaskSpec(id="T-02", label="git plumbing and branch mapping", phase="P-1")])
    m = gitsync.map_branch("git plumbing and branch mapping", plan, allow_matcher=True)
    assert m.task == "" and "too close" in m.why


# ── degenerate repositories ───────────────────────────────────────────────────

def test_a_directory_that_is_not_a_repository_is_typed_not_crashed(project):
    """The normal state after `roadmap init` in a plain directory."""
    f = gitsync.facts(project)
    assert f.ok is False
    assert f.reason == gitsync.REASON_NOT_A_REPO
    assert f.commits == ()


def test_nothing_in_gitsync_writes_outside_the_output_directory(project):
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    gitsync.facts(project)
    after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert before == after


def test_appending_the_same_records_twice_adds_nothing(project):
    """What makes `roadmap sync` safe to run from a hook on every commit."""
    recs = [gitsync.make("commit", "T-01", ts="2026-01-01T00:00:00Z", ref="abc")]
    assert len(gitsync.append_new(project, recs)) == 1
    assert gitsync.append_new(project, recs) == []
    assert len(gitsync.load_evidence(project)) == 1
