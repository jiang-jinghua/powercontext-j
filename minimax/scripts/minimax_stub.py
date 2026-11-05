"""Local stand-in for MiniMax's chat endpoint, used to exercise the HTTP client path.

This is a development tool, not a model. Its output is the reviewed reference patch,
so any run produced against it must be labelled as a stub run, never as a MiniMax run.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    patch_path = Path("patches/T-001.patch")
    log_path: Path | None = None

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"path": self.path, "body": body}, ensure_ascii=False) + "\n")
        diff = self.patch_path.read_text(encoding="utf-8").rstrip()
        payload = {
            "id": "stub-request",
            "model": body.get("model", "stub"),
            "choices": [
                {
                    "message": {"role": "assistant", "content": "```diff\n" + diff + "\n```"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 0},
            "base_resp": {"status_code": 0, "status_msg": ""},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--patch", default="patches/T-001.patch")
    parser.add_argument("--log", default="")
    args = parser.parse_args()
    Handler.patch_path = Path(args.patch)
    Handler.log_path = Path(args.log) if args.log else None
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
