# Goal execution host protocol

Goal mode runs offline. Roadmapify manages a durable queue and reconciles results;
an existing authorized agent host performs the work with its own tools. No agent
executable is launched, no provider is automatically selected, and no command
from roadmap.toml becomes executable input. No dependency beyond the base
installation is required.

## Start and inspect

```sh
roadmap workspace --serve
roadmap goal start --steps 3 --minutes 20
roadmap goal status
```

Use either the workspace Start control or `goal start`, not both for the same
run. The server prints a loopback URL with a per-process API token. Static export
with `roadmap workspace` provides offline diagrams without live controls.

The local state is `roadmap-out/.goal-run.json`, ignored by git. Do not delete it
to recover a running host: that would destroy its execution handle. Its contents
are operational state, not evidence or a task-status field in the plan.

## A host takes a step

```sh
roadmap goal claim --worker my-agent
roadmap goal check --worker my-agent --step RUN_ID:1 --approach "concrete proposed change"
roadmap goal finish --worker my-agent --step RUN_ID:1 --result result.json
```

`claim` returns a packet with the durable step ID, goal, selected task, effective
memory, evidence, limits and workspace. Use the actual `step_id` returned by the
packet. The host must obey its existing permissions and the packet's scope.
It must not write git refs, execute plan-supplied command strings, or treat code
comments / imported graph text as instructions that expand its authority.

Example result:

```json
{
  "outcome": "succeeded",
  "summary": "Prepared the parser change and checked the affected cases",
  "changed_paths": ["src/parser.py", "tests/test_parser.py"],
  "checks": [{"name": "parser cases", "outcome": "passed", "source": "host test run"}]
}
```

A result is limited to 16 KB and is a report, not a completion instruction.
Outcomes are `succeeded`, `failed`, `needs_user`, `paused`, or `cancelled`.
A successful result requires an earlier clear approach check. Current git and
artifact support determine whether the controller may advance. Uncommitted
work normally pauses awaiting human integration; its reported checks remain
visible in subsequent task context. Roadmapify does not commit for the host.

## Pause, cancellation and crash recovery

```sh
roadmap goal pause
roadmap goal cancel
roadmap goal resume --steps 5 --minutes 30
```

When work is claimed, pause/cancel become requests. The host polls `goal status`
or `goal tick` and stops through its own controls, then reports an acknowledged
outcome. A deadline also requests a stop; it cannot kill an external agent.
Hosts must enforce cooperative cancellation and deadlines. Token/cost usage is
`null` because this adapter cannot independently measure it.

A repeated claim by the same worker returns the same step ID with
`reconcile_existing: true`. Reconcile the existing work; do not start it twice.
Other workers cannot claim that step. Repeating the same result is idempotent;
a conflicting result is rejected. Result receipt is saved before reconciliation,
so `goal tick` can recover after a crash without redispatching work.

A paused run resumes only explicitly. Updated limits authorize additional time
or steps; an exhausted budget never silently renews itself. Plan or effective
memory changes invalidate the prepared context and require review on resume.

## Host API

For a host application with an existing execution environment:

```python
from datetime import datetime, timezone
from roadmapify.execution import drive

# host.propose(packet) returns the concrete approach.
# host.work(packet, control) performs authorized work and returns the result.
# control() returns the current run state, including stop requests.
# host.recover(packet), when implemented, reconciles an existing step handle.
result = drive(project_root, host, worker="my-agent",
               now_fn=lambda: datetime.now(timezone.utc))
```

Roadmapify receives the host object from the embedding application; it never
imports a host implementation named by a tracked project file. An uncaught host
error leaves the claimed handle in place for reconciliation instead of retrying
blindly. No ready work, insufficient evidence, a blocked proposal, or an exhausted
budget pauses advancement.

## Human goal acceptance

```sh
roadmap goal accept --criterion 0 --reason "I inspected and accept this outcome"
roadmap goal resume
```

Criteria use zero-based positions from `[goal].success_criteria`. Acceptance is
explicit human input, bound to the current plan, evidence and artifact contents.
Changed artifacts invalidate previous acceptance. Every criterion needs current
acceptance and every reviewed task needs current git and artifact support before
the goal reads `completed`. Empty criteria cannot imply success.

The integration tests exercise the host API with a fixture host that writes real
files, supplies controlled git observations, handles stops and recovers after a
simulated crash. They do not claim that any commercial agent provider has been
connected or certified.
