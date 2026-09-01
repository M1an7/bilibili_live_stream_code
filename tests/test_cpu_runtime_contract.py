from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.cpu_runtime import (
    CpuRuntimeContractError,
    CpuRuntimeInstaller,
    CpuRuntimeInstallJobManager,
    CpuRuntimeRegistry,
    CpuRuntimeVerifier,
    canonical_cpu_manifest_bytes,
)
from backend.voice.storage import VoiceStoragePaths


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class CpuRuntimeFixture:
    def __init__(self, root: Path, signed: bool = True, **updates):
        self.root = root
        (root / "python").mkdir(parents=True)
        (root / "engine").mkdir()
        (root / "bert" / "chinese-roberta-wwm-ext-large-onnx").mkdir(parents=True)
        (root / "python" / "python.exe").write_bytes(b"cpu-python")
        (root / "engine" / "sidecar.py").write_text("print('cpu sidecar')\n", "utf-8")
        (root / "bert" / "chinese-roberta-wwm-ext-large-onnx" / "model_fp16.onnx").write_bytes(b"bert")
        files = {
            item.relative_to(root).as_posix(): sha256(item)
            for item in root.rglob("*") if item.is_file()
        }
        payload = {
            "schema_version": 1,
            "runtime_id": "style-bert-vits2-cpu-1",
            "engine": "style-bert-vits2-onnx-cpu",
            "engine_api_version": 1,
            "platform": "windows-x86_64",
            "build_version": "2026.09.01-dev1",
            "style_bert_vits2_commit": "a" * 40,
            "aivmlib_commit": "b" * 40,
            "python_version": "3.11.9",
            "onnxruntime_version": "1.22.1",
            "supported_architectures": ["Style-Bert-VITS2"],
            "supported_languages": ["zh-CN"],
            "entrypoint": "engine/sidecar.py",
            "gpu": False,
            "providers": ["CPUExecutionProvider"],
            "default_threads": 4,
            "files": files,
        }
        payload.update(updates)
        self.payload = payload
        (root / "runtime-manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        if signed:
            signature = self.private_key.sign(canonical_cpu_manifest_bytes(payload))
            (root / "runtime-manifest.sig").write_text(base64.b64encode(signature).decode("ascii"), "ascii")


class CpuRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, **kwargs):
        return CpuRuntimeFixture(self.root / f"fixture-{len(list(self.root.iterdir()))}", **kwargs)

    @staticmethod
    def verifier(fixture, **kwargs):
        return CpuRuntimeVerifier(
            public_key=fixture.public_key,
            expected_platform="windows-x86_64",
            **kwargs,
        )

    def assert_code(self, expected, callback):
        with self.assertRaises(CpuRuntimeContractError) as caught:
            callback()
        self.assertEqual(caught.exception.code, expected)

    def test_signed_cpu_runtime_registers_for_multilingual_chinese(self):
        fixture = self.fixture()
        record = self.verifier(fixture).verify_directory(fixture.root)
        self.assertFalse(record.manifest.gpu)
        self.assertEqual(record.manifest.providers, ("CPUExecutionProvider",))

        paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        shutil.copytree(fixture.root, paths.cpu_runtimes / record.runtime_id)
        registry = CpuRuntimeRegistry(paths, self.verifier(fixture))
        self.assertEqual(record.runtime_id, registry.find_compatible("Style-Bert-VITS2", "zh-CN").runtime_id)

    def test_gpu_providers_and_gpu_dependencies_are_rejected(self):
        for updates, code in [
            ({"gpu": True}, "gpu_forbidden"),
            ({"providers": ["CUDAExecutionProvider"]}, "provider_forbidden"),
            ({"providers": ["DmlExecutionProvider"]}, "provider_forbidden"),
        ]:
            fixture = self.fixture(**updates)
            self.assert_code(code, lambda fixture=fixture: self.verifier(fixture).verify_directory(fixture.root))

        fixture = self.fixture()
        banned = fixture.root / "python" / "onnxruntime_providers_cuda.dll"
        banned.write_bytes(b"gpu")
        fixture.payload["files"]["python/onnxruntime_providers_cuda.dll"] = sha256(banned)
        (fixture.root / "runtime-manifest.json").write_text(json.dumps(fixture.payload), "utf-8")
        signature = fixture.private_key.sign(canonical_cpu_manifest_bytes(fixture.payload))
        (fixture.root / "runtime-manifest.sig").write_text(base64.b64encode(signature).decode("ascii"), "ascii")
        self.assert_code("gpu_dependency_forbidden", lambda: self.verifier(fixture).verify_directory(fixture.root))

    def test_signature_hash_and_unsigned_development_contract(self):
        fixture = self.fixture()
        (fixture.root / "engine" / "sidecar.py").write_text("tampered", "utf-8")
        self.assert_code("hash_mismatch", lambda: self.verifier(fixture).verify_directory(fixture.root))

        unsigned = self.fixture(signed=False)
        self.assert_code("signature_required", lambda: self.verifier(unsigned).verify_directory(unsigned.root))
        self.assertFalse(self.verifier(unsigned, allow_unsigned=True).verify_directory(unsigned.root).signed)

    def test_installer_uses_separate_cpu_root_and_rejects_zip_traversal(self):
        fixture = self.fixture()
        paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        installer = CpuRuntimeInstaller(paths, self.verifier(fixture))
        installed = installer.install_directory(fixture.root)
        self.assertEqual(paths.cpu_runtimes / installed.runtime_id, installed.path)

        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../escape.txt", "bad")
        self.assert_code("unsafe_archive_path", lambda: installer.install_zip(archive))
        self.assertFalse((self.root / "escape.txt").exists())

    def test_background_install_job_reports_completion_and_refreshes_registry(self):
        fixture = self.fixture()
        paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        verifier = self.verifier(fixture)
        registry = CpuRuntimeRegistry(paths, verifier)
        jobs = CpuRuntimeInstallJobManager(CpuRuntimeInstaller(paths, verifier), registry)
        self.addCleanup(jobs.shutdown)

        job_id = jobs.start({"source_type": "directory", "path": str(fixture.root)})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = jobs.get(job_id)
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)

        self.assertEqual("completed", job["status"])
        self.assertEqual(100, job["progress"])
        self.assertEqual("style-bert-vits2-cpu-1", job["result"]["runtime_id"])
        self.assertEqual("ready", registry.status()["state"])


if __name__ == "__main__":
    unittest.main()
