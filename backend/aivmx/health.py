from __future__ import annotations

import hashlib
import json
import os
import uuid
import wave
from pathlib import Path

from backend.voice.health import runtime_digest
from backend.voice.storage import VoiceStoragePaths

from .registry import AivmxVoiceRecord


def aivmx_voice_digest(record: AivmxVoiceRecord, style_id: int) -> str:
    payload = {
        "model_uuid": record.metadata.model_uuid,
        "sha256": record.sha256,
        "style_id": int(style_id),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class AivmxHealthStore:
    def __init__(self, paths: VoiceStoragePaths):
        self.paths = paths.ensure()

    def _stem(self, record: AivmxVoiceRecord, style_id: int) -> str:
        return f"aivmx-{record.metadata.model_uuid}-{int(style_id)}"

    def _state_path(self, record: AivmxVoiceRecord, style_id: int) -> Path:
        return self.paths.voice_state / f"{self._stem(record, style_id)}.json"

    def _preview_path(self, record: AivmxVoiceRecord, style_id: int) -> Path:
        return self.paths.voice_state / f"{self._stem(record, style_id)}.preview.wav"

    def get(self, record: AivmxVoiceRecord, style_id: int, runtime_record=None) -> dict:
        if runtime_record is None:
            return {"health": "runtime_required", "message": "音色已导入，等待兼容的 CPU 运行时"}
        try:
            state = json.loads(self._state_path(record, style_id).read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"health": "runtime_required", "message": "兼容 CPU 运行时已就绪，等待 CPU 试听验证"}
        if state.get("voice_digest") != aivmx_voice_digest(record, style_id):
            return {"health": "runtime_required", "message": "AIVMX 音色已变化，需要重新进行 CPU 试听验证"}
        if state.get("runtime_digest") != runtime_digest(runtime_record):
            return {"health": "runtime_required", "message": "CPU 运行时已变化，需要重新进行试听验证"}
        if state.get("health") != "ready" or not self._preview_path(record, style_id).is_file():
            return {"health": "runtime_required", "message": "AIVMX 音色需要重新进行 CPU 试听验证"}
        return dict(state)

    def promote_ready(
        self,
        record: AivmxVoiceRecord,
        style_id: int,
        runtime_record,
        pcm: bytes,
        sample_rate: int,
        channels: int = 1,
        sample_width: int = 2,
        metrics: dict | None = None,
    ) -> dict:
        preview = self._preview_path(record, style_id)
        preview_temp = preview.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with wave.open(str(preview_temp), "wb") as output:
                output.setparams((channels, sample_width, sample_rate, 0, "NONE", ""))
                output.writeframes(pcm)
            os.replace(preview_temp, preview)
        finally:
            preview_temp.unlink(missing_ok=True)

        state = {
            "health": "ready",
            "message": "实时 CPU 音色已通过中文合成与试听验证",
            "voice_digest": aivmx_voice_digest(record, style_id),
            "runtime_digest": runtime_digest(runtime_record),
            "runtime_id": runtime_record.runtime_id,
            "preview_audio": str(preview),
            "metrics": dict(metrics or {}),
        }
        state_path = self._state_path(record, style_id)
        state_temp = state_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            state_temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")
            os.replace(state_temp, state_path)
        finally:
            state_temp.unlink(missing_ok=True)
        return state

    def invalidate(self, record: AivmxVoiceRecord, style_id: int) -> None:
        self._state_path(record, style_id).unlink(missing_ok=True)
        self._preview_path(record, style_id).unlink(missing_ok=True)
