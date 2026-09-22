#!/usr/bin/env python3
"""Render the actual Rust LCD frames to a GIF; no board or camera required.

Run after tools/build_watercolor_rust.sh:
    python tools/preview_robot.py --output /tmp/robot-workstation.gif
"""
import argparse
from functools import partial
from pathlib import Path
import sys
import time
from unittest.mock import patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from display.ui_renderer import UIRenderer
from display.robot_backend import RustRobotRenderer


class PreviewBoard:
    LCD_WIDTH = 240
    LCD_HEIGHT = 280

    def draw_image(self, x, y, width, height, frame):
        self.frame = frame


class PreviewUI(UIRenderer):
    def _render_logo(self):
        pass


def unpack(frame, width=240, height=280):
    pixels = np.frombuffer(frame, dtype=">u2").reshape(height, width)
    rgb = np.stack((((pixels >> 11) & 31) * 255 // 31,
                    ((pixels >> 5) & 63) * 255 // 63,
                    (pixels & 31) * 255 // 31), axis=-1).astype(np.uint8)
    return Image.fromarray(rgb)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="/tmp/robot-workstation.gif")
    parser.add_argument("--font", default="")
    parser.add_argument("--events", action="store_true", help="Preview fire/extinguisher and rain/umbrella scenes")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    candidates = [args.font, root / "assets/NotoSansSC-Bold.ttf",
                  "/System/Library/Fonts/Supplemental/Arial.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    font = next((str(p) for p in candidates if p and Path(p).is_file()), "")
    if not font:
        parser.error("pass --font with a TrueType font, or run install.sh")
    board = PreviewBoard()
    with patch("config.DISPLAY_UI_STYLE", "robot"), patch(
        "display.ui_renderer.RustRobotRenderer", partial(RustRobotRenderer, seed=7)
    ), patch(
        "display.ui_renderer.time.monotonic", return_value=0.0
    ):
        ui = PreviewUI(board, font_path=font)
    ui.update(battery_level=72, battery_color=(255, 255, 255), wifi_signal_level=3)
    frames = []
    timings = []
    # Show a glance, scratching while thinking, typing, sleep, and a photo.
    for i in range(720 if args.events else 540):
        t = i / 20
        thinking = not args.events and 15 <= t < 18
        working = args.events or 18 <= t < 21 or t >= 25
        idle = 60.0 if not args.events and 21 <= t < 25 else 0.0
        label = "Thinking" if thinking else "Working" if working else "Sleeping" if idle else "Idle"
        if args.events:
            label = ("Oh no!" if 1 <= t < 3 else "Grab extinguisher" if 3 <= t < 5.6
                     else "Extinguish!" if 5.6 <= t < 10 else "Put it back" if 10 <= t < 14
                     else "Rain!" if 17.7 <= t < 18.8 else "Find umbrella" if 18.8 <= t < 21.8
                     else "Keep dry" if 21.8 <= t < 29 else "Rain stopped" if 29 <= t < 32 else "Working")
        ui.update(activity="thinking" if thinking else "speaking" if working else "idle", text=label)
        if idle:
            ui._robot_idle_since = -60.0
        start = time.perf_counter()
        with patch("display.ui_renderer.time.monotonic", return_value=t), patch(
            "config.ROBOT_EVENTS_ENABLED", args.events
        ):
            if args.events and i in (20, 320):
                ui._robot.trigger_event("fire" if i == 20 else "rain")
            if i == (660 if args.events else 500):
                # Existing project logo stands in for a captured camera frame.
                ui.show_photo(str(root / "assets/logo.png"), duration=2.0)
            ui._render_frame()
        timings.append((time.perf_counter() - start) * 1000)
        frames.append(unpack(board.frame))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, save_all=True, append_images=frames[1:],
                   duration=50, loop=0, optimize=False)
    # A larger contact sheet helps inspect geometry; the GIF keeps LCD size.
    columns = 3 if args.events else 5
    sheet = Image.new("RGB", (240 * columns, 560 if args.events else 280))
    indices = (40, 80, 145, 360, 400, 465) if args.events else (180, 330, 390, 485, 525)
    for cell, index in enumerate(indices):
        sheet.paste(frames[index], (240 * (cell % columns), 280 * (cell // columns)))
    sheet.save(output.with_suffix(".png"))
    print(f"Saved {output} and {output.with_suffix('.png')}")
    print(f"UI render (this host, includes mock clock, excludes SPI): median={np.median(timings):.2f}ms "
          f"p95={np.percentile(timings, 95):.2f}ms")


if __name__ == "__main__":
    main()
