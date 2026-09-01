from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from backend.cpu_runtime.manager import CpuRuntimeManager
from backend.cpu_runtime.manifest import CpuRuntimeManifest
from backend.cpu_runtime.registry import CpuRuntimeRecord
from backend.runtime.client import SidecarError


FIXTURE = Path(__file__).parent / "fixtures" / "fake_cpu_sidecar.py"


def runtime_record(root: Path, threads: int = 4) -> CpuRuntimeRecord:
    manifest = CpuRuntimeManifest.from_dict({
        "schema_version": 1,
        "runtime_id": "fake-cpu-runtime",
        "engine": "style-bert-vits2-onnx-cpu",
        "engine_api_version": 1,
        "platform": "windows-x86_64",
        "build_version": "test",
        "style_bert_vits2_commit": "a" * 40,
        "aivmlib_commit": "b" * 40,
        "python_version": "3.11",
        "onnxruntime_version": "1.20.1",
        "supported_architectures": ["Style-Bert-VITS2"],
        "supported_languages": ["zh-CN"],
        "entrypoint": "engine/sidecar.py",
        "gpu": False,
        "providers": ["CPUExecutionProvider"],
        "default_threads": threads,
        "files": {"engine/sidecar.py": "sha256:" + "0" * 64},
    })
    return CpuRuntimeRecord(root, manifest, False)


class CpuRuntimeManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        runtime_root = self.root / "runtime"
        runtime_root.mkdir()
        self.record = runtime_record(runtime_root)
        self.managers: list[CpuRuntimeManager] = []

    def tearDown(self):
        for manager in self.managers:
            manager.shutdown()

    def manager(self, mode: str = "normal", timeout: float = 3.0, verifier=None) -> CpuRuntimeManager:
        def command(_record, host, port, token, allowed_root):
            return [
                sys.executable, str(FIXTURE), "--host", host, "--port", str(port),
                "--token-stdin", "--allowed-root", str(allowed_root), "--mode", mode,
            ]

        manager = CpuRuntimeManager(
            self.root / "logs",
            self.root / "aivmx-voices",
            command_builder=command,
            startup_timeout=timeout,
            runtime_verifier=verifier,
        )
        self.managers.append(manager)
        return manager

    def test_starts_only_on_prepare_and_shutdown_releases_process(self):
        manager = self.manager()
        self.assertEqual("stopped", manager.status()["state"])
        self.assertIsNone(manager.process)
        status = manager.prepare(self.record)
        self.assertEqual("ready", status["state"])
        process = manager.process
        self.assertIsNone(process.poll())
        self.assertNotIn(manager.token, " ".join(str(item) for item in process.args))
        manager.shutdown()
        self.assertEqual("stopped", manager.status()["state"])
        self.assertIsNotNone(process.poll())

    def test_forces_zero_gpu_offline_and_hard_thread_limits(self):
        manager = self.manager()
        manager.prepare(self.record)
        health = manager.client.health()
        environment = health["environment"]
        self.assertEqual("-1", environment["CUDA_VISIBLE_DEVICES"])
        self.assertEqual("false", environment["TOKENIZERS_PARALLELISM"])
        for key in ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE"):
            self.assertEqual("1", environment[key])
        for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "ORT_INTRA_OP_NUM_THREADS", "BILILIVE_CPU_THREADS"):
            self.assertEqual("4", environment[key])
        self.assertEqual("1", environment["ORT_INTER_OP_NUM_THREADS"])
        self.assertEqual(0, manager.status()["metrics"]["vram_mb"])

    def test_loads_and_streams_chinese_with_cpu_metrics(self):
        manager = self.manager()
        manager.prepare(self.record)
        result = manager.load_voice({"model_uuid": "00000000-0000-4000-8000-000000000001", "style_id": 0})
        self.assertEqual("ready", result["status"])
        stream = manager.synthesize("中文测试", language="zh")
        self.assertGreater(len(b"".join(stream)), 4_000)
        status = manager.status()
        self.assertEqual("ready", status["state"])
        self.assertEqual(23, status["metrics"]["first_pcm_ms"])
        self.assertEqual(2060, status["metrics"]["peak_rss_mb"])
        self.assertEqual(["CPUExecutionProvider"], status["metrics"]["providers"])

    def test_rejects_gpu_provider_or_nonzero_vram(self):
        for mode in ("gpu-provider", "vram-used"):
            with self.subTest(mode=mode):
                manager = self.manager(mode=mode)
                with self.assertRaises(SidecarError) as caught:
                    manager.prepare(self.record)
                self.assertEqual("cpu_contract_failed", caught.exception.code)
                self.assertEqual("failed", manager.status()["state"])

    def test_reverifies_runtime_before_launch(self):
        checked = []
        record = self.record

        class Verifier:
            def verify_directory(self, path):
                checked.append(Path(path))
                return record

        manager = self.manager(verifier=Verifier())
        manager.prepare(record)
        self.assertEqual([record.path], checked)

    def test_connection_failure_restarts_exactly_once(self):
        manager = self.manager(mode="crash-load-once")
        manager.prepare(self.record)
        first_pid = manager.process.pid
        manager.load_voice({"model_uuid": "00000000-0000-4000-8000-000000000001", "style_id": 0})
        self.assertNotEqual(first_pid, manager.process.pid)
        self.assertEqual(1, manager.status()["metrics"]["restart_count"])

    def test_startup_timeout_terminates_process_tree(self):
        manager = self.manager(mode="never-ready", timeout=0.25)
        started = time.monotonic()
        with self.assertRaises(SidecarError) as caught:
            manager.prepare(self.record)
        self.assertEqual("startup_timeout", caught.exception.code)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(manager.process is None or manager.process.poll() is not None)


if __name__ == "__main__":
    unittest.main()
