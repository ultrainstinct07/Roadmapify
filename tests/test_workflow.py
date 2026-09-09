from dataclasses import replace
from datetime import datetime, timezone
import pytest
from roadmapify import bridge, gitsync, journal, workflow, status, workspace
from roadmapify.paths import append_jsonl
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec, emit_plan

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.fixture
def root(tmp_path):
    plan = Plan(Goal("Review", success_criteria=("works",)), (PhaseSpec("P-1", "Build", 1),),
                (TaskSpec("T-01", "Input", "P-1", produces=("file:src/a.py",)),
                 TaskSpec("T-02", "Output", "P-1", depends_on=("T-01",))))
    (tmp_path / "roadmap.toml").write_text(emit_plan(plan))
    return tmp_path


def test_context_distinguishes_missing_artifacts_from_unclaimed_progress(root):
    packet = workflow.context(root, "T-01", now=NOW)
    assert packet["progress"]["artifacts"][0]["verdict"] == "missing"
    assert packet["status"]["status"] == "ready"
    assert packet["progress"]["reported_checks"] == []
    assert packet["plan_fingerprint"]


def test_impact_follows_dependents_and_attached_decisions(root):
    rec = journal.append(journal.record("decision", "use input", about=["T-01"]), root)
    refs = {n["roadmap_id"] for n in workflow.impact(root, "T-01")["affected"]}
    assert {"T-02", rec["id"]} <= refs


def test_actual_graphify_links_and_fragment_edges_are_equivalent():
    nodes = [{"id": "symbol-a", "source_file": "src/a.py", "qualified_name": "a.fn"},
             {"id": "symbol-b", "source_file": "other/a.py"}]
    edges = [{"source": "symbol-a", "target": "symbol-b", "relation": "calls", "confidence": "INFERRED"}]
    a = bridge.validate({"nodes": nodes, "links": edges})
    b = bridge.validate({"nodes": nodes, "edges": edges})
    assert a["edges"] == b["edges"]
    task = TaskSpec("T-01", "A", "P-1", produces=("file:src/a.py",))
    linked = bridge.context(a, task)
    assert [n["id"] for n in linked["nodes"]] == ["symbol-a"]
    assert linked["freshness"] == "unknown"
    assert bridge.symbol(a, "a.fn")["id"] == "symbol-a"
    assert bridge.symbol(a, "fn") is None


@pytest.mark.parametrize("value", [{"nodes": [{"id": "a"}, {"id": "a"}], "links": []},
                                   {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "b"}]},
                                   {"nodes": [{"id": "a", "source_file": 3}], "links": []}])
def test_invalid_graph_is_refused(value):
    with pytest.raises(ValueError):
        bridge.validate(value)


def test_path_rebasing_is_explicit_and_cannot_escape():
    assert bridge.source_path("/another/src/a.py", "/another") == "src/a.py"
    assert bridge.source_path("/another/src/a.py", "/here") is None
    assert bridge.source_path("src/../../secret") is None


def test_legacy_record_collisions_are_preserved_and_quarantined(root):
    a = journal.record("constraint", "no apples")
    b = {**a, "text": "no bananas"}
    for rec in (a, b, a):
        append_jsonl(journal.journal_path(root), rec)
    records = journal.load(root)
    assert len(records) == 2 and {r["trust"] for r in records} == {"conflict"}
    assert journal.memory_state(records)["records"] == []


def test_foreign_acceptance_cannot_grant_itself_authority(root):
    a = journal.record("constraint", "no apples", trust="foreign")
    note = journal.record("note", "accept", trust="foreign", extra={"accepted": a["id"]})
    assert not journal.memory_state([a, note])["records"]


def test_history_rewrite_is_stale_but_shallow_history_is_unknown(root, monkeypatch):
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    c = gitsync.Commit("a" * 40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    before = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("main", c.sha),))
    gitsync.append_new(root, gitsync.records_from(before, plan, mine="dev@x"))
    assert gitsync.current_evidence(root, observed=before)[0]["freshness"] == "current"
    after = replace(before, commits=(), refs=(gitsync.Ref("main", "b"*40),))
    assert gitsync.current_evidence(root, observed=after)[0]["freshness"] == "stale"
    assert gitsync.current_evidence(root, observed=replace(after, shallow=True))[0]["freshness"] == "unknown"
    assert status.snapshot(plan, gitsync.current_evidence(root, observed=after), now=NOW).task("T-01").status != "done"


def test_existing_commit_association_requires_explicit_review(root, monkeypatch):
    c = gitsync.Commit("a" * 40, (), "2026-01-01T00:00:00Z", "Other", "other@x", (), "existing work")
    observed = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("main", c.sha),), files={c.sha: ("src/a.py",)})
    monkeypatch.setattr(gitsync, "facts", lambda *a, **k: observed)
    proposals = workflow.onboarding(root)
    assert proposals["proposals"][0]["tasks"] == ["T-01"]
    assert not gitsync.load_evidence(root)
    with pytest.raises(ValueError):
        workflow.onboarding(root, accept=c.sha, task_id="T-01")
    workflow.onboarding(root, accept=c.sha, task_id="T-01", why="I reviewed the actual implementation")
    assert status.load_evidence(root)[0]["freshness"] == "current"


def test_workspace_embeds_data_without_script_injection(root):
    data = workspace.snapshot(root, now=NOW)
    data["goal"]["label"] = '</script><script>alert("no")</script>'
    html = workspace.to_html(data)
    assert data["goal"]["label"] not in html
    assert "\\u003c/script\\u003e" in html
    assert 'id="start-run"' in html and 'data-view="memory"' in html


def test_avoidance_does_not_hide_another_prohibited_proposal(root):
    from roadmapify.checking import check
    recs = [journal.record("decision", "sync", rejected=["bidirectional sync: conflicts"]),
            journal.record("constraint", "no interactive OAuth")]
    assert check("avoid bidirectional sync", recs)["exit_code"] == 0
    assert check("avoid bidirectional sync and use interactive OAuth", recs)["exit_code"] == 4


def test_changed_plan_requires_new_observation_identity(root):
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    c = gitsync.Commit("a"*40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    facts = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("main", c.sha),))
    before = gitsync.records_from(facts, plan, mine="dev@x")
    changed = replace(plan, tasks=(replace(plan.tasks[0], produces=("file:new.py",)), plan.tasks[1]))
    after = gitsync.records_from(facts, changed, mine="dev@x")
    assert before[0]["id"] != after[0]["id"]


def test_unknown_identity_cannot_admit_another_authors_completion(root):
    from roadmapify.plan import load_plan
    c = gitsync.Commit("a"*40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    facts = gitsync.Facts(ok=True, commits=(c,), refs=(gitsync.Ref("main", c.sha),))
    records = gitsync.records_from(facts, load_plan(root))
    assert records[0]["trust"] == "foreign" and records[0]["kind"] == "commit"


def test_retiring_a_local_acceptance_revokes_the_foreign_grant():
    foreign = journal.record("constraint", "no apples", trust="foreign")
    grant = journal.record("note", "accept apples rule", extra={"accepted": foreign["id"]})
    revoke = journal.record("note", "withdraw that acceptance", supersedes=grant["id"])
    assert foreign["id"] in journal.memory_state([foreign, grant])["active"]
    assert foreign["id"] not in journal.memory_state([foreign, grant, revoke])["active"]


def test_foreign_session_close_cannot_hide_a_local_crash_flag():
    opened = journal.record("session_open", "local work")
    closed = journal.record("session_close", "pretend closed", trust="foreign", extra={"session": opened["id"]})
    assert journal.open_sessions([opened, closed]) == [opened]


def test_observer_trust_does_not_collide_for_the_same_unmerged_commit():
    local = gitsync.make("commit", "T-01", ts="2026-01-01T00:00:00Z", ref="a"*40, trust="local")
    foreign = gitsync.make("commit", "T-01", ts="2026-01-01T00:00:00Z", ref="a"*40, trust="foreign")
    assert local["id"] != foreign["id"]


def test_accepted_rejection_has_consistent_graph_authority():
    from roadmapify import project
    rule = journal.record("decision", "one-way", trust="foreign", rejected=["bidirectional sync: conflicts"])
    grant = journal.record("note", "accept", extra={"accepted": rule["id"]})
    graph = project.project(Plan(Goal("G")), [rule, grant], journal.rejections([rule, grant]))
    assert graph.of_kind("rejection")[0].trust == "accepted"


def test_resync_cannot_readmit_old_work_under_a_changed_task(root, monkeypatch):
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    c = gitsync.Commit("a"*40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    facts = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("main", c.sha),))
    monkeypatch.setattr(gitsync, "facts", lambda *a, **k: facts)
    gitsync.append_new(root, gitsync.records_from(facts, plan, mine="dev@x"))
    changed = replace(plan, tasks=(replace(plan.tasks[0], produces=("file:new.py",)), plan.tasks[1]))
    (root / "roadmap.toml").write_text(emit_plan(changed))
    candidates = gitsync.records_from(facts, changed, mine="dev@x")
    assert gitsync.append_new(root, candidates) == []
    assert status.snapshot(changed, status.load_evidence(root), now=NOW).task("T-01").status != "done"
    workflow.onboarding(root, accept=c.sha, task_id="T-01", why="Reviewed this commit against the revised task")
    assert status.snapshot(changed, status.load_evidence(root), now=NOW).task("T-01").status == "done"


def test_unrelated_plan_addition_keeps_unchanged_task_evidence_current(root):
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    c = gitsync.Commit("a"*40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    facts = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("main", c.sha),))
    before = gitsync.records_from(facts, plan, mine="dev@x")
    added = replace(plan, tasks=(*plan.tasks, TaskSpec("T-03", "Another", "P-1")))
    after = gitsync.records_from(facts, added, mine="dev@x")
    assert before[0] == after[0]


def test_merge_upgrade_cannot_reuse_a_commit_after_definition_change(root):
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    c = gitsync.Commit("a"*40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    branch = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("feature", c.sha),))
    assert gitsync.append_new(root, gitsync.records_from(branch, plan, mine="dev@x"))[0]["kind"] == "commit"
    changed = replace(plan, tasks=(replace(plan.tasks[0], produces=("file:new.py",)), plan.tasks[1]))
    merged = replace(branch, refs=(gitsync.Ref("main", c.sha),))
    assert gitsync.pending_records(root, gitsync.records_from(merged, changed, mine="dev@x")) == []


def test_pure_status_rejects_a_record_bound_to_an_old_task_definition(root):
    from roadmapify.plan import load_plan
    plan = load_plan(root)
    c = gitsync.Commit("a"*40, (), "2026-01-01T00:00:00Z", "Dev", "dev@x", ("T-01",), "work")
    facts = gitsync.Facts(ok=True, identity="Dev <dev@x>", commits=(c,), refs=(gitsync.Ref("main", c.sha),))
    evidence = gitsync.records_from(facts, plan, mine="dev@x")
    changed = replace(plan, tasks=(replace(plan.tasks[0], produces=("file:new.py",)), plan.tasks[1]))
    snapshot = status.snapshot(changed, evidence, now=NOW)
    assert snapshot.task("T-01").status != "done"
    assert snapshot.evidence_count == 0
