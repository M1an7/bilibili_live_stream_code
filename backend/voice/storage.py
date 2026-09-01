from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class VoiceStoragePaths:
    root: Path
    voices: Path
    aivmx_voices: Path
    runtimes: Path
    cpu_runtimes: Path
    speech_cache: Path
    staging: Path
    logs: Path
    voice_state: Path

    @classmethod
    def resolve(
        cls,
        platform_name: str | None = None,
        env: Mapping[str, str] | None = None,
        home: Path | str | None = None,
    ) -> "VoiceStoragePaths":
        platform_name = platform_name or sys.platform
        env = os.environ if env is None else env
        home_path = Path(home) if home is not None else Path.home()

        override = env.get("BILILIVE_DATA_HOME", "").strip()
        if override:
            root = Path(override)
        elif platform_name == "win32":
            local_app_data = env.get("LOCALAPPDATA", "").strip()
            root = (Path(local_app_data) if local_app_data else home_path / "AppData" / "Local") / "BiliLiveTool"
        elif platform_name == "darwin":
            root = home_path / "Library" / "Application Support" / "BiliLiveTool"
        else:
            xdg_data_home = env.get("XDG_DATA_HOME", "").strip()
            root = (Path(xdg_data_home) if xdg_data_home else home_path / ".local" / "share") / "BiliLiveTool"

        runtime_override = env.get("BILILIVE_RUNTIME_HOME", "").strip()
        runtime_root = Path(runtime_override) if runtime_override else root / "runtimes"
        return cls(
            root=root,
            voices=root / "voices",
            aivmx_voices=root / "aivmx-voices",
            runtimes=runtime_root,
            cpu_runtimes=runtime_root / ".cpu",
            speech_cache=root / "cache" / "speech",
            staging=root / "staging",
            logs=root / "logs",
            voice_state=root / "voice-state",
        )

    def ensure(self) -> "VoiceStoragePaths":
        for path in (self.voices, self.aivmx_voices, self.runtimes, self.cpu_runtimes, self.speech_cache, self.staging, self.logs, self.voice_state):
            path.mkdir(parents=True, exist_ok=True)
        return self
