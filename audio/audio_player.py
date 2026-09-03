"""
Audio player — streams raw PCM data to speaker via sox subprocess.

Accepts 24kHz 16-bit mono PCM (decoded from Opus).
Uses a queue-based approach so callers can push decoded PCM chunks asynchronously.
"""

import asyncio
import logging
import subprocess
from collections.abc import Callable

import config

log = logging.getLogger("player")


class AudioPlayer:
    """Async audio player using sox."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.on_pcm_written: Callable[[bytes], None] | None = None

    def start(self):
        """Start sox playback subprocess and writer task."""
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
        # Clean up any previous writer task
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        # Drain leftover queue
        self._queue = asyncio.Queue()
        self._stopping = False
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
                    if self.on_pcm_written:
                        self.on_pcm_written(chunk)
                    await loop.run_in_executor(None, self._process.stdin.write, chunk)
                    await loop.run_in_executor(None, self._process.stdin.flush)
                except Exception:
                    break
        if self._process and self._process.stdin:
            try:
                await loop.run_in_executor(None, self._process.stdin.close)
            except Exception:
                pass

    async def put(self, pcm_data: bytes):
        """Enqueue decoded PCM data for playback."""
        if self._stopping:
            return
        await self._queue.put(pcm_data)

    async def stop(self):
        """Drain queued audio and stop playback."""
        self._stopping = True
        tail_padding = self._tail_padding()
        if tail_padding:
            await self._queue.put(tail_padding)
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except asyncio.TimeoutError:
                self._task.cancel()
            except asyncio.CancelledError:
                # An immediate barge-in may abort playback while a graceful
                # drain is waiting for the same writer task.
                pass
            self._task = None
        if self._process:
            try:
                loop = asyncio.get_event_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, self._process.wait),
                    timeout=config.AUDIO_OUTPUT_DRAIN_TIMEOUT_SEC,
                )
            except Exception:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=1)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
            self._process = None
        log.info("player stopped")

    async def abort(self):
        """Immediately discard queued speech and terminate playback."""
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        process = self._process
        self._process = None
        if process:
            try:
                if process.stdin:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.terminate()
                await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, process.wait),
                    timeout=0.5,
                )
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=0.5)
                except Exception:
                    pass
        self._queue = asyncio.Queue()
        log.info("player aborted")

    def _tail_padding(self) -> bytes:
        """Return a short silence pad so ALSA/sox drains audible speech tails."""
        ms = max(0, config.AUDIO_OUTPUT_TAIL_PADDING_MS)
        if ms <= 0:
            return b""
        sample_width = 2
        channels = 1
        frames = config.AUDIO_OUTPUT_SAMPLE_RATE * ms // 1000
        return b"\x00" * frames * sample_width * channels

    def is_active(self) -> bool:
        return self._process is not None and self._process.poll() is None
