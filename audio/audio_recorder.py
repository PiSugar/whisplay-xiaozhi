"""
Audio recorder — captures microphone input via sox subprocess.

Produces raw PCM chunks (16kHz, 16-bit signed, mono) suitable for
Opus encoding and feeding to the XiaoZhi server.
"""

import asyncio
import logging
import subprocess

import config

log = logging.getLogger("recorder")


class AudioRecorder:
    """Async PCM audio recorder using sox."""

    def __init__(self):
        self._process: subprocess.Popen | None = None

    def start(self):
        """Start the sox recording subprocess."""
        cmd = [
            "sox",
            "-t", "alsa", config.ALSA_INPUT_DEVICE,
            "-r", str(config.AUDIO_INPUT_SAMPLE_RATE),
            "-b", "16",
            "-e", "signed-integer",
            "-c", "1",
            "-t", "raw",
            "-",
        ]
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        log.info("recorder started (pid=%s)", self._process.pid)

    def stop(self):
        """Terminate the recording subprocess."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                self._process.kill()
            self._process = None
            log.info("recorder stopped")

    async def read_frames(self, frame_bytes: int):
        """Async generator that yields PCM frames of exactly frame_bytes size."""
        if not self._process or not self._process.stdout:
            return
        loop = asyncio.get_event_loop()
        while self._process and self._process.poll() is None:
            try:
                data = await loop.run_in_executor(
                    None, self._process.stdout.read, frame_bytes
                )
            except Exception:
                break
            if not data or len(data) < frame_bytes:
                break
            yield data
