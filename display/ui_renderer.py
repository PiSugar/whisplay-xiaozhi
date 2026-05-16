"""
UI renderer — renders status/emoji/text/battery to the Whisplay HAT LCD.

Runs in a background thread at ~30 FPS, directly writing RGB565 to the LCD via SPI.
Adapted from whisplay-chatbot python/chatbot-ui.py (RenderThread).
"""

import os
import time
import threading
import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from display.text_utils import (
    image_to_rgb565, wrap_text, draw_mixed_text, get_line_image, clear_line_cache,
    hex_to_rgb, luminance,
)

if TYPE_CHECKING:
    from hardware.whisplay_board import WhisplayBoard

log = logging.getLogger("display")

# Default font search paths
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_REQUIRED_FONT_PATH = os.path.join(_ASSETS_DIR, "NotoSansSC-Bold.ttf")
_WIFI_LEVEL_ICONS = {
    1: "wifi-weak.png",
    2: "wifi-medium.png",
    3: "wifi-strong.png",
}
_STATUS_ICON_HEIGHT = 15
_NETWORK_ICON_CENTER_SCALE = 1.4
_HEADER_TOP_Y = 8
_WIFI_EXTRA_UP_PX = 1
_TITLE_OFFSET_Y = -5
_EMOJI_OFFSET_Y = 10
_STATUS_ICON_GROUP_DOWN_PX = 5


def _find_font(custom_path: str = "") -> str:
    if custom_path and os.path.exists(custom_path):
        return custom_path
    if os.path.exists(_REQUIRED_FONT_PATH):
        return _REQUIRED_FONT_PATH
    return ""


class DisplayState:
    """Thread-safe snapshot of what should be rendered."""

    def __init__(self):
        self.lock = threading.Lock()
        self.status: str = "Hello"
        self.emoji: str = "😄"
        self.text: str = ""
        self.battery_level: int = -1
        self.battery_color: tuple[int, int, int] = (128, 128, 128)
        self.wifi_signal_level: int = 0
        self.scroll_top: float = 0.0
        self.scroll_speed: float = 0.25
        self._prev_text: str = ""

    def update(self, **kwargs):
        with self.lock:
            new_text = kwargs.get("text")
            if new_text is not None and new_text != self._prev_text:
                # Reset scroll when text changes (not appended)
                if not new_text.startswith(self._prev_text):
                    self.scroll_top = 0.0
                    clear_line_cache()
                self._prev_text = new_text
                self.text = new_text

            for key in (
                "status", "emoji", "battery_level", "battery_color",
                "wifi_signal_level", "scroll_speed",
            ):
                if key in kwargs and kwargs[key] is not None:
                    setattr(self, key, kwargs[key])

    def snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "emoji": self.emoji,
                "text": self.text,
                "battery_level": self.battery_level,
                "battery_color": self.battery_color,
                "wifi_signal_level": self.wifi_signal_level,
                "scroll_top": self.scroll_top,
                "scroll_speed": self.scroll_speed,
            }


class UIRenderer(threading.Thread):
    """Background thread that continuously renders the UI to the LCD."""

    def __init__(self, board: "WhisplayBoard", font_path: str = "", fps: int = 30):
        super().__init__(daemon=True)
        self.board = board
        self.fps = fps
        self.running = False
        self.state = DisplayState()

        resolved = _find_font(font_path)
        if not resolved:
            raise RuntimeError(
                f"Required font not found: {_REQUIRED_FONT_PATH}. "
                "Run install.sh to install NotoSansSC-Bold.ttf."
            )
        self._font_path = resolved
        self._text_font = None
        self._status_font = None
        self._emoji_font = None
        self._battery_font = None
        self._line_height = 0
        self._wifi_source_icon_cache: dict[str, Image.Image | None] = {}
        self._wifi_scaled_icon_cache: dict[tuple[str, int, float], Image.Image | None] = {}

        if resolved:
            self._text_font = ImageFont.truetype(resolved, 20)
            self._status_font = ImageFont.truetype(resolved, 20)
            self._emoji_font = ImageFont.truetype(resolved, 40)
            self._battery_font = ImageFont.truetype(resolved, 13)
            asc, desc = self._text_font.getmetrics()
            self._line_height = asc + desc

        # Show startup screen
        self._render_logo()

    # ==================== Public API ====================
    def update(self, **kwargs):
        """Thread-safe update of display data."""
        self.state.update(**kwargs)

    def run(self):
        self.running = True
        interval = 1.0 / self.fps
        while self.running:
            t0 = time.time()
            try:
                self._render_frame()
            except Exception as e:
                log.error("render error: %s", e)
            elapsed = time.time() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def stop(self):
        self.running = False

    # ==================== Internal rendering ====================
    def _render_logo(self):
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png"
        )
        if os.path.exists(logo_path):
            img = Image.open(logo_path).convert("RGBA")
            img = img.resize((self.board.LCD_WIDTH, self.board.LCD_HEIGHT), Image.LANCZOS)
            data = image_to_rgb565(img, self.board.LCD_WIDTH, self.board.LCD_HEIGHT)
            self.board.set_backlight(100)
            self.board.draw_image(0, 0, self.board.LCD_WIDTH, self.board.LCD_HEIGHT, data)
            time.sleep(1)

    def _render_frame(self):
        snap = self.state.snapshot()
        W, H = self.board.LCD_WIDTH, self.board.LCD_HEIGHT
        header_h = 98  # status + emoji + margin

        # Header
        header = Image.new("RGBA", (W, header_h), (0, 0, 0, 255))
        hdr_draw = ImageDraw.Draw(header)
        self._draw_header(header, hdr_draw, snap, W)
        self.board.draw_image(0, 0, W, header_h, image_to_rgb565(header, W, header_h))

        # Text area
        text_h = H - header_h
        text_img = Image.new("RGBA", (W, text_h), (0, 0, 0, 255))
        self._draw_text_area(text_img, text_h, snap)
        self.board.draw_image(0, header_h, W, text_h, image_to_rgb565(text_img, W, text_h))

    def _draw_header(self, image: Image.Image, draw: ImageDraw.Draw, snap: dict, width: int):
        if not self._status_font:
            return

        # Status text (top-left)
        draw_mixed_text(
            image,
            snap["status"],
            self._status_font,
            (self.board.CornerHeight, _HEADER_TOP_Y + _TITLE_OFFSET_Y),
        )

        # Emoji (centered)
        emoji = snap["emoji"]
        if self._emoji_font:
            bbox = self._emoji_font.getbbox(emoji)
            ew = bbox[2] - bbox[0]
            draw_mixed_text(image, emoji, self._emoji_font, ((width - ew) // 2, 28 + _EMOJI_OFFSET_Y))

        # Battery icon (top-right)
        self._draw_status_icons(draw, snap, width)

    def _draw_status_icons(self, draw: ImageDraw.Draw, snap: dict, width: int):
        cursor_x = width - 15
        icon_gap = 8

        battery_w = self._measure_battery_icon(snap["battery_level"])
        if battery_w > 0:
            cursor_x -= battery_w
            self._draw_battery(draw, snap, cursor_x, _HEADER_TOP_Y + _STATUS_ICON_GROUP_DOWN_PX)
            cursor_x -= icon_gap

        wifi_w = self._measure_wifi_icon(snap["wifi_signal_level"])
        if wifi_w > 0:
            cursor_x -= wifi_w
            self._draw_wifi(
                draw,
                snap["wifi_signal_level"],
                cursor_x,
                _HEADER_TOP_Y + _STATUS_ICON_GROUP_DOWN_PX - _WIFI_EXTRA_UP_PX,
            )

    def _measure_battery_icon(self, level: int) -> int:
        if level < 0:
            return 0
        bw = 26
        head_w = 2
        return bw + head_w

    def _draw_battery(self, draw: ImageDraw.Draw, snap: dict, x: int, y: int):
        level = snap["battery_level"]
        if level < 0:
            return
        color = snap["battery_color"]
        font = self._battery_font

        bw, bh = 26, 14
        corner = 3
        lw = 2
        head_w, head_h = 2, 5

        # Outline
        draw.rounded_rectangle([x, y, x + bw, y + bh], radius=corner,
                               outline="white", width=lw)
        # Fill
        if color != (0, 0, 0):
            draw.rectangle([x + lw, y + lw, x + bw - lw, y + bh - lw], fill=color)
        # Head
        draw.rectangle([x + bw, y + (bh - head_h) // 2,
                        x + bw + head_w, y + (bh + head_h) // 2], fill="white")
        # Level text
        if font:
            txt = str(level)
            tb = font.getbbox(txt)
            tw = tb[2] - tb[0]
            tx = x + (bw - tw) // 2
            asc, desc = font.getmetrics()
            ty = y + (bh - asc - desc) // 2
            fill = "black" if luminance(color) > 128 else "white"
            draw.text((tx, ty), txt, font=font, fill=fill)

    def _measure_wifi_icon(self, level: int) -> int:
        icon = self._get_wifi_icon(level)
        if not icon:
            return 0
        return max(1, int(round(icon.width / _NETWORK_ICON_CENTER_SCALE)))

    def _draw_wifi(self, draw: ImageDraw.Draw, level: int, x: int, y: int):
        icon = self._get_wifi_icon(level)
        if not icon:
            return

        base_w = self._measure_wifi_icon(level)
        paste_x = x + (base_w - icon.width) // 2
        paste_y = y + (_STATUS_ICON_HEIGHT - icon.height) // 2
        draw._image.paste(icon, (paste_x, paste_y), icon)

    def _get_wifi_icon(self, level: int) -> Image.Image | None:
        try:
            lvl = int(level)
        except (TypeError, ValueError):
            return None
        if lvl < 1 or lvl > 3:
            return None

        icon_name = _WIFI_LEVEL_ICONS[lvl]
        cache_key = (icon_name, _STATUS_ICON_HEIGHT, _NETWORK_ICON_CENTER_SCALE)
        if cache_key in self._wifi_scaled_icon_cache:
            return self._wifi_scaled_icon_cache[cache_key]

        if icon_name in self._wifi_source_icon_cache:
            src = self._wifi_source_icon_cache[icon_name]
        else:
            icon_path = os.path.join(_ASSETS_DIR, icon_name)
            src = None
            if os.path.exists(icon_path):
                src = Image.open(icon_path).convert("RGBA")
            self._wifi_source_icon_cache[icon_name] = src
        if not src:
            self._wifi_scaled_icon_cache[cache_key] = None
            return None

        src_w, src_h = src.size
        if src_h <= 0:
            self._wifi_scaled_icon_cache[cache_key] = None
            return None

        scaled_h = max(1, int(round(_STATUS_ICON_HEIGHT * _NETWORK_ICON_CENTER_SCALE)))
        scaled_w = max(1, int(round(src_w * scaled_h / src_h)))
        resized = src.resize((scaled_w, scaled_h), Image.LANCZOS)
        self._wifi_scaled_icon_cache[cache_key] = resized
        return resized

    def _draw_text_area(self, image: Image.Image, area_h: int, snap: dict):
        text = snap["text"]
        if not text or not self._text_font:
            return

        font = self._text_font
        lh = self._line_height
        W = self.board.LCD_WIDTH
        lines = wrap_text(text, font, W - 20)
        max_scroll = max(0, (len(lines) + 1) * lh - area_h)

        scroll_top = snap["scroll_top"]
        speed = snap["scroll_speed"]

        # Render visible lines
        y = 0
        for i, line in enumerate(lines):
            line_top = i * lh
            line_bot = line_top + lh
            if line_bot >= scroll_top and line_top - scroll_top <= area_h:
                draw_mixed_text(image, line, font, (10, int(line_top - scroll_top)))
            y = line_bot

        # Advance scroll
        if speed > 0 and scroll_top < max_scroll:
            new_top = min(scroll_top + speed, max_scroll)
            with self.state.lock:
                self.state.scroll_top = new_top
