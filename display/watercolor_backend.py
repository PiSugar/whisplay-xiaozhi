"""Backend selection and caption compositing for the watercolor UI."""

from __future__ import annotations

import logging
import importlib.util
import platform
from pathlib import Path

from PIL import Image

from display.watercolor_orb import OrbRenderer as PythonOrbRenderer

log = logging.getLogger("display.watercolor")

def _load_native_renderer():
    """Load the deployed extension, or the checked-in Linux AArch64 build."""
    try:
        from display._watercolor_rust import OrbRenderer

        return OrbRenderer
    except ImportError:
        pass

    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        return None
    extension = (
        Path(__file__).resolve().parent.parent
        / "rust"
        / "watercolor_renderer"
        / "prebuilt"
        / "linux-aarch64"
        / "_watercolor_rust.so"
    )
    if not extension.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("display._watercolor_rust", extension)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.OrbRenderer
    except (ImportError, OSError, AttributeError):
        log.exception("failed to load prebuilt Rust watercolor renderer: %s", extension)
        return None


NativeOrbRenderer = _load_native_renderer()


class RustOrbRenderer:
    """Python-compatible facade around the native Rust renderer."""

    backend = "rust"

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
        threads: int = 2,
    ):
        if NativeOrbRenderer is None:
            raise RuntimeError("Rust watercolor extension is not installed")
        self.width = width
        self.height = height
        self._native = NativeOrbRenderer(
            width,
            height,
            diameter,
            render_scale,
            smooth_fbm,
            temporal_3d,
            audio_reactivity,
            idle_speed,
            speech_motion,
            threads,
        )
        # Reuse the proven mixed-language caption layout without involving
        # Pillow in the animated orb path. Full RGBA overlays are cached.
        self._captions = PythonOrbRenderer(
            width=width,
            height=height,
            diameter=diameter,
            render_scale=0.2,
            caption_font_path=caption_font_path,
            caption_font_size=caption_font_size,
            caption_offset_x=caption_offset_x,
        )
        self._overlay_cache: dict[tuple[str, str], bytes] = {}

    def caption_pages(self, text: str) -> list[str]:
        """Return pages using the same layout engine as native overlays."""
        return self._captions.caption_pages(text)

    def _overlay(self, status_text: str, caption_text: str) -> bytes | None:
        kind = "status" if status_text else "caption"
        text = status_text or caption_text
        if not text:
            return None
        if kind == "caption":
            text = self._captions._caption_line(None, text)
            if not text:
                return None
        key = (kind, text)
        cached = self._overlay_cache.get(key)
        if cached is not None:
            return cached
        layer = (
            self._captions._status_layer(text)
            if kind == "status"
            else self._captions._caption_layer(text)
        )
        overlay = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        overlay.paste(layer, (0, self._captions.status_top), layer)
        result = overlay.tobytes()
        if len(self._overlay_cache) >= 24:
            self._overlay_cache.clear()
        self._overlay_cache[key] = result
        return result

    def rgb565(
        self,
        phase: float,
        level: float,
        peak: float = 0.0,
        bands=(0.0, 0.0, 0.0, 0.0),
        cumulative=(0.0, 0.0, 0.0, 0.0),
        visual_scale: float = 1.0,
        status_text: str = "",
        caption_text: str = "",
        caption_position: int | None = None,
        caption_complete: bool = True,
    ) -> bytes:
        if caption_text and caption_position is not None:
            caption_text = self._captions._caption_page_for_playback(
                caption_text, caption_position, caption_complete
            )
        return self._native.rgb565(
            phase,
            level,
            peak,
            list(bands),
            list(cumulative),
            visual_scale,
            self._overlay(status_text, caption_text),
        )


def create_watercolor_renderer(*, backend: str = "auto", threads: int = 2, **kwargs):
    """Create the requested backend, falling back only in auto mode."""
    selected = backend.lower()
    if selected in ("auto", "rust") and NativeOrbRenderer is not None:
        renderer = RustOrbRenderer(threads=threads, **kwargs)
        log.info("using Rust watercolor renderer (%d threads)", threads)
        return renderer
    if selected == "rust":
        raise RuntimeError(
            "WATERCOLOR_BACKEND=rust but no compatible Rust extension is available; "
            "run tools/build_watercolor_rust.sh"
        )
    if selected == "auto":
        log.info("Rust watercolor extension unavailable; using Python renderer")
    renderer = PythonOrbRenderer(**kwargs)
    renderer.backend = "python"
    return renderer
