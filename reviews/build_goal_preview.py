"""Build the standalone design preview from this checkout's real roadmap.

Run from the repository: python3 reviews/build_goal_preview.py
This produces a review artifact, not a new production CLI command.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roadmapify import journal, status
from roadmapify.plan import load_plan, validate_plan, find_cycles
from roadmapify.project import project, to_json


def main():
    plan = load_plan(ROOT)
    errors = validate_plan(plan) + [str(c) for c in find_cycles(plan)]
    if errors:
        raise ValueError("Invalid preview plan: " + "; ".join(errors))
    now = datetime.now(timezone.utc)
    records = journal.load(ROOT)
    snapshot = status.snapshot(plan, status.load_evidence(ROOT), now=now)
    data = {
        "root": str(ROOT), "captured": now.isoformat(),
        "goal": asdict(plan.goal), "phases": [asdict(p) for p in plan.ordered_phases()],
        "tasks": [asdict(t) for t in plan.tasks], "snapshot": asdict(snapshot),
        "graph": to_json(project(plan, records, journal.rejections(records))),
    }
    # Never let a project string terminate the containing script element.
    payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("goal-mode.template.html").read_text()
    result = template.replace("/* ROADMAP_DATA */ null", payload)
    output = Path(__file__).with_name("goal-mode.html")
    output.write_text(result)
    print(output)


if __name__ == "__main__":
    main()
