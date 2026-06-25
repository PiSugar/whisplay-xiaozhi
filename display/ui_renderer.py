"""
UI renderer — renders status/emoji/text/battery to the Whisplay HAT LCD.

Runs in a background thread at ~30 FPS, directly writing RGB565 to the LCD via SPI.
Adapted from whisplay-chatbot python/chatbot-ui.py (RenderThread).
"""

import os
import re
import time
import threading
import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

import config
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
_TOOL_TAG_RE = re.compile(
    r"[%％﹪]\s*([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)",
    re.IGNORECASE,
)
_TOOL_TAG_BG = (8, 42, 112)
_TOOL_TAG_FG = (255, 255, 255)
_TOOL_TAG_COUNT_FG = (122, 205, 255)
_TOOL_TAG_MARGIN_Y = 2


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
        self.scroll_speed: float = max(0.0, config.DISPLAY_SCROLL_SPEED)
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
        self._tool_tag_font = None
        self._line_height = 0
        self._wifi_source_icon_cache: dict[str, Image.Image | None] = {}
        self._wifi_scaled_icon_cache: dict[tuple[str, int, float], Image.Image | None] = {}

        if resolved:
            self._text_font = ImageFont.truetype(resolved, 20)
            self._status_font = ImageFont.truetype(resolved, 20)
            self._emoji_font = ImageFont.truetype(resolved, 40)
            self._battery_font = ImageFont.truetype(resolved, 13)
            self._tool_tag_font = ImageFont.truetype(resolved, 17)
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
        lines = self._build_text_lines(text, font, W - 20)
        content_h = sum(self._line_item_height(line, lh) for line in lines) + lh
        max_scroll = max(0, content_h - area_h)

        scroll_top = snap["scroll_top"]
        speed = snap["scroll_speed"]

        # Render visible lines
        y = 0
        line_top = 0
        for line in lines:
            item_h = self._line_item_height(line, lh)
            line_bot = line_top + item_h
            if line_bot >= scroll_top and line_top - scroll_top <= area_h:
                y = int(line_top - scroll_top)
                if isinstance(line, dict) and line.get("type") == "tool_tag":
                    self._draw_tool_tag(
                        image,
                        line["label"],
                        int(line.get("count", 1)),
                        font,
                        10,
                        y,
                        W - 20,
                        item_h,
                    )
                else:
                    draw_mixed_text(image, str(line), font, (10, y))
            line_top = line_bot

        # Advance scroll
        if speed > 0 and scroll_top < max_scroll:
            new_top = min(scroll_top + speed, max_scroll)
            with self.state.lock:
                self.state.scroll_top = new_top

    def _build_text_lines(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
        lines = []
        pending_tool_name = ""
        pending_tool_count = 0

        def flush_tool_tag():
            nonlocal pending_tool_name, pending_tool_count
            if not pending_tool_name or pending_tool_count <= 0:
                return
            lines.append({"type": "tool_tag", "label": pending_tool_name, "count": pending_tool_count})
            pending_tool_name = ""
            pending_tool_count = 0

        def append_tool_tag(name: str):
            nonlocal pending_tool_name, pending_tool_count
            if pending_tool_name and pending_tool_name != name:
                flush_tool_tag()
            pending_tool_name = name
            pending_tool_count += 1

        def append_text(value: str):
            parts = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for i, raw_line in enumerate(parts):
                if raw_line:
                    lines.extend(wrap_text(raw_line, font, max_width))
                elif lines and 0 < i < len(parts) - 1:
                    lines.append("")

        def consume_tail_after_marker(value: str, tool_name: str = "") -> tuple[str, int]:
            tail = value.lstrip(" \t:-—,，.。…")
            if not tail:
                return "", 0

            def is_tool_arg_token(token: str, current_tool: str = "") -> bool:
                if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", token):
                    return True
                if current_tool.lower() == "local_command":
                    return False
                if len(token) <= 8 and not re.search(r"[。！？；，,.!?;]", token):
                    return True
                return False

            extra_count = 0
            consumed_current_arg = False
            while tail:
                parts = tail.split(None, 1)
                first = parts[0]
                rest = parts[1].lstrip(" \t:-—,，.。…") if len(parts) > 1 else ""

                if tool_name and first.lower() == tool_name.lower():
                    extra_count += 1
                    tail = rest
                    parts = tail.split(None, 1)
                    if parts and is_tool_arg_token(parts[0], tool_name):
                        tail = parts[1].lstrip(" \t:-—,，.。…") if len(parts) > 1 else ""
                    continue

                if not consumed_current_arg and is_tool_arg_token(first, tool_name):
                    consumed_current_arg = True
                    tail = rest
                    continue

                return tail, extra_count

            return "", extra_count

        for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            matches = list(_TOOL_TAG_RE.finditer(raw_line))
            if not matches:
                flush_tool_tag()
                append_text(raw_line)
                continue

            before = raw_line[:matches[0].start()]
            if before.strip():
                flush_tool_tag()
                append_text(before)

            cursor = matches[0].start()
            for match in matches:
                between = raw_line[cursor:match.start()]
                visible_between, extra_count = consume_tail_after_marker(
                    between,
                    pending_tool_name if pending_tool_name else "",
                )
                for _ in range(extra_count):
                    append_tool_tag(pending_tool_name)
                if visible_between.strip():
                    flush_tool_tag()
                    append_text(visible_between)
                append_tool_tag(match.group(1))
                cursor = match.end()

            tail, extra_count = consume_tail_after_marker(raw_line[cursor:], pending_tool_name)
            for _ in range(extra_count):
                append_tool_tag(pending_tool_name)
            if tail.strip():
                flush_tool_tag()
                append_text(tail)

        flush_tool_tag()
        return lines

    def _line_item_height(self, line, line_height: int) -> int:
        if isinstance(line, dict) and line.get("type") == "tool_tag":
            return line_height + _TOOL_TAG_MARGIN_Y * 2
        return line_height

    def _draw_tool_tag(
        self,
        image: Image.Image,
        label: str,
        count: int,
        font: ImageFont.FreeTypeFont,
        x: int,
        y: int,
        max_width: int,
        line_height: int,
    ):
        draw = ImageDraw.Draw(image)
        tag_font = self._tool_tag_font or font
        bbox = tag_font.getbbox(label)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        count_text = f"x{count}" if count > 1 else ""
        count_bbox = tag_font.getbbox(count_text) if count_text else (0, 0, 0, 0)
        count_w = count_bbox[2] - count_bbox[0]
        count_h = count_bbox[3] - count_bbox[1]
        count_gap = 7 if count_text else 0
        pad_x = 10
        tag_w = min(max_width, text_w + count_gap + count_w + pad_x * 2)
        inner_h = max(1, line_height - _TOOL_TAG_MARGIN_Y * 2)
        tag_h = min(max(12, inner_h - 2), 22)
        tag_y = y + _TOOL_TAG_MARGIN_Y + max(0, (inner_h - tag_h) // 2)
        draw.rounded_rectangle(
            [x, tag_y, x + tag_w, tag_y + tag_h],
            radius=6,
            fill=_TOOL_TAG_BG,
        )
        content_w = text_w + count_gap + count_w
        text_x = x + (tag_w - content_w) // 2
        text_y = tag_y + (tag_h - text_h) // 2 - bbox[1]
        draw.text((text_x, text_y), label, font=tag_font, fill=_TOOL_TAG_FG)
        if count_text:
            count_x = text_x + text_w + count_gap
            draw.text((count_x, text_y), count_text, font=tag_font, fill=_TOOL_TAG_COUNT_FG)
