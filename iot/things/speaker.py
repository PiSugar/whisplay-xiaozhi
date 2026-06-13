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
            command = self._amixer_cmd("cget", self._control) if self._control.startswith("name=") else self._amixer_cmd("sget", self._control)
            r = subprocess.run(
                command,
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                m = re.search(r":\s*values=(\d+)", r.stdout)
                if m:
                    return int(m.group(1))
                m = re.search(r"\[(\d+)%\]", r.stdout)
                if m:
                    return int(m.group(1))
        except Exception as e:
            log.warning("read volume failed: %s", e)
        return 50  # fallback

    def _write_volume(self, level: int):
        try:
            command = self._amixer_cmd("cset", self._control, str(level)) if self._control.startswith("name=") else self._amixer_cmd("sset", self._control, f"{level}%")
            subprocess.run(
                command,
                capture_output=True, timeout=5,
            )
        except Exception as e:
            log.warning("set volume failed: %s", e)
