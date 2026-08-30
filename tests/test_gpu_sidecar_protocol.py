from __future__ import annotations

import array
import tempfile
import threading
import unittest
from pathlib import Path

from backend.runtime.client import SidecarClient, SidecarError
from runtime.gpt_sovits_gpu.protocol import GpuVoiceEngine, ProtocolError
from runtime.gpt_sovits_gpu.sidecar import create_server


class FakePipeline:
    def __init__(self, config):
        self.config = config
        self.stopped = False
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        yield 32000, array.array("h", [1000, -1000] * 1000)
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
        for path in (self.gpt, self.sovits, self.reference):
            path.write_bytes(b"model")
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

    def request(self, **overrides):
        payload = {
            "voice_id": "haibara-jp",
            "model_version": "v2Pro",
            "gpt_path": str(self.gpt),
            "sovits_path": str(self.sovits),
            "reference_audio_path": str(self.reference),
            "prompt_text": "今日何食べたい？",
            "prompt_language": "ja",
        }
        payload.update(overrides)
        return payload

    def test_load_is_cuda_fp16_batch_one_and_japanese_stream_is_pcm(self):
        loaded = self.engine.load_voice(self.request())
        self.assertEqual("ready", loaded["status"])
        config = self.pipelines[0].config["custom"]
        self.assertEqual("cuda", config["device"])
        self.assertTrue(config["is_half"])
        self.assertEqual("v2Pro", config["version"])

        chunks = list(self.engine.synthesize({"text": "こんにちは", "language": "ja", "speed": 1.0}))
        self.assertEqual(32000, chunks[0][0])
        self.assertGreater(len(chunks[0][1]), 100)
        request = self.pipelines[0].requests[0]
        self.assertEqual("ja", request["text_lang"])
        self.assertEqual(1, request["batch_size"])
        self.assertTrue(request["return_fragment"])

    def test_paths_must_remain_inside_authorized_voice_root(self):
        outside = self.root / "outside.ckpt"
        outside.write_bytes(b"private")
        with self.assertRaises(ProtocolError) as caught:
            self.engine.load_voice(self.request(gpt_path=str(outside)))
        self.assertEqual("path_not_allowed", caught.exception.code)

    def test_only_v2pro_japanese_and_expected_file_types_are_allowed(self):
        with self.assertRaises(ProtocolError):
            self.engine.load_voice(self.request(model_version="v3"))
        self.engine.load_voice(self.request())
        with self.assertRaises(ProtocolError) as caught:
            list(self.engine.synthesize({"text": "test", "language": "en"}))
        self.assertEqual("language_not_supported", caught.exception.code)

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
        self.assertGreater(len(b"".join(stream)), 100)
        self.assertEqual(32000, stream.sample_rate)
        client.cancel()
        self.assertTrue(self.pipelines[-1].stopped)
        client.shutdown()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
