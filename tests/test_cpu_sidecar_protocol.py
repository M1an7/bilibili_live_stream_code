from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from runtime.style_bert_vits2_cpu.protocol import CpuVoiceEngine, ProtocolError, _cpu_session_options, _default_rss_probe
from runtime.style_bert_vits2_cpu.sidecar import create_server


MODEL_UUID = "c7c97043-39ad-47af-b150-765244885895"


class FakeModel:
    def __init__(self):
        self.calls = []
        self.unloaded = 0

    def infer(self, text, language, speaker_id, style_id, speed):
        self.calls.append((text, language, speaker_id, style_id, speed))
        return 44_100, b"\x01\x00" * 2_000

    def unload(self):
        self.unloaded += 1


class CpuSidecarProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.voice_root = self.root / "voices"
        directory = self.voice_root / MODEL_UUID
        directory.mkdir(parents=True)
        self.model_path = directory / "model.aivmx"
        self.model_path.write_bytes(b"safe-aivmx")
        digest = "sha256:" + hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        (directory / "install.json").write_text(json.dumps({
            "schema_version": 1,
            "model_uuid": MODEL_UUID,
            "model_file": "model.aivmx",
            "sha256": digest,
            "permissions_confirmed": True,
        }), "utf-8")
        self.metadata = SimpleNamespace(
            model_uuid=MODEL_UUID,
            architecture="Style-Bert-VITS2",
            model_format="ONNX",
            languages=("ja", "zh-CN"),
            styles=(SimpleNamespace(style_id=0, speaker_id=0, name="ノーマル"),),
        )
        self.model = FakeModel()

    def engine(self, providers=("CPUExecutionProvider",)):
        return CpuVoiceEngine(
            self.root,
            self.voice_root,
            metadata_reader=lambda _path: self.metadata,
            model_factory=lambda _path, _metadata: self.model,
            provider_probe=lambda: list(providers),
            rss_probe=lambda: {"rss_mb": 321, "peak_rss_mb": 456},
        )

    def test_loads_registered_model_and_synthesizes_chinese_with_cpu_only(self):
        engine = self.engine()

        loaded = engine.load_voice({"model_uuid": MODEL_UUID, "style_id": 0})
        chunks = list(engine.synthesize({
            "request_id": "request-1",
            "text": "保持中文并按中文发音朗读",
            "language": "zh",
            "speed": 1.25,
        }))

        self.assertEqual(loaded["providers"], ["CPUExecutionProvider"])
        self.assertEqual(chunks, [(44_100, b"\x01\x00" * 2_000)])
        self.assertEqual(self.model.calls[0], ("保持中文并按中文发音朗读", "ZH", 0, 0, 1.25))
        health = engine.health()
        self.assertEqual(health["vram_mb"], 0)
        self.assertEqual(health["rss_mb"], 321)

    def test_rejects_any_gpu_provider_and_modified_voice_file(self):
        with self.assertRaises(ProtocolError) as caught:
            self.engine(("CPUExecutionProvider", "CUDAExecutionProvider"))
        self.assertEqual(caught.exception.code, "gpu_provider_forbidden")

        engine = self.engine()
        self.model_path.write_bytes(b"changed")
        with self.assertRaises(ProtocolError) as caught:
            engine.load_voice({"model_uuid": MODEL_UUID, "style_id": 0})
        self.assertEqual(caught.exception.code, "voice_hash_mismatch")

    def test_rejects_unknown_style_non_chinese_and_concurrent_synthesis(self):
        engine = self.engine()
        with self.assertRaises(ProtocolError) as caught:
            engine.load_voice({"model_uuid": MODEL_UUID, "style_id": 9})
        self.assertEqual(caught.exception.code, "style_not_found")

        engine.load_voice({"model_uuid": MODEL_UUID, "style_id": 0})
        with self.assertRaises(ProtocolError) as caught:
            list(engine.synthesize({"request_id": "ja", "text": "テスト", "language": "ja"}))
        self.assertEqual(caught.exception.code, "language_not_supported")

        engine._synthesis_lock.acquire()
        try:
            with self.assertRaises(ProtocolError) as caught:
                list(engine.synthesize({"request_id": "busy", "text": "测试", "language": "zh"}))
            self.assertEqual(caught.exception.code, "runtime_busy")
        finally:
            engine._synthesis_lock.release()

    def test_onnx_sessions_have_a_real_four_thread_hard_limit(self):
        class Options:
            intra_op_num_threads = 0
            inter_op_num_threads = 0
            execution_mode = None
            graph_optimization_level = None
            enable_cpu_mem_arena = True

        fake_ort = SimpleNamespace(
            SessionOptions=Options,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"),
            GraphOptimizationLevel=SimpleNamespace(ORT_DISABLE_ALL="disabled"),
        )
        with patch.dict("os.environ", {"BILILIVE_CPU_THREADS": "4"}):
            options = _cpu_session_options(fake_ort, disable_arena=True)

        self.assertEqual(4, options.intra_op_num_threads)
        self.assertEqual(1, options.inter_op_num_threads)
        self.assertEqual("sequential", options.execution_mode)
        self.assertEqual("disabled", options.graph_optimization_level)
        self.assertFalse(options.enable_cpu_mem_arena)

    def test_windows_memory_metrics_use_the_native_working_set_probe(self):
        result = _default_rss_probe(
            platform_name="nt",
            windows_probe=lambda: {"rss_mb": 1536, "peak_rss_mb": 2048},
        )

        self.assertEqual({"rss_mb": 1536, "peak_rss_mb": 2048}, result)

    def test_loopback_http_requires_token_and_returns_framed_pcm(self):
        engine = self.engine()
        server = create_server("127.0.0.1", 0, "secret-token", engine)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/health")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 401)

        load = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/voices/load",
            data=json.dumps({"model_uuid": MODEL_UUID, "style_id": 0}).encode(),
            method="POST",
            headers={"Authorization": "Bearer secret-token", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(load, timeout=2) as response:
            self.assertEqual(json.loads(response.read())["status"], "ready")

        tts = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/tts",
            data=json.dumps({"request_id": "http-1", "text": "中文测试", "language": "zh"}).encode(),
            method="POST",
            headers={"Authorization": "Bearer secret-token", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(tts, timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "application/x-bililive-pcm-stream")
            self.assertGreater(len(response.read()), 4_000)


if __name__ == "__main__":
    unittest.main()
