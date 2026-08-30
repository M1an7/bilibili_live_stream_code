from __future__ import annotations

import argparse
import json
import math
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def response(handler, status, payload=b"", content_type="application/json", headers=None):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    for key, value in (headers or {}).items():
        handler.send_header(key, str(value))
    handler.end_headers()
    handler.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", required=True)
    parser.add_argument("--allowed-root", required=True)
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()
    if args.mode == "never-ready":
        time.sleep(60)
        return

    state = {"loaded": False, "cancelled": False, "server": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def authorized(self):
            return self.headers.get("Authorization") == f"Bearer {args.token}"

        def read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if not self.authorized():
                response(self, 401, b'{"code":"unauthorized"}')
                return
            if self.path == "/v1/health":
                response(self, 200, json.dumps({"status": "ready", "gpu": "fake-rtx", "vram_mb": 768}).encode())
            else:
                response(self, 404, b'{"code":"not_found"}')

        def do_POST(self):
            if not self.authorized():
                response(self, 401, b'{"code":"unauthorized"}')
                return
            payload = self.read_json()
            if self.path == "/v1/voices/load":
                restart_marker = os.path.join(args.allowed_root, ".fake-restarted")
                if args.mode == "crash-load-once" and not os.path.exists(restart_marker):
                    os.makedirs(args.allowed_root, exist_ok=True)
                    open(restart_marker, "wb").close()
                    os._exit(17)
                if payload.get("voice_id") == "cuda-error":
                    response(self, 422, b'{"code":"cuda_out_of_memory","message":"fake cuda oom"}')
                    return
                state["loaded"] = True
                response(self, 200, json.dumps({"status": "ready", "warmup_ms": 12, "peak_vram_mb": 888}).encode())
            elif self.path == "/v1/tts":
                if not state["loaded"]:
                    response(self, 409, b'{"code":"voice_not_loaded","message":"load first"}')
                    return
                frames = bytearray()
                for index in range(1600):
                    frames.extend(struct.pack("<h", int(math.sin(index / 12) * 6000)))
                response(
                    self,
                    200,
                    bytes(frames),
                    "audio/L16",
                    {"X-Sample-Rate": "32000", "X-Channels": "1", "X-Sample-Width": "2", "X-First-Pcm-Ms": "18"},
                )
            elif self.path == "/v1/cancel":
                state["cancelled"] = True
                response(self, 200, b'{"status":"cancelled"}')
            elif self.path == "/v1/shutdown":
                response(self, 200, b'{"status":"stopping"}')
                threading.Thread(target=state["server"].shutdown, daemon=True).start()
            else:
                response(self, 404, b'{"code":"not_found"}')

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    state["server"] = server
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
