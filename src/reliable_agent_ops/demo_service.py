from __future__ import annotations

import json
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer

from .receipts import write_bytes_atomic

DEFAULT_STATE = {"service": "synthetic-agent", "status": "healthy"}


def ensure_state(path: Path) -> None:
    if path.exists():
        return
    write_bytes_atomic(
        path,
        (json.dumps(DEFAULT_STATE, sort_keys=True) + "\n").encode("utf-8"),
    )


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "SyntheticAgent/0.1"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        state_path = Path(os.environ.get("AGENT_STATE_FILE", "/state/health.json"))
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
                raise ValueError("invalid state")
            body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
            status = (
                HTTPStatus.OK
                if payload["status"] == "healthy"
                else HTTPStatus.SERVICE_UNAVAILABLE
            )
        except (OSError, ValueError, json.JSONDecodeError):
            body = b'{"service":"synthetic-agent","status":"state-error"}\n'
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class SyntheticHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # HTTPServer performs a reverse-DNS lookup here. The demo does not need a
        # canonical hostname, so avoid a slow or unavailable external resolver.
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main() -> int:
    state_path = Path(os.environ.get("AGENT_STATE_FILE", "/state/health.json"))
    ensure_state(state_path)
    host = os.environ.get("AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_PORT", "8080"))
    completion_file_value = os.environ.get("AGENT_COMPLETION_FILE")
    completion_file = Path(completion_file_value) if completion_file_value else None
    max_runtime_seconds = int(os.environ.get("AGENT_MAX_RUNTIME_SECONDS", "180"))
    if completion_file is not None:
        completion_file.unlink(missing_ok=True)
    server = SyntheticHTTPServer((host, port), HealthHandler)

    def stop_when_complete() -> None:
        deadline = time.monotonic() + max_runtime_seconds
        while time.monotonic() < deadline:
            if completion_file is not None and completion_file.exists():
                break
            time.sleep(0.2)
        server.shutdown()

    monitor = threading.Thread(target=stop_when_complete, daemon=True)
    monitor.start()
    print(f"synthetic-agent listening on port {port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        monitor.join(timeout=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
