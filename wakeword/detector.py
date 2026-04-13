"""
Wake word detector — uses openwakeword to detect trigger phrases.

Runs sox subprocess for PCM input, feeds to openwakeword model,
calls the on_wake callback when detection fires.
"""

import asyncio
import logging
import subprocess
import time

import numpy as np

import config

log = logging.getLogger("wakeword")


class WakeWordDetector:
    """Async wake word detector using openwakeword."""

    def __init__(self, on_wake):
        self._on_wake = on_wake
        self._model = None
        self._process = None

    async def run(self):
        """Main detection loop."""
        try:
            from openwakeword.model import Model
        except ImportError:
            log.error("openwakeword not installed — wake word disabled")
            return

        wake_words = config.WAKE_WORDS
        threshold = config.WAKE_WORD_THRESHOLD
        cooldown = config.WAKE_WORD_COOLDOWN_SEC

        # Check for custom model paths vs built-in names
        model_paths = [w for w in wake_words if w.endswith((".onnx", ".tflite"))]
        model_names = [w for w in wake_words if w not in model_paths]

        try:
            self._model = Model(
                wakeword_models=model_paths or model_names
            )
        except Exception as e:
            log.error("failed to init model: %s", e)
            return

        sox_cmd = [
            "sox", "-t", "alsa", config.ALSA_INPUT_DEVICE,
            "-r", "16000", "-b", "16", "-e", "signed-integer",
            "-c", "1", "-t", "raw", "-",
        ]

        self._process = subprocess.Popen(
            sox_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

        chunk_samples = 1280  # 80ms at 16kHz
        chunk_bytes = chunk_samples * 2
        last_trigger = 0.0
        loop = asyncio.get_event_loop()

        log.info("wake word detector started (words=%s, threshold=%.2f)", wake_words, threshold)

        try:
            while True:
                data = await loop.run_in_executor(
                    None, self._process.stdout.read, chunk_bytes
                )
                if not data or len(data) < chunk_bytes:
                    await asyncio.sleep(0.01)
                    continue

                audio = np.frombuffer(data, dtype=np.int16)
                try:
                    prediction = self._model.predict(audio)
                except Exception:
                    continue

                now = time.time()
                if now - last_trigger < cooldown:
                    continue

                for keyword, score in prediction.items():
                    if score >= threshold:
                        last_trigger = now
                        log.info("wake word detected: %s (%.3f)", keyword, score)
                        await self._on_wake()
                        break
        except asyncio.CancelledError:
            pass
        finally:
            if self._process:
                self._process.terminate()
