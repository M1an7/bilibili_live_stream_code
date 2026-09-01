from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AivmxContractError(ValueError):
    def __init__(self, code: str, message: str, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "field": self.field}


@dataclass(frozen=True)
class AivmxStyle:
    name: str
    style_id: int
    speaker_id: int
    model_uuid: str

    @property
    def voice_key(self) -> str:
        return f"aivmx:{self.model_uuid}:{self.style_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "style_id": self.style_id,
            "speaker_id": self.speaker_id,
            "voice_key": self.voice_key,
        }


@dataclass(frozen=True)
class AivmxMetadata:
    manifest_version: str
    model_uuid: str
    display_name: str
    description: str
    creators: tuple[str, ...]
    license_text: str
    architecture: str
    model_format: str
    version: str
    training_epochs: int | None
    training_steps: int | None
    speaker_name: str
    languages: tuple[str, ...]
    styles: tuple[AivmxStyle, ...]
    hyper_parameters: dict[str, Any]
    style_vectors: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "model_uuid": self.model_uuid,
            "display_name": self.display_name,
            "description": self.description,
            "creators": list(self.creators),
            "license": self.license_text,
            "architecture": self.architecture,
            "model_format": self.model_format,
            "version": self.version,
            "training_epochs": self.training_epochs,
            "training_steps": self.training_steps,
            "speaker_name": self.speaker_name,
            "languages": list(self.languages),
            "styles": [style.to_dict() for style in self.styles],
        }
