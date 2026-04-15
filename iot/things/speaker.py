"""
Speaker IoT Thing — volume control via amixer.
"""

import asyncio
import logging
import re
import subprocess

from iot.thing import Thing, Parameter, ValueType

log = logging.getLogger("iot.speaker")

# amixer control name — WM8960 codec uses "Speaker" not "Master"
_AMIXER_CONTROLS = ["Speaker", "Master", "Playback", "PCM"]


def _find_control() -> str:
    """Find the first working amixer simple control name."""
    for name in _AMIXER_CONTROLS:
        try:
            r = subprocess.run(
                ["amixer", "sget", name],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "%" in r.stdout:
                return name
        except Exception:
            continue
    return "Master"  # fallback


class Speaker(Thing):
    def __init__(self):
        super().__init__("Speaker", "板载扬声器，支持音量调节")
        self._control = _find_control()
        log.info("using amixer control: %s", self._control)

        self.add_property("volume", "当前音量 (0-100)", self._get_volume)

        self.add_method(
            "SetVolume",
            "设置音量",
            [Parameter("volume", "音量值 (0-100)", ValueType.NUMBER)],
            self._set_volume,
        )

    async def _get_volume(self) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._read_volume)

    async def _set_volume(self, params) -> dict:
        level = int(params["volume"].get_value())
        level = max(0, min(100, level))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._write_volume, level)
        log.info("volume set to %d%%", level)
        return {"status": "success", "volume": level}

    # ---- sync helpers (run in executor) ----

    def _read_volume(self) -> int:
        try:
            r = subprocess.run(
                ["amixer", "sget", self._control],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                m = re.search(r"\[(\d+)%\]", r.stdout)
                if m:
                    return int(m.group(1))
        except Exception as e:
            log.warning("read volume failed: %s", e)
        return 50  # fallback

    def _write_volume(self, level: int):
        try:
            subprocess.run(
                ["amixer", "sset", self._control, f"{level}%"],
                capture_output=True, timeout=5,
            )
        except Exception as e:
            log.warning("set volume failed: %s", e)
