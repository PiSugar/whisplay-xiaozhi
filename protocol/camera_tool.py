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
FrameCallback = Callable[[bytes], None]

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

SELECTED_PHOTO_DESCRIPTION = (
    "Analyze the photo that the user manually framed and captured with the device button. "
    "Use this whenever the user refers to this photo, the selected photo, or asks to use, "
    "record, organize, remember, or add something visible in that photo to a list. The "
    "selected photo persists across conversation turns until the user takes another one."
)

PENDING_SELECTED_PHOTO_DESCRIPTION = (
    "ACTIVE INPUT ATTACHMENT: the user has just manually captured a photo and it is "
    "automatically attached to their next request. For the next request, ambiguous "
    "references such as this, it, that, these, the item, or the thing refer to this "
    "selected photo even when the user does not say the word photo. You MUST call this "
    "tool first with the user's request as the question, then use its result to complete "
    "the requested task, including adding visible items to a shopping list."
)

SELECTED_PHOTO_INPUT_SCHEMA = CAMERA_CAPTURE_INPUT_SCHEMA


@dataclass
class _VisionConfig:
    url: str = ""
    token: str = ""
    device_id: str = ""
    client_id: str = ""


_vision = _VisionConfig()
_selected_user_photo: Path | None = None


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


def _viewfinder_command() -> str:
    configured = config.CAMERA_VIEWFINDER_COMMAND
    if configured:
        resolved = shutil.which(configured) if "/" not in configured else configured
        if resolved and os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return resolved
        raise RuntimeError(f"configured viewfinder command is unavailable: {configured}")
    for candidate in ("rpicam-vid", "libcamera-vid"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("Raspberry Pi viewfinder command not found (rpicam-vid/libcamera-vid)")


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


def set_selected_user_photo(path: str | Path) -> Path:
    """Select a manually captured photo for later conversational use."""
    global _selected_user_photo
    selected = Path(path).resolve()
    if not selected.is_file():
        raise FileNotFoundError(f"selected photo does not exist: {selected}")
    _selected_user_photo = selected
    log.info("selected user photo: %s", selected)
    return selected


def get_selected_user_photo() -> Path | None:
    global _selected_user_photo
    selected = _selected_user_photo
    if selected is not None and selected.is_file():
        return selected
    try:
        saved = sorted(
            _output_directory().glob("user-photo-*.jpg"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        saved = []
    if saved:
        _selected_user_photo = saved[0].resolve()
        return _selected_user_photo
    return None


def save_user_photo(jpeg: bytes) -> Path:
    """Persist a frame captured from the button-driven viewfinder."""
    if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.rstrip().endswith(b"\xff\xd9"):
        raise ValueError("viewfinder did not provide a valid JPEG frame")
    output_dir = _output_directory()
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_path = output_dir / f"user-photo-{timestamp}-{uuid.uuid4().hex[:6]}.jpg"
    output_path.write_bytes(jpeg)
    try:
        with Image.open(output_path) as image:
            image.verify()
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return set_selected_user_photo(output_path)


class CameraViewfinder:
    """Stream MJPEG frames from rpicam-vid for the button-driven viewfinder."""

    def __init__(self, frame_callback: FrameCallback | None = None):
        self.frame_callback = frame_callback
        self.process: asyncio.subprocess.Process | None = None
        self.latest_frame: bytes | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._frame_ready = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        command = [
            _viewfinder_command(),
            "--camera", str(config.CAMERA_INDEX),
            "--nopreview",
            "--timeout", "0",
            "--codec", "mjpeg",
            "--framerate", str(config.CAMERA_VIEWFINDER_FPS),
            "--width", str(config.CAMERA_VIEWFINDER_WIDTH),
            "--height", str(config.CAMERA_VIEWFINDER_HEIGHT),
            "--quality", str(config.CAMERA_VIEWFINDER_QUALITY),
            "--flush",
            "--output", "-",
        ]
        if config.CAMERA_AUTOFOCUS:
            command.extend(
                [
                    "--autofocus-mode", "continuous",
                    "--autofocus-range", config.CAMERA_AUTOFOCUS_RANGE,
                    "--autofocus-speed", config.CAMERA_AUTOFOCUS_SPEED,
                ]
            )
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True
        self._reader_task = asyncio.create_task(self._read_frames())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        log.info(
            "camera viewfinder started: %sx%s @ %s fps",
            config.CAMERA_VIEWFINDER_WIDTH,
            config.CAMERA_VIEWFINDER_HEIGHT,
            config.CAMERA_VIEWFINDER_FPS,
        )

    async def _read_frames(self) -> None:
        if not self.process or not self.process.stdout:
            return
        buffer = bytearray()
        try:
            while self._running:
                chunk = await self.process.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        if len(buffer) > 1:
                            del buffer[:-1]
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start:
                            del buffer[:start]
                        if len(buffer) > config.CAMERA_MAX_FRAME_BYTES:
                            buffer.clear()
                        break
                    frame = bytes(buffer[start : end + 2])
                    del buffer[: end + 2]
                    if len(frame) > config.CAMERA_MAX_FRAME_BYTES:
                        continue
                    self.latest_frame = frame
                    self._frame_ready.set()
                    if self.frame_callback:
                        try:
                            self.frame_callback(frame)
                        except Exception:
                            log.exception("failed to display camera viewfinder frame")
        finally:
            self._running = False

    async def _drain_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        tail = bytearray()
        while True:
            chunk = await self.process.stderr.read(4096)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > 8192:
                del tail[:-8192]
        if self.process.returncode not in (None, 0, -15) and tail:
            log.warning("camera viewfinder stderr: %s", tail.decode("utf-8", "replace")[-1000:])

    async def capture(self) -> Path:
        try:
            await asyncio.wait_for(
                self._frame_ready.wait(), timeout=config.CAMERA_VIEWFINDER_READY_TIMEOUT_SEC
            )
            frame = self.latest_frame
            if not frame:
                raise RuntimeError("camera viewfinder has no frame")
            return save_user_photo(frame)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        process = self.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not current and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.process = None
        log.info("camera viewfinder stopped")


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


async def analyze_selected_photo(
    params: dict,
    progress_callback: ProgressCallback | None = None,
    photo_callback: PhotoCallback | None = None,
) -> McpToolResult:
    question = str(params.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    selected = get_selected_user_photo()
    if selected is None:
        raise RuntimeError(
            "No user-selected photo is available. Ask the user to double-click the button, "
            "frame the photo, and single-click to capture it."
        )
    image_bytes = selected.read_bytes()
    if photo_callback:
        photo_callback(str(selected))
    if progress_callback:
        progress_callback("camera\nAnalyzing selected photo...")
    try:
        vision_text = await asyncio.to_thread(_upload_for_explanation, image_bytes, question)
        result = json.loads(vision_text)
        if isinstance(result, dict):
            result["device_photo"] = {
                "path": str(selected),
                "source": "user_button_capture",
                "persistent": True,
            }
        else:
            result = {"result": result, "device_photo": {"path": str(selected)}}
        log.info("selected user photo analyzed successfully: %s", selected)
        return McpToolResult(
            content=[{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        )
    finally:
        if progress_callback:
            progress_callback(None)


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
