"""Watercolor voice orb ported from whisplay-chatgpt."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# Shader-space palette recovered from the saved ChatGPT advanced-bloop bundle.
COLOR_MAIN = np.asarray((0xDC, 0xF7, 0xFF), dtype=np.float32) / 255.0
COLOR_LOW = np.asarray((0x01, 0x81, 0xFE), dtype=np.float32) / 255.0
COLOR_MID = np.asarray((0xA4, 0xEF, 0xFF), dtype=np.float32) / 255.0
COLOR_HIGH = np.asarray((0xFF, 0xFD, 0xEF), dtype=np.float32) / 255.0
COLOR_TRANSITION = np.asarray((0x33, 0xA0, 0xFD), dtype=np.float32) / 255.0
# Efficient-tier calibration is deliberately separate from the palette. It
# compensates nearest FBM sampling while preserving a small population of
# true COLOR_LOW wells.
COARSE_PIGMENT_LIFT = 0.024
COARSE_MIDDLE_SEEP = 0.035
ORB_VERTICAL_OFFSET = -10
CAPTION_HORIZONTAL_PADDING = 12


def _smoothstep(edge0, edge1, value):
    width = np.asarray(edge1) - np.asarray(edge0)
    width = np.where(np.abs(width) < 1e-6, 1e-6, width)
    amount = np.clip((value - edge0) / width, 0.0, 1.0)
    return amount * amount * (3.0 - 2.0 * amount)


def _hash(x, y):
    return np.mod(np.sin(x * 12.9898 + y * 4.1414) * 43758.5453, 1.0)


def _noise(x, y):
    """Vectorised smooth value noise, equivalent to the shader's 2D noise."""
    ix = np.floor(x)
    iy = np.floor(y)
    ux = x - ix
    uy = y - iy
    ux = ux * ux * (3.0 - 2.0 * ux)
    uy = uy * uy * (3.0 - 2.0 * uy)
    a = _hash(ix, iy)
    b = _hash(ix + 1.0, iy)
    c = _hash(ix, iy + 1.0)
    d = _hash(ix + 1.0, iy + 1.0)
    return ((a * (1.0 - ux) + b * ux) * (1.0 - uy) + (c * (1.0 - ux) + d * ux) * uy) ** 2


def _scalar_hash(value: float) -> float:
    return math.sin(value * 12.9898 + 78.233) * 43758.5453 % 1.0


def _smooth_random(value: float, seed: float) -> float:
    """Non-periodic scalar value noise in the -1..1 range."""
    index = math.floor(value)
    fraction = value - index
    fraction = fraction * fraction * (3.0 - 2.0 * fraction)
    first = _scalar_hash(index + seed * 17.17)
    second = _scalar_hash(index + 1.0 + seed * 17.17)
    return (first * (1.0 - fraction) + second * fraction) * 2.0 - 1.0


def _noise3(x, y, z: float, seed: float):
    """CPU-friendly temporal slices approximating the shader's 3D cnoise."""
    index = math.floor(z)
    fraction = z - index
    fraction = fraction * fraction * (3.0 - 2.0 * fraction)

    def sample_slice(slice_index: int):
        offset_x = _scalar_hash(slice_index + seed * 11.31) * 97.0
        offset_y = _scalar_hash(slice_index + seed * 23.73) * 113.0
        return _noise(x + offset_x, y + offset_y)

    first = sample_slice(index)
    second = sample_slice(index + 1)
    return first * (1.0 - fraction) + second * fraction


def _fbm(x, y):
    """Four-octave rotating FBM matching the structure of OpenAI's shader."""
    value = np.zeros_like(x)
    amplitude = 0.5
    cosine = math.cos(0.5)
    sine = math.sin(0.5)
    for _ in range(4):
        value += amplitude * _noise(x, y)
        rotated_x = cosine * x + sine * y
        rotated_y = -sine * x + cosine * y
        x = rotated_x * 2.0 + 100.0
        y = rotated_y * 2.0 + 100.0
        amplitude *= 0.5
    return value


def _linear_burn(base, blend):
    return np.maximum(base + blend - 1.0, 0.0)


def _burn_with_opacity(base, blend, opacity):
    opacity = opacity[..., None]
    return _linear_burn(base, blend) * opacity + base * (1.0 - opacity)


def _directional_bristles(layer, grain, dx: int, dy: int):
    """Pull broken pigment-edge seeds into short one-way brush filaments."""
    ahead = np.roll(layer, shift=(-dy, -dx), axis=(0, 1))
    seed = np.clip(layer - ahead, 0.0, 1.0)
    seed *= _smoothstep(0.07, 0.27, np.abs(grain))
    # Max-compositing keeps each seed as a distinct taper instead of merging
    # neighbouring hairs into a wide outline.
    trail = seed * 0.72
    for distance, opacity in ((1, 0.62), (2, 0.43), (3, 0.27), (4, 0.14)):
        shifted = np.roll(
            seed,
            shift=(dy * distance, dx * distance),
            axis=(0, 1),
        )
        np.maximum(trail, shifted * opacity, out=trail)
    return np.clip(trail * 1.65, 0.0, 1.0)


class OrbRenderer:
    """CPU adaptation of ChatGPT's noise-textured WebGL voice bloop."""

    def __init__(
        self,
        width: int = 240,
        height: int = 280,
        diameter: int = 168,
        render_scale: float = 0.37,
        smooth_fbm: bool = False,
        temporal_3d: bool = False,
        audio_reactivity: float = 3.2,
        idle_speed: float = 0.70,
        speech_motion: float = 3.6,
        caption_font_path: str = "",
        caption_font_size: int = 15,
        caption_offset_x: int = 3,
    ):
        self.width = width
        self.height = height
        self.diameter = diameter
        self.smooth_fbm = smooth_fbm
        self.temporal_3d = temporal_3d
        self.audio_reactivity = max(0.5, min(5.0, audio_reactivity))
        self.idle_speed = max(0.05, min(1.0, idle_speed))
        self.speech_motion = max(0.5, min(5.0, speech_motion))
        # Do not restart the deterministic flow at the same pose on every
        # launch. This changes only the visual clock origin; audio travel
        # remains continuous and is still shared with clip playback.
        self.phase_offset = _scalar_hash(time.monotonic_ns() * 1e-6) * 71.0
        # The original runs this shader on the GPU. A 37%-resolution field is
        # the highest measured tier with enough headroom for 8 FPS on Zero 2 W.
        self.render_scale = max(0.2, min(1.0, render_scale))
        # The Pi 5 auto profile renders at 60%; this is the quality tier, not
        # the old experimental 70% threshold left behind by earlier tuning.
        self.high_quality = self.smooth_fbm and self.render_scale >= 0.58
        self.render_width = max(1, round(width * self.render_scale))
        self.render_height = max(1, round(height * self.render_scale))
        render_radius = max(1.0, diameter * self.render_scale / 2.0)

        yy, xx = np.mgrid[0:self.render_height, 0:self.render_width].astype(np.float32)
        render_center_x = (self.render_width - 1) / 2.0
        render_center_y = (
            (self.render_height - 1) / 2.0
            + ORB_VERTICAL_OFFSET * self.render_scale
        )
        self.x = (xx - render_center_x) / render_radius
        self.y = (yy - render_center_y) / render_radius
        self.radius = np.sqrt(self.x * self.x + self.y * self.y)

        # The fluid may be low resolution, but the silhouette is always built
        # at native LCD resolution and never reacts to audio.
        full_y, full_x = np.mgrid[0:height, 0:width].astype(np.float32)
        full_radius = diameter / 2.0
        center_x = (width - 1) / 2.0
        center_y = (height - 1) / 2.0 + ORB_VERTICAL_OFFSET
        self.full_distance = np.sqrt(
            (full_x - center_x) ** 2 + (full_y - center_y) ** 2
        )
        # Estimate true pixel coverage at 4x4 subpixel positions. This keeps
        # the silhouette crisp while removing diagonal stair-steps after the
        # final RGB565 conversion. The mask is generated only once at startup.
        coverage = np.zeros((height, width), dtype=np.float32)
        offsets = (np.arange(4, dtype=np.float32) + 0.5) / 4.0 - 0.5
        radius_squared = full_radius * full_radius
        for offset_y in offsets:
            dy_squared = (full_y + offset_y - center_y) ** 2
            for offset_x in offsets:
                distance_squared = (
                    (full_x + offset_x - center_x) ** 2 + dy_squared
                )
                coverage += distance_squared <= radius_squared
        self.circle_mask = (coverage / 16.0)[..., None]
        covered_y, covered_x = np.where(coverage > 0.0)
        self.circle_bbox = (
            int(covered_x.min()),
            int(covered_y.min()),
            int(covered_x.max()) + 1,
            int(covered_y.max()) + 1,
        )
        # Text remains at its established location while only the orb moves.
        unshifted_bottom = math.ceil((height - 1) / 2.0 + full_radius)
        self.status_top = min(height - 2 * 11 - 4, unshifted_bottom + 8)
        self.status_font = ImageFont.load_default()
        self.caption_font_size = max(10, min(24, int(caption_font_size)))
        self.caption_offset_x = max(-20, min(20, int(caption_offset_x)))
        self.caption_fonts = self._load_caption_fonts(
            caption_font_path, self.caption_font_size
        )
        self.caption_font = self.caption_fonts[0]
        self._missing_glyphs = {
            id(font): bytes(font.getmask("\U0010ffff"))
            for font in self.caption_fonts
        }
        self._caption_font_cache: dict[str, object] = {}
        self._caption_width_cache: dict[str, float] = {}
        self._caption_pages_cache: dict[str, list[tuple[int, str]]] = {}
        self._caption_layer_cache: dict[str, Image.Image] = {}
        self._status_layer_cache: dict[str, Image.Image] = {}

        # WebGL evaluates FBM in parallel. On the Pi CPU, precompute the same
        # four-octave field once and bilinearly sample it at moving coordinates.
        self.fbm_size = 128
        self.fbm_period = 16.0
        grid = np.arange(self.fbm_size, dtype=np.float32) * self.fbm_period / self.fbm_size
        table_y, table_x = np.meshgrid(grid, grid)
        self.fbm_table = _fbm(table_x, table_y).astype(np.float32)

    @staticmethod
    def _load_caption_fonts(configured_path: str = "", font_size: int = 15) -> list:
        # Keep multiple fonts so mixed-language captions can fall back per
        # character. Some Pi images ship Droid Sans Fallback with CJK glyphs
        # but no usable Latin glyphs.
        paths = (
            (configured_path,) if configured_path else ()
        ) + (
            "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        )
        fonts = []
        loaded_paths: set[str] = set()
        for path in paths:
            if not path or path in loaded_paths:
                continue
            try:
                fonts.append(ImageFont.truetype(path, font_size))
                loaded_paths.add(path)
            except OSError:
                continue
        if not fonts:
            fonts.append(ImageFont.load_default())
        return fonts

    def _caption_font_for(self, character: str):
        cached = self._caption_font_cache.get(character)
        if cached is not None:
            return cached
        if character.isspace():
            selected = self.caption_fonts[0]
        else:
            selected = self.caption_fonts[0]
            for font in self.caption_fonts:
                if bytes(font.getmask(character)) != self._missing_glyphs[id(font)]:
                    selected = font
                    break
        self._caption_font_cache[character] = selected
        return selected

    def _caption_character_width(self, character: str) -> float:
        cached = self._caption_width_cache.get(character)
        if cached is None:
            cached = self._caption_font_for(character).getlength(character)
            self._caption_width_cache[character] = cached
        return cached

    def _caption_text_width(self, text: str) -> float:
        return sum(
            self._caption_character_width(character)
            for character in text
        )

    @staticmethod
    def _remember(cache: dict, key, value, max_items: int = 32):
        if len(cache) >= max_items:
            cache.clear()
        cache[key] = value
        return value

    def _caption_pages(self, text: str) -> list[tuple[int, str]]:
        """Lay out caption pages and their character start positions."""
        max_width = self.width - CAPTION_HORIZONTAL_PADDING * 2
        normalized = " ".join(text.replace("\r", "").split())
        if not normalized:
            return []
        cached = self._caption_pages_cache.get(normalized)
        if cached is not None:
            return cached
        pages: list[tuple[int, str]] = []
        page = ""
        page_width = 0.0
        page_start = 0
        cursor = 0
        space_width = self._caption_character_width(" ")
        for word_index, word in enumerate(normalized.split(" ")):
            word_start = cursor + (1 if word_index else 0)
            cursor = word_start + len(word)
            word_width = self._caption_text_width(word)
            candidate_width = word_width if not page else page_width + space_width + word_width
            if candidate_width <= max_width:
                if not page:
                    page_start = word_start
                    page = word
                    page_width = word_width
                else:
                    page = f"{page} {word}"
                    page_width = candidate_width
                continue
            if word_width <= max_width:
                # The whole word moves to the next page.
                if page:
                    pages.append((page_start, page))
                page = word
                page_width = word_width
                page_start = word_start
                continue
            # CJK text and exceptionally long words may contain no spaces.
            # Split only these unavoidable over-width tokens by character.
            if page:
                pages.append((page_start, page))
            page = ""
            page_width = 0.0
            page_start = word_start
            for offset, character in enumerate(word):
                width = self._caption_character_width(character)
                if page and page_width + width > max_width:
                    pages.append((page_start, page))
                    page = character
                    page_width = width
                    page_start = word_start + offset
                else:
                    page += character
                    page_width += width
        if page:
            pages.append((page_start, page))
        return self._remember(self._caption_pages_cache, normalized, pages)

    def _caption_line(self, draw: ImageDraw.ImageDraw | None, text: str) -> str:
        """Return the newest laid-out page for tests and static callers."""
        pages = self._caption_pages(text)
        return pages[-1][1] if pages else ""

    def caption_pages(self, text: str) -> list[str]:
        """Return display-ready caption pages in reading order."""
        return [page for _, page in self._caption_pages(text)]

    def _caption_page_for_playback(
        self,
        text: str,
        position: int,
        complete: bool,
    ) -> str:
        if position <= 0:
            return ""
        pages = self._caption_pages(text)
        finalized = pages if complete else pages[:-1]
        selected = ""
        for start, page in finalized:
            if start > position:
                break
            selected = page
        return selected

    def _status_layer(self, status_text: str) -> Image.Image:
        cached = self._status_layer_cache.get(status_text)
        if cached is not None:
            return cached
        lines = status_text.splitlines()
        line_height = 11
        layer = Image.new("RGBA", (self.width, max(1, len(lines) * line_height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for index, line in enumerate(lines):
            box = draw.textbbox((0, 0), line, font=self.status_font)
            text_width = box[2] - box[0]
            draw.text(
                ((self.width - text_width) // 2, index * line_height),
                line,
                font=self.status_font,
                fill=(158, 184, 196, 255),
            )
        return self._remember(self._status_layer_cache, status_text, layer, 8)

    def _caption_layer(self, line: str) -> Image.Image:
        cached = self._caption_layer_cache.get(line)
        if cached is not None:
            return cached
        padding_y = 2
        text_top = 0
        text_bottom = 1
        for character in line:
            font = self._caption_font_for(character)
            box = font.getbbox(character)
            text_top = min(text_top, box[1])
            text_bottom = max(text_bottom, box[3])
        layer_height = max(1, text_bottom - text_top + padding_y * 2)
        layer = Image.new("RGBA", (self.width, layer_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        text_width = self._caption_text_width(line)
        left = (self.width - text_width) / 2.0 + self.caption_offset_x
        top = padding_y - text_top
        for character in line:
            font = self._caption_font_for(character)
            draw.text(
                (round(left), top),
                character,
                font=font,
                fill=(190, 211, 220, 255),
            )
            left += self._caption_character_width(character)
        return self._remember(self._caption_layer_cache, line, layer, 24)

    def _sample_fbm(self, x, y):
        scale = self.fbm_size / self.fbm_period
        px = np.mod(x * scale, self.fbm_size)
        py = np.mod(y * scale, self.fbm_size)
        floor_x = np.floor(px)
        floor_y = np.floor(py)
        x0 = floor_x.astype(np.int16) % self.fbm_size
        y0 = floor_y.astype(np.int16) % self.fbm_size
        if not self.smooth_fbm:
            return self.fbm_table[y0, x0]

        # Pi 5 quality mode: use the same Hermite interpolation curve as value
        # noise. Plain bilinear weights average too much of the FBM's highs and
        # lows, flattening the colour sheets; Hermite weights retain distinct
        # basins while keeping derivatives continuous at cell boundaries.
        x1 = (x0 + 1) % self.fbm_size
        y1 = (y0 + 1) % self.fbm_size
        fx = px - floor_x
        fy = py - floor_y
        fx = fx * fx * (3.0 - 2.0 * fx)
        fy = fy * fy * (3.0 - 2.0 * fy)
        top = self.fbm_table[y0, x0] * (1.0 - fx) + self.fbm_table[y0, x1] * fx
        bottom = self.fbm_table[y1, x0] * (1.0 - fx) + self.fbm_table[y1, x1] * fx
        return top * (1.0 - fy) + bottom * fy

    def _watercolor(
        self,
        phase: float,
        level: float,
        bands: Sequence[float],
        cumulative: Sequence[float],
    ) -> np.ndarray:
        audio = np.clip(np.asarray(tuple(bands)[:4], dtype=np.float32), 0.0, 1.0)
        if audio.size < 4:
            audio = np.pad(audio, (0, 4 - audio.size))
        travelled = np.asarray(tuple(cumulative)[:4], dtype=np.float32)
        if travelled.size < 4:
            travelled = np.pad(travelled, (0, 4 - travelled.size))
        # OpenAI drives each fluid layer with a different cumulative audio
        # channel. Our analyser integrates in seconds, so scale the travel to
        # the range used by the browser animation without coupling the layers.
        travelled *= self.audio_reactivity * self.speech_motion
        reactive_audio = np.clip(audio * self.audio_reactivity * 1.35, 0.0, 1.55)
        # The browser bloop drives both local pigment boundaries and the broad
        # temporal domains. Use RMS plus the four bands as a smooth voice
        # envelope; this keeps idle motion intact while making assistant speech
        # visibly accelerate and fold the whole internal field.
        voice_strength = min(
            1.6,
            level * 1.15 + float(np.mean(reactive_audio)) * 0.72,
        )

        # Keep clock motion independent from sound. OpenAI applies cumulative
        # audio later to individual fields; adding it here moves every layer in
        # the same direction and makes speech look like one global tilt.
        # Cumulative mean-band travel acts as an additional continuous clock:
        # speech speeds up the watercolor, then leaves only a phase offset when
        # it stops. Because it never resets, transitions cannot flash.
        time_value = (
            (phase + self.phase_offset) * self.idle_speed
            + travelled[3] * 0.10
        )
        vertical_displacement = 0.01 * math.sin(math.tau / 4.0 * time_value)
        uv_x = self.x * 0.5 + 0.5
        uv_y = 1.0 - ((self.y - vertical_displacement) * 0.5 + 0.5)

        # Broad animated domain warp. The browser shader uses two 3D classic
        # noise samples; moving 2D samples preserve the same visual behaviour.
        if self.temporal_3d:
            noise_x = _noise3(
                uv_x,
                uv_y + 74.8572,
                time_value * 0.30 + travelled[0] * 0.045,
                1.0,
            )
            noise_y = _noise3(
                uv_x + 203.91282,
                uv_y + 10.0,
                time_value * 0.30 + travelled[2] * 0.042,
                2.0,
            )
        else:
            noise_x = _noise(
                uv_x + time_value * 0.09, uv_y + 74.8572 + time_value * 0.04
            )
            noise_y = _noise(
                uv_x + 203.91282 - time_value * 0.05,
                uv_y + 10.0 + time_value * 0.07,
            )
        uv_x = uv_x + (noise_x - 0.5) * 2.0 * 0.19
        uv_y = uv_y + (noise_y - 0.5) * 0.19

        if self.temporal_3d:
            watercolor_a = _noise3(
                uv_x * 18.0 + 344.91282,
                uv_y * 18.0,
                time_value * 0.30 + travelled[1] * 0.040,
                3.0,
            )
            watercolor_b = _noise3(
                uv_x * 39.6 + 723.937,
                uv_y * 39.6,
                time_value * 0.40 - travelled[3] * 0.035,
                4.0,
            )
        else:
            watercolor_a = _noise(
                uv_x * 18.0 + 344.91282 + time_value * 0.3, uv_y * 18.0
            )
            watercolor_b = _noise(
                uv_x * 39.6 + 723.937, uv_y * 39.6 + time_value * 0.4
            )
        watercolor = watercolor_a + 0.5 * watercolor_b
        watercolor_warp = 0.01 + voice_strength * 0.004
        uv_x = uv_x + watercolor * watercolor_warp
        uv_y = uv_y + watercolor * watercolor_warp - 0.09

        # Procedural replacement for the bundled 512px watercolor texture.
        # Translate the texture coordinates monotonically. The former
        # sin(cumulative) cross-fades repeatedly retraced the same path and
        # made the watercolor appear to turn back after every syllable.
        texture_a = _noise(
            (uv_x + time_value * 0.011 + travelled[0] * 0.012) * 9.0 + 63.861,
            (uv_y + time_value * 0.007 + travelled[1] * 0.009) * 9.0 + 368.937,
        )
        texture_b = _noise(
            (uv_x - time_value * 0.009 - travelled[2] * 0.010) * 9.0 + 272.861,
            (1.0 - uv_y + time_value * 0.006 + travelled[3] * 0.011) * 9.0
            + 829.937,
        )
        # Each sheet uses a fixed mixture of independently advecting fields;
        # motion comes from sampling position, never an oscillating blend.
        texture_displacement0 = (
            texture_a * 0.64 + texture_b * 0.36 - 0.5
        ) * 0.08
        texture_displacement1 = (
            texture_b * 0.56 + watercolor_a * 0.44 - 0.5
        ) * 0.08
        texture_displacement3 = (
            watercolor_b * 0.58 + texture_a * 0.42 - 0.5
        ) * 0.08
        uv_x = uv_x + texture_displacement0
        uv_y = uv_y + texture_displacement0

        st_x = uv_x * 1.25
        st_y = uv_y * 1.25
        q_x = self._sample_fbm(
            st_x * 0.5 + 0.075 * (time_value + travelled[3] * 0.175),
            st_y * 0.5 + 0.075 * (time_value + travelled[3] * 0.175),
        )
        q_y = self._sample_fbm(
            st_x * 0.5 + 0.075 * (time_value + travelled[0] * 0.136),
            st_y * 0.5 + 0.075 * (time_value + travelled[0] * 0.136),
        )
        r_x = self._sample_fbm(
            st_x + q_x + 0.3 + 0.15 * (time_value + travelled[1] * 0.234),
            st_y + q_y + 9.2 + 0.15 * (time_value + travelled[1] * 0.234),
        )
        r_y = self._sample_fbm(
            st_x + q_x + 8.3 + 0.126 * (time_value + travelled[2] * 0.165),
            st_y + q_y + 0.8 + 0.126 * (time_value + travelled[2] * 0.165),
        )
        field = self._sample_fbm(st_x + r_x - q_x, st_y + r_y - q_y)
        full_fbm = np.power(np.clip((field + 0.6 * field * field + 0.7 * field + 0.5) * 0.5, 0.0, None), 0.55)

        sin_offsets = np.asarray(
            (travelled[0] * 0.15, -travelled[1] * 0.5, travelled[2] * 1.5),
            dtype=np.float32,
        )
        fbm_centered = full_fbm - 0.5
        flow_x = noise_x - 0.5
        flow_y = noise_y - 0.5
        # Our squared value noise is positive and skewed, unlike the signed
        # classic noise used by the browser shader. Differences between fields
        # with the same distribution give us inexpensive, genuinely
        # zero-centred signals without another full-screen noise evaluation.
        signed_flow = noise_x - noise_y
        signed_watercolor = watercolor_a - watercolor_b
        signed_texture = texture_a - texture_b
        signed_q = q_x - q_y
        signed_r = r_x - r_y
        speech_warp = 1.0 + voice_strength * 1.05
        if self.high_quality:
            # The browser shader's decisive squeeze comes from the domain
            # `fbm(st + r - q)`, not from rotating the finished layers. Turn
            # that domain into a local pressure potential and use its spatial
            # gradient as a convergent/divergent vector field. Every basin has
            # its own direction, so pressure arrives from all around the orb.
            pressure = full_fbm + signed_q * 0.42 + signed_r * 0.30
            pressure_x = (
                np.roll(pressure, -1, axis=1)
                - np.roll(pressure, 1, axis=1)
            ) * 0.5
            pressure_y = (
                np.roll(pressure, -1, axis=0)
                - np.roll(pressure, 1, axis=0)
            ) * 0.5
            np.clip(pressure_x, -0.22, 0.22, out=pressure_x)
            np.clip(pressure_y, -0.22, 0.22, out=pressure_y)
            # A finite difference on the reduced CPU grid is much smaller
            # than the continuously sampled WebGL domain derivative. Preserve
            # the calm idle field but amplify assistant-driven convergence so
            # opposing basins visibly press into one another on the LCD.
            pressure_drive = 0.24 + voice_strength * 3.20
        else:
            pressure_x = 0.0
            pressure_y = 0.0
            pressure_drive = 0.0
        # Advect each pigment sheet through a different local vector field.
        # No coordinate is rotated around the orb centre: speech therefore
        # creates internal folds and counter-flow instead of turning the whole
        # picture as one rigid disc. All inputs below are zero-centred and
        # spatially varying, so there is no hidden global translation either.
        local_drive = 0.20 + voice_strength * 0.20
        layer1_x = uv_x + (
            signed_q * 0.24 + signed_texture * 0.10 - signed_flow * 0.07
        ) * local_drive - pressure_x * pressure_drive
        layer1_y = uv_y + (
            signed_r * 0.22 - signed_watercolor * 0.08 + signed_flow * 0.06
        ) * local_drive - pressure_y * pressure_drive
        layer2_x = uv_x + (
            -signed_r * 0.22 + signed_watercolor * 0.11 + signed_q * 0.06
        ) * local_drive + (
            pressure_x * 0.68 + pressure_y * 0.26
        ) * pressure_drive
        layer2_y_base = uv_y + (
            signed_q * 0.20 + signed_texture * 0.10 - signed_flow * 0.05
        ) * local_drive + (
            pressure_y * 0.68 - pressure_x * 0.26
        ) * pressure_drive
        layer3_x = uv_x + (
            signed_texture * 0.20 - signed_q * 0.13 + signed_r * 0.06
        ) * local_drive + (
            pressure_y * 0.58 - pressure_x * 0.34
        ) * pressure_drive
        layer3_y_base = uv_y + (
            -signed_watercolor * 0.18 - signed_r * 0.12 + signed_flow * 0.09
        ) * local_drive + (
            pressure_x * 0.58 + pressure_y * 0.34
        ) * pressure_drive
        base_y = (
            layer1_y
            + fbm_centered * 1.05
            + texture_displacement0
            + flow_y * 0.20 * speech_warp
            + 0.025
        )
        layer1_noise = _noise(
            (layer1_x + fbm_centered * 1.20 + flow_x * 0.22 * speech_warp) * 2.0
            + sin_offsets[0] * 0.10,
            base_y * 2.0 + time_value * 0.5 + sin_offsets[0],
        ) * 2.0
        # Keep the browser shader's layered height-field structure, but avoid
        # exposing a single y=0.5 skeleton. Each sheet gets a different low
        # frequency threshold field assembled from noise already computed for
        # domain warping. Speech strengthens local folds rather than merely
        # tilting the whole sheet.
        threshold_reactivity = 1.0 + voice_strength * 0.65
        threshold1 = threshold_reactivity * (
            signed_q * 0.70
            + signed_r * 0.42
            + signed_watercolor * 0.17
            + signed_flow * 0.22
        )
        layer1 = _smoothstep(
            layer1_noise - 1.8,
            layer1_noise + 1.8,
            (base_y - 0.5 + threshold1)
            * (5.0 - reactive_audio[0] * 0.45)
            + 0.5,
        ) ** 0.8

        layer2_y = (
            layer2_y_base
            + fbm_centered * 0.78
            + texture_displacement1
            - flow_x * 0.18 * speech_warp
            + 0.025
        )
        layer2_noise = _noise(
            (layer2_x - fbm_centered * 0.72 + flow_y * 0.20 * speech_warp) * 4.0
            + sin_offsets[1] * 0.22
            + 293.0,
            layer2_y * 4.0 + time_value + sin_offsets[1] * 0.5,
        ) * 2.0
        threshold2 = threshold_reactivity * (
            signed_r * -0.62
            + signed_q * 0.33
            + signed_texture * 0.20
            - signed_flow * 0.18
        )
        layer2 = _smoothstep(
            layer2_noise - (0.9 + reactive_audio[1] * 0.70) * 1.5,
            layer2_noise + (0.9 + reactive_audio[1] * 1.30) * 1.5,
            (layer2_y - 0.6 + threshold2)
            * (5.0 - reactive_audio[1] * 1.45)
            + 0.5,
        ) ** 0.9

        layer3_y = (
            layer3_y_base
            + fbm_centered * 0.92
            + texture_displacement3
            + (flow_x - flow_y) * 0.15 * speech_warp
        )
        layer3_noise = _noise(
            (layer3_x + fbm_centered * 1.05 - flow_y * 0.18 * speech_warp) * 6.0
            + sin_offsets[2] * 0.12
            + 153.0,
            layer3_y * 6.0 + time_value * 1.2 + sin_offsets[2] * 0.8,
        ) * 2.0
        threshold3 = threshold_reactivity * (
            signed_q * -0.38
            + signed_r * -0.51
            + signed_watercolor * 0.14
            + signed_texture * 0.16
            + signed_flow * 0.20
        )
        layer3 = _smoothstep(
            layer3_noise - (1.05 + reactive_audio[2] * 0.25),
            layer3_noise + (1.05 + reactive_audio[2] * 0.40),
            (layer3_y - 0.9 + threshold3)
            * (6.0 - reactive_audio[2] * 1.2)
            + 0.5,
        )

        if self.smooth_fbm:
            # Bilinear FBM removes sampling blocks, but by itself can make the
            # watercolor layers look airbrushed. Reintroduce fine structure
            # only around each color boundary using fields already calculated
            # above, then tighten the transition without creating hard cells.
            edge1 = layer1 * (1.0 - layer1) * 4.0
            edge2 = layer2 * (1.0 - layer2) * 4.0
            edge3 = layer3 * (1.0 - layer3) * 4.0
            if self.high_quality:
                # Pi 5: stronger pigment breakup at the sheet boundaries.
                # The response is concentrated around the centre of each
                # transition so layer interiors stay smooth and colourful.
                edge1 *= 0.68 + edge1 * 0.32
                edge2 *= 0.70 + edge2 * 0.30
                edge3 *= 0.72 + edge3 * 0.28
                erosion1, erosion2, erosion3 = 0.30, 0.25, 0.21
            else:
                erosion1, erosion2, erosion3 = 0.18, 0.15, 0.12
            layer1 = np.clip(
                layer1 + (watercolor_a - watercolor_b) * erosion1 * edge1,
                0.0,
                1.0,
            )
            layer2 = np.clip(
                layer2 + (texture_a - texture_b) * erosion2 * edge2,
                0.0,
                1.0,
            )
            layer3 = np.clip(
                layer3 + (watercolor_b - texture_a) * erosion3 * edge3,
                0.0,
                1.0,
            )
            # Preserve broad, continuous colour ramps in high-quality mode.
            # Tight thresholds posterise the palette into just a few colours,
            # especially after RGB565 conversion on the display.
            if self.high_quality:
                layer1 = _smoothstep(0.03, 0.97, layer1)
                layer2 = _smoothstep(0.04, 0.96, layer2)
                layer3 = _smoothstep(0.04, 0.96, layer3)
            else:
                layer1 = _smoothstep(0.05, 0.95, layer1)
                layer2 = _smoothstep(0.05, 0.95, layer2)
                layer3 = _smoothstep(0.05, 0.95, layer3)

        # Both hardware tiers use exactly the same pigment compositor. Device
        # tuning may reduce field resolution and FBM interpolation, but must
        # never substitute linear-burn colours: that old Zero path clipped the
        # lower sheet into a saturated blue half-disc.
        layer1_rgb = layer1[..., None]
        layer2_rgb = layer2[..., None]
        layer3_rgb = layer3[..., None]
        # Nearest-sampled FBM on the efficient tier statistically produces
        # wider low-valued basins than the interpolated Pi 5 field. Compensate
        # that sampling bias without changing any palette endpoint.
        coarse_pigment_lift = COARSE_PIGMENT_LIFT if not self.smooth_fbm else 0.0
        coarse_middle_seep = COARSE_MIDDLE_SEEP if not self.smooth_fbm else 0.0
        # Quadratic pigment ramp: unlike a direct LOW -> MAIN mix, this gives
        # saturated cyan a broad footprint while retaining local deep-blue
        # wells. q/r modulation is available in both performance tiers.
        pigment1 = (
            layer1_rgb * 0.41
            + 0.44
            + coarse_pigment_lift
            + (signed_q * 0.09 + signed_r * 0.05)[..., None]
        )
        pigment1 = np.clip(pigment1, 0.28, 0.96)
        inverse_layer1 = 1.0 - pigment1
        rgb = (
            COLOR_LOW * inverse_layer1 * inverse_layer1
            + COLOR_TRANSITION * 2.0 * inverse_layer1 * pigment1
            + COLOR_MAIN * pigment1 * pigment1
        )

        middle = COLOR_MID * (1.0 - layer2_rgb) + COLOR_MAIN * layer2_rgb
        middle_amount = (
            (1.0 - layer1_rgb) * (0.115 + coarse_middle_seep)
            + layer1_rgb * 0.34
        )
        rgb = rgb * (1.0 - middle_amount) + middle * middle_amount

        high = COLOR_HIGH * (1.0 - layer3_rgb) + COLOR_MAIN * layer3_rgb
        high_amount = (
            layer1 * layer2 * 0.56
            + (1.0 - layer1) * layer3 * 0.055
        )[..., None]
        rgb = rgb * (1.0 - high_amount) + high * high_amount

        if not self.smooth_fbm:
            # Nearest FBM plus RGB565 quantisation leaves the efficient tier
            # about eight green levels below the Pi 5 output under identical
            # inputs. This small display-space calibration aligns perceived
            # cyan without changing the shared palette or layer proportions.
            rgb += np.asarray((0.0, 0.026, 0.004), dtype=np.float32)

        if self.high_quality:
            # The browser's watercolor texture contributes continuous tonal
            # variation inside each sheet, not only at its boundary. Recreate
            # that depth from already-advecting low-frequency fields. Blending
            # toward palette endpoints retains many intermediate colours and
            # avoids both flat fills and granular erosion.
            # Reuse signed_r as scratch storage: at 192x224 the Pi 5 is more
            # constrained by NumPy memory traffic than arithmetic. A single
            # continuous relief field gives the same internal colour depth
            # without allocating several full-frame blend masks.
            relief = signed_r
            relief += signed_q * 0.55
            relief += signed_flow * 0.24
            relief *= 3.0
            np.tanh(relief, out=relief)
            relief_light = np.clip(relief, 0.0, 1.0)[..., None]
            relief_dark = np.clip(-relief, 0.0, 1.0)[..., None] * 0.20
            rgb += relief_light * np.asarray(
                (0.090, 0.150, 0.150), dtype=np.float32
            )
            rgb = rgb * (1.0 - relief_dark) + COLOR_LOW * relief_dark
            # Signed high-frequency tint can subtract blue faster than red
            # and create grey/black specks after RGB565 quantisation. Use its
            # magnitude only as a restrained watercolor highlight; large-scale
            # light/dark variation still comes from the smooth relief above.
            np.abs(signed_watercolor, out=signed_watercolor)
            rgb += signed_watercolor[..., None] * np.asarray(
                (0.030, 0.065, 0.075), dtype=np.float32
            )

            # ChatGPT's watercolor has brush-end colour hairs rather than a
            # uniformly eroded outline. Select only the trailing side of each
            # pigment boundary and pull sparse seeds along three independent
            # flow directions. The fixed one-way offsets preserve the
            # continuous advection model and cannot oscillate backwards.
            bristle1 = _directional_bristles(
                layer1,
                signed_texture + signed_q * 0.35,
                1,
                -1,
            )[..., None]
            bristle2 = _directional_bristles(
                layer2,
                signed_flow - signed_texture * 0.50,
                -1,
                1,
            )[..., None]
            bristle3 = _directional_bristles(
                layer3,
                signed_q - signed_flow * 0.40,
                1,
                1,
            )[..., None]
            amount1 = bristle1 * 0.32
            amount2 = bristle2 * 0.24
            amount3 = bristle3 * 0.18
            rgb = rgb * (1.0 - amount1) + COLOR_LOW * amount1
            rgb = rgb * (1.0 - amount2) + COLOR_TRANSITION * amount2
            rgb = rgb * (1.0 - amount3) + COLOR_HIGH * amount3
            # Multiple relief/highlight layers otherwise pin the Pi 5 blue
            # channel at 255 even though the sampled reference averages near
            # 250. A tiny final display-space trim restores the warm-white and
            # pale-cyan separation while leaving Zero's shared palette intact.
            rgb -= np.asarray((0.002, 0.002, 0.035), dtype=np.float32)

        return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)

    def image(
        self,
        phase: float,
        level: float,
        peak: float = 0.0,
        bands: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
        cumulative: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
        visual_scale: float = 1.0,
        status_text: str = "",
        caption_text: str = "",
        caption_position: int | None = None,
        caption_complete: bool = True,
    ) -> Image.Image:
        level = max(0.0, min(1.0, level))
        frame = self._watercolor(phase, level, bands, cumulative)
        resampling = (
            Image.Resampling.BILINEAR
            if self.high_quality
            else Image.Resampling.BICUBIC
        )
        fluid = Image.fromarray(frame, "RGB").resize(
            (self.width, self.height), resampling
        )
        fluid_rgb = np.asarray(fluid, dtype=np.float32)
        visual_scale = max(1.0, min(1.18, float(visual_scale)))
        mask = self.circle_mask
        if visual_scale > 1.001:
            # User speech expands only the native-resolution silhouette. The
            # watercolor coordinates remain unchanged, revealing more of the
            # already-rendered field instead of zooming its pigment texture.
            radius = self.diameter * 0.5 * visual_scale
            mask = np.clip(radius + 0.5 - self.full_distance, 0.0, 1.0)[..., None]
        masked = np.clip(fluid_rgb * mask, 0, 255).astype(np.uint8)
        image = Image.fromarray(masked, "RGB")
        if status_text:
            layer = self._status_layer(status_text)
            image.paste(layer, (0, self.status_top), layer)
        elif caption_text:
            line = (
                self._caption_line(None, caption_text)
                if caption_position is None
                else self._caption_page_for_playback(
                    caption_text,
                    caption_position,
                    caption_complete,
                )
            )
            # Match the baseline area used by the second pause-status line
            # ("Click to continue") instead of hugging the display edge.
            if line:
                layer = self._caption_layer(line)
                image.paste(layer, (0, self.status_top), layer)
        return image

    def rgb565(
        self,
        phase: float,
        level: float,
        peak: float = 0.0,
        bands: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
        cumulative: Sequence[float] = (0.0, 0.0, 0.0, 0.0),
        visual_scale: float = 1.0,
        status_text: str = "",
        caption_text: str = "",
        caption_position: int | None = None,
        caption_complete: bool = True,
    ) -> bytes:
        rgb = np.asarray(
            self.image(
                phase,
                level,
                peak,
                bands,
                cumulative,
                visual_scale,
                status_text,
                caption_text,
                caption_position,
                caption_complete,
            ),
            dtype=np.uint16,
        )
        packed = (
            ((rgb[:, :, 0] & 0xF8) << 8)
            | ((rgb[:, :, 1] & 0xFC) << 3)
            | (rgb[:, :, 2] >> 3)
        )
        return packed.astype(">u2", copy=False).tobytes()
