import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import config
from application import Application


class ApplicationListeningStateTests(unittest.IsolatedAsyncioTestCase):
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
            status="Listening...", emoji="🎤", text=""
        )
        self.assertEqual(app.state, app.LISTENING)

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
