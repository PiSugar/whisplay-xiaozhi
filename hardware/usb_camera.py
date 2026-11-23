"""Discover Linux USB cameras and build FFmpeg capture arguments."""

import re
import shutil
from pathlib import Path

import config


def is_capture_device(path: Path) -> bool:
    try:
        return path.is_char_device()
    except OSError:
        return False


def usb_camera_device(root=Path("/dev/v4l")) -> Path | None:
    device = config.USB_CAMERA_DEVICE
    if device == "off":
        return None
    if device != "auto":
        path = Path(device).expanduser()
        return path if is_capture_device(path) else None

    # udev's index 0 is the image node on standard UVC cameras. The secondary
    # node is often metadata. Never select Raspberry Pi ISP or codec nodes.
    for directory, pattern in (("by-id", "usb-*-video-index0"),
                               ("by-path", "*-usb*-video-index0")):
        for path in sorted((root / directory).glob(pattern)):
            if is_capture_device(path):
                return path
    return None


def usb_camera_required() -> bool:
    return config.USB_CAMERA_DEVICE not in ("auto", "off")


def usb_capture_command(device: Path, width: int, height: int, quality: int, *,
                        fps: int | None = None, output: Path | None = None) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg is required for USB cameras")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "v4l2"]
    if config.USB_CAMERA_SIZE:
        if not re.fullmatch(r"[1-9]\d*x[1-9]\d*", config.USB_CAMERA_SIZE):
            raise ValueError("XIAOZHI_USB_CAMERA_SIZE must be WIDTHxHEIGHT")
        command.extend(["-video_size", config.USB_CAMERA_SIZE])
    command.extend(["-i", str(device), "-an"])
    filters = [f"scale={width}:{height}:force_original_aspect_ratio=decrease",
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"]
    if fps is not None:
        filters.insert(0, f"fps={fps}")
    if output is not None and config.CAMERA_WARMUP_MS:
        command.extend(["-ss", str(config.CAMERA_WARMUP_MS / 1000)])
    qscale = 2 + round((100 - quality) * 25 / 100)
    command.extend(["-vf", ",".join(filters), "-c:v", "mjpeg", "-threads", "1",
                    "-q:v", str(qscale)])
    if output is None:
        command.extend(["-f", "mjpeg", "pipe:1"])
    else:
        command.extend(["-frames:v", "1", "-f", "image2", "-y", str(output)])
    return command
