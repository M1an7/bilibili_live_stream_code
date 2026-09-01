from __future__ import annotations

import array
import uuid

from backend.aivmx.health import AivmxHealthStore
from backend.runtime.client import SidecarError


class AivmxSpeechService:
    PREVIEW_TEXT = "准备完成，可以开始播报。"

    def __init__(self, paths, voice_registry, runtime_registry, manager, player, health_store: AivmxHealthStore):
        self.paths = paths
        self.voice_registry = voice_registry
        self.runtime_registry = runtime_registry
        self.manager = manager
        self.player = player
        self.health_store = health_store
        self.active_voice_key = ""

    @staticmethod
    def _parse_voice_key(voice_key: str) -> tuple[str, int]:
        if not isinstance(voice_key, str):
            raise SidecarError("invalid_voice_key", "实时 CPU 音色标识无效")
        parts = voice_key.split(":")
        if len(parts) != 3 or parts[0] != "aivmx":
            raise SidecarError("invalid_voice_key", "实时 CPU 音色标识无效")
        try:
            model_uuid = str(uuid.UUID(parts[1]))
            style_id = int(parts[2])
        except (ValueError, TypeError) as exc:
            raise SidecarError("invalid_voice_key", "实时 CPU 音色标识无效") from exc
        if model_uuid != parts[1] or style_id < 0 or str(style_id) != parts[2]:
            raise SidecarError("invalid_voice_key", "实时 CPU 音色标识无效")
        return model_uuid, style_id

    def _resolve(self, voice_key: str, require_ready: bool = False):
        model_uuid, style_id = self._parse_voice_key(voice_key)
        self.voice_registry.refresh()
        record = self.voice_registry.get(model_uuid)
        if not record:
            raise SidecarError("voice_not_found", "实时 CPU 音色不存在")
        if style_id not in {style.style_id for style in record.metadata.styles}:
            raise SidecarError("style_not_found", "AIVMX 风格不存在")
        runtime = self.runtime_registry.find_compatible(record.metadata.architecture, "zh-CN")
        if not runtime:
            raise SidecarError("runtime_required", "请先安装兼容的 CPU 语音运行时")
        if require_ready:
            state = self.health_store.get(record, style_id, runtime)
            if state.get("health") != "ready":
                raise SidecarError("voice_not_ready", "请先用当前 CPU 运行时完成中文音色试听验证")
        return record, style_id, runtime

    def _load(self, record, style_id: int, runtime) -> dict:
        self.manager.prepare(runtime)
        return self.manager.load_voice({"model_uuid": record.metadata.model_uuid, "style_id": style_id})

    @staticmethod
    def _validate_non_silent(pcm: bytes) -> None:
        if len(pcm) < 640 or len(pcm) % 2:
            raise SidecarError("invalid_audio", "CPU 试听音频过短或格式无效")
        samples = array.array("h")
        samples.frombytes(pcm)
        peak = max(abs(value) for value in samples)
        energy = sum(value * value for value in samples) / len(samples)
        if peak < 64 or energy < 256:
            raise SidecarError("silent_audio", "CPU 运行时生成了静音或近似静音音频")

    def prepare(self, voice_key: str, preview_text: str = "") -> dict:
        try:
            record, style_id, runtime = self._resolve(voice_key)
            self._load(record, style_id, runtime)
            text = preview_text.strip() if isinstance(preview_text, str) else ""
            stream = self.manager.synthesize(
                text or self.PREVIEW_TEXT,
                language="zh",
                request_timeout=90.0,
            )
            playback = self.player.play(stream, volume=1.0, capture=True)
            self._validate_non_silent(playback.pcm)
            state = self.health_store.promote_ready(
                record,
                style_id,
                runtime,
                playback.pcm,
                stream.sample_rate,
                stream.channels,
                stream.sample_width,
                self.manager.status().get("metrics", {}),
            )
            self.active_voice_key = voice_key
            return {"health": state["health"], "message": state["message"], "runtime": self.manager.status()}
        except Exception:
            self.shutdown()
            raise

    def preview(self, voice_key: str, preview_text: str = "") -> dict:
        try:
            return self.prepare(voice_key, preview_text)
        finally:
            self.shutdown()

    def speak(self, text: str, voice_key: str, volume: float = 1.0, rate: float = 1.0) -> dict:
        if not isinstance(text, str) or not text.strip():
            return {"code": -1, "msg": "播报文本不能为空"}
        if self.active_voice_key != voice_key or self.manager.status().get("state") != "ready":
            record, style_id, runtime = self._resolve(voice_key, require_ready=True)
            self._load(record, style_id, runtime)
            self.active_voice_key = voice_key
        stream = self.manager.synthesize(
            text.strip(),
            language="zh",
            speed=max(0.5, min(2.0, float(rate))),
        )
        playback = self.player.play(stream, volume=volume)
        return {
            "code": 0,
            "msg": "",
            "data": {"bytes_played": playback.bytes_played, "runtime": self.manager.status()},
        }

    def stop(self) -> dict:
        self.player.stop()
        self.manager.cancel()
        return {"code": 0}

    def shutdown(self) -> None:
        self.player.stop()
        self.manager.shutdown()
        self.active_voice_key = ""
