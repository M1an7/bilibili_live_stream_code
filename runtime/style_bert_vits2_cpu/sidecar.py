from __future__ import annotations

import argparse
import hmac
import json
import struct
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .protocol import CpuVoiceEngine, ProtocolError
except ImportError:
    from protocol import CpuVoiceEngine, ProtocolError


MAX_REQUEST_BYTES = 1024 * 1024
FRAME_METADATA = 1
FRAME_PCM = 2
FRAME_ERROR = 3
FRAME_END = 4
FRAME_HEADER = struct.Struct(">BI")


def _json(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def create_server(host: str, port: int, token: str, engine: CpuVoiceEngine) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ProtocolError("unsafe_bind", "CPU 侧车只允许绑定 127.0.0.1")
    if not isinstance(token, str) or len(token) < 8:
        raise ProtocolError("invalid_token", "CPU 侧车令牌无效")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, _format, *_args):
            return

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            return hmac.compare_digest(header, f"Bearer {token}")

        def _send(self, status: int, payload: dict) -> None:
            body = _json(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _payload(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ProtocolError("invalid_request", "请求长度无效") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ProtocolError("invalid_request", "请求体为空或过大")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("invalid_request", "请求体不是有效 JSON") from exc
            if not isinstance(payload, dict):
                raise ProtocolError("invalid_request", "请求体必须是 JSON 对象")
            return payload

        def do_GET(self):
            if not self._authorized():
                self._send(401, {"code": "unauthorized", "message": "未授权"})
                return
            if self.path == "/v1/health":
                self._send(200, engine.health())
            else:
                self._send(404, {"code": "not_found", "message": "接口不存在"})

        def do_POST(self):
            if not self._authorized():
                self._send(401, {"code": "unauthorized", "message": "未授权"})
                return
            try:
                payload = self._payload()
                if self.path == "/v1/voices/load":
                    self._send(200, engine.load_voice(payload))
                elif self.path == "/v1/tts":
                    self._tts(payload)
                elif self.path == "/v1/cancel":
                    cancelled = engine.cancel(payload.get("request_id") or None)
                    self._send(200, {"status": "cancelled" if cancelled else "not_active"})
                elif self.path == "/v1/shutdown":
                    self._send(200, {"status": "stopping"})
                    engine.cancel(None)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._send(404, {"code": "not_found", "message": "接口不存在"})
            except ProtocolError as exc:
                self._send(exc.status, {"code": exc.code, "message": exc.message})
            except (BrokenPipeError, ConnectionResetError):
                engine.cancel(None)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self._send(500, {"code": "internal_error", "message": "CPU 侧车内部错误"})

        def _tts(self, payload: dict) -> None:
            started = time.perf_counter()
            chunks = engine.synthesize(payload)
            try:
                sample_rate, first = next(chunks)
            except StopIteration as exc:
                raise ProtocolError("empty_audio", "CPU 音色没有返回音频", 422) from exc
            first_pcm_ms = round((time.perf_counter() - started) * 1000)
            engine.metrics["first_pcm_ms"] = first_pcm_ms
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bililive-pcm-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._frame(FRAME_METADATA, _json({
                "request_id": payload["request_id"],
                "sample_rate": sample_rate,
                "channels": 1,
                "sample_width": 2,
                "first_pcm_ms": first_pcm_ms,
            }))
            self._frame(FRAME_PCM, first)
            try:
                for current_rate, chunk in chunks:
                    if current_rate != sample_rate:
                        raise ProtocolError("sample_rate_changed", "PCM 流采样率发生变化", 422)
                    self._frame(FRAME_PCM, chunk)
            except ProtocolError as exc:
                self._frame(FRAME_ERROR, _json({"code": exc.code, "message": exc.message}))
                return
            except Exception:
                self._frame(FRAME_ERROR, _json({"code": "stream_failed", "message": "CPU 音频流中断"}))
                return
            self._frame(FRAME_END, b"")

        def _frame(self, kind: int, payload: bytes) -> None:
            self.wfile.write(FRAME_HEADER.pack(kind, len(payload)))
            if payload:
                self.wfile.write(payload)
            self.wfile.flush()

    server = ThreadingHTTPServer((host, int(port)), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="BiliLiveTool Style-Bert-VITS2 CPU sidecar")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token-stdin", action="store_true")
    parser.add_argument("--allowed-root", required=True)
    args = parser.parse_args()
    if not args.token_stdin:
        raise SystemExit("token pipe is required")
    token = sys.stdin.readline().strip()
    runtime_root = Path(__file__).resolve().parent.parent
    engine = CpuVoiceEngine(runtime_root, Path(args.allowed_root))
    server = create_server(args.host, args.port, token, engine)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        engine.close_model()
        server.server_close()


if __name__ == "__main__":
    main()
