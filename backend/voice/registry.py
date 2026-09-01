from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .builder import BuiltVoicePack
from .manifest import VOICE_ID_PATTERN, VoiceContractError, VoiceManifest
from .storage import VoiceStoragePaths
from .validator import VoicePackValidator, is_link_or_reparse
from .health import VoiceHealthStore


@dataclass(frozen=True)
class VoicePackRecord:
    voice_id: str
    display_name: str
    health: str
    message: str
    manifest: VoiceManifest | None

    @property
    def selectable(self) -> bool:
        return self.health == "ready"

    def to_dict(self) -> dict:
        manifest = self.manifest
        return {
            "voice_id": self.voice_id,
            "voice_key": f"pack:{self.voice_id}",
            "display_name": self.display_name,
            "health": self.health,
            "selectable": self.selectable,
            "message": self.message,
            "model_version": manifest.model_version if manifest else "",
            "source_language": manifest.source_language if manifest else "",
            "supported_output_languages": list(manifest.supported_output_languages) if manifest else [],
            "engine": manifest.engine if manifest else "",
            "authorization_present": bool(manifest and manifest.license_file),
        }


class VoicePackRegistry:
    def __init__(self, paths: VoiceStoragePaths, validator: VoicePackValidator):
        self.paths = paths.ensure()
        self.validator = validator
        self.health_store = VoiceHealthStore(paths)
        self.runtime_registry = None
        self._lock = threading.RLock()
        self._records: dict[str, VoicePackRecord] = {}
        self.refresh()

    def _record_from_directory(self, directory: Path) -> VoicePackRecord:
        voice_id = directory.name
        try:
            validation = self.validator.validate_directory(directory)
            manifest = validation.manifest
            if not manifest or manifest.voice_id != voice_id:
                raise VoiceContractError("voice_id_mismatch", "目录名与音色 ID 不一致")
            runtime = self.runtime_registry.find_compatible(manifest.model_version, "ja") if self.runtime_registry else None
            if self.runtime_registry and runtime is None:
                health = {"health": "runtime_required", "message": "音色已导入，等待兼容的 GPU 运行时"}
            else:
                health = self.health_store.get(manifest, runtime)
            return VoicePackRecord(
                voice_id=voice_id,
                display_name=manifest.display_name,
                health=health["health"],
                message=health["message"],
                manifest=manifest,
            )
        except (VoiceContractError, OSError, json.JSONDecodeError) as exc:
            message = getattr(exc, "message", "音色包损坏或无法读取")
            return VoicePackRecord(voice_id, voice_id, "invalid", message, None)

    def refresh(self) -> list[VoicePackRecord]:
        records: dict[str, VoicePackRecord] = {}
        with self._lock:
            for entry in self.paths.voices.iterdir():
                if entry.name.startswith("."):
                    continue
                if not VOICE_ID_PATTERN.fullmatch(entry.name):
                    continue
                if not entry.is_dir() or is_link_or_reparse(entry):
                    records[entry.name] = VoicePackRecord(entry.name, entry.name, "invalid", "音色目录不安全", None)
                else:
                    records[entry.name] = self._record_from_directory(entry)
            self._records = records
            return list(records.values())

    def set_runtime_registry(self, runtime_registry) -> list[VoicePackRecord]:
        self.runtime_registry = runtime_registry
        return self.refresh()

    def install_staged(self, built: BuiltVoicePack) -> VoicePackRecord:
        staging = Path(built.staging_path)
        validation = self.validator.validate_directory(staging)
        manifest = validation.manifest
        if not validation.valid or not manifest:
            raise VoiceContractError("invalid_pack", "音色包未通过结构校验")
        if manifest.voice_id != built.manifest.voice_id:
            raise VoiceContractError("voice_id_mismatch", "待安装音色 ID 不一致")

        destination = self.paths.voices / manifest.voice_id
        incoming = self.paths.voices / f".incoming-{uuid.uuid4().hex}"
        backup = self.paths.voices / f".backup-{uuid.uuid4().hex}"
        moved_old = False
        with self._lock:
            try:
                os.replace(staging, incoming)
                if destination.exists():
                    if is_link_or_reparse(destination):
                        raise VoiceContractError("unsafe_destination", "现有音色目录不安全，拒绝覆盖")
                    os.replace(destination, backup)
                    moved_old = True
                os.replace(incoming, destination)
            except BaseException:
                if moved_old and backup.exists() and not destination.exists():
                    os.replace(backup, destination)
                if incoming.exists():
                    shutil.rmtree(incoming, ignore_errors=True)
                raise
            else:
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)

            record = self._record_from_directory(destination)
            if record.health == "invalid":
                raise VoiceContractError("post_install_validation_failed", record.message)
            self._records[record.voice_id] = record
            return record

    def get(self, voice_id: str) -> VoicePackRecord | None:
        with self._lock:
            return self._records.get(voice_id)

    def list_packs(self) -> list[dict]:
        with self._lock:
            return [record.to_dict() for record in sorted(self._records.values(), key=lambda item: item.display_name)]
