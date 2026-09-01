from __future__ import annotations

import argparse
import json
import math
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


FRAME_HEADER = struct.Struct(">BI")


def frame(kind: int, payload: bytes = b"") -> bytes:
    return FRAME_HEADER.pack(kind, len(payload)) + payload


def response(handler, status: int, payload: bytes = b"", content_type: str = "application/json") -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token-stdin", action="store_true")
    parser.add_argument("--allowed-root", required=True)
    parser.add_argument("--mode", default="normal")
    args = parser.parse_args()
    if not args.token_stdin:
        raise SystemExit("token pipe is required")
    token = os.sys.stdin.readline().strip()
    if args.mode == "never-ready":
        time.sleep(60)
        return

    providers = ["CPUExecutionProvider"]
    vram_mb = 0
    if args.mode == "gpu-provider":
        providers.append("CUDAExecutionProvider")
    if args.mode == "vram-used":
        vram_mb = 128
    state = {"loaded": False, "server": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {token}"

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if not self.authorized():
                response(self, 401, b'{"code":"unauthorized"}')
                return
            if self.path != "/v1/health":
                response(self, 404, b'{"code":"not_found"}')
                return
            environment_keys = (
                "CUDA_VISIBLE_DEVICES",
                "PYTHONDONTWRITEBYTECODE",
                "HF_HUB_OFFLINE",
                "HF_DATASETS_OFFLINE",
                "TRANSFORMERS_OFFLINE",
                "TOKENIZERS_PARALLELISM",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "ORT_INTRA_OP_NUM_THREADS",
                "ORT_INTER_OP_NUM_THREADS",
                "BILILIVE_CPU_THREADS",
                "BILILIVE_CPU_CACHE_DIR",
            )
            response(self, 200, json.dumps({
                "status": "ready",
                "providers": providers,
                "vram_mb": vram_mb,
                "rss_mb": 1700,
                "peak_rss_mb": 1900,
                "environment": {key: os.environ.get(key, "") for key in environment_keys},
            }).encode())

        def do_POST(self):
            if not self.authorized():
                response(self, 401, b'{"code":"unauthorized"}')
                return
            payload = self.read_json()
            if self.path == "/v1/voices/load":
                marker = os.path.join(args.allowed_root, ".fake-cpu-restarted")
                if args.mode == "crash-load-once" and not os.path.exists(marker):
                    os.makedirs(args.allowed_root, exist_ok=True)
                    open(marker, "wb").close()
                    os._exit(17)
                state["loaded"] = True
                response(self, 200, json.dumps({
                    "status": "ready",
                    "warmup_ms": 51,
                    "providers": providers,
                    "vram_mb": vram_mb,
                    "rss_mb": 1810,
                    "peak_rss_mb": 2060,
                }).encode())
            elif self.path == "/v1/tts":
                if not state["loaded"]:
                    response(self, 409, b'{"code":"voice_not_loaded","message":"load first"}')
                    return
                if payload.get("language") not in ("zh", "zh-CN"):
                    response(self, 422, b'{"code":"language_not_supported","message":"Chinese only"}')
                    return
                pcm = bytearray()
                for index in range(3200):
                    pcm.extend(struct.pack("<h", int(math.sin(index / 12) * 6000)))
                metadata = json.dumps({
                    "request_id": payload["request_id"],
                    "sample_rate": 44100,
                    "channels": 1,
                    "sample_width": 2,
                    "first_pcm_ms": 23,
                }).encode()
                response(self, 200, frame(1, metadata) + frame(2, bytes(pcm)) + frame(4), "application/x-bililive-pcm-stream")
            elif self.path == "/v1/cancel":
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
