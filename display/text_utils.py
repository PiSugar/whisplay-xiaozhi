"""
Text and image utilities for LCD rendering.
Adapted from whisplay-chatbot python/utils.py.
"""

import os
import unicodedata
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import cairosvg
except ImportError:
    cairosvg = None


ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
EMOJI_SVG_DIR = os.path.join(ASSETS_DIR, "emoji_svg")


# ==================== Color Utils ====================
def hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    h = hex_color.lstrip("#")
    if len(h) not in (6, 8) or not all(c in "0123456789abcdefABCDEF" for c in h):
        return None
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def luminance(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


# ==================== Image Utils ====================
def image_to_rgb565(image: Image.Image, width: int, height: int) -> list:
    image = image.convert("RGB")
    image.thumbnail((width, height), Image.LANCZOS)
    bg = Image.new("RGB", (width, height), (0, 0, 0))
    x = (width - image.width) // 2
    y = (height - image.height) // 2
    bg.paste(image, (x, y))
    arr = np.array(bg)
    r = (arr[:, :, 0] >> 3).astype(np.uint16)
    g = (arr[:, :, 1] >> 2).astype(np.uint16)
    b = (arr[:, :, 2] >> 3).astype(np.uint16)
    rgb565 = (r << 11) | (g << 5) | b
    high = (rgb565 >> 8).astype(np.uint8)
    low = (rgb565 & 0xFF).astype(np.uint8)
    return np.dstack((high, low)).flatten().tolist()


# ==================== Emoji Utils ====================
def _emoji_filename(char: str) -> str:
    return "-".join(f"{ord(c):x}" for c in char) + ".svg"


def _is_emoji(char: str) -> bool:
    return unicodedata.category(char) in ("So", "Sk") or ord(char) > 0x1F000


def get_emoji_image(char: str, size: int) -> Image.Image | None:
    if cairosvg is None:
        return None
    path = os.path.join(EMOJI_SVG_DIR, _emoji_filename(char))
    if not os.path.exists(path):
        return None
    try:
        png_bytes = cairosvg.svg2png(url=path, output_width=size, output_height=size)
        return Image.open(BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return None


# ==================== Text Utils ====================
_char_size_cache: dict = {}
_line_image_cache: dict = {}


def get_char_size(font: ImageFont.FreeTypeFont, char: str) -> tuple[int, int]:
    key = (font.getname(), font.size, char)
    if key in _char_size_cache:
        return _char_size_cache[key]
    if _is_emoji(char):
        img = get_emoji_image(char, size=font.size)
        if img:
            _char_size_cache[key] = (img.width, img.height)
            return img.width, img.height
    bbox = font.getbbox(char)
    result = (bbox[2] - bbox[0], bbox[3] - bbox[1])
    _char_size_cache[key] = result
    return result


def get_line_image(text: str, font: ImageFont.FreeTypeFont) -> Image.Image:
    key = (font.getname(), font.size, text)
    if key in _line_image_cache:
        return _line_image_cache[key]

    ascent, descent = font.getmetrics()
    baseline = ascent
    line_height = ascent + descent

    width = sum(get_char_size(font, c)[0] for c in text)
    img = Image.new("RGBA", (max(1, width), line_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x = 0
    for char in text:
        if _is_emoji(char):
            emoji_img = get_emoji_image(char, size=font.size)
            if emoji_img:
                img.paste(emoji_img, (x, baseline - emoji_img.height), emoji_img)
                x += emoji_img.width
        else:
            draw.text((x, 0), char, font=font, fill=(255, 255, 255))
            x += get_char_size(font, char)[0]

    _line_image_cache[key] = img
    return img


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines = []
    current = ""
    current_w = 0
    for char in text:
        cw = get_char_size(font, char)[0]
        if current_w + cw <= max_width:
            current += char
            current_w += cw
        else:
            if current:
                lines.append(current)
            current = char
            current_w = cw
    if current:
        lines.append(current)
    return lines


def draw_mixed_text(image: Image.Image, text: str, font: ImageFont.FreeTypeFont, xy: tuple[int, int]):
    line_img = get_line_image(text, font)
    image.paste(line_img, xy, line_img)


def clear_line_cache():
    _line_image_cache.clear()
