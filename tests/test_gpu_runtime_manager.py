from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from backend.runtime.client import SidecarError
from backend.runtime.manager import GpuRuntimeManager
from backend.runtime.manifest import RuntimeManifest
from backend.runtime.registry import RuntimeRecord


FIXTURE = Path(__file__).parent / "fixtures" / "fake_gpu_sidecar.py"


def runtime_record(root: Path) -> RuntimeRecord:
    manifest = RuntimeManifest.from_dict(
        {
            "schema_version": 1,
            "runtime_id": "fake-runtime",
            "engine": "gpt-sovits-gpu",
            "engine_api_version": 1,
            "platform": "windows-x86_64",
            "build_version": "test",
            "gpt_sovits_commit": "a" * 40,
            "python_version": "3.10",
            "torch_version": "test",
            "cuda_version": "12.6",
            "supported_model_versions": ["v2Pro"],
            "supported_languages": ["ja"],
            "entrypoint": "engine/sidecar.py",
            "gpu": True,
            "precision": "fp16",
            "minimum_compute_capability": "6.1",
            "minimum_vram_mb": 4096,
            "files": {"engine/sidecar.py": "sha256:" + "0" * 64},
        }
    )
    return RuntimeRecord(root, manifest, False)


class GpuRuntimeManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        runtime_root = self.root / "runtime"
        runtime_root.mkdir()
        self.record = runtime_record(runtime_root)
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.shutdown()

    def manager(self, mode="normal", timeout=3.0, runtime_verifier=None):
        def command(_record, host, port, token, allowed_root):
            return [
                sys.executable,
                str(FIXTURE),
                "--host", host,
                "--port", str(port),
                "--token-stdin",
                "--allowed-root", str(allowed_root),
                "--mode", mode,
            ]

        instance = GpuRuntimeManager(
            log_directory=self.root / "logs",
            allowed_voice_root=self.root / "voices",
            command_builder=command,
            startup_timeout=timeout,
            runtime_verifier=runtime_verifier,
        )
        self.managers.append(instance)
        return instance

    def test_reverifies_runtime_immediately_before_launch(self):
        verified = []

        class Verifier:
            def verify_directory(self, path):
                verified.append(Path(path))
                return self_record

        self_record = self.record
        manager = self.manager(runtime_verifier=Verifier())
        manager.prepare(self.record)
        self.assertEqual([self.record.path], verified)

    def test_has_no_process_until_prepared_and_releases_it_on_shutdown(self):
        manager = self.manager()
        self.assertEqual("stopped", manager.status()["state"])
        self.assertIsNone(manager.process)
        status = manager.prepare(self.record)
        self.assertEqual("ready", status["state"])
        process = manager.process
        self.assertIsNotNone(process)
        self.assertIsNone(process.poll())
        self.assertNotIn(manager.token, " ".join(str(item) for item in process.args))
        manager.shutdown()
        self.assertEqual("stopped", manager.status()["state"])
        self.assertIsNotNone(process.poll())

    def test_random_bearer_token_protects_loopback_server(self):
        manager = self.manager()
        manager.prepare(self.record)
        request = urllib.request.Request(f"http://127.0.0.1:{manager.port}/v1/health")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=1)
        self.assertEqual(401, caught.exception.code)
        self.assertNotIn(manager.token, (manager.log_path.read_text("utf-8") if manager.log_path.exists() else ""))

    def test_sidecar_is_cuda_scoped_and_forced_offline(self):
        manager = self.manager()
        manager.prepare(self.record)
        environment = manager.client.health()["environment"]
        self.assertEqual("0", environment["CUDA_VISIBLE_DEVICES"])
        self.assertEqual("1", environment["PYTHONDONTWRITEBYTECODE"])
        for key in ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE"):
            self.assertEqual("1", environment[key])
        cache_root = (self.root / "logs" / "gpu-runtime-cache").resolve()
        for key in ("NUMBA_CACHE_DIR", "HF_HOME", "XDG_CACHE_HOME", "MPLCONFIGDIR", "BILILIVE_GPU_CACHE_DIR"):
            self.assertEqual(str(cache_root), environment[key])
        self.assertFalse(str(cache_root).startswith(str(self.record.path.resolve()) + str(Path("/"))))
        self.assertEqual("1", environment["OMP_NUM_THREADS"])
        self.assertEqual("1", environment["MKL_NUM_THREADS"])

    def test_load_and_stream_pcm_with_metrics(self):
        manager = self.manager()
        manager.prepare(self.record)
        load = manager.load_voice({"voice_id": "haibara-jp", "model_version": "v2Pro"})
        self.assertEqual("ready", load["status"])
        stream = manager.synthesize("こんにちは", language="ja")
        pcm = b"".join(stream)
        self.assertGreater(len(pcm), 1000)
        self.assertEqual(32000, stream.sample_rate)
        self.assertEqual(1, stream.channels)
        self.assertEqual(2, stream.sample_width)
        self.assertEqual("ready", manager.status()["state"])
        self.assertEqual(18, manager.status()["metrics"]["first_pcm_ms"])

    def test_synthesis_can_use_a_separate_cold_warmup_timeout(self):
        observed = {}

        class TimeoutClient:
            def synthesize(self, request, on_close=None, on_error=None, timeout=None):
                observed["timeout"] = timeout
                raise SidecarError("expected", "stop")

        manager = self.manager()
        manager._state = "ready"
        manager.client = TimeoutClient()
        with self.assertRaises(SidecarError):
            manager.synthesize("準備中", request_timeout=90.0)
        self.assertEqual(90.0, observed["timeout"])

    def test_structured_cuda_error_is_preserved(self):
        manager = self.manager()
        manager.prepare(self.record)
        process = manager.process
        with self.assertRaises(SidecarError) as caught:
            manager.load_voice({"voice_id": "cuda-error"})
        self.assertEqual("cuda_out_of_memory", caught.exception.code)
        self.assertEqual("failed", manager.status()["state"])
        self.assertIsNotNone(process.poll())

    def test_synthesis_failure_terminates_the_sidecar(self):
        manager = self.manager()
        manager.prepare(self.record)
        manager.load_voice({"voice_id": "haibara-jp"})
        process = manager.process
        with self.assertRaises(SidecarError) as caught:
            manager.synthesize("cuda-error")
        self.assertEqual("cuda_error", caught.exception.code)
        self.assertIsNotNone(process.poll())

    def test_connection_failure_gets_exactly_one_process_restart(self):
        manager = self.manager(mode="crash-load-once")
        manager.prepare(self.record)
        first_pid = manager.process.pid
        result = manager.load_voice({"voice_id": "haibara-jp"})
        self.assertEqual("ready", result["status"])
        self.assertNotEqual(first_pid, manager.process.pid)
        self.assertEqual(1, manager.status()["metrics"]["restart_count"])

    def test_cancel_is_idempotent_and_returns_to_ready(self):
        manager = self.manager()
        manager.prepare(self.record)
        manager.cancel()
        manager.cancel()
        self.assertEqual("ready", manager.status()["state"])

    def test_startup_timeout_terminates_process_tree(self):
        manager = self.manager(mode="never-ready", timeout=0.25)
        started = time.monotonic()
        with self.assertRaises(SidecarError) as caught:
            manager.prepare(self.record)
        self.assertEqual("startup_timeout", caught.exception.code)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual("failed", manager.status()["state"])
        self.assertTrue(manager.process is None or manager.process.poll() is not None)


if __name__ == "__main__":
    unittest.main()
