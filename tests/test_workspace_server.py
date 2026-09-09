"""Transport tests exercise the real local API and its mutation boundary."""
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from roadmapify import workspace
from roadmapify.plan import Goal, PhaseSpec, Plan, TaskSpec, emit_plan


def test_local_control_requires_token_and_matching_origin(tmp_path):
    p = Plan(Goal("Goal"), (PhaseSpec("P-1", "Phase", 1),), (TaskSpec("T-01", "Task", "P-1"),))
    (tmp_path / "roadmap.toml").write_text(emit_plan(p))
    server, url = workspace.serve(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin, token = url.split('/#token=')
    def post(headers):
        request = Request(origin + '/api/start', data=b'{"steps":1,"minutes":1}',
                          headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)
    try:
        assert post({})[0] == 403
        assert post({"X-Roadmap-Token": token, "Origin": "https://unrelated.example"})[0] == 403
        code, result = post({"X-Roadmap-Token": token, "Origin": origin})
        assert code == 200 and result["state"] == "queued"
        assert post({"X-Roadmap-Token": token})[0] == 400
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
