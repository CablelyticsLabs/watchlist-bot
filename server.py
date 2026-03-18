"""
server.py
─────────
Optional local web server that:
  - Serves the dashboard.html at http://localhost:8000
  - Exposes /api/latest  → returns today's picks JSON
  - Exposes /api/run     → triggers a manual analysis run
  - Exposes /api/status  → returns bot status

Run with:
    python server.py
Then open: http://localhost:8000
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

try:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import urllib.parse as urlparse
except ImportError:
    pass

log = logging.getLogger("BotServer")

BOT_STATUS = {
    "running": False,
    "last_run": None,
    "last_run_duration": None,
    "error": None,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.debug("%s - - %s", self.address_string(), format % args)

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str = "text/html"):
        if not path.exists():
            self.send_response(404)
            self.end_headers()
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/dashboard", "/dashboard.html"):
            self.send_file(Path("dashboard.html"), "text/html; charset=utf-8")

        elif path == "/api/latest":
            today = datetime.now().strftime("%Y-%m-%d")
            json_file = Path("output") / f"picks_{today}.json"
            if json_file.exists():
                data = json.loads(json_file.read_text())
                self.send_json({"date": today, "picks": data, "status": "ok"})
            else:
                self.send_json({"error": "No picks for today yet", "status": "pending"}, 404)

        elif path == "/api/status":
            self.send_json({
                "bot": "WatchlistBot",
                "status": "running" if BOT_STATUS["running"] else "idle",
                "last_run": BOT_STATUS["last_run"],
                "last_run_duration": BOT_STATUS["last_run_duration"],
                "error": BOT_STATUS["error"],
            })

        elif path == "/api/run":
            if BOT_STATUS["running"]:
                self.send_json({"error": "Analysis already in progress"}, 409)
                return
            threading.Thread(target=_run_analysis_thread, daemon=True).start()
            self.send_json({"message": "Analysis started", "status": "started"})

        elif path.startswith("/output/"):
            fname = path.lstrip("/")
            self.send_file(Path(fname), "application/json")

        else:
            self.send_response(404)
            self.end_headers()


def _run_analysis_thread():
    BOT_STATUS["running"] = True
    BOT_STATUS["error"] = None
    start = datetime.now()
    try:
        result = subprocess.run(
            ["python", "bot.py", "--now"],
            capture_output=True, text=True, timeout=1800
        )
        if result.returncode != 0:
            BOT_STATUS["error"] = result.stderr[-500:] if result.stderr else "Unknown error"
    except Exception as exc:
        BOT_STATUS["error"] = str(exc)
    finally:
        BOT_STATUS["running"] = False
        BOT_STATUS["last_run"] = datetime.now().isoformat()
        duration = (datetime.now() - start).seconds
        BOT_STATUS["last_run_duration"] = f"{duration // 60}m {duration % 60}s"


def main(host: str = "0.0.0.0", port: int = 8000):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = HTTPServer((host, port), Handler)
    log.info("WatchlistBot Dashboard → http://localhost:%d", port)
    log.info("API endpoints: /api/latest  /api/status  /api/run")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped")


if __name__ == "__main__":
    main()
