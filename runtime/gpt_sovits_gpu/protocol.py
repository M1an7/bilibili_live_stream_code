from __future__ import annotations

import gc
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator


SUPPORTED_MODELS = frozenset({"v2Pro", "v2ProPlus"})
MAX_TEXT_LENGTH = 500
MAX_PROMPT_LENGTH = 500


class ProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _default_gpu_probe() -> dict:
    try:
        import torch
    except ImportError as exc:
        raise ProtocolError("torch_missing", "GPU 运行时缺少 PyTorch", 503) from exc
    if not torch.cuda.is_available():
        raise ProtocolError("cuda_unavailable", "CUDA 当前不可用", 503)
    properties = torch.cuda.get_device_properties(0)
    capability = torch.cuda.get_device_capability(0)
    if capability < (6, 1):
        raise ProtocolError("gpu_not_supported", "GPU 计算能力低于 6.1", 503)
    return {
        "gpu": properties.name,
        "vram_total_mb": round(properties.total_memory / 1024**2),
        "compute_capability": f"{capability[0]}.{capability[1]}",
        "torch": torch.__version__,
        "cuda": torch.version.cuda or "",
    }


def _default_pipeline_factory(runtime_root: Path, config: dict):
    upstream = runtime_root / "upstream"
    if not (upstream / "GPT_SoVITS" / "TTS_infer_pack" / "TTS.py").is_file():
        raise ProtocolError("upstream_missing", "GPU 运行时缺少固定版本 GPT-SoVITS 源码", 503)
    os.chdir(upstream)
    for value in (str(upstream), str(upstream / "GPT_SoVITS")):
        if value not in sys.path:
            sys.path.insert(0, value)
    try:
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS
    except Exception as exc:
        raise ProtocolError("upstream_import_failed", "GPT-SoVITS 推理模块加载失败", 503) from exc
    return TTS(config)


class GpuVoiceEngine:
    def __init__(
        self,
        runtime_root: Path,
        allowed_voice_root: Path,
        pipeline_factory: Callable[[dict], object] | None = None,
        gpu_probe: Callable[[], dict] | None = None,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.allowed_voice_root = Path(allowed_voice_root).resolve()
        self.pipeline_factory = pipeline_factory or (lambda config: _default_pipeline_factory(self.runtime_root, config))
        self.gpu_info = (gpu_probe or _default_gpu_probe)()
        self.pipeline = None
        self.voice: dict | None = None
        self._load_lock = threading.RLock()
        self._synthesis_lock = threading.Lock()
        self.metrics = {"warmup_ms": 0, "peak_vram_mb": 0, "first_pcm_ms": 0}

    def _allowed_file(self, raw_path, suffix: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path:
            raise ProtocolError("invalid_path", "音色文件路径无效")
        try:
            path = Path(raw_path).resolve(strict=True)
            path.relative_to(self.allowed_voice_root)
        except (OSError, ValueError) as exc:
            raise ProtocolError("path_not_allowed", "音色文件不在授权数据目录内", 403) from exc
        if not path.is_file() or path.suffix.lower() != suffix:
            raise ProtocolError("invalid_file_type", f"音色文件必须是 {suffix}")
        return path

    def health(self) -> dict:
        return {
            "status": "ready",
            **self.gpu_info,
            "voice_loaded": bool(self.voice),
            "vram_mb": self._vram_mb(),
        }

    @staticmethod
    def _validate_voice_id(value) -> str:
        if not isinstance(value, str) or not value or len(value) > 100:
            raise ProtocolError("invalid_voice_id", "音色 ID 无效")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in value):
            raise ProtocolError("invalid_voice_id", "音色 ID 无效")
        return value

    def load_voice(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ProtocolError("invalid_request", "音色加载参数无效")
        voice_id = self._validate_voice_id(request.get("voice_id"))
        model_version = request.get("model_version")
        if model_version not in SUPPORTED_MODELS:
            raise ProtocolError("model_not_supported", "仅支持 v2Pro 与 v2ProPlus 音色")
        prompt_language = request.get("prompt_language")
        if prompt_language != "ja":
            raise ProtocolError("language_not_supported", "参考音频语言必须是日语 ja")
        prompt_text = request.get("prompt_text")
        if not isinstance(prompt_text, str) or not prompt_text.strip() or len(prompt_text) > MAX_PROMPT_LENGTH:
            raise ProtocolError("invalid_prompt", "参考音频对应的日文台词无效")
        gpt = self._allowed_file(request.get("gpt_path"), ".ckpt")
        sovits = self._allowed_file(request.get("sovits_path"), ".pth")
        reference = self._allowed_file(request.get("reference_audio_path"), ".wav")
        pretrained = self.runtime_root / "upstream" / "GPT_SoVITS" / "pretrained_models"
        config = {
            "custom": {
                "device": "cuda",
                "is_half": True,
                "version": model_version,
                "t2s_weights_path": str(gpt),
                "vits_weights_path": str(sovits),
                "bert_base_path": str(pretrained / "chinese-roberta-wwm-ext-large"),
                "cnhuhbert_base_path": str(pretrained / "chinese-hubert-base"),
            }
        }
        started = time.perf_counter()
        with self._load_lock:
            self.close_pipeline()
            try:
                self.pipeline = self.pipeline_factory(config)
            except ProtocolError:
                raise
            except Exception as exc:
                raise self._map_inference_error(exc, "model_load_failed") from exc
            self.voice = {
                "voice_id": voice_id,
                "model_version": model_version,
                "reference_audio_path": str(reference),
                "prompt_text": prompt_text.strip(),
                "prompt_language": "ja",
            }
            self.metrics["warmup_ms"] = round((time.perf_counter() - started) * 1000)
            self.metrics["peak_vram_mb"] = max(self.metrics["peak_vram_mb"], self._peak_vram_mb())
        return {
            "status": "ready",
            "warmup_ms": self.metrics["warmup_ms"],
            "peak_vram_mb": self.metrics["peak_vram_mb"],
        }

    def synthesize(self, request: dict) -> Iterator[tuple[int, bytes]]:
        if not self.pipeline or not self.voice:
            raise ProtocolError("voice_not_loaded", "请先加载个性化音色", 409)
        if not isinstance(request, dict):
            raise ProtocolError("invalid_request", "合成参数无效")
        text = request.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise ProtocolError("invalid_text", "弹幕文本为空或过长")
        if request.get("language", "ja") != "ja":
            raise ProtocolError("language_not_supported", "当前仅支持日语合成")
        speed = request.get("speed", 1.0)
        if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not 0.5 <= float(speed) <= 2.0:
            raise ProtocolError("invalid_speed", "语速必须在 0.5 到 2.0 之间")
        inputs = {
            "text": text.strip(),
            "text_lang": "ja",
            "ref_audio_path": self.voice["reference_audio_path"],
            "prompt_text": self.voice["prompt_text"],
            "prompt_lang": "ja",
            "top_k": 15,
            "top_p": 1.0,
            "temperature": 1.0,
            "text_split_method": "cut5",
            "batch_size": 1,
            "batch_threshold": 0.75,
            "split_bucket": False,
            "speed_factor": float(speed),
            "fragment_interval": 0.08,
            "seed": -1,
            "parallel_infer": True,
            "repetition_penalty": 1.35,
            "return_fragment": True,
            "streaming_mode": False,
        }
        with self._synthesis_lock:
            try:
                for sample_rate, audio in self.pipeline.run(inputs):
                    raw = audio.tobytes() if hasattr(audio, "tobytes") else bytes(audio)
                    if raw:
                        yield int(sample_rate), raw
                self.metrics["peak_vram_mb"] = max(self.metrics["peak_vram_mb"], self._peak_vram_mb())
            except ProtocolError:
                raise
            except Exception as exc:
                raise self._map_inference_error(exc, "synthesis_failed") from exc

    def cancel(self) -> None:
        pipeline = self.pipeline
        if pipeline and hasattr(pipeline, "stop"):
            pipeline.stop()

    def close_pipeline(self) -> None:
        pipeline = self.pipeline
        self.pipeline = None
        self.voice = None
        if pipeline is not None:
            try:
                if hasattr(pipeline, "stop"):
                    pipeline.stop()
            finally:
                del pipeline
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except ImportError:
                    pass

    def _vram_mb(self) -> int:
        try:
            import torch
            return round(torch.cuda.memory_allocated() / 1024**2) if torch.cuda.is_available() else 0
        except ImportError:
            return 0

    def _peak_vram_mb(self) -> int:
        try:
            import torch
            return round(torch.cuda.max_memory_allocated() / 1024**2) if torch.cuda.is_available() else 0
        except ImportError:
            return 0

    @staticmethod
    def _map_inference_error(exc: Exception, default_code: str) -> ProtocolError:
        message = str(exc).lower()
        if "out of memory" in message or "cuda oom" in message:
            return ProtocolError("cuda_out_of_memory", "GPU 显存不足，请关闭其他显存任务后重试", 422)
        if "cuda" in message:
            return ProtocolError("cuda_error", "CUDA 推理失败，请检查显卡驱动和运行时", 422)
        return ProtocolError(default_code, "GPT-SoVITS 推理失败", 422)
