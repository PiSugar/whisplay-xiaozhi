"""Caption facade for the Rust voxel workstation renderer."""
from display.watercolor_backend import RustOrbRenderer, _load_native_renderer


class RustRobotRenderer(RustOrbRenderer):
    def __init__(self, *, width=240, height=280, caption_font_path="",
                 caption_font_size=15, caption_offset_x=3, diameter=168, seed=None):
        native = _load_native_renderer("RobotRenderer")
        if native is None:
            raise RuntimeError(
                "Robot mode requires the updated Rust extension; "
                "run bash tools/build_watercolor_rust.sh on this platform"
            )
        self.width, self.height = width, height
        self._native = native(width, height, seed=seed)
        self._init_captions(width, height, diameter, caption_font_path,
                            caption_font_size, caption_offset_x)

    def trigger_event(self, name):
        """Trigger one cosmetic event for previews, with no application effects."""
        self._native.trigger_event(name)

    def rotate_view(self):
        self._native.rotate_view()

    def render(self, phase, working, idle_seconds, sleep_after, caption_text="", *, thinking=False,
               effects_allowed=True):
        return self._native.rgb565(
            phase, working, idle_seconds, sleep_after,
            self._overlay("", caption_text), thinking, effects_allowed,
        )
