"""Desired behavior for all 18 reproduced review defects."""
from __future__ import annotations

import ast
import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roadmapify import cli, export, gitsync, hooks, journal, project, render
from roadmapify import serve, status, traverse, verify
from roadmapify.paths import append_jsonl, out_path
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec, emit_plan, validate_plan

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)
TS = "2026-01-01T00:00:00Z"


def plan(*tasks):
    return Plan(Goal("Review fixture"),
                (PhaseSpec("P-1", "One", 1, ships="example"),), tuple(tasks))


def task(tid="T-01", **kw):
    return TaskSpec(tid, tid, "P-1", **kw)


def seed(root, p=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "roadmap.toml").write_text(emit_plan(p or plan(task())))


def command(root, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = cli.dispatch([*args, "--root", str(root)])
    return code, output.getvalue()


def rec(kind, text, **kw):
    return journal.record(kind, text, ts=TS, author="review", **kw)


def memory_lifecycle(root):
    seed(root)
    old = journal.append(rec("constraint", "no interactive OAuth"), root)
    journal.append(rec("note", "retired", supersedes=old["id"]), root)
    assert command(root, "check", "use interactive OAuth")[0] == 0
    cli.refresh_brief(root, now=NOW)
    assert old["text"] not in out_path("BRIEF.md", root=root).read_text()
    records = journal.load(root)
    assert old["text"] not in export.to_html(plan(task()), records, [], now=NOW)
    return "retired constraint remains in BRIEF and HTML after check stops enforcing it"


def foreign_supersession(root):
    seed(root)
    old = journal.append(rec("constraint", "no interactive OAuth"), root)
    before = command(root, "check", "use interactive OAuth")[0]
    journal.append(rec("note", "foreign retirement", trust="foreign",
                       supersedes=old["id"]), root)
    after = command(root, "check", "use interactive OAuth")[0]
    assert (before, after) == (4, 4)
    return "unaccepted foreign supersession changes check exit 4 to exit 0"


def acceptance_display(root):
    seed(root)
    c = journal.append(rec("constraint", "no interactive OAuth", trust="foreign"), root)
    assert command(root, "doctor", "--accept", c["id"])[0] == 0
    assert command(root, "check", "use interactive OAuth")[0] == 4
    text = out_path("BRIEF.md", root=root).read_text()
    assert "Recorded by others (not enforced)" not in text
    assert "Constraints (non-negotiable)" in text
    return "accepted foreign constraint enforces but BRIEF still labels it not enforced"


def mcp_parity(root):
    seed(root)
    journal.append(rec("constraint", "one conditional dependency only",
                       why="no networkx"), root)
    for text in ("no orange clocks", "no violet boats", "no silver moons",
                 "no amber wheels", "no purple stars"):
        journal.append(rec("constraint", text), root)
    query = "use networkx for graph operations"
    code = command(root, "check", query)[0]
    mcp = serve.call_tool("check_approach", {"approach": query}, root=root)
    assert code == 4 and mcp.startswith("VIOLATES_CONSTRAINT")
    return "CLI rejects the named dependency with exit 4; MCP returns CLEAR"


def stale_views(root):
    seed(root)
    cli.refresh_brief(root, now=NOW)
    p = plan(replace(task(), label="New task label"))
    (root / "roadmap.toml").write_text(emit_plan(p))
    assert "New task label" in command(root, "explain", "T-01")[1]
    assert "New task label" in serve.call_tool("get_node", {"label": "T-01"}, root=root)
    return "CLI and MCP get_node use old graph after the source plan changes"


def uncheckable_report(root):
    seed(root)
    p = plan(task(produces=("cmd:example --self-test",)))
    snap = status.snapshot(p, [{"id": "E-claim", "kind": "claim", "task": "T-01", "ts": TS, "trust": "local"}], now=NOW)
    report = verify.verify(p, snap, root)
    assert report.tasks[0].finding == "uncheckable_claim"
    assert report.verdict == verify.UNCHECKABLE
    assert "every declared deliverable is on disk" not in render.verify_screen(p, report, now=NOW)
    return "uncheckable_claim has overall corroborated verdict and all-on-disk summary"


def foreign_evidence(root):
    p = plan(task())
    evidence = [{"kind": "merge", "task": "T-01", "trust": "foreign"}]
    assert status.snapshot(p, evidence, now=NOW).task("T-01").status != "done"
    return "foreign merge record with no commit ref or id derives done"


def stale_evidence(root):
    seed(root)
    p = plan(task())
    c = gitsync.Commit("a" * 40, (), TS, "A", "a@example.test", ("T-01",), "work")
    before = gitsync.Facts(ok=True, refs=(gitsync.Ref("main", c.sha),), commits=(c,))
    gitsync.append_new(root, gitsync.records_from(before, p, mine=c.email))
    after = gitsync.Facts(ok=True, refs=(gitsync.Ref("main", "b" * 40),), commits=())
    gitsync.append_new(root, gitsync.records_from(after, p, mine=c.email))
    assert status.snapshot(p, status.load_evidence(root), now=NOW).task("T-01").status != "done"
    return "old merge remains done after new facts no longer contain its commit"


def hashed_task_id(root):
    p = plan(task("T-2f9a"))
    assert validate_plan(p) == []
    assert gitsync.task_ids("Roadmap: T-2f9a") == ("T-2f9a",)
    assert gitsync.map_branch("feature/T-2f9a-example", p).task == "T-2f9a"
    return "valid content-hashed task id cannot be recognized by trailer or branch parser"


def record_collision(root):
    a = rec("decision", "same decision", why="reason from branch A")
    b = rec("decision", "same decision", why="reason from branch B")
    assert a["id"] != b["id"] and a != b
    first, second = root / "first", root / "second"
    for r in (a, b):
        append_jsonl(journal.journal_path(first), r)
    for r in (b, a):
        append_jsonl(journal.journal_path(second), r)
    assert len(journal.load(first)) == len(journal.load(second)) == 2
    assert journal.load(first) == journal.load(second)
    return "two valid records share id; union order chooses which reason survives"


def remaining_chain(root):
    p = plan(task("T-01"), task("T-02", depends_on=("T-01",)),
             task("T-03", depends_on=("T-02",)), task("T-04"),
             task("T-05", depends_on=("T-04",)))
    ev = [{"id": "E-" + x, "kind": "merge", "task": x, "ref": "ref-"+x, "trust": "local", "ts": TS} for x in ("T-01", "T-02", "T-03")]
    snap = status.snapshot(p, ev, now=NOW)
    assert snap.remaining_path == ("T-04", "T-05") and snap.ready == ("T-04",)
    screen = render.next_screen(p, snap, now=NOW)
    assert "T-04 → T-05" in screen
    return "finished original chain remains the displayed critical path while another chain is unfinished"


def symlink_ancestor(root):
    seed(root)
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "test_external.py").write_text("def test_external(): pass\n")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    r = verify.resolve_spec("test:linked/test_external.py", root)
    g = verify.resolve_spec("glob:linked/*.py", root)
    assert r.verdict == "unresolvable"
    assert g.verdict == "unresolvable" and g.matches == 0
    return "verifier parses and globs outside the project through a symlinked ancestor"


def hook_path(root):
    body = hooks._hook_body("/usr/bin/python3")
    assert "Path('$_ROOT')" not in body
    assert "Path(sys.argv[1])" in body
    assert '" "$_ROOT"' in body
    assert "&& exit 0" not in body
    return "hook root passed as argv data"


def json_flag(root):
    seed(root)
    code, text = command(root, "check", "independent example", "--json")
    assert code == 0
    result = json.loads(text)
    assert result["verdict"] == "CLEAR" and result["exit_code"] == 0
    return "structured CLI check"


def graph_collision(root):
    p = plan(task(produces=("file:a-b.py", "file:a_b.py")))
    g = project.project(p)
    assert len(g.of_kind("deliverable")) == 2
    return "different deliverable paths collapse to one slug-based graph node"


def trust_in_retrieval(root):
    r = rec("constraint", "no interactive OAuth", trust="foreign")
    g = project.to_json(project.project(plan(task()), [r]))
    node = next(n for n in g["nodes"] if n["roadmap_id"] == r["id"])
    assert node["roadmap_trust"] == "foreign"
    text = traverse.explain_screen(traverse.explain(g, r["id"]), r["id"])
    assert "foreign" in text and "not enforced" in text
    return "graph has foreign trust metadata but explain text drops it"


def negative_proposal(root):
    seed(root)
    journal.append(rec("decision", "one-way sync", rejected=["bidirectional sync: conflicts"]), root)
    assert command(root, "check", "avoid bidirectional sync")[0] == 0
    return "avoiding an explicitly rejected approach is itself rejected"


def session_scope(root):
    seed(root)
    a = journal.append(rec("session_open", "agent A"), root)
    b = journal.append(rec("session_open", "agent B"), root)
    assert command(root, "resume", a["id"])[0] == 0
    open_ = journal.open_sessions(root=root)
    assert len(open_) == 2 and a["id"] not in {r["id"] for r in open_}
    resumed = next(r for r in open_ if r["id"] != b["id"])
    assert command(root, "checkpoint", "agent A finished")[0] == 1
    assert command(root, "checkpoint", "agent A finished", "--session", resumed["id"])[0] == 0
    assert [r["id"] for r in journal.open_sessions(root=root)] == [b["id"]]
    return "checkpoint closes only the selected session"



CASES = [memory_lifecycle, foreign_supersession, acceptance_display, mcp_parity,
         stale_views, uncheckable_report, foreign_evidence, stale_evidence,
         hashed_task_id, record_collision, remaining_chain, symlink_ancestor,
         hook_path, json_flag, graph_collision, trust_in_retrieval,
         negative_proposal, session_scope]


@pytest.mark.parametrize("case", CASES, ids=lambda f: f.__name__)
def test_review_regression(case, tmp_path):
    case(tmp_path / case.__name__)
