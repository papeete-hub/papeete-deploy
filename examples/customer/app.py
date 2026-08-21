"""The customer's identity endpoint at GET /, and GET /order — proof of cross-actor discovery.

`/order` calls the waiter BY NAME — `http://waiter:8080/`, the actor's manifest `name` as a DNS
hostname on the product's own Docker network — never an IP, never a registry lookup. That name
resolution is Docker Compose's embedded network DNS, the same mechanism every service on one
Compose project already gets for free; nothing here builds discovery, it only relies on it.
"""
import http.server
import json
import urllib.request
from pathlib import Path

import yaml

MANIFEST = yaml.safe_load(Path(__file__).with_name("actor.yaml").read_text())
WAITER_URL = "http://waiter:8080/"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/order":
            with urllib.request.urlopen(WAITER_URL, timeout=5) as resp:
                waiter = json.loads(resp.read())
            body = {"customer": MANIFEST, "reached": WAITER_URL, "waiter_says": waiter}
        else:
            body = MANIFEST
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass    # keep container logs quiet — the test reads HTTP responses, not stdout


if __name__ == "__main__":
    http.server.HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
