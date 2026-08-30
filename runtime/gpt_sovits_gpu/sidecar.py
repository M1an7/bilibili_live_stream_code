from __future__ import annotations

import argparse
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .protocol import GpuVoiceEngine, ProtocolError
except ImportError:
    from protocol import GpuVoiceEngine, ProtocolError


MAX_REQUEST_BYTES = 1024 * 1024


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def create_server(host: str, port: int, token: str, engine: GpuVoiceEngine) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ProtocolError("unsafe_bind", "GPU 侧车只允许绑定 127.0.0.1")
    if not isinstance(token, str) or len(token) < 8:
        raise ProtocolError("invalid_token", "GPU 侧车令牌无效")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, _format, *_args):
            return

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied, f"Bearer {token}")

        def _send(self, status: int, payload: dict) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ProtocolError("invalid_request", "请求长度无效") from exc
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ProtocolError("request_too_large", "GPU 侧车请求过大", 413)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("invalid_json", "请求不是有效的 UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise ProtocolError("invalid_request", "请求必须是 JSON 对象")
            return payload

        def _guard(self) -> bool:
            if self._authorized():
                return True
            self._send(401, {"code": "unauthorized", "message": "无权访问 GPU 侧车"})
            return False

        def do_GET(self):
            if not self._guard():
                return
            if self.path != "/v1/health":
                self._send(404, {"code": "not_found", "message": "接口不存在"})
                return
            self._send(200, engine.health())

        def do_POST(self):
            if not self._guard():
                return
            try:
                payload = self._read()
                if self.path == "/v1/voices/load":
                    self._send(200, engine.load_voice(payload))
                elif self.path == "/v1/tts":
                    self._tts(payload)
                elif self.path == "/v1/cancel":
                    engine.cancel()
                    self._send(200, {"status": "cancelled"})
                elif self.path == "/v1/shutdown":
                    self._send(200, {"status": "stopping"})
                    engine.cancel()
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._send(404, {"code": "not_found", "message": "接口不存在"})
            except ProtocolError as exc:
                self._send(exc.status, {"code": exc.code, "message": exc.message})
            except (BrokenPipeError, ConnectionResetError):
                engine.cancel()
            except Exception:
                self._send(500, {"code": "internal_error", "message": "GPU 侧车内部错误"})

        def _tts(self, payload: dict) -> None:
            started = time.perf_counter()
            chunks = engine.synthesize(payload)
            try:
                sample_rate, first = next(chunks)
            except StopIteration as exc:
                raise ProtocolError("empty_audio", "GPT-SoVITS 没有返回音频", 422) from exc
            first_pcm_ms = round((time.perf_counter() - started) * 1000)
            engine.metrics["first_pcm_ms"] = first_pcm_ms
            self.send_response(200)
            self.send_header("Content-Type", "audio/L16")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Sample-Rate", str(sample_rate))
            self.send_header("X-Channels", "1")
            self.send_header("X-Sample-Width", "2")
            self.send_header("X-First-Pcm-Ms", str(first_pcm_ms))
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            for current_rate, chunk in chunks:
                if current_rate != sample_rate:
                    raise ProtocolError("sample_rate_changed", "PCM 流采样率发生变化", 422)
                self.wfile.write(chunk)
                self.wfile.flush()

    return ThreadingHTTPServer((host, int(port)), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="BiliLiveTool GPT-SoVITS GPU sidecar")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token", required=True)
    parser.add_argument("--allowed-root", required=True)
    args = parser.parse_args()
    runtime_root = Path(__file__).resolve().parent.parent
    engine = GpuVoiceEngine(runtime_root, Path(args.allowed_root))
    server = create_server(args.host, args.port, args.token, engine)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        engine.close_pipeline()
        server.server_close()


if __name__ == "__main__":
    main()
