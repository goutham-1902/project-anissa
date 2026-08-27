from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from project.dashboard import compose_dashboard
from project.environment import ProjectEnvironment


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def resolve_static_path(
    request_path: str,
    *,
    static_root: Path,
    private_assets_root: Path,
) -> Path | None:
    is_private_asset = request_path.startswith("/assets/")
    root = private_assets_root if is_private_asset else static_root
    relative = request_path.removeprefix("/assets/") if is_private_asset else (
        "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
    )
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def make_handler(*, static_root: Path, worklog_path: Path, status_path: Path,
                 environment: ProjectEnvironment):
    class DashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send(self, code: int, content: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                self._send(200, b'{"ok":true}\n', "application/json; charset=utf-8")
                return
            if path == "/api/dashboard":
                try:
                    data = compose_dashboard(
                        environment=environment,
                        worklog_path=worklog_path,
                        status_path=status_path,
                    )
                    payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                    self._send(200, payload, "application/json; charset=utf-8")
                except Exception as exc:
                    payload = json.dumps({"error": type(exc).__name__, "message": str(exc)}).encode("utf-8")
                    self._send(500, payload, "application/json; charset=utf-8")
                return
            candidate = resolve_static_path(
                path,
                static_root=static_root,
                private_assets_root=environment.private_assets_root,
            )
            if candidate is None:
                self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            if not candidate.exists() or not candidate.is_file():
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            self._send(200, candidate.read_bytes(), MIME.get(candidate.suffix, "application/octet-stream"))

    return DashboardHandler


def run_server(*, host: str, port: int, static_root: Path, worklog_path: Path,
               status_path: Path, environment: ProjectEnvironment):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("A2A dashboard is Mac-local only; bind to 127.0.0.1")
    handler = make_handler(
        static_root=static_root, worklog_path=worklog_path,
        status_path=status_path, environment=environment,
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.serve_forever()
