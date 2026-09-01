from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from backend.runtime.keys import release_public_key
from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import is_link_or_reparse

from .manifest import CpuRuntimeContractError, CpuRuntimeManifest, canonical_cpu_manifest_bytes


MAX_RUNTIME_FILES = 100_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def current_platform_id() -> str:
    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-x86_64"
    return f"{sys.platform}-x86_64"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class CpuRuntimeRecord:
    path: Path
    manifest: CpuRuntimeManifest
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
            "providers": list(self.manifest.providers),
            "default_threads": self.manifest.default_threads,
            "supported_languages": list(self.manifest.supported_languages),
            "gpu": False,
        }


class CpuRuntimeVerifier:
    def __init__(self, public_key: bytes | None = None, expected_platform: str | None = None, allow_unsigned: bool = False):
        configured = os.environ.get("BILILIVE_RUNTIME_PUBLIC_KEY", "").strip()
        if public_key is None and configured:
            try:
                public_key = base64.b64decode(configured, validate=True)
            except ValueError as exc:
                raise CpuRuntimeContractError("invalid_public_key", "CPU 运行时公钥配置无效") from exc
        self.public_key = public_key if public_key is not None else release_public_key()
        self.expected_platform = expected_platform or current_platform_id()
        self.allow_unsigned = allow_unsigned

    def verify_directory(self, path: Path | str) -> CpuRuntimeRecord:
        root = Path(path)
        if not root.is_dir() or is_link_or_reparse(root):
            raise CpuRuntimeContractError("invalid_runtime", "CPU 运行时目录不存在或不安全")
        manifest_path = root / "runtime-manifest.json"
        if not manifest_path.is_file() or is_link_or_reparse(manifest_path) or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise CpuRuntimeContractError("missing_manifest", "CPU 运行时缺少有效清单")
        try:
            payload = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CpuRuntimeContractError("invalid_manifest", "CPU 运行时清单不是有效 UTF-8 JSON") from exc
        manifest = CpuRuntimeManifest.from_dict(payload)
        if manifest.platform != self.expected_platform:
            raise CpuRuntimeContractError("platform_mismatch", "CPU 运行时平台与当前系统不匹配", "platform")
        signature_path = root / "runtime-manifest.sig"
        signed = signature_path.is_file()
        if not signed and not self.allow_unsigned:
            raise CpuRuntimeContractError("signature_required", "生产模式拒绝未签名的 CPU 运行时")
        if signed:
            try:
                signature = base64.b64decode(signature_path.read_text("ascii").strip(), validate=True)
                Ed25519PublicKey.from_public_bytes(self.public_key).verify(signature, canonical_cpu_manifest_bytes(payload))
            except (ValueError, UnicodeError, InvalidSignature) as exc:
                raise CpuRuntimeContractError("invalid_signature", "CPU 运行时签名校验失败") from exc
        allowed = {"runtime-manifest.json", *manifest.files}
        if signed:
            allowed.add("runtime-manifest.sig")
        members = list(root.rglob("*"))
        if len(members) > MAX_RUNTIME_FILES:
            raise CpuRuntimeContractError("too_many_files", "CPU 运行时文件数量超过安全限制")
        actual: set[str] = set()
        for member in members:
            if is_link_or_reparse(member):
                raise CpuRuntimeContractError("unsafe_link", "CPU 运行时不能包含链接")
            if member.is_file():
                actual.add(member.relative_to(root).as_posix())
            elif not member.is_dir():
                raise CpuRuntimeContractError("unsafe_member", "CPU 运行时包含不安全成员")
        if actual != allowed:
            detail = "、".join(sorted(actual.symmetric_difference(allowed)))
            raise CpuRuntimeContractError("file_contract_mismatch", f"CPU 运行时文件与清单不一致：{detail}")
        entries = list(manifest.files.items())
        workers = min(8, max(1, os.cpu_count() or 1), max(1, len(entries)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cpu-runtime-hash") as pool:
            actual_hashes = pool.map(lambda entry: sha256_file(root / entry[0]), entries)
            for (relative, expected), actual_hash in zip(entries, actual_hashes):
                if expected != actual_hash:
                    raise CpuRuntimeContractError("hash_mismatch", f"CPU 运行时文件校验失败：{relative}")
        return CpuRuntimeRecord(root, manifest, signed)


class CpuRuntimeRegistry:
    def __init__(self, paths: VoiceStoragePaths, verifier: CpuRuntimeVerifier):
        self.paths = paths.ensure()
        self.verifier = verifier
        self.records: dict[str, CpuRuntimeRecord] = {}
        self.errors: list[dict] = []
        self.refresh()

    def refresh(self) -> list[CpuRuntimeRecord]:
        records: dict[str, CpuRuntimeRecord] = {}
        errors: list[dict] = []
        for entry in self.paths.cpu_runtimes.iterdir():
            if entry.name.startswith("."):
                continue
            try:
                record = self.verifier.verify_directory(entry)
                if record.runtime_id != entry.name:
                    raise CpuRuntimeContractError("runtime_id_mismatch", "CPU 运行时目录名与 ID 不一致")
                records[record.runtime_id] = record
            except (CpuRuntimeContractError, OSError) as exc:
                errors.append({"runtime_id": entry.name, "code": getattr(exc, "code", "io_error"), "message": str(exc)})
        self.records, self.errors = records, errors
        return list(records.values())

    def find_compatible(self, architecture: str, language: str) -> CpuRuntimeRecord | None:
        matches = [
            record for record in self.records.values()
            if architecture in record.manifest.supported_architectures
            and language in record.manifest.supported_languages
        ]
        return sorted(matches, key=lambda item: item.manifest.build_version, reverse=True)[0] if matches else None

    def status(self) -> dict:
        records = [record.to_dict() for record in sorted(self.records.values(), key=lambda item: item.runtime_id)]
        return {
            "state": "ready" if records else "missing",
            "runtimes": records,
            "errors": list(self.errors),
            "runtime_root": str(self.paths.cpu_runtimes),
        }
