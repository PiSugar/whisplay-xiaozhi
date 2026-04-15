"""
Speaker IoT Thing — volume control via amixer.
"""

import asyncio
import logging
import re
import subprocess

from iot.thing import Thing, Parameter, ValueType

log = logging.getLogger("iot.speaker")

# Preferred simple-control names for WM8960 codec
_CONTROL_NAMES = ["Speaker", "Master", "Playback", "PCM"]


def _find_card() -> str:
    """Dynamically detect the ALSA card index for the WM8960 codec.

    Reads /proc/asound/cards and looks for a line containing 'wm8960'.
    Returns the card index as a string, or empty string to use the default card.
    """
    try:
        with open("/proc/asound/cards", "r") as f:
            for line in f:
                if "wm8960" in line.lower():
                    # Line format: " 1 [wm8960soundcard]: simple-card - ..."
                    m = re.match(r"\s*(\d+)\s+\[", line)
                    if m:
                        return m.group(1)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("failed to detect sound card: %s", e)
    return ""  # empty = default card


def _find_control(card: str) -> str:
    """Find the first working amixer simple control on the given card."""
    card_args = ["-c", card] if card else []
    for name in _CONTROL_NAMES:
        try:
            r = subprocess.run(
                ['amixer', '-M'] + card_args + ['sget', name],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "%" in r.stdout:
                return name
        except Exception:
            continue
    return "Speaker"  # fallback


class Speaker(Thing):
    def __init__(self):
        super().__init__("Speaker", "板载扬声器，支持音量调节")
        self._card = _find_card()
        self._control = _find_control(self._card)
        log.info("using amixer card=%s control=%s", self._card or "default", self._control)

        self.add_property("volume", "当前音量 (0-100)", self._get_volume)

        self.add_method(
            "SetVolume",
            "设置音量",
            [Parameter("volume", "音量值 (0-100)", ValueType.NUMBER)],
            self._set_volume,
        )

    def _amixer_cmd(self, *args: str) -> list[str]:
        """Build amixer command with the correct card argument.

        Always includes -M so percentages use the same mapped (perceptual)
        scale as alsamixer.
        """
        cmd = ["amixer", "-M"]
        if self._card:
            cmd += ["-c", self._card]
        cmd += list(args)
        return cmd

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
                self._amixer_cmd("sget", self._control),
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
                self._amixer_cmd("sset", self._control, f"{level}%"),
                capture_output=True, timeout=5,
            )
        except Exception as e:
            log.warning("set volume failed: %s", e)
