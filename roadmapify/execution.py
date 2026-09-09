"""Durable, bounded goal execution through an existing local agent host.

Roadmapify publishes work; the host claims it and uses its own authorized tools.
No subprocess, provider import, plan command execution, or task-status mutation.
Every transition is locked and persisted atomically. A claimed step is never
redispatched after a crash; its owning host must reconcile its durable step ID.
"""
from __future__ import annotations
import hashlib
import json
import secrets
from datetime import timedelta
from pathlib import Path
from roadmapify import gitsync, journal, status, verify, workflow
from roadmapify.paths import derive_lock, out_path, write_json_atomic

STATE_FILE = ".goal-run.json"
TERMINAL = {"completed", "cancelled", "failed"}
RESULTS = {"succeeded", "failed", "needs_user", "paused", "cancelled"}


def read(root):
    path = out_path(STATE_FILE, root=root)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 5_000_000:
        raise ValueError("unsafe or oversized goal state")
    state = json.loads(path.read_text())
    if state.get("workspace") != str(Path(root).resolve()):
        raise ValueError("goal state belongs to a different workspace")
    return state


def save(root, state, *, now):
    state["updated_at"] = now.isoformat()
    state["revision"] = state.get("revision", 0) + 1
    if len(json.dumps(state)) > 5_000_000:
        raise ValueError("goal state exceeds 5 MB; shorten reported summaries")
    write_json_atomic(out_path(STATE_FILE, root=root), state)
    return state


def transition(state, value, reason, *, now):
    state["state"], state["reason"] = value, reason
    state.setdefault("events", []).append({"at": now.isoformat(), "state": value, "reason": reason})

    if len(state["events"]) > 200:
        state["events_trimmed"] = state.get("events_trimmed", 0) + len(state["events"]) - 200
        state["events"] = state["events"][-200:]


def _required(root):
    state = read(root)
    if state is None:
        raise ValueError("no goal run; start one with explicit limits")
    return state


def _fingerprints(root, *, now):
    plan, records, evidence, snap = workflow.inputs(root, now=now)
    return plan, records, evidence, snap, gitsync.plan_fingerprint(plan), workflow.memory_fingerprint(records)


def _expired(state, now):
    from roadmapify.render import parse_ts
    return now >= parse_ts(state["deadline"])


def _same_inputs(state, plan_hash, memory_hash):
    return state["plan_fingerprint"] == plan_hash and state["memory_fingerprint"] == memory_hash


def _publish(root, state, *, now):
    plan, records, evidence, snap, plan_hash, memory_hash = _fingerprints(root, now=now)
    if not _same_inputs(state, plan_hash, memory_hash):
        transition(state, "paused", "goal, plan or effective memory changed; review before resuming", now=now)
        return
    choices = [tid for tid in snap.ready if tid in state["scope"]
               and all(snap.task(d).status == status.DONE for d in plan.task(tid).depends_on)]
    if not choices:
        report = verify.verify(plan, snap, root)
        all_done = bool(plan.tasks) and all(snap.task(t.id).status == status.DONE
                                          for t in plan.tasks if not t.provisional)
        criteria = list(plan.goal.success_criteria)
        accepted = state.get("acceptance", {})
        stamp = evidence_stamp(evidence) + workflow.artifact_stamp(root, plan)
        gate = (all_done and bool(criteria) and report.verdict == verify.CORROBORATED and
                all(accepted.get(str(i), {}).get("evidence_stamp") == stamp
                    for i in range(len(criteria))))
        transition(state, "completed" if gate else "awaiting_user",
                   "all goal criteria accepted with current supporting evidence" if gate else
                   "no ready work in scope; inspect blockers, integration and goal acceptance", now=now)
        state["step"] = None
        return
    if _expired(state, now) or state["steps_used"] >= state["max_steps"]:
        transition(state, "paused", "run budget exhausted", now=now)
        return
    task = choices[0]
    packet = workflow.context(root, task, now=now)
    step_id = state["id"] + ":" + str(state["steps_used"] + 1)
    packet.update(run_id=state["id"], step_id=step_id, workspace=state["workspace"],
                  deadline=state["deadline"], limits={"max_steps": state["max_steps"],
                                                     "steps_used": state["steps_used"]})
    if len(json.dumps(packet)) > 128_000:
        raise ValueError("step context exceeds 128 KB; narrow or retire obsolete memory before dispatch")
    state["step"] = {"id": step_id, "task": task, "packet": packet, "worker": None,
                     "result": None, "approach": None}
    transition(state, "queued", "bounded step ready for an agent host to claim", now=now)


def start(root, *, now, max_steps=3, minutes=20, scope=()):
    if type(max_steps) is not int or not 1 <= max_steps <= 100 or type(minutes) is not int or not 1 <= minutes <= 240:
        raise ValueError("limits: 1–100 steps and 1–240 minutes")
    with derive_lock(root):
        existing = read(root)
        if existing and existing["state"] not in TERMINAL:
            raise ValueError("a run already exists; resume or cancel it")
        plan, records, evidence, snap, ph, mh = _fingerprints(root, now=now)
        allowed = [t.id for t in plan.tasks if not t.provisional]
        if scope and not set(scope).issubset(allowed):
            raise ValueError("scope must name known, reviewed tasks")
        state = {"schema": 1, "id": secrets.token_hex(16), "workspace": str(Path(root).resolve()),
                 "created_at": now.isoformat(), "deadline": (now + timedelta(minutes=minutes)).isoformat(),
                 "max_steps": max_steps, "steps_used": 0, "scope": list(scope) or allowed,
                 "plan_fingerprint": ph, "memory_fingerprint": mh,
                 "state": "preparing", "history": [], "acceptance": {}, "step": None,
                 "usage": {"tokens": None, "cost": None}, "permissions": "existing host permissions; no git ref writes"}
        _publish(root, state, now=now)
        return save(root, state, now=now)


def tick(root, *, now):
    with derive_lock(root):
        state = _required(root)
        if state["state"] in TERMINAL or state["state"] in ("paused", "awaiting_user", "pause_requested", "cancel_requested"):
            return state
        plan, records, evidence, snap, ph, mh = _fingerprints(root, now=now)
        if _expired(state, now) or not _same_inputs(state, ph, mh):
            running = state["state"] == "running"
            transition(state, "pause_requested" if running else "paused",
                       "deadline or context changed; host acknowledgment required" if running else
                       "deadline or context changed", now=now)
            return save(root, state, now=now)
        if state["state"] == "observing":
            _reconcile(root, state, now=now)
            return save(root, state, now=now)
        return state


def claim(root, worker, *, now):
    if not isinstance(worker, str) or not worker.strip() or len(worker) > 120:
        raise ValueError("worker must be a nonempty host identity up to 120 characters")
    tick(root, now=now)
    with derive_lock(root):
        state = _required(root)
        step = state.get("step")
        if step and step["worker"] == worker and state["state"] in ("running", "pause_requested", "cancel_requested"):
            return {"state": state["state"], "packet": step["packet"], "reconcile_existing": True}
        if state["state"] != "queued":
            raise ValueError("no queued step; inspect the run state")
        step["worker"] = worker
        state["steps_used"] += 1
        transition(state, "running", "step claimed by " + worker, now=now)
        save(root, state, now=now)
        return {"state": state["state"], "packet": step["packet"], "reconcile_existing": False}


def check_approach(root, worker, step_id, approach, *, now):
    from roadmapify.checking import check
    with derive_lock(root):
        state = _required(root)
        step = _owned(state, worker, step_id)
        if state["state"] != "running" or _expired(state, now):
            raise ValueError("run is not authorized to perform work")
        plan, records, evidence, snap, ph, mh = _fingerprints(root, now=now)
        if not _same_inputs(state, ph, mh):
            raise ValueError("context changed; acknowledge pause and review before resuming")
        if not isinstance(approach, str) or not approach.strip() or len(approach) > 4000:
            raise ValueError("a bounded, concrete approach is required")
        result = check(approach, records, plan)
        step["approach"] = result
        save(root, state, now=now)
        return result


def _owned(state, worker, step_id):
    step = state.get("step")
    if not step or step["id"] != step_id or step["worker"] != worker:
        raise ValueError("worker/step does not own the current execution handle")
    return step


def request(root, action, *, now):
    if action not in ("pause", "cancel"):
        raise ValueError("expected pause or cancel")
    with derive_lock(root):
        state = _required(root)
        if state["state"] in TERMINAL:
            return state
        value = (action + "_requested") if state["state"] in ("running", "pause_requested", "cancel_requested") else ("paused" if action == "pause" else "cancelled")
        if state["state"] == "cancel_requested":
            value = "cancel_requested"
        transition(state, value, action + " requested; claimed work requires host acknowledgment", now=now)
        return save(root, state, now=now)


def finish(root, worker, step_id, result, *, now):
    if not isinstance(result, dict) or result.get("outcome") not in RESULTS or len(json.dumps(result)) > 16_000:
        raise ValueError("result requires a bounded valid outcome")
    if not isinstance(result.get("changed_paths", []), list) or not isinstance(result.get("checks", []), list):
        raise ValueError("changed_paths and checks must be arrays")
    for path in result.get("changed_paths", []):
        if not isinstance(path, str) or verify.anchor(root, path) is None:
            raise ValueError("reported changed path escapes workspace")
    with derive_lock(root):
        state = _required(root)
        for previous in state["history"]:
            if previous["id"] == step_id:
                if previous["worker"] == worker and previous["result"] == result:
                    return state
                raise ValueError("conflicting duplicate result")
        step = _owned(state, worker, step_id)
        if step.get("result") is not None:
            if step["result"] == result:
                return state
            raise ValueError("conflicting duplicate result")
        if state["state"] not in ("running", "pause_requested", "cancel_requested"):
            raise ValueError("step is not running")
        if result["outcome"] == "succeeded" and (not step["approach"] or step["approach"]["exit_code"] != 0):
            raise ValueError("a clear current approach check is required before reporting successful work")
        step["result"] = result
        step["stop_requested"] = state["state"] if state["state"].endswith("_requested") else ""
        # Persist receipt BEFORE reconciliation. Recovery resumes observing,
        # never dispatches the same work again.
        transition(state, "observing", "host returned; reconciling observations", now=now)
        save(root, state, now=now)
        _reconcile(root, state, now=now)
        return save(root, state, now=now)


def _reconcile(root, state, *, now):
    step = state["step"]
    result = step["result"]
    stop = step.get("stop_requested", "")
    summary = {k: step[k] for k in ("id", "task", "worker", "result")}
    if not any(h["id"] == step["id"] for h in state["history"]):
        state["history"].append(summary)
    state["step"] = None
    if stop or result["outcome"] != "succeeded":
        final = "cancelled" if stop == "cancel_requested" or result["outcome"] == "cancelled" else "paused"
        if result["outcome"] == "needs_user":
            final = "awaiting_user"
        transition(state, final, "host acknowledged stop: " + str(result.get("summary", result["outcome"])), now=now)
        return
    plan, records, evidence, snap, ph, mh = _fingerprints(root, now=now)
    if not _same_inputs(state, ph, mh):
        transition(state, "paused", "context changed during work; inspect the result", now=now)
        return
    facts = gitsync.facts(root)
    if facts.ok:
        observed = gitsync.records_from(facts, plan, mine=gitsync.identity_email(facts.identity),
                                       associations=gitsync.reviewed_associations(records))
        gitsync.append_new(root, observed)
    evidence = gitsync.current_evidence(root, observed=facts, plan=plan)
    snap = status.snapshot(plan, evidence, now=now)
    report = verify.verify(plan, snap, root, task=summary["task"])
    summary["verification"] = report.verdict
    summary["artifacts"] = [r.verdict for r in report.resolutions]
    if snap.task(summary["task"]).status != status.DONE or report.verdict != verify.CORROBORATED:
        transition(state, "awaiting_user", "work reported; current completion evidence or artifact support is insufficient. Inspect checks and integrate changes before resuming", now=now)
        return
    _publish(root, state, now=now)


def resume(root, *, now, minutes=None, max_steps=None):
    with derive_lock(root):
        state = _required(root)
        if state["state"] not in ("paused", "awaiting_user"):
            raise ValueError("resume requires an acknowledged pause")
        if minutes is not None:
            if type(minutes) is not int or not 1 <= minutes <= 240:
                raise ValueError("minutes must be 1–240")
            state["deadline"] = (now + timedelta(minutes=minutes)).isoformat()
        if max_steps is not None:
            if type(max_steps) is not int or not state["steps_used"] < max_steps <= 100:
                raise ValueError("max steps must exceed consumed steps and be at most 100")
            state["max_steps"] = max_steps
        plan, records, evidence, snap, ph, mh = _fingerprints(root, now=now)
        if not _same_inputs(state, ph, mh):
            state["acceptance"] = {}
            state["plan_fingerprint"], state["memory_fingerprint"] = ph, mh
        state["step"] = None
        _publish(root, state, now=now)
        return save(root, state, now=now)


def evidence_stamp(evidence):
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def accept(root, criterion, reason, *, now):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("human acceptance requires a reason")
    with derive_lock(root):
        state = _required(root)
        if state["state"] not in ("paused", "awaiting_user"):
            raise ValueError("accept goal criteria only while the run is paused")
        plan, records, evidence, snap, ph, mh = _fingerprints(root, now=now)
        if not _same_inputs(state, ph, mh):
            raise ValueError("context changed; resume to reconcile before acceptance")
        if type(criterion) is not int or not 0 <= criterion < len(plan.goal.success_criteria):
            raise ValueError("criterion index is out of range (zero based)")
        state["acceptance"][str(criterion)] = {"reason": journal.sanitize(reason), "at": now.isoformat(),
                                              "evidence_stamp": evidence_stamp(evidence) + workflow.artifact_stamp(root, plan)}
        return save(root, state, now=now)


def drive(root, host, *, worker, now_fn):
    """Run queued steps using an explicitly supplied host object.

    Host implements propose(packet) -> text, and work(packet, control) -> result.
    control() reports live pause/cancel/deadline state. The host must honor those
    requests and its existing permission boundaries. No host is loaded from a
    project path or from plan text. Durable step IDs let the host deduplicate
    work when reconnecting after a crash.
    """
    while True:
        state = tick(root, now=now_fn())
        if state["state"] not in ("queued", "running"):
            return state
        claimed = claim(root, worker, now=now_fn())
        packet = claimed["packet"]
        if claimed["reconcile_existing"]:
            # A host must explicitly reconcile prior work; never rerun blindly.
            if not hasattr(host, "recover"):
                return request(root, "pause", now=now_fn())
            result = host.recover(packet)
        else:
            verdict = check_approach(root, worker, packet["step_id"], host.propose(packet), now=now_fn())
            if verdict["exit_code"]:
                result = {"outcome": "needs_user", "summary": "proposed approach was blocked", "check": verdict}
            else:
                result = host.work(packet, lambda: tick(root, now=now_fn())["state"])
        state = finish(root, worker, packet["step_id"], result, now=now_fn())
        if state["state"] != "queued":
            return state
