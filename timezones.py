"""Time zones: the world's standard-time zones, shaded in turn and labelled by their offset.

Data source
-----------
Natural Earth 1:10m time zones (public domain): the zones of standard time,
with their offset from UTC; daylight saving time is not shown. Cartopy
downloads them once.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import shapely

import cartopy.crs as ccrs
import cartopy.io.shapereader as shapereader
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection

from biomes import burn, on_globe
from hydrology import project_lines

logger = logging.getLogger(__name__)

TIMEZONES_ATTRIBUTION = "Time zones: Natural Earth"

# Zones alternate between two shades as the offset steps by an hour; half-hour zones get a third
SHADES = ("#d9c8a5", "#b8cfe0", "#c8b8d8")


class TimeZoneDataError(RuntimeError):
    """The time zones could not be read."""


def load_time_zones() -> list[tuple[float, str, shapely.Geometry]]:
    """``(offset in hours, label, outline)`` of each zone, from Natural Earth."""
    try:
        reader = shapereader.Reader(shapereader.natural_earth("10m", "cultural", "time_zones"))
        zones = []
        for record in reader.records():
            attributes: dict[str, Any] = dict(record.attributes)
            if record.geometry is None:
                continue
            zones.append((float(attributes["zone"]), str(attributes["time_zone"]),
                          shapely.orient_polygons(record.geometry)))
    except Exception as exc:
        raise TimeZoneDataError(f"Could not read the Natural Earth time zones: {exc}") from exc
    return zones


def shade(offset: float) -> str:
    """The zone's fill: whole hours alternate between two shades, other offsets take a third."""
    if offset != int(offset):
        return SHADES[2]
    return SHADES[int(offset) % 2]


def zone_grid(zones: list[tuple[float, str, shapely.Geometry]], cells_per_degree: int = 10) -> np.ndarray:
    """A global grid of each cell's shade (index into :data:`SHADES`, plus one; 0 outside every zone).

    Filled with :func:`biomes.burn`, so the zones never go through Cartopy's
    polygon clipping, which fails on some of them near the poles.
    """
    grid = np.zeros((180 * cells_per_degree, 360 * cells_per_degree), np.uint8)
    for offset, _, outline in zones:
        value = SHADES.index(shade(offset)) + 1
        for polygon in shapely.get_parts(outline):
            rings = [np.asarray(polygon.exterior.coords)[:, :2]] + [np.asarray(r.coords)[:, :2]
                                                                     for r in polygon.interiors]
            points = np.concatenate(rings)
            parts = list(np.cumsum([0] + [len(r) for r in rings[:-1]]))
            burn(grid, points, parts, value, cells_per_degree)
    return grid


def draw_time_zones(ax: GeoAxes, zones: list[tuple[float, str, shapely.Geometry]], alpha: float = 0.45,
                    regrid_shape: int = 750) -> None:
    """Shade the zones on *ax*, outline them and label each in sight with its offset."""
    palette = np.zeros((256, 4), np.uint8)
    for i, colour in enumerate(SHADES):
        r, g, b = (int(colour[k:k + 2], 16) for k in (1, 3, 5))
        palette[i + 1] = (r, g, b, round(alpha * 255))
    ax.imshow(palette[zone_grid(zones)], origin="upper", extent=(-180, 180, -90, 90), transform=ccrs.PlateCarree(),
              interpolation="nearest", regrid_shape=regrid_shape, zorder=5)
    lines = [run for _, _, outline in zones for run in project_lines(ax, shapely.boundary(outline))]
    ax.add_collection(LineCollection(lines, colors="#6a5a40", linewidths=0.6, zorder=6))
    labelled: set[str] = set()
    for offset, label, outline in sorted(zones, key=lambda z: -float(shapely.area(z[2]))):
        if label in labelled:
            continue
        parts = sorted(shapely.get_parts(outline), key=lambda p: -float(shapely.area(p)))
        point = parts[0].representative_point()
        if on_globe(ax, point.x, point.y, margin=0.9):
            labelled.add(label)
            text = label.replace("UTC", "UTC ").replace(":00", "") if offset else "UTC"
            ax.text(point.x, point.y, text, transform=ccrs.PlateCarree(), fontsize=10, color="#3a2a10",
                    ha="center", va="center", zorder=8,
                    path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
    logger.info("Time zones drawn (%d labelled).", len(labelled))


def offsets_in(zones: list[tuple[float, str, shapely.Geometry]]) -> np.ndarray:
    """The distinct offsets of *zones*, sorted."""
    return np.unique([offset for offset, _, _ in zones])
