"""Identify the Whisplay Plus DSI panel without importing GPIO or SPI."""

from pathlib import Path
import sys


def detect_plus_output(
    drm: Path = Path("/sys/class/drm"),
    device_tree: Path = Path("/proc/device-tree"),
) -> str | None:
    # Ordinary DSI panels and either HAT's shared ALSA card are insufficient
    # to identify Plus. Match the compatible from the PiSugar panel overlay.
    for compatible in device_tree.rglob("panel*/compatible"):
        try:
            if b"pisugar,st7701p-480x640" not in compatible.read_bytes().split(b"\0"):
                continue
            status = compatible.with_name("status")
            if status.exists() and status.read_bytes().rstrip(b"\0") not in (b"okay", b"ok"):
                continue
        except OSError:
            continue
        break
    else:
        return None

    for connector in sorted(drm.glob("card*-DSI-*")):
        try:
            if ((connector / "status").read_text().strip() == "connected"
                    and "480x640" in (connector / "modes").read_text().splitlines()):
                return connector.name.split("-", 1)[1]
        except OSError:
            continue
    # The driver can be configured before the desktop/DRM connector is ready.
    return "DSI-1"


if __name__ == "__main__":
    output = detect_plus_output()
    if output is None:
        sys.exit(1)
    print(output)
