import base64
import unittest

from backend.services.system_speech_service import SystemSpeechService


class FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.terminated = False

    def communicate(self):
        return "", ""

    def poll(self):
        return None if not self.terminated else self.returncode

    def terminate(self):
        self.terminated = True


class SystemSpeechServiceTests(unittest.TestCase):
    def test_discovers_linux_espeak_voices(self):
        voice_output = """Pty Language Age/Gender VoiceName File Other Languages
 5  zh             M  Mandarin         cmn
 5  ja             M  Japanese         ja
"""
        service = SystemSpeechService(
            platform_name="linux",
            which=lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None,
            runner=lambda *args, **kwargs: FakeCompletedProcess(stdout=voice_output),
        )

        result = service.get_capabilities()

        self.assertEqual(result["code"], 0)
        self.assertTrue(result["data"]["supported"])
        self.assertEqual(result["data"]["engine"], "espeak-ng")
        self.assertEqual(
            result["data"]["voices"][0],
            {
                "name": "Mandarin",
                "voiceURI": "cmn",
                "lang": "zh",
                "default": True,
            },
        )

    def test_speaks_with_rate_volume_and_voice_without_a_gpu(self):
        calls = []
        process = FakeProcess()

        def popen(args, **kwargs):
            calls.append((args, kwargs))
            return process

        service = SystemSpeechService(
            platform_name="linux",
            which=lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None,
            popen_factory=popen,
        )

        result = service.speak("桌面语音测试", voice_uri="cmn", rate=1.2, volume=0.6)

        self.assertEqual(result, {"code": 0})
        self.assertEqual(
            calls[0][0],
            [
                "/usr/bin/espeak-ng",
                "-s",
                "210",
                "-a",
                "120",
                "-v",
                "cmn",
                "--",
                "桌面语音测试",
            ],
        )
        self.assertEqual(calls[0][1]["encoding"], "utf-8")
        self.assertEqual(calls[0][1]["errors"], "replace")

    def test_reports_unsupported_when_no_system_engine_exists(self):
        service = SystemSpeechService(
            platform_name="linux",
            which=lambda _name: None,
        )

        result = service.get_capabilities()

        self.assertFalse(result["data"]["supported"])
        self.assertIn("espeak", result["data"]["error"])

    def test_uses_windows_sapi_from_wsl_when_linux_tts_is_missing(self):
        powershell_output = (
            '[{"name":"Microsoft Huihui Desktop",'
            '"voiceURI":"Microsoft Huihui Desktop",'
            '"lang":"zh-CN","default":true}]'
        )

        def which(name):
            if name == "powershell.exe":
                return "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
            return None

        service = SystemSpeechService(
            platform_name="linux",
            which=which,
            runner=lambda *args, **kwargs: FakeCompletedProcess(stdout=powershell_output),
        )

        result = service.get_capabilities()

        self.assertTrue(result["data"]["supported"])
        self.assertEqual(result["data"]["engine"], "sapi")
        self.assertEqual(result["data"]["voices"][0]["lang"], "zh-CN")

    def test_passes_unicode_text_to_sapi_with_an_encoded_command(self):
        calls = []

        def popen(args, **kwargs):
            calls.append((args, kwargs))
            return FakeProcess()

        service = SystemSpeechService(
            platform_name="win32",
            which=lambda name: "powershell.exe" if name == "powershell.exe" else None,
            popen_factory=popen,
        )

        result = service.speak(
            "音声テストです",
            voice_uri="Microsoft Haruka Desktop",
            rate=1.0,
            volume=1.0,
        )

        self.assertEqual(result, {"code": 0})
        self.assertIn("-EncodedCommand", calls[0][0])
        script = base64.b64decode(calls[0][0][-1]).decode("utf-16-le")
        encoded_text = base64.b64encode("音声テストです".encode("utf-8")).decode("ascii")
        encoded_voice = base64.b64encode(
            "Microsoft Haruka Desktop".encode("utf-8")
        ).decode("ascii")
        self.assertIn(encoded_text, script)
        self.assertIn(encoded_voice, script)
        self.assertNotIn("BILI_LIVE_TTS_TEXT", script)


if __name__ == "__main__":
    unittest.main()
