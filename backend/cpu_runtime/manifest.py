from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


RUNTIME_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
BANNED_PATH_TOKENS = (
    "onnxruntime-gpu",
    "onnxruntime_gpu",
    "onnxruntime_providers_cuda",
    "onnxruntime_providers_tensorrt",
    "cudnn",
    "cublas",
    "cufft",
    "tensorrt",
)


class CpuRuntimeContractError(ValueError):
    def __init__(self, code: str, message: str, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "field": self.field}


def canonical_cpu_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CpuRuntimeContractError("unsafe_path", f"CPU 运行时路径不安全：{field}", field)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CpuRuntimeContractError("unsafe_path", f"CPU 运行时路径不安全：{field}", field)
    normalized = path.as_posix()
    lowered = normalized.lower()
    if any(token in lowered for token in BANNED_PATH_TOKENS):
        raise CpuRuntimeContractError("gpu_dependency_forbidden", f"CPU 运行时包含 GPU 依赖：{normalized}", field)
    return normalized


@dataclass(frozen=True)
class CpuRuntimeManifest:
    schema_version: int
    runtime_id: str
    engine: str
    engine_api_version: int
    platform: str
    build_version: str
    style_bert_vits2_commit: str
    aivmlib_commit: str
    python_version: str
    onnxruntime_version: str
    supported_architectures: tuple[str, ...]
    supported_languages: tuple[str, ...]
    entrypoint: str
    gpu: bool
    providers: tuple[str, ...]
    default_threads: int
    files: dict[str, str]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CpuRuntimeManifest":
        if not isinstance(payload, Mapping):
            raise CpuRuntimeContractError("invalid_manifest", "CPU 运行时清单必须是 JSON 对象")
        required = set(cls.__dataclass_fields__)
        if set(payload) != required:
            detail = "、".join(sorted(required.symmetric_difference(payload)))
            raise CpuRuntimeContractError("invalid_manifest_fields", f"CPU 运行时清单字段不匹配：{detail}")
        if payload["schema_version"] != 1:
            raise CpuRuntimeContractError("schema_mismatch", "不支持的 CPU 运行时清单版本", "schema_version")
        runtime_id = payload["runtime_id"]
        if not isinstance(runtime_id, str) or not RUNTIME_ID.fullmatch(runtime_id):
            raise CpuRuntimeContractError("invalid_runtime_id", "CPU 运行时 ID 格式无效", "runtime_id")
        if payload["engine"] != "style-bert-vits2-onnx-cpu":
            raise CpuRuntimeContractError("engine_mismatch", "CPU 运行时引擎不受支持", "engine")
        if payload["engine_api_version"] != 1:
            raise CpuRuntimeContractError("engine_api_mismatch", "CPU 运行时接口版本不兼容", "engine_api_version")
        if payload["gpu"] is not False:
            raise CpuRuntimeContractError("gpu_forbidden", "实时 CPU 运行时禁止启用 GPU", "gpu")
        if payload["providers"] != ["CPUExecutionProvider"]:
            raise CpuRuntimeContractError("provider_forbidden", "实时 CPU 运行时只允许 CPUExecutionProvider", "providers")
        if payload["supported_architectures"] != ["Style-Bert-VITS2"]:
            raise CpuRuntimeContractError("architecture_mismatch", "CPU 运行时架构声明无效", "supported_architectures")
        languages = payload["supported_languages"]
        if not isinstance(languages, list) or "zh-CN" not in languages or not all(isinstance(item, str) for item in languages):
            raise CpuRuntimeContractError("language_mismatch", "CPU 运行时必须支持中文 zh-CN", "supported_languages")
        if not isinstance(payload["platform"], str) or not payload["platform"]:
            raise CpuRuntimeContractError("invalid_platform", "CPU 运行时平台无效", "platform")
        if not isinstance(payload["build_version"], str) or not payload["build_version"].strip():
            raise CpuRuntimeContractError("invalid_build_version", "CPU 运行时构建版本无效", "build_version")
        for field in ("style_bert_vits2_commit", "aivmlib_commit"):
            if not isinstance(payload[field], str) or not COMMIT.fullmatch(payload[field]):
                raise CpuRuntimeContractError("invalid_commit", f"固定提交无效：{field}", field)
        for field in ("python_version", "onnxruntime_version"):
            if not isinstance(payload[field], str) or not payload[field].strip():
                raise CpuRuntimeContractError("invalid_manifest", f"CPU 运行时字段无效：{field}", field)
        threads = payload["default_threads"]
        if not isinstance(threads, int) or isinstance(threads, bool) or not 1 <= threads <= 4:
            raise CpuRuntimeContractError("invalid_threads", "CPU 推理线程数必须为 1 到 4", "default_threads")
        entrypoint = safe_relative(payload["entrypoint"], "entrypoint")
        raw_files = payload["files"]
        if not isinstance(raw_files, Mapping) or not raw_files:
            raise CpuRuntimeContractError("invalid_files", "CPU 运行时文件清单不能为空", "files")
        files: dict[str, str] = {}
        for raw_path, digest in raw_files.items():
            path = safe_relative(raw_path, f"files.{raw_path}")
            if not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise CpuRuntimeContractError("invalid_hash", f"CPU 运行时文件哈希无效：{path}", f"files.{path}")
            files[path] = digest
        if entrypoint not in files:
            raise CpuRuntimeContractError("missing_entrypoint", "CPU 运行时入口未列入文件清单", "entrypoint")
        return cls(
            schema_version=1,
            runtime_id=runtime_id,
            engine="style-bert-vits2-onnx-cpu",
            engine_api_version=1,
            platform=payload["platform"],
            build_version=payload["build_version"],
            style_bert_vits2_commit=payload["style_bert_vits2_commit"],
            aivmlib_commit=payload["aivmlib_commit"],
            python_version=payload["python_version"],
            onnxruntime_version=payload["onnxruntime_version"],
            supported_architectures=("Style-Bert-VITS2",),
            supported_languages=tuple(languages),
            entrypoint=entrypoint,
            gpu=False,
            providers=("CPUExecutionProvider",),
            default_threads=threads,
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
            "style_bert_vits2_commit": self.style_bert_vits2_commit,
            "aivmlib_commit": self.aivmlib_commit,
            "python_version": self.python_version,
            "onnxruntime_version": self.onnxruntime_version,
            "supported_architectures": list(self.supported_architectures),
            "supported_languages": list(self.supported_languages),
            "entrypoint": self.entrypoint,
            "gpu": self.gpu,
            "providers": list(self.providers),
            "default_threads": self.default_threads,
            "files": dict(self.files),
        }
