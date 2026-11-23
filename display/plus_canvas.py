"""Slide-out drawing surface for the Whisplay Plus chat interface."""

import time
from functools import cache

from canvas.render import render_canvas
from canvas.scene import CanvasScene

CANVAS_CSS = b"""
.plus-canvas { background: #f8f6f0; color: #253343; }
.plus-canvas-title { font-size: 22px; font-weight: 700; }
.plus-canvas button {
    background: #fffdfa; background-image: none; color: #304054;
    border: 1px solid #ddd6cb; box-shadow: none;
    min-height: 42px; border-radius: 10px; padding: 0 12px;
}
.plus-canvas button:hover, .plus-canvas button:active { background: #e8edf5; }
.plus-canvas button.plus-canvas-talk { background: #2474cd; color: #fff; border-color: #2474cd; }
.plus-canvas-status, .plus-canvas-caption, .plus-canvas button.plus-canvas-talk { border-radius: 20px; }
.plus-canvas-status { background: #fffdfa; padding: 8px 13px; }
.plus-canvas-prompt { color: #565f69; background: #f8f6f0; padding: 8px; }
.plus-canvas-caption { background: #29384a; color: #fff; padding: 8px 12px; }
"""


@cache
def _paper_type(Gtk):
    from gi.repository import Graphene

    class CanvasPaper(Gtk.Widget):
        """Present the paper texture without using it as a layout size request."""

        def __init__(self):
            super().__init__(hexpand=True, vexpand=True)
            self._texture = None

        def set_texture(self, texture):
            self._texture = texture
            self.queue_draw()

        def do_snapshot(self, snapshot):
            texture = self._texture
            if texture is not None:
                bounds = Graphene.Rect()
                bounds.init(0, 0, min(self.get_width(), texture.get_width()),
                            min(self.get_height(), texture.get_height()))
                snapshot.append_texture(texture, bounds)

    return CanvasPaper


class PlusCanvas:
    def __init__(self, Gtk, texture, on_talk, on_select):
        self.scene = CanvasScene()
        self._texture = texture
        self._on_select = on_select
        self._revision = -1
        self._snapshot = None
        self._frame_at = 0.0
        self._progress = None
        self._viewport_size = (0, 0)
        self.caption = ""

        self.widget = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, transition_duration=220,
            halign=Gtk.Align.FILL, valign=Gtk.Align.FILL, visible=False,
        )
        self.widget.connect("notify::child-revealed", self._on_reveal_changed)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("plus-canvas")
        self.widget.set_child(panel)

        toolbar = Gtk.Box(spacing=8, margin_top=10, margin_start=12, margin_end=12)
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back.set_tooltip_text("Back to conversation")
        back.connect("clicked", lambda *_: self.close())
        toolbar.append(back)
        title = Gtk.Label(label="Canvas", xalign=0, hexpand=True)
        title.add_css_class("plus-canvas-title")
        toolbar.append(title)
        for label, action in (("Undo", self.scene.undo), ("Clear", self.scene.clear)):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, callback=action: callback())
            toolbar.append(button)
        panel.append(toolbar)

        paper = Gtk.Overlay(vexpand=True, hexpand=True, margin_start=12, margin_end=12)
        self._picture = _paper_type(Gtk)()
        paper.set_child(self._picture)
        gesture = Gtk.GestureClick(button=1)
        gesture.connect("released", self._select_point)
        self._picture.add_controller(gesture)
        self._prompt = Gtk.Label(
            label="Tap where you want XiaoZhi to draw", wrap=True, can_target=False,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        self._prompt.add_css_class("plus-canvas-prompt")
        paper.add_overlay(self._prompt)
        self._caption = Gtk.Label(
            wrap=True, can_target=False, halign=Gtk.Align.FILL, valign=Gtk.Align.END,
            margin_bottom=8, visible=False,
        )
        self._caption.add_css_class("plus-canvas-caption")
        self._caption.add_css_class("plus-text")
        paper.add_overlay(self._caption)
        panel.append(paper)

        footer = Gtk.Box(spacing=10, margin_start=12, margin_end=12, margin_bottom=12)
        self._status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self._status.add_css_class("plus-canvas-status")
        footer.append(self._status)
        self._talk = Gtk.Button(label="Talk")
        self._talk.add_css_class("plus-canvas-talk")
        self._talk.connect("clicked", lambda *_: on_talk())
        footer.append(self._talk)
        panel.append(footer)

    @property
    def is_open(self):
        return self.widget.get_reveal_child()

    def open(self):
        self.widget.set_visible(True)
        self.widget.set_reveal_child(True)

    def close(self):
        self.widget.set_reveal_child(False)
        self._on_reveal_changed()

    def _on_reveal_changed(self, *_args):
        if not self.is_open and not self.widget.get_child_revealed():
            self.widget.set_visible(False)

    def _select_point(self, _gesture, count, x, y):
        size = self._paper_size()
        if count == 1 and size and 0 <= x < size[0] and 0 <= y < size[1]:
            width, height = size
            self.scene.configure(width, height)
            self.scene.select(x, y)
            self._on_select()

    def _paper_size(self):
        if not self.is_open or not self.widget.get_child_revealed():
            return None
        width, height = self._picture.get_width(), self._picture.get_height()
        # Gtk.Revealer can temporarily allocate a much larger child while
        # sliding it in. Never use those allocations to size a bitmap.
        viewport_width, viewport_height = self._viewport_size
        if 0 < width <= viewport_width and 0 < height <= viewport_height:
            return width, height
        return None

    def refresh(self, activity, status, viewport_width, viewport_height):
        self._viewport_size = viewport_width, viewport_height
        size = self._paper_size()
        if size is None:
            return
        self.scene.configure(*size)
        revision = self.scene.revision
        now = time.monotonic()
        changed = revision != self._revision
        if changed or (self._progress is not None and now - self._frame_at >= 0.065):
            if changed:
                self._snapshot = self.scene.snapshot()
                self._revision = revision
                self._prompt.set_visible(self._snapshot["selected"] is None)
            image, self._progress = render_canvas(self._snapshot, now)
            self._picture.set_texture(self._texture(image))
            self._frame_at = now

        if self._progress is not None:
            label = f"XiaoZhi · Drawing {round(self._progress * 100)}%"
        else:
            label = f"XiaoZhi · {status.rstrip('.') or activity.title()}"
        if label != self._status.get_text():
            self._status.set_text(label)
        self._talk.set_visible(activity == "idle" and self._progress is None)
        show_caption = activity == "speaking" and bool(self.caption)
        self._caption.set_visible(show_caption)
        if show_caption and self._caption.get_text() != self.caption:
            self._caption.set_text(self.caption)
