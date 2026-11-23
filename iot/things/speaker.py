"""
Speaker IoT Thing — volume control via amixer.
"""

import asyncio
import logging
import re
import subprocess

from iot.thing import Thing, Parameter, ValueType

log = logging.getLogger("iot.speaker")

# Preferred controls: unified Whisplay first, legacy codec controls as fallback.
_CONTROL_NAMES = ["speaker", "Speaker", "Master", "Playback", "PCM"]


def _find_card() -> str:
    """Dynamically detect the Whisplay ALSA card.

    Returns the unified card name when available; otherwise returns a legacy
    card index, or an empty string to use the default card.
    """
    try:
        with open("/proc/asound/cards", "r") as f:
            fallback_index = ""
            for line in f:
                lower = line.lower()
                m = re.match(r"\s*(\d+)\s+\[([^\]]+)\]", line)
                if not m:
                    continue
                card_index, card_name = m.group(1), m.group(2).strip()
                if "whisplaysound" in lower:
                    return card_name
                if not fallback_index and ("wm8960" in lower or "es8389" in lower):
                    fallback_index = card_index
            return fallback_index
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("failed to detect sound card: %s", e)
    return ""  # empty = default card


def _find_control(card: str) -> str:
    """Find the first working amixer control on the given card."""
    card_args = ["-c", card] if card else []
    try:
        r = subprocess.run(
            ["amixer"] + card_args + ["controls"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "name='speaker'" in r.stdout:
            return "name=speaker"
    except Exception:
        pass

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
        self._volume_lock = asyncio.Lock()
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
        try:
            return await self.get_volume()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("read volume failed: %s", exc)
            return 50

    async def _set_volume(self, params) -> dict:
        level = await self.set_volume(int(params["volume"].get_value()))
        return {"status": "success", "volume": level}

    async def get_volume(self) -> int:
        async with self._volume_lock:
            return await asyncio.to_thread(self._read_volume)

    async def set_volume(self, level: int) -> int:
        level = max(0, min(100, level))
        async with self._volume_lock:
            await asyncio.to_thread(self._write_volume, level)
        log.info("volume set to %d%%", level)
        return level

    # ---- sync helpers (run in executor) ----

    def _read_volume(self) -> int:
        verb = "cget" if self._control.startswith("name=") else "sget"
        result = subprocess.run(self._amixer_cmd(verb, self._control),
                                capture_output=True, text=True, timeout=5, check=True)
        match = re.search(r":\s*values=(\d+)|\[(\d+)%\]", result.stdout)
        if not match:
            raise ValueError("amixer returned no volume value")
        return max(0, min(100, int(match.group(1) or match.group(2))))

    def _write_volume(self, level: int):
        verb, value = ("cset", str(level)) if self._control.startswith("name=") else ("sset", f"{level}%")
        subprocess.run(self._amixer_cmd(verb, self._control, value),
                       capture_output=True, timeout=5, check=True)
