from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import is_link_or_reparse

from .keys import release_public_key
from .manifest import RuntimeContractError, RuntimeManifest, canonical_manifest_bytes


MAX_RUNTIME_FILES = 100_000
MAX_MANIFEST_SIZE = 16 * 1024 * 1024


def current_platform_id() -> str:
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-x86_64"
    return f"{sys.platform}-x86_64"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class RuntimeRecord:
    path: Path
    manifest: RuntimeManifest
    signed: bool

    @property
    def runtime_id(self) -> str:
        return self.manifest.runtime_id

    def to_dict(self) -> dict:
        return {
            "runtime_id": self.runtime_id,
            "path": str(self.path),
            "signed": self.signed,
            "build_version": self.manifest.build_version,
            "platform": self.manifest.platform,
            "gpu": self.manifest.gpu,
            "precision": self.manifest.precision,
            "supported_model_versions": list(self.manifest.supported_model_versions),
            "supported_languages": list(self.manifest.supported_languages),
        }


class RuntimeVerifier:
    def __init__(
        self,
        public_key: bytes | None = None,
        expected_platform: str | None = None,
        allow_unsigned: bool = False,
    ):
        configured_key = os.environ.get("BILILIVE_RUNTIME_PUBLIC_KEY", "").strip()
        if public_key is None and configured_key:
            try:
                public_key = base64.b64decode(configured_key, validate=True)
            except ValueError as exc:
                raise RuntimeContractError("invalid_public_key", "运行时公钥配置无效") from exc
        self.public_key = public_key if public_key is not None else release_public_key()
        self.expected_platform = expected_platform or current_platform_id()
        self.allow_unsigned = allow_unsigned

    def verify_directory(self, path: Path) -> RuntimeRecord:
        root = Path(path)
        if not root.is_dir() or is_link_or_reparse(root):
            raise RuntimeContractError("invalid_runtime", "运行时目录不存在或不安全")
        manifest_path = root / "runtime-manifest.json"
        if not manifest_path.is_file() or is_link_or_reparse(manifest_path):
            raise RuntimeContractError("missing_manifest", "运行时缺少 runtime-manifest.json")
        if manifest_path.stat().st_size > MAX_MANIFEST_SIZE:
            raise RuntimeContractError("manifest_too_large", "运行时清单过大")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeContractError("invalid_manifest", "运行时清单不是有效 UTF-8 JSON") from exc
        manifest = RuntimeManifest.from_dict(payload)
        if manifest.platform != self.expected_platform:
            raise RuntimeContractError("platform_mismatch", "运行时平台与当前系统不匹配", "platform")

        signature_path = root / "runtime-manifest.sig"
        signed = signature_path.is_file()
        if not signed and not self.allow_unsigned:
            raise RuntimeContractError("signature_required", "生产模式拒绝未签名的 GPU 运行时")
        if signed:
            if is_link_or_reparse(signature_path) or signature_path.stat().st_size > 1024:
                raise RuntimeContractError("invalid_signature", "运行时签名文件无效")
            try:
                signature = base64.b64decode(signature_path.read_text("ascii").strip(), validate=True)
                Ed25519PublicKey.from_public_bytes(self.public_key).verify(
                    signature, canonical_manifest_bytes(payload)
                )
            except (ValueError, UnicodeError, InvalidSignature) as exc:
                raise RuntimeContractError("invalid_signature", "运行时签名校验失败") from exc

        allowed = {"runtime-manifest.json", *manifest.files}
        if signed:
            allowed.add("runtime-manifest.sig")
        actual: set[str] = set()
        members = list(root.rglob("*"))
        if len(members) > MAX_RUNTIME_FILES:
            raise RuntimeContractError("too_many_files", "运行时文件数量超过安全限制")
        for member in members:
            if is_link_or_reparse(member):
                raise RuntimeContractError("unsafe_link", "运行时不能包含符号链接或重解析点")
            if member.is_file():
                relative = member.relative_to(root).as_posix()
                actual.add(relative)
            elif not member.is_dir():
                raise RuntimeContractError("unsafe_member", "运行时包含不安全成员")
        if actual != allowed:
            detail = "、".join(sorted(actual.symmetric_difference(allowed)))
            raise RuntimeContractError("file_contract_mismatch", f"运行时文件与清单不一致：{detail}")
        file_entries = list(manifest.files.items())
        worker_count = min(8, max(1, os.cpu_count() or 1), max(1, len(file_entries)))

        def verify_hash(entry: tuple[str, str]) -> tuple[str, str, str]:
            relative, expected = entry
            return relative, expected, _sha256(root / relative)

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="runtime-hash") as pool:
            for offset in range(0, len(file_entries), 512):
                batch = file_entries[offset:offset + 512]
                for relative, expected, actual_hash in pool.map(verify_hash, batch):
                    if actual_hash != expected:
                        raise RuntimeContractError("hash_mismatch", f"运行时文件校验失败：{relative}")
        return RuntimeRecord(root, manifest, signed)


class RuntimeRegistry:
    def __init__(self, paths: VoiceStoragePaths, verifier: RuntimeVerifier):
        self.paths = paths.ensure()
        self.verifier = verifier
        self.records: dict[str, RuntimeRecord] = {}
        self.errors: list[dict[str, str]] = []
        self.refresh()

    def refresh(self) -> list[RuntimeRecord]:
        records: dict[str, RuntimeRecord] = {}
        errors: list[dict[str, str]] = []
        for entry in self.paths.runtimes.iterdir():
            if entry.name.startswith("."):
                continue
            try:
                record = self.verifier.verify_directory(entry)
                if record.runtime_id != entry.name:
                    raise RuntimeContractError("runtime_id_mismatch", "运行时目录名与 ID 不一致")
                records[record.runtime_id] = record
            except (RuntimeContractError, OSError) as exc:
                errors.append({"runtime_id": entry.name, "code": getattr(exc, "code", "io_error"), "message": str(exc)})
        self.records = records
        self.errors = errors
        return list(records.values())

    def find_compatible(self, model_version: str, language: str = "ja") -> RuntimeRecord | None:
        matches = [
            record for record in self.records.values()
            if model_version in record.manifest.supported_model_versions
            and language in record.manifest.supported_languages
        ]
        return sorted(matches, key=lambda item: item.manifest.build_version, reverse=True)[0] if matches else None

    def status(self) -> dict:
        records = [record.to_dict() for record in sorted(self.records.values(), key=lambda item: item.runtime_id)]
        return {
            "state": "ready" if records else "missing",
            "runtimes": records,
            "errors": list(self.errors),
            "runtime_root": str(self.paths.runtimes),
        }
