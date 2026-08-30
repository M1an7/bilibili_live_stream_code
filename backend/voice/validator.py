from __future__ import annotations

import hashlib
import json
import os
import stat
import wave
from dataclasses import dataclass
from pathlib import Path

from .manifest import VoiceContractError, VoiceManifest


MAX_MEMBERS = 4_096
MAX_FILE_SIZE = 2 * 1024**3
MAX_TOTAL_SIZE = 4 * 1024**3
ALLOWED_EXACT = {
    "manifest.json",
    "model/gpt.ckpt",
    "model/sovits.pth",
    "reference.wav",
    "reference.txt",
    "preview.wav",
    "LICENSE.txt",
}


def is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def validate_pcm_wav(path: Path, label: str = "参考音频") -> None:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE" or audio.getsampwidth() not in (1, 2, 3, 4):
                raise VoiceContractError("invalid_wav", f"{label}必须是 PCM WAV 文件")
            if audio.getnchannels() not in (1, 2) or audio.getframerate() <= 0 or audio.getnframes() <= 0:
                raise VoiceContractError("invalid_wav", f"{label}的 WAV 参数无效")
    except (wave.Error, EOFError, OSError) as exc:
        raise VoiceContractError("invalid_wav", f"{label}必须是有效的 PCM WAV 文件") from exc


@dataclass(frozen=True)
class VoiceValidationResult:
    valid: bool
    health: str
    code: str
    message: str
    manifest: VoiceManifest | None


class VoicePackValidator:
    def validate_directory(self, path: Path) -> VoiceValidationResult:
        root = Path(path)
        if not root.is_dir() or is_link_or_reparse(root):
            raise VoiceContractError("invalid_pack", "音色包目录不存在或是符号链接")

        members = list(root.rglob("*"))
        if len(members) > MAX_MEMBERS:
            raise VoiceContractError("too_many_files", "音色包文件数量超过安全限制")

        files: dict[str, Path] = {}
        total_size = 0
        for member in members:
            if is_link_or_reparse(member):
                raise VoiceContractError("unsafe_link", "音色包不能包含符号链接或重解析点")
            relative = member.relative_to(root).as_posix()
            if member.is_dir():
                if relative != "model":
                    raise VoiceContractError("unexpected_path", f"音色包包含不允许的目录：{relative}")
                continue
            if not member.is_file() or relative not in ALLOWED_EXACT:
                raise VoiceContractError("unexpected_file", f"音色包包含不允许的文件：{relative}")
            size = member.stat().st_size
            if size > MAX_FILE_SIZE:
                raise VoiceContractError("file_too_large", f"文件超过 2 GiB 限制：{relative}")
            total_size += size
            if total_size > MAX_TOTAL_SIZE:
                raise VoiceContractError("pack_too_large", "音色包总大小超过 4 GiB 限制")
            files[relative] = member

        manifest_path = files.get("manifest.json")
        if not manifest_path:
            raise VoiceContractError("missing_manifest", "音色包缺少 manifest.json")
        if manifest_path.stat().st_size > 1024 * 1024:
            raise VoiceContractError("manifest_too_large", "音色清单超过 1 MiB 限制")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise VoiceContractError("invalid_manifest", "manifest.json 不是有效的 UTF-8 JSON") from exc
        manifest = VoiceManifest.from_dict(payload)

        expected_files = {"manifest.json", *manifest.files.keys()}
        if set(files) != expected_files:
            missing = expected_files - set(files)
            extra = set(files) - expected_files
            detail = "、".join(sorted(missing or extra))
            raise VoiceContractError("file_contract_mismatch", f"音色包文件与清单不一致：{detail}")

        for relative, expected_hash in manifest.files.items():
            if sha256_file(files[relative]) != expected_hash:
                raise VoiceContractError("hash_mismatch", f"文件完整性校验失败：{relative}")

        if files[manifest.license_file].stat().st_size == 0:
            raise VoiceContractError("missing_license", "授权说明文件不能为空")
        try:
            if not files[manifest.reference_text].read_text("utf-8").strip():
                raise VoiceContractError("missing_reference_text", "参考音频对应台词不能为空")
        except UnicodeError as exc:
            raise VoiceContractError("invalid_reference_text", "参考台词必须是 UTF-8 文本") from exc
        validate_pcm_wav(files[manifest.reference_audio])
        if manifest.preview_audio:
            validate_pcm_wav(files[manifest.preview_audio], "试听音频")

        return VoiceValidationResult(
            valid=True,
            health="runtime_required",
            code="runtime_required",
            message="文件已安全导入，等待安装 CPU 运行时后试听并启用",
            manifest=manifest,
        )
