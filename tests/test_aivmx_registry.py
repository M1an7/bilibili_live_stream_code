from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.aivmx import (
    AivmxContractError,
    AivmxInstallJobManager,
    AivmxMetadataReader,
    AivmxVoiceRegistry,
)
from backend.voice.storage import VoiceStoragePaths


MODEL_UUID = "11111111-2222-4333-8444-555555555555"
SPEAKER_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _field(number: int, payload: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _metadata_entry(key: str, value: str) -> bytes:
    return _field(1, key.encode("utf-8")) + _field(2, value.encode("utf-8"))


def manifest(**updates) -> dict:
    payload = {
        "manifest_version": "1.0",
        "name": "灰原哀实时音色",
        "description": "授权素材训练的本地直播音色",
        "creators": ["local-user"],
        "license": "仅限已授权的训练、合成与公开直播使用。",
        "model_architecture": "Style-Bert-VITS2",
        "model_format": "ONNX",
        "training_epochs": 100,
        "training_steps": 11500,
        "uuid": MODEL_UUID,
        "version": "1.0.0",
        "speakers": [{
            "name": "灰原哀",
            "icon": "data:image/png;base64," + base64.b64encode(b"png").decode("ascii"),
            "supported_languages": ["ja", "zh-CN"],
            "uuid": SPEAKER_UUID,
            "local_id": 0,
            "styles": [{"name": "Neutral", "icon": None, "local_id": 0, "voice_samples": []}],
        }],
    }
    payload.update(updates)
    return payload


def write_aivmx(path: Path, manifest_payload: dict | None = None, graph: bytes = b"graph") -> bytes:
    raw = b"".join([
        _field(8, graph),
        _field(14, _metadata_entry("aivm_manifest", json.dumps(manifest_payload or manifest(), ensure_ascii=False))),
        _field(14, _metadata_entry("aivm_hyper_parameters", json.dumps({"model_name": "haibara"}))),
        _field(14, _metadata_entry("aivm_style_vectors", base64.b64encode(b"style-vectors").decode("ascii"))),
    ])
    path.write_bytes(raw)
    return raw


class AivmxRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = VoiceStoragePaths.resolve(env={"BILILIVE_DATA_HOME": str(self.root / "data")}).ensure()
        self.reader = AivmxMetadataReader(max_model_bytes=1024 * 1024)
        self.registry = AivmxVoiceRegistry(self.paths, self.reader)
        self.jobs = AivmxInstallJobManager(self.registry)
        self.source = self.root / "haibara.aivmx"

    def tearDown(self):
        self.jobs.shutdown()
        self.temp.cleanup()

    def test_reads_metadata_without_loading_the_large_graph(self):
        write_aivmx(self.source, graph=b"x" * 500_000)

        metadata = self.reader.read(self.source)

        self.assertEqual(metadata.model_uuid, MODEL_UUID)
        self.assertEqual(metadata.display_name, "灰原哀实时音色")
        self.assertEqual(metadata.languages, ("ja", "zh-CN"))
        self.assertEqual(metadata.styles[0].voice_key, f"aivmx:{MODEL_UUID}:0")
        self.assertEqual(metadata.training_steps, 11500)

    def test_installs_unmodified_file_atomically_after_rights_confirmation(self):
        original = write_aivmx(self.source)

        record = self.registry.install(self.source, permissions_confirmed=True)

        installed = self.paths.aivmx_voices / MODEL_UUID / "model.aivmx"
        self.assertEqual(installed.read_bytes(), original)
        self.assertEqual(record.health, "runtime_required")
        self.assertFalse(record.selectable)
        listed = self.registry.list_voices()
        self.assertEqual(listed[0]["voice_key"], f"aivmx:{MODEL_UUID}:0")
        self.assertEqual(listed[0]["resource_mode"], "cpu_zero_vram")
        install = json.loads((installed.parent / "install.json").read_text("utf-8"))
        self.assertTrue(install["permissions_confirmed"])
        self.assertEqual(install["sha256"], record.sha256)

    def test_rejects_unsupported_or_unauthorized_models(self):
        cases = [
            (manifest(model_architecture="Style-Bert-VITS2 (JP-Extra)"), "architecture_not_supported"),
            (manifest(speakers=[{**manifest()["speakers"][0], "supported_languages": ["ja"]}]), "chinese_not_supported"),
            (manifest(license=None), "license_required"),
            (manifest(uuid="not-a-uuid"), "invalid_manifest"),
        ]
        for index, (payload, expected_code) in enumerate(cases):
            with self.subTest(expected_code):
                source = self.root / f"invalid-{index}.aivmx"
                write_aivmx(source, payload)
                with self.assertRaises(AivmxContractError) as caught:
                    self.reader.read(source)
                self.assertEqual(caught.exception.code, expected_code)

        write_aivmx(self.source)
        with self.assertRaises(AivmxContractError) as caught:
            self.registry.install(self.source, permissions_confirmed=False)
        self.assertEqual(caught.exception.code, "permissions_required")

    def test_rejects_wrong_extension_oversized_file_and_links(self):
        wrong = self.root / "model.onnx"
        write_aivmx(wrong)
        with self.assertRaises(AivmxContractError) as caught:
            self.reader.read(wrong)
        self.assertEqual(caught.exception.code, "invalid_extension")

        tiny_reader = AivmxMetadataReader(max_model_bytes=32)
        write_aivmx(self.source)
        with self.assertRaises(AivmxContractError) as caught:
            tiny_reader.read(self.source)
        self.assertEqual(caught.exception.code, "model_too_large")

        link = self.root / "linked.aivmx"
        try:
            link.symlink_to(self.source)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(AivmxContractError) as caught:
            self.reader.read(link)
        self.assertEqual(caught.exception.code, "unsafe_source")

    def test_failed_update_restores_the_existing_voice(self):
        first_bytes = write_aivmx(self.source, manifest(description="first"))
        self.registry.install(self.source, permissions_confirmed=True)
        second = self.root / "second.aivmx"
        write_aivmx(second, manifest(description="second"))
        destination = self.paths.aivmx_voices / MODEL_UUID
        real_replace = os.replace

        def fail_incoming(source, target):
            if Path(source).name.startswith(".incoming-") and Path(target) == destination:
                raise OSError("simulated replacement failure")
            return real_replace(source, target)

        with patch("backend.aivmx.registry.os.replace", side_effect=fail_incoming):
            with self.assertRaises(OSError):
                self.registry.install(second, permissions_confirmed=True)

        self.assertEqual((destination / "model.aivmx").read_bytes(), first_bytes)
        self.assertEqual(self.registry.refresh()[0].metadata.description, "first")

    def test_background_install_reports_progress_and_structured_errors(self):
        write_aivmx(self.source)
        job_id = self.jobs.start({"path": str(self.source), "permissions_confirmed": True})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.jobs.get(job_id)
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["progress"], 100)
        self.assertEqual(job["result"]["voice_key"], f"aivmx:{MODEL_UUID}:0")

        denied = self.jobs.start({"path": str(self.source), "permissions_confirmed": False})
        while time.monotonic() < deadline + 3:
            job = self.jobs.get(denied)
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "permissions_required")


if __name__ == "__main__":
    unittest.main()
