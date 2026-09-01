from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from backend.runtime.registry import MAX_MANIFEST_SIZE, RuntimeVerifier
from backend.runtime.keys import release_public_key


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_gpu_runtime.ps1"
SIGN = ROOT / "scripts" / "sign_runtime_manifest.py"
VERIFY = ROOT / "scripts" / "verify_gpu_runtime.ps1"
SPLIT = ROOT / "scripts" / "split_gpu_runtime.ps1"
JOIN = ROOT / "scripts" / "join_gpu_runtime_parts.ps1"
PIN = (ROOT / "runtime" / "gpt_sovits_gpu" / "PINNED_GPT_SOVITS_COMMIT").read_text("ascii").strip()


class GpuRuntimePackagingTests(unittest.TestCase):
    def test_runtime_manifest_limit_covers_a_complete_portable_python_runtime(self):
        self.assertGreaterEqual(MAX_MANIFEST_SIZE, 16 * 1024 * 1024)

    def test_bundled_release_public_key_is_a_valid_ed25519_key(self):
        public = release_public_key()
        self.assertEqual(32, len(public))
        Ed25519PublicKey.from_public_bytes(public)

    def test_builder_is_data_disk_first_pinned_cu126_and_separate(self):
        script = BUILD.read_text("utf-8")
        self.assertIn("[string]$BuildRoot", script)
        self.assertIn("[string]$BuildTempRoot", script)
        self.assertIn("Get-PSDrive", script)
        self.assertIn("PINNED_GPT_SOVITS_COMMIT", script)
        self.assertIn("git", script)
        self.assertIn("3.10", script)
        self.assertIn("cu126", script.lower())
        self.assertIn("pretrained_models.zip", script)
        self.assertIn("open_jtalk_dic_utf_8-1.11", script)
        self.assertIn("ffmpeg.exe", script.lower())
        self.assertIn("BiliLiveTool-GPT-SoVITS-CU126", script)
        self.assertIn("tar -a -c -f", script)
        self.assertIn("GPU runtime ZIP creation", script)
        self.assertIn("split_gpu_runtime.ps1", script)
        self.assertIn("PythonArchiveSha256", script)
        self.assertIn("python-build-standalone", script)
        self.assertIn("install_only_stripped.tar.gz", script)
        self.assertIn("PretrainedModelsArchive", script)
        self.assertIn("PinnedSourceRepository", script)
        self.assertIn("PythonArchivePath", script)
        self.assertIn("OpenJTalkArchive", script)
        self.assertIn("FfmpegPath", script)
        self.assertIn("FfprobePath", script)
        self.assertNotIn("-m venv", script)
        self.assertIn("relocation-probe", script)
        self.assertIn("relocated runtime verification", script)
        self.assertIn('$env:PIP_CACHE_DIR = $BuildCache', script)
        self.assertIn('$env:NUMBA_CACHE_DIR = $BuildRuntimeCache', script)
        self.assertIn('$env:TEMP = $BuildTemp', script)
        self.assertIn('$env:TMP = $BuildTemp', script)
        self.assertIn("Enable-MsvcEnvironment", script)
        self.assertIn("VsDevCmd.bat", script)
        self.assertIn("RuntimePythonHeader", script)
        self.assertIn("portable Python extraction", script)
        self.assertIn('$env:CMAKE_GENERATOR = "NMake Makefiles"', script)
        self.assertIn("requirements-windows.lock", script)
        self.assertIn("--require-hashes", script)
        self.assertIn("Japanese dictionary pre-generation", script)
        self.assertIn("user.dict", script)
        self.assertIn("userdict.md5", script)
        self.assertNotIn('Join-Path $UpstreamRoot "requirements.txt"', script)
        self.assertNotIn("pip install --upgrade", script)
        for checksum_name in (
            "PretrainedModelsSha256",
            "OpenJTalkDictionarySha256",
            "FfmpegSha256",
            "FfprobeSha256",
        ):
            self.assertIn(checksum_name, script)

    def test_builder_requires_base_models_and_preserves_upstream_license(self):
        script = BUILD.read_text("utf-8")
        for required in (
            "chinese-hubert-base",
            "chinese-roberta-wwm-ext-large",
            "pretrained_models/sv",
            "v2Pro/s2Gv2Pro.pth",
            "v2Pro/s2Dv2Pro.pth",
            "v2Pro/s2Gv2ProPlus.pth",
            "v2Pro/s2Dv2ProPlus.pth",
            "fast_langdetect/lid.176.bin",
            "LICENSE",
        ):
            self.assertIn(required, script)

    def test_windows_dependency_lock_is_complete_and_hash_pinned(self):
        lock = (ROOT / "runtime" / "gpt_sovits_gpu" / "requirements-windows.lock").read_text("utf-8")
        self.assertIn("torch==2.7.1+cu126", lock)
        self.assertIn("torchaudio==2.7.1+cu126", lock)
        self.assertIn("pyopenjtalk==", lock)
        self.assertGreater(lock.count("--hash=sha256:"), 150)
        for forbidden in ("python-mecab-ko", "uvloop==", "triton==", "nvidia-cublas"):
            self.assertNotIn(forbidden, lock)

    def test_signer_produces_a_verifiable_complete_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            (root / "engine").mkdir(parents=True)
            (root / "python").mkdir(parents=True)
            (root / "upstream" / "GPT_SoVITS" / "pretrained_models" / "sv").mkdir(parents=True)
            (root / "engine" / "sidecar.py").write_text("print('ok')", "utf-8")
            (root / "engine" / "protocol.py").write_text("# protocol", "utf-8")
            (root / "python" / "python.exe").write_bytes(b"python")
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
            self.assertIn("python/python.exe", record.manifest.files)
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
        self.assertIn("PythonPath", script)

    def test_large_runtime_has_streaming_split_and_verified_join_tools(self):
        split = SPLIT.read_text("utf-8")
        join = JOIN.read_text("utf-8")
        self.assertIn("PartSizeMiB = 1900", split)
        self.assertIn("IncrementalHash", split)
        self.assertIn("parts-manifest.json", split)
        self.assertIn("IncrementalHash", join)
        self.assertIn("Output already exists", join)
        self.assertIn("Reassembled GPU runtime checksum failed", join)


if __name__ == "__main__":
    unittest.main()
