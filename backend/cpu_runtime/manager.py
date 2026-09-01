from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from backend.runtime.client import PcmStream, SidecarClient, SidecarError

from .registry import CpuRuntimeRecord


CommandBuilder = Callable[[CpuRuntimeRecord, str, int, str, Path], list[str]]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _default_command(record: CpuRuntimeRecord, host: str, port: int, token: str, allowed_root: Path) -> list[str]:
    python = record.path / "python" / "python.exe"
    if not python.is_file():
        python = record.path / "python" / "Scripts" / "python.exe"
    return [
        str(python),
        str(record.path / record.manifest.entrypoint),
        "--host", host,
        "--port", str(port),
        "--token-stdin",
        "--allowed-root", str(allowed_root),
    ]


class CpuRuntimeManager:
    STATES = frozenset({"missing", "stopped", "starting", "ready", "busy", "stopping", "failed"})

    def __init__(
        self,
        log_directory: Path,
        allowed_voice_root: Path,
        command_builder: CommandBuilder | None = None,
        startup_timeout: float = 45.0,
        client_factory=SidecarClient,
        runtime_verifier=None,
    ):
        self.log_directory = Path(log_directory)
        self.allowed_voice_root = Path(allowed_voice_root)
        self.command_builder = command_builder or _default_command
        self.startup_timeout = startup_timeout
        self.client_factory = client_factory
        self.runtime_verifier = runtime_verifier
        self._lock = threading.RLock()
        self._state = "stopped"
        self._error: dict | None = None
        self._record: CpuRuntimeRecord | None = None
        self.process: subprocess.Popen | None = None
        self.client: SidecarClient | None = None
        self.port = 0
        self.token = ""
        self._active_request_id = ""
        self.log_path = self.log_directory / "cpu-sidecar.log"
        self._log_stream = None
        self.metrics = {
            "first_pcm_ms": 0,
            "warmup_ms": 0,
            "rss_mb": 0,
            "peak_rss_mb": 0,
            "vram_mb": 0,
            "providers": [],
            "restart_count": 0,
        }

    def status(self) -> dict:
        with self._lock:
            process_running = bool(self.process and self.process.poll() is None)
            return {
                "state": self._state,
                "runtime_id": self._record.runtime_id if self._record else "",
                "process_running": process_running,
                "error": dict(self._error) if self._error else None,
                "metrics": {**self.metrics, "providers": list(self.metrics["providers"])},
            }

    @staticmethod
    def _validate_health(health: dict) -> None:
        if health.get("providers") != ["CPUExecutionProvider"] or health.get("vram_mb") != 0:
            raise SidecarError("cpu_contract_failed", "CPU 语音运行时未满足零显存约束")

    def _update_metrics(self, payload: dict) -> None:
        if isinstance(payload.get("warmup_ms"), (int, float)):
            self.metrics["warmup_ms"] = int(payload["warmup_ms"])
        if isinstance(payload.get("rss_mb"), (int, float)):
            self.metrics["rss_mb"] = int(payload["rss_mb"])
        if isinstance(payload.get("peak_rss_mb"), (int, float)):
            self.metrics["peak_rss_mb"] = max(self.metrics["peak_rss_mb"], int(payload["peak_rss_mb"]))
        self.metrics["vram_mb"] = int(payload.get("vram_mb", 0))
        if isinstance(payload.get("providers"), list):
            self.metrics["providers"] = list(payload["providers"])

    def prepare(self, record: CpuRuntimeRecord) -> dict:
        if self.runtime_verifier is not None:
            verified = self.runtime_verifier.verify_directory(record.path)
            if verified.manifest != record.manifest or verified.runtime_id != record.runtime_id:
                raise SidecarError("runtime_changed", "CPU 语音运行时已变化，请刷新后重试")
            record = verified
        with self._lock:
            if self.process and self.process.poll() is None and self._record == record:
                if self._state == "ready":
                    return self.status()
                if self._state == "busy":
                    raise SidecarError("runtime_busy", "CPU 语音运行时正在处理其他请求")
            self._shutdown_locked(graceful=True)
            self._record = record
            self._state = "starting"
            self._error = None
            self.port = _free_loopback_port()
            self.token = secrets.token_urlsafe(48)
            self.log_directory.mkdir(parents=True, exist_ok=True)
            runtime_cache = (self.log_directory / "cpu-runtime-cache").resolve()
            runtime_cache.mkdir(parents=True, exist_ok=True)
            self.allowed_voice_root.mkdir(parents=True, exist_ok=True)
            self._log_stream = self.log_path.open("ab", buffering=0)
            command = self.command_builder(record, "127.0.0.1", self.port, self.token, self.allowed_voice_root)
            threads = str(record.manifest.default_threads)
            environment = os.environ.copy()
            environment.update({
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                "CUDA_VISIBLE_DEVICES": "-1",
                "HF_HUB_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "HF_HOME": str(runtime_cache),
                "XDG_CACHE_HOME": str(runtime_cache),
                "MPLCONFIGDIR": str(runtime_cache),
                "BILILIVE_CPU_CACHE_DIR": str(runtime_cache),
                "BILILIVE_CPU_THREADS": threads,
                "OMP_NUM_THREADS": threads,
                "MKL_NUM_THREADS": threads,
                "OPENBLAS_NUM_THREADS": threads,
                "NUMEXPR_NUM_THREADS": threads,
                "ORT_INTRA_OP_NUM_THREADS": threads,
                "ORT_INTER_OP_NUM_THREADS": "1",
            })
            options: dict = {
                "cwd": str(record.path),
                "env": environment,
                "stdin": subprocess.PIPE,
                "stdout": self._log_stream,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                below_normal = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
                options["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                    | below_normal
                )
            else:
                options["start_new_session"] = True
                options["preexec_fn"] = lambda: os.nice(5)
            try:
                self.process = subprocess.Popen(command, **options)
                if not self.process.stdin:
                    raise OSError("sidecar token pipe unavailable")
                self.process.stdin.write((self.token + "\n").encode("utf-8"))
                self.process.stdin.close()
            except OSError as exc:
                error = SidecarError("runtime_start_failed", "无法启动 CPU 语音运行时")
                self._fail_locked(error)
                raise error from exc
            self.client = self.client_factory("127.0.0.1", self.port, self.token)

        deadline = time.monotonic() + self.startup_timeout
        last_error: SidecarError | None = None
        while time.monotonic() < deadline:
            with self._lock:
                if not self.process or self.process.poll() is not None:
                    error = SidecarError("runtime_exited", "CPU 语音运行时启动时意外退出")
                    self._fail_locked(error)
                    raise error
                client = self.client
            try:
                health = client.health(timeout=min(0.5, max(0.05, deadline - time.monotonic())))
                if health.get("status") == "ready":
                    self._validate_health(health)
                    with self._lock:
                        self._update_metrics(health)
                        self._state = "ready"
                    return self.status()
            except SidecarError as exc:
                if exc.code == "cpu_contract_failed":
                    with self._lock:
                        self._fail_locked(exc)
                    raise
                last_error = exc
            time.sleep(0.05)
        error = SidecarError("startup_timeout", "CPU 语音运行时启动超时")
        with self._lock:
            self._fail_locked(error)
        raise error from last_error

    def load_voice(self, request: dict) -> dict:
        with self._lock:
            if self._state != "ready" or not self.client:
                raise SidecarError("runtime_not_ready", "CPU 语音运行时尚未准备好")
            self._state = "busy"
            client = self.client
        try:
            result = client.load_voice(request)
            self._validate_health(result)
            with self._lock:
                self._update_metrics(result)
                self._state = "ready"
            return result
        except SidecarError as exc:
            if exc.code == "sidecar_unavailable":
                with self._lock:
                    record = self._record
                    can_restart = bool(record and self.metrics["restart_count"] < 1)
                    if can_restart:
                        self.metrics["restart_count"] += 1
                if can_restart:
                    self.prepare(record)
                    return self.load_voice(request)
            with self._lock:
                self._fail_locked(exc)
            raise

    def synthesize(self, text: str, language: str = "zh", request_timeout: float | None = None, **options) -> PcmStream:
        with self._lock:
            if self._state != "ready" or not self.client:
                raise SidecarError("runtime_not_ready", "CPU 语音运行时尚未准备好")
            self._state = "busy"
            client = self.client

        def complete() -> None:
            with self._lock:
                self._active_request_id = ""
                if self._state == "busy":
                    self._state = "ready"

        def failed(error: SidecarError) -> None:
            with self._lock:
                self._active_request_id = ""
                self._fail_locked(error)

        try:
            stream = client.synthesize(
                {"text": text, "language": language, **options},
                on_close=complete,
                on_error=failed,
                timeout=request_timeout,
            )
            with self._lock:
                self._active_request_id = stream.request_id
                self.metrics["first_pcm_ms"] = stream.first_pcm_ms
            return stream
        except SidecarError as exc:
            with self._lock:
                self._fail_locked(exc)
            raise

    def cancel(self) -> None:
        with self._lock:
            client = self.client
            request_id = self._active_request_id
            if not client or not self.process or self.process.poll() is not None or not request_id:
                return
        try:
            client.cancel(request_id)
        except SidecarError:
            return
        with self._lock:
            if self._state not in ("stopping", "stopped"):
                self._state = "ready"

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_locked(graceful=True)

    def _fail_locked(self, error: SidecarError) -> None:
        self._error = error.to_dict()
        self._state = "failed"
        self._terminate_process_locked()

    def _shutdown_locked(self, graceful: bool) -> None:
        process = self.process
        if process and process.poll() is None:
            self._state = "stopping"
            if graceful and self.client:
                try:
                    self.client.shutdown()
                except SidecarError:
                    pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._terminate_process_locked()
        self.process = None
        self.client = None
        self.token = ""
        self._active_request_id = ""
        self.port = 0
        self._close_log_locked()
        if self._state != "failed":
            self._state = "stopped"

    def _terminate_process_locked(self) -> None:
        process = self.process
        if process and process.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
        self._close_log_locked()

    def _close_log_locked(self) -> None:
        if self._log_stream:
            try:
                self._log_stream.close()
            except OSError:
                pass
            self._log_stream = None
