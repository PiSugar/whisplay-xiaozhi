import importlib.util
import math
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image, ImageFont

if importlib.util.find_spec("dotenv") is None:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

from display.robot_backend import RustRobotRenderer
from display.ui_renderer import UIRenderer, DisplayState
from display.watercolor_backend import _load_native_renderer

NativeRobotRenderer = _load_native_renderer("RobotRenderer")


def fake_ui():
    ui = object.__new__(UIRenderer)
    ui.board = Mock(LCD_WIDTH=240, LCD_HEIGHT=280)
    ui.ui_style = "robot"
    ui.state = DisplayState()
    ui._robot = Mock()
    ui._robot_rotate_requested = threading.Event()
    ui._robot.render.return_value = bytes(240 * 280 * 2)
    ui._robot.caption_pages.return_value = ["first", "second"]
    ui._watercolor = None
    ui._robot_started_at = 0.0
    ui._robot_idle_since = 0.0
    ui._watercolor_caption_source = ""
    ui._watercolor_caption_page = 0
    ui._watercolor_caption_started_at = 0.0
    ui._photo_lock = threading.Lock()
    ui._photo_preview = None
    ui._photo_preview_until = 0.0
    ui._watercolor_status_overlay_cache = {}
    ui._battery_font = ImageFont.load_default()
    ui._wifi_source_icon_cache = {}
    ui._wifi_scaled_icon_cache = {}
    return ui


class RobotIntegrationTests(unittest.TestCase):
    def test_tool_tags_do_not_replace_or_reset_caption_progress(self):
        ui = fake_ui()
        with patch('config.WATERCOLOR_CAPTION_PAGE_SECONDS', 3):
            self.assertEqual(ui._select_watercolor_caption({'activity':'speaking','text':'hello world'}, 10), 'first')
            self.assertEqual(ui._select_watercolor_caption({'activity':'speaking','text':'hello world'}, 14), 'second')
            baseline = (ui._watercolor_caption_page, ui._watercolor_caption_started_at)
            for terminal in ('Running command', 'Updated output', ''):
                snap = {'activity':'speaking', 'text':'hello %web.search... world', 'terminal_text':terminal}
                self.assertEqual(ui._select_watercolor_caption(snap, 15), 'second')
                self.assertEqual((ui._watercolor_caption_page, ui._watercolor_caption_started_at), baseline)
                self.assertEqual(ui._watercolor_caption_source, 'hello world')
            self.assertEqual(ui._select_watercolor_caption({'activity':'thinking','text':'%web.search...'},16),'second')
            self.assertEqual(ui._select_watercolor_caption({'activity':'thinking','text':'hello world'},16.5),'second')
            self.assertEqual(ui._select_watercolor_caption({'activity':'speaking','text':'hello world'},17),'second')

    def test_tags_render_above_caption_and_keep_caption_pixels_unchanged(self):
        ui = fake_ui()
        ui._tool_tag_font = ImageFont.load_default()
        ui._robot._captions = types.SimpleNamespace(status_top=238)
        base = bytes([0x12,0x34]) * (240*280)
        snap = {'text':'before %web.search... %web.search... after','activity':'speaking'}
        self.assertEqual(ui._compact_caption_content(snap), ('before after','web.search',2))
        frame = ui._composite_caption_tool_tag(base, snap)
        self.assertNotEqual(frame, base)
        self.assertEqual(frame[238*240*2:], base[238*240*2:])
        self.assertEqual(frame[:206*240*2], base[:206*240*2])
        self.assertEqual(ui._composite_caption_tool_tag(base, {'text':'no tools'}), base)

    def test_portrait_and_landscape_photos_fill_card_without_matte(self):
        for size in [(50, 200), (400, 50), (100, 100)]:
            ui = fake_ui()
            ui._show_photo_image(Image.new('RGB', size, (19, 83, 157)), duration=2)
            preview = ui._active_photo_preview()
            self.assertEqual(preview['rgb'].shape, (44, 76, 3))
            self.assertTrue(np.all(preview['rgb'] == np.array([19, 83, 157])))
            self.assertTrue(np.all(preview['alpha'] == 255))

    def test_rotation_is_consumed_by_render_thread_once(self):
        ui = fake_ui()
        ui.rotate_robot_view()
        ui._robot.rotate_view.assert_not_called()
        ui._render_robot_frame(ui.state.snapshot())
        ui._render_robot_frame(ui.state.snapshot())
        ui._robot.rotate_view.assert_called_once()

    def test_missing_or_outdated_extension_has_rebuild_instruction(self):
        with patch("display.robot_backend._load_native_renderer", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "build_watercolor_rust.sh"):
                RustRobotRenderer()

    def test_activity_wakes_and_terminal_triggers_typing(self):
        ui = fake_ui()
        with patch("display.ui_renderer.time.monotonic", return_value=50.0):
            ui._render_robot_frame({**ui.state.snapshot(), "activity": "idle"})
        self.assertEqual(ui._robot.render.call_args.args[1:3], (False, 50.0))
        for activity in ("listening", "camera", "connecting", "speaking", "activating"):
            with patch("display.ui_renderer.time.monotonic", return_value=60.0):
                ui._render_robot_frame({**ui.state.snapshot(), "activity": activity})
            args = ui._robot.render.call_args.args
            self.assertEqual(args[1], activity not in ("listening", "camera"))
            self.assertEqual(args[2], 0.0)
        with patch("display.ui_renderer.time.monotonic", return_value=70.0):
            ui._render_robot_frame({**ui.state.snapshot(), "activity": "idle", "terminal_text": "Running"})
        self.assertEqual(ui._robot.render.call_args.args[1:3], (True, 0.0))

    def test_robot_photo_is_small_card_and_expires(self):
        ui = fake_ui()
        with patch("display.ui_renderer.time.monotonic", return_value=10.0):
            ui._show_photo_image(Image.new("RGB", (320, 240), "red"), duration=2)
            preview = ui._active_photo_preview()
            ui._render_frame()
        self.assertEqual(preview["kind"], "robot")
        self.assertLessEqual(preview["y1"], 84)
        self.assertEqual(preview["x1"] - preview["x0"], 76)
        self.assertEqual(ui._robot.render.call_args.args[2], 0.0)
        frame = ui.board.draw_image.call_args.args[-1]
        pixels = np.frombuffer(frame, dtype=">u2").reshape(280, 240)
        self.assertNotEqual(pixels[60, 120], 0)
        self.assertTrue(np.all(pixels[90:220] == 0))
        with patch("display.ui_renderer.time.monotonic", return_value=12.1):
            self.assertIsNone(ui._active_photo_preview())

    def test_thinking_suppresses_typing_and_resets_idle(self):
        ui = fake_ui()
        with patch("display.ui_renderer.time.monotonic", return_value=70.0):
            ui._render_robot_frame({**ui.state.snapshot(), "activity": "thinking", "terminal_text": "Working..."})
        self.assertEqual(ui._robot.render.call_args.args[1:3], (False, 0.0))
        self.assertTrue(ui._robot.render.call_args.kwargs["thinking"])
        self.assertFalse(ui._robot.render.call_args.kwargs["effects_allowed"])

    def test_events_do_not_compete_with_listening_camera_or_tools(self):
        ui = fake_ui()
        for activity in ("listening", "camera"):
            ui._render_robot_frame({**ui.state.snapshot(), "activity": activity})
            self.assertFalse(ui._robot.render.call_args.kwargs["effects_allowed"])
        ui._render_robot_frame({**ui.state.snapshot(), "terminal_text": "Tool running"})
        self.assertFalse(ui._robot.render.call_args.kwargs["effects_allowed"])
        with patch("config.ROBOT_EVENTS_ENABLED", False):
            ui._render_robot_frame(ui.state.snapshot())
        self.assertFalse(ui._robot.render.call_args.kwargs["effects_allowed"])

    def test_status_and_caption_use_watercolor_layout(self):
        ui = fake_ui()
        snap = {"activity": "speaking", "text": "hello world",
                "battery_level": 72, "battery_color": (255, 255, 255),
                "wifi_signal_level": 3}
        with patch("display.ui_renderer.time.monotonic", return_value=2.0):
            ui._render_robot_frame(snap)
        self.assertEqual(ui._robot.render.call_args.args[-1], "first")
        frame = ui.board.draw_image.call_args.args[-1]
        pixels = np.frombuffer(frame, dtype=">u2").reshape(280, 240)
        self.assertTrue(np.any(pixels[:40, 120:] != 0))
        with patch("display.ui_renderer.time.monotonic", return_value=20.0):
            ui._render_robot_frame(snap)
        self.assertEqual(ui._robot.render.call_args.args[-1], "second")


@unittest.skipIf(NativeRobotRenderer is None, "build the updated Rust extension first")
class NativeRobotTests(unittest.TestCase):
    def test_work_break_preview_and_orbit_frames(self):
        r = NativeRobotRenderer(seed=7)
        with self.assertRaises(ValueError):
            r.trigger_work_break('unknown')
        r.trigger_work_break('drink')
        before = r.rgb565(0.0, True, 0.0, effects_allowed=False)
        for i in range(1, 100):
            frame = r.rgb565(i / 30, True, 0.0, effects_allowed=False)
        self.assertNotEqual(before, frame)
        r.rotate_view()
        for i in range(100, 140):
            turned = r.rgb565(i / 30, True, 0.0, effects_allowed=False)
        self.assertNotEqual(frame, turned)
        self.assertEqual(len(turned), 240 * 280 * 2)

    def test_event_lifecycle_and_frame_format(self):
        for kind, duration in (("fire", 13), ("rain", 16)):
            r = NativeRobotRenderer(seed=7)
            baseline = NativeRobotRenderer(seed=7)
            r.trigger_event(kind)
            with self.assertRaisesRegex(ValueError, "already active"):
                r.trigger_event("rain")
            for i in range((duration + 1) * 20):
                frame = r.rgb565(i / 20, True, 0.0)
                plain = baseline.rgb565(i / 20, True, 0.0, effects_allowed=False)
                if i == 80:
                    self.assertEqual(len(frame), 240 * 280 * 2)
                    self.assertNotEqual(frame, plain)
            r.trigger_event("fire")  # Finished events release the scheduler.
            for i in range(20):
                r.rgb565(duration + 1 + i / 20, True, 0.0, effects_allowed=False)
            r.trigger_event("rain")  # Priority interruption also releases it.
            with self.assertRaisesRegex(ValueError, "fire or rain"):
                r.trigger_event("unknown")

    def test_animation_frames_and_scene_bounds(self):
        r = NativeRobotRenderer()
        a = r.rgb565(0.0, False, 0.0)
        b = r.rgb565(7.25, False, 0.0)
        self.assertEqual(len(a), 240 * 280 * 2)
        self.assertNotEqual(a, b)
        p = np.frombuffer(a, dtype=">u2").reshape(280, 240)
        self.assertTrue(np.any(p[85:215]))
        self.assertFalse(np.any(p[:84]))
        self.assertFalse(np.any(p[220:]))

    def test_sleep_then_wake_produces_different_pose(self):
        r = NativeRobotRenderer()
        for i in range(90):
            asleep = r.rgb565(i / 30, False, 60.0)
        for i in range(90, 180):
            awake = r.rgb565(i / 30, True, 0.0)
        # Exclude the terminal screen and floating Z: inspect the robot head.
        asleep = np.frombuffer(asleep, dtype=">u2").reshape(280, 240)
        awake = np.frombuffer(awake, dtype=">u2").reshape(280, 240)
        self.assertFalse(np.array_equal(asleep[85:115, 125:165], awake[85:115, 125:165]))

    def test_overlay_and_invalid_input(self):
        r = NativeRobotRenderer(120, 140)
        white = bytes([255, 255, 255, 255]) * (120 * 140)
        self.assertEqual(r.rgb565(0.0, False, 0.0, 45.0, white), b"\xff\xff" * (120 * 140))
        with self.assertRaises(ValueError):
            r.rgb565(0.0, False, 0.0, 45.0, b"bad")
        for t in (math.nan, math.inf, -1.0):
            with self.assertRaises(ValueError):
                r.rgb565(t, False, 0.0)
        for size in ((0, 280), (240, 10000)):
            with self.assertRaises(ValueError):
                NativeRobotRenderer(*size)

    def test_thinking_is_distinct_from_typing_at_same_time(self):
        thinking, typing = NativeRobotRenderer(), NativeRobotRenderer()
        for i in range(60):
            scratch = thinking.rgb565(i / 30, False, 0.0, thinking=True)
            work = typing.rgb565(i / 30, True, 0.0)
        a = np.frombuffer(scratch, dtype=">u2").reshape(280, 240)
        b = np.frombuffer(work, dtype=">u2").reshape(280, 240)
        self.assertGreater(np.count_nonzero(a[100:155, 95:155] != b[100:155, 95:155]), 30)


if __name__ == "__main__":
    unittest.main()
