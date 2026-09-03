"""Local voice barge-in detector for assistant playback."""

from __future__ import annotations

import math
import time
from collections import deque

import numpy as np


class BargeInDetector:
    """Detect sustained near-field speech above the learned speaker echo floor."""

    def __init__(
        self,
        min_rms: float = 850.0,
        required_frames: int = 4,
        warmup_ms: int = 350,
        pre_roll_frames: int = 6,
    ):
        self.min_rms = max(1.0, float(min_rms))
        self.required_frames = max(1, int(required_frames))
        self.warmup_seconds = max(0.0, float(warmup_ms) / 1000.0)
        self._chunks: deque[bytes] = deque(maxlen=max(1, pre_roll_frames))
        self._speaker_rms = 0.0
        self.reset()

    def reset(self, now: float | None = None):
        self._started_at = time.monotonic() if now is None else now
        self._floor = 120.0
        self._speaker_rms = 0.0
        self._hits = 0
        self._chunks.clear()

    @staticmethod
    def _rms(pcm: bytes) -> float:
        usable = len(pcm) - len(pcm) % 2
        if usable <= 0:
            return 0.0
        samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
        return math.sqrt(float(np.mean(samples * samples))) if samples.size else 0.0

    def update_speaker(self, pcm: bytes):
        """Update the current playback reference level."""
        self._speaker_rms = self._rms(pcm)

    def process(self, pcm: bytes, now: float | None = None) -> list[bytes] | None:
        """Return buffered user audio once a sustained interruption is detected."""
        now = time.monotonic() if now is None else now
        rms = self._rms(pcm)
        self._chunks.append(pcm)

        # Give the acoustic path time to settle and learn the initial echo.
        if now - self._started_at < self.warmup_seconds:
            self._floor += (rms - self._floor) * 0.25
            self._hits = 0
            return None

        threshold = max(
            self.min_rms,
            self._floor * 2.6 + 150.0,
            self._speaker_rms * 0.16,
        )
        if rms >= threshold:
            self._hits += 1
        else:
            self._hits = 0
            self._floor += (rms - self._floor) * 0.08

        if self._hits < self.required_frames:
            return None
        buffered = list(self._chunks)
        self._chunks.clear()
        self._hits = 0
        return buffered
