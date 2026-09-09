"""Packaged offline goal workspace and optional loopback control server."""
from __future__ import annotations
from dataclasses import asdict
import json
from pathlib import Path
from roadmapify import bridge, execution, journal, workflow, verify, gitsync
from roadmapify.project import project, to_json
from roadmapify.paths import out_path, write_text_atomic


def snapshot(root, *, now, code_graph=None):
    plan, records, evidence, snap = workflow.inputs(root, now=now)
    return {"root": str(Path(root).resolve()), "captured": now.isoformat(),
            "goal": asdict(plan.goal), "phases": [asdict(p) for p in plan.ordered_phases()],
            "tasks": [asdict(t) for t in plan.tasks], "snapshot": asdict(snap),
            "graph": to_json(project(plan, records, journal.rejections(records))),
            "plan_fingerprint": gitsync.plan_fingerprint(plan),
            "memory_fingerprint": workflow.memory_fingerprint(records),
            "verification": asdict(verify.verify(plan, snap, root)), "evidence": evidence,
            "execution": execution.read(root), "code_graph": code_graph,
            "effective_memory": journal.memory_state(records)["records"]}


def to_html(data):
    payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("&", "\\u0026")
    return Path(__file__).with_name("workspace.html").read_text().replace("/* ROADMAP_DATA */ null", payload)


def export(root, *, now, output=None, code_graph=None):
    output = Path(output) if output else out_path("goal.html", root=root)
    write_text_atomic(output, to_html(snapshot(root, now=now, code_graph=code_graph)))
    return output


def serve(root, *, port=0, code_graph=None):
    """Explicit loopback UI. A per-process token gates every API operation.

    Only goal controls are exposed, never arbitrary commands, file paths or
    host result submission. Agent hosts use the CLI/API in their own environment.
    """
    import hmac
    import secrets
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit
    from roadmapify.cli import _now
    from roadmapify.paths import LockBusy
    token = secrets.token_urlsafe(32)
    root = Path(root).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, value, content_type="application/json"):
            body = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            host = self.headers.get("Host", "")
            expected = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            return (host == expected and (not origin or origin == "http://" + expected)
                    and hmac.compare_digest(self.headers.get("X-Roadmap-Token", ""), token))

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                if path == "/":
                    if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                        return self.send(403, {"error": "invalid host"})
                    return self.send(200, to_html(snapshot(root, now=_now(), code_graph=code_graph)), "text/html; charset=utf-8")
                if not self.authorized():
                    return self.send(403, {"error": "local workspace token required"})
                if path == "/api/snapshot":
                    return self.send(200, snapshot(root, now=_now(), code_graph=code_graph))
                return self.send(404, {"error": "not found"})
            except (ValueError, OSError, LockBusy) as exc:
                self.send(400, {"error": str(exc)})

        def do_POST(self):
            if not self.authorized():
                return self.send(403, {"error": "local workspace token required"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 16_384:
                    return self.send(413, {"error": "request too large"})
                args = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(args, dict):
                    raise ValueError("expected an object")
                path = urlsplit(self.path).path
                if path == "/api/start":
                    result = execution.start(root, now=_now(), max_steps=args.get("steps", 3),
                                             minutes=args.get("minutes", 20))
                elif path in ("/api/pause", "/api/cancel"):
                    result = execution.request(root, path.rsplit("/", 1)[1], now=_now())
                elif path == "/api/resume":
                    result = execution.resume(root, now=_now(), max_steps=args.get("steps"), minutes=args.get("minutes"))
                elif path == "/api/tick":
                    result = execution.tick(root, now=_now()) if execution.read(root) else {"state": "idle"}
                else:
                    return self.send(404, {"error": "unknown control"})
                return self.send(200, result)
            except (ValueError, OSError, LockBusy, TypeError) as exc:
                self.send(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/#token={token}"
    return server, url
