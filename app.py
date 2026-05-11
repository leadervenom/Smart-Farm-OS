from __future__ import annotations

import json
import mimetypes
import os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from services.data_store import FarmStore

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def load_env() -> None:
    """Small .env loader so the local prototype runs without extra packages."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()
store = FarmStore()


class SmartFarmHandler(BaseHTTPRequestHandler):
    server_version = "SmartFarmOS/1.0"

    def log_message(self, format: str, *args):
        # Keep terminal clean. Comment this line if you want request logs.
        return

    def _send_json(self, data, status: int = 200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, path: Path):
        if not path.exists() or not path.is_file():
            self.send_error(404, "File not found")
            return
        content = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                self._send_file(TEMPLATES_DIR / "index.html")
            elif path == "/api/state":
                self._send_json(store.next_state())
            elif path == "/api/plants":
                self._send_json({"plants": [p.to_row() for p in store.plants]})
            elif path.startswith("/static/"):
                safe = Path(path.replace("/static/", "", 1))
                if ".." in safe.parts:
                    self.send_error(403)
                    return
                self._send_file(STATIC_DIR / safe)
            else:
                self.send_error(404, "Route not found")
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            data = self._read_json()
            if path == "/api/manual-override":
                self._send_json(store.manual_override(data["target"], data["value"]))
            elif path == "/api/rack-control":
                self._send_json(store.rack_manual_override(data["rack"], data["target"], data["value"]))
            elif path == "/api/optimize-rack":
                self._send_json(store.optimize_rack(data["rack"], bool(data.get("apply", True))))
            elif path == "/api/predictive-scan":
                self._send_json(store.run_predictive_scan())
            elif path == "/api/apply-action":
                self._send_json(store.apply_action(data["action_id"], data.get("alert_id")))
            elif path == "/api/dismiss-alert":
                self._send_json(store.dismiss_alert(data["alert_id"]))
            elif path == "/api/ai-plan":
                self._send_json(store.optimize_all_racks(data.get("alert_id"), source="dashboard_or_alert"))
            elif path == "/api/notify-alert":
                self._send_json(store.notify_alert(data["alert_id"], data.get("recipient")))
            elif path.startswith("/api/plants/"):
                plant_id = path.rsplit("/", 1)[-1]
                self._send_json(store.update_plant(plant_id, data))
            else:
                self.send_error(404, "Route not found")
        except Exception as exc:
            self._send_json({"error": str(exc)}, 400)


def run(host: str = "127.0.0.1", port: int = 5000) -> None:
    server = ThreadingHTTPServer((host, port), SmartFarmHandler)
    print(f"Smart Farm OS running at http://{host}:{port}")
    print("Press CTRL+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    run()
