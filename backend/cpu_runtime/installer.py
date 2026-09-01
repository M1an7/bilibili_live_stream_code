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

from .manifest import CpuRuntimeContractError
from .registry import CpuRuntimeRecord, CpuRuntimeVerifier


Progress = Callable[[dict], None]
MAX_FILES = 100_000
MAX_FILE_SIZE = 4 * 1024**3
MAX_TOTAL_SIZE = 12 * 1024**3


def _safe_member(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise CpuRuntimeContractError("unsafe_archive_path", "CPU 运行时压缩包包含不安全路径")
    path = PurePosixPath(name.rstrip("/"))
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CpuRuntimeContractError("unsafe_archive_path", f"CPU 运行时压缩包包含不安全路径：{name}")
    return path.as_posix()


class CpuRuntimeInstaller:
    def __init__(self, paths: VoiceStoragePaths, verifier: CpuRuntimeVerifier):
        self.paths = paths.ensure()
        self.verifier = verifier
        self.staging_root = self.paths.cpu_runtimes / ".staging"
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def _space(self, required: int) -> None:
        reserve = max(512 * 1024**2, required // 5)
        if shutil.disk_usage(self.paths.cpu_runtimes).free < required + reserve:
            raise CpuRuntimeContractError("insufficient_disk_space", "CPU 运行时数据盘空间不足")

    def install_directory(self, path: Path | str, progress: Progress | None = None) -> CpuRuntimeRecord:
        source = Path(path)
        if not source.is_dir() or is_link_or_reparse(source):
            raise CpuRuntimeContractError("invalid_runtime", "CPU 运行时目录不存在或不安全")
        size = 0
        for member in source.rglob("*"):
            if is_link_or_reparse(member):
                raise CpuRuntimeContractError("unsafe_link", "CPU 运行时目录不能包含链接")
            if member.is_file():
                size += member.stat().st_size
        self._space(size)
        staging = self.staging_root / f"directory-{uuid.uuid4().hex}"
        try:
            if progress:
                progress({"phase": "copy", "percent": 5, "message": "正在复制 CPU 运行时"})
            shutil.copytree(source, staging)
            return self._install_staged(staging, progress)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def install_zip(self, path: Path | str, progress: Progress | None = None) -> CpuRuntimeRecord:
        archive = Path(path)
        if not archive.is_file() or is_link_or_reparse(archive):
            raise CpuRuntimeContractError("invalid_archive", "CPU 运行时压缩包不存在或不安全")
        staging = self.staging_root / f"archive-{uuid.uuid4().hex}"
        try:
            with zipfile.ZipFile(archive) as package:
                members = package.infolist()
                if len(members) > MAX_FILES:
                    raise CpuRuntimeContractError("too_many_files", "CPU 运行时压缩包文件过多")
                normalized = []
                total = 0
                for member in members:
                    relative = _safe_member(member.filename)
                    mode = (member.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise CpuRuntimeContractError("unsafe_archive_link", "CPU 运行时压缩包不能包含链接")
                    if member.file_size > MAX_FILE_SIZE:
                        raise CpuRuntimeContractError("archive_file_too_large", f"CPU 运行时压缩包成员过大：{relative}")
                    total += member.file_size
                    if total > MAX_TOTAL_SIZE:
                        raise CpuRuntimeContractError("archive_too_large", "CPU 运行时解压后超过 12 GiB")
                    normalized.append((member, relative))
                self._space(total)
                staging.mkdir()
                for member, relative in normalized:
                    target = staging / relative
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with package.open(member) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination, 1024 * 1024)
            roots = [entry for entry in staging.iterdir() if entry.is_dir()]
            source = roots[0] if len(roots) == 1 and not (staging / "runtime-manifest.json").is_file() else staging
            return self._install_staged(source, progress)
        except zipfile.BadZipFile as exc:
            raise CpuRuntimeContractError("invalid_archive", "CPU 运行时压缩包已损坏") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _install_staged(self, staging: Path, progress: Progress | None) -> CpuRuntimeRecord:
        if progress:
            progress({"phase": "verify", "percent": 75, "message": "正在校验 CPU 运行时"})
        record = self.verifier.verify_directory(staging)
        destination = self.paths.cpu_runtimes / record.runtime_id
        incoming = self.paths.cpu_runtimes / f".incoming-{uuid.uuid4().hex}"
        backup = self.paths.cpu_runtimes / f".backup-{uuid.uuid4().hex}"
        moved_old = False
        try:
            os.replace(staging, incoming)
            if destination.exists():
                if is_link_or_reparse(destination):
                    raise CpuRuntimeContractError("unsafe_destination", "现有 CPU 运行时目录不安全")
                os.replace(destination, backup)
                moved_old = True
            os.replace(incoming, destination)
            installed = self.verifier.verify_directory(destination)
        except BaseException:
            if destination.exists() and moved_old:
                shutil.rmtree(destination, ignore_errors=True)
            if moved_old and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        if progress:
            progress({"phase": "done", "percent": 100, "message": "CPU 运行时安装完成"})
        return installed
