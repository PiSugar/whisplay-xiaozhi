import unittest
from unittest.mock import Mock, call, patch

from display.plus_ui import WhisplayPlusUI, _clean_user_text
from display.ui_renderer import DisplayState


class PlusTranscriptTests(unittest.TestCase):
    def test_user_prefix_cleanup_preserves_language_and_internal_symbols(self):
        self.assertEqual(_clean_user_text("\ufeff\u200b🗣️\ufffd  「你好」🙂 "), "「你好」🙂")
        self.assertEqual(_clean_user_text("¿Qué tal?"), "¿Qué tal?")
        self.assertEqual(_clean_user_text("\ufffd\u200b🗣️"), "")

    def test_chat_events_do_not_fall_back_to_raw_notice_text(self):
        ui = object.__new__(WhisplayPlusUI)
        ui.state = DisplayState()
        ui._append_user = Mock()
        ui._set_assistant = Mock()

        ui.update(text="🗣️ \ufffd", user_text="\ufffd")
        ui._append_user.assert_called_once_with("\ufffd")
        self.assertEqual(ui.state.snapshot()["text"], "")

        ui.update(text="%tool...", assistant_text="%tool...")
        ui._set_assistant.assert_called_once_with("%tool...")
        self.assertEqual(ui.state.snapshot()["text"], "")

    def test_new_command_replaces_output_and_ignores_stale_callbacks(self):
        ui = object.__new__(WhisplayPlusUI)
        ui._command = Mock()
        ui._command.get_parent.return_value = None
        ui._transcript = Mock()
        first_tag, second_tag = object(), object()
        ui._append_tool = Mock(side_effect=[first_tag, second_tag])
        ui._command_token = None
        ui._command_timeout = None
        loop = Mock()

        with patch("display.plus_ui.asyncio.get_running_loop", return_value=loop):
            older = ui.begin_command()
            ui._command.get_parent.return_value = ui._transcript
            ui.update_command(older, "$ first command")
            ui.update_command(older, None)
            previous_timer = ui._command_timeout

            latest = ui.begin_command()
            previous_timer.cancel.assert_called_once_with()
            ui._transcript.remove.assert_called_once_with(ui._command)
            self.assertEqual(ui._transcript.insert_child_after.call_args_list, [
                call(ui._command, first_tag), call(ui._command, second_tag),
            ])
            ui.update_command(latest, "$ second command")
            ui.update_command(older, "late output")
            ui.update_command(older, None)
            ui._command.set_text.assert_called_with("$ second command" + "\n" * 9)
            self.assertIsNone(ui._command_timeout)

            ui.update_command(latest, "\n".join(str(i) for i in range(1, 16)))
            ui._command.set_text.assert_called_with("\n".join(str(i) for i in range(6, 16)))

            ui.update_command(latest, None)
            loop.call_later.assert_called_with(5, ui._clear_command)
            ui._clear_command()
            self.assertEqual(ui._transcript.remove.call_count, 2)
            ui.update_command(older, "late output after expiry")
            ui._command.set_visible.assert_called_with(False)
            ui._command.set_text.assert_called_with("")
