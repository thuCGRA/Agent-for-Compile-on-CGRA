#!/usr/bin/env python3
"""Offline stand-in for an OpenAI-compatible chat endpoint.

Every POST /v1/chat/completions is answered with the next pins file from the
``--pins`` list (the last one repeats).  Passing two files gives a scripted
recovery: the first answer is rejected by the validator, the second succeeds.

    python3 scripts/mock_replay.py --port 18431 \
        --pins /tmp/bad_pins.json --pins /home/steve/llm-lg-demo/pins_m0.json

The server prints one line per request so the transcript of a demo run shows
exactly how many times the agent was asked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ReplayHandler(BaseHTTPRequestHandler):
    pins_files: list = []
    ii: int = 4
    delay: float = 0.0
    calls = 0
    lock = threading.Lock()

    def log_message(self, fmt, *args):  # keep stderr readable
        sys.stderr.write("[mock] %s\n" % (fmt % args))

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self._send({"ok": True})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        text = json.dumps(req)
        if "NODES" not in text:
            sys.stderr.write("[mock] WARNING: request does not look like a placement prompt\n")

        with ReplayHandler.lock:
            n = ReplayHandler.calls
            ReplayHandler.calls += 1
        idx = min(n, len(self.pins_files) - 1)
        path = self.pins_files[idx]
        with open(path) as f:
            data = json.load(f)
        pins = data.get("pins") if isinstance(data, dict) else data
        content = json.dumps(
            {
                "pins": pins,
                "reasoning": "replay of %s (%d pin(s))" % (os.path.basename(path), len(pins)),
                "ii": int(data.get("ii") or self.ii) if isinstance(data, dict) else self.ii,
            }
        )
        if self.delay:
            time.sleep(self.delay)
        sys.stderr.write(
            "[mock] request %d -> %s (%d pins)\n"
            % (n + 1, os.path.basename(path), len(pins))
        )
        self._send(
            {
                "id": "chatcmpl-mock-%d" % (n + 1),
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req.get("model", "mock"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=18431)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--pins", action="append", required=True, help="repeatable; last repeats")
    ap.add_argument("--ii", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds before each answer")
    args = ap.parse_args()

    files = []
    for p in args.pins:
        if not os.path.isfile(p):
            print("no such pins file: %s" % p, file=sys.stderr)
            return 2
        files.append(os.path.abspath(p))
    ReplayHandler.pins_files = files
    ReplayHandler.ii = args.ii
    ReplayHandler.delay = args.delay

    srv = ThreadingHTTPServer((args.host, args.port), ReplayHandler)
    sys.stderr.write(
        "[mock] listening on http://%s:%d/v1  pins=%s\n"
        % (args.host, args.port, ", ".join(os.path.basename(f) for f in files))
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
