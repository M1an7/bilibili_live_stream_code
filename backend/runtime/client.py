from __future__ import annotations

import json
import secrets
import struct
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Callable


MAX_ERROR_BODY = 64 * 1024
MAX_JSON_BODY = 1024 * 1024
MAX_FRAME_SIZE = 16 * 1024 * 1024
FRAME_METADATA = 1
FRAME_PCM = 2
FRAME_ERROR = 3
FRAME_END = 4
FRAME_HEADER = struct.Struct(">BI")


class SidecarError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "status": self.status}


class PcmStream(Iterator[bytes]):
    def __init__(
        self,
        response,
        expected_request_id: str,
        on_close: Callable[[], None] | None = None,
        on_error: Callable[[SidecarError], None] | None = None,
    ):
        self.response = response
        self._closed = False
        self._on_close = on_close
        self._on_error = on_error
        kind, payload = self._read_frame()
        if kind != FRAME_METADATA:
            self.close()
            raise SidecarError("invalid_stream", "语音音频流缺少元数据帧")
        try:
            metadata = json.loads(payload.decode("utf-8"))
            self.request_id = str(metadata["request_id"])
            self.sample_rate = int(metadata["sample_rate"])
            self.channels = int(metadata["channels"])
            self.sample_width = int(metadata["sample_width"])
            self.first_pcm_ms = int(metadata["first_pcm_ms"])
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            self.close()
            raise SidecarError("invalid_stream", "语音音频流元数据无效") from exc
        if self.request_id != expected_request_id:
            self.close()
            raise SidecarError("request_mismatch", "语音音频流请求标识不匹配")

    def __iter__(self) -> "PcmStream":
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            kind, payload = self._read_frame()
            if kind == FRAME_PCM:
                if not payload:
                    raise SidecarError("invalid_stream", "语音音频帧为空")
                return payload
            if kind == FRAME_END:
                self.close()
                raise StopIteration
            if kind == FRAME_ERROR:
                try:
                    error = json.loads(payload.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    error = {}
                raise SidecarError(str(error.get("code") or "stream_failed"), str(error.get("message") or "语音音频流中断"))
            raise SidecarError("invalid_stream", "语音音频流包含未知帧")
        except StopIteration:
            raise
        except SidecarError as exc:
            self.close()
            if self._on_error:
                self._on_error(exc)
            raise
        except (OSError, TimeoutError) as exc:
            error = SidecarError("sidecar_unavailable", "语音音频流连接中断")
            self.close()
            if self._on_error:
                self._on_error(error)
            raise error from exc

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            block = self.response.read(size - len(chunks))
            if not block:
                raise SidecarError("stream_truncated", "语音音频流意外中断")
            chunks.extend(block)
        return bytes(chunks)

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(FRAME_HEADER.size)
        kind, size = FRAME_HEADER.unpack(header)
        if size > MAX_FRAME_SIZE:
            raise SidecarError("frame_too_large", "语音音频帧超过大小限制")
        return kind, self._read_exact(size) if size else b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.response.close()
        finally:
            if self._on_close:
                self._on_close()

    def __del__(self):
        self.close()


class SidecarClient:
    def __init__(self, host: str, port: int, token: str, timeout: float = 30.0, load_timeout: float = 600.0):
        if host not in ("127.0.0.1", "::1"):
            raise ValueError("语音侧车只允许回环地址")
        self.base_url = f"http://{host}:{port}"
        self.token = token
        self.timeout = timeout
        self.load_timeout = load_timeout

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, application/x-bililive-pcm-stream",
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(MAX_ERROR_BODY)
            finally:
                exc.close()
            try:
                error = json.loads(body.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                error = {}
            if exc.code in (502, 503, 504) and not error.get("code"):
                raise SidecarError("sidecar_unavailable", "语音运行时连接中断", exc.code) from exc
            raise SidecarError(str(error.get("code") or f"http_{exc.code}"), str(error.get("message") or "语音运行时请求失败"), exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SidecarError("sidecar_unavailable", "无法连接语音运行时") from exc

    def _json(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        response = self._request(method, path, payload, timeout)
        try:
            raw = response.read(MAX_JSON_BODY + 1)
            if len(raw) > MAX_JSON_BODY:
                raise SidecarError("response_too_large", "语音运行时响应过大")
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            if isinstance(exc, SidecarError):
                raise
            raise SidecarError("invalid_response", "语音运行时返回了无效响应") from exc
        finally:
            response.close()

    def health(self, timeout: float = 1.0) -> dict:
        return self._json("GET", "/v1/health", timeout=timeout)

    def load_voice(self, request: dict) -> dict:
        return self._json("POST", "/v1/voices/load", request, timeout=self.load_timeout)

    def synthesize(
        self,
        request: dict,
        on_close: Callable[[], None] | None = None,
        on_error: Callable[[SidecarError], None] | None = None,
        timeout: float | None = None,
    ) -> PcmStream:
        payload = dict(request)
        request_id = payload.get("request_id") or secrets.token_urlsafe(18)
        payload["request_id"] = request_id
        response = self._request("POST", "/v1/tts", payload, timeout=timeout)
        if response.headers.get_content_type() != "application/x-bililive-pcm-stream":
            response.close()
            raise SidecarError("invalid_stream", "语音运行时返回了未知音频协议")
        try:
            stream = PcmStream(response, str(request_id), on_close=on_close, on_error=on_error)
        except SidecarError as exc:
            response.close()
            if on_error:
                on_error(exc)
            raise
        if stream.sample_rate <= 0 or stream.channels not in (1, 2) or stream.sample_width not in (1, 2, 3, 4):
            stream.close()
            raise SidecarError("invalid_pcm_format", "语音运行时返回的 PCM 参数无效")
        return stream

    def cancel(self, request_id: str = "") -> dict:
        return self._json("POST", "/v1/cancel", {"request_id": request_id})

    def shutdown(self) -> dict:
        return self._json("POST", "/v1/shutdown", {}, timeout=2.0)
