from __future__ import annotations

import argparse
import array
import json
import math
import subprocess
import sys
import time
import wave
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.runtime.client import SidecarClient
from backend.runtime.manager import GpuRuntimeManager
from backend.runtime.manifest import RuntimeManifest
from backend.runtime.registry import RuntimeRecord


def audio_metrics(pcm: bytes, sample_rate: int) -> dict:
    if not pcm or len(pcm) % 2:
        raise RuntimeError("GPU returned empty or misaligned PCM")
    samples = array.array("h")
    samples.frombytes(pcm)
    peak = max(abs(value) for value in samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    if peak < 64 or rms < 16:
        raise RuntimeError("GPU returned silent or near-silent PCM")
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


def gpu_processes() -> dict[int, int]:
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    processes = {}
    for line in result.stdout.splitlines():
        try:
            pid, memory = (part.strip() for part in line.split(",", 1))
            processes[int(pid)] = int(memory)
        except (TypeError, ValueError):
            continue
    return processes


def wait_for_gpu_release(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid not in gpu_processes():
            return True
        time.sleep(0.2)
    return pid not in gpu_processes()


def acceptance_record(runtime: Path) -> RuntimeRecord:
    manifest = RuntimeManifest.from_dict({
        "schema_version": 1,
        "runtime_id": "gpt-sovits-real-acceptance",
        "engine": "gpt-sovits-gpu",
        "engine_api_version": 1,
        "platform": "windows-x86_64",
        "build_version": "local-real-acceptance",
        "gpt_sovits_commit": "a" * 40,
        "python_version": "3.10",
        "torch_version": "2.7.1+cu126",
        "cuda_version": "12.6",
        "supported_model_versions": ["v2Pro", "v2ProPlus"],
        "supported_languages": ["ja"],
        "entrypoint": "engine/sidecar.py",
        "gpu": True,
        "precision": "fp16",
        "minimum_compute_capability": "6.1",
        "minimum_vram_mb": 4096,
        "files": {"engine/sidecar.py": "sha256:" + "0" * 64},
    })
    return RuntimeRecord(runtime, manifest, False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real NVIDIA GPU GPT-SoVITS acceptance test")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--voice-directory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--benchmark-output", default="")
    parser.add_argument("--text", default="配信の準備ができたわ。今日もよろしくね。")
    args = parser.parse_args()

    runtime = Path(args.runtime_root).resolve()
    voice = Path(args.voice_directory).resolve()
    python_candidates = (
        runtime / "python" / "bin" / "python",
        runtime / "python" / "python.exe",
        runtime / "python" / "Scripts" / "python.exe",
    )
    runtime_python = next((path for path in python_candidates if path.is_file()), None)
    if not runtime_python:
        raise SystemExit("runtime Python was not found")
    sidecar = runtime / "engine" / "sidecar.py"
    metadata = json.loads((voice / "manifest.json").read_text("utf-8"))
    voice_id = str(metadata["voice_id"])
    baseline = gpu_processes()

    def command(_record, host, port, _token, allowed_root):
        return [
            str(runtime_python), str(sidecar), "--host", host, "--port", str(port),
            "--token-stdin", "--allowed-root", str(allowed_root),
        ]

    manager = GpuRuntimeManager(
        Path(args.output).resolve().parent / "acceptance-logs",
        voice.parent,
        command_builder=command,
        startup_timeout=90,
        client_factory=lambda host, port, token: SidecarClient(host, port, token, timeout=180, load_timeout=900),
    )
    started = time.perf_counter()
    benchmark = None
    process = None
    sidecar_pid = 0
    try:
        manager.prepare(acceptance_record(runtime))
        process = manager.process
        sidecar_pid = process.pid
        health = manager.client.health()
        startup_ms = round((time.perf_counter() - started) * 1000)

        load_started = time.perf_counter()
        loaded = manager.load_voice({"voice_id": voice_id})
        load_ms = round((time.perf_counter() - load_started) * 1000)

        synth_started = time.perf_counter()
        stream = manager.synthesize(args.text, language="ja", speed=1.0, request_timeout=180)
        first = next(stream)
        first_pcm_ms = round((time.perf_counter() - synth_started) * 1000)
        pcm = first + b"".join(stream)
        total_ms = round((time.perf_counter() - synth_started) * 1000)
        metrics = audio_metrics(pcm, stream.sample_rate)
        write_wav(Path(args.output), pcm, stream.sample_rate)

        warm_started = time.perf_counter()
        warm_stream = manager.synthesize("短い弾幕のテストよ。", language="ja", speed=1.0, request_timeout=180)
        warm_first = next(warm_stream)
        warm_first_pcm_ms = round((time.perf_counter() - warm_started) * 1000)
        warm_pcm = warm_first + b"".join(warm_stream)
        warm_total_ms = round((time.perf_counter() - warm_started) * 1000)
        warm_metrics = audio_metrics(warm_pcm, warm_stream.sample_rate)
        final_health = manager.client.health()
        benchmark = {
            "result": "passed" if warm_first_pcm_ms <= 3000 else "latency_failed",
            "gpu": health.get("gpu", ""),
            "vram_total_mb": health.get("vram_total_mb", 0),
            "runtime_startup_ms": startup_ms,
            "voice_load_ms": load_ms,
            "reported_warmup_ms": loaded.get("warmup_ms", 0),
            "cold_first_pcm_ms": first_pcm_ms,
            "cold_reported_first_pcm_ms": stream.first_pcm_ms,
            "cold_total_ms": total_ms,
            "warm_first_pcm_ms": warm_first_pcm_ms,
            "warm_reported_first_pcm_ms": warm_stream.first_pcm_ms,
            "warm_total_ms": warm_total_ms,
            "peak_vram_mb": max(loaded.get("peak_vram_mb", 0), final_health.get("vram_mb", 0)),
            "preview": metrics,
            "warm_preview": warm_metrics,
            "output": str(Path(args.output).resolve()),
            "sidecar_pid": sidecar_pid,
            "other_gpu_processes_before": baseline,
        }
    finally:
        manager.shutdown()

    process_released = bool(process and process.poll() is not None)
    gpu_released = wait_for_gpu_release(sidecar_pid) if sidecar_pid else False
    if benchmark is None:
        raise RuntimeError("GPU acceptance did not produce a benchmark")
    benchmark["sidecar_process_released"] = process_released
    benchmark["post_shutdown_vram_released"] = gpu_released
    if not process_released or not gpu_released:
        benchmark["result"] = "release_failed"
    if args.benchmark_output:
        target = Path(args.benchmark_output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(benchmark, ensure_ascii=False, indent=2))
    if not process_released:
        raise RuntimeError("sidecar process was not released")
    if not gpu_released:
        raise RuntimeError("sidecar GPU memory was not released")
    if benchmark["result"] != "passed":
        raise RuntimeError(f"warm first PCM exceeded 3 seconds: {benchmark['warm_first_pcm_ms']} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
