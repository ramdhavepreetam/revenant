#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "web"


class StaticUIHandler(SimpleHTTPRequestHandler):
    server_version = "AIBotStaticUI/0.1"
    api_base_url = "http://127.0.0.1:8766"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/config.js":
            return self.send_config()
        if path.startswith("/api/") or path.startswith("/audio/"):
            return self.send_error_json(
                "The UI server is static only. API requests must go to the backend service.",
                HTTPStatus.BAD_GATEWAY,
            )
        return self.send_static(path)

    def send_config(self) -> None:
        payload = f"window.AIBOT_API_BASE_URL = {json.dumps(self.api_base_url)};\n".encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, message: str, status: HTTPStatus) -> None:
        payload = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_static(self, path: str) -> None:
        if path in {"", "/"}:
            file_path = STATIC_DIR / "index.html"
        else:
            requested = path.lstrip("/")
            file_path = (STATIC_DIR / requested).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                return self.send_error_json("Invalid path", HTTPStatus.BAD_REQUEST)
            if not file_path.exists() or not file_path.is_file():
                file_path = STATIC_DIR / "index.html"

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the static AIBot web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8766")
    args = parser.parse_args()

    StaticUIHandler.api_base_url = args.api_base_url.rstrip("/")
    server = ThreadingHTTPServer((args.host, args.port), StaticUIHandler)
    print(f"AIBot static UI running at http://{args.host}:{args.port}")
    print(f"Configured backend API: {StaticUIHandler.api_base_url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AIBot static UI.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
