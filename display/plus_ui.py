"""Fullscreen touchscreen UI for the Whisplay Plus desktop session."""

import asyncio
import logging
import os
import subprocess
import tempfile
import time
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw

import config
from display.text_utils import ASSETS_DIR, TOOL_TAG_RE, get_emoji_image
from display.ui_renderer import DisplayState

log = logging.getLogger(__name__)

_CSS = b"""
window.whisplay-plus { background: #111822; color: #f4f7fb; }
.plus-header { font-size: 19px; font-weight: bold; }
.plus-meta { font-size: 13px; color: #b8c7da; }
.plus-emoji { font-size: 88px; }
.plus-emoji-art { -gtk-icon-size: 101px; }
.plus-wifi-icon { -gtk-icon-size: 22px; }
.plus-battery-icon { -gtk-icon-size: 35px; }
.plus-response { background: #1d2838; border-radius: 16px; }
.plus-text { font-family: "Noto Sans SC", "Noto Sans CJK SC", sans-serif; font-size: 20px; font-weight: 700; }
.plus-user {
    background: #283c51; border-radius: 12px; padding: 8px 11px; color: #eef6ff;
}
.plus-tool-tag {
    background: #082a70; color: #d9edff; font-size: 13px; font-weight: bold;
    border-radius: 6px; padding: 3px 9px;
}
.plus-terminal {
    background: #111b25; border: 1px solid #355044; border-radius: 9px;
    padding: 9px; color: #8ee5ba; font-family: monospace; font-size: 13px;
}
.plus-action { border-radius: 14px; min-height: 48px; padding: 0 14px; font-size: 16px; }
button.plus-talk {
    background: #1d2d42; background-image: none; color: #edf3fb;
    border: 1px solid #3a506c; box-shadow: none;
}
button.plus-talk:hover, button.plus-talk:active {
    background: #263b54; border-color: #506989;
}
button.plus-talk.plus-talk-ready {
    background: #2474cd; border-color: #428bdd;
}
button.plus-talk.plus-talk-ready:hover, button.plus-talk.plus-talk-ready:active {
    background: #2d81df; border-color: #67a7ef;
}
button.plus-icon-button {
    min-width: 48px; min-height: 48px; padding: 0;
    background: #151e2a; background-image: none; color: #e7edf5;
    border: 1px solid #344155; border-radius: 12px; box-shadow: none;
}
button.plus-icon-button:hover, button.plus-icon-button:active {
    background: #202c3c; border-color: #51627a;
}
button.plus-talk:disabled, button.plus-icon-button:disabled {
    background: #172230; color: #a8b8cc; border-color: #2d3c50;
}
.plus-icon-button image { -gtk-icon-size: 22px; }
.plus-scrim, .plus-scrim:hover, .plus-scrim:active {
    background: rgba(3, 8, 14, 0.72); border: none; border-radius: 0; box-shadow: none;
}
.plus-settings-sheet {
    background: #182231; color: #f4f7fb;
    border-top: 1px solid #334257; border-radius: 20px 20px 0 0;
}
.plus-settings-title { font-size: 20px; font-weight: bold; }
.plus-settings-caption { color: #a8b7cb; font-size: 14px; font-weight: bold; }
.plus-settings-value { color: #e7edf5; font-size: 16px; }
.plus-settings-sheet button {
    background: #28364a; background-image: none;
    color: #f4f7fb; border: 1px solid #39485e; box-shadow: none;
}
.plus-settings-sheet button:hover, .plus-settings-sheet button:active { background: #31425a; }
.plus-settings-sheet button.plus-settings-exit {
    background: #382735; color: #ffa4a5; border-color: #654355;
}
.plus-settings-sheet button.plus-settings-exit:hover,
.plus-settings-sheet button.plus-settings-exit:active { background: #513343; }
.plus-volume trough { min-height: 8px; background: #344256; border: none; border-radius: 5px; }
.plus-volume highlight { background: #57e49a; border: none; border-radius: 5px; margin: 0; }
.plus-volume slider {
    min-width: 26px; min-height: 26px; border-radius: 50%;
    background: #57e49a; border: 1px solid #8bf1ba; box-shadow: none;
}
"""

_EMOJI_SIZE = 101
_WIFI_STRENGTH = ("weak", "medium", "strong")
_COMMAND_LINGER_SECONDS = 5
_COMMAND_LINES = 10


def _rotation_path():
    config_dir = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_dir / "whisplay-xiaozhi" / "plus-orientation"


def _read_rotation(path):
    try:
        rotation = int(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return rotation if rotation in (0, 90, 180, 270) else 0


def _save_rotation(path, rotation):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(f"{rotation}\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _clean_user_text(text):
    for index, char in enumerate(text):
        if (char.isspace() or char in "🗣︎️\ufffd" or unicodedata.category(char) in ("Cc", "Cf")):
            continue
        return text[index:].rstrip()
    return ""


@cache
def _app_surface_type():
    from gi.repository import Graphene, Gsk, Gtk

    class AppSurface(Gtk.Widget):
        def __init__(self, child, rotation):
            super().__init__()
            self.child = child
            self.rotation = rotation
            child.set_parent(self)

        def set_rotation(self, rotation):
            self.rotation = rotation
            self.queue_allocate()

        def do_measure(self, _orientation, _for_size):
            # The compositor sizes the fullscreen window independently of
            # the orientation of the contents.
            return 0, 0, -1, -1

        def do_size_allocate(self, width, height, baseline):
            if self.child is None:
                return
            transform = None
            if self.rotation:
                origin = Graphene.Point()
                origin.init(width if self.rotation in (90, 180) else 0,
                            height if self.rotation in (180, 270) else 0)
                transform = Gsk.Transform.new().translate(origin).rotate(self.rotation)
                if self.rotation in (90, 270):
                    width, height = height, width
                baseline = -1
            # Using the allocation transform rotates both rendering and GTK's
            # input coordinates, including the settings controls.
            self.child.allocate(width, height, baseline, transform)

        def do_snapshot(self, snapshot):
            if self.child is not None:
                self.snapshot_child(self.child, snapshot)

        def detach(self):
            self.child.unparent()
            self.child = None

    return AppSurface


class WhisplayPlusUI:
    def __init__(self, output, on_talk, on_exit, speaker, on_camera=None):
        import gi
        gi.require_version("Gdk", "4.0")
        gi.require_version("Graphene", "1.0")
        gi.require_version("Gsk", "4.0")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, GLib, Gtk, Pango

        if not Gtk.init_check():
            raise RuntimeError("No graphical desktop session is available")

        self._Gtk, self._Gdk, self._GLib = Gtk, Gdk, GLib
        self._wrap_mode = Pango.WrapMode.WORD_CHAR
        self.state = DisplayState()
        self._task = None
        self._photo = None
        self._shown_photo = None
        self._snapshot = None
        self._assistant = None
        self._assistant_length = 0
        self._assistant_offset = 0
        self._seen_tools = Counter()
        self._last_tool = None
        self._command_token = None
        self._command_timeout = None
        self._follow_transcript = True
        self._speaker = speaker
        self._volume_task = None
        self._pending_volume = None
        self._read_volume = False

        self._window = Gtk.Window(title="Whisplay XiaoZhi")
        self._window.add_css_class("whisplay-plus")
        self._window.connect("close-request", self._request_exit, on_exit)

        provider = Gtk.CssProvider()
        provider.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self._window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._provider = provider

        overlay = Gtk.Overlay()
        self._overlay = overlay
        self._rotation_file = _rotation_path()
        self._app_surface = _app_surface_type()(overlay, _read_rotation(self._rotation_file))
        self._window.set_child(self._app_surface)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{edge}")(14)
        overlay.set_child(root)
        self._root = root

        header = Gtk.Box(spacing=10)
        self._status = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self._status.add_css_class("plus-header")
        self._wifi_icons = []
        for strength in _WIFI_STRENGTH:
            with Image.open(Path(ASSETS_DIR) / f"wifi-{strength}.png") as source:
                icon = source.convert("RGBA").resize((22, 22), Image.Resampling.LANCZOS)
                self._wifi_icons.append(self._texture(icon))
        self._wifi = Gtk.Image(valign=Gtk.Align.CENTER)
        self._wifi.add_css_class("plus-wifi-icon")
        self._battery_icon = Gtk.Image(valign=Gtk.Align.CENTER)
        self._battery_icon.add_css_class("plus-battery-icon")
        self._battery_label = Gtk.Label()
        self._battery_label.add_css_class("plus-meta")
        battery = Gtk.Box(spacing=5)
        battery.append(self._battery_icon)
        battery.append(self._battery_label)
        self._settings_button = self._icon_button("view-more-symbolic", "Settings")
        self._settings_button.connect("clicked", self._open_settings)
        for widget in (self._status, self._wifi, battery, self._settings_button):
            header.append(widget)
        root.append(header)

        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, vexpand=True)
        self._emoji = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self._emoji_image = Gtk.Image(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._emoji_image.add_css_class("plus-emoji-art")
        self._emoji_fallback = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=3)
        self._emoji_fallback.add_css_class("plus-emoji")
        self._emoji.add_named(self._emoji_image, "image")
        self._emoji.add_named(self._emoji_fallback, "text")
        self._body.append(self._emoji)

        self._text = self._text_label()
        self._transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, visible=False)
        self._command = self._text_label(style="plus-terminal")
        self._command.set_wrap(False)
        self._command.set_ellipsize(Pango.EllipsizeMode.END)
        self._command.set_visible(False)
        response = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for edge in ("top", "start", "end"):
            getattr(response, f"set_margin_{edge}")(14)
        response.append(self._text)
        response.append(self._transcript)
        # Include the bottom padding in the scrollable content itself.
        response.append(Gtk.Box(height_request=96, can_target=False))
        self._scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.add_css_class("plus-response")
        self._scroll.set_child(response)
        adjustment = self._scroll.get_vadjustment()
        adjustment.connect("value-changed", self._update_follow_transcript)
        adjustment.connect("changed", self._scroll_transcript_to_end)

        self._picture = Gtk.Picture(can_shrink=True, vexpand=True, hexpand=True)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._caption = Gtk.Label(wrap=True)
        self._caption.add_css_class("plus-meta")
        self._preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._preview.append(self._picture)
        self._preview.append(self._caption)
        content = Gtk.Stack(vexpand=True, hhomogeneous=False, vhomogeneous=False)
        content.add_named(self._scroll, "conversation")
        content.add_named(self._preview, "photo")
        self._content = content
        conversation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, vexpand=True)
        conversation.append(content)
        self._body.append(conversation)
        root.append(self._body)

        actions = Gtk.Box(spacing=10)
        self._talk = Gtk.Button(label="Tap to talk", hexpand=True)
        self._talk.add_css_class("plus-talk")
        self._talk.add_css_class("plus-talk-ready")
        self._talk.add_css_class("plus-action")
        self._talk.connect("clicked", lambda *_: on_talk())
        actions.append(self._talk)
        self._camera = None
        if on_camera:
            self._camera = self._icon_button(self._camera_icon(), "Open camera")
            self._camera.set_visible(False)
            self._camera.connect("clicked", lambda *_: on_camera())
            actions.append(self._camera)
        conversation.append(actions)

        overlay.add_overlay(self._build_settings(on_exit))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self._window.add_controller(keys)

        monitor = self._find_monitor(output)
        if monitor is None:
            log.warning("Plus output %s is unavailable in the desktop session", output)
            self._window.fullscreen()
        else:
            self._window.fullscreen_on_monitor(monitor)

    @staticmethod
    def _request_exit(_window, on_exit):
        on_exit()
        return True

    def _icon_button(self, icon, label):
        if isinstance(icon, str):
            button = self._Gtk.Button.new_from_icon_name(icon)
        else:
            image = self._Gtk.Image.new_from_paintable(icon)
            image.set_halign(self._Gtk.Align.CENTER)
            image.set_valign(self._Gtk.Align.CENTER)
            button = self._Gtk.Button(child=image)
        button.add_css_class("plus-icon-button")
        self._label_icon_button(button, label)
        return button

    def _label_icon_button(self, button, label):
        button.set_tooltip_text(label)
        button.update_property([self._Gtk.AccessibleProperty.LABEL], [label])

    def _build_settings(self, on_exit):
        Gtk = self._Gtk
        self._settings = Gtk.Overlay(visible=False)
        backdrop = Gtk.Button(focusable=False, tooltip_text="Close settings")
        backdrop.add_css_class("plus-scrim")
        backdrop.connect("clicked", self._close_settings)
        self._settings.set_child(backdrop)

        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END)
        sheet.add_css_class("plus-settings-sheet")
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(panel, f"set_margin_{edge}")(16)
        sheet.append(panel)
        self._settings.add_overlay(sheet)

        header = Gtk.Box(spacing=10)
        title = Gtk.Label(label="Settings", hexpand=True, xalign=0)
        title.add_css_class("plus-settings-title")
        self._settings_close = self._icon_button("window-close-symbolic", "Close settings")
        self._settings_close.connect("clicked", self._close_settings)
        header.append(title)
        header.append(self._settings_close)
        panel.append(header)

        self._volume_label = Gtk.Label(label="Speaker volume", xalign=0)
        self._volume_label.add_css_class("plus-settings-caption")
        panel.append(self._volume_label)
        volume_row = Gtk.Box(spacing=14)
        volume_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        volume_icon.set_pixel_size(24)
        volume_row.append(volume_icon)
        self._volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._volume.set_hexpand(True)
        self._volume.set_draw_value(False)
        self._volume.set_size_request(-1, 42)
        self._volume.set_tooltip_text("Speaker volume")
        self._volume.add_css_class("plus-volume")
        self._volume.set_sensitive(False)
        self._volume_handler = self._volume.connect("value-changed", self._on_volume_change)
        volume_row.append(self._volume)
        self._volume_value = Gtk.Label(label="—", width_chars=4, xalign=1)
        self._volume_value.add_css_class("plus-settings-value")
        volume_row.append(self._volume_value)
        panel.append(volume_row)

        self._rotation_label = Gtk.Label(
            label=f"App orientation · {self._app_surface.rotation}°", xalign=0,
        )
        self._rotation_label.add_css_class("plus-settings-caption")
        panel.append(self._rotation_label)
        rotate = Gtk.Button(label="Rotate 90° clockwise")
        rotate.add_css_class("plus-action")
        rotate.connect("clicked", self._on_rotate)
        panel.append(rotate)

        clear = Gtk.Button(label="Clear conversation")
        clear.add_css_class("plus-action")
        clear.connect("clicked", self._on_clear_conversation)
        panel.append(clear)

        self._exit_actions = Gtk.Stack(vhomogeneous=False)
        self._exit_button = Gtk.Button(label="Exit app")
        self._exit_button.add_css_class("plus-settings-exit")
        self._exit_button.add_css_class("plus-action")
        self._exit_actions.add_named(self._exit_button, "default")
        self._exit_button.connect("clicked", self._show_exit_confirmation)
        confirmation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        confirmation.append(Gtk.Label(label="Exit Whisplay XiaoZhi?"))
        buttons = Gtk.Box(spacing=10, homogeneous=True)
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("plus-action")
        cancel.connect("clicked", self._cancel_exit_confirmation)
        self._exit_confirm = Gtk.Button(label="Exit")
        self._exit_confirm.add_css_class("plus-action")
        self._exit_confirm.add_css_class("plus-settings-exit")
        self._exit_confirm.connect("clicked", lambda *_: on_exit())
        buttons.append(cancel)
        buttons.append(self._exit_confirm)
        confirmation.append(buttons)
        self._exit_actions.add_named(confirmation, "confirm")
        self._exit_cancel = cancel
        panel.append(self._exit_actions)
        return self._settings

    def _on_rotate(self, _button):
        rotation = (self._app_surface.rotation + 90) % 360
        self._app_surface.set_rotation(rotation)
        try:
            _save_rotation(self._rotation_file, rotation)
        except OSError as exc:
            log.warning("Unable to save Plus orientation: %s", exc)
            self._rotation_label.set_text(f"App orientation · {rotation}° (not saved)")
        else:
            self._rotation_label.set_text(f"App orientation · {rotation}°")

    def _open_settings(self, _button):
        self._exit_actions.set_visible_child_name("default")
        self._root.set_sensitive(False)
        self._settings.set_visible(True)
        self._settings_close.grab_focus()
        self._read_volume = True
        self._volume.set_sensitive(False)
        self._start_volume_task()

    def _close_settings(self, _button=None):
        self._settings.set_visible(False)
        self._root.set_sensitive(True)
        self._settings_button.grab_focus()

    def _on_clear_conversation(self, _button):
        self._clear_command()
        while child := self._transcript.get_first_child():
            self._transcript.remove(child)
        self._transcript.set_visible(False)
        self._assistant = None
        # Future chunks of an in-progress reply include its previous prefix.
        self._assistant_offset = self._assistant_length
        self._seen_tools.clear()
        self._last_tool = None
        self._text.set_visible(False)
        self.state.update(text="")
        self._scroll.get_vadjustment().set_value(0)
        self._follow_transcript = True
        self._close_settings()

    def _on_key_pressed(self, _controller, key, _keycode, _state):
        if key == self._Gdk.KEY_Escape and self._settings.get_visible():
            self._close_settings()
            return True
        return False

    def _show_exit_confirmation(self, _button):
        self._exit_actions.set_visible_child_name("confirm")
        self._exit_cancel.grab_focus()

    def _cancel_exit_confirmation(self, _button):
        self._exit_actions.set_visible_child_name("default")
        self._exit_button.grab_focus()

    def _on_volume_change(self, slider):
        self._pending_volume = int(slider.get_value())
        self._volume_value.set_text(f"{self._pending_volume}%")
        self._start_volume_task()

    def _start_volume_task(self):
        if self._volume_task is None or self._volume_task.done():
            self._volume_task = asyncio.create_task(self._sync_volume())

    async def _sync_volume(self):
        try:
            while self._pending_volume is not None or self._read_volume:
                if self._pending_volume is not None:
                    await asyncio.sleep(0.08)
                    volume, self._pending_volume = self._pending_volume, None
                    await self._speaker.set_volume(volume)
                else:
                    self._read_volume = False
                    volume = await self._speaker.get_volume()
                    with self._volume.handler_block(self._volume_handler):
                        self._volume.set_value(volume)
                    self._volume_value.set_text(f"{volume}%")
                    self._volume.set_sensitive(True)
                self._volume_label.set_text("Speaker volume")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("Plus speaker volume unavailable: %s", exc)
            self._volume_label.set_text("Speaker volume unavailable")

    def _find_monitor(self, output):
        monitors = self._window.get_display().get_monitors()
        panel = None
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            connector = getattr(monitor, "get_connector", lambda: None)()
            if connector == output:
                return monitor
            geometry = monitor.get_geometry()
            if (connector is None and sorted((geometry.width, geometry.height)) == [480, 640]):
                panel = monitor
        return panel

    def _texture(self, image):
        width, height = image.size
        return self._Gdk.MemoryTexture.new(
            width, height, self._Gdk.MemoryFormat.R8G8B8A8,
            self._GLib.Bytes.new(image.tobytes()), width * 4,
        )

    def _camera_icon(self):
        # Draw at 4× size so the outline stays smooth on the small panel.
        image = Image.new("RGBA", (96, 96))
        draw = ImageDraw.Draw(image)
        color = "#e7edf5"
        draw.rounded_rectangle((33, 12, 63, 29), radius=8, fill=color)
        draw.rounded_rectangle((8, 24, 88, 84), radius=16, outline=color, width=7)
        draw.ellipse((33, 39, 63, 69), outline=color, width=7)
        return self._texture(image.resize((22, 22), Image.Resampling.LANCZOS))

    def _text_label(self, text="", style="plus-text"):
        label = self._Gtk.Label(label=text, xalign=0, wrap=True)
        label.set_wrap_mode(self._wrap_mode)
        label.add_css_class(style)
        return label

    def _tool_tag(self, name):
        label = self._text_label(name, "plus-tool-tag")
        label.set_halign(self._Gtk.Align.START)
        return label

    def _append_transcript(self, widget):
        self._text.set_visible(False)
        self._transcript.append(widget)
        self._transcript.set_visible(True)

    def _append_user(self, text):
        text = _clean_user_text(text)
        if not text:
            return
        self._seen_tools.clear()
        self._last_tool = None
        self._reset_assistant()
        label = self._text_label(text)
        label.add_css_class("plus-user")
        label.set_halign(self._Gtk.Align.END)
        self._append_transcript(label)

    def _reset_assistant(self):
        self._assistant = None
        self._assistant_length = 0
        self._assistant_offset = 0

    def _append_tool(self, name):
        self._seen_tools[name.casefold()] += 1
        if self._assistant is not None:
            self._assistant = None
            self._assistant_offset = self._assistant_length
        if self._last_tool and self._last_tool[0] == name:
            _, count, label = self._last_tool
            count += 1
            label.set_text(f"{name} ×{count}")
        else:
            count, label = 1, self._tool_tag(name)
            self._append_transcript(label)
        self._last_tool = name, count, label
        return label

    def _set_assistant(self, text):
        self._assistant_length = len(text)
        remaining_tools = self._seen_tools.copy()
        parts = []
        for index, value in enumerate(TOOL_TAG_RE.split(text[self._assistant_offset:])):
            if index % 2 == 0:
                if value.strip():
                    parts.append((value.strip(), 0))
            elif remaining_tools[value.casefold()]:
                remaining_tools[value.casefold()] -= 1
            elif parts and parts[-1][0] == value and parts[-1][1]:
                parts[-1] = value, parts[-1][1] + 1
            else:
                parts.append((value, 1))

        if not parts:
            return
        self._last_tool = None
        if self._assistant is None:
            self._assistant = self._Gtk.Box(orientation=self._Gtk.Orientation.VERTICAL, spacing=6)
            self._append_transcript(self._assistant)
        child = self._assistant.get_first_child()
        while child:
            self._assistant.remove(child)
            child = self._assistant.get_first_child()
        for value, count in parts:
            if count:
                self._assistant.append(self._tool_tag(f"{value} ×{count}" if count > 1 else value))
            else:
                self._assistant.append(self._text_label(value))

    def _update_follow_transcript(self, adjustment):
        bottom = adjustment.get_upper() - adjustment.get_page_size()
        self._follow_transcript = bottom - adjustment.get_value() <= 4

    def _scroll_transcript_to_end(self, adjustment):
        if self._follow_transcript:
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())

    def begin_command(self):
        self._clear_command()
        tag = self._append_tool("local_command")
        self._transcript.insert_child_after(self._command, tag)
        self._command_token = object()
        return self._command_token

    def update_command(self, token, text):
        if token is not self._command_token:
            return
        if text is None:
            if self._command.get_visible() and self._command_timeout is None:
                self._command_timeout = asyncio.get_running_loop().call_later(
                    _COMMAND_LINGER_SECONDS, self._clear_command
                )
            return
        lines = text.splitlines()[-_COMMAND_LINES:]
        lines.extend([""] * (_COMMAND_LINES - len(lines)))
        self._command.set_text("\n".join(lines))
        self._command.set_visible(True)

    def _clear_command(self):
        if self._command_timeout:
            self._command_timeout.cancel()
            self._command_timeout = None
        self._command_token = None
        self._command.set_visible(False)
        self._command.set_text("")
        if self._command.get_parent() is not None:
            self._transcript.remove(self._command)

    def _update_emoji(self, text):
        icon = get_emoji_image(text, _EMOJI_SIZE)
        if icon is None and "\ufe0f" in text:
            icon = get_emoji_image(text.replace("\ufe0f", ""), _EMOJI_SIZE)
        if icon is None:
            self._emoji_fallback.set_text(text)
            self._emoji.set_visible_child_name("text")
        else:
            self._emoji_image.set_from_paintable(self._texture(icon))
            self._emoji.set_visible_child_name("image")

    def _update_battery(self, level, color):
        icon = Image.new("RGBA", (35, 16))
        draw = ImageDraw.Draw(icon)
        draw.rounded_rectangle((0, 0, 30, 15), radius=3, outline="#8492a5")
        draw.rounded_rectangle((32, 5, 34, 10), radius=1, fill="#8492a5")
        fill_width = round(25 * max(0, min(100, level)) / 100)
        if fill_width:
            draw.rectangle((3, 3, 2 + fill_width, 12), fill=color)
        self._battery_icon.set_from_paintable(self._texture(icon))
        self._battery_label.set_text(f"{level}%" if level >= 0 else "—%")
        self._battery_icon.set_tooltip_text(
            f"Battery {level}%" if level >= 0 else "Battery level unavailable"
        )

    def start(self):
        self._window.present()
        self._task = asyncio.create_task(self._pump_desktop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        self._window.set_visible(False)
        if self._volume_task:
            await self._volume_task
        self._clear_command()
        self._Gtk.StyleContext.remove_provider_for_display(
            self._window.get_display(), self._provider
        )
        self._app_surface.detach()
        self._window.destroy()

    async def _pump_desktop(self):
        context = self._GLib.MainContext.default()
        while True:
            # Bound each iteration so desktop events cannot starve voice I/O.
            for _ in range(16):
                if not context.iteration(False):
                    break
            self._refresh()
            await asyncio.sleep(1 / 30)

    def _refresh(self):
        width, height = self._overlay.get_width(), self._overlay.get_height()
        orientation = self._Gtk.Orientation
        landscape = width > height
        layout = orientation.HORIZONTAL if landscape else orientation.VERTICAL
        if self._body.get_orientation() != layout:
            self._body.set_orientation(layout)
            self._emoji.set_size_request(140 if landscape else -1, -1)

        snap = self.state.snapshot()
        if snap != self._snapshot:
            previous = self._snapshot or {}
            if snap["status"] != previous.get("status"):
                self._status.set_text(snap["status"])
            if snap["emoji"] != previous.get("emoji"):
                self._update_emoji(snap["emoji"])
            if snap["text"] != previous.get("text"):
                self._text.set_text(snap["text"])
                self._text.set_visible(bool(snap["text"]) and not self._transcript.get_first_child())
            wifi = snap["wifi_signal_level"]
            if wifi != previous.get("wifi_signal_level"):
                self._wifi.set_visible(wifi > 0)
                if wifi > 0:
                    self._wifi.set_from_paintable(self._wifi_icons[min(wifi, len(self._wifi_icons)) - 1])
                    self._wifi.set_tooltip_text(f"Wi-Fi signal {wifi}/3")
            if (snap["battery_level"] != previous.get("battery_level")
                    or snap["battery_color"] != previous.get("battery_color")):
                self._update_battery(snap["battery_level"], snap["battery_color"])
            activity = snap["activity"]
            talk_label = {
                "activating": "Pairing…", "connecting": "Connecting…",
                "listening": "Listening…", "speaking": "Interrupt",
            }.get(activity, "Tap to talk")
            self._talk.set_label(talk_label)
            if talk_label == "Tap to talk":
                self._talk.add_css_class("plus-talk-ready")
            else:
                self._talk.remove_css_class("plus-talk-ready")
            self._talk.set_sensitive(activity not in ("activating", "connecting", "listening"))
            if self._camera and activity != previous.get("activity"):
                self._label_icon_button(self._camera, "Take photo" if activity == "camera" else "Open camera")
                self._talk.set_visible(activity != "camera")
            self._snapshot = snap

        photo = self._photo
        if photo and time.monotonic() >= photo[2]:
            photo = None
        if photo is not self._shown_photo:
            if photo:
                try:
                    texture = self._Gdk.Texture.new_from_bytes(self._GLib.Bytes.new(photo[0]))
                except self._GLib.Error as exc:
                    log.warning("Unable to display photo: %s", exc)
                else:
                    self._picture.set_paintable(texture)
                    self._caption.set_text(photo[1])
                    self._caption.set_visible(bool(photo[1]))
                    self._content.set_visible_child_name("photo")
            else:
                self._picture.set_paintable(None)
                self._content.set_visible_child_name("conversation")
            self._shown_photo = photo
        self._emoji.set_visible(landscape or self._content.get_visible_child_name() == "conversation")

    def update(self, **kwargs):
        # Listening is shown in the status and action labels. Keep the last
        # expression when the shared application sends its SPI microphone emoji.
        if kwargs.get("emoji") == "🎤":
            del kwargs["emoji"]
        if kwargs.get("new_assistant"):
            self._reset_assistant()
        if "user_text" in kwargs:
            self._append_user(kwargs["user_text"])
            kwargs["text"] = ""
        # The command runner creates its own tag and attaches the output to it.
        if "tool_name" in kwargs and kwargs["tool_name"] != "local_command":
            self._append_tool(kwargs["tool_name"])
        if "assistant_text" in kwargs:
            if kwargs["assistant_text"]:
                self._set_assistant(kwargs["assistant_text"])
            kwargs["text"] = ""
        self.state.update(**kwargs)

    def update_audio(self, pcm, sample_rate, role):
        """The Plus layout uses activity updates for its listening indicator."""

    def set_camera_available(self, available):
        if self._camera:
            self._camera.set_visible(available)

    def show_photo(self, image_path, duration=None):
        self.show_photo_bytes(Path(image_path).read_bytes(), duration)

    def show_photo_bytes(self, jpeg, duration=None, caption=""):
        seconds = config.CAMERA_PREVIEW_SECONDS if duration is None else duration
        self._photo = (jpeg, caption, time.monotonic() + max(0.1, seconds))

    def clear_photo(self):
        self._photo = None
