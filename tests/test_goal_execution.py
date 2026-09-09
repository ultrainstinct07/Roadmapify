"""Execution invariants and an end-to-end host exercising real file changes."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pytest
from roadmapify import execution, gitsync, journal, status
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec, emit_plan

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    plan = Plan(Goal("Ship the example", success_criteria=("the example works",)),
                (PhaseSpec("P-1", "Build", 1, ships="working example"),),
                (TaskSpec("T-01", "First", "P-1", produces=("file:first.txt",)),
                 TaskSpec("T-02", "Second", "P-1", depends_on=("T-01",), produces=("file:second.txt",))))
    (tmp_path / "roadmap.toml").write_text(emit_plan(plan))
    facts = {"value": gitsync.Facts(ok=True, identity="Dev <dev@example.com>")}
    monkeypatch.setattr(gitsync, "facts", lambda *a, **k: facts["value"])
    return tmp_path, plan, facts


def completed_commit(facts, tid, index):
    old = facts["value"]
    sha = str(index) * 40
    parents = (old.commits[0].sha,) if old.commits else ()
    commit = gitsync.Commit(sha, parents, "2026-01-01T00:00:00Z", "Dev", "dev@example.com", (tid,), tid)
    facts["value"] = replace(old, refs=(gitsync.Ref("main", sha),), commits=(commit, *old.commits))


def test_host_executes_bounded_steps_and_goal_needs_explicit_acceptance(workspace):
    root, plan, facts = workspace
    execution.start(root, now=NOW, max_steps=3)
    class Host:
        def propose(self, packet):
            return "write the declared example artifact"
        def work(self, packet, control):
            assert control() == "running"
            tid = packet["task"]["id"]
            name = "first.txt" if tid == "T-01" else "second.txt"
            (root / name).write_text("actual fixture work\n")
            completed_commit(facts, tid, 1 if tid == "T-01" else 2)
            return {"outcome": "succeeded", "changed_paths": [name],
                    "checks": [{"name": "read artifact", "outcome": "passed"}]}
    result = execution.drive(root, Host(), worker="test-host", now_fn=lambda: NOW)
    assert result["steps_used"] == 2 and len(result["history"]) == 2
    assert result["state"] == "awaiting_user"
    assert (root / "second.txt").read_text() == "actual fixture work\n"
    execution.accept(root, 0, "I inspected the example and accept the outcome", now=NOW)
    final = execution.resume(root, now=NOW)
    assert final["state"] == "completed"


def test_reported_success_cannot_complete_uncommitted_work(workspace):
    root, _, _ = workspace
    execution.start(root, now=NOW)
    packet = execution.claim(root, "host", now=NOW)["packet"]
    execution.check_approach(root, "host", packet["step_id"], "write first artifact", now=NOW)
    (root / "first.txt").write_text("prepared")
    result = execution.finish(root, "host", packet["step_id"], {"outcome": "succeeded"}, now=NOW)
    assert result["state"] == "awaiting_user" and result["steps_used"] == 1
    assert "insufficient" in result["reason"]
    assert status.load_evidence(root) == []


def test_claim_reconnect_does_not_dispatch_duplicate_work(workspace):
    root, _, _ = workspace
    execution.start(root, now=NOW)
    first = execution.claim(root, "host", now=NOW)
    again = execution.claim(root, "host", now=NOW)
    assert first["packet"]["step_id"] == again["packet"]["step_id"]
    assert again["reconcile_existing"] and execution.read(root)["steps_used"] == 1
    with pytest.raises(ValueError):
        execution.claim(root, "other-host", now=NOW)


def test_pause_and_cancel_need_host_acknowledgment(workspace):
    root, _, _ = workspace
    execution.start(root, now=NOW)
    packet = execution.claim(root, "host", now=NOW)["packet"]
    assert execution.request(root, "pause", now=NOW)["state"] == "pause_requested"
    with pytest.raises(ValueError):
        execution.resume(root, now=NOW)
    assert execution.request(root, "cancel", now=NOW)["state"] == "cancel_requested"
    result = {"outcome": "cancelled", "summary": "host stopped"}
    final = execution.finish(root, "host", packet["step_id"], result, now=NOW)
    assert final["state"] == "cancelled"
    assert execution.finish(root, "host", packet["step_id"], result, now=NOW) == final


def test_deadline_requests_stop_without_claiming_agent_stopped(workspace):
    root, _, _ = workspace
    execution.start(root, now=NOW, minutes=1)
    execution.claim(root, "host", now=NOW)
    late = execution.tick(root, now=NOW + timedelta(minutes=2))
    assert late["state"] == "pause_requested"


def test_changed_memory_pauses_before_dispatch(workspace):
    root, _, _ = workspace
    execution.start(root, now=NOW)
    journal.append(journal.record("constraint", "no first artifact"), root)
    assert execution.tick(root, now=NOW)["state"] == "paused"
    with pytest.raises(ValueError):
        execution.claim(root, "host", now=NOW)


def test_success_requires_an_approach_check(workspace):
    root, _, _ = workspace
    execution.start(root, now=NOW)
    packet = execution.claim(root, "host", now=NOW)["packet"]
    with pytest.raises(ValueError, match="approach"):
        execution.finish(root, "host", packet["step_id"], {"outcome": "succeeded"}, now=NOW)


def test_recovery_resumes_reconciliation_after_result_is_saved(workspace, monkeypatch):
    root, _, _ = workspace
    execution.start(root, now=NOW)
    packet = execution.claim(root, "host", now=NOW)["packet"]
    original = execution._reconcile
    def crash(*a, **k):
        raise OSError("simulated crash after result receipt")
    monkeypatch.setattr(execution, "_reconcile", crash)
    with pytest.raises(OSError):
        execution.finish(root, "host", packet["step_id"], {"outcome": "paused"}, now=NOW)
    assert execution.read(root)["state"] == "observing"
    monkeypatch.setattr(execution, "_reconcile", original)
    restored = execution.tick(root, now=NOW)
    assert restored["state"] == "paused" and restored["steps_used"] == 1
    assert len(restored["history"]) == 1


@pytest.mark.parametrize("kwargs", [{"max_steps": 0}, {"minutes": 0}, {"max_steps": True}, {"scope": ["T-99"]}])
def test_invalid_limits_or_scope_rejected(workspace, kwargs):
    with pytest.raises(ValueError):
        execution.start(workspace[0], now=NOW, **kwargs)


def test_run_state_never_writes_plan_or_task_status(workspace):
    root, _, _ = workspace
    before = (root / "roadmap.toml").read_bytes()
    execution.start(root, now=NOW)
    execution.request(root, "cancel", now=NOW)
    assert (root / "roadmap.toml").read_bytes() == before
    assert execution.read(root)["usage"] == {"tokens": None, "cost": None}


def test_artifact_changes_invalidate_human_acceptance(workspace):
    root, plan, facts = workspace
    for tid, index, name in (("T-01", 1, "first.txt"), ("T-02", 2, "second.txt")):
        (root / name).write_text("reviewed content")
        completed_commit(facts, tid, index)
    gitsync.append_new(root, gitsync.records_from(facts["value"], plan, mine="dev@example.com"))
    execution.start(root, now=NOW)
    execution.accept(root, 0, "reviewed", now=NOW)
    (root / "second.txt").write_text("changed after review")
    assert execution.resume(root, now=NOW)["state"] == "awaiting_user"
