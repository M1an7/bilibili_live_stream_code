"""Compatibility helpers for pyannote.audio 3.x on torchaudio 2.11+."""

from dataclasses import dataclass
from os import PathLike
from typing import BinaryIO, Union

import soundfile
import torch
import torchaudio


@dataclass(frozen=True)
class AudioMetaData:
    sample_rate: int
    num_frames: int
    num_channels: int
    bits_per_sample: int
    encoding: str


def _bits_per_sample(subtype: str) -> int:
    if subtype.startswith("PCM_"):
        suffix = subtype.removeprefix("PCM_")
        return int(suffix) if suffix.isdigit() else 0
    if subtype == "FLOAT":
        return 32
    if subtype == "DOUBLE":
        return 64
    return 0


def _info(
    uri: Union[BinaryIO, str, PathLike[str]],
    backend: str | None = None,
) -> AudioMetaData:
    del backend
    metadata = soundfile.info(uri)
    subtype = metadata.subtype or ""
    return AudioMetaData(
        sample_rate=metadata.samplerate,
        num_frames=metadata.frames,
        num_channels=metadata.channels,
        bits_per_sample=_bits_per_sample(subtype),
        encoding=subtype,
    )


def _load(
    uri: Union[BinaryIO, str, PathLike[str]],
    frame_offset: int = 0,
    num_frames: int = -1,
    normalize: bool = True,
    channels_first: bool = True,
    format: str | None = None,
    buffer_size: int = 4096,
    backend: str | None = None,
) -> tuple[torch.Tensor, int]:
    del normalize, format, buffer_size, backend
    with soundfile.SoundFile(uri) as audio_file:
        audio_file.seek(frame_offset)
        frames = -1 if num_frames < 0 else num_frames
        samples = audio_file.read(frames=frames, dtype="float32", always_2d=True)
        sample_rate = audio_file.samplerate
    if channels_first:
        samples = samples.T
    return torch.from_numpy(samples.copy()), sample_rate


def install_torchaudio_compat() -> None:
    if not hasattr(torchaudio, "AudioMetaData"):
        torchaudio.AudioMetaData = AudioMetaData
    if not hasattr(torchaudio, "info"):
        torchaudio.info = _info
    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["soundfile"]
    # torchaudio 2.11 routes load through TorchCodec, which is not available in
    # the CUDA 13 training image. These training inputs are PCM WAV files, so a
    # soundfile-backed loader provides the same normalized float tensor API.
    torchaudio.load = _load
