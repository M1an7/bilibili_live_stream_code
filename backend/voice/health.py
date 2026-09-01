from __future__ import annotations

import hashlib
import json
import os
import uuid
import wave
from pathlib import Path

from .manifest import VoiceManifest
from .storage import VoiceStoragePaths
from .validator import sha256_file


def _canonical_digest(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def voice_digest(manifest: VoiceManifest) -> str:
    return _canonical_digest(manifest.to_dict())


def runtime_digest(record) -> str:
    manifest = record.manifest
    if hasattr(manifest, "to_dict"):
        payload = manifest.to_dict()
    else:
        payload = {
            "runtime_id": getattr(record, "runtime_id", ""),
            "build_version": getattr(manifest, "build_version", ""),
        }
    return _canonical_digest(payload)


class VoiceHealthStore:
    def __init__(self, paths: VoiceStoragePaths):
        self.paths = paths.ensure()

    def _path(self, voice_id: str) -> Path:
        return self.paths.voice_state / f"{voice_id}.json"

    def get(self, manifest: VoiceManifest, runtime_record=None) -> dict:
        state_path = self._path(manifest.voice_id)
        try:
            state = json.loads(state_path.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            if runtime_record is not None:
                return {"health": "runtime_required", "message": "兼容 GPU 运行时已就绪，等待 GPU 试听验证"}
            return {"health": "runtime_required", "message": "音色已导入，等待 GPU 运行时准备与试听"}
        if state.get("voice_digest") != voice_digest(manifest):
            return {"health": "runtime_required", "message": "音色文件已变化，需要重新进行 GPU 试听验证"}
        if runtime_record is not None and state.get("runtime_digest") != runtime_digest(runtime_record):
            return {"health": "runtime_required", "message": "GPU 运行时已变化，需要重新进行试听验证"}
        if state.get("health") != "ready":
            return {"health": "runtime_required", "message": "音色需要重新进行 GPU 试听验证"}
        return dict(state)

    def promote_ready(
        self,
        voice_directory: Path,
        manifest: VoiceManifest,
        runtime_record,
        pcm: bytes,
        sample_rate: int,
        channels: int = 1,
        sample_width: int = 2,
        metrics: dict | None = None,
    ) -> VoiceManifest:
        directory = Path(voice_directory)
        preview_temp = directory / f".preview-{uuid.uuid4().hex}.wav"
        preview = directory / "preview.wav"
        try:
            with wave.open(str(preview_temp), "wb") as output:
                output.setparams((channels, sample_width, sample_rate, 0, "NONE", ""))
                output.writeframes(pcm)
            os.replace(preview_temp, preview)
        finally:
            if preview_temp.exists():
                preview_temp.unlink(missing_ok=True)

        payload = manifest.to_dict()
        payload["preview_audio"] = "preview.wav"
        payload["files"]["preview.wav"] = sha256_file(preview)
        updated = VoiceManifest.from_dict(payload)
        manifest_temp = directory / f".manifest-{uuid.uuid4().hex}.json"
        manifest_temp.write_text(json.dumps(updated.to_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.replace(manifest_temp, directory / "manifest.json")
        state = {
            "health": "ready",
            "message": "GPU 音色已通过真实合成与试听验证",
            "voice_digest": voice_digest(updated),
            "runtime_digest": runtime_digest(runtime_record),
            "runtime_id": runtime_record.runtime_id,
            "metrics": dict(metrics or {}),
        }
        state_temp = self._path(updated.voice_id).with_suffix(f".{uuid.uuid4().hex}.tmp")
        state_temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", "utf-8")
        os.replace(state_temp, self._path(updated.voice_id))
        return updated

    def invalidate(self, voice_id: str) -> None:
        self._path(voice_id).unlink(missing_ok=True)
