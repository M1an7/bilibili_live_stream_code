from __future__ import annotations

import copy
import threading
import time
import uuid
from pathlib import Path

from .contract import AivmxContractError
from .registry import AivmxVoiceRegistry


class AivmxInstallJobManager:
    def __init__(self, registry: AivmxVoiceRegistry):
        self.registry = registry
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._shutdown = False

    def start(self, request: dict) -> str:
        if not isinstance(request, dict):
            raise AivmxContractError("invalid_request", "AIVMX 导入参数无效")
        path = str(request.get("path", "")).strip()
        if not path:
            raise AivmxContractError("invalid_request", "请选择 AIVMX 音色文件", "path")
        with self._lock:
            if self._shutdown:
                raise AivmxContractError("jobs_stopped", "AIVMX 导入服务正在关闭")
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "stage": "prepare",
                "progress": 0,
                "message": "已加入 AIVMX 导入队列",
                "result": None,
                "error": None,
                "finished_at": None,
            }
            worker = threading.Thread(
                target=self._run,
                args=(job_id, path, request.get("permissions_confirmed") is True),
                daemon=True,
                name=f"aivmx-import-{job_id[:8]}",
            )
            self._threads[job_id] = worker
            worker.start()
            return job_id

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            self._jobs[job_id].update(values)

    def _run(self, job_id: str, path: str, permissions_confirmed: bool) -> None:
        try:
            self._update(job_id, status="running", message="正在检查 AIVMX 音色")

            def progress(stage: str, percentage: int, message: str) -> None:
                self._update(job_id, stage=stage, progress=percentage, message=message)

            record = self.registry.install(Path(path), permissions_confirmed, progress)
            voices = record.to_dicts()
            self._update(
                job_id,
                status="completed",
                stage="done",
                progress=100,
                message=record.message,
                result=voices[0] if len(voices) == 1 else {"model_uuid": record.metadata.model_uuid, "voices": voices},
                finished_at=time.time(),
            )
        except AivmxContractError as exc:
            self._update(
                job_id,
                status="failed",
                message=exc.message,
                error=exc.to_dict(),
                finished_at=time.time(),
            )
        except Exception:
            self._update(
                job_id,
                status="failed",
                message="AIVMX 音色导入失败，请查看应用日志",
                error={"code": "internal_error", "message": "AIVMX 音色导入失败，请查看应用日志", "field": ""},
                finished_at=time.time(),
            )

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise AivmxContractError("job_not_found", "找不到 AIVMX 导入任务")
            return copy.deepcopy({key: value for key, value in job.items() if key != "finished_at"})

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=3)
