"""Render the animated CanvasScene with the existing Pillow dependency."""

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

import config

_PAPER = "#f8f6f0"


def _partial_path(points: list[tuple[int, int]], progress: float) -> list[tuple[int, int]]:
    if len(points) < 2 or progress <= 0:
        return points[:1]
    lengths = [math.dist(start, end) for start, end in zip(points, points[1:])]
    remaining = sum(lengths) * min(1.0, progress)
    partial = [points[0]]
    for start, end, length in zip(points, points[1:], lengths):
        if length <= 0:
            continue
        if remaining >= length:
            partial.append(end)
            remaining -= length
            continue
        ratio = remaining / length
        partial.append((round(start[0] + (end[0] - start[0]) * ratio), round(start[1] + (end[1] - start[1]) * ratio)))
        break
    return partial

def _bezier_path(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    path = []
    for step in range(41):
        t = step / 40.0
        inverse = 1.0 - t
        if len(points) == 3:
            weights = (inverse * inverse, 2 * inverse * t, t * t)
        else:
            weights = (inverse ** 3, 3 * inverse * inverse * t, 3 * inverse * t * t, t ** 3)
        x = sum(point[0] * weight for point, weight in zip(points, weights))
        y = sum(point[1] * weight for point, weight in zip(points, weights))
        path.append((round(x), round(y)))
    return path

def _spline_path(points: list[tuple[int, int]], closed: bool) -> list[tuple[int, int]]:
    path = []
    count = len(points)
    segment_count = count if closed else count - 1
    for index in range(segment_count):
        p0 = points[(index - 1) % count] if closed or index > 0 else points[0]
        p1 = points[index]
        p2 = points[(index + 1) % count]
        p3 = points[(index + 2) % count] if closed or index + 2 < count else points[-1]
        for step in range(9):
            if path and step == 0:
                continue
            t = step / 8.0
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            path.append((round(x), round(y)))
    if closed and path[-1] != path[0]:
        path.append(path[0])
    return path

@lru_cache(maxsize=64)
def _font(size):
    for candidate in (
        config.FONT_PATH,
        str(Path(__file__).resolve().parents[1] / "assets/NotoSansSC-Bold.ttf"),
        str(Path.home() / ".local/share/fonts/whisplay-xiaozhi/NotoSansSC-Bold.ttf"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "DejaVuSans.ttf",
    ):
        if candidate:
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _draw_command(draw, command, anchor, progress):
    alpha = round(255 * command["opacity"])
    stroke = (*ImageColor.getrgb(command["stroke"]), alpha)
    fill = (*ImageColor.getrgb(command["fill"]), alpha) if command["fill"] else None
    width = command["width"]
    ax, ay = anchor
    kind = command["type"]
    complete = progress >= 1

    if kind in {"line", "polyline", "polygon", "bezier", "spline"}:
        points = [(round(ax + x), round(ay + y)) for x, y in command["points"]]
        closed = kind == "polygon" or command.get("closed", False)
        if kind == "bezier":
            points = _bezier_path(points)
        elif kind == "spline":
            points = _spline_path(points, closed)
        if closed and points[0] != points[-1]:
            points.append(points[0])
        if closed and complete and fill:
            draw.polygon(points, fill=fill)
        path = points if complete else _partial_path(points, progress)
        if len(path) >= 2:
            draw.line(path, fill=stroke, width=width, joint="curve")
        return path[-1]

    x, y = ax + command["x"], ay + command["y"]
    if kind in {"circle", "ellipse", "arc"}:
        rx = command["r"] if kind == "circle" else command["rx"]
        ry = command["r"] if kind == "circle" else command["ry"]
        box = (x - rx, y - ry, x + rx, y + ry)
        if complete and kind != "arc":
            draw.ellipse(box, fill=fill, outline=stroke, width=width)
            return x, y
        start = command["start"] if kind == "arc" else -90
        end = command["end"] if kind == "arc" else 270
        sweep = end - start
        end = start + max(-360, min(360, sweep)) * progress
        if end != start:
            draw.arc(box, min(start, end), max(start, end), fill=stroke, width=width)
        angle = math.radians(end)
        return x + rx * math.cos(angle), y + ry * math.sin(angle)

    if kind == "rectangle":
        right, bottom = x + command["w"], y + command["h"]
        if complete:
            draw.rounded_rectangle((x, y, right, bottom), radius=command["radius"],
                                   fill=fill, outline=stroke, width=width)
            return x, y
        path = _partial_path([(x, y), (right, y), (right, bottom), (x, bottom), (x, y)], progress)
        if len(path) >= 2:
            draw.line(path, fill=stroke, width=width, joint="curve")
        return path[-1]

    text = command["text"]
    font = _font(command["size"])
    visible = text[:math.ceil(len(text) * progress)]
    draw.text((x, y), visible, font=font, fill=stroke)
    return draw.textbbox((x, y), visible, font=font)[2], y + command["size"] / 2


def render_canvas(snapshot, now):
    """Return the paper image and progress for the newest animating drawing."""
    width, height = snapshot["width"], snapshot["height"]
    image = Image.new("RGB", (width, height), _PAPER)
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(16, height, 24):
        for x in range(16, width, 24):
            draw.point((x, y), fill="#d5cfc4")

    marker = snapshot["selected"]
    if marker is not None:
        x, y = marker
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline="#2581df", width=2)
        draw.line((x - 15, y, x + 15, y), fill="#2581df", width=2)
        draw.line((x, y - 15, x, y + 15), fill="#2581df", width=2)

    progress = None
    brush = None
    for drawing in snapshot["drawings"]:
        ratio = max(0.0, min(1.0, (now - drawing["created_at"]) / drawing["duration"]))
        commands = drawing["commands"]
        scaled = ratio * len(commands)
        completed = int(scaled)
        for command in commands[:completed]:
            _draw_command(draw, command, drawing["anchor"], 1.0)
        if completed < len(commands):
            progress = ratio
            brush = _draw_command(draw, commands[completed], drawing["anchor"], scaled - completed)
    if brush is not None:
        x, y = brush
        halo = 8 + 2 * math.sin(now * 8)
        draw.ellipse((x - halo, y - halo, x + halo, y + halo), outline="#60a5fa", width=2)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#2563eb")
    return image.convert("RGBA"), progress
