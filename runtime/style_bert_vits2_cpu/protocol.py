from __future__ import annotations

import gc
import hashlib
import io
import json
import os
import stat
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator


MAX_TEXT_LENGTH = 500
MAX_INSTALL_BYTES = 1024 * 1024
BANNED_PROVIDERS = {"CUDAExecutionProvider", "TensorrtExecutionProvider", "DmlExecutionProvider"}


class ProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _cpu_thread_count() -> int:
    try:
        configured = int(os.environ.get("BILILIVE_CPU_THREADS", "4"))
    except ValueError:
        configured = 4
    return max(1, min(4, configured))


def _cpu_session_options(onnxruntime, disable_arena: bool = False):
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = _cpu_thread_count()
    options.inter_op_num_threads = 1
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.log_severity_level = 3
    if disable_arena:
        options.enable_cpu_mem_arena = False
    add_config = getattr(options, "add_session_config_entry", None)
    if add_config:
        add_config("session.intra_op.allow_spinning", "0")
        add_config("session.inter_op.allow_spinning", "0")
    return options


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _default_provider_probe() -> list[str]:
    try:
        import onnxruntime
    except ImportError as exc:
        raise ProtocolError("onnxruntime_missing", "CPU 运行时缺少 ONNX Runtime", 503) from exc
    available = set(onnxruntime.get_available_providers())
    forbidden = sorted(available & BANNED_PROVIDERS)
    if forbidden:
        raise ProtocolError("gpu_provider_forbidden", f"CPU 运行时检测到 GPU Provider：{', '.join(forbidden)}", 503)
    if "CPUExecutionProvider" not in available:
        raise ProtocolError("cpu_provider_missing", "CPU 运行时缺少 CPUExecutionProvider", 503)
    return ["CPUExecutionProvider"]


def _windows_rss_probe() -> dict[str, int]:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
    return {
        "rss_mb": round(counters.WorkingSetSize / 1024**2),
        "peak_rss_mb": round(counters.PeakWorkingSetSize / 1024**2),
    }


def _default_rss_probe(
    platform_name: str | None = None,
    windows_probe: Callable[[], dict[str, int]] | None = None,
) -> dict[str, int]:
    if (platform_name or os.name) == "nt":
        try:
            return (windows_probe or _windows_rss_probe)()
        except (AttributeError, OSError):
            pass
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        scale = 1024 if peak > 1024 * 1024 else 1
        return {"rss_mb": 0, "peak_rss_mb": round(peak * scale / 1024**2)}
    except (ImportError, OSError):
        return {"rss_mb": 0, "peak_rss_mb": 0}


def _has_external_data(model) -> bool:
    from onnx.external_data_helper import uses_external_data

    def graph_uses_external(graph) -> bool:
        if any(uses_external_data(tensor) for tensor in graph.initializer):
            return True
        for sparse in graph.sparse_initializer:
            if uses_external_data(sparse.values) or uses_external_data(sparse.indices):
                return True
        for node in graph.node:
            for attribute in node.attribute:
                if attribute.HasField("t") and uses_external_data(attribute.t):
                    return True
                if any(uses_external_data(tensor) for tensor in attribute.tensors):
                    return True
                if attribute.HasField("sparse_tensor"):
                    sparse = attribute.sparse_tensor
                    if uses_external_data(sparse.values) or uses_external_data(sparse.indices):
                        return True
                for sparse in attribute.sparse_tensors:
                    if uses_external_data(sparse.values) or uses_external_data(sparse.indices):
                        return True
                if attribute.HasField("g") and graph_uses_external(attribute.g):
                    return True
                if any(graph_uses_external(child) for child in attribute.graphs):
                    return True
        return False

    return graph_uses_external(model.graph)


def _default_metadata_reader(path: Path):
    try:
        import aivmlib
    except ImportError as exc:
        raise ProtocolError("aivmlib_missing", "CPU 运行时缺少 aivmlib", 503) from exc
    try:
        with path.open("rb") as stream:
            metadata = aivmlib.read_aivmx_metadata(stream)
    except Exception as exc:
        raise ProtocolError("invalid_aivmx", "AIVMX 元数据校验失败", 422) from exc
    manifest = metadata.manifest
    speakers = list(manifest.speakers)
    if len(speakers) != 1:
        raise ProtocolError("speaker_not_supported", "实时 CPU 音色仅支持单说话人 AIVMX", 422)
    speaker = speakers[0]
    return SimpleNamespace(
        model_uuid=str(manifest.uuid),
        architecture=str(manifest.model_architecture),
        model_format=str(manifest.model_format),
        languages=tuple(speaker.supported_languages),
        styles=tuple(SimpleNamespace(style_id=style.local_id, speaker_id=speaker.local_id, name=style.name) for style in speaker.styles),
        raw=metadata,
    )


class StyleBertVits2ModelAdapter:
    def __init__(self, model, languages):
        self.model = model
        self.languages = languages

    def infer(self, text: str, language: str, speaker_id: int, style_id: int, speed: float) -> tuple[int, bytes]:
        style_names = {value: name for name, value in self.model.style2id.items()}
        if style_id not in style_names:
            raise ProtocolError("style_not_found", "AIVMX 风格不存在", 404)
        sample_rate, audio = self.model.infer(
            text=text,
            language=self.languages.ZH,
            speaker_id=speaker_id,
            style=style_names[style_id],
            length=1.0 / speed,
            line_split=False,
        )
        return int(sample_rate), audio.tobytes()

    def unload(self) -> None:
        self.model.unload()


def _default_model_factory(runtime_root: Path, path: Path, metadata):
    try:
        import numpy as np
        import onnx
        import onnxruntime
        from style_bert_vits2.constants import Languages
        from style_bert_vits2.models.hyper_parameters import HyperParameters
        from style_bert_vits2.nlp import onnx_bert_models
        from style_bert_vits2.tts_model import TTSModel
    except ImportError as exc:
        raise ProtocolError("runtime_dependency_missing", "CPU 运行时依赖不完整", 503) from exc
    try:
        onnx_model = onnx.load_model(str(path), load_external_data=False)
        if _has_external_data(onnx_model):
            raise ProtocolError("external_data_forbidden", "AIVMX 禁止引用外部 ONNX 数据", 422)
        del onnx_model
        raw = metadata.raw
        hyper = HyperParameters.model_validate(raw.hyper_parameters.model_dump())
        if raw.style_vectors is None:
            raise ProtocolError("style_vectors_missing", "AIVMX 缺少风格向量", 422)
        style_vectors = np.load(io.BytesIO(raw.style_vectors), allow_pickle=False)
        providers = [("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})]
        bert_root = runtime_root / "bert" / "chinese-roberta-wwm-ext-large-onnx"
        bert_model = bert_root / "model_fp16.onnx"
        if not bert_model.is_file():
            raise ProtocolError("bert_model_missing", "CPU 运行时缺少中文 ONNX BERT 模型", 503)
        onnx_bert_models.load_tokenizer(Languages.ZH, str(bert_root))
        loaded_bert_models = getattr(onnx_bert_models, "__loaded_models", None)
        if not isinstance(loaded_bert_models, dict):
            raise ProtocolError("runtime_api_mismatch", "CPU 运行时的 Style-Bert-VITS2 接口不兼容", 503)
        bert_session = loaded_bert_models.get(Languages.ZH)
        if bert_session is None:
            bert_session = onnxruntime.InferenceSession(
                str(bert_model),
                sess_options=_cpu_session_options(onnxruntime, disable_arena=True),
                providers=providers,
            )
            loaded_bert_models[Languages.ZH] = bert_session
        if bert_session.get_providers() != ["CPUExecutionProvider"]:
            raise ProtocolError("provider_forbidden", "中文 BERT 未使用纯 CPU Provider", 503)
        model = TTSModel(path, hyper, style_vectors, device="cpu", onnx_providers=providers)
        model.is_onnx_model = True
        model.onnx_session = onnxruntime.InferenceSession(
            str(path),
            sess_options=_cpu_session_options(onnxruntime),
            providers=providers,
        )
        if model.onnx_session is None or model.onnx_session.get_providers() != ["CPUExecutionProvider"]:
            raise ProtocolError("provider_forbidden", "音色网络未使用纯 CPU Provider", 503)
        return StyleBertVits2ModelAdapter(model, Languages)
    except ProtocolError:
        raise
    except Exception as exc:
        raise ProtocolError("model_load_failed", "AIVMX CPU 模型加载失败", 422) from exc


class CpuVoiceEngine:
    def __init__(
        self,
        runtime_root: Path,
        allowed_voice_root: Path,
        metadata_reader: Callable[[Path], object] | None = None,
        model_factory: Callable[[Path, object], object] | None = None,
        provider_probe: Callable[[], list[str]] | None = None,
        rss_probe: Callable[[], dict[str, int]] | None = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.allowed_voice_root = Path(allowed_voice_root).resolve()
        self.metadata_reader = metadata_reader or _default_metadata_reader
        self.model_factory = model_factory or (lambda path, metadata: _default_model_factory(self.runtime_root, path, metadata))
        self.providers = list((provider_probe or _default_provider_probe)())
        if self.providers != ["CPUExecutionProvider"]:
            raise ProtocolError("gpu_provider_forbidden", "实时 CPU 运行时只允许 CPUExecutionProvider", 503)
        self.rss_probe = rss_probe or _default_rss_probe
        self.model = None
        self.voice = None
        self._load_lock = threading.RLock()
        self._synthesis_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.active_request_id = ""
        self.metrics = {"warmup_ms": 0, "first_pcm_ms": 0, "vram_mb": 0, "rss_mb": 0, "peak_rss_mb": 0}

    def _voice_directory(self, model_uuid: str) -> Path:
        try:
            canonical = str(uuid.UUID(model_uuid))
            directory = (self.allowed_voice_root / canonical).resolve(strict=True)
            directory.relative_to(self.allowed_voice_root)
        except (ValueError, OSError) as exc:
            raise ProtocolError("voice_not_found", "AIVMX 音色不存在", 404) from exc
        if canonical != model_uuid or _is_link_or_reparse(directory) or not directory.is_dir():
            raise ProtocolError("unsafe_voice", "AIVMX 音色目录不安全", 403)
        return directory

    def _contract(self, model_uuid: str):
        directory = self._voice_directory(model_uuid)
        install_path = directory / "install.json"
        model_path = directory / "model.aivmx"
        if not install_path.is_file() or _is_link_or_reparse(install_path) or install_path.stat().st_size > MAX_INSTALL_BYTES:
            raise ProtocolError("invalid_install", "AIVMX 安装记录无效", 422)
        if not model_path.is_file() or _is_link_or_reparse(model_path):
            raise ProtocolError("invalid_model", "AIVMX 模型文件无效", 422)
        try:
            install = json.loads(install_path.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("invalid_install", "AIVMX 安装记录损坏", 422) from exc
        if install.get("schema_version") != 1 or install.get("model_uuid") != model_uuid or install.get("model_file") != "model.aivmx":
            raise ProtocolError("invalid_install", "AIVMX 安装记录与目录不一致", 422)
        if install.get("permissions_confirmed") is not True:
            raise ProtocolError("permissions_required", "AIVMX 音色缺少授权确认", 403)
        if install.get("sha256") != _sha256(model_path):
            raise ProtocolError("voice_hash_mismatch", "AIVMX 音色文件已变化", 422)
        metadata = self.metadata_reader(model_path)
        if metadata.model_uuid != model_uuid:
            raise ProtocolError("voice_uuid_mismatch", "AIVMX UUID 与安装目录不一致", 422)
        if metadata.architecture != "Style-Bert-VITS2" or metadata.model_format != "ONNX" or "zh-CN" not in metadata.languages:
            raise ProtocolError("voice_not_supported", "AIVMX 不兼容多语言 CPU 推理", 422)
        return model_path, metadata

    def health(self) -> dict:
        memory = self.rss_probe()
        self.metrics["rss_mb"] = int(memory.get("rss_mb", 0))
        self.metrics["peak_rss_mb"] = max(self.metrics["peak_rss_mb"], int(memory.get("peak_rss_mb", 0)))
        return {
            "status": "ready",
            "providers": list(self.providers),
            "voice_loaded": bool(self.voice),
            "vram_mb": 0,
            **self.metrics,
        }

    def load_voice(self, request: dict) -> dict:
        if not isinstance(request, dict) or set(request) != {"model_uuid", "style_id"}:
            raise ProtocolError("invalid_request", "AIVMX 音色加载参数无效")
        model_uuid = request.get("model_uuid")
        style_id = request.get("style_id")
        if not isinstance(model_uuid, str) or not isinstance(style_id, int) or isinstance(style_id, bool):
            raise ProtocolError("invalid_request", "AIVMX 音色加载参数无效")
        model_path, metadata = self._contract(model_uuid)
        styles = {style.style_id: style for style in metadata.styles}
        if style_id not in styles:
            raise ProtocolError("style_not_found", "AIVMX 风格不存在", 404)
        started = time.perf_counter()
        with self._load_lock:
            self.close_model()
            self.model = self.model_factory(model_path, metadata)
            style = styles[style_id]
            self.voice = {
                "model_uuid": model_uuid,
                "style_id": style_id,
                "speaker_id": style.speaker_id,
            }
            self.metrics["warmup_ms"] = round((time.perf_counter() - started) * 1000)
        health = self.health()
        return {
            "status": "ready",
            "warmup_ms": self.metrics["warmup_ms"],
            "providers": list(self.providers),
            "vram_mb": 0,
            "rss_mb": health["rss_mb"],
            "peak_rss_mb": health["peak_rss_mb"],
        }

    def synthesize(self, request: dict) -> Iterator[tuple[int, bytes]]:
        if self.model is None or self.voice is None:
            raise ProtocolError("voice_not_loaded", "请先加载 AIVMX CPU 音色", 409)
        if not isinstance(request, dict):
            raise ProtocolError("invalid_request", "CPU 合成参数无效")
        request_id = request.get("request_id")
        text = request.get("text")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ProtocolError("invalid_request_id", "CPU 合成请求标识无效")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise ProtocolError("invalid_text", "弹幕文本为空或过长")
        if request.get("language", "zh") != "zh":
            raise ProtocolError("language_not_supported", "实时 CPU 音色只按中文发音合成")
        speed = request.get("speed", 1.0)
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not 0.5 <= float(speed) <= 2.0:
            raise ProtocolError("invalid_speed", "语速必须在 0.5 到 2.0 之间")
        if not self._synthesis_lock.acquire(blocking=False):
            raise ProtocolError("runtime_busy", "CPU 音色正在合成上一条弹幕", 409)
        try:
            with self._request_lock:
                self.active_request_id = request_id
                self._cancel_event.clear()
            sample_rate, pcm = self.model.infer(
                text.strip(),
                "ZH",
                self.voice["speaker_id"],
                self.voice["style_id"],
                float(speed),
            )
            if self._cancel_event.is_set():
                return
            if not isinstance(pcm, (bytes, bytearray)) or len(pcm) < 2 or len(pcm) % 2:
                raise ProtocolError("invalid_audio", "CPU 音色生成的 PCM 无效", 422)
            yield int(sample_rate), bytes(pcm)
            self.health()
        finally:
            with self._request_lock:
                if self.active_request_id == request_id:
                    self.active_request_id = ""
            self._synthesis_lock.release()

    def cancel(self, request_id: str | None = None) -> bool:
        with self._request_lock:
            active = self.active_request_id
            if request_id is not None and request_id != active:
                return False
            if not active:
                return False
            self._cancel_event.set()
            return True

    def close_model(self) -> None:
        model = self.model
        self.model = None
        self.voice = None
        if model is not None:
            try:
                model.unload()
            finally:
                del model
                gc.collect()
