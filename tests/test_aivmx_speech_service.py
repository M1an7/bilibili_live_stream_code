from __future__ import annotations

import math
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.aivmx.health import AivmxHealthStore
from backend.aivmx.protobuf import AivmxMetadataReader
from backend.aivmx.registry import AivmxVoiceRegistry
from backend.runtime.client import SidecarError
from backend.services.aivmx_speech_service import AivmxSpeechService
from backend.services.streaming_audio_player import StreamingAudioPlayer
from backend.voice.storage import VoiceStoragePaths
from tests.test_aivmx_registry import MODEL_UUID, write_aivmx


VOICE_KEY = f"aivmx:{MODEL_UUID}:0"


def tone(frames: int = 3600, amplitude: int = 8000) -> bytes:
    return b"".join(struct.pack("<h", int(math.sin(index / 12) * amplitude)) for index in range(frames))


class FakeOutput:
    def __init__(self, **_config):
        self.blocks = []

    def start(self):
        pass

    def write(self, block):
        self.blocks.append(bytes(block))

    def stop(self):
        pass

    def close(self):
        pass


class Stream:
    sample_rate = 44100
    channels = 1
    sample_width = 2

    def __init__(self, pcm: bytes):
        self._blocks = iter((pcm[:2400], pcm[2400:]))

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._blocks)

    def close(self):
        pass


class FakeRuntimeRegistry:
    def __init__(self, record):
        self.record = record
        self.lookups = []

    def find_compatible(self, architecture, language):
        self.lookups.append((architecture, language))
        return self.record


class FakeManager:
    def __init__(self, pcm: bytes | None = None):
        self.pcm = tone() if pcm is None else pcm
        self.prepared = []
        self.loaded = []
        self.spoken = []
        self.cancelled = 0
        self.closed = False
        self.state = "stopped"

    def prepare(self, record):
        self.prepared.append(record)
        self.state = "ready"
        return self.status()

    def load_voice(self, request):
        self.loaded.append(dict(request))
        return {"status": "ready", "warmup_ms": 50, "providers": ["CPUExecutionProvider"], "vram_mb": 0}

    def synthesize(self, text, language="zh", **options):
        self.spoken.append((text, language, options))
        return Stream(self.pcm)

    def cancel(self):
        self.cancelled += 1

    def shutdown(self):
        self.closed = True
        self.state = "stopped"

    def status(self):
        return {
            "state": self.state,
            "metrics": {
                "first_pcm_ms": 210,
                "warmup_ms": 50,
                "rss_mb": 1800,
                "peak_rss_mb": 2050,
                "providers": ["CPUExecutionProvider"],
                "vram_mb": 0,
            },
        }


class AivmxSpeechServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        source = self.root / "haibara.aivmx"
        write_aivmx(source)
        self.registry = AivmxVoiceRegistry(self.paths, AivmxMetadataReader(max_model_bytes=1024 * 1024))
        self.registry.install(source, permissions_confirmed=True)
        self.runtime = SimpleNamespace(
            runtime_id="style-bert-vits2-cpu-test",
            path=self.root / "runtime",
            manifest=SimpleNamespace(build_version="dev", to_dict=lambda: {"runtime_id": "style-bert-vits2-cpu-test", "build_version": "dev"}),
        )
        self.runtime_registry = FakeRuntimeRegistry(self.runtime)
        self.health = AivmxHealthStore(self.paths)
        self.player = StreamingAudioPlayer(output_stream_factory=FakeOutput)

    def service(self, pcm: bytes | None = None):
        manager = FakeManager(pcm)
        service = AivmxSpeechService(
            self.paths,
            self.registry,
            self.runtime_registry,
            manager,
            self.player,
            self.health,
        )
        return service, manager

    def test_prepare_uses_chinese_cpu_pipeline_and_writes_separate_health_preview(self):
        service, manager = self.service()
        result = service.prepare(VOICE_KEY)

        self.assertEqual("ready", result["health"])
        self.assertEqual([("Style-Bert-VITS2", "zh-CN")], self.runtime_registry.lookups)
        self.assertEqual({"model_uuid": MODEL_UUID, "style_id": 0}, manager.loaded[0])
        self.assertEqual("zh", manager.spoken[0][1])
        self.assertEqual("准备完成，可以开始播报。", manager.spoken[0][0])
        self.assertEqual(90.0, manager.spoken[0][2]["request_timeout"])
        self.assertTrue((self.paths.voice_state / f"aivmx-{MODEL_UUID}-0.preview.wav").is_file())
        self.assertFalse((self.paths.aivmx_voices / MODEL_UUID / "preview.wav").exists())
        self.assertEqual(0, result["runtime"]["metrics"]["vram_mb"])

    def test_silent_preview_is_rejected_without_marking_voice_ready(self):
        service, manager = self.service(b"\x00\x00" * 3600)
        with self.assertRaises(SidecarError) as caught:
            service.prepare(VOICE_KEY)
        self.assertEqual("silent_audio", caught.exception.code)
        self.assertTrue(manager.closed)
        record = self.registry.get(MODEL_UUID)
        self.assertEqual("runtime_required", self.health.get(record, 0, self.runtime)["health"])

    def test_preview_releases_cpu_process_but_prepare_keeps_it_for_live_speech(self):
        service, manager = self.service()
        service.preview(VOICE_KEY, "中文试听")
        self.assertTrue(manager.closed)
        self.assertEqual("", service.active_voice_key)

        service, manager = self.service()
        service.prepare(VOICE_KEY)
        self.assertFalse(manager.closed)
        self.assertEqual(VOICE_KEY, service.active_voice_key)

    def test_speak_requires_this_runtime_preview_and_never_falls_back(self):
        service, manager = self.service()
        with self.assertRaises(SidecarError) as caught:
            service.speak("保持中文发音", VOICE_KEY)
        self.assertEqual("voice_not_ready", caught.exception.code)
        self.assertEqual([], manager.loaded)

        service.prepare(VOICE_KEY)
        result = service.speak("保持中文发音", VOICE_KEY, volume=0.8, rate=1.2)
        self.assertEqual(0, result["code"])
        self.assertEqual(("保持中文发音", "zh"), manager.spoken[-1][:2])
        self.assertEqual(1.2, manager.spoken[-1][2]["speed"])

    def test_runtime_change_invalidates_previous_preview(self):
        service, _manager = self.service()
        service.prepare(VOICE_KEY)
        service.shutdown()
        self.runtime_registry.record = SimpleNamespace(
            runtime_id="replacement-runtime",
            path=self.root / "replacement-runtime",
            manifest=SimpleNamespace(build_version="changed", to_dict=lambda: {"runtime_id": "replacement-runtime", "build_version": "changed"}),
        )
        with self.assertRaises(SidecarError) as caught:
            service.speak("中文", VOICE_KEY)
        self.assertEqual("voice_not_ready", caught.exception.code)

    def test_invalid_key_or_style_is_rejected_and_stop_is_idempotent(self):
        service, manager = self.service()
        for key in ("pack:x", f"aivmx:{MODEL_UUID}:9", "aivmx:not-a-uuid:0"):
            with self.subTest(key=key), self.assertRaises(SidecarError):
                service.prepare(key)
        service.stop()
        service.stop()
        self.assertEqual(2, manager.cancelled)


if __name__ == "__main__":
    unittest.main()
