import unittest

import numpy as np

from audio.barge_in import BargeInDetector


def _pcm(rms: float, samples: int = 960) -> bytes:
    values = np.empty(samples, dtype=np.int16)
    values[0::2] = int(rms)
    values[1::2] = -int(rms)
    return values.tobytes()


class BargeInDetectorTests(unittest.TestCase):
    def test_echo_floor_does_not_trigger(self):
        detector = BargeInDetector(required_frames=3, warmup_ms=300)
        detector.reset(now=0.0)
        detector.update_speaker(_pcm(8000))
        for now in (0.0, 0.1, 0.2, 0.4, 0.5, 0.6):
            self.assertIsNone(detector.process(_pcm(700), now=now))

    def test_sustained_voice_returns_preroll(self):
        detector = BargeInDetector(required_frames=3, warmup_ms=200)
        detector.reset(now=0.0)
        detector.update_speaker(_pcm(4000))
        detector.process(_pcm(500), now=0.0)
        detector.process(_pcm(500), now=0.1)
        self.assertIsNone(detector.process(_pcm(3500), now=0.3))
        self.assertIsNone(detector.process(_pcm(3500), now=0.36))
        buffered = detector.process(_pcm(3500), now=0.42)
        self.assertIsNotNone(buffered)
        self.assertGreaterEqual(len(buffered), 3)

    def test_short_noise_burst_resets_consecutive_hits(self):
        detector = BargeInDetector(required_frames=2, warmup_ms=0)
        detector.reset(now=0.0)
        self.assertIsNone(detector.process(_pcm(3000), now=0.1))
        self.assertIsNone(detector.process(_pcm(100), now=0.2))
        self.assertIsNone(detector.process(_pcm(3000), now=0.3))


if __name__ == "__main__":
    unittest.main()
