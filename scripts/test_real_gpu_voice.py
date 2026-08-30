from __future__ import annotations

import argparse
import array
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.runtime.client import SidecarClient, SidecarError


def free_port() -> int:
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Real RTX GPT-SoVITS acceptance test")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--voice-directory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--benchmark-output", default="")
    parser.add_argument("--text", default="配信の準備ができたわ。今日もよろしくね。")
    args = parser.parse_args()

    runtime = Path(args.runtime_root).resolve()
    voice = Path(args.voice_directory).resolve()
    python_candidates = [runtime / "python" / "bin" / "python", runtime / "python" / "Scripts" / "python.exe"]
    runtime_python = next((path for path in python_candidates if path.is_file()), None)
    if not runtime_python:
        raise SystemExit("runtime Python was not found")
    sidecar = runtime / "engine" / "sidecar.py"
    metadata = json.loads((voice / "voice.json").read_text("utf-8"))
    model_version = metadata.get("version", "v2Pro")
    gpt = next(voice.glob("*.ckpt"))
    sovits = next(voice.glob("*.pth"))
    reference = voice / "reference.wav"
    token = secrets.token_urlsafe(48)
    port = free_port()
    log_path = runtime / "real-gpu-sidecar.log"
    baseline = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    started = time.perf_counter()
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                str(runtime_python), str(sidecar), "--host", "127.0.0.1", "--port", str(port),
                "--token", token, "--allowed-root", str(voice.parent),
            ],
            cwd=runtime,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "NO_PROXY": "127.0.0.1,localhost"},
        )
    client = SidecarClient("127.0.0.1", port, token, timeout=180, load_timeout=900)
    try:
        deadline = time.monotonic() + 90
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"sidecar exited during startup: {log_path}")
            try:
                health = client.health(timeout=1)
                break
            except SidecarError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("sidecar startup timed out")
                time.sleep(0.1)
        startup_ms = round((time.perf_counter() - started) * 1000)
        load_started = time.perf_counter()
        loaded = client.load_voice({
            "voice_id": metadata.get("name", "haibara-jp").replace("_", "-"),
            "model_version": model_version,
            "gpt_path": str(gpt),
            "sovits_path": str(sovits),
            "reference_audio_path": str(reference),
            "prompt_text": metadata["prompt_text"],
            "prompt_language": metadata.get("prompt_language", "ja"),
        })
        load_ms = round((time.perf_counter() - load_started) * 1000)

        synth_started = time.perf_counter()
        stream = client.synthesize({"text": args.text, "language": "ja", "speed": 1.0})
        first_pcm_ms = round((time.perf_counter() - synth_started) * 1000)
        pcm = b"".join(stream)
        total_ms = round((time.perf_counter() - synth_started) * 1000)
        metrics = audio_metrics(pcm, stream.sample_rate)
        write_wav(Path(args.output), pcm, stream.sample_rate)

        warm_started = time.perf_counter()
        warm_stream = client.synthesize({"text": "短い弾幕のテストよ。", "language": "ja", "speed": 1.0})
        warm_first_pcm_ms = round((time.perf_counter() - warm_started) * 1000)
        warm_pcm = b"".join(warm_stream)
        warm_total_ms = round((time.perf_counter() - warm_started) * 1000)
        warm_metrics = audio_metrics(warm_pcm, warm_stream.sample_rate)
        final_health = client.health()
        benchmark = {
            "result": "passed" if warm_first_pcm_ms <= 3000 else "latency_failed",
            "gpu": health.get("gpu", ""),
            "vram_total_mb": health.get("vram_total_mb", 0),
            "runtime_startup_ms": startup_ms,
            "voice_load_ms": load_ms,
            "reported_warmup_ms": loaded.get("warmup_ms", 0),
            "cold_first_pcm_ms": first_pcm_ms,
            "cold_total_ms": total_ms,
            "warm_first_pcm_ms": warm_first_pcm_ms,
            "warm_total_ms": warm_total_ms,
            "peak_vram_mb": max(loaded.get("peak_vram_mb", 0), final_health.get("vram_mb", 0)),
            "preview": metrics,
            "warm_preview": warm_metrics,
            "output": str(Path(args.output).resolve()),
            "sidecar_pid": process.pid,
            "other_gpu_processes_before": baseline,
        }
        if args.benchmark_output:
            target = Path(args.benchmark_output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(json.dumps(benchmark, ensure_ascii=False, indent=2))
        if benchmark["result"] != "passed":
            raise RuntimeError(f"warm first PCM exceeded 3 seconds: {warm_first_pcm_ms} ms")
    finally:
        try:
            client.shutdown()
        except Exception:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.poll() is None:
        raise RuntimeError("sidecar process was not released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
