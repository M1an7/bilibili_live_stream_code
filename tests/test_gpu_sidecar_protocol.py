from __future__ import annotations

import array
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
from unittest.mock import patch
from pathlib import Path

from backend.runtime.client import SidecarClient, SidecarError
from runtime.gpt_sovits_gpu.protocol import GpuVoiceEngine, ProtocolError, _default_pipeline_factory
from runtime.gpt_sovits_gpu.sidecar import create_server


class FakePipeline:
    def __init__(self, config):
        self.config = config
        self.stopped = False
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        yield 32000, array.array("h", [1000, -1000] * 1000)
        if request["text"] == "midstream-error":
            raise RuntimeError("cuda stream failed")
        yield 32000, array.array("h", [500, -500] * 500)

    def stop(self):
        self.stopped = True


class GpuSidecarProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.voices = self.root / "voices"
        self.runtime = self.root / "runtime"
        self.voices.mkdir()
        self.runtime.mkdir()
        self.pack = self.voices / "haibara-jp"
        (self.pack / "model").mkdir(parents=True)
        self.gpt = self.pack / "model" / "gpt.ckpt"
        self.sovits = self.pack / "model" / "sovits.pth"
        self.reference = self.pack / "reference.wav"
        self.reference_text = self.pack / "reference.txt"
        self.license = self.pack / "LICENSE.txt"
        for path in (self.gpt, self.sovits, self.reference):
            path.write_bytes(b"model")
        self.reference_text.write_text("今日何食べたい？", "utf-8")
        self.license.write_text("authorized", "utf-8")
        self._write_manifest()
        self.pipelines = []

        def factory(config):
            pipeline = FakePipeline(config)
            self.pipelines.append(pipeline)
            return pipeline

        self.engine = GpuVoiceEngine(
            self.runtime,
            self.voices,
            pipeline_factory=factory,
            gpu_probe=lambda: {"gpu": "fake-rtx", "vram_total_mb": 6144},
        )

    @staticmethod
    def _digest(path):
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_manifest(self, **overrides):
        payload = {
            "schema_version": 1,
            "voice_id": "haibara-jp",
            "engine": "gpt-sovits-gpu",
            "engine_api_version": 1,
            "model_version": "v2Pro",
            "source_language": "ja",
            "supported_output_languages": ["ja"],
            "models": {"gpt": "model/gpt.ckpt", "sovits": "model/sovits.pth"},
            "reference_audio": "reference.wav",
            "reference_text": "reference.txt",
            "license_file": "LICENSE.txt",
            "files": {
                "model/gpt.ckpt": self._digest(self.gpt),
                "model/sovits.pth": self._digest(self.sovits),
                "reference.wav": self._digest(self.reference),
                "reference.txt": self._digest(self.reference_text),
                "LICENSE.txt": self._digest(self.license),
            },
        }
        payload.update(overrides)
        (self.pack / "manifest.json").write_text(json.dumps(payload), "utf-8")

    def request(self, **overrides):
        payload = {"voice_id": "haibara-jp"}
        payload.update(overrides)
        return payload

    def test_default_pipeline_keeps_mutable_tts_config_outside_the_signed_runtime(self):
        source = self.runtime / "upstream" / "GPT_SoVITS" / "TTS_infer_pack" / "TTS.py"
        source.parent.mkdir(parents=True)
        source.write_text("# import contract", "utf-8")
        signed_config = self.runtime / "upstream" / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        signed_config.parent.mkdir(parents=True)
        signed_config.write_text("signed-original", "utf-8")
        cache = self.root / "runtime-cache"

        class FakeTtsConfig:
            def __init__(self, payload):
                self.payload = payload
                self.configs_path = str(signed_config)

        class FakeTts:
            def __init__(self, config):
                target = Path(config.configs_path) if hasattr(config, "configs_path") else signed_config
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("upstream-mutated", "utf-8")
                self.config = config

        module = types.ModuleType("GPT_SoVITS.TTS_infer_pack.TTS")
        module.TTS = FakeTts
        module.TTS_Config = FakeTtsConfig
        original_cwd = Path.cwd()
        try:
            with patch.dict(sys.modules, {"GPT_SoVITS.TTS_infer_pack.TTS": module}), patch.dict(
                os.environ, {"BILILIVE_GPU_CACHE_DIR": str(cache)}
            ):
                pipeline = _default_pipeline_factory(self.runtime, {"custom": {"version": "v2Pro"}})
        finally:
            os.chdir(original_cwd)

        self.assertEqual("signed-original", signed_config.read_text("utf-8"))
        self.assertEqual(cache / "tts_infer.yaml", Path(pipeline.config.configs_path))
        self.assertEqual("upstream-mutated", (cache / "tts_infer.yaml").read_text("utf-8"))

    def test_load_is_cuda_fp16_batch_one_and_japanese_stream_is_pcm(self):
        loaded = self.engine.load_voice(self.request())
        self.assertEqual("ready", loaded["status"])
        config = self.pipelines[0].config["custom"]
        self.assertEqual("cuda", config["device"])
        self.assertTrue(config["is_half"])
        self.assertEqual("v2Pro", config["version"])

        chunks = list(self.engine.synthesize({"request_id": "direct-1", "text": "こんにちは", "language": "ja", "speed": 1.0}))
        self.assertEqual(32000, chunks[0][0])
        self.assertGreater(len(chunks[0][1]), 100)
        request = self.pipelines[0].requests[0]
        self.assertEqual("ja", request["text_lang"])
        self.assertEqual(1, request["batch_size"])
        self.assertTrue(request["return_fragment"])

    def test_paths_must_remain_inside_authorized_voice_root(self):
        outside = self.root / "outside.ckpt"
        outside.write_bytes(b"private")
        self._write_manifest(models={"gpt": "../../outside.ckpt", "sovits": "model/sovits.pth"})
        with self.assertRaises(ProtocolError) as caught:
            self.engine.load_voice(self.request())
        self.assertIn(caught.exception.code, {"path_not_allowed", "invalid_voice_manifest"})

    def test_only_v2pro_japanese_and_expected_file_types_are_allowed(self):
        with self.assertRaises(ProtocolError):
            self.engine.load_voice(self.request(model_version="v3"))
        self._write_manifest(model_version="v3")
        with self.assertRaises(ProtocolError):
            self.engine.load_voice(self.request())
        self._write_manifest(model_version="v2Pro")
        self.engine.load_voice(self.request())
        with self.assertRaises(ProtocolError) as caught:
            list(self.engine.synthesize({"request_id": "direct-2", "text": "test", "language": "en"}))
        self.assertEqual("language_not_supported", caught.exception.code)

    def test_v2proplus_pack_uses_its_declared_pipeline_version(self):
        self._write_manifest(model_version="v2ProPlus")
        self.engine.load_voice(self.request())
        self.assertEqual("v2ProPlus", self.pipelines[-1].config["custom"]["version"])

    def test_token_authenticated_http_surface_supports_health_load_tts_cancel_shutdown(self):
        server = create_server("127.0.0.1", 0, "secret-token", self.engine)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        port = server.server_address[1]

        unauthorized = SidecarClient("127.0.0.1", port, "wrong")
        with self.assertRaises(SidecarError) as caught:
            unauthorized.health()
        self.assertEqual("unauthorized", caught.exception.code)

        client = SidecarClient("127.0.0.1", port, "secret-token")
        self.assertEqual("ready", client.health()["status"])
        client.load_voice(self.request())
        stream = client.synthesize({"text": "こんにちは", "language": "ja"})
        self.assertTrue(stream.request_id)
        self.assertGreater(len(b"".join(stream)), 100)
        self.assertEqual(32000, stream.sample_rate)
        self.assertIn(client.cancel(stream.request_id)["status"], {"cancelled", "not_active"})
        client.shutdown()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_midstream_cuda_error_is_a_structured_frame_not_pcm_noise(self):
        server = create_server("127.0.0.1", 0, "secret-token", self.engine)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        client = SidecarClient("127.0.0.1", server.server_address[1], "secret-token")
        client.load_voice(self.request())
        stream = client.synthesize({"text": "midstream-error", "language": "ja"})
        self.assertGreater(len(next(stream)), 100)
        with self.assertRaises(SidecarError) as caught:
            next(stream)
        self.assertEqual("cuda_error", caught.exception.code)
        client.shutdown()
        thread.join(timeout=2)

    def test_client_closes_http_error_responses(self):
        body = io.BytesIO(b'{"code":"bad_request","message":"bad"}')
        error = urllib.error.HTTPError("http://127.0.0.1/", 400, "bad", {}, body)
        client = SidecarClient("127.0.0.1", 1, "token")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(SidecarError):
                client.health()
        self.assertTrue(body.closed)

    def test_shared_client_connection_error_is_engine_neutral(self):
        client = SidecarClient("127.0.0.1", 1, "token")
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(SidecarError) as caught:
                client.health()
        self.assertIn("语音运行时", caught.exception.message)
        self.assertNotIn("GPU", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
