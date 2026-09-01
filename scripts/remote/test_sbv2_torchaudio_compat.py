import os
import sys
from pathlib import Path


repo_root = Path(os.environ["STYLE_BERT_VITS2_ROOT"])
sys.path.insert(0, str(repo_root))

from sbv2_torchaudio_compat import install_torchaudio_compat


install_torchaudio_compat()

import torchaudio
from pyannote.audio.core.io import get_torchaudio_info


wav_path = next(Path(os.environ["STYLE_BERT_VITS2_DATASET"]).joinpath("raw").glob("*.wav"))
metadata = get_torchaudio_info({"audio": str(wav_path)})

assert metadata.sample_rate == 32000
assert metadata.num_channels == 1
assert metadata.num_frames > 0
assert metadata.bits_per_sample == 16
assert "soundfile" in torchaudio.list_audio_backends()

waveform, sample_rate = torchaudio.load(str(wav_path), backend="soundfile")
assert sample_rate == metadata.sample_rate
assert waveform.shape == (metadata.num_channels, metadata.num_frames)
assert waveform.dtype.is_floating_point
print(
    "torchaudio_compat=ok",
    metadata.sample_rate,
    metadata.num_channels,
    metadata.num_frames,
    metadata.bits_per_sample,
)
