"""Explain coverage and attribution without collapsing uncertainty into a health score."""
from roadmapify.status import normalize_evidence


def score(plan, evidence, *, now):
    known = {t.id for t in plan.tasks if not t.provisional}
    admitted = [r for r in evidence if normalize_evidence(r, known_tasks=known, now=now)]
    covered = {r["task"] for r in admitted}
    declared = sum(r.get("confidence") == "declared" for r in admitted)
    return {"tasks": len(known), "tasks_with_current_evidence": len(covered),
            "coverage": len(covered) / len(known) if known else None,
            "declared_attribution_share": declared / len(admitted) if admitted else None,
            "admitted_records": len(admitted), "excluded_records": len(evidence) - len(admitted),
            "interpretation": "coverage of current observations, not implementation completeness"}
