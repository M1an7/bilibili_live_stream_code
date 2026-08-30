from __future__ import annotations

import array
from pathlib import Path

from backend.runtime.client import SidecarError
from backend.voice.health import VoiceHealthStore


class PersonalizedSpeechService:
    PREVIEW_TEXT = "準備ができました。"

    def __init__(self, paths, voice_registry, runtime_registry, manager, player, health_store: VoiceHealthStore):
        self.paths = paths
        self.voice_registry = voice_registry
        self.runtime_registry = runtime_registry
        self.manager = manager
        self.player = player
        self.health_store = health_store
        self.active_voice_key = ""

    @staticmethod
    def _voice_id(voice_key: str) -> str:
        if not isinstance(voice_key, str) or not voice_key.startswith("pack:") or not voice_key[5:]:
            raise SidecarError("invalid_voice_key", "个性化音色标识无效")
        return voice_key[5:]

    def _resolve(self, voice_key: str):
        voice_id = self._voice_id(voice_key)
        record = self.voice_registry.get(voice_id)
        if not record or not record.manifest:
            raise SidecarError("voice_not_found", "个性化音色不存在")
        runtime = self.runtime_registry.find_compatible(record.manifest.model_version, "ja")
        if not runtime:
            raise SidecarError("runtime_required", "请先安装兼容的 GPU 语音运行时")
        return record, runtime

    def _load(self, voice_record, runtime_record) -> dict:
        manifest = voice_record.manifest
        directory = self.paths.voices / manifest.voice_id
        self.manager.prepare(runtime_record)
        prompt_text = (directory / manifest.reference_text).read_text("utf-8").strip()
        return self.manager.load_voice(
            {
                "voice_id": manifest.voice_id,
                "model_version": manifest.model_version,
                "gpt_path": str((directory / manifest.models["gpt"]).resolve()),
                "sovits_path": str((directory / manifest.models["sovits"]).resolve()),
                "reference_audio_path": str((directory / manifest.reference_audio).resolve()),
                "prompt_text": prompt_text,
                "prompt_language": manifest.source_language,
            }
        )

    @staticmethod
    def _validate_non_silent(pcm: bytes) -> None:
        if len(pcm) < 640 or len(pcm) % 2:
            raise SidecarError("invalid_audio", "GPU 试听音频过短或格式无效")
        samples = array.array("h")
        samples.frombytes(pcm)
        peak = max(abs(value) for value in samples)
        energy = sum(value * value for value in samples) / len(samples)
        if peak < 64 or energy < 256:
            raise SidecarError("silent_audio", "GPU 运行时生成了静音或近似静音音频")

    def prepare(self, voice_key: str, preview_text: str = "") -> dict:
        voice_record, runtime_record = self._resolve(voice_key)
        self._load(voice_record, runtime_record)
        stream = self.manager.synthesize(
            (preview_text or self.PREVIEW_TEXT).strip(),
            language="ja",
            request_timeout=90.0,
        )
        playback = self.player.play(stream, volume=1.0, capture=True)
        self._validate_non_silent(playback.pcm)
        updated = self.health_store.promote_ready(
            self.paths.voices / voice_record.voice_id,
            voice_record.manifest,
            runtime_record,
            playback.pcm,
            stream.sample_rate,
            stream.channels,
            stream.sample_width,
            self.manager.status().get("metrics", {}),
        )
        self.voice_registry.refresh()
        self.active_voice_key = voice_key
        state = self.health_store.get(updated, runtime_record)
        return {"health": state["health"], "message": state["message"], "runtime": self.manager.status()}

    def speak(self, text: str, voice_key: str, volume: float = 1.0, rate: float = 1.0) -> dict:
        if not isinstance(text, str) or not text.strip():
            return {"code": -1, "msg": "播报文本不能为空"}
        voice_record, runtime_record = self._resolve(voice_key)
        if self.active_voice_key != voice_key or self.manager.status().get("state") != "ready":
            self._load(voice_record, runtime_record)
            self.active_voice_key = voice_key
        stream = self.manager.synthesize(text.strip(), language="ja", speed=max(0.5, min(2.0, float(rate))))
        playback = self.player.play(stream, volume=volume)
        return {"code": 0, "msg": "", "data": {"bytes_played": playback.bytes_played, "runtime": self.manager.status()}}

    def stop(self) -> dict:
        self.player.stop()
        self.manager.cancel()
        return {"code": 0}

    def shutdown(self) -> None:
        self.player.stop()
        self.manager.shutdown()
        self.active_voice_key = ""
