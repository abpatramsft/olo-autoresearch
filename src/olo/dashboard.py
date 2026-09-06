from __future__ import annotations

import json
import mimetypes
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .frontier import rank_frontier
from .reporting import build_report
from .research import eligible_nodes
from .state import StateStore
from .utils import read_json, tail_text


WEB_DIR = Path(__file__).resolve().parent / "web"


def find_free_port(preferred: int, attempts: int = 30) -> int:
    for offset in range(attempts):
        port = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
            try:
                handle.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f"no free dashboard port in {preferred}..{preferred + attempts - 1}"
    )


def dashboard_state(store: StateStore) -> dict:
    config = store.config()
    graph = store.graph()
    return {
        "status": store.status_summary(),
        "config": config,
        "graph": graph,
        "frontier": rank_frontier(graph, config),
        "annotations": store.annotations()[-100:],
        "learnings": store.learning_context(limit=20),
        "eligible_ids": [node["id"] for node in eligible_nodes(graph)],
        "final_test": read_json(store.state_dir / "final-test/outcome.json", None),
        "proposals": store.proposals()[-100:],
        "events": store.events()[-100:],
        "round_history": (store.meta().get("round_history") or [])[-50:],
    }


def experiment_detail(store: StateStore, exp_id: str) -> dict:
    graph = store.graph()
    node = graph.get("nodes", {}).get(exp_id)
    if node is None:
        raise KeyError(exp_id)
    outcome = store.latest_outcome(exp_id)
    attempt = int(node.get("attempts") or 0)
    attempt_dir = store.state_dir / outcome["artifact_dir"] if outcome and outcome.get("artifact_dir") else store.attempt_dir(exp_id, attempt) if attempt else None
    discarded = store.experiment_dir(exp_id) / "discard/diff.patch"
    records = []
    directory = store.experiment_dir(exp_id)
    for record in sorted([*directory.glob("*/*/outcome.json"), *directory.glob("checks/*/check.json"), *directory.glob("discard/outcome.json")]):
        value = read_json(record, {})
        records.append({
            "path": record.relative_to(store.state_dir).as_posix(), "kind": record.relative_to(directory).parts[0],
            "status": value.get("status"), "score": value.get("score"), "created_at": value.get("created_at"),
            "artifacts": [path.relative_to(store.state_dir).as_posix() for path in sorted(record.parent.rglob("*")) if path.is_file()],
        })
    return {
        "node": node,
        "outcome": outcome,
        "records": records,
        "annotations": [
            item
            for item in store.annotations()
            if item.get("experiment_id") == exp_id
        ],
        "diff": tail_text(discarded) if discarded.exists() else tail_text(attempt_dir / "diff.patch") if attempt_dir else "",
        "benchmark_stdout": (
            tail_text(attempt_dir / "benchmark.stdout.log") if attempt_dir else ""
        ),
        "benchmark_stderr": (
            tail_text(attempt_dir / "benchmark.stderr.log") if attempt_dir else ""
        ),
    }


def make_handler(store: StateStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value) -> None:
            self._send(
                status,
                json.dumps(value, sort_keys=True).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/health":
                    self._json(200, {"ok": True})
                    return
                if path == "/api/state":
                    self._json(200, dashboard_state(store))
                    return
                if path == "/api/measurement":
                    self._json(200, read_json(store.state_dir / "measurement.json", {}))
                    return
                if path in {"/api/report", "/api/report.md"}:
                    report = build_report(store)
                    if path.endswith(".md"):
                        self._send(200, report.encode("utf-8"), "text/markdown; charset=utf-8")
                    else:
                        self._json(200, {"markdown": report, "path": ".olo/report.md", "phase": store.config().get("phase")})
                    return
                if path.startswith("/api/artifact/"):
                    relative = Path(path[len("/api/artifact/"):])
                    artifact = (store.state_dir / relative).resolve()
                    if relative.is_absolute() or not relative.parts or relative.parts[0] not in {"experiments", "final-test"} or not artifact.is_relative_to(store.state_dir.resolve()) or not artifact.is_file() or artifact.suffix not in {".json", ".log", ".patch"}:
                        self._json(404, {"error": "unknown evidence file"})
                        return
                    self._send(200, artifact.read_bytes(), "text/plain; charset=utf-8")
                    return
                if path.startswith("/api/experiment/"):
                    exp_id = path.rsplit("/", 1)[-1]
                    self._json(200, experiment_detail(store, exp_id))
                    return
                if path == "/":
                    asset = WEB_DIR / "index.html"
                else:
                    name = path.lstrip("/")
                    if name not in {"app.js", "style.css", "vendor/marked.esm.js", "vendor/purify.es.mjs", "vendor/file-text.svg", "vendor/download.svg", "vendor/x.svg"}:
                        self._json(404, {"error": "not found"})
                        return
                    asset = WEB_DIR / name
                content_type = "application/javascript; charset=utf-8" if asset.suffix in {".js", ".mjs"} else mimetypes.guess_type(asset.name)[0] or "text/plain"
                self._send(200, asset.read_bytes(), content_type)
            except KeyError:
                self._json(404, {"error": "unknown experiment"})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

    return Handler


def serve_dashboard(store: StateStore, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(store))
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
