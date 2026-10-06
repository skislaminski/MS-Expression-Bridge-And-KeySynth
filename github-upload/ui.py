"""The bridge's interface in the browser: status, learning parameters, assignments per preset,
export and import.

Runs as a small HTTP server in the same process as the bridge and is reachable from this
computer only. The interface itself sends nothing to the pedal; it only changes the assignments.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PAGE = Path(__file__).resolve().parent / "ui.html"
DEFAULT_PORT = 8765
MAX_BODY = 1_000_000


def serve(bridge, settings: dict) -> str:
    """Starts the server in a background thread and returns its address."""
    port = int(settings.get("port", DEFAULT_PORT))
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _reply(self, status: int, body: bytes, content_type: str, filename: str = "") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data) -> None:
            self._reply(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def _local(self) -> bool:
            """Accept requests from our own page only, not from other websites."""
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in allowed and (
                origin is None or origin.split("://")[-1] in allowed)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if not self._local():
                self._json(403, {"error": "Only reachable locally."})
            elif url.path == "/":
                self._reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self._json(200, bridge.state())
            elif url.path == "/api/export":
                self._export(parse_qs(url.query).get("key", [None])[0])
            else:
                self._json(404, {"error": "Not found."})

        def _export(self, key) -> None:
            """All assignments, or those of one preset, as a file to download."""
            reply = bridge.call(lambda: bridge.export_data(key))
            if "error" in reply:
                self._json(400, reply)
                return
            presets = reply["result"]["presets"]
            name = "all-presets" if key is None else f"preset-{next(iter(presets.values()))['display']}"
            body = json.dumps(reply["result"], indent=2, ensure_ascii=False).encode("utf-8")
            self._reply(200, body, "application/json; charset=utf-8", f"expression-bridge-{name}.json")

        def do_POST(self) -> None:
            if not self._local():
                self._json(403, {"error": "Only reachable locally."})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(min(length, MAX_BODY)) or b"{}")
                actions = {
                    "/api/learn": lambda: bridge.learn(str(data.get("action"))),
                    "/api/mapping": lambda: bridge.save_mapping(str(data.get("key")), data),
                    "/api/delete": lambda: bridge.delete_mapping(str(data.get("key"))),
                    "/api/import": lambda: bridge.import_data(data),
                }
                if self.path not in actions or not isinstance(data, dict):
                    self._json(404, {"error": "Not found."})
                    return
                reply = bridge.call(actions[self.path])
                self._json(400 if "error" in reply else 200, reply)
            except ValueError:
                self._json(400, {"error": "Invalid request."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"
