import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_windows.ps1"


class WindowsPackagingTests(unittest.TestCase):
    def test_builds_a_single_windowed_executable_with_runtime_assets(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"--onefile"', script)
        self.assertIn('"--windowed"', script)
        self.assertIn('"frontend/dist;frontend/dist"', script)
        self.assertIn('"bilibili.ico;."', script)
        self.assertIn('"VERSION;."', script)

    def test_does_not_bundle_private_or_development_files(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        for private_path in (
            "cookies.txt",
            "config.json",
            "last_settings.json",
            "frontend/node_modules",
            "tests/",
        ):
            self.assertNotIn(private_path, script)

    def test_keeps_the_desktop_speech_bridge_available(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"backend.services.system_speech_service"', script)

    def test_collects_voice_import_code_without_bundling_voice_models(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"--collect-submodules", "backend.voice"', script)
        for private_asset in ("voice/", "*.ckpt", "*.pth", "reference.wav"):
            self.assertNotIn(private_asset, script)


if __name__ == "__main__":
    unittest.main()
