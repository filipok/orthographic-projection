"""Geology: the age or the kind of the rocks at the surface, from Macrostrat's tiles.

Data source
-----------
Macrostrat (https://macrostrat.org/), Peters, S. E., Husson, J. M. &
Czaplewski, J. (2018). Macrostrat: A platform for geological data integration
and deep-time Earth crust research. *Geochemistry, Geophysics, Geosystems* 19,
1393-1409. https://doi.org/10.1029/2018GC007467 — its ``carto`` vector tiles,
CC BY 4.0, which join published geological maps; each unit names its own map,
and the credit names the maps on show.

A whole globe is drawn from zoom 2, where the tiles hold one map of the world
(Chorlton 2007, Geological Survey of Canada, Open File 5529), so continents
compare. Zoom 3 and up, used for closer maps, join regional maps of different
detail (Europe's is Asch 2003, 1:5,000,000), and their joins can show. Many of
them draw the rocks under the young (Quaternary) cover, so loess and river
deposits are often missing.

Age
---
A unit's age is a pair of intervals, oldest and youngest, given by Macrostrat's
interval numbers; the ages in years in the tiles follow older limits (the
Cretaceous from 145.0 Ma, now 143.1) and are not used. A unit is coloured by
the shortest interval that holds its whole age: an epoch of the Cenozoic, a
period, an era or an eon; before the Cambrian an era or an eon, or else the
Precambrian. Two spans join the eras on either side of the Precambrian's end,
for the units that straddle it (:data:`SPANS`, over 1% of the land). The
colours are the International Commission on Stratigraphy's, from Macrostrat,
but for those two, which have none. Ice, units without an age and the few that
run from the Precambrian past the Paleozoic stay clear.

Rock
----
The tiles list a unit's classes of rock in alphabetical order, not by how
common they are, so a unit gets a class only when it has just one, and is mixed
when it has several; units with none stay clear.

The tiles are cached under ``~/.cache/ortho_tiles/geology/`` as they arrive.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import math
import os
import re
import struct
import urllib.request
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, NamedTuple

import numpy as np

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

import incomplete
from biomes import _hex_rgb, _key, _swatch, burn, visible_points
from climate import MAX_CELLS, View, land_mask, visible_box
from gbif import _fields, _tag_pairs, _varint

logger = logging.getLogger(__name__)

TILE_URL = "https://tiles.macrostrat.org/carto/{z}/{x}/{y}.mvt"
INTERVALS_URL = "https://macrostrat.org/api/v2/defs/intervals?all&format=json"
DATA_VERSION = 1
MODES = ("age", "rock")
DEFAULT_ALPHA = 0.7

GLOBE_ZOOM = 2            # one map of the whole world; from zoom 3 the tiles join regional maps
CLOSE_ZOOMS = (5, 4, 3)   # for closer maps, the first that needs at most MAX_TILES
MAX_TILES = 64
GLOBE_CELL = 0.05         # degrees
MIN_CELL = 0.01
FINE_COAST_CELL = 0.03    # below this the land mask uses the 1:10m coastline
MERCATOR_LAT = 85.0511    # the tiles end here

ATTRIBUTION = "Geology: {maps}, via Macrostrat (Peters et al. 2018), CC BY 4.0"

# Kind of rock -> (R, G, B), in key order
ROCKS: dict[str, tuple[int, int, int]] = {
    "Sedimentary": (245, 222, 140),
    "Volcanic": (220, 80, 60),
    "Plutonic": (240, 150, 190),
    "Other igneous": (150, 90, 60),
    "Metamorphic": (150, 130, 190),
    "Mixed": (185, 185, 175),
}
# Spans across the end of the Precambrian: (the interval that starts it, the one that ends it, (R, G, B))
SPANS: tuple[tuple[str, str, tuple[int, int, int]], ...] = (
    ("Neoproterozoic", "Paleozoic", (190, 150, 140)),
    ("Precambrian", "Paleozoic", (140, 95, 105)),
)
_POLYGON = 3              # the vector tile geometry type
_AGE_SLACK = 0.02         # million years: limits that differ only by rounding are the same
_LAST_PRECAMBRIAN = 538.0 # an interval that ends before this is Precambrian


class GeologyDataError(RuntimeError):
    """The geology tiles or the table of intervals could not be downloaded or read."""


class Interval(NamedTuple):
    name: str
    oldest: float             # million years ago
    youngest: float
    colour: tuple[int, int, int]


class Unit(NamedTuple):
    """A polygon of a tile: its tags, and its rings in fractions of the tile, east and south from its corner."""
    tags: dict[str, Any]
    rings: list[np.ndarray]


class Entry(NamedTuple):
    """What a cell of the grid holds: a class, and the map it comes from."""
    name: str
    colour: tuple[int, int, int]
    source: str


class Layer(NamedTuple):
    """The geology around what a map shows, in longitudes relative to *centre_lon*."""
    cells: np.ndarray                              # uint16, rows north to south: 0 nothing, else an entry's number + 1
    entries: list[Entry]
    extent: tuple[float, float, float, float]      # west, east, south, north
    centre_lon: float


def resolve_geology(mode: str | None) -> str:
    """``"age"`` or ``"rock"`` for *mode* (``None`` or blank: age), by its name or the start of it.

    Raises ``ValueError`` for any other.
    """
    key = (mode or MODES[0]).strip().lower() or MODES[0]
    for name in MODES:
        if name.startswith(key):
            return name
    raise ValueError(f"Unknown geology {mode!r}; choose age or rock.")


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "geology")


def _get(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _save(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    with open(part, "wb") as fh:
        fh.write(data)
    os.replace(part, path)


def read_intervals(text: str | bytes) -> dict[int, Interval]:
    """Macrostrat's interval table: number -> interval."""
    return {int(row["int_id"]): Interval(str(row["name"]), float(row["b_age"]), float(row["t_age"]),
                                         _hex_rgb(str(row["color"])))
            for row in json.loads(text)["success"]["data"] if row.get("color")}


def read_classes(text: str | bytes) -> list[Interval]:
    """The intervals a unit may be coloured by, shortest first: see the module's notes on age."""
    rows: dict[str, Interval] = {}
    for row in json.loads(text)["success"]["data"]:
        ranks = {scale["name"] for scale in row.get("timescales") or []}
        oldest, youngest = float(row["b_age"]), float(row["t_age"])
        cenozoic_epoch = "international epochs" in ranks and oldest <= 66 + _AGE_SLACK
        period = "international periods" in ranks and youngest < _LAST_PRECAMBRIAN
        if cenozoic_epoch or period or ranks & {"international eras", "international eons"} or row["name"] == "Precambrian":
            rows[row["name"]] = Interval(str(row["name"]), oldest, youngest, _hex_rgb(str(row["color"])))
    if not rows:
        raise ValueError("no epochs, periods, eras or eons in it")
    for first, last, colour in SPANS:
        if first in rows and last in rows:
            name = f"{first} to {last}"
            rows[name] = Interval(name, rows[first].oldest, rows[last].youngest, colour)
    return sorted(rows.values(), key=lambda i: i.oldest - i.youngest)


def load_intervals(cache_dir: str | None = None) -> tuple[dict[int, Interval], list[Interval]]:
    """Macrostrat's intervals by number, and those units are coloured by; the table is downloaded once (~0.6 MB)."""
    path = os.path.join(cache_dir or _default_cache_dir(), f"intervals_v{DATA_VERSION}.json")
    try:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                data = fh.read()
            return read_intervals(data), read_classes(data)
        data = _get(INTERVALS_URL)
        table = read_intervals(data), read_classes(data)      # read before it is kept
        _save(path, data)
        return table
    except Exception as exc:                                  # network and table errors alike
        raise GeologyDataError(f"Could not read Macrostrat's table of intervals: {exc}") from exc


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------


def _value(message: bytes) -> str | int | float | None:
    """The text or number in a vector tile's value message."""
    for field, value in _fields(message):
        if field == 1:
            return value.decode("utf-8", "replace")
        if field == 2:
            return float(struct.unpack("<f", value)[0])
        if field == 3:
            return float(struct.unpack("<d", value)[0])
        if field in (4, 5, 7):
            return int(value)
        if field == 6:
            return (value >> 1) ^ -(value & 1)
    return None


def _rings(geometry: bytes, extent: int) -> list[np.ndarray]:
    """The rings of a polygon's geometry, as (east, south) fractions of the tile."""
    rings: list[list[tuple[int, int]]] = []
    x = y = i = 0
    while i < len(geometry):
        command, i = _varint(geometry, i)
        action, count = command & 7, command >> 3
        if action == 7:                                        # ClosePath
            continue
        for _ in range(count):
            dx, i = _varint(geometry, i)
            dy, i = _varint(geometry, i)
            x += (dx >> 1) ^ -(dx & 1)
            y += (dy >> 1) ^ -(dy & 1)
            if action == 1:                                    # MoveTo
                rings.append([])
            rings[-1].append((x, y))
    return [np.array(ring, float) / extent for ring in rings if len(ring) > 2]


def read_tile(data: bytes) -> list[Unit]:
    """The geological units in a Macrostrat tile."""
    units = []
    for field, layer in _fields(data):
        if field != 3:
            continue
        name, keys, values, features, extent = "", [], [], [], 4096
        for part, value in _fields(layer):
            if part == 1:
                name = value.decode()
            elif part == 2:
                features.append(value)
            elif part == 3:
                keys.append(value.decode())
            elif part == 4:
                values.append(_value(value))
            elif part == 5:
                extent = value
        if name != "units":                                    # "lines" are faults and the like
            continue
        for feature in features:
            tags, geometry, kind = b"", b"", 0
            for part, value in _fields(feature):
                if part == 2:
                    tags = value
                elif part == 3:
                    kind = value
                elif part == 4:
                    geometry = value
            if kind == _POLYGON:
                units.append(Unit({keys[k]: values[v] for k, v in _tag_pairs(tags)}, _rings(geometry, extent)))
    return units


def _load_tile(z: int, x: int, y: int, cache_dir: str) -> list[Unit] | Exception:
    """The units of one tile, or what went wrong; a tile is kept only once it has been read."""
    path = os.path.join(cache_dir, "carto", str(z), f"{x}_{y}.mvt")
    try:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return read_tile(fh.read())
        data = _get(TILE_URL.format(z=z, x=x, y=y))
        units = read_tile(data)
        _save(path, data)
        return units
    except Exception as exc:                                   # network and tile errors alike
        return exc


def _row(lat: float, z: int) -> float:
    """The tile row (and fraction) of *lat* at zoom *z*."""
    lat = math.radians(max(-MERCATOR_LAT, min(MERCATOR_LAT, lat)))
    return (1 - math.asinh(math.tan(lat)) / math.pi) / 2 * 2 ** z


def tiles_over(view: View, z: int) -> list[tuple[int, int]]:
    """The (x, y) of the tiles at zoom *z* that *view* touches, each once."""
    n = 2 ** z
    west, east = view.centre_lon + view.west, view.centre_lon + view.east
    first, last = math.floor((west + 180) / 360 * n), math.ceil((east + 180) / 360 * n)
    columns = sorted({x % n for x in range(first, max(last, first + 1))})
    top, bottom = math.floor(_row(view.north, z)), math.ceil(_row(view.south, z))
    rows = range(max(0, min(top, n - 1)), max(1, min(bottom, n)))
    return [(x, y) for x in columns for y in rows]


def close_zoom(view: View) -> int:
    """The finest zoom of a closer map that needs at most :data:`MAX_TILES` tiles."""
    for z in CLOSE_ZOOMS:
        if len(tiles_over(view, z)) <= MAX_TILES:
            return z
    return CLOSE_ZOOMS[-1]


def load_tiles(tiles: Sequence[tuple[int, int]], z: int, cache_dir: str | None = None
               ) -> Iterator[tuple[int, int, list[Unit]]]:
    """The (x, y, units) of each tile that could be had; the others are noted, and never kept.

    Raises :class:`GeologyDataError` if none could be.
    """
    folder = cache_dir or _default_cache_dir()
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        found = list(pool.map(lambda t: _load_tile(z, *t, folder), tiles))
    failed = [result for result in found if isinstance(result, Exception)]
    if failed and len(failed) == len(found):
        raise GeologyDataError(f"Could not read the geology tiles: {failed[0]}") from failed[0]
    if failed:
        incomplete.note(logger, "%d of %d geology tiles could not be downloaded (first error: %s).",
                        len(failed), len(found), failed[0])
    for (x, y), result in zip(tiles, found):
        if not isinstance(result, Exception):
            yield x, y, result


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


def age_class(tags: dict[str, Any], intervals: dict[int, Interval], classes: Sequence[Interval]) -> Interval | None:
    """The shortest of *classes* (sorted so) that holds the whole age of the unit with *tags*."""
    oldest, youngest = intervals.get(tags.get("b_int_id", -1)), intervals.get(tags.get("t_int_id", -1))
    if oldest is None or youngest is None:
        return None
    for interval in classes:
        if (interval.oldest + _AGE_SLACK >= oldest.oldest
                and interval.youngest - _AGE_SLACK <= youngest.youngest):
            return interval
    return None


def rock_class(tags: dict[str, Any]) -> str | None:
    """The kind of rock, one of :data:`ROCKS`, of the unit with *tags*; ``None`` if it gives none."""
    classes = {str(tags.get(f"lith_class{i}") or "") for i in range(1, 4)} - {""}
    if not classes:
        return None
    if len(classes) > 1:
        return "Mixed"
    (only,) = classes
    if only == "igneous":
        types = {str(tags.get(f"lith_type{i}") or "") for i in range(1, 14)} - {"", "igneous"}
        return {("volcanic",): "Volcanic", ("plutonic",): "Plutonic"}.get(tuple(sorted(types)), "Other igneous")
    return only.capitalize() if only.capitalize() in ROCKS else None


def source_name(tags: dict[str, Any]) -> str:
    """The map a unit comes from, as a credit: ``Europe 1:5M (Asch, K. 2003)``."""
    authors = re.sub(r"\s+", " ", str(tags.get("ref_authors") or "")).strip(" ,")
    if len(authors) > 45:
        authors = authors.split(",")[0] + " et al."
    details = " ".join(part for part in (authors, str(tags.get("ref_year") or "").strip()) if part)
    name = str(tags.get("ref_name") or "").strip() or f"Macrostrat source {tags.get('source_id')}"
    return f"{name} ({details})" if details else name


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def cell_size(view: View, regrid_shape: int) -> float:
    """Degrees per cell of a closer map: fine enough for the output's pixels, within the cap on cells."""
    width, height = view.east - view.west, view.north - view.south
    return max(MIN_CELL, max(width, height) / regrid_shape, math.sqrt(width * height / MAX_CELLS))


def rasterise(tiles: Iterable[tuple[int, int, list[Unit]]], z: int, view: View, cell: float, by: str,
              intervals: dict[int, Interval], classes: Sequence[Interval]) -> tuple[np.ndarray, list[Entry]]:
    """The grid of cells over *view*, and what its numbers mean (see :class:`Layer`)."""
    cols, rows = math.ceil((view.east - view.west) / cell - 1e-9), math.ceil((view.north - view.south) / cell - 1e-9)
    grid = np.zeros((rows, cols), np.uint16)
    numbers: dict[Entry, int] = {}
    n = 2 ** z
    for x, y, units in tiles:
        west = x / n * 360 - 180 - view.centre_lon             # the tile's edges, relative to the map's centre
        shifts = [s for s in (-720, -360, 0, 360, 720) if west + s < view.east and west + s + 360 / n > view.west]
        for unit in units:
            if by == "rock":
                rock = rock_class(unit.tags)
                entry = Entry(rock, ROCKS[rock], source_name(unit.tags)) if rock else None
            else:
                interval = age_class(unit.tags, intervals, classes)
                entry = Entry(interval.name, interval.colour, source_name(unit.tags)) if interval else None
            if not unit.rings:
                continue
            # A unit without a class is burned as nothing: it may lie on top of one that has a class
            value = numbers.setdefault(entry, len(numbers) + 1) if entry else 0
            points = np.concatenate(unit.rings)
            starts = np.cumsum([0, *(len(ring) for ring in unit.rings[:-1])]).tolist()
            lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y + points[:, 1]) / n))))
            for shift in shifts:
                lon = west + shift + points[:, 0] * 360 / n
                # burn maps (lon, lat) to (column, row) as ((lon + 180), (90 - lat)) at one cell per degree
                burn(grid, np.column_stack([(lon - view.west) / cell - 180, 90 - (view.north - lat) / cell]),
                     starts, value, 1)
    return grid, list(numbers)


def load_layer(view: View, cell: float, z: int, by: str, cache_dir: str | None = None) -> Layer:
    """The geology over *view* on land, in cells of *cell* degrees, from the tiles of zoom *z*."""
    intervals, classes = load_intervals(cache_dir)
    tiles = load_tiles(tiles_over(view, z), z, cache_dir)
    grid, entries = rasterise(tiles, z, view, cell, by, intervals, classes)
    rows, cols = grid.shape
    extent = (view.west, view.west + cols * cell, view.north - rows * cell, view.north)
    grid[~land_mask(grid.shape, extent, view.centre_lon, scale="10m" if cell < FINE_COAST_CELL else "50m")] = 0
    return Layer(grid, entries, extent, view.centre_lon)


def load_geology(axes: Sequence[GeoAxes], by: str = "age", whole_globe: bool = True, regrid_shape: int = 750,
                 cache_dir: str | None = None) -> list[Layer]:
    """The geology for each of *axes*: one world grid shared by whole globes, else a window for each map."""
    if whole_globe:
        logger.info("Loading the geology of the world (Macrostrat) …")
        return [load_layer(View(0.0, -180.0, 180.0, -90.0, 90.0), GLOBE_CELL, GLOBE_ZOOM, by, cache_dir)] * len(axes)
    layers = []
    for ax in axes:
        view = visible_box(ax)
        z = close_zoom(view)
        logger.info("Loading the geology (Macrostrat, zoom %d) …", z)
        layers.append(load_layer(view, cell_size(view, regrid_shape), z, by, cache_dir))
    return layers


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def geology_rgba(layer: Layer, alpha: float) -> np.ndarray:
    palette = np.zeros((len(layer.entries) + 1, 4), np.uint8)
    for number, entry in enumerate(layer.entries, start=1):
        palette[number] = (*entry.colour, round(alpha * 255))
    return palette[layer.cells]


def add_geology_overlay(ax: GeoAxes, layer: Layer, alpha: float = DEFAULT_ALPHA, regrid_shape: int = 750) -> None:
    """Draw *layer* on *ax*."""
    ax.imshow(geology_rgba(layer, alpha), origin="upper", extent=layer.extent,
              transform=ccrs.PlateCarree(central_longitude=layer.centre_lon), interpolation="nearest",
              regrid_shape=regrid_shape, zorder=5)
    logger.info("Geology drawn (alpha=%.2f).", alpha)


def visible_entries(axes: Sequence[GeoAxes], layers: Sequence[Layer]) -> Counter[Entry]:
    """How much of *axes* each entry of *layers* covers, in sample points (see :func:`biomes.visible_points`)."""
    counts: Counter[Entry] = Counter()
    for ax, layer in zip(axes, layers):
        west, east, south, north = layer.extent
        rows, cols = layer.cells.shape
        lon, lat, _, _ = visible_points(ax)
        col = np.floor(((lon - layer.centre_lon - west) % 360) / (east - west) * cols).astype(int)
        row = np.floor((north - lat) / (north - south) * rows).astype(int)
        inside = (col >= 0) & (col < cols) & (row >= 0) & (row < rows)
        values, n = np.unique(layer.cells[row[inside], col[inside]], return_counts=True)
        for value, count in zip(values, n):
            if value:
                counts[layer.entries[value - 1]] += int(count)
    return counts


def add_geology_legend(ax: GeoAxes, shown: Counter[Entry], by: str = "age", classes: Sequence[Interval] | None = None,
                       y: float = -0.01, x: float = 0.5, alpha: float = 1.0, cache_dir: str | None = None
                       ) -> Legend | None:
    """A key to the classes on the map, the youngest rocks first; ``None`` when none shows.

    Its colours are as pale as the map's at *alpha* over white: many ages differ only in shade.
    """
    colours = {entry.name: entry.colour for entry in shown}
    if not colours:
        return None
    if by == "rock":
        names = [name for name in ROCKS if name in colours]
        title = "Rocks (Macrostrat); mixed where a map gives several kinds"
    else:
        ages = {i.name: (i.oldest, -i.youngest) for i in classes or load_intervals(cache_dir)[1]}
        names = sorted(colours, key=lambda name: ages[name])
        title = "Age of the rocks (Macrostrat): the shortest interval that holds a unit's whole age"
    swatches = [_swatch([round(alpha * v + (1 - alpha) * 255) for v in colours[name]]) for name in names]
    return _key(ax, swatches, names, title, min(len(names), 6), x, y)


def geology_attribution(shown: Counter[Entry]) -> str:
    """The credit for the maps on show, the one covering most first."""
    maps: Counter[str] = Counter()
    for entry, count in shown.items():
        maps[entry.source] += count
    return ATTRIBUTION.format(maps="; ".join(name for name, _ in maps.most_common()) or "Macrostrat")
