"""Route overlays loaded from GeoJSON files.

A route file is a GeoJSON ``FeatureCollection``, ``Feature`` or bare
geometry containing ``LineString`` / ``MultiLineString`` geometries, with
coordinates as ``[lon, lat]`` in degrees.  ``Polygon`` / ``MultiPolygon``
geometries are drawn as translucent filled areas.  Styling follows the
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
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.artist import Artist
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

DEFAULT_ROUTE_COLOR = "#ff0000"
DEFAULT_ROUTE_WIDTH = 2.0
DEFAULT_FILL_OPACITY = 0.35

Line = tuple[tuple[float, float], ...]
# A polygon as its rings: the exterior first, then any holes.
Area = tuple[Line, ...]


@dataclass(frozen=True)
class Route:
    """A named, styled set of polylines and filled areas in lon/lat degrees.

    ``fill`` defaults to ``color`` when not given.  ``legend`` is the label
    shown in the route key; ``name`` is used when it is not given.
    """

    name: str
    lines: tuple[Line, ...]
    color: str = DEFAULT_ROUTE_COLOR
    linewidth: float = DEFAULT_ROUTE_WIDTH
    areas: tuple[Area, ...] = ()
    fill: str | None = None
    fill_opacity: float = DEFAULT_FILL_OPACITY
    legend: str | None = None


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


def _parse_ring(coords: Any, where: str) -> Line:
    if not isinstance(coords, list) or len(coords) < 4:
        raise ValueError(f"{where}: a polygon ring needs at least 4 positions")
    ring = tuple(_parse_position(pos, where) for pos in coords)
    if ring[0] != ring[-1]:
        raise ValueError(f"{where}: polygon ring is not closed (first and last positions differ)")
    return ring


def _parse_polygon(coords: Any, where: str) -> Area:
    if not isinstance(coords, list) or not coords:
        raise ValueError(f"{where}: Polygon has no rings")
    return tuple(_parse_ring(ring, where) for ring in coords)


def _parse_geometry(geometry: Any, where: str) -> tuple[tuple[Line, ...], tuple[Area, ...]]:
    """Return ``(lines, areas)`` for a supported geometry."""
    if not isinstance(geometry, dict):
        raise ValueError(f"{where}: missing geometry")
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "LineString":
        return (_parse_line(coords, where),), ()
    if gtype == "MultiLineString":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{where}: MultiLineString has no lines")
        return tuple(_parse_line(part, where) for part in coords), ()
    if gtype == "Polygon":
        return (), (_parse_polygon(coords, where),)
    if gtype == "MultiPolygon":
        if not isinstance(coords, list) or not coords:
            raise ValueError(f"{where}: MultiPolygon has no polygons")
        return (), tuple(_parse_polygon(part, where) for part in coords)
    raise ValueError(
        f"{where}: unsupported geometry type {gtype!r} "
        "(expected LineString, MultiLineString, Polygon or MultiPolygon)"
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

    fill = props.get("fill", color)
    if not mcolors.is_color_like(fill):
        raise ValueError(f"{where}: invalid fill colour {fill!r}")

    opacity = props.get("fill-opacity", DEFAULT_FILL_OPACITY)
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not 0 <= opacity <= 1:
        raise ValueError(f"{where}: fill-opacity must be a number from 0 to 1, got {opacity!r}")

    legend = props.get("legend")
    if legend is not None and (not isinstance(legend, str) or not legend.strip()):
        raise ValueError(f"{where}: legend must be a non-empty string, got {legend!r}")

    lines, areas = _parse_geometry(feature.get("geometry"), where)
    return Route(
        name=str(props.get("name") or default_name),
        lines=lines,
        color=color,
        linewidth=float(width),
        areas=areas,
        fill=fill,
        fill_opacity=float(opacity),
        legend=legend.strip() if legend else None,
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
    """Draw *routes* on *ax* as great-circle polylines and filled areas.

    Segments between vertices follow the geodesic (``ccrs.Geodetic``), so
    sparse routes still curve correctly on the globe.  Areas are drawn just
    below the lines so routes stay visible across them.
    """
    for route in routes:
        if route.areas:
            ax.add_geometries(
                [Polygon(area[0], area[1:]) for area in route.areas],
                crs=ccrs.PlateCarree(),
                facecolor=mcolors.to_rgba(route.fill or route.color, route.fill_opacity),
                edgecolor=route.color,
                linewidth=route.linewidth,
                zorder=zorder - 1,
            )
        for line in route.lines:
            lons, lats = zip(*line)
            ax.plot(
                lons, lats,
                color=route.color,
                linewidth=route.linewidth,
                transform=ccrs.Geodetic(),
                zorder=zorder,
            )
        logger.info(
            "Drew route '%s' (%d line(s), %d area(s)).",
            route.name, len(route.lines), len(route.areas),
        )


def _legend_handle(route: Route) -> Artist:
    """A key swatch styled like *route*: a line, or a filled box for areas."""
    if route.lines:
        # A dark outline keeps pale lines (white, yellow) visible on the light key
        width = max(route.linewidth, 2.0)
        return Line2D(
            [], [], color=route.color, linewidth=width,
            path_effects=[pe.Stroke(linewidth=width + 1.5, foreground="#333333"), pe.Normal()],
        )
    return mpatches.Patch(
        facecolor=mcolors.to_rgba(route.fill or route.color, route.fill_opacity),
        edgecolor=route.color,
        linewidth=route.linewidth,
    )


def add_route_legend(
    ax: GeoAxes, routes: Sequence[Route], y: float = -0.01, x: float = 0.5,
) -> Legend | None:
    """Add a key below the globe naming each route next to its colour.

    Each entry's label is the route's ``legend``, else its ``name``; routes
    that share a label share one entry, styled like the first of them.
    Entries are listed top layer first, the reverse of drawing order, as in
    a GIS layer list.  *y* is the key's top edge and *x* its horizontal
    centre, in axes coordinates.

    The key is added as a separate artist, so it does not replace another
    legend on *ax* (such as the Köppen-Geiger one). Returns the key, or
    ``None`` when there are no routes.
    """
    entries: dict[str, Artist] = {}
    for route in reversed(routes):
        entries.setdefault(route.legend or route.name, _legend_handle(route))
    if not entries:
        return None

    legend = Legend(
        ax,
        list(entries.values()),
        list(entries),
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=1 if len(entries) < 4 else 2,
        fontsize=11,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=2.5,
        columnspacing=2.0,
        borderpad=0.7,
        labelspacing=0.6,
    )
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
