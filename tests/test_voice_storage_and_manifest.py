import tempfile
import unittest
from pathlib import Path


from backend.voice.manifest import VoiceContractError, VoiceManifest
from backend.voice.storage import VoiceStoragePaths


def valid_manifest_payload():
    return {
        "schema_version": 1,
        "voice_id": "haibara-jp",
        "display_name": "灰原哀（日语）",
        "engine": "gpt-sovits-cpu",
        "engine_api_version": 1,
        "model_version": "v2Pro",
        "source_language": "ja",
        "supported_output_languages": ["ja"],
        "models": {"gpt": "model/gpt.ckpt", "sovits": "model/sovits.pth"},
        "reference_audio": "reference.wav",
        "reference_text": "reference.txt",
        "preview_audio": None,
        "license_file": "LICENSE.txt",
        "usage": ["ai_training", "synthetic_speech", "public_livestream"],
        "created_at": "2026-08-31T00:00:00Z",
        "files": {
            "model/gpt.ckpt": "sha256:" + "0" * 64,
            "model/sovits.pth": "sha256:" + "1" * 64,
            "reference.wav": "sha256:" + "2" * 64,
            "reference.txt": "sha256:" + "3" * 64,
            "LICENSE.txt": "sha256:" + "4" * 64,
        },
    }


class VoiceStoragePathsTests(unittest.TestCase):
    def test_windows_storage_uses_local_app_data(self):
        paths = VoiceStoragePaths.resolve(
            platform_name="win32",
            env={"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"},
        )
        self.assertEqual(
            paths.root,
            Path(r"C:\Users\tester\AppData\Local") / "BiliLiveTool",
        )

    def test_development_override_takes_precedence_and_ensure_creates_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            paths = VoiceStoragePaths.resolve(
                platform_name="linux",
                env={"BILILIVE_DATA_HOME": str(root)},
            ).ensure()
            self.assertEqual(paths.root, root)
            for path in (paths.voices, paths.runtimes, paths.speech_cache, paths.staging, paths.logs):
                self.assertTrue(path.is_dir())


class VoiceManifestTests(unittest.TestCase):
    def test_round_trips_a_valid_manifest(self):
        manifest = VoiceManifest.from_dict(valid_manifest_payload())
        self.assertEqual(manifest.voice_id, "haibara-jp")
        self.assertEqual(manifest.relative_files()["gpt"], "model/gpt.ckpt")
        self.assertEqual(VoiceManifest.from_dict(manifest.to_dict()), manifest)

    def test_accepts_the_canonical_gpu_engine(self):
        payload = valid_manifest_payload()
        payload["engine"] = "gpt-sovits-gpu"
        self.assertEqual("gpt-sovits-gpu", VoiceManifest.from_dict(payload).engine)

    def test_manifest_rejects_path_traversal(self):
        payload = valid_manifest_payload()
        payload["models"]["gpt"] = "../outside.ckpt"
        with self.assertRaisesRegex(VoiceContractError, "相对路径"):
            VoiceManifest.from_dict(payload)

    def test_manifest_requires_japanese_output_for_this_pack(self):
        payload = valid_manifest_payload()
        payload["supported_output_languages"] = []
        with self.assertRaisesRegex(VoiceContractError, "输出语言"):
            VoiceManifest.from_dict(payload)

    def test_manifest_rejects_unsupported_model_and_missing_usage(self):
        payload = valid_manifest_payload()
        payload["model_version"] = "v4"
        with self.assertRaisesRegex(VoiceContractError, "模型版本"):
            VoiceManifest.from_dict(payload)

        payload = valid_manifest_payload()
        payload["usage"] = ["ai_training"]
        with self.assertRaisesRegex(VoiceContractError, "使用授权"):
            VoiceManifest.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
