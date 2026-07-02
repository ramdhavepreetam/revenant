#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from web_app import LocalUIHandler


class BackendAPIHandler(LocalUIHandler):
    server_version = "AIBotBackend/0.1"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def send_static(self, path: str) -> None:
        self.send_json(
            {
                "error": "This is the AIBot backend API. Run ui_app.py for the web UI.",
                "api": "/api",
            },
            HTTPStatus.NOT_FOUND,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local AIBot backend API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), BackendAPIHandler)
    print(f"AIBot backend API running at http://{args.host}:{args.port}")
    print("Use ui_app.py for the browser UI. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AIBot backend.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
