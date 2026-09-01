from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path

from .installer import CpuRuntimeInstaller
from .manifest import CpuRuntimeContractError
from .registry import CpuRuntimeRegistry


class CpuRuntimeInstallJobManager:
    def __init__(self, installer: CpuRuntimeInstaller, registry: CpuRuntimeRegistry):
        self.installer = installer
        self.registry = registry
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._shutdown = False

    def start(self, request: dict) -> str:
        if not isinstance(request, dict):
            raise CpuRuntimeContractError("invalid_request", "CPU 运行时安装参数无效")
        source_type = request.get("source_type")
        source_path = str(request.get("path", "")).strip()
        if source_type not in ("zip", "directory") or not source_path:
            raise CpuRuntimeContractError("invalid_request", "请选择 CPU 运行时 ZIP 或目录")
        with self._lock:
            if self._shutdown:
                raise CpuRuntimeContractError("jobs_stopped", "CPU 运行时安装服务正在关闭")
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "phase": "prepare",
                "progress": 0,
                "message": "已加入 CPU 运行时安装队列",
                "result": None,
                "error": None,
            }
            worker = threading.Thread(
                target=self._run,
                args=(job_id, source_type, source_path),
                daemon=True,
                name=f"cpu-runtime-install-{job_id[:8]}",
            )
            self._threads[job_id] = worker
            worker.start()
            return job_id

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            self._jobs[job_id].update(values)

    def _run(self, job_id: str, source_type: str, source_path: str) -> None:
        try:
            self._update(job_id, status="running", message="正在检查 CPU 运行时")

            def progress(update: dict) -> None:
                self._update(
                    job_id,
                    phase=update.get("phase", "install"),
                    progress=int(update.get("percent", 0)),
                    message=update.get("message", "正在安装 CPU 运行时"),
                )

            if source_type == "zip":
                record = self.installer.install_zip(Path(source_path), progress)
            else:
                record = self.installer.install_directory(Path(source_path), progress)
            self.registry.refresh()
            self._update(
                job_id,
                status="completed",
                phase="done",
                progress=100,
                message="CPU 运行时安装完成",
                result=record.to_dict(),
            )
        except CpuRuntimeContractError as exc:
            self._update(job_id, status="failed", message=exc.message, error=exc.to_dict())
        except Exception:
            self._update(
                job_id,
                status="failed",
                message="CPU 运行时安装失败，请查看应用日志",
                error={"code": "internal_error", "message": "CPU 运行时安装失败，请查看应用日志", "field": ""},
            )

    def get(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise CpuRuntimeContractError("job_not_found", "找不到 CPU 运行时安装任务")
            return copy.deepcopy(self._jobs[job_id])

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=3)
