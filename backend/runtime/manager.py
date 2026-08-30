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

from .client import PcmStream, SidecarClient, SidecarError
from .registry import RuntimeRecord


CommandBuilder = Callable[[RuntimeRecord, str, int, str, Path], list[str]]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _default_command(record: RuntimeRecord, host: str, port: int, token: str, allowed_root: Path) -> list[str]:
    python = record.path / "bin" / "python.exe"
    entrypoint = record.path / record.manifest.entrypoint
    return [
        str(python),
        str(entrypoint),
        "--host", host,
        "--port", str(port),
        "--token", token,
        "--allowed-root", str(allowed_root),
    ]


class GpuRuntimeManager:
    STATES = frozenset({"missing", "stopped", "starting", "ready", "busy", "stopping", "failed"})

    def __init__(
        self,
        log_directory: Path,
        allowed_voice_root: Path,
        command_builder: CommandBuilder | None = None,
        startup_timeout: float = 45.0,
        client_factory=SidecarClient,
    ):
        self.log_directory = Path(log_directory)
        self.allowed_voice_root = Path(allowed_voice_root)
        self.command_builder = command_builder or _default_command
        self.startup_timeout = startup_timeout
        self.client_factory = client_factory
        self._lock = threading.RLock()
        self._state = "stopped"
        self._error: dict | None = None
        self._record: RuntimeRecord | None = None
        self.process: subprocess.Popen | None = None
        self.client: SidecarClient | None = None
        self.port = 0
        self.token = ""
        self.log_path = self.log_directory / "gpu-sidecar.log"
        self._log_stream = None
        self.metrics = {"first_pcm_ms": 0, "warmup_ms": 0, "peak_vram_mb": 0, "restart_count": 0}

    def status(self) -> dict:
        with self._lock:
            process_running = bool(self.process and self.process.poll() is None)
            return {
                "state": self._state,
                "runtime_id": self._record.runtime_id if self._record else "",
                "process_running": process_running,
                "error": dict(self._error) if self._error else None,
                "metrics": dict(self.metrics),
            }

    def prepare(self, record: RuntimeRecord) -> dict:
        with self._lock:
            if self.process and self.process.poll() is None and self._record == record and self._state in ("ready", "busy"):
                return self.status()
            self._shutdown_locked(graceful=True)
            self._record = record
            self._state = "starting"
            self._error = None
            self.port = _free_loopback_port()
            self.token = secrets.token_urlsafe(48)
            self.log_directory.mkdir(parents=True, exist_ok=True)
            self.allowed_voice_root.mkdir(parents=True, exist_ok=True)
            self._log_stream = self.log_path.open("ab", buffering=0)
            command = self.command_builder(record, "127.0.0.1", self.port, self.token, self.allowed_voice_root)
            environment = os.environ.copy()
            environment.update({"PYTHONUNBUFFERED": "1", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
            options: dict = {
                "cwd": str(record.path),
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": self._log_stream,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            try:
                self.process = subprocess.Popen(command, **options)
            except OSError as exc:
                self._fail_locked(SidecarError("runtime_start_failed", "无法启动 GPU 语音运行时"))
                raise SidecarError("runtime_start_failed", "无法启动 GPU 语音运行时") from exc
            self.client = self.client_factory("127.0.0.1", self.port, self.token)

        deadline = time.monotonic() + self.startup_timeout
        last_error: SidecarError | None = None
        while time.monotonic() < deadline:
            with self._lock:
                if not self.process or self.process.poll() is not None:
                    error = SidecarError("runtime_exited", "GPU 语音运行时启动时意外退出")
                    self._fail_locked(error)
                    raise error
                client = self.client
            try:
                health = client.health(timeout=min(0.5, max(0.05, deadline - time.monotonic())))
                if health.get("status") == "ready":
                    with self._lock:
                        self._state = "ready"
                        if isinstance(health.get("vram_mb"), (int, float)):
                            self.metrics["peak_vram_mb"] = int(health["vram_mb"])
                    return self.status()
            except SidecarError as exc:
                last_error = exc
            time.sleep(0.05)
        error = SidecarError("startup_timeout", "GPU 语音运行时启动超时")
        with self._lock:
            self._fail_locked(error)
        raise error from last_error

    def load_voice(self, request: dict) -> dict:
        with self._lock:
            if self._state not in ("ready", "busy") or not self.client:
                raise SidecarError("runtime_not_ready", "GPU 语音运行时尚未准备好")
            self._state = "busy"
            client = self.client
        try:
            result = client.load_voice(request)
            with self._lock:
                self.metrics["warmup_ms"] = int(result.get("warmup_ms", 0))
                self.metrics["peak_vram_mb"] = max(self.metrics["peak_vram_mb"], int(result.get("peak_vram_mb", 0)))
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
                self._state = "failed"
                self._error = exc.to_dict()
            raise

    def synthesize(self, text: str, language: str = "ja", **options) -> PcmStream:
        with self._lock:
            if self._state != "ready" or not self.client:
                raise SidecarError("runtime_not_ready", "GPU 语音运行时尚未准备好")
            self._state = "busy"
            client = self.client

        def complete() -> None:
            with self._lock:
                if self._state == "busy":
                    self._state = "ready"

        try:
            stream = client.synthesize({"text": text, "language": language, **options}, on_close=complete)
            with self._lock:
                self.metrics["first_pcm_ms"] = stream.first_pcm_ms
            return stream
        except SidecarError as exc:
            with self._lock:
                self._state = "failed"
                self._error = exc.to_dict()
            raise

    def cancel(self) -> None:
        with self._lock:
            client = self.client
            if not client or not self.process or self.process.poll() is not None:
                return
        try:
            client.cancel()
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
