from __future__ import annotations

import array
import threading
from dataclasses import dataclass
from typing import Callable


class AudioPlaybackError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PlaybackResult:
    bytes_played: int
    pcm: bytes


class StreamingAudioPlayer:
    def __init__(self, output_stream_factory: Callable | None = None, max_capture_bytes: int = 32 * 1024 * 1024):
        self.output_stream_factory = output_stream_factory
        self.max_capture_bytes = max_capture_bytes
        self._lock = threading.RLock()
        self._generation = 0
        self._stream = None

    @staticmethod
    def _cancelled(token) -> bool:
        if token is None:
            return False
        if callable(token):
            return bool(token())
        if hasattr(token, "is_set"):
            return bool(token.is_set())
        return bool(token)

    @staticmethod
    def _scale_pcm16(block: bytes, volume: float) -> bytes:
        if volume == 1.0:
            return block
        samples = array.array("h")
        samples.frombytes(block)
        for index, value in enumerate(samples):
            samples[index] = max(-32768, min(32767, round(value * volume)))
        return samples.tobytes()

    def _factory(self):
        if self.output_stream_factory:
            return self.output_stream_factory
        try:
            import sounddevice
        except ImportError as exc:
            raise AudioPlaybackError("audio_runtime_missing", "缺少本地音频播放组件 sounddevice") from exc
        return sounddevice.RawOutputStream

    def play(self, stream, volume: float = 1.0, token=None, capture: bool = False) -> PlaybackResult:
        sample_rate = int(getattr(stream, "sample_rate", 0))
        channels = int(getattr(stream, "channels", 0))
        sample_width = int(getattr(stream, "sample_width", 0))
        if sample_rate <= 0 or channels not in (1, 2) or sample_width != 2:
            raise AudioPlaybackError("invalid_pcm", "仅接受有效的 16 位 PCM 音频流")
        volume = max(0.0, min(1.0, float(volume)))
        with self._lock:
            self._generation += 1
            generation = self._generation
        output = None
        captured = bytearray()
        played = 0
        frame_size = channels * sample_width
        try:
            for raw in stream:
                with self._lock:
                    stopped = generation != self._generation
                if stopped or self._cancelled(token):
                    raise AudioPlaybackError("cancelled", "语音播放已取消")
                block = bytes(raw)
                if not block or len(block) % frame_size:
                    raise AudioPlaybackError("invalid_pcm", "PCM 音频块没有按采样帧对齐")
                block = self._scale_pcm16(block, volume)
                if output is None:
                    output = self._factory()(
                        samplerate=sample_rate,
                        channels=channels,
                        dtype="int16",
                    )
                    with self._lock:
                        self._stream = output
                    output.start()
                output.write(block)
                played += len(block)
                if capture:
                    if len(captured) + len(block) > self.max_capture_bytes:
                        raise AudioPlaybackError("capture_too_large", "试听音频超过本地缓存限制")
                    captured.extend(block)
            if played == 0:
                raise AudioPlaybackError("empty_audio", "GPU 运行时没有返回音频")
            return PlaybackResult(played, bytes(captured))
        finally:
            try:
                if output is not None:
                    output.stop()
                    output.close()
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
                with self._lock:
                    if self._stream is output:
                        self._stream = None

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
