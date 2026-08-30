from __future__ import annotations

import copy
import shutil
import threading
import time
import uuid

from .builder import BuiltVoicePack, VoiceBuildRequest, VoiceJobCancelled, VoicePackBuilder
from .manifest import VoiceContractError
from .registry import VoicePackRegistry


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class VoiceJobManager:
    def __init__(self, builder: VoicePackBuilder, registry: VoicePackRegistry):
        self.builder = builder
        self.registry = registry
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._shutdown = False

    def start_build(self, request: dict) -> str:
        with self._lock:
            if self._shutdown:
                raise VoiceContractError("jobs_stopped", "音色任务服务正在关闭")
            job_id = uuid.uuid4().hex
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "stage": "prepare",
                "progress": 0,
                "message": "已加入导入队列",
                "result": None,
                "error": None,
                "finished_at": None,
            }
            cancel_event = threading.Event()
            self._cancel_events[job_id] = cancel_event
            worker = threading.Thread(
                target=self._run_build,
                args=(job_id, copy.deepcopy(request), cancel_event),
                name=f"voice-import-{job_id[:8]}",
                daemon=True,
            )
            self._threads[job_id] = worker
            worker.start()
            return job_id

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def _run_build(self, job_id: str, payload: dict, cancel_event: threading.Event) -> None:
        built: BuiltVoicePack | None = None
        try:
            self._update(job_id, status="running", message="正在准备导入")
            request = VoiceBuildRequest.from_dict(payload)

            def progress(stage: str, percentage: int, message: str) -> None:
                self._update(job_id, stage=stage, progress=percentage, message=message)

            built = self.builder.build(request, progress=progress, cancelled=cancel_event.is_set)
            if cancel_event.is_set():
                raise VoiceJobCancelled("音色导入已取消")
            self._update(job_id, stage="install", progress=98, message="正在原子安装音色包")
            record = self.registry.install_staged(built)
            built = None
            self._update(
                job_id,
                status="completed",
                stage="done",
                progress=100,
                message=record.message,
                result=record.to_dict(),
                finished_at=time.time(),
            )
        except VoiceJobCancelled as exc:
            self._update(
                job_id,
                status="cancelled",
                message=str(exc),
                error={"code": "cancelled", "message": str(exc), "field": ""},
                finished_at=time.time(),
            )
        except VoiceContractError as exc:
            self._update(
                job_id,
                status="failed",
                message=exc.message,
                error={"code": exc.code, "message": exc.message, "field": exc.field},
                finished_at=time.time(),
            )
        except Exception:
            self._update(
                job_id,
                status="failed",
                message="音色导入失败，请查看应用日志",
                error={"code": "internal_error", "message": "音色导入失败，请查看应用日志", "field": ""},
                finished_at=time.time(),
            )
        finally:
            if built and built.staging_path.exists():
                shutil.rmtree(built.staging_path, ignore_errors=True)

    def get(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise VoiceContractError("job_not_found", "找不到音色导入任务")
            result = {key: value for key, value in job.items() if key != "finished_at"}
            return copy.deepcopy(result)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise VoiceContractError("job_not_found", "找不到音色导入任务")
            if job["status"] in TERMINAL_STATUSES:
                return False
            self._cancel_events[job_id].set()
            return True

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            events = list(self._cancel_events.values())
            threads = list(self._threads.values())
        for event in events:
            event.set()
        for thread in threads:
            thread.join(timeout=2)
