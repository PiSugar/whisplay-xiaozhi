"""Raspberry Pi camera MCP tool using rpicam-still/libcamera-still."""

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from PIL import Image
import requests

import config
from protocol.mcp_handler import McpToolResult

log = logging.getLogger("mcp.camera")

ProgressCallback = Callable[[str | None], None]
PhotoCallback = Callable[[str], None]

CAMERA_CAPTURE_DESCRIPTION = (
    "Always remember you have a camera. If the user asks you to see something, use "
    "this tool to take a photo and then explain it. The question must say what should "
    "be inspected in the photo."
)

CAMERA_CAPTURE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "The question to ask the vision model about the new photo.",
        },
    },
    "required": ["question"],
}


@dataclass
class _VisionConfig:
    url: str = ""
    token: str = ""
    device_id: str = ""
    client_id: str = ""


_vision = _VisionConfig()


def configure_vision(
    url: str | None,
    token: str | None,
    device_id: str,
    client_id: str,
) -> None:
    """Store the vision endpoint supplied by the server's MCP initialize call."""
    global _vision
    _vision = _VisionConfig(
        url=(url or "").strip(),
        token=(token or "").strip(),
        device_id=(device_id or "").strip(),
        client_id=(client_id or "").strip(),
    )
    if _vision.url:
        endpoint = urlsplit(_vision.url)
        log.info("vision capability configured: %s://%s", endpoint.scheme, endpoint.netloc)
    else:
        log.warning("MCP initialize did not provide a vision URL")


def is_enabled() -> bool:
    return config.CAMERA_TOOL_ENABLED


def _bounded_int(value, default: int, minimum: int, maximum: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return max(minimum, min(maximum, parsed))


def _camera_command() -> str:
    configured = config.CAMERA_COMMAND
    if configured:
        resolved = shutil.which(configured) if "/" not in configured else configured
        if resolved and os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return resolved
        raise RuntimeError(f"configured camera command is unavailable: {configured}")
    for candidate in ("rpicam-still", "libcamera-still"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("Raspberry Pi camera command not found (rpicam-still/libcamera-still)")


def _output_directory() -> Path:
    configured = Path(config.CAMERA_OUTPUT_DIR).expanduser()
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parent.parent / configured
    configured.mkdir(parents=True, exist_ok=True)
    return configured


def _cleanup_old_captures(directory: Path, keep: int) -> None:
    captures = sorted(
        directory.glob("capture-*.jpg"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in captures[max(1, keep):]:
        try:
            path.unlink()
        except OSError:
            log.warning("failed to remove old camera capture: %s", path)


def _upload_for_explanation(image_bytes: bytes, question: str) -> str:
    vision = _vision
    if not vision.url:
        raise RuntimeError(
            "Vision service is unavailable: the server did not provide a vision capability"
        )
    if not question.strip():
        raise ValueError("question is required")

    headers = {
        "Device-Id": vision.device_id,
        "Client-Id": vision.client_id,
    }
    if vision.token:
        headers["Authorization"] = f"Bearer {vision.token}"

    response = requests.post(
        vision.url,
        headers=headers,
        data={"question": question.strip()},
        files={"file": ("camera.jpg", image_bytes, "image/jpeg")},
        timeout=config.CAMERA_VISION_TIMEOUT_SEC,
        allow_redirects=False,
    )
    response.raise_for_status()
    result_text = response.text.strip()
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        # Match the verified cardputer-xiaozhi behavior: a plain-text vision
        # response is still a valid tool result and is wrapped for MCP.
        result = {"result": result_text}
    if isinstance(result, dict):
        log.info("vision response received (keys=%s)", sorted(map(str, result.keys())))
    else:
        log.info("vision response received (%s)", type(result).__name__)
    # The hosted XiaoZhi vision service has returned multiple compatible JSON
    # shapes over time (for example answer/result and action/response). Do not
    # impose one server schema here; cardputer-xiaozhi returns any JSON intact.
    return json.dumps(result, ensure_ascii=False)


async def capture_photo(
    params: dict,
    progress_callback: ProgressCallback | None = None,
    photo_callback: PhotoCallback | None = None,
) -> McpToolResult:
    question = str(params.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    width = _bounded_int(params.get("width"), config.CAMERA_WIDTH, 160, 1280, "width")
    height = _bounded_int(params.get("height"), config.CAMERA_HEIGHT, 120, 960, "height")
    quality = _bounded_int(params.get("quality"), config.CAMERA_QUALITY, 30, 95, "quality")
    camera_command = _camera_command()
    output_dir = _output_directory()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"capture-{timestamp}-{uuid.uuid4().hex[:6]}.jpg"

    if progress_callback:
        progress_callback("camera\nCapturing photo...")

    command = [
        camera_command,
        "--camera",
        str(config.CAMERA_INDEX),
        "--nopreview",
        "--timeout",
        str(config.CAMERA_WARMUP_MS),
    ]
    if config.CAMERA_AUTOFOCUS:
        command.extend(
            [
                "--autofocus-on-capture",
                "--autofocus-range",
                config.CAMERA_AUTOFOCUS_RANGE,
                "--autofocus-speed",
                config.CAMERA_AUTOFOCUS_SPEED,
            ]
        )
    command.extend(
        [
            "--width",
            str(width),
            "--height",
            str(height),
            "--quality",
            str(quality),
            "--encoding",
            "jpg",
            "--output",
            str(output_path),
        ]
    )
    log.info("capturing camera image: %sx%s quality=%s", width, height, quality)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=max(2.0, config.CAMERA_TIMEOUT_SEC)
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        output_path.unlink(missing_ok=True)
        raise TimeoutError("camera capture timed out") from exc

    if process.returncode != 0 or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        message = stderr.decode("utf-8", "replace").strip()
        if not message:
            message = stdout.decode("utf-8", "replace").strip()
        raise RuntimeError(f"camera capture failed: {message[-1000:] or 'unknown error'}")

    try:
        with Image.open(output_path) as image:
            image.verify()
        with Image.open(output_path) as image:
            actual_width, actual_height = image.size
        image_bytes = output_path.read_bytes()
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    _cleanup_old_captures(output_dir, config.CAMERA_KEEP_CAPTURES)
    log.info("camera image captured: %s (%d bytes)", output_path, len(image_bytes))
    if photo_callback:
        try:
            photo_callback(str(output_path))
        except Exception:
            log.exception("failed to show camera preview: %s", output_path)
    if progress_callback:
        progress_callback(f"camera\nAnalyzing {actual_width}x{actual_height} photo...")

    try:
        vision_result = await asyncio.to_thread(
            _upload_for_explanation, image_bytes, question
        )
        log.info("camera image analyzed successfully")
    finally:
        if progress_callback:
            progress_callback(None)

    return McpToolResult(content=[{"type": "text", "text": vision_result}])
