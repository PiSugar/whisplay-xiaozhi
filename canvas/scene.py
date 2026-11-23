"""Thread-safe, template-free drawing scene used by the XiaoZhi canvas tool."""

from __future__ import annotations

import copy
import logging
import math
import threading
import time


ALLOWED_TYPES = {
    "line", "polyline", "polygon", "bezier", "spline",
    "circle", "ellipse", "rectangle", "arc", "text",
}
DEFAULT_COLOR = "#334155"
log = logging.getLogger("canvas")


def _number(value, name: str, minimum: float = -320.0, maximum: float = 320.0) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{name} must be a finite number")
    return float(max(minimum, min(maximum, value)))


def _color(value, name: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a #RRGGBB color")
    value = value.strip()
    if len(value) != 7 or value[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in value[1:]):
        raise ValueError(f"{name} must be a #RRGGBB color")
    return value.lower()


def _point(value, name: str) -> list[float]:
    if isinstance(value, dict) and "x" in value and "y" in value:
        value = [value["x"], value["y"]]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be [x, y]")
    return [_number(value[0], f"{name}.x"), _number(value[1], f"{name}.y")]


def sanitize_command(raw: dict) -> dict:
    """Validate one model-authored drawing primitive.

    Coordinates remain relative to the user's selected anchor. Rendering is
    clipped to the canvas, so a malformed or over-large drawing cannot escape
    into the chat UI.
    """
    if not isinstance(raw, dict):
        raise ValueError("each command must be an object")
    kind = str(raw.get("type", "")).lower()
    if kind not in ALLOWED_TYPES:
        raise ValueError(f"unsupported drawing type: {kind or '<missing>'}")

    command = {
        "type": kind,
        "stroke": _color(raw.get("stroke", DEFAULT_COLOR), "stroke"),
        "fill": _color(raw.get("fill"), "fill", allow_none=True),
        "width": int(round(_number(raw.get("width", 4), "width", 1, 20))),
        "opacity": _number(raw.get("opacity", 1), "opacity", 0.1, 1),
    }
    if kind in {"line", "polyline", "polygon"}:
        points = raw.get("points")
        if kind == "line" and not isinstance(points, list):
            if "start" in raw and "end" in raw:
                points = [raw["start"], raw["end"]]
            elif all(key in raw for key in ("x1", "y1", "x2", "y2")):
                points = [[raw["x1"], raw["y1"]], [raw["x2"], raw["y2"]]]
            elif all(key in raw for key in ("x", "y", "x2", "y2")):
                points = [[raw["x"], raw["y"]], [raw["x2"], raw["y2"]]]
        required = 2 if kind != "polygon" else 3
        if not isinstance(points, list) or not required <= len(points) <= 96:
            raise ValueError(f"{kind}.points must contain {required}..96 points")
        command["points"] = [_point(point, f"points[{index}]") for index, point in enumerate(points)]
    elif kind in {"bezier", "spline"}:
        points = raw.get("points")
        valid_count = isinstance(points, list) and (
            len(points) in (3, 4) if kind == "bezier" else 3 <= len(points) <= 48
        )
        if not valid_count:
            expected = "3 or 4" if kind == "bezier" else "3..48"
            raise ValueError(f"{kind}.points must contain {expected} points")
        command["points"] = [_point(point, f"points[{index}]") for index, point in enumerate(points)]
        command["closed"] = bool(raw.get("closed", False)) if kind == "spline" else False
    elif kind in {"circle", "ellipse", "arc"}:
        command["x"] = _number(raw.get("x", 0), "x")
        command["y"] = _number(raw.get("y", 0), "y")
        if kind == "circle":
            command["r"] = _number(raw.get("r"), "r", 1, 220)
        else:
            command["rx"] = _number(raw.get("rx"), "rx", 1, 220)
            command["ry"] = _number(raw.get("ry"), "ry", 1, 260)
        if kind == "arc":
            command["start"] = _number(raw.get("start", 0), "start", -720, 720)
            command["end"] = _number(raw.get("end", 180), "end", -720, 720)
    elif kind == "rectangle":
        command["x"] = _number(raw.get("x", 0), "x")
        command["y"] = _number(raw.get("y", 0), "y")
        command["w"] = _number(raw.get("w"), "w", 1, 440)
        command["h"] = _number(raw.get("h"), "h", 1, 520)
        command["radius"] = int(round(_number(raw.get("radius", 0), "radius", 0, 80)))
    elif kind == "text":
        text = str(raw.get("text", ""))[:80]
        if not text:
            raise ValueError("text.text cannot be empty")
        command.update(
            x=_number(raw.get("x", 0), "x"),
            y=_number(raw.get("y", 0), "y"),
            text=text,
            size=int(round(_number(raw.get("size", 24), "size", 10, 72))),
        )
    return command


class CanvasScene:
    def __init__(self):
        self._lock = threading.Lock()
        self.width = 400
        self.height = 480
        self.selected: tuple[int, int] | None = None
        self.drawings: list[dict] = []
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def configure(self, width: int, height: int):
        with self._lock:
            width, height = max(1, int(width)), max(1, int(height))
            if (width, height) == (self.width, self.height):
                return

            def rescale(anchor):
                x, y = anchor
                return (round(x * (width - 1) / max(1, self.width - 1)),
                        round(y * (height - 1) / max(1, self.height - 1)))

            if self.selected is not None:
                self.selected = rescale(self.selected)
            for drawing in self.drawings:
                drawing["anchor"] = rescale(drawing["anchor"])
            self.width, self.height = width, height
            self._revision += 1

    def select(self, x: float, y: float) -> dict:
        with self._lock:
            self.selected = (
                max(0, min(self.width - 1, round(x))),
                max(0, min(self.height - 1, round(y))),
            )
            self._revision += 1
            return self._context_locked()

    def context(self) -> dict:
        with self._lock:
            return self._context_locked()

    def _context_locked(self) -> dict:
        return {
            "selected": list(self.selected) if self.selected else None,
            "canvas": {"width": self.width, "height": self.height},
            "coordinate_system": "command coordinates are pixel offsets from selected; +x right, +y down",
        }

    def draw(self, params: dict) -> dict:
        commands = params.get("commands") if isinstance(params, dict) else None
        if not isinstance(commands, list) or not 1 <= len(commands) <= 64:
            raise ValueError("commands must contain 1..64 drawing primitives")
        sanitized = []
        rejected = []
        for index, command in enumerate(commands):
            try:
                sanitized.append(sanitize_command(command))
            except ValueError as exc:
                rejected.append({"index": index, "error": str(exc)})
                keys = sorted(command) if isinstance(command, dict) else []
                log.warning("skipping invalid drawing primitive %d (%s): %s", index, keys, exc)
        if not sanitized:
            return {
                "ok": False,
                "error": "No valid drawing primitives were provided",
                "rejected": rejected,
            }
        with self._lock:
            if self.selected is None:
                raise ValueError("No canvas position selected. Ask the user to tap the canvas first.")
            drawing = {
                "anchor": self.selected,
                "commands": sanitized,
                "created_at": time.monotonic(),
                "duration": max(1.2, min(14.0, len(sanitized) * 0.5)),
                "label": str(params.get("label", "drawing"))[:60],
            }
            self.drawings.append(drawing)
            self._revision += 1
            if len(self.drawings) > 40:
                self.drawings.pop(0)
            return {
                "ok": True,
                "label": drawing["label"],
                "anchor": list(self.selected),
                "primitive_count": len(sanitized),
                "rejected_count": len(rejected),
                "rejected": rejected,
            }

    def undo(self, _params: dict | None = None) -> dict:
        with self._lock:
            removed = bool(self.drawings)
            if removed:
                self.drawings.pop()
                self._revision += 1
            return {"ok": True, "removed": removed, "remaining": len(self.drawings)}

    def clear(self, _params: dict | None = None) -> dict:
        with self._lock:
            count = len(self.drawings)
            self.drawings.clear()
            if count:
                self._revision += 1
            return {"ok": True, "removed": count}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "width": self.width,
                "height": self.height,
                "selected": self.selected,
                "drawings": copy.deepcopy(self.drawings),
            }


DRAW_TOOL_DESCRIPTION = """Draw the object requested by the user on the slide-out canvas at the position they tapped.
You are the illustrator: compose the requested object yourself from the generic primitives in `commands`; the device has no built-in object templates.
All x/y values and all points are pixel OFFSETS relative to the selected position, with +x right and +y down. The selected position is the VISUAL CENTER of the requested object, so balance its overall bounding box around [0,0], not around a top-left corner. Keep normal drawings roughly within -140..140 on each axis.
Use layered color, varied stroke widths, filled silhouettes, highlights, shadows, and small expressive details. Prefer bezier or spline for organic outlines, faces, limbs, clouds, plants, and creatures; use geometric primitives where they improve structure. A polished illustration normally uses 10..30 purposeful primitives ordered back-to-front. Call this tool whenever the user asks to draw, sketch, or add something at the selected place.
Call the tool once with the complete drawing. Every command must include the required geometry fields described in its schema. Do not repeatedly promise to draw; call the tool and only confirm after it returns `ok: True`.
Supported types:
- line/polyline/polygon: points [[x,y],...]
- bezier: 3 points for quadratic or 4 for cubic curves
- spline: 3..48 points for a smooth organic path; closed=true allows fill
- circle: x,y,r
- ellipse/arc: x,y,rx,ry (arc also uses start/end degrees)
- rectangle: x,y,w,h,radius
- text: x,y,text,size
Every primitive accepts stroke #RRGGBB, optional fill #RRGGBB, width 1..20, and opacity 0.1..1."""


DRAW_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Short name for what you drew"},
        "commands": {
            "type": "array",
            "minItems": 1,
            "maxItems": 64,
            "description": "Generic drawing primitives using coordinates relative to the selected touch point",
            "items": {
                "type": "object",
                "properties": {
                    "stroke": {"type": "string", "description": "outline color as #RRGGBB"},
                    "fill": {"type": "string", "description": "optional fill color as #RRGGBB"},
                    "width": {"type": "number", "description": "outline width from 1 to 20"},
                    "opacity": {"type": "number", "minimum": 0.1, "maximum": 1},
                },
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["line"]},
                            "points": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
                        },
                        "required": ["type", "points"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["bezier"]},
                            "points": {"type": "array", "minItems": 3, "maxItems": 4, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
                        },
                        "required": ["type", "points"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["spline"]},
                            "points": {"type": "array", "minItems": 3, "maxItems": 48, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
                            "closed": {"type": "boolean"},
                        },
                        "required": ["type", "points"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["polyline"]},
                            "points": {"type": "array", "minItems": 2, "maxItems": 96, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
                        },
                        "required": ["type", "points"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["polygon"]},
                            "points": {"type": "array", "minItems": 3, "maxItems": 96, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
                        },
                        "required": ["type", "points"],
                    },
                    {"type": "object", "properties": {"type": {"type": "string", "enum": ["circle"]}, "x": {"type": "number"}, "y": {"type": "number"}, "r": {"type": "number"}}, "required": ["type", "r"]},
                    {"type": "object", "properties": {"type": {"type": "string", "enum": ["ellipse"]}, "x": {"type": "number"}, "y": {"type": "number"}, "rx": {"type": "number"}, "ry": {"type": "number"}}, "required": ["type", "rx", "ry"]},
                    {"type": "object", "properties": {"type": {"type": "string", "enum": ["rectangle"]}, "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"}, "h": {"type": "number"}, "radius": {"type": "number"}}, "required": ["type", "w", "h"]},
                    {"type": "object", "properties": {"type": {"type": "string", "enum": ["arc"]}, "x": {"type": "number"}, "y": {"type": "number"}, "rx": {"type": "number"}, "ry": {"type": "number"}, "start": {"type": "number"}, "end": {"type": "number"}}, "required": ["type", "rx", "ry", "start", "end"]},
                    {"type": "object", "properties": {"type": {"type": "string", "enum": ["text"]}, "x": {"type": "number"}, "y": {"type": "number"}, "text": {"type": "string"}, "size": {"type": "number"}}, "required": ["type", "text"]},
                ],
            },
        },
    },
    "required": ["commands"],
}
