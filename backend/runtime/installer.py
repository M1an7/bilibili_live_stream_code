from __future__ import annotations

import os
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from backend.voice.storage import VoiceStoragePaths
from backend.voice.validator import is_link_or_reparse

from .manifest import RuntimeContractError
from .registry import RuntimeRecord, RuntimeVerifier


Progress = Callable[[dict], None]
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_FILE_SIZE = 4 * 1024**3
MAX_ARCHIVE_TOTAL_SIZE = 20 * 1024**3


def _report(progress: Progress | None, phase: str, percent: int, message: str) -> None:
    if progress:
        progress({"phase": phase, "percent": percent, "message": message})


def _safe_member(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise RuntimeContractError("unsafe_archive_path", "压缩包包含不安全路径")
    path = PurePosixPath(name.rstrip("/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeContractError("unsafe_archive_path", f"压缩包包含不安全路径：{name}")
    return path.as_posix()


class RuntimeInstaller:
    def __init__(self, paths: VoiceStoragePaths, verifier: RuntimeVerifier):
        self.paths = paths.ensure()
        self.verifier = verifier

    def _check_space(self, required: int) -> None:
        free = shutil.disk_usage(self.paths.staging).free
        reserve = max(512 * 1024**2, required // 5)
        if free < required + reserve:
            raise RuntimeContractError(
                "insufficient_disk_space",
                f"数据盘空间不足，需要至少 {(required + reserve) / 1024**3:.1f} GiB 可用空间",
            )

    def install_zip(self, path: Path, progress: Progress | None = None) -> RuntimeRecord:
        archive = Path(path)
        if not archive.is_file() or is_link_or_reparse(archive):
            raise RuntimeContractError("invalid_archive", "GPU 运行时压缩包不存在或不安全")
        staging = self.paths.staging / f"runtime-zip-{uuid.uuid4().hex}"
        try:
            with zipfile.ZipFile(archive) as package:
                members = package.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise RuntimeContractError("too_many_files", "GPU 运行时压缩包文件过多")
                total = 0
                normalized: list[tuple[zipfile.ZipInfo, str]] = []
                for member in members:
                    relative = _safe_member(member.filename)
                    mode = (member.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise RuntimeContractError("unsafe_archive_link", "压缩包不能包含符号链接")
                    if member.file_size > MAX_ARCHIVE_FILE_SIZE:
                        raise RuntimeContractError("archive_file_too_large", f"压缩包成员过大：{relative}")
                    total += member.file_size
                    if total > MAX_ARCHIVE_TOTAL_SIZE:
                        raise RuntimeContractError("archive_too_large", "GPU 运行时压缩包解压后超过 20 GiB")
                    normalized.append((member, relative))
                self._check_space(total)
                staging.mkdir(parents=True)
                _report(progress, "extract", 5, "正在安全解压 GPU 运行时")
                for index, (member, relative) in enumerate(normalized):
                    target = staging / relative
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with package.open(member) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination, 1024 * 1024)
                    if index % 200 == 0:
                        _report(progress, "extract", min(70, 5 + int(65 * index / max(1, len(normalized)))), "正在解压 GPU 运行时")
            roots = [item for item in staging.iterdir() if item.is_dir()]
            source = roots[0] if len(roots) == 1 and not (staging / "runtime-manifest.json").exists() else staging
            return self._install_staged(source, progress)
        except zipfile.BadZipFile as exc:
            raise RuntimeContractError("invalid_archive", "GPU 运行时压缩包已损坏") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def install_directory(self, path: Path, progress: Progress | None = None) -> RuntimeRecord:
        source = Path(path)
        if not source.is_dir() or is_link_or_reparse(source):
            raise RuntimeContractError("invalid_runtime", "GPU 运行时目录不存在或不安全")
        size = 0
        for member in source.rglob("*"):
            if is_link_or_reparse(member):
                raise RuntimeContractError("unsafe_link", "GPU 运行时目录不能包含链接")
            if member.is_file():
                size += member.stat().st_size
        self._check_space(size)
        staging = self.paths.staging / f"runtime-dir-{uuid.uuid4().hex}"
        _report(progress, "copy", 5, "正在复制 GPU 运行时")
        try:
            shutil.copytree(source, staging)
            return self._install_staged(staging, progress)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _install_staged(self, staging: Path, progress: Progress | None) -> RuntimeRecord:
        _report(progress, "verify", 75, "正在校验签名和文件完整性")
        record = self.verifier.verify_directory(staging)
        destination = self.paths.runtimes / record.runtime_id
        incoming = self.paths.runtimes / f".incoming-{uuid.uuid4().hex}"
        backup = self.paths.runtimes / f".backup-{uuid.uuid4().hex}"
        moved_old = False
        try:
            os.replace(staging, incoming)
            if destination.exists():
                if is_link_or_reparse(destination):
                    raise RuntimeContractError("unsafe_destination", "现有 GPU 运行时目录不安全")
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
        installed = self.verifier.verify_directory(destination)
        _report(progress, "complete", 100, "GPU 运行时安装完成")
        return installed
