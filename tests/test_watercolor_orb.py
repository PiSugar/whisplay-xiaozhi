import os
import importlib.util
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np

from display.watercolor_orb import OrbRenderer

if importlib.util.find_spec("dotenv") is None:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

from display.ui_renderer import UIRenderer
from display.watercolor_backend import NativeOrbRenderer, create_watercolor_renderer


class _FakeBoard:
    LCD_WIDTH = 240
    LCD_HEIGHT = 280
    CornerHeight = 12

    def __init__(self):
        self.frame = None

    def draw_image(self, x, y, width, height, data):
        self.frame = (x, y, width, height, bytes(data))


class WatercolorOrbTests(unittest.TestCase):
    def test_watercolor_requires_native_renderer(self):
        with (
            patch("display.watercolor_backend.NativeOrbRenderer", None),
            self.assertRaisesRegex(RuntimeError, "requires the Rust extension"),
        ):
            create_watercolor_renderer(render_scale=0.2)

    def test_native_backend_frame_size_when_extension_is_built(self):
        if NativeOrbRenderer is None:
            self.skipTest("Rust watercolor extension is not built")
        renderer = create_watercolor_renderer(threads=2, render_scale=0.2)
        plain = renderer.rgb565(1.0, 0.0)
        frame = renderer.rgb565(
            1.0,
            0.7,
            bands=(0.2, 0.4, 0.7, 0.6),
            cumulative=(0.1, 0.3, 0.6, 0.5),
            caption_text="native",
        )
        self.assertEqual(renderer.backend, "rust")
        self.assertEqual(len(frame), 240 * 280 * 2)
        self.assertNotEqual(frame, plain)

    def test_rgb565_frame_matches_lcd_size(self):
        renderer = OrbRenderer(render_scale=0.2)
        frame = renderer.rgb565(phase=0.0, level=0.0)
        self.assertEqual(len(frame), 240 * 280 * 2)

    def test_assistant_audio_changes_internal_pigment(self):
        renderer = OrbRenderer(render_scale=0.2)
        renderer.phase_offset = 0.0
        quiet = renderer.rgb565(phase=1.0, level=0.0)
        speaking = renderer.rgb565(
            phase=1.0,
            level=0.7,
            bands=(0.2, 0.4, 0.7, 0.6),
            cumulative=(0.1, 0.3, 0.6, 0.5),
        )
        self.assertNotEqual(quiet, speaking)

    def test_user_scale_changes_boundary_without_moving_interior(self):
        renderer = OrbRenderer(render_scale=0.2)
        normal = np.asarray(renderer.image(1.0, 0.2, visual_scale=1.0))
        expanded = np.asarray(renderer.image(1.0, 0.2, visual_scale=1.12))
        self.assertTrue(np.array_equal(normal[130:150, 110:130], expanded[130:150, 110:130]))
        self.assertGreater(np.count_nonzero(expanded), np.count_nonzero(normal))

    def test_ui_renderer_selects_watercolor_and_accepts_pcm(self):
        if NativeOrbRenderer is None:
            self.skipTest("Rust watercolor extension is not built")
        board = _FakeBoard()
        font = next(
            (
                path
                for path in (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/System/Library/Fonts/Supplemental/Arial.ttf",
                )
                if os.path.exists(path)
            ),
            "",
        )
        if not font:
            self.skipTest("no test font available")
        with (
            patch("config.DISPLAY_UI_STYLE", "watercolor"),
            patch("config.WATERCOLOR_RENDER_SCALE", 0.2),
            patch.object(UIRenderer, "_render_logo"),
        ):
            ui = UIRenderer(board, font_path=font)
        samples = (np.sin(np.linspace(0, np.pi * 20, 960)) * 12000).astype(np.int16)
        ui.update(activity="speaking", text="Hello")
        ui.update_audio(samples.tobytes(), 16000, "assistant")
        ui._render_frame()
        self.assertIsNotNone(ui._watercolor)
        self.assertIsNotNone(board.frame)
        self.assertEqual(len(board.frame[4]), 240 * 280 * 2)
        # The reference 60 Hz accumulator advances pigment phase much farther
        # than the old raw-duration sum, making one spoken chunk visible.
        self.assertGreater(ui._audio["assistant"]["cumulative"][0], 0.5)
        self.assertEqual(len(ui._audio["assistant"]["bands"]), 4)

    def test_watercolor_caption_starts_at_first_page_and_advances_slowly(self):
        ui = object.__new__(UIRenderer)
        ui._watercolor = OrbRenderer(render_scale=0.2)
        ui._watercolor_caption_source = ""
        ui._watercolor_caption_page = 0
        ui._watercolor_caption_started_at = 0.0
        text = "这是第一段需要完整显示的字幕内容，这是第二段内容，这是最后一段内容。"
        pages = ui._watercolor.caption_pages(text)
        self.assertGreaterEqual(len(pages), 2)

        with patch("config.WATERCOLOR_CAPTION_PAGE_SECONDS", 3.0):
            first = ui._select_watercolor_caption(
                {"activity": "speaking", "text": text[:24]}, 10.0
            )
            after_append = ui._select_watercolor_caption(
                {"activity": "speaking", "text": text}, 11.0
            )
            before_timeout = ui._select_watercolor_caption(
                {"activity": "speaking", "text": text}, 12.9
            )
            second = ui._select_watercolor_caption(
                {"activity": "speaking", "text": text}, 13.1
            )

        self.assertEqual(first, pages[0])
        self.assertEqual(after_append, pages[0])
        self.assertEqual(before_timeout, pages[0])
        self.assertEqual(second, pages[1])
        recognized = ui._select_watercolor_caption(
            {"activity": "listening", "text": "🗣️ 用户识别内容"}, 14.0
        )
        self.assertEqual(recognized, "用户识别内容")

    def test_watercolor_caption_font_size_is_configurable(self):
        renderer = OrbRenderer(render_scale=0.2, caption_font_size=17)
        self.assertEqual(renderer.caption_font_size, 17)

    def test_watercolor_caption_horizontal_offset(self):
        centered = OrbRenderer(render_scale=0.2, caption_offset_x=0)
        shifted = OrbRenderer(render_scale=0.2, caption_offset_x=3)
        centered_box = centered._caption_layer("测试 ABC").getbbox()
        shifted_box = shifted._caption_layer("测试 ABC").getbbox()
        self.assertIsNotNone(centered_box)
        self.assertIsNotNone(shifted_box)
        self.assertEqual(shifted_box[0] - centered_box[0], 3)
        self.assertEqual(shifted_box[2] - centered_box[2], 3)


if __name__ == "__main__":
    unittest.main()
