from __future__ import annotations

import json
import math
import struct
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from backend.runtime.client import SidecarError
from backend.services.personalized_speech_service import PersonalizedSpeechService
from backend.services.streaming_audio_player import AudioPlaybackError, StreamingAudioPlayer
from backend.voice.builder import VoiceBuildRequest, VoicePackBuilder
from backend.voice.health import VoiceHealthStore
from backend.voice.registry import VoicePackRegistry
from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import VoicePackValidator


def tone(frames=3200, amplitude=8000):
    return b"".join(struct.pack("<h", int(math.sin(index / 12) * amplitude)) for index in range(frames))


class FakeOutput:
    def __init__(self, **config):
        self.config = config
        self.blocks = []
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def write(self, block):
        self.blocks.append(bytes(block))

    def stop(self):
        self.stopped = True

    def close(self):
        pass


class Stream:
    sample_rate = 32000
    channels = 1
    sample_width = 2

    def __init__(self, blocks):
        self.blocks = iter(blocks)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.blocks)

    def close(self):
        pass


class FakeRuntimeRegistry:
    def __init__(self, record):
        self.record = record

    def find_compatible(self, model_version, language="ja"):
        return self.record


class FakeManager:
    def __init__(self, pcm=None):
        self.pcm = pcm if pcm is not None else tone()
        self.prepared = []
        self.loaded = []
        self.spoken = []
        self.cancelled = 0
        self.closed = False

    def prepare(self, record):
        self.prepared.append(record)
        return {"state": "ready"}

    def load_voice(self, request):
        self.loaded.append(request)
        return {"status": "ready", "warmup_ms": 15, "peak_vram_mb": 820}

    def synthesize(self, text, language="ja", **options):
        self.spoken.append((text, language, options))
        return Stream([self.pcm[:2000], self.pcm[2000:]])

    def cancel(self):
        self.cancelled += 1

    def shutdown(self):
        self.closed = True

    def status(self):
        return {"state": "ready", "metrics": {"first_pcm_ms": 120, "peak_vram_mb": 820}}


class PersonalizedSpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        gpt, sovits, license_path, reference = [self.root / name for name in ("a.ckpt", "a.pth", "LICENSE.txt", "ref.wav")]
        gpt.write_bytes(b"gpt")
        sovits.write_bytes(b"sovits")
        license_path.write_text("authorized", "utf-8")
        with wave.open(str(reference), "wb") as output:
            output.setparams((1, 2, 32000, 0, "NONE", ""))
            output.writeframes(tone())
        validator = VoicePackValidator()
        builder = VoicePackBuilder(self.paths, validator)
        self.registry = VoicePackRegistry(self.paths, validator)
        built = builder.build(VoiceBuildRequest("haibara-jp", "灰原哀（日语）", "v2Pro", gpt, sovits, reference, "今日何食べたい？", license_path))
        self.registry.install_staged(built)
        self.runtime = SimpleNamespace(runtime_id="test-cu126", manifest=SimpleNamespace(build_version="dev"), path=self.root / "runtime")
        self.health = VoiceHealthStore(self.paths)
        self.outputs = []

        def output_factory(**config):
            output = FakeOutput(**config)
            self.outputs.append(output)
            return output

        self.player = StreamingAudioPlayer(output_stream_factory=output_factory)

    def service(self, pcm=None):
        manager = FakeManager(pcm)
        service = PersonalizedSpeechService(
            self.paths,
            self.registry,
            FakeRuntimeRegistry(self.runtime),
            manager,
            self.player,
            self.health,
        )
        return service, manager

    def test_player_starts_on_first_chunk_scales_volume_and_captures_pcm(self):
        result = self.player.play(Stream([struct.pack("<hhh", 1000, -1000, 2000)]), volume=0.5, capture=True)
        self.assertTrue(self.outputs[0].started)
        self.assertEqual((500, -500, 1000), struct.unpack("<hhh", b"".join(self.outputs[0].blocks)))
        self.assertEqual(b"".join(self.outputs[0].blocks), result.pcm)

    def test_player_rejects_invalid_alignment_and_honors_cancellation(self):
        with self.assertRaises(AudioPlaybackError) as caught:
            self.player.play(Stream([b"\x00"]), capture=True)
        self.assertEqual("invalid_pcm", caught.exception.code)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(AudioPlaybackError) as caught:
            self.player.play(Stream([tone(10)]), token=cancelled)
        self.assertEqual("cancelled", caught.exception.code)

    def test_prepare_loads_only_the_validated_voice_id_and_promotes_real_preview(self):
        service, manager = self.service()
        result = service.prepare("pack:haibara-jp")
        self.assertEqual("ready", result["health"])
        request = manager.loaded[0]
        self.assertEqual({"voice_id": "haibara-jp"}, request)
        self.assertTrue((self.paths.voices / "haibara-jp" / "preview.wav").is_file())
        self.assertEqual(90.0, manager.spoken[0][2]["request_timeout"])
        manifest = json.loads((self.paths.voices / "haibara-jp" / "manifest.json").read_text("utf-8"))
        self.assertEqual("preview.wav", manifest["preview_audio"])
        self.assertIn("preview.wav", manifest["files"])
        self.registry.refresh()
        self.assertTrue(self.registry.get("haibara-jp").selectable)

    def test_silent_preview_is_rejected_and_never_marked_ready(self):
        service, manager = self.service(b"\x00\x00" * 3200)
        with self.assertRaises(SidecarError) as caught:
            service.prepare("pack:haibara-jp")
        self.assertEqual("silent_audio", caught.exception.code)
        self.assertTrue(manager.closed)
        self.registry.refresh()
        self.assertEqual("runtime_required", self.registry.get("haibara-jp").health)

    def test_transient_preview_promotes_health_then_releases_the_gpu(self):
        service, manager = self.service()
        result = service.preview("pack:haibara-jp", "テスト")
        self.assertEqual("ready", result["health"])
        self.assertTrue(manager.closed)
        self.assertEqual("", service.active_voice_key)

    def test_health_is_invalidated_when_voice_manifest_changes(self):
        service, _manager = self.service()
        service.prepare("pack:haibara-jp")
        pack = self.paths.voices / "haibara-jp"
        manifest = json.loads((pack / "manifest.json").read_text("utf-8"))
        manifest["display_name"] = "changed"
        (pack / "manifest.json").write_text(json.dumps(manifest), "utf-8")
        self.registry.refresh()
        self.assertEqual("runtime_required", self.registry.get("haibara-jp").health)

    def test_speak_has_no_system_fallback_and_shutdown_releases_runtime(self):
        service, manager = self.service()
        service.prepare("pack:haibara-jp")
        result = service.speak("弹幕テスト", "pack:haibara-jp", volume=0.8)
        self.assertEqual(0, result["code"])
        service.stop()
        self.assertGreater(manager.cancelled, 0)
        service.shutdown()
        self.assertTrue(manager.closed)

    def test_speak_rejects_a_pack_that_has_not_passed_this_runtime_preview(self):
        service, manager = self.service()
        with self.assertRaises(SidecarError) as caught:
            service.speak("テスト", "pack:haibara-jp")
        self.assertEqual("voice_not_ready", caught.exception.code)
        self.assertEqual([], manager.loaded)

    def test_runtime_change_invalidates_a_prepared_voice_before_reload(self):
        service, manager = self.service()
        service.prepare("pack:haibara-jp")
        service.shutdown()
        service.runtime_registry.record = SimpleNamespace(
            runtime_id="replacement-cu126",
            manifest=SimpleNamespace(build_version="changed"),
            path=self.root / "replacement-runtime",
        )
        with self.assertRaises(SidecarError) as caught:
            service.speak("テスト", "pack:haibara-jp")
        self.assertEqual("voice_not_ready", caught.exception.code)

    def test_registry_hides_ready_pack_when_its_runtime_is_removed(self):
        service, _manager = self.service()
        service.prepare("pack:haibara-jp")
        self.registry.set_runtime_registry(FakeRuntimeRegistry(self.runtime))
        self.assertTrue(self.registry.get("haibara-jp").selectable)
        self.registry.set_runtime_registry(FakeRuntimeRegistry(None))
        self.assertEqual("runtime_required", self.registry.get("haibara-jp").health)
        self.assertFalse(self.registry.get("haibara-jp").selectable)

    def test_unpreviewed_pack_distinguishes_a_compatible_runtime_from_a_missing_one(self):
        self.registry.set_runtime_registry(FakeRuntimeRegistry(self.runtime))
        record = self.registry.get("haibara-jp")
        self.assertEqual("runtime_required", record.health)
        self.assertEqual("兼容 GPU 运行时已就绪，等待 GPU 试听验证", record.message)


if __name__ == "__main__":
    unittest.main()
