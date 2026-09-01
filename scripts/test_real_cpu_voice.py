from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
import wave
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.aivmx.protobuf import AivmxMetadataReader
from backend.cpu_runtime.manager import CpuRuntimeManager
from backend.cpu_runtime.registry import CpuRuntimeVerifier
from backend.runtime.client import SidecarClient


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def audio_metrics(pcm: bytes, sample_rate: int) -> dict:
    if not pcm or len(pcm) % 2:
        raise RuntimeError("CPU runtime returned empty or misaligned PCM")
    samples = array.array("h")
    samples.frombytes(pcm)
    peak = max(abs(value) for value in samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    if peak < 64 or rms < 16:
        raise RuntimeError("CPU runtime returned silent or near-silent PCM")
    return {
        "bytes": len(pcm),
        "duration_ms": round(len(samples) / sample_rate * 1000),
        "peak": peak,
        "rms": round(rms, 2),
    }


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", ""))
        output.writeframes(pcm)


def link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real Style-Bert-VITS2 AIVMX CPU acceptance test")
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--aivmx", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--benchmark-output", default="")
    parser.add_argument("--work-root", default="")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--allow-unsigned-development", action="store_true")
    parser.add_argument("--text", default="准备完成，可以开始播报。")
    parser.add_argument("--warm-text", default="欢迎来到直播间，谢谢你的关注。")
    args = parser.parse_args()

    if not 1 <= args.threads <= 4:
        raise SystemExit("--threads must be between 1 and 4")
    runtime = Path(args.runtime).resolve()
    source_model = Path(args.aivmx).resolve()
    output = Path(args.output).resolve()
    verifier = CpuRuntimeVerifier(
        expected_platform="windows-x86_64",
        allow_unsigned=args.allow_unsigned_development,
    )
    record = verifier.verify_directory(runtime)
    if record.manifest.providers != ("CPUExecutionProvider",) or record.manifest.gpu:
        raise RuntimeError("runtime manifest is not CPU-only")

    metadata = AivmxMetadataReader().read(source_model)
    if metadata.architecture != "Style-Bert-VITS2" or metadata.model_format != "ONNX" or "zh-CN" not in metadata.languages:
        raise RuntimeError("AIVMX is not compatible with Chinese Style-Bert-VITS2 ONNX inference")
    if not metadata.styles:
        raise RuntimeError("AIVMX does not contain a style")

    parent = Path(args.work_root).resolve() if args.work_root else output.parent / ".cpu-benchmark-work"
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    voice_root = temporary / "voices"
    voice_directory = voice_root / metadata.model_uuid
    voice_directory.mkdir(parents=True)
    installed_model = voice_directory / "model.aivmx"
    link_or_copy(source_model, installed_model)
    digest = sha256_file(installed_model)
    (voice_directory / "install.json").write_text(json.dumps({
        "schema_version": 1,
        "model_uuid": metadata.model_uuid,
        "model_file": "model.aivmx",
        "sha256": digest,
        "permissions_confirmed": True,
    }, ensure_ascii=False), "utf-8")

    manager = CpuRuntimeManager(
        temporary / "logs",
        voice_root,
        startup_timeout=90,
        client_factory=lambda host, port, token: SidecarClient(host, port, token, timeout=180, load_timeout=600),
        runtime_verifier=verifier,
    )
    benchmark = None
    process = None
    try:
        prepare_started = time.perf_counter()
        manager.prepare(record)
        process = manager.process
        runtime_startup_ms = round((time.perf_counter() - prepare_started) * 1000)

        load_started = time.perf_counter()
        loaded = manager.load_voice({"model_uuid": metadata.model_uuid, "style_id": metadata.styles[0].style_id})
        voice_load_ms = round((time.perf_counter() - load_started) * 1000)

        def synthesize(text: str) -> tuple[bytes, object, int, int]:
            started = time.perf_counter()
            stream = manager.synthesize(text, language="zh", speed=1.0, request_timeout=180)
            first = next(stream)
            first_pcm_ms = round((time.perf_counter() - started) * 1000)
            pcm = first + b"".join(stream)
            total_ms = round((time.perf_counter() - started) * 1000)
            return pcm, stream, first_pcm_ms, total_ms

        cold_pcm, cold_stream, cold_first, cold_total = synthesize(args.text)
        write_wav(output, cold_pcm, cold_stream.sample_rate)
        cold_audio = audio_metrics(cold_pcm, cold_stream.sample_rate)
        warm_pcm, warm_stream, warm_first, warm_total = synthesize(args.warm_text)
        warm_audio = audio_metrics(warm_pcm, warm_stream.sample_rate)
        health = manager.client.health()
        if health.get("providers") != ["CPUExecutionProvider"] or health.get("vram_mb") != 0:
            raise RuntimeError("sidecar violated CPUExecutionProvider / zero-VRAM contract")
        benchmark = {
            "result": "passed" if warm_first <= 3000 else "latency_failed",
            "model_uuid": metadata.model_uuid,
            "model_sha256": digest,
            "threads": args.threads,
            "providers": health["providers"],
            "vram_mb": health["vram_mb"],
            "runtime_startup_ms": runtime_startup_ms,
            "voice_load_ms": voice_load_ms,
            "warmup_ms": loaded.get("warmup_ms", 0),
            "cold_first_pcm_ms": cold_first,
            "cold_reported_first_pcm_ms": cold_stream.first_pcm_ms,
            "cold_total_ms": cold_total,
            "warm_first_pcm_ms": warm_first,
            "warm_reported_first_pcm_ms": warm_stream.first_pcm_ms,
            "warm_total_ms": warm_total,
            "rss_mb": health.get("rss_mb", 0),
            "peak_rss_mb": health.get("peak_rss_mb", 0),
            "cold_audio": cold_audio,
            "warm_audio": warm_audio,
            "output": str(output),
        }
    finally:
        manager.shutdown()
        process_released = bool(process and process.poll() is not None)
        if benchmark is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        else:
            print(json.dumps({"failure_artifacts": str(temporary)}, ensure_ascii=False), file=sys.stderr)

    if benchmark is None:
        raise RuntimeError("CPU acceptance did not produce benchmark data")
    benchmark["sidecar_process_released"] = process_released
    if not process_released:
        benchmark["result"] = "release_failed"
    if args.benchmark_output:
        target = Path(args.benchmark_output).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(benchmark, ensure_ascii=False, indent=2))
    if benchmark["result"] == "latency_failed":
        raise RuntimeError(f"warm first PCM exceeded 3 seconds: {benchmark['warm_first_pcm_ms']} ms")
    if benchmark["result"] != "passed":
        raise RuntimeError("CPU runtime did not release its sidecar process")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
