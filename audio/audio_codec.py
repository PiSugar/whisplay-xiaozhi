"""
Opus audio codec wrapper for XiaoZhi protocol.

Encoding: 16kHz mono PCM → Opus frames
Decoding: Opus frames → 24kHz mono PCM
"""

import opuslib

import config


class OpusEncoder:
    """Encode 16kHz 16-bit mono PCM to Opus frames."""

    def __init__(self, frame_duration_ms: int = 60):
        self._sample_rate = config.AUDIO_INPUT_SAMPLE_RATE
        self._frame_duration = frame_duration_ms
        self._frame_samples = self._sample_rate * frame_duration_ms // 1000
        self._frame_bytes = self._frame_samples * 2  # 16-bit
        self._encoder = opuslib.Encoder(self._sample_rate, 1, opuslib.APPLICATION_VOIP)

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    @property
    def frame_duration_ms(self) -> int:
        return self._frame_duration

    def encode(self, pcm: bytes) -> bytes:
        """Encode one frame of PCM data to Opus."""
        return self._encoder.encode(pcm, self._frame_samples)


class OpusDecoder:
    """Decode Opus frames to 24kHz 16-bit mono PCM."""

    def __init__(self, frame_duration_ms: int = 60):
        self._sample_rate = config.AUDIO_OUTPUT_SAMPLE_RATE
        self._frame_duration = frame_duration_ms
        self._frame_samples = self._sample_rate * frame_duration_ms // 1000
        self._decoder = opuslib.Decoder(self._sample_rate, 1)

    def decode(self, opus_data: bytes) -> bytes:
        """Decode one Opus frame to PCM."""
        return self._decoder.decode(opus_data, self._frame_samples)
