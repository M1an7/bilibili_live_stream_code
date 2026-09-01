from __future__ import annotations

import tempfile
import unittest
import sys
import types
from pathlib import Path
from types import SimpleNamespace

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
danmu_stub = types.ModuleType("backend.services.danmu_service")
danmu_stub.DanmuService = object
sys.modules.setdefault("backend.services.danmu_service", danmu_stub)

from backend.api_service import ApiService
from backend.aivmx.protobuf import AivmxMetadataReader
from tests.test_aivmx_registry import MODEL_UUID, write_aivmx


VOICE_KEY = f"aivmx:{MODEL_UUID}:0"


class FakeJobs:
    def __init__(self, job_id="job-1"):
        self.job_id = job_id
        self.started = []
        self.closed = 0

    def start(self, request):
        self.started.append(request)
        return self.job_id

    def get(self, job_id):
        return {"job_id": job_id, "status": "completed"}

    def shutdown(self):
        self.closed += 1


class FakeVoiceRegistry:
    def __init__(self):
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def list_voices(self):
        return [{
            "voice_key": VOICE_KEY,
            "model_uuid": MODEL_UUID,
            "style_id": 0,
            "resource_mode": "cpu_zero_vram",
        }]

    def get(self, model_uuid):
        if model_uuid != MODEL_UUID:
            return None
        return SimpleNamespace(metadata=SimpleNamespace(architecture="Style-Bert-VITS2"))


class FakeRuntimeRegistry:
    def status(self):
        return {"state": "ready", "runtimes": [{"runtime_id": "cpu-test", "gpu": False}]}

    def find_compatible(self, architecture, language):
        if (architecture, language) != ("Style-Bert-VITS2", "zh-CN"):
            return None
        return SimpleNamespace(runtime_id="cpu-test")


class FakeHealth:
    def get(self, _record, _style_id, _runtime):
        return {"health": "ready", "message": "ready", "metrics": {"vram_mb": 0}}


class FakeManager:
    def status(self):
        return {"state": "stopped", "metrics": {"vram_mb": 0}}


class FakeSpeech:
    def __init__(self):
        self.prepared = []
        self.previewed = []
        self.closed = 0

    def prepare(self, key):
        self.prepared.append(key)
        return {"health": "ready", "runtime": {"metrics": {"vram_mb": 0}}}

    def preview(self, key, text):
        self.previewed.append((key, text))
        return {"health": "ready"}

    def shutdown(self):
        self.closed += 1


class AivmxApiTests(unittest.TestCase):
    def setUp(self):
        self.api = ApiService.__new__(ApiService)
        self.api.aivmx_reader = AivmxMetadataReader(max_model_bytes=1024 * 1024)
        self.api.aivmx_jobs = FakeJobs("aivmx-job")
        self.api.aivmx_registry = FakeVoiceRegistry()
        self.api.cpu_runtime_jobs = FakeJobs("cpu-job")
        self.api.cpu_runtime_registry = FakeRuntimeRegistry()
        self.api.cpu_runtime_manager = FakeManager()
        self.api.aivmx_speech = FakeSpeech()
        self.api.aivmx_health = FakeHealth()

    def test_inspect_import_and_list_aivmx(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "voice.aivmx"
            write_aivmx(source)
            inspected = self.api.inspect_aivmx(str(source))

        self.assertEqual(0, inspected["code"])
        self.assertEqual(MODEL_UUID, inspected["data"]["model_uuid"])
        self.assertEqual("Style-Bert-VITS2", inspected["data"]["architecture"])
        self.assertTrue(inspected["data"]["sha256"].startswith("sha256:"))

        request = {"path": "D:/voice.aivmx", "permissions_confirmed": True}
        self.assertEqual("aivmx-job", self.api.start_aivmx_install(request)["data"]["job_id"])
        self.assertEqual([request], self.api.aivmx_jobs.started)
        self.assertEqual("completed", self.api.get_aivmx_job("aivmx-job")["data"]["status"])
        self.assertEqual(VOICE_KEY, self.api.list_aivmx_voices()["data"][0]["voice_key"])

    def test_cpu_runtime_install_status_prepare_preview_and_release(self):
        request = {"source_type": "zip", "path": "D:/cpu-runtime.zip"}
        self.assertEqual("cpu-job", self.api.start_cpu_runtime_install(request)["data"]["job_id"])
        self.assertEqual("completed", self.api.get_cpu_runtime_job("cpu-job")["data"]["status"])
        status = self.api.get_cpu_runtime_status()["data"]
        self.assertEqual("ready", status["state"])
        self.assertEqual(0, status["process"]["metrics"]["vram_mb"])

        self.assertEqual("ready", self.api.prepare_aivmx_voice(VOICE_KEY)["data"]["health"])
        self.assertEqual("ready", self.api.preview_aivmx_voice(VOICE_KEY, "中文试听")["data"]["health"])
        self.assertEqual([(VOICE_KEY, "中文试听")], self.api.aivmx_speech.previewed)
        self.assertEqual(0, self.api.release_aivmx_voice()["code"])
        self.assertEqual(1, self.api.aivmx_speech.closed)

    def test_cpu_runtime_picker_rejects_unknown_source_kind(self):
        result = self.api.choose_cpu_runtime_source("cuda")
        self.assertEqual(-1, result["code"])
        self.assertEqual("invalid_source_kind", result["error"]["code"])


if __name__ == "__main__":
    unittest.main()
