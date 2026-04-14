"""
Audio player — streams raw PCM data to speaker via sox subprocess.

Accepts 24kHz 16-bit mono PCM (decoded from Opus).
Uses a queue-based approach so callers can push decoded PCM chunks asynchronously.
"""

import asyncio
import logging
import subprocess

import config

log = logging.getLogger("player")


class AudioPlayer:
    """Async audio player using sox."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self):
        """Start sox playback subprocess and writer task."""
        # Clean up any previous writer task
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        # Drain leftover queue
        self._queue = asyncio.Queue()
        cmd = [
            "sox",
            "-t", "raw",
            "-r", str(config.AUDIO_OUTPUT_SAMPLE_RATE),
            "-b", "16",
            "-e", "signed-integer",
            "-c", "1",
            "-",
            "-t", "alsa", config.ALSA_OUTPUT_DEVICE,
        ]
        self._process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._queue = asyncio.Queue()
        self._task = asyncio.get_event_loop().create_task(self._writer())
        log.info("player started (pid=%s)", self._process.pid)

    async def _writer(self):
        """Background task that drains the queue into sox stdin."""
        loop = asyncio.get_event_loop()
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            if self._process and self._process.stdin:
                try:
                    await loop.run_in_executor(None, self._process.stdin.write, chunk)
                    await loop.run_in_executor(None, self._process.stdin.flush)
                except Exception:
                    break

    async def put(self, pcm_data: bytes):
        """Enqueue decoded PCM data for playback."""
        await self._queue.put(pcm_data)

    async def stop(self):
        """Stop playback, close sox process."""
        # Signal writer to exit
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except asyncio.TimeoutError:
                self._task.cancel()
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        log.info("player stopped")

    def is_active(self) -> bool:
        return self._process is not None and self._process.poll() is None
