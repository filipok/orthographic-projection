"""The shape a map is cut to: the globe's disc, or a rectangle.

A globe, and a radius map by default, is a disc filling its square frame. A
radius map can instead be cut to a rectangle (``--rectangle``), and every
layer that keeps names, arrows or sample points on the map asks here what
"on the map" means, rather than assuming the disc.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

_RECTANGLE = "_ortho_rectangle"
_ASPECT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:[:x×]\s*(\d+(?:\.\d+)?)\s*)?$")
# Wider or taller than this is a strip rather than a map
MAX_ASPECT = 4.0


def parse_aspect(text: str) -> float:
    """Width over height from ``"3:2"``, ``"2:3"``, ``"1.414:1"`` or ``"1.5"``; ValueError for anything else."""
    found = _ASPECT.match(str(text))
    width, height = (float(found[1]), float(found[2] or 1)) if found else (0.0, 0.0)
    if not width or not height:
        raise ValueError(f"a rectangle is written width:height, such as 3:2 or 2:3, not {text!r}")
    aspect = width / height
    if not 1 / MAX_ASPECT <= aspect <= MAX_ASPECT:
        raise ValueError(f"a rectangle can be at most {MAX_ASPECT:g} times wider than tall or taller than wide, "
                         f"not {text}")
    return aspect


def half_sides(half: float, aspect: float) -> tuple[float, float]:
    """Half the width and height of a rectangle of *aspect* whose longer side is ``2 * half``."""
    return (half, half / aspect) if aspect >= 1 else (half * aspect, half)


def max_rectangle_radius_km(aspect: float, earth_radius_km: float) -> float:
    """The largest radius whose rectangle of *aspect* keeps its corners on the globe.

    The rectangle's longer side is the radius map's diameter, so its corners
    lie further out than the radius by the diagonal over the longer side.
    """
    long, short = max(aspect, 1 / aspect), 1.0
    return earth_radius_km * math.asin(long / math.hypot(long, short))


def set_rectangle(ax: Any) -> None:
    """Mark *ax* as cut to a rectangle, so the layers use all of its frame."""
    setattr(ax, _RECTANGLE, True)


def is_rectangle(ax: Any) -> bool:
    """True if *ax* is cut to a rectangle, False for a disc."""
    return bool(getattr(ax, _RECTANGLE, False))


def coverage(ax: Any) -> float:
    """The share of its frame the map on *ax* covers: all of it for a rectangle, π/4 for a disc."""
    return 1.0 if is_rectangle(ax) else math.pi / 4


def inside(ax: Any, x: Any, y: Any, margin: float = 1.0) -> np.ndarray:
    """True where (*x*, *y*), in the axes' projection, lies on the map, shrunk to *margin* of its size.

    The map is the disc inside the frame, or the whole frame for a rectangle.
    """
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    with np.errstate(invalid="ignore"):
        u = (2 * np.asarray(x, float) - x0 - x1) / (x1 - x0)
        v = (2 * np.asarray(y, float) - y0 - y1) / (y1 - y0)
        if is_rectangle(ax):
            return (np.abs(u) <= margin) & (np.abs(v) <= margin)
        return u ** 2 + v ** 2 <= margin ** 2


def box_inside(ax: Any, box: tuple[float, float, float, float], margin: float = 1.0) -> bool:
    """True if the box ``(x0, y0, x1, y1)``, in display pixels, lies wholly on the map, shrunk to *margin*.

    The disc is the circle as wide as the frame; a rectangle is the frame.
    """
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    (px0, py0), (px1, py1) = ax.transData.transform([(x0, y0), (x1, y1)])
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    corners = [(box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3])]
    if is_rectangle(ax):
        half_w, half_h = margin * abs(px1 - px0) / 2, margin * abs(py1 - py0) / 2
        return all(abs(x - cx) <= half_w and abs(y - cy) <= half_h for x, y in corners)
    radius = margin * abs(px1 - px0) / 2
    return all((x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 for x, y in corners)
