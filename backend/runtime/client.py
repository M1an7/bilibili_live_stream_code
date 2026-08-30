from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Callable


MAX_ERROR_BODY = 64 * 1024
MAX_JSON_BODY = 1024 * 1024


class SidecarError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "status": self.status}


class PcmStream(Iterator[bytes]):
    def __init__(self, response, chunk_size: int = 32 * 1024, on_close: Callable[[], None] | None = None):
        self.response = response
        self.chunk_size = chunk_size
        self.sample_rate = int(response.headers.get("X-Sample-Rate", "0"))
        self.channels = int(response.headers.get("X-Channels", "0"))
        self.sample_width = int(response.headers.get("X-Sample-Width", "0"))
        self.first_pcm_ms = int(float(response.headers.get("X-First-Pcm-Ms", "0")))
        self._closed = False
        self._on_close = on_close

    def __iter__(self) -> "PcmStream":
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        try:
            chunk = self.response.read(self.chunk_size)
        except BaseException:
            self.close()
            raise
        if not chunk:
            self.close()
            raise StopIteration
        return chunk

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
            raise ValueError("GPU 侧车只允许回环地址")
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
                "Accept": "application/json, audio/L16",
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
                raise SidecarError("sidecar_unavailable", "GPU 语音运行时连接中断", exc.code) from exc
            raise SidecarError(str(error.get("code") or f"http_{exc.code}"), str(error.get("message") or "GPU 语音运行时请求失败"), exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SidecarError("sidecar_unavailable", "无法连接 GPU 语音运行时") from exc

    def _json(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        response = self._request(method, path, payload, timeout)
        try:
            raw = response.read(MAX_JSON_BODY + 1)
            if len(raw) > MAX_JSON_BODY:
                raise SidecarError("response_too_large", "GPU 运行时响应过大")
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            if isinstance(exc, SidecarError):
                raise
            raise SidecarError("invalid_response", "GPU 运行时返回了无效响应") from exc
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
        timeout: float | None = None,
    ) -> PcmStream:
        response = self._request("POST", "/v1/tts", request, timeout=timeout)
        stream = PcmStream(response, on_close=on_close)
        if stream.sample_rate <= 0 or stream.channels not in (1, 2) or stream.sample_width not in (1, 2, 3, 4):
            stream.close()
            raise SidecarError("invalid_pcm_format", "GPU 运行时返回的 PCM 参数无效")
        return stream

    def cancel(self) -> dict:
        return self._json("POST", "/v1/cancel", {})

    def shutdown(self) -> dict:
        return self._json("POST", "/v1/shutdown", {}, timeout=2.0)
