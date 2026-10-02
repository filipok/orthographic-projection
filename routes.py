"""Route overlays loaded from GeoJSON files.

A route file is a GeoJSON ``FeatureCollection``, ``Feature`` or bare
geometry containing ``LineString`` / ``MultiLineString`` geometries, with
coordinates as ``[lon, lat]`` in degrees.  Styling follows the
`simplestyle-spec <https://github.com/mapbox/simplestyle-spec>`_ property
names so the same file renders sensibly on geojson.io and GitHub::

    {
      "type": "Feature",
      "properties": {"name": "My route", "stroke": "#ff0000", "stroke-width": 2},
      "geometry": {"type": "LineString", "coordinates": [[-5.35, 36.14], [-14.36, -7.95]]}
    }
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import matplotlib.colors as mcolors
import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes

logger = logging.getLogger(__name__)

DEFAULT_ROUTE_COLOR = "#ff0000"
DEFAULT_ROUTE_WIDTH = 2.0

Line = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Route:
    """A named, styled set of polylines in lon/lat degrees."""

    name: str
    lines: tuple[Line, ...]
    color: str = DEFAULT_ROUTE_COLOR
    linewidth: float = DEFAULT_ROUTE_WIDTH


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_position(pos: Any, where: str) -> tuple[float, float]:
    if not isinstance(pos, list) or len(pos) < 2:
        raise ValueError(f"{where}: position {pos!r} is not [lon, lat]")
    lon, lat = pos[0], pos[1]
    for value in (lon, lat):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{where}: position {pos!r} has a non-numeric coordinate")
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"{where}: position {pos!r} is out of range (expected [lon, lat])")
    return float(lon), float(lat)


def _parse_line(coords: Any, where: str) -> Line:
    if not isinstance(coords, list) or len(coords) < 2:
        raise ValueError(f"{where}: a line needs at least 2 positions")
    return tuple(_parse_position(pos, where) for pos in coords)


def _parse_geometry(geometry: Any, where: str) -> tuple[Line, ...]:
    if not isinstance(geometry, dict):
        raise ValueError(f"{where}: missing geometry")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "LineString":
        return (_parse_line(coords, where),)
    if gtype == "MultiLineString":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{where}: MultiLineString has no lines")
        return tuple(_parse_line(part, where) for part in coords)
    raise ValueError(
        f"{where}: unsupported geometry type {gtype!r} (expected LineString or MultiLineString)"
    )


def _parse_feature(feature: Any, default_name: str, where: str) -> Route:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise ValueError(f"{where}: expected a GeoJSON Feature")
    props = feature.get("properties") or {}

    color = props.get("stroke", DEFAULT_ROUTE_COLOR)
    if not mcolors.is_color_like(color):
        raise ValueError(f"{where}: invalid stroke colour {color!r}")

    width = props.get("stroke-width", DEFAULT_ROUTE_WIDTH)
    if isinstance(width, bool) or not isinstance(width, (int, float)) or not width > 0:
        raise ValueError(f"{where}: stroke-width must be a positive number, got {width!r}")

    return Route(
        name=str(props.get("name") or default_name),
        lines=_parse_geometry(feature.get("geometry"), where),
        color=color,
        linewidth=float(width),
    )


def load_routes(path: str) -> list[Route]:
    """Load every route in the GeoJSON file at *path*.

    Raises ``OSError`` if the file cannot be read and ``ValueError`` if it
    is not valid route GeoJSON.
    """
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON ({exc})") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a GeoJSON object")

    kind = data.get("type")
    if kind == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise ValueError(f"{path}: FeatureCollection has no 'features' list")
    elif kind == "Feature":
        features = [data]
    else:
        features = [{"type": "Feature", "properties": {}, "geometry": data}]

    stem = os.path.splitext(os.path.basename(path))[0]
    routes = [
        _parse_feature(
            feature,
            default_name=stem if len(features) == 1 else f"{stem}[{i}]",
            where=f"{path} feature {i}",
        )
        for i, feature in enumerate(features)
    ]
    if not routes:
        raise ValueError(f"{path}: no routes found")

    logger.debug("Loaded %d route(s) from %s", len(routes), path)
    return routes


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def draw_routes(ax: GeoAxes, routes: Sequence[Route], zorder: float = 9) -> None:
    """Draw *routes* on *ax* as great-circle polylines.

    Segments between vertices follow the geodesic (``ccrs.Geodetic``), so
    sparse routes still curve correctly on the globe.
    """
    for route in routes:
        for line in route.lines:
            lons, lats = zip(*line)
            ax.plot(
                lons, lats,
                color=route.color,
                linewidth=route.linewidth,
                transform=ccrs.Geodetic(),
                zorder=zorder,
            )
        logger.info("Drew route '%s' (%d line(s)).", route.name, len(route.lines))
