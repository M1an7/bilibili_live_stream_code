import json
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path


from backend.voice.builder import VoiceBuildRequest, VoiceJobCancelled, VoicePackBuilder
from backend.voice.manifest import VoiceContractError
from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import VoicePackValidator


class VoicePackBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        self.gpt = self.root / "source.ckpt"
        self.sovits = self.root / "source.pth"
        self.reference = self.root / "reference.wav"
        self.license = self.root / "permission.txt"
        self.gpt.write_bytes(b"gpt-opaque-weights")
        self.sovits.write_bytes(b"sovits-opaque-weights")
        self.license.write_text("Authorized for training, speech and livestream use.", encoding="utf-8")
        with wave.open(str(self.reference), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(32_000)
            output.writeframes(b"\x00\x00" * 3_200)
        self.validator = VoicePackValidator()
        self.builder = VoicePackBuilder(self.paths, self.validator)

    def tearDown(self):
        self.temp.cleanup()

    def make_request(self):
        return VoiceBuildRequest(
            voice_id="haibara-jp",
            display_name="灰原哀（日语）",
            model_version="v2Pro",
            gpt_path=self.gpt,
            sovits_path=self.sovits,
            reference_audio_path=self.reference,
            reference_text="今日何食べたい？",
            license_path=self.license,
        )

    def test_builds_standard_pack_with_real_hashes(self):
        built = self.builder.build(self.make_request())
        manifest = json.loads((built.staging_path / "manifest.json").read_text("utf-8"))
        self.assertEqual("gpt-sovits-gpu", manifest["engine"])
        self.assertEqual(manifest["models"]["gpt"], "model/gpt.ckpt")
        self.assertTrue(manifest["files"]["model/gpt.ckpt"].startswith("sha256:"))
        self.assertEqual(len(manifest["files"]["model/gpt.ckpt"]), 71)
        self.assertEqual(built.validation.health, "runtime_required")
        self.assertEqual((built.staging_path / "reference.txt").read_text("utf-8"), "今日何食べたい？")

    def test_rejects_missing_license_and_cleans_staging(self):
        request = replace(self.make_request(), license_path=self.root / "missing.txt")
        with self.assertRaisesRegex(VoiceContractError, "授权"):
            self.builder.build(request)
        self.assertEqual(list(self.paths.staging.iterdir()), [])

    def test_rejects_symlinked_source(self):
        link = self.root / "linked.pth"
        try:
            link.symlink_to(self.sovits)
        except OSError:
            self.skipTest("当前文件系统不允许创建符号链接")
        request = replace(self.make_request(), sovits_path=link)
        with self.assertRaisesRegex(VoiceContractError, "符号链接"):
            self.builder.build(request)

    def test_rejects_non_pcm_wav_without_touching_source(self):
        original = b"not-a-wave"
        self.reference.write_bytes(original)
        with self.assertRaisesRegex(VoiceContractError, "WAV"):
            self.builder.build(self.make_request())
        self.assertEqual(self.reference.read_bytes(), original)

    def test_cancellation_removes_only_its_staging_directory(self):
        keep = self.paths.staging / "keep-me"
        keep.mkdir()
        with self.assertRaises(VoiceJobCancelled):
            self.builder.build(self.make_request(), cancelled=lambda: True)
        self.assertTrue(keep.is_dir())
        self.assertEqual([path.name for path in self.paths.staging.iterdir()], ["keep-me"])


if __name__ == "__main__":
    unittest.main()
