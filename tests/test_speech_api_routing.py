from __future__ import annotations

import tempfile
import unittest
import sys
import types
from unittest.mock import patch
from pathlib import Path

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
danmu_stub = types.ModuleType("backend.services.danmu_service")
danmu_stub.DanmuService = object
sys.modules.setdefault("backend.services.danmu_service", danmu_stub)

from backend.api_service import ApiService
from backend.runtime.client import SidecarError
from backend.runtime.manifest import RuntimeContractError


class FakeSystemSpeech:
    def __init__(self):
        self.calls = []
        self.stopped = 0

    def speak(self, *args):
        self.calls.append(args)
        return {"code": 0, "engine": "system"}

    def stop(self):
        self.stopped += 1
        return {"code": 0}


class FakePersonalizedSpeech:
    def __init__(self):
        self.calls = []
        self.prepares = []
        self.stopped = 0
        self.closed = 0

    def speak(self, text, voice_key, volume=1.0, rate=1.0):
        self.calls.append((text, voice_key, rate, volume))
        return {"code": 0, "engine": "gpu"}

    def prepare(self, voice_key, preview_text=""):
        self.prepares.append((voice_key, preview_text))
        return {"health": "ready"}

    def preview(self, voice_key, preview_text=""):
        self.prepares.append((f"preview:{voice_key}", preview_text))
        return {"health": "ready"}

    def stop(self):
        self.stopped += 1
        return {"code": 0}

    def shutdown(self):
        self.closed += 1


class FakeRuntimeRegistry:
    def status(self):
        return {"state": "ready", "runtimes": [{"runtime_id": "test"}]}


class FakeAivmxSpeech(FakePersonalizedSpeech):
    def speak(self, text, voice_key, volume=1.0, rate=1.0):
        self.calls.append((text, voice_key, rate, volume))
        return {"code": 0, "engine": "cpu"}


class SpeechApiRoutingTests(unittest.TestCase):
    def setUp(self):
        self.api = ApiService.__new__(ApiService)
        self.api.speech_service = FakeSystemSpeech()
        self.api.personalized_speech = FakePersonalizedSpeech()
        self.api.aivmx_speech = FakeAivmxSpeech()
        self.api.runtime_registry = FakeRuntimeRegistry()

    def test_system_voice_only_reaches_system_speech(self):
        result = self.api.speak_text("中文测试", "sapi-uri", 1.1, 0.8, "system:sapi-uri")
        self.assertEqual("system", result["engine"])
        self.assertEqual(1, len(self.api.speech_service.calls))
        self.assertEqual([], self.api.personalized_speech.calls)
        self.assertEqual([], self.api.aivmx_speech.calls)

    def test_pack_voice_only_reaches_gpu_service_with_complete_key(self):
        result = self.api.speak_text("こんにちは", "", 1.0, 0.7, "pack:haibara-jp")
        self.assertEqual("gpu", result["engine"])
        self.assertEqual([], self.api.speech_service.calls)
        self.assertEqual(("こんにちは", "pack:haibara-jp", 1.0, 0.7), self.api.personalized_speech.calls[0])
        self.assertEqual([], self.api.aivmx_speech.calls)

    def test_aivmx_voice_only_reaches_cpu_service_and_preserves_chinese(self):
        key = "aivmx:11111111-2222-4333-8444-555555555555:0"
        result = self.api.speak_text("保持中文发音", "", 1.1, 0.6, key)
        self.assertEqual("cpu", result["engine"])
        self.assertEqual(("保持中文发音", key, 1.1, 0.6), self.api.aivmx_speech.calls[0])
        self.assertEqual([], self.api.speech_service.calls)
        self.assertEqual([], self.api.personalized_speech.calls)

    def test_unknown_personalized_prefix_is_rejected_without_fallback(self):
        result = self.api.speak_text("不应播放", "", 1, 1, "unknown:voice")
        self.assertEqual(-1, result["code"])
        self.assertEqual("invalid_voice_key", result["error"]["code"])
        self.assertEqual([], self.api.speech_service.calls)
        self.assertEqual([], self.api.personalized_speech.calls)
        self.assertEqual([], self.api.aivmx_speech.calls)

    def test_pack_failure_is_structured_and_never_falls_back(self):
        def fail(*_args, **_kwargs):
            raise SidecarError("cuda_out_of_memory", "显存不足")

        self.api.personalized_speech.speak = fail
        result = self.api.speak_text("test", "", 1, 1, "pack:haibara-jp")
        self.assertEqual(-1, result["code"])
        self.assertEqual("cuda_out_of_memory", result["error"]["code"])
        self.assertEqual([], self.api.speech_service.calls)

    def test_prepare_preview_release_and_status_api(self):
        self.assertEqual("ready", self.api.prepare_voice("pack:haibara-jp")["data"]["health"])
        self.assertEqual("ready", self.api.preview_voice("pack:haibara-jp", "テスト")["data"]["health"])
        self.assertIn(("preview:pack:haibara-jp", "テスト"), self.api.personalized_speech.prepares)
        self.assertEqual("ready", self.api.get_gpu_runtime_status()["data"]["state"])
        self.api.release_personalized_voice()
        self.assertEqual(1, self.api.personalized_speech.closed)

    def test_stop_speech_cancels_all_three_backends(self):
        self.api.stop_speech()
        self.assertEqual(1, self.api.speech_service.stopped)
        self.assertEqual(1, self.api.personalized_speech.stopped)
        self.assertEqual(1, self.api.aivmx_speech.stopped)

    def test_runtime_root_reconfigure_keeps_non_runtime_voice_build_jobs_alive(self):
        class Jobs:
            def __init__(self):
                self.closed = 0

            def shutdown(self):
                self.closed += 1

        self.api.voice_jobs = Jobs()
        self.api.runtime_jobs = Jobs()
        self.api.aivmx_jobs = Jobs()
        self.api.cpu_runtime_jobs = Jobs()

        self.api._shutdown_voice_services(include_voice_jobs=False)

        self.assertEqual(0, self.api.voice_jobs.closed)
        self.assertEqual(1, self.api.runtime_jobs.closed)
        self.assertEqual(1, self.api.aivmx_jobs.closed)
        self.assertEqual(1, self.api.cpu_runtime_jobs.closed)

    def test_runtime_root_preflight_requires_a_real_writable_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "runtime-data"
            result = ApiService._preflight_runtime_root(target, minimum_free_bytes=1)
            self.assertEqual(target.resolve(), result)
            self.assertEqual([], list(target.glob(".bililive-write-probe-*")))

    def test_runtime_root_preflight_rejects_insufficient_space(self):
        with tempfile.TemporaryDirectory() as temp:
            usage = types.SimpleNamespace(total=100, used=100, free=0)
            with patch("backend.api_service.shutil.disk_usage", return_value=usage):
                with self.assertRaises(RuntimeContractError) as caught:
                    ApiService._preflight_runtime_root(Path(temp), minimum_free_bytes=1)
            self.assertEqual("insufficient_disk_space", caught.exception.code)

    def test_runtime_root_preflight_rejects_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real"
            real.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable")
            with self.assertRaises(RuntimeContractError) as caught:
                ApiService._preflight_runtime_root(link, minimum_free_bytes=1)
            self.assertEqual("invalid_runtime_root", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
