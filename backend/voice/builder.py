from __future__ import annotations

import json
import shutil
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .manifest import VoiceContractError, VoiceManifest
from .storage import VoiceStoragePaths
from .validator import MAX_FILE_SIZE, VoicePackValidator, VoiceValidationResult, is_link_or_reparse, sha256_file, validate_pcm_wav


class VoiceJobCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceBuildRequest:
    voice_id: str
    display_name: str
    model_version: str
    gpt_path: Path
    sovits_path: Path
    reference_audio_path: Path
    reference_text: str
    license_path: Path
    source_language: str = "ja"
    supported_output_languages: tuple[str, ...] = ("ja",)

    @classmethod
    def from_dict(cls, payload: dict) -> "VoiceBuildRequest":
        if not isinstance(payload, dict):
            raise VoiceContractError("invalid_request", "导入参数格式错误")
        try:
            return cls(
                voice_id=str(payload.get("voice_id", "")).strip(),
                display_name=str(payload.get("display_name", "")).strip(),
                model_version=str(payload.get("model_version", "")).strip(),
                gpt_path=Path(payload.get("gpt_path", "")),
                sovits_path=Path(payload.get("sovits_path", "")),
                reference_audio_path=Path(payload.get("reference_audio_path", "")),
                reference_text=str(payload.get("reference_text", "")).strip(),
                license_path=Path(payload.get("license_path", "")),
                source_language=str(payload.get("source_language", "ja")),
                supported_output_languages=tuple(payload.get("supported_output_languages", ["ja"])),
            )
        except (TypeError, ValueError) as exc:
            raise VoiceContractError("invalid_request", "导入参数格式错误") from exc


@dataclass(frozen=True)
class BuiltVoicePack:
    staging_path: Path
    manifest: VoiceManifest
    validation: VoiceValidationResult


ProgressCallback = Callable[[str, int, str], None]


class VoicePackBuilder:
    def __init__(self, paths: VoiceStoragePaths, validator: VoicePackValidator):
        self.paths = paths
        self.validator = validator

    @staticmethod
    def _notify(progress: ProgressCallback | None, stage: str, percentage: int, message: str) -> None:
        if progress:
            progress(stage, percentage, message)

    @staticmethod
    def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
        if cancelled and cancelled():
            raise VoiceJobCancelled("音色导入已取消")

    @staticmethod
    def _validate_source(path: Path, label: str, suffix: str | None = None) -> None:
        try:
            if not path.is_file():
                raise VoiceContractError("missing_source", f"缺少{label}文件")
            if is_link_or_reparse(path):
                raise VoiceContractError("unsafe_source", f"{label}不能是符号链接或重解析点")
            if suffix and path.suffix.lower() != suffix:
                raise VoiceContractError("invalid_source_type", f"{label}必须是 {suffix} 文件")
            size = path.stat().st_size
            if size <= 0 or size > MAX_FILE_SIZE:
                raise VoiceContractError("invalid_source_size", f"{label}文件为空或超过 2 GiB")
        except OSError as exc:
            raise VoiceContractError("source_unavailable", f"无法读取{label}文件") from exc

    def _copy(self, source: Path, destination: Path, cancelled: Callable[[], bool] | None) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, destination.open("xb") as dst:
            while True:
                self._check_cancelled(cancelled)
                block = src.read(1024 * 1024)
                if not block:
                    break
                dst.write(block)

    def build(
        self,
        request: VoiceBuildRequest,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> BuiltVoicePack:
        self._notify(progress, "prepare", 2, "正在检查导入文件")
        self._check_cancelled(cancelled)
        self._validate_source(request.gpt_path, "GPT 权重", ".ckpt")
        self._validate_source(request.sovits_path, "SoVITS 权重", ".pth")
        self._validate_source(request.reference_audio_path, "参考音频", ".wav")
        self._validate_source(request.license_path, "授权说明")
        if not request.reference_text.strip():
            raise VoiceContractError("missing_reference_text", "参考音频对应日文台词不能为空")
        validate_pcm_wav(request.reference_audio_path)

        self.paths.ensure()
        staging_path = self.paths.staging / uuid.uuid4().hex
        try:
            staging_path.mkdir(parents=False, exist_ok=False)
            self._notify(progress, "copy", 10, "正在复制 GPT 权重")
            self._copy(request.gpt_path, staging_path / "model" / "gpt.ckpt", cancelled)
            self._notify(progress, "copy", 35, "正在复制 SoVITS 权重")
            self._copy(request.sovits_path, staging_path / "model" / "sovits.pth", cancelled)
            self._notify(progress, "copy", 55, "正在复制参考音频和授权说明")
            self._copy(request.reference_audio_path, staging_path / "reference.wav", cancelled)
            self._copy(request.license_path, staging_path / "LICENSE.txt", cancelled)
            (staging_path / "reference.txt").write_text(request.reference_text.strip(), encoding="utf-8")

            self._notify(progress, "hash", 65, "正在计算文件完整性摘要")
            file_names = [
                "model/gpt.ckpt",
                "model/sovits.pth",
                "reference.wav",
                "reference.txt",
                "LICENSE.txt",
            ]
            hashes = {}
            for index, relative in enumerate(file_names):
                self._check_cancelled(cancelled)
                hashes[relative] = sha256_file(staging_path / relative)
                self._notify(progress, "hash", 65 + int((index + 1) / len(file_names) * 20), f"已校验 {relative}")

            created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            payload = {
                "schema_version": 1,
                "voice_id": request.voice_id,
                "display_name": request.display_name,
                "engine": "gpt-sovits-cpu",
                "engine_api_version": 1,
                "model_version": request.model_version,
                "source_language": request.source_language,
                "supported_output_languages": list(request.supported_output_languages),
                "models": {"gpt": "model/gpt.ckpt", "sovits": "model/sovits.pth"},
                "reference_audio": "reference.wav",
                "reference_text": "reference.txt",
                "preview_audio": None,
                "license_file": "LICENSE.txt",
                "usage": ["ai_training", "synthetic_speech", "public_livestream"],
                "created_at": created_at,
                "files": hashes,
            }
            manifest = VoiceManifest.from_dict(payload)
            (staging_path / "manifest.json").write_text(
                json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._check_cancelled(cancelled)
            self._notify(progress, "validate", 90, "正在执行最终结构校验")
            validation = self.validator.validate_directory(staging_path)
            self._notify(progress, "validate", 100, validation.message)
            return BuiltVoicePack(staging_path=staging_path, manifest=manifest, validation=validation)
        except BaseException:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise
