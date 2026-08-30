from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


RUNTIME_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SUPPORTED_MODEL_VERSIONS = frozenset({"v2Pro", "v2ProPlus"})
SUPPORTED_LANGUAGES = frozenset({"ja"})


class RuntimeContractError(ValueError):
    def __init__(self, code: str, message: str, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "field": self.field}


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RuntimeContractError("unsafe_path", f"运行时路径不安全：{field}", field)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeContractError("unsafe_path", f"运行时路径不安全：{field}", field)
    return path.as_posix()


def canonical_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class RuntimeManifest:
    schema_version: int
    runtime_id: str
    engine: str
    engine_api_version: int
    platform: str
    build_version: str
    gpt_sovits_commit: str
    python_version: str
    torch_version: str
    cuda_version: str
    supported_model_versions: tuple[str, ...]
    supported_languages: tuple[str, ...]
    entrypoint: str
    gpu: bool
    precision: str
    minimum_compute_capability: str
    minimum_vram_mb: int
    files: dict[str, str]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuntimeManifest":
        if not isinstance(payload, Mapping):
            raise RuntimeContractError("invalid_manifest", "运行时清单必须是 JSON 对象")
        required = set(cls.__dataclass_fields__)
        if set(payload) != required:
            detail = "、".join(sorted(required.symmetric_difference(payload)))
            raise RuntimeContractError("invalid_manifest_fields", f"运行时清单字段不匹配：{detail}")
        if payload["schema_version"] != 1:
            raise RuntimeContractError("schema_mismatch", "不支持的运行时清单版本", "schema_version")
        runtime_id = payload["runtime_id"]
        if not isinstance(runtime_id, str) or not RUNTIME_ID_PATTERN.fullmatch(runtime_id):
            raise RuntimeContractError("invalid_runtime_id", "运行时 ID 格式无效", "runtime_id")
        if payload["engine"] != "gpt-sovits-gpu":
            raise RuntimeContractError("engine_mismatch", "运行时引擎不受支持", "engine")
        if payload["engine_api_version"] != 1:
            raise RuntimeContractError("engine_api_mismatch", "GPU 运行时接口版本不兼容", "engine_api_version")
        if payload["gpu"] is not True:
            raise RuntimeContractError("gpu_required", "该运行时必须启用 GPU", "gpu")
        if payload["precision"] != "fp16":
            raise RuntimeContractError("precision_mismatch", "该运行时必须使用 FP16", "precision")
        if not isinstance(payload["platform"], str) or not payload["platform"]:
            raise RuntimeContractError("invalid_platform", "运行时平台无效", "platform")
        if not isinstance(payload["build_version"], str) or not payload["build_version"].strip():
            raise RuntimeContractError("invalid_build_version", "运行时构建版本无效", "build_version")
        if not isinstance(payload["gpt_sovits_commit"], str) or not COMMIT_PATTERN.fullmatch(payload["gpt_sovits_commit"]):
            raise RuntimeContractError("invalid_commit", "GPT-SoVITS 固定提交无效", "gpt_sovits_commit")
        for field in ("python_version", "torch_version", "cuda_version", "minimum_compute_capability"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise RuntimeContractError("invalid_manifest", f"运行时字段无效：{field}", field)
        minimum_vram_mb = payload["minimum_vram_mb"]
        if not isinstance(minimum_vram_mb, int) or isinstance(minimum_vram_mb, bool) or minimum_vram_mb < 1024:
            raise RuntimeContractError("invalid_vram_requirement", "最低显存要求无效", "minimum_vram_mb")
        model_versions = payload["supported_model_versions"]
        if not isinstance(model_versions, list) or not model_versions or not set(model_versions) <= SUPPORTED_MODEL_VERSIONS:
            raise RuntimeContractError("unsupported_model_versions", "运行时模型版本声明无效", "supported_model_versions")
        languages = payload["supported_languages"]
        if not isinstance(languages, list) or not languages or not set(languages) <= SUPPORTED_LANGUAGES:
            raise RuntimeContractError("unsupported_languages", "运行时语言声明无效", "supported_languages")
        entrypoint = _safe_relative(payload["entrypoint"], "entrypoint")
        raw_files = payload["files"]
        if not isinstance(raw_files, Mapping) or not raw_files:
            raise RuntimeContractError("invalid_files", "运行时文件清单不能为空", "files")
        files: dict[str, str] = {}
        for raw_path, digest in raw_files.items():
            path = _safe_relative(raw_path, f"files.{raw_path}")
            if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                raise RuntimeContractError("invalid_hash", f"文件哈希无效：{path}", f"files.{path}")
            files[path] = digest
        if entrypoint not in files:
            raise RuntimeContractError("missing_entrypoint", "入口文件未列入文件清单", "entrypoint")
        return cls(
            schema_version=1,
            runtime_id=runtime_id,
            engine="gpt-sovits-gpu",
            engine_api_version=1,
            platform=payload["platform"],
            build_version=payload["build_version"],
            gpt_sovits_commit=payload["gpt_sovits_commit"],
            python_version=payload["python_version"],
            torch_version=payload["torch_version"],
            cuda_version=payload["cuda_version"],
            supported_model_versions=tuple(model_versions),
            supported_languages=tuple(languages),
            entrypoint=entrypoint,
            gpu=True,
            precision="fp16",
            minimum_compute_capability=payload["minimum_compute_capability"],
            minimum_vram_mb=minimum_vram_mb,
            files=files,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "runtime_id": self.runtime_id,
            "engine": self.engine,
            "engine_api_version": self.engine_api_version,
            "platform": self.platform,
            "build_version": self.build_version,
            "gpt_sovits_commit": self.gpt_sovits_commit,
            "python_version": self.python_version,
            "torch_version": self.torch_version,
            "cuda_version": self.cuda_version,
            "supported_model_versions": list(self.supported_model_versions),
            "supported_languages": list(self.supported_languages),
            "entrypoint": self.entrypoint,
            "gpu": self.gpu,
            "precision": self.precision,
            "minimum_compute_capability": self.minimum_compute_capability,
            "minimum_vram_mb": self.minimum_vram_mb,
            "files": dict(self.files),
        }
