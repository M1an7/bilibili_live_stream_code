from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from backend.runtime.registry import RuntimeVerifier
from backend.runtime.keys import release_public_key


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_gpu_runtime.ps1"
SIGN = ROOT / "scripts" / "sign_runtime_manifest.py"
VERIFY = ROOT / "scripts" / "verify_gpu_runtime.ps1"
PIN = (ROOT / "runtime" / "gpt_sovits_gpu" / "PINNED_GPT_SOVITS_COMMIT").read_text("ascii").strip()


class GpuRuntimePackagingTests(unittest.TestCase):
    def test_bundled_release_public_key_is_a_valid_ed25519_key(self):
        public = release_public_key()
        self.assertEqual(32, len(public))
        Ed25519PublicKey.from_public_bytes(public)

    def test_builder_is_data_disk_first_pinned_cu126_and_separate(self):
        script = BUILD.read_text("utf-8")
        self.assertIn("[string]$BuildRoot", script)
        self.assertIn("Get-PSDrive", script)
        self.assertIn("PINNED_GPT_SOVITS_COMMIT", script)
        self.assertIn("git", script)
        self.assertIn("3.10", script)
        self.assertIn("cu126", script.lower())
        self.assertIn("pretrained_models.zip", script)
        self.assertIn("open_jtalk_dic_utf_8-1.11", script)
        self.assertIn("ffmpeg.exe", script.lower())
        self.assertIn("BiliLiveTool-GPT-SoVITS-CU126", script)
        self.assertIn("Compress-Archive", script)
        self.assertIn("PythonInstallerSha256", script)
        self.assertIn("Start-Process", script)
        self.assertIn("python-3.10.11-amd64.exe", script)
        self.assertIn("PretrainedModelsArchive", script)

    def test_builder_requires_base_models_and_preserves_upstream_license(self):
        script = BUILD.read_text("utf-8")
        for required in (
            "chinese-hubert-base",
            "chinese-roberta-wwm-ext-large",
            "pretrained_models/sv",
            "pretrained_models/v2Pro",
            "fast_langdetect/lid.176.bin",
            "LICENSE",
        ):
            self.assertIn(required, script)

    def test_signer_produces_a_verifiable_complete_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            (root / "engine").mkdir(parents=True)
            (root / "python" / "Scripts").mkdir(parents=True)
            (root / "upstream" / "GPT_SoVITS" / "pretrained_models" / "sv").mkdir(parents=True)
            (root / "engine" / "sidecar.py").write_text("print('ok')", "utf-8")
            (root / "engine" / "protocol.py").write_text("# protocol", "utf-8")
            (root / "python" / "Scripts" / "python.exe").write_bytes(b"python")
            (root / "upstream" / "LICENSE").write_text("MIT", "utf-8")
            (root / "upstream" / "GPT_SoVITS" / "pretrained_models" / "sv" / "model.ckpt").write_bytes(b"model")
            private_key = Ed25519PrivateKey.generate()
            private_path = Path(temp) / "private.pem"
            private_path.write_bytes(private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            subprocess.run(
                [
                    sys.executable, str(SIGN), "--runtime-root", str(root),
                    "--runtime-id", "gpt-sovits-cu126", "--build-version", "test-1",
                    "--gpt-sovits-commit", PIN, "--private-key", str(private_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            record = RuntimeVerifier(public_key=public, expected_platform="windows-x86_64").verify_directory(root)
            self.assertTrue(record.signed)
            self.assertEqual(PIN, record.manifest.gpt_sovits_commit)
            self.assertIn("python/Scripts/python.exe", record.manifest.files)
            self.assertNotIn("runtime-manifest.json", record.manifest.files)

    def test_main_exe_explicitly_excludes_gpu_runtime_and_protected_weights(self):
        script = (ROOT / "scripts" / "build_windows.ps1").read_text("utf-8")
        self.assertIn('"--exclude-module", "torch"', script)
        self.assertIn('"--exclude-module", "torchaudio"', script)
        self.assertIn('"--exclude-module", "runtime.gpt_sovits_gpu"', script)
        for forbidden in ("*.ckpt;", "*.pth;", "voice/", "pretrained_models;"):
            self.assertNotIn(forbidden, script)

    def test_verification_script_exists_and_defaults_to_signed_mode(self):
        script = VERIFY.read_text("utf-8")
        self.assertIn("RuntimeVerifier", script)
        self.assertIn("AllowUnsignedDevelopment", script)


if __name__ == "__main__":
    unittest.main()
