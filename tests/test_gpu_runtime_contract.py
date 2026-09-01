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
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.runtime.installer import RuntimeInstaller
from backend.runtime.manifest import RuntimeContractError, canonical_manifest_bytes
from backend.runtime import registry as runtime_registry
from backend.runtime.registry import RuntimeRegistry, RuntimeVerifier
from backend.voice.storage import VoiceStoragePaths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


class RuntimeFixture:
    def __init__(self, root: Path, signed: bool = True, **overrides):
        self.root = root
        self.root.mkdir(parents=True)
        (root / "bin").mkdir()
        (root / "engine").mkdir()
        (root / "models").mkdir()
        (root / "bin" / "python.exe").write_bytes(b"python-runtime")
        (root / "engine" / "sidecar.py").write_text("print('sidecar')\n", encoding="utf-8")
        (root / "models" / "base.bin").write_bytes(b"base-model")
        files = {
            name: _sha256(root / name)
            for name in ("bin/python.exe", "engine/sidecar.py", "models/base.bin")
        }
        payload = {
            "schema_version": 1,
            "runtime_id": "gpt-sovits-cu126",
            "engine": "gpt-sovits-gpu",
            "engine_api_version": 1,
            "platform": "windows-x86_64",
            "build_version": "2026.08.31-dev1",
            "gpt_sovits_commit": "a" * 40,
            "python_version": "3.10.16",
            "torch_version": "2.7.1+cu126",
            "cuda_version": "12.6",
            "supported_model_versions": ["v2Pro", "v2ProPlus"],
            "supported_languages": ["ja"],
            "entrypoint": "engine/sidecar.py",
            "gpu": True,
            "precision": "fp16",
            "minimum_compute_capability": "6.1",
            "minimum_vram_mb": 4096,
            "files": files,
        }
        payload.update(overrides)
        self.payload = payload
        (root / "runtime-manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if signed:
            signature = self.private_key.sign(canonical_manifest_bytes(payload))
            (root / "runtime-manifest.sig").write_text(
                base64.b64encode(signature).decode("ascii"), encoding="ascii"
            )


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self, **kwargs) -> RuntimeFixture:
        return RuntimeFixture(self.root / f"fixture-{len(list(self.root.iterdir()))}", **kwargs)

    def verifier(self, fixture: RuntimeFixture, **kwargs) -> RuntimeVerifier:
        return RuntimeVerifier(
            public_key=fixture.public_key,
            expected_platform="windows-x86_64",
            **kwargs,
        )

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(RuntimeContractError) as caught:
            callback()
        self.assertEqual(code, caught.exception.code)

    def test_signed_runtime_is_verified_and_registered(self):
        fixture = self.fixture()
        record = self.verifier(fixture).verify_directory(fixture.root)
        self.assertEqual("gpt-sovits-cu126", record.runtime_id)
        self.assertTrue(record.signed)
        self.assertIn("v2Pro", record.manifest.supported_model_versions)

        paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        shutil.copytree(fixture.root, paths.runtimes / fixture.payload["runtime_id"])
        registry = RuntimeRegistry(paths, self.verifier(fixture))
        self.assertEqual("gpt-sovits-cu126", registry.find_compatible("v2Pro").runtime_id)
        self.assertEqual("ready", registry.status()["state"])

    def test_platform_engine_api_and_gpu_contract_are_strict(self):
        platform = self.fixture(platform="linux-x86_64")
        self.assert_code("platform_mismatch", lambda: self.verifier(platform).verify_directory(platform.root))
        api = self.fixture(engine_api_version=999)
        self.assert_code("engine_api_mismatch", lambda: self.verifier(api).verify_directory(api.root))
        cpu = self.fixture(gpu=False)
        self.assert_code("gpu_required", lambda: self.verifier(cpu).verify_directory(cpu.root))

    def test_paths_must_be_safe_relative_members(self):
        fixture = self.fixture(entrypoint="../outside.py")
        self.assert_code("unsafe_path", lambda: self.verifier(fixture).verify_directory(fixture.root))
        fixture = self.fixture(files={"../escape": "sha256:" + "0" * 64})
        self.assert_code("unsafe_path", lambda: self.verifier(fixture).verify_directory(fixture.root))

    def test_signature_and_hash_tampering_are_rejected(self):
        fixture = self.fixture()
        (fixture.root / "engine" / "sidecar.py").write_text("tampered", encoding="utf-8")
        self.assert_code("hash_mismatch", lambda: self.verifier(fixture).verify_directory(fixture.root))

        fixture = self.fixture()
        fixture.payload["build_version"] = "changed-after-signing"
        (fixture.root / "runtime-manifest.json").write_text(json.dumps(fixture.payload), encoding="utf-8")
        self.assert_code("invalid_signature", lambda: self.verifier(fixture).verify_directory(fixture.root))

    def test_full_verification_hashes_large_file_sets_concurrently(self):
        fixture = self.fixture()
        for index in range(16):
            relative = f"models/shard-{index:02d}.bin"
            target = fixture.root / relative
            target.write_bytes(bytes([index]) * 64)
            fixture.payload["files"][relative] = _sha256(target)
        (fixture.root / "runtime-manifest.json").write_text(
            json.dumps(fixture.payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        signature = fixture.private_key.sign(canonical_manifest_bytes(fixture.payload))
        (fixture.root / "runtime-manifest.sig").write_text(
            base64.b64encode(signature).decode("ascii"), encoding="ascii"
        )

        real_hash = runtime_registry._sha256

        def storage_latency(path):
            time.sleep(0.05)
            return real_hash(path)

        started = time.perf_counter()
        with mock.patch("backend.runtime.registry._sha256", side_effect=storage_latency):
            self.verifier(fixture).verify_directory(fixture.root)
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_unsigned_is_rejected_unless_development_override_is_explicit(self):
        fixture = self.fixture(signed=False)
        self.assert_code("signature_required", lambda: self.verifier(fixture).verify_directory(fixture.root))
        record = self.verifier(fixture, allow_unsigned=True).verify_directory(fixture.root)
        self.assertFalse(record.signed)

    def test_runtime_root_can_live_outside_main_data_root(self):
        paths = VoiceStoragePaths.resolve(
            env={
                "BILILIVE_DATA_HOME": str(self.root / "data"),
                "BILILIVE_RUNTIME_HOME": str(self.root / "large-data-disk"),
            }
        ).ensure()
        self.assertEqual(self.root / "large-data-disk", paths.runtimes)
        self.assertEqual(self.root / "data" / "voice-state", paths.voice_state)
        self.assertTrue(paths.runtimes.is_dir())

    def test_zip_traversal_is_rejected_before_extraction(self):
        fixture = self.fixture()
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for item in fixture.root.rglob("*"):
                if item.is_file():
                    package.write(item, item.relative_to(fixture.root))
            package.writestr("../outside.txt", "escape")
        installer = RuntimeInstaller(
            VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure(),
            self.verifier(fixture),
        )
        self.assert_code("unsafe_archive_path", lambda: installer.install_zip(archive))
        self.assertFalse((self.root / "outside.txt").exists())

    def test_insufficient_disk_space_has_a_structured_error(self):
        fixture = self.fixture()
        paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        installer = RuntimeInstaller(paths, self.verifier(fixture))
        with mock.patch("backend.runtime.installer.shutil.disk_usage") as usage:
            usage.return_value = shutil._ntuple_diskusage(total=100, used=99, free=1)
            self.assert_code("insufficient_disk_space", lambda: installer.install_directory(fixture.root))
            usage.assert_called_with(paths.runtimes)

    def test_runtime_install_stages_on_the_runtime_target_volume(self):
        fixture = self.fixture()
        paths = VoiceStoragePaths.resolve(env={
            "BILILIVE_DATA_HOME": str(self.root / "system-data"),
            "BILILIVE_RUNTIME_HOME": str(self.root / "runtime-data"),
        }).ensure()
        installer = RuntimeInstaller(paths, self.verifier(fixture))
        real_copytree = shutil.copytree
        observed = {}

        def capture_copy(source, destination, *args, **kwargs):
            observed.setdefault("destination", Path(destination))
            return real_copytree(source, destination, *args, **kwargs)

        with mock.patch("backend.runtime.installer.shutil.copytree", side_effect=capture_copy):
            installer.install_directory(fixture.root)
        observed["destination"].relative_to(paths.runtimes)
        self.assertNotEqual(paths.staging, observed["destination"].parent)

    def test_atomic_replace_rolls_back_existing_runtime_on_failure(self):
        first = self.fixture(build_version="old")
        paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        verifier = self.verifier(first)
        installer = RuntimeInstaller(paths, verifier)
        installer.install_directory(first.root)
        destination = paths.runtimes / first.payload["runtime_id"]
        self.assertEqual("old", json.loads((destination / "runtime-manifest.json").read_text())["build_version"])

        second = self.fixture(build_version="new")
        second_verifier = self.verifier(second)
        installer = RuntimeInstaller(paths, second_verifier)
        real_replace = __import__("os").replace

        def fail_incoming(source, target):
            if Path(target) == destination and Path(source).name.startswith(".incoming-"):
                raise OSError("simulated replace failure")
            return real_replace(source, target)

        with mock.patch("backend.runtime.installer.os.replace", side_effect=fail_incoming):
            with self.assertRaises(OSError):
                installer.install_directory(second.root)
        self.assertEqual("old", json.loads((destination / "runtime-manifest.json").read_text())["build_version"])


if __name__ == "__main__":
    unittest.main()
