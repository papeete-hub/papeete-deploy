"""The waiter's own identity endpoint — serves its manifest at GET /.

PROVES THE MANIFEST IS READ AT RUNTIME, not duplicated into the image: `actor.yaml` ships beside
this file (the folder-root convention) and is parsed once at startup, not restated here.
"""
import http.server
import json
from pathlib import Path

import yaml

MANIFEST = yaml.safe_load(Path(__file__).with_name("actor.yaml").read_text())


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(MANIFEST).encode())

    def log_message(self, *args):
        pass    # keep container logs quiet — the test reads HTTP responses, not stdout


if __name__ == "__main__":
    http.server.HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
