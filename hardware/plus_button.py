"""Whisplay Plus uses BCM17 for its physical talk button."""

import asyncio
import errno
import json
import logging
import os
import stat
import time
from pathlib import Path

import gpiod

from hardware.whisplay_board import _GPIOD_V2, _request_input

log = logging.getLogger(__name__)
_BUTTON_GPIO = 17
_DEFAULT_BRIDGE_STATE = "/tmp/whisplay-plus-gpio-state.json"


def _read_bridge_state(path):
    """Read the latest sample from the Plus GPIO bridge if it is still running."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as state:
            info = os.fstat(state.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()):
                return None
            packet = json.loads(state.read(4096))
    except (OSError, UnicodeError, ValueError):
        return None

    if not isinstance(packet, dict):
        return None
    updated = packet.get("updatedAt")
    pressed = packet.get("buttonPressed")
    if (packet.get("enabled") is True and packet.get("ready") is True
            and packet.get("buttonGpio") == _BUTTON_GPIO and isinstance(pressed, bool)
            and isinstance(updated, (float, int)) and 0 <= time.time() - updated <= 5):
        return pressed
    return None


def _open_header_chip():
    candidates = sorted(Path("/dev").glob("gpiochip*"))
    for path in candidates:
        try:
            chip = gpiod.Chip(str(path))
        except OSError:
            continue
        label = chip.get_info().label if _GPIOD_V2 else chip.label()
        if label == "pinctrl-rp1" or label.startswith("pinctrl-bcm"):
            return chip
        chip.close()
    raise RuntimeError("Raspberry Pi header GPIO controller not found")


class WhisplayPlusButton:
    def __init__(self, on_press):
        self._chip = _open_header_chip()
        try:
            self._line = _request_input(self._chip, _BUTTON_GPIO, consumer="whisplay-plus-button")
        except OSError as exc:
            self._chip.close()
            if exc.errno != errno.EBUSY:
                raise
            self._chip = self._line = None
            path = Path(os.getenv("WHISPLAY_GPIO_STATE_FILE") or _DEFAULT_BRIDGE_STATE)
            log.info("Plus GPIO17 is busy; listening for shared button state at %s", path)
            self._task = asyncio.create_task(
                self._poll(on_press, lambda: _read_bridge_state(path), interval=0.02, debounce=0)
            )
        except Exception:
            self._chip.close()
            raise
        else:
            self._task = asyncio.create_task(
                self._poll(on_press, self._line.get_value, interval=0.01, debounce=0.03)
            )

    async def _poll(self, on_press, read, *, interval, debounce):
        try:
            stable = candidate = read()
            loop = asyncio.get_running_loop()
            since = loop.time()
            while True:
                await asyncio.sleep(interval)
                value = read()
                now = loop.time()
                if value is None or stable is None:
                    stable = candidate = value
                    continue
                if value != candidate:
                    candidate, since = value, now
                if value != stable and now - since >= debounce:
                    stable = value
                    if stable:
                        on_press()
        except OSError as exc:
            log.warning("Plus button stopped responding: %s", exc)

    def stop(self):
        self._task.cancel()
        if self._line is not None:
            try:
                self._line.release()
            finally:
                self._chip.close()
