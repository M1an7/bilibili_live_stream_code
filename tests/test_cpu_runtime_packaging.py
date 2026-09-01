from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.cpu_runtime.registry import CpuRuntimeVerifier


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_cpu_runtime.ps1"
VERIFY = ROOT / "scripts" / "verify_cpu_runtime.ps1"
VERIFY_CLI = ROOT / "scripts" / "verify_cpu_runtime_cli.py"
BENCHMARK = ROOT / "scripts" / "test_real_cpu_voice.py"
SIGN = ROOT / "scripts" / "sign_runtime_manifest.py"
LOCK = ROOT / "runtime" / "style_bert_vits2_cpu" / "requirements-windows.lock"
STYLE_PIN = (ROOT / "runtime" / "style_bert_vits2_cpu" / "PINNED_STYLE_BERT_VITS2_COMMIT").read_text("ascii").strip()
AIVMLIB_PIN = (ROOT / "runtime" / "style_bert_vits2_cpu" / "PINNED_AIVMLIB_COMMIT").read_text("ascii").strip()


class CpuRuntimePackagingTests(unittest.TestCase):
    def test_builder_is_data_disk_first_pinned_cpu_only_and_relocatable(self):
        script = BUILD.read_text("utf-8")
        for required in (
            "[string]$OutputRoot",
            "[string]$CacheRoot",
            "[string]$Python",
            "[string]$SigningPython",
            '[ValidateSet("China", "Official")]',
            "pypi.tuna.tsinghua.edu.cn",
            "hf-mirror.com",
            "$Curl = Get-Command curl.exe",
            '"--location"',
            '"--retry" "3"',
            '$PartialDestination = "$Destination.part"',
            "Move-Item $PartialDestination $Destination -Force",
            "Get-PSDrive",
            "PINNED_STYLE_BERT_VITS2_COMMIT",
            "PINNED_AIVMLIB_COMMIT",
            "requirements-windows.lock",
            "--require-hashes",
            "--no-build-isolation",
            "CPUExecutionProvider",
            'onnxruntime\\transformers',
            'transformers\\kernels',
            "chinese-roberta-wwm-ext-large-onnx",
            'Name = "added_tokens.json"',
            'Name = "config.json"',
            "model_fp16.onnx",
            "relocation-probe",
            "Style-Bert-VITS2-CPU",
            "tar -a -c -f",
        ):
            self.assertIn(required, script)
        self.assertIn("& $ManifestPython @SignArgs", script)
        self.assertIn('Release signing Python is missing', script)
        for forbidden in (
            "-m venv",
            "pip install --upgrade",
            "voice/",
            "voice\\",
            "*.aivmx",
            "onnxruntime-gpu",
            "onnxruntime-directml",
        ):
            self.assertNotIn(forbidden, script.lower())
        self.assertIn("$env:TEMP = $BuildTemp", script)
        self.assertIn("$env:TMP = $BuildTemp", script)
        self.assertIn("$env:PIP_CACHE_DIR = $PipCache", script)
        self.assertNotIn("--only-binary=:all:", script)
        self.assertNotIn("Invoke-WebRequest -Uri $Uri", script)

    def test_windows_dependency_lock_is_hash_pinned_and_has_no_gpu_stack(self):
        lock = LOCK.read_text("utf-8").lower()
        for dependency in (
            "onnx==",
            "onnxruntime==",
            "pydantic==",
            "transformers==",
            "cn2an==",
            "pypinyin==",
            "pyworld-prebuilt==",
        ):
            self.assertIn(dependency, lock)
        self.assertGreater(lock.count("--hash=sha256:"), 70)
        for forbidden in (
            "torch==",
            "torchaudio==",
            "onnxruntime-gpu",
            "onnxruntime-directml",
            "cuda",
            "cudnn",
            "tensorrt",
            "nvidia-",
        ):
            self.assertNotIn(forbidden, lock)

    def test_signer_produces_a_verifiable_cpu_runtime_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            for directory in (root / "engine", root / "python", root / "upstream" / "style-bert-vits2", root / "upstream" / "aivmlib"):
                directory.mkdir(parents=True)
            (root / "engine" / "sidecar.py").write_text("print('ok')", "utf-8")
            (root / "engine" / "protocol.py").write_text("# protocol", "utf-8")
            (root / "python" / "python.exe").write_bytes(b"python")
            (root / "upstream" / "style-bert-vits2" / "LICENSE").write_text("AGPL", "utf-8")
            (root / "upstream" / "aivmlib" / "LICENSE").write_text("MIT", "utf-8")
            private_key = Ed25519PrivateKey.generate()
            private_path = Path(temp) / "private.pem"
            private_path.write_bytes(private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            subprocess.run(
                [
                    sys.executable,
                    str(SIGN),
                    "--runtime-root",
                    str(root),
                    "--engine",
                    "style-bert-vits2-onnx-cpu",
                    "--runtime-id",
                    "style-bert-vits2-cpu",
                    "--build-version",
                    "test-1",
                    "--style-bert-vits2-commit",
                    STYLE_PIN,
                    "--aivmlib-commit",
                    AIVMLIB_PIN,
                    "--onnxruntime-version",
                    "1.22.1",
                    "--private-key",
                    str(private_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            record = CpuRuntimeVerifier(public_key=public, expected_platform="windows-x86_64").verify_directory(root)
            self.assertTrue(record.signed)
            self.assertFalse(record.manifest.gpu)
            self.assertEqual(("CPUExecutionProvider",), record.manifest.providers)
            self.assertEqual(4, record.manifest.default_threads)

    def test_unsigned_development_manifest_does_not_require_signing_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "runtime"
            for directory in (root / "engine", root / "python", root / "upstream" / "style-bert-vits2", root / "upstream" / "aivmlib"):
                directory.mkdir(parents=True)
            (root / "engine" / "sidecar.py").write_text("print('ok')", "utf-8")
            (root / "engine" / "protocol.py").write_text("# protocol", "utf-8")
            (root / "python" / "python.exe").write_bytes(b"python")
            (root / "upstream" / "style-bert-vits2" / "LICENSE").write_text("AGPL", "utf-8")
            (root / "upstream" / "aivmlib" / "LICENSE").write_text("MIT", "utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(SIGN),
                    "--runtime-root",
                    str(root),
                    "--engine",
                    "style-bert-vits2-onnx-cpu",
                    "--runtime-id",
                    "style-bert-vits2-cpu",
                    "--build-version",
                    "test-unsigned",
                    "--style-bert-vits2-commit",
                    STYLE_PIN,
                    "--aivmlib-commit",
                    AIVMLIB_PIN,
                    "--allow-unsigned",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((root / "runtime-manifest.json").is_file())
            self.assertFalse((root / "runtime-manifest.sig").exists())

    def test_verifier_and_real_benchmark_enforce_cpu_contract(self):
        verify = VERIFY.read_text("utf-8")
        verify_cli = VERIFY_CLI.read_text("utf-8")
        benchmark = BENCHMARK.read_text("utf-8")
        self.assertIn("verify_cpu_runtime_cli.py", verify)
        self.assertNotIn("-c $VerifyCode", verify)
        for required in ("CpuRuntimeVerifier", "CPUExecutionProvider", "torch"):
            self.assertIn(required, verify_cli)
        self.assertIn("AllowUnsignedDevelopment", verify)
        for required in (
            "CPUExecutionProvider",
            "vram_mb",
            "peak_rss_mb",
            "first_pcm_ms",
            "failure_artifacts",
            "--runtime",
            "--aivmx",
            "wave",
        ):
            self.assertIn(required, benchmark)
        sidecar = (ROOT / "runtime" / "style_bert_vits2_cpu" / "sidecar.py").read_text("utf-8")
        self.assertIn("traceback.print_exc", sidecar)

    def test_main_exe_collects_control_plane_but_excludes_cpu_inference_runtime(self):
        script = (ROOT / "scripts" / "build_windows.ps1").read_text("utf-8")
        self.assertIn('"--collect-submodules", "backend.aivmx"', script)
        self.assertIn('"--collect-submodules", "backend.cpu_runtime"', script)
        for module in ("onnx", "onnxruntime", "transformers", "runtime.style_bert_vits2_cpu"):
            self.assertIn(f'"--exclude-module", "{module}"', script)
        for protected in ("*.aivmx", "voice/", "voice\\", "model_fp16.onnx"):
            self.assertNotIn(protected, script.lower())


if __name__ == "__main__":
    unittest.main()
