from datetime import datetime, timezone
from roadmapify.score import score
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec


def test_no_observations_means_unknown_attribution_not_zero_success():
    plan = Plan(Goal("G"), (PhaseSpec("P-1", "P", 1),), (TaskSpec("T-01", "T", "P-1"),))
    result = score(plan, [], now=datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert result["coverage"] == 0
    assert result["declared_attribution_share"] is None
