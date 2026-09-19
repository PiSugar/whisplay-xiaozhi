import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from protocol.camera_tool import (
    analyze_selected_photo,
    capture_photo,
    configure_vision,
    save_user_photo,
)
from protocol.mcp_handler import McpHandler, McpToolResult


class _FakeCameraProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class CameraToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_uploads_photo_and_returns_vision_result(self):
        shown_photos = []
        camera_commands = []
        with tempfile.TemporaryDirectory() as temp_dir:
            async def fake_subprocess(*args, **kwargs):
                camera_commands.append(args)
                output_path = Path(args[args.index("--output") + 1])
                Image.new("RGB", (320, 240), "blue").save(output_path, "JPEG")
                return _FakeCameraProcess()

            with (
                patch("protocol.camera_tool._camera_command", return_value="/usr/bin/rpicam-still"),
                patch("protocol.camera_tool.config.CAMERA_OUTPUT_DIR", temp_dir),
                patch("protocol.camera_tool.asyncio.create_subprocess_exec", side_effect=fake_subprocess),
                patch("protocol.camera_tool.requests.post") as post,
            ):
                post.return_value.text = json.dumps(
                    {"success": True, "result": "a blue image"}
                )
                post.return_value.raise_for_status.return_value = None
                configure_vision(
                    "https://vision.example/mcp/vision/explain",
                    "secret-token",
                    "device-1",
                    "client-1",
                )
                result = await capture_photo(
                    {"question": "What is visible?", "width": 320, "height": 240},
                    photo_callback=shown_photos.append,
                )

        self.assertIsInstance(result, McpToolResult)
        self.assertEqual(result.content[0]["type"], "text")
        self.assertEqual(json.loads(result.content[0]["text"])["result"], "a blue image")
        request = post.call_args
        self.assertEqual(request.kwargs["data"]["question"], "What is visible?")
        self.assertTrue(request.kwargs["files"]["file"][1].startswith(b"\xff\xd8"))
        self.assertEqual(request.kwargs["headers"]["Device-Id"], "device-1")
        self.assertEqual(request.kwargs["headers"]["Client-Id"], "client-1")
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(len(shown_photos), 1)
        self.assertTrue(shown_photos[0].endswith(".jpg"))
        self.assertIn("--autofocus-on-capture", camera_commands[0])
        self.assertIn("--autofocus-range", camera_commands[0])

    async def test_plain_text_vision_response_is_wrapped_like_cardputer(self):
        from protocol.camera_tool import _upload_for_explanation

        configure_vision(
            "https://vision.example/mcp/vision/explain",
            "secret-token",
            "device-1",
            "client-1",
        )
        with patch("protocol.camera_tool.requests.post") as post:
            post.return_value.text = "a desk"
            post.return_value.raise_for_status.return_value = None
            result = _upload_for_explanation(b"\xff\xd8photo\xff\xd9", "What is visible?")

        self.assertEqual(json.loads(result), {"result": "a desk"})

    async def test_button_photo_persists_and_can_be_analyzed_later(self):
        shown_photos = []
        with tempfile.TemporaryDirectory() as temp_dir:
            jpeg_path = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (320, 240), "green").save(jpeg_path, "JPEG")
            with patch("protocol.camera_tool.config.CAMERA_OUTPUT_DIR", temp_dir):
                selected = save_user_photo(jpeg_path.read_bytes())
            self.assertTrue(selected.name.startswith("user-photo-"))

            with patch(
                "protocol.camera_tool._upload_for_explanation",
                return_value=json.dumps({"answer": "milk and apples"}),
            ) as upload:
                result = await analyze_selected_photo(
                    {"question": "Add these items to my shopping list"},
                    photo_callback=shown_photos.append,
                )

            payload = json.loads(result.content[0]["text"])
            self.assertEqual(payload["answer"], "milk and apples")
            self.assertEqual(payload["device_photo"]["source"], "user_button_capture")
            self.assertTrue(payload["device_photo"]["persistent"])
            self.assertEqual(payload["device_photo"]["path"], str(selected))
            self.assertEqual(shown_photos, [str(selected)])
            self.assertTrue(upload.call_args.args[0].startswith(b"\xff\xd8"))

    async def test_mcp_handler_preserves_image_blocks(self):
        handler = McpHandler()
        expected = [
            {"type": "text", "text": "captured"},
            {"type": "image", "data": "abc", "mimeType": "image/jpeg"},
        ]
        handler.register("self.camera.take_photo", lambda _: McpToolResult(expected))

        rpc_id, response = await handler.handle(
            {
                "payload": {
                    "id": "camera-1",
                    "method": "tools/call",
                    "params": {"name": "self.camera.take_photo", "arguments": {}},
                }
            }
        )

        self.assertEqual(rpc_id, "camera-1")
        self.assertEqual(response, {"content": expected})

    async def test_mcp_description_can_mark_photo_as_next_input_attachment(self):
        handler = McpHandler()
        handler.register("photo", lambda _: "ok", description="normal")
        handler.update_description("photo", "active attachment")

        rpc_id, response = await handler.handle(
            {
                "payload": {
                    "id": "tools-1",
                    "method": "tools/list",
                    "params": {},
                }
            }
        )

        self.assertEqual(rpc_id, "tools-1")
        self.assertEqual(response["tools"][0]["description"], "active attachment")


if __name__ == "__main__":
    unittest.main()
