import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import config
from application import Application
from protocol.mcp_handler import McpToolResult


class ApplicationListeningStateTests(unittest.IsolatedAsyncioTestCase):
    def robot_button_app(self):
        app = object.__new__(Application)
        app._camera_viewfinder = None
        app._button_click_task = None
        app._button_hold_task = None
        app._robot_button_down = False
        app._robot_button_rotated = False
        app._robot_second_click = False
        app.display = Mock()
        app._handle_button_press = AsyncMock()
        app._enter_camera_viewfinder = AsyncMock()
        return app

    async def test_robot_hold_rotates_once_without_wake(self):
        app = self.robot_button_app()
        with patch('application.config.DISPLAY_UI_STYLE', 'robot'):
            await app._handle_physical_button_down()
            await asyncio.sleep(0.70)
            await app._handle_physical_button_down()
            await asyncio.sleep(0.05)
            await app._handle_physical_button_up()
        app.display.rotate_robot_view.assert_called_once()
        app._handle_button_press.assert_not_awaited()
        app._enter_camera_viewfinder.assert_not_awaited()

    async def test_robot_short_and_double_click_remain_available(self):
        app = self.robot_button_app()
        with patch('application.config.DISPLAY_UI_STYLE', 'robot'), patch(
            'application.config.CAMERA_DOUBLE_CLICK_SECONDS', 0.02
        ):
            await app._handle_physical_button_down()
            await app._handle_physical_button_up()
            await asyncio.sleep(0.04)
            app._handle_button_press.assert_awaited_once()
            app._handle_button_press.reset_mock()
            for _ in range(2):
                await app._handle_physical_button_down()
                await app._handle_physical_button_up()
            await asyncio.sleep(0.04)
        app._enter_camera_viewfinder.assert_awaited_once()
        app._handle_button_press.assert_not_awaited()
        app.display.rotate_robot_view.assert_not_called()

    async def test_double_click_enters_camera_without_single_click_action(self):
        app = object.__new__(Application)
        app._camera_viewfinder = None
        app._camera_capture_task = None
        app._button_click_task = None
        app._enter_camera_viewfinder = AsyncMock()
        app._handle_button_press = AsyncMock()

        await app._handle_button_click_event()
        await app._handle_button_click_event()
        await asyncio.sleep(0)

        app._enter_camera_viewfinder.assert_awaited_once()
        app._handle_button_press.assert_not_awaited()

    async def test_single_click_keeps_existing_wake_behavior(self):
        app = object.__new__(Application)
        app._camera_viewfinder = None
        app._camera_capture_task = None
        app._button_click_task = None
        app._handle_button_press = AsyncMock()

        with patch("application.config.CAMERA_DOUBLE_CLICK_SECONDS", 0):
            await app._handle_button_click_event()
            await asyncio.sleep(0.01)

        app._handle_button_press.assert_awaited_once()

    async def test_manual_photo_refreshes_context_and_auto_listens_after_reconnect(self):
        app = object.__new__(Application)
        app._camera_viewfinder = Mock()
        app._camera_viewfinder.capture = AsyncMock(
            return_value=Path("/tmp/user-photo-test.jpg")
        )
        app._camera_capture_task = Mock()
        app.client = Mock(connected=False)
        app.display = Mock()
        app.mcp = Mock()
        app._set_state = Mock()
        app._update_display = Mock()
        app._schedule_reconnect = Mock()

        await app._capture_user_photo()

        app.mcp.update_description.assert_called_once()
        app._schedule_reconnect.assert_called_once_with(
            silent=True, resume_listening=True
        )
        app._update_display.assert_any_call(
            status="Photo saved",
            emoji="✅",
            text="Photo attached. Tell Xiaozhi what to do with it.",
        )

    async def test_official_camera_tool_uses_pending_button_photo(self):
        app = object.__new__(Application)
        app._pending_photo_for_next_input = True
        app.mcp = Mock()
        app._update_terminal_progress = Mock()
        app._show_camera_photo = Mock()
        app._schedule_terminal_clear = Mock()
        vision = McpToolResult(
            content=[{"type": "text", "text": '{"text":"a carton of milk"}'}]
        )

        with patch("application.analyze_selected_photo", AsyncMock(return_value=vision)):
            result = await app._capture_photo_with_display(
                {"question": "Add this to my shopping list"}
            )

        self.assertIs(result, vision)
        self.assertFalse(app._pending_photo_for_next_input)
        app.mcp.update_description.assert_called_once()

    async def test_start_listening_replaces_idle_prompt(self):
        app = object.__new__(Application)
        app.client = Mock(connected=True)
        app.client.send_listen_start = AsyncMock()
        app.led = None
        app.display = None
        app.recorder = Mock()
        app._state = app.IDLE
        app._keep_listening = True
        app._tts_text_buffer = "old"
        app._recording_task = None
        app._update_display = Mock()
        app._stream_audio = AsyncMock()

        await app._start_listening()
        if app._recording_task:
            await app._recording_task

        app._update_display.assert_any_call(
            status="Listening...", emoji="🎤", text="", activity="listening"
        )
        self.assertEqual(app.state, app.LISTENING)

    async def test_recognition_and_listen_stop_trigger_thinking_animation(self):
        app = object.__new__(Application)
        app._state = app.LISTENING
        app._stop_listening = AsyncMock()
        app._update_display = Mock()
        await app._on_stt("What is this?")
        app._update_display.assert_any_call(status="Thinking...", emoji="🤔", activity="thinking")
        app._update_display.reset_mock()
        await app._on_listen_stop()
        app._update_display.assert_any_call(status="Thinking...", emoji="🤔", activity="thinking")

    async def test_speaker_write_drives_watercolor_and_echo_reference(self):
        app = object.__new__(Application)
        app._barge_in = Mock()
        app.display = Mock()
        pcm = b"\x01\x00" * 32

        app._on_speaker_pcm_written(pcm)

        app._barge_in.update_speaker.assert_called_once_with(pcm)
        app.display.update_audio.assert_called_once_with(
            pcm, config.AUDIO_OUTPUT_SAMPLE_RATE, "assistant"
        )


if __name__ == "__main__":
    unittest.main()
