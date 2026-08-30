from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


VOICE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SUPPORTED_MODEL_VERSIONS = {"v2Pro", "v2ProPlus"}
EXPECTED_ENGINE = "gpt-sovits-gpu"
SUPPORTED_ENGINES = {EXPECTED_ENGINE, "gpt-sovits-cpu"}
EXPECTED_ENGINE_API_VERSION = 1
REQUIRED_USAGE = {"ai_training", "synthetic_speech", "public_livestream"}


class VoiceContractError(ValueError):
    def __init__(self, code: str, message: str, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def _required_string(payload: dict[str, Any], field: str, label: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise VoiceContractError("invalid_manifest", f"{label}不能为空", field)
    return value.strip()


def _contract_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise VoiceContractError("invalid_path", f"{field} 必须是包内相对路径", field)
    if "\\" in value:
        raise VoiceContractError("invalid_path", f"{field} 必须使用安全的包内相对路径", field)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise VoiceContractError("invalid_path", f"{field} 必须是安全的包内相对路径", field)
    return path.as_posix()


@dataclass(frozen=True)
class VoiceManifest:
    schema_version: int
    voice_id: str
    display_name: str
    engine: str
    engine_api_version: int
    model_version: str
    source_language: str
    supported_output_languages: tuple[str, ...]
    models: dict[str, str]
    reference_audio: str
    reference_text: str
    preview_audio: str | None
    license_file: str
    usage: tuple[str, ...]
    created_at: str
    files: dict[str, str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "VoiceManifest":
        if not isinstance(payload, dict):
            raise VoiceContractError("invalid_manifest", "音色清单必须是 JSON 对象")
        if payload.get("schema_version") != 1:
            raise VoiceContractError("unsupported_schema", "不支持的音色清单版本", "schema_version")

        voice_id = _required_string(payload, "voice_id", "音色 ID")
        if not VOICE_ID_PATTERN.fullmatch(voice_id):
            raise VoiceContractError("invalid_voice_id", "音色 ID 只能包含小写字母、数字和连字符", "voice_id")

        engine = _required_string(payload, "engine", "语音引擎")
        if engine not in SUPPORTED_ENGINES or payload.get("engine_api_version") != EXPECTED_ENGINE_API_VERSION:
            raise VoiceContractError("unsupported_engine", "音色引擎或接口版本不兼容", "engine")

        model_version = _required_string(payload, "model_version", "模型版本")
        if model_version not in SUPPORTED_MODEL_VERSIONS:
            raise VoiceContractError("unsupported_model", "仅支持 v2Pro 与 v2ProPlus 模型版本", "model_version")

        source_language = _required_string(payload, "source_language", "源语言")
        outputs = payload.get("supported_output_languages")
        if not isinstance(outputs, list) or not outputs or not all(isinstance(item, str) and item for item in outputs):
            raise VoiceContractError("invalid_languages", "至少需要一种输出语言", "supported_output_languages")
        if source_language != "ja" or "ja" not in outputs:
            raise VoiceContractError("unsupported_language", "当前音色包必须支持日语输出语言", "supported_output_languages")

        models = payload.get("models")
        if not isinstance(models, dict) or set(models) != {"gpt", "sovits"}:
            raise VoiceContractError("invalid_models", "清单必须同时包含 GPT 与 SoVITS 权重路径", "models")
        normalized_models = {
            "gpt": _contract_path(models["gpt"], "models.gpt"),
            "sovits": _contract_path(models["sovits"], "models.sovits"),
        }

        preview_value = payload.get("preview_audio")
        preview_audio = None if preview_value in (None, "") else _contract_path(preview_value, "preview_audio")

        usage = payload.get("usage")
        if not isinstance(usage, list) or not all(isinstance(item, str) and item for item in usage):
            raise VoiceContractError("invalid_usage", "使用授权声明格式错误", "usage")
        if not REQUIRED_USAGE.issubset(set(usage)):
            raise VoiceContractError("missing_usage", "使用授权必须涵盖训练、合成语音和公开直播", "usage")

        files = payload.get("files")
        if not isinstance(files, dict) or not files:
            raise VoiceContractError("invalid_files", "清单缺少文件哈希", "files")
        normalized_files: dict[str, str] = {}
        for path_value, digest in files.items():
            path = _contract_path(path_value, "files")
            if not isinstance(digest, str) or not HASH_PATTERN.fullmatch(digest):
                raise VoiceContractError("invalid_hash", f"文件 {path} 的 SHA-256 格式错误", "files")
            normalized_files[path] = digest

        manifest = cls(
            schema_version=1,
            voice_id=voice_id,
            display_name=_required_string(payload, "display_name", "显示名称"),
            engine=engine,
            engine_api_version=EXPECTED_ENGINE_API_VERSION,
            model_version=model_version,
            source_language=source_language,
            supported_output_languages=tuple(outputs),
            models=normalized_models,
            reference_audio=_contract_path(payload.get("reference_audio"), "reference_audio"),
            reference_text=_contract_path(payload.get("reference_text"), "reference_text"),
            preview_audio=preview_audio,
            license_file=_contract_path(payload.get("license_file"), "license_file"),
            usage=tuple(usage),
            created_at=_required_string(payload, "created_at", "创建时间"),
            files=normalized_files,
        )

        required_paths = set(manifest.relative_files().values()) - {""}
        if required_paths != set(manifest.files):
            raise VoiceContractError("file_contract_mismatch", "清单路径与文件哈希列表不一致", "files")
        return manifest

    def relative_files(self) -> dict[str, str]:
        result = {
            "gpt": self.models["gpt"],
            "sovits": self.models["sovits"],
            "reference_audio": self.reference_audio,
            "reference_text": self.reference_text,
            "license": self.license_file,
        }
        if self.preview_audio:
            result["preview_audio"] = self.preview_audio
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "voice_id": self.voice_id,
            "display_name": self.display_name,
            "engine": self.engine,
            "engine_api_version": self.engine_api_version,
            "model_version": self.model_version,
            "source_language": self.source_language,
            "supported_output_languages": list(self.supported_output_languages),
            "models": dict(self.models),
            "reference_audio": self.reference_audio,
            "reference_text": self.reference_text,
            "preview_audio": self.preview_audio,
            "license_file": self.license_file,
            "usage": list(self.usage),
            "created_at": self.created_at,
            "files": dict(self.files),
        }
