import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_windows.ps1"
RELEASE_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release.yml"
PORTABLE_GUIDE = PROJECT_ROOT / "WINDOWS-README.txt"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
VERSION_FILE = PROJECT_ROOT / "VERSION"


class WindowsPackagingTests(unittest.TestCase):
    def test_builds_a_single_windowed_executable_with_runtime_assets(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("[string]$PythonPath", script)
        self.assertIn("Requested Windows build Python was not found", script)
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

    def test_windows_release_keeps_files_in_a_folder_with_a_usage_notice(self):
        self.assertTrue(PORTABLE_GUIDE.is_file())
        guide = PORTABLE_GUIDE.read_text(encoding="utf-8")
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("独立文件夹", guide)
        self.assertIn("logs", guide)
        self.assertIn("config.json", guide)
        self.assertIn('mkdir "$PACKAGE_DIR"', workflow)
        self.assertIn('WINDOWS-README.txt "$PACKAGE_DIR/README-Windows.txt"', workflow)

    def test_release_uses_a_python_version_supported_by_the_voice_control_plane(self):
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn("python-version: '3.12'", workflow)
        self.assertIn('requires-python = ">=3.12"', pyproject)

    def test_project_metadata_matches_the_release_version(self):
        release_version = VERSION_FILE.read_text(encoding="utf-8").strip().removeprefix("v")
        pyproject = PYPROJECT.read_text(encoding="utf-8")

        self.assertIn(f'version = "{release_version}"', pyproject)


if __name__ == "__main__":
    unittest.main()
