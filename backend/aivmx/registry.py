from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import is_link_or_reparse

from .contract import AivmxContractError, AivmxMetadata
from .protobuf import AivmxMetadataReader


Progress = Callable[[str, int, str], None]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class AivmxVoiceRecord:
    path: Path
    metadata: AivmxMetadata
    sha256: str
    installed_at: str
    health: str = "runtime_required"
    message: str = "音色已导入，等待兼容的 CPU 运行时"

    @property
    def selectable(self) -> bool:
        return self.health == "ready"

    def to_dicts(self) -> list[dict]:
        records = []
        for style in self.metadata.styles:
            records.append({
                "voice_key": style.voice_key,
                "model_uuid": self.metadata.model_uuid,
                "style_id": style.style_id,
                "speaker_id": style.speaker_id,
                "display_name": self.metadata.display_name if len(self.metadata.styles) == 1 else f"{self.metadata.display_name} · {style.name}",
                "style_name": style.name,
                "engine_kind": "aivmx-cpu",
                "resource_mode": "cpu_zero_vram",
                "health": self.health,
                "selectable": self.selectable,
                "message": self.message,
                "supported_output_languages": list(self.metadata.languages),
                "authorization_present": bool(self.metadata.license_text),
                "sha256": self.sha256,
                "installed_at": self.installed_at,
            })
        return records


class AivmxVoiceRegistry:
    def __init__(self, paths: VoiceStoragePaths, reader: AivmxMetadataReader | None = None):
        self.paths = paths.ensure()
        self.reader = reader or AivmxMetadataReader()
        self._lock = threading.RLock()
        self._records: dict[str, AivmxVoiceRecord] = {}
        self.refresh()

    @staticmethod
    def _install_payload(metadata: AivmxMetadata, digest: str, installed_at: str) -> dict:
        return {
            "schema_version": 1,
            "model_uuid": metadata.model_uuid,
            "model_file": "model.aivmx",
            "sha256": digest,
            "installed_at": installed_at,
            "permissions_confirmed": True,
            "metadata": metadata.to_dict(),
        }

    def _read_directory(self, directory: Path) -> AivmxVoiceRecord:
        if is_link_or_reparse(directory) or not directory.is_dir():
            raise AivmxContractError("unsafe_install", "已安装 AIVMX 音色目录不安全")
        model = directory / "model.aivmx"
        install_path = directory / "install.json"
        if not model.is_file() or is_link_or_reparse(model) or not install_path.is_file() or is_link_or_reparse(install_path):
            raise AivmxContractError("invalid_install", "已安装 AIVMX 音色缺少必要文件")
        try:
            install = json.loads(install_path.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AivmxContractError("invalid_install", "AIVMX 安装记录损坏") from exc
        metadata = self.reader.read(model)
        digest = sha256_file(model)
        if install.get("schema_version") != 1 or install.get("model_uuid") != directory.name:
            raise AivmxContractError("invalid_install", "AIVMX 安装记录与目录不一致")
        if install.get("sha256") != digest or metadata.model_uuid != directory.name:
            raise AivmxContractError("hash_mismatch", "AIVMX 音色文件已发生变化")
        if install.get("permissions_confirmed") is not True:
            raise AivmxContractError("permissions_required", "AIVMX 音色缺少授权确认")
        installed_at = install.get("installed_at")
        if not isinstance(installed_at, str) or not installed_at:
            raise AivmxContractError("invalid_install", "AIVMX 安装时间无效")
        return AivmxVoiceRecord(model, metadata, digest, installed_at)

    def refresh(self) -> list[AivmxVoiceRecord]:
        records: dict[str, AivmxVoiceRecord] = {}
        with self._lock:
            for entry in self.paths.aivmx_voices.iterdir():
                if entry.name.startswith("."):
                    continue
                try:
                    uuid.UUID(entry.name)
                    record = self._read_directory(entry)
                except (AivmxContractError, OSError, ValueError):
                    continue
                records[record.metadata.model_uuid] = record
            self._records = records
            return list(records.values())

    def get(self, model_uuid: str) -> AivmxVoiceRecord | None:
        with self._lock:
            return self._records.get(model_uuid)

    def list_voices(self) -> list[dict]:
        with self._lock:
            voices = [voice for record in self._records.values() for voice in record.to_dicts()]
        return sorted(voices, key=lambda item: (item["display_name"], item["style_id"]))

    def install(
        self,
        source: Path | str,
        permissions_confirmed: bool,
        progress: Progress | None = None,
    ) -> AivmxVoiceRecord:
        if permissions_confirmed is not True:
            raise AivmxContractError("permissions_required", "请确认已获得训练、合成语音和公开直播权限", "permissions_confirmed")
        source_path = Path(source)
        metadata = self.reader.read(source_path)
        destination = self.paths.aivmx_voices / metadata.model_uuid
        staging = self.paths.aivmx_voices / f".staging-{uuid.uuid4().hex}"
        incoming = self.paths.aivmx_voices / f".incoming-{uuid.uuid4().hex}"
        backup = self.paths.aivmx_voices / f".backup-{uuid.uuid4().hex}"
        moved_old = False
        try:
            staging.mkdir(parents=False)
            target = staging / "model.aivmx"
            total = source_path.stat().st_size
            copied = 0
            if progress:
                progress("copy", 5, "正在复制 AIVMX 音色")
            with source_path.open("rb") as input_stream, target.open("xb") as output_stream:
                for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                    output_stream.write(block)
                    copied += len(block)
                    if progress:
                        progress("copy", min(75, 5 + int(70 * copied / total)), "正在复制 AIVMX 音色")
            staged_metadata = self.reader.read(target)
            if staged_metadata != metadata:
                raise AivmxContractError("copy_mismatch", "AIVMX 复制后元数据不一致")
            digest = sha256_file(target)
            installed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            install = self._install_payload(metadata, digest, installed_at)
            (staging / "install.json").write_text(json.dumps(install, ensure_ascii=False, indent=2) + "\n", "utf-8")
            if progress:
                progress("verify", 85, "正在校验 AIVMX 音色")
            with self._lock:
                os.replace(staging, incoming)
                if destination.exists():
                    if is_link_or_reparse(destination):
                        raise AivmxContractError("unsafe_destination", "现有 AIVMX 音色目录不安全")
                    os.replace(destination, backup)
                    moved_old = True
                os.replace(incoming, destination)
                try:
                    record = self._read_directory(destination)
                except BaseException:
                    if destination.exists():
                        shutil.rmtree(destination, ignore_errors=True)
                    if moved_old and backup.exists():
                        os.replace(backup, destination)
                    raise
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                self._records[metadata.model_uuid] = record
            if progress:
                progress("done", 100, "AIVMX 音色导入完成")
            return record
        except BaseException:
            if moved_old and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        finally:
            for temporary in (staging, incoming):
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
