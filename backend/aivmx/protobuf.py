from __future__ import annotations

import base64
import json
import stat
import uuid
from pathlib import Path

from backend.voice.validator import is_link_or_reparse

from .contract import AivmxContractError, AivmxMetadata, AivmxStyle


MAX_METADATA_ENTRY_BYTES = 16 * 1024 * 1024
MAX_METADATA_ENTRIES = 64


def _read_varint(stream, *, limit: int = 10) -> int:
    value = 0
    for offset in range(limit):
        raw = stream.read(1)
        if not raw:
            raise AivmxContractError("invalid_onnx", "AIVMX protobuf 意外结束")
        byte = raw[0]
        value |= (byte & 0x7F) << (offset * 7)
        if byte < 0x80:
            return value
    raise AivmxContractError("invalid_onnx", "AIVMX protobuf varint 无效")


def _skip_field(stream, wire_type: int, file_size: int) -> None:
    if wire_type == 0:
        _read_varint(stream)
        return
    if wire_type == 1:
        length = 8
    elif wire_type == 2:
        length = _read_varint(stream)
    elif wire_type == 5:
        length = 4
    else:
        raise AivmxContractError("invalid_onnx", "AIVMX protobuf wire type 不受支持")
    if length < 0 or stream.tell() + length > file_size:
        raise AivmxContractError("invalid_onnx", "AIVMX protobuf 字段长度无效")
    stream.seek(length, 1)


def _parse_string_entry(payload: bytes) -> tuple[str, str]:
    from io import BytesIO

    stream = BytesIO(payload)
    values: dict[int, str] = {}
    while stream.tell() < len(payload):
        tag = _read_varint(stream)
        field_number, wire_type = tag >> 3, tag & 7
        if field_number not in (1, 2) or wire_type != 2:
            _skip_field(stream, wire_type, len(payload))
            continue
        length = _read_varint(stream)
        if length > MAX_METADATA_ENTRY_BYTES or stream.tell() + length > len(payload):
            raise AivmxContractError("invalid_metadata", "AIVMX 元数据条目过大或损坏")
        try:
            values[field_number] = stream.read(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AivmxContractError("invalid_metadata", "AIVMX 元数据不是有效 UTF-8") from exc
    if 1 not in values or 2 not in values:
        raise AivmxContractError("invalid_metadata", "AIVMX 元数据键值不完整")
    return values[1], values[2]


def _safe_uuid(value, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AivmxContractError("invalid_manifest", f"AIVMX UUID 无效：{field}", field) from exc
    if parsed.version not in (4, 5):
        raise AivmxContractError("invalid_manifest", f"AIVMX UUID 版本无效：{field}", field)
    return str(parsed)


def _optional_nonnegative(value, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AivmxContractError("invalid_manifest", f"AIVMX 字段无效：{field}", field)
    return value


class AivmxMetadataReader:
    def __init__(self, max_model_bytes: int = 2 * 1024**3):
        self.max_model_bytes = max_model_bytes

    def _raw_metadata(self, path: Path) -> dict[str, str]:
        file_size = path.stat().st_size
        metadata: dict[str, str] = {}
        with path.open("rb") as stream:
            while stream.tell() < file_size:
                tag = _read_varint(stream)
                field_number, wire_type = tag >> 3, tag & 7
                if field_number == 14 and wire_type == 2:
                    length = _read_varint(stream)
                    if length > MAX_METADATA_ENTRY_BYTES or stream.tell() + length > file_size:
                        raise AivmxContractError("invalid_metadata", "AIVMX 元数据条目过大或损坏")
                    key, value = _parse_string_entry(stream.read(length))
                    metadata[key] = value
                    if len(metadata) > MAX_METADATA_ENTRIES:
                        raise AivmxContractError("invalid_metadata", "AIVMX 元数据条目过多")
                else:
                    _skip_field(stream, wire_type, file_size)
        return metadata

    def read(self, source: Path | str) -> AivmxMetadata:
        path = Path(source)
        if path.suffix.lower() != ".aivmx":
            raise AivmxContractError("invalid_extension", "请选择 .aivmx 音色文件", "path")
        try:
            info = path.lstat()
        except OSError as exc:
            raise AivmxContractError("missing_source", "AIVMX 音色文件不存在", "path") from exc
        if not stat.S_ISREG(info.st_mode) or is_link_or_reparse(path):
            raise AivmxContractError("unsafe_source", "AIVMX 音色文件不安全", "path")
        if info.st_size <= 0:
            raise AivmxContractError("invalid_onnx", "AIVMX 音色文件为空", "path")
        if info.st_size > self.max_model_bytes:
            raise AivmxContractError("model_too_large", "AIVMX 音色文件超过大小限制", "path")

        raw = self._raw_metadata(path)
        required = {"aivm_manifest", "aivm_hyper_parameters", "aivm_style_vectors"}
        if not required.issubset(raw):
            raise AivmxContractError("metadata_missing", "AIVMX 缺少必要的音色元数据")
        try:
            manifest = json.loads(raw["aivm_manifest"])
            hyper_parameters = json.loads(raw["aivm_hyper_parameters"])
            style_vectors = base64.b64decode(raw["aivm_style_vectors"], validate=True)
        except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
            raise AivmxContractError("invalid_metadata", "AIVMX 元数据无法解析") from exc
        if not isinstance(manifest, dict) or not isinstance(hyper_parameters, dict) or not style_vectors:
            raise AivmxContractError("invalid_metadata", "AIVMX 元数据结构无效")
        if manifest.get("manifest_version") != "1.0":
            raise AivmxContractError("manifest_version_not_supported", "不支持的 AIVMX 清单版本", "manifest_version")
        if manifest.get("model_architecture") != "Style-Bert-VITS2":
            raise AivmxContractError("architecture_not_supported", "仅支持多语言 Style-Bert-VITS2 AIVMX", "model_architecture")
        if manifest.get("model_format") != "ONNX":
            raise AivmxContractError("model_format_not_supported", "AIVMX 必须包含 ONNX 模型", "model_format")
        license_text = manifest.get("license")
        if not isinstance(license_text, str) or not license_text.strip():
            raise AivmxContractError("license_required", "AIVMX 必须包含授权说明", "license")
        model_uuid = _safe_uuid(manifest.get("uuid"), "uuid")
        display_name = manifest.get("name")
        version = manifest.get("version")
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 80:
            raise AivmxContractError("invalid_manifest", "AIVMX 音色名称无效", "name")
        if not isinstance(version, str) or not version.strip():
            raise AivmxContractError("invalid_manifest", "AIVMX 音色版本无效", "version")
        speakers = manifest.get("speakers")
        if not isinstance(speakers, list) or len(speakers) != 1 or not isinstance(speakers[0], dict):
            raise AivmxContractError("speaker_not_supported", "首版实时 CPU 音色仅支持单说话人 AIVMX", "speakers")
        speaker = speakers[0]
        _safe_uuid(speaker.get("uuid"), "speakers.0.uuid")
        speaker_name = speaker.get("name")
        speaker_id = speaker.get("local_id")
        languages = speaker.get("supported_languages")
        if not isinstance(speaker_name, str) or not speaker_name.strip():
            raise AivmxContractError("invalid_manifest", "AIVMX 说话人名称无效", "speakers.0.name")
        if not isinstance(speaker_id, int) or isinstance(speaker_id, bool) or speaker_id < 0:
            raise AivmxContractError("invalid_manifest", "AIVMX 说话人 ID 无效", "speakers.0.local_id")
        if not isinstance(languages, list) or not all(isinstance(item, str) for item in languages):
            raise AivmxContractError("invalid_manifest", "AIVMX 语言声明无效", "speakers.0.supported_languages")
        if "zh-CN" not in languages:
            raise AivmxContractError("chinese_not_supported", "该 AIVMX 未声明中文 zh-CN 合成能力", "speakers.0.supported_languages")
        styles_payload = speaker.get("styles")
        if not isinstance(styles_payload, list) or not styles_payload:
            raise AivmxContractError("styles_required", "AIVMX 至少需要一个音色风格", "speakers.0.styles")
        styles: list[AivmxStyle] = []
        style_ids: set[int] = set()
        for index, style in enumerate(styles_payload):
            if not isinstance(style, dict):
                raise AivmxContractError("invalid_manifest", "AIVMX 风格声明无效", f"speakers.0.styles.{index}")
            name, style_id = style.get("name"), style.get("local_id")
            if not isinstance(name, str) or not name.strip() or len(name) > 20:
                raise AivmxContractError("invalid_manifest", "AIVMX 风格名称无效", f"speakers.0.styles.{index}.name")
            if not isinstance(style_id, int) or isinstance(style_id, bool) or not 0 <= style_id <= 31 or style_id in style_ids:
                raise AivmxContractError("invalid_manifest", "AIVMX 风格 ID 无效", f"speakers.0.styles.{index}.local_id")
            style_ids.add(style_id)
            styles.append(AivmxStyle(name.strip(), style_id, speaker_id, model_uuid))
        creators = manifest.get("creators", [])
        if not isinstance(creators, list) or not all(isinstance(item, str) and item.strip() for item in creators):
            raise AivmxContractError("invalid_manifest", "AIVMX 制作者字段无效", "creators")
        description = manifest.get("description", "")
        if not isinstance(description, str):
            raise AivmxContractError("invalid_manifest", "AIVMX 描述字段无效", "description")
        return AivmxMetadata(
            manifest_version="1.0",
            model_uuid=model_uuid,
            display_name=display_name.strip(),
            description=description,
            creators=tuple(item.strip() for item in creators),
            license_text=license_text.strip(),
            architecture="Style-Bert-VITS2",
            model_format="ONNX",
            version=version,
            training_epochs=_optional_nonnegative(manifest.get("training_epochs"), "training_epochs"),
            training_steps=_optional_nonnegative(manifest.get("training_steps"), "training_steps"),
            speaker_name=speaker_name.strip(),
            languages=tuple(languages),
            styles=tuple(styles),
            hyper_parameters=hyper_parameters,
            style_vectors=style_vectors,
        )
