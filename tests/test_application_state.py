import unittest
from unittest.mock import AsyncMock, Mock

import config
from application import Application


class ApplicationListeningStateTests(unittest.IsolatedAsyncioTestCase):
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
