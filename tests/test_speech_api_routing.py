from __future__ import annotations

import tempfile
import unittest
import sys
import types
from pathlib import Path

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
danmu_stub = types.ModuleType("backend.services.danmu_service")
danmu_stub.DanmuService = object
sys.modules.setdefault("backend.services.danmu_service", danmu_stub)

from backend.api_service import ApiService
from backend.runtime.client import SidecarError


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

    def stop(self):
        self.stopped += 1
        return {"code": 0}

    def shutdown(self):
        self.closed += 1


class FakeRuntimeRegistry:
    def status(self):
        return {"state": "ready", "runtimes": [{"runtime_id": "test"}]}


class SpeechApiRoutingTests(unittest.TestCase):
    def setUp(self):
        self.api = ApiService.__new__(ApiService)
        self.api.speech_service = FakeSystemSpeech()
        self.api.personalized_speech = FakePersonalizedSpeech()
        self.api.runtime_registry = FakeRuntimeRegistry()

    def test_system_voice_only_reaches_system_speech(self):
        result = self.api.speak_text("中文测试", "sapi-uri", 1.1, 0.8, "system:sapi-uri")
        self.assertEqual("system", result["engine"])
        self.assertEqual(1, len(self.api.speech_service.calls))
        self.assertEqual([], self.api.personalized_speech.calls)

    def test_pack_voice_only_reaches_gpu_service_with_complete_key(self):
        result = self.api.speak_text("こんにちは", "", 1.0, 0.7, "pack:haibara-jp")
        self.assertEqual("gpu", result["engine"])
        self.assertEqual([], self.api.speech_service.calls)
        self.assertEqual(("こんにちは", "pack:haibara-jp", 1.0, 0.7), self.api.personalized_speech.calls[0])

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
        self.assertEqual("ready", self.api.get_gpu_runtime_status()["data"]["state"])
        self.api.release_personalized_voice()
        self.assertEqual(1, self.api.personalized_speech.closed)

    def test_stop_speech_cancels_both_backends(self):
        self.api.stop_speech()
        self.assertEqual(1, self.api.speech_service.stopped)
        self.assertEqual(1, self.api.personalized_speech.stopped)


if __name__ == "__main__":
    unittest.main()
