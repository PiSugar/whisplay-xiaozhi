"""
PiSugar battery monitor — async TCP client for pisugar-server.

Protocol: send "get battery\n" → receive "battery: <float>\n"
          send "get battery_charging\n" → receive "battery_charging: true/false\n"
"""

import asyncio
import logging

import config

log = logging.getLogger("battery")


class BatteryMonitor:
    def __init__(self):
        self.level: int = -1
        self.charging: bool = False
        self._task: asyncio.Task | None = None

    async def start(self):
        if not config.PISUGAR_ENABLED:
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self):
        while True:
            try:
                await self._query()
            except Exception as e:
                log.debug("battery query failed: %s", e)
            await asyncio.sleep(config.BATTERY_POLL_INTERVAL)

    async def _query(self):
        reader, writer = await asyncio.open_connection(
            config.PISUGAR_HOST, config.PISUGAR_PORT
        )
        try:
            # Query battery level
            writer.write(b"get battery\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            text = line.decode().strip()
            if text.startswith("battery:"):
                val = text.split(":", 1)[1].strip()
                self.level = max(0, min(100, int(float(val))))

            # Query charging state
            writer.write(b"get battery_charging\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=3)
            text = line.decode().strip()
            if "true" in text.lower():
                self.charging = True
            else:
                self.charging = False
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def get_color(self) -> tuple[int, int, int]:
        """Return RGB color tuple based on battery level."""
        if self.level < 0:
            return (128, 128, 128)
        if self.charging:
            return (0, 200, 255)
        if self.level <= 10:
            return (255, 0, 0)
        if self.level <= 30:
            return (255, 165, 0)
        return (52, 211, 81)
