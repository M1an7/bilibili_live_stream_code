import tempfile
import time
import unittest
import wave
from dataclasses import replace
from pathlib import Path


from backend.voice.builder import BuiltVoicePack, VoiceBuildRequest, VoicePackBuilder
from backend.voice.jobs import VoiceJobManager
from backend.voice.manifest import VoiceContractError
from backend.voice.registry import VoicePackRegistry
from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import VoicePackValidator, VoiceValidationResult


def wait_for_terminal_job(manager, job_id, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if result["status"] in {"completed", "failed", "cancelled"}:
            return result
        time.sleep(0.01)
    raise AssertionError("background voice job did not finish")


class VoiceRegistryAndJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        self.gpt = self.root / "voice.ckpt"
        self.sovits = self.root / "voice.pth"
        self.reference = self.root / "reference.wav"
        self.license = self.root / "LICENSE.txt"
        self.gpt.write_bytes(b"gpt")
        self.sovits.write_bytes(b"sovits")
        self.license.write_text("authorized", "utf-8")
        with wave.open(str(self.reference), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(32_000)
            output.writeframes(b"\x00\x00" * 3_200)
        self.validator = VoicePackValidator()
        self.builder = VoicePackBuilder(self.paths, self.validator)
        self.registry = VoicePackRegistry(self.paths, self.validator)
        self.jobs = VoiceJobManager(self.builder, self.registry)

    def tearDown(self):
        self.jobs.shutdown()
        self.temp.cleanup()

    def request(self, voice_id="haibara-jp", display_name="灰原哀（日语）"):
        return VoiceBuildRequest(
            voice_id=voice_id,
            display_name=display_name,
            model_version="v2Pro",
            gpt_path=self.gpt,
            sovits_path=self.sovits,
            reference_audio_path=self.reference,
            reference_text="今日何食べたい？",
            license_path=self.license,
        )

    def request_dict(self):
        request = self.request()
        return {
            "voice_id": request.voice_id,
            "display_name": request.display_name,
            "model_version": request.model_version,
            "gpt_path": str(request.gpt_path),
            "sovits_path": str(request.sovits_path),
            "reference_audio_path": str(request.reference_audio_path),
            "reference_text": request.reference_text,
            "license_path": str(request.license_path),
            "source_language": "ja",
            "supported_output_languages": ["ja"],
        }

    def test_atomic_install_is_management_only_until_runtime_ready(self):
        built = self.builder.build(self.request())
        record = self.registry.install_staged(built)
        self.assertEqual(record.health, "runtime_required")
        self.assertFalse(record.selectable)
        self.assertTrue((self.paths.voices / record.voice_id / "manifest.json").is_file())
        self.assertEqual(self.registry.list_packs()[0]["voice_key"], "pack:haibara-jp")

    def test_failed_update_preserves_existing_pack(self):
        first = self.registry.install_staged(self.builder.build(self.request()))
        bad_staging = self.paths.staging / "invalid-update"
        bad_staging.mkdir()
        invalid = BuiltVoicePack(
            staging_path=bad_staging,
            manifest=first.manifest,
            validation=VoiceValidationResult(False, "invalid", "invalid", "invalid", first.manifest),
        )
        with self.assertRaises(VoiceContractError):
            self.registry.install_staged(invalid)
        self.assertEqual(self.registry.get(first.voice_id).manifest.created_at, first.manifest.created_at)

    def test_background_job_reports_structured_completion(self):
        job_id = self.jobs.start_build(self.request_dict())
        result = wait_for_terminal_job(self.jobs, job_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["health"], "runtime_required")
        self.assertEqual(result["stage"], "done")
        self.assertEqual(result["progress"], 100)

    def test_background_job_maps_contract_errors(self):
        request = self.request_dict()
        request["license_path"] = str(self.root / "missing.txt")
        result = wait_for_terminal_job(self.jobs, self.jobs.start_build(request))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "missing_source")


if __name__ == "__main__":
    unittest.main()
