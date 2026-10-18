"""Species records: where any species (or genus, family …) has been recorded, from GBIF.

Data source
-----------
GBIF, the Global Biodiversity Information Facility (https://www.gbif.org/),
which gathers occurrence records from museums, surveys and citizen science
(iNaturalist, eBird …). A name, scientific (``Panthera leo``, ``Quercus``) or
common (``lion``, ``red fox``), is looked up in the GBIF Backbone Taxonomy.
The records are read from GBIF's occurrence density maps as counts of
records in cells of about 20 km (1/512 of a quarter of the globe's width,
zoom 1 in EPSG:4326), eight vector tiles in all, and cached under
``~/.cache/ortho_tiles/gbif/``. Fossils and zoo or garden specimens are left
out, so the map shows where the species has lived wild.

These are records, not a range map: places with many naturalists, such as
Europe and North America, have far more records than the tropics.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any, NamedTuple

import numpy as np

from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import PolyCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from tectonics import project

logger = logging.getLogger(__name__)

DATA_VERSION = 1
API = "https://api.gbif.org"
BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"     # the GBIF Backbone Taxonomy
ZOOM = 1                                             # 4 x 2 tiles of 512 cells: ~0.18 degrees, ~20 km
TILE_CELLS = 512
CELL_DEGREES = 180 / 2 ** ZOOM / TILE_CELLS
FIRST_YEAR = 1600
# Every kind of record but fossils and living (zoo, garden) specimens
WILD_RECORDS = ("HUMAN_OBSERVATION", "MACHINE_OBSERVATION", "OBSERVATION", "PRESERVED_SPECIMEN",
                "MATERIAL_SAMPLE", "MATERIAL_CITATION", "OCCURRENCE")
# Records in a cell: the lower bound of each class, and its colour
COUNT_STEPS = (1, 2, 11, 101, 1001)
COUNT_COLOURS = ("#fa9fb5", "#f768a1", "#dd3497", "#ae017e", "#49006a")


class GbifDataError(RuntimeError):
    """GBIF could not be reached, or sent something unreadable."""


class Taxon(NamedTuple):
    key: int
    name: str            # scientific name, without the author
    rank: str            # "SPECIES", "GENUS" …
    common: str | None   # English name, if GBIF has one


class Occurrences(NamedTuple):
    taxon: Taxon
    lon: np.ndarray      # cell centres
    lat: np.ndarray
    records: np.ndarray  # records in each cell
    years: tuple[int, int] | None
    retrieved: str       # date the records were read, YYYY-MM-DD


def _get(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _get_json(path: str, **params) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    try:
        return json.loads(_get(url))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GbifDataError(f"Could not reach GBIF: {exc}") from exc


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "gbif")


def parse_years(text: str | None) -> tuple[int, int] | None:
    """``"1990-2020"`` or ``"2015"`` as (first, last) years; ``None`` for all years. Raises ``ValueError``."""
    if not text or not str(text).strip():
        return None
    parts = [p.strip() for p in str(text).split("-")]
    try:
        first, last = (int(parts[0]), int(parts[-1])) if len(parts) <= 2 else (None, None)
    except ValueError:
        first = last = None
    this_year = time.localtime().tm_year
    if first is None or last is None or not FIRST_YEAR <= first <= last <= this_year:
        raise ValueError(f"Species years must be YEAR or FROM-TO between {FIRST_YEAR} and {this_year}, "
                         f"not {text!r}.")
    return first, last


def _common_name(key: int) -> str | None:
    try:
        return _get_json(f"/v1/species/{key}").get("vernacularName") or None
    except GbifDataError:
        return None


def _record_count(key: int) -> int:
    return int(_get_json("/v1/occurrence/search", taxonKey=key, hasCoordinate="true", limit=0).get("count", 0))


def _cached_names(cache_dir: str) -> dict:
    try:
        with open(os.path.join(cache_dir, "names.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def resolve_taxon(name: str, cache_dir: str | None = None) -> Taxon:
    """The GBIF taxon a scientific or common *name* stands for.

    A scientific name is matched first (``Panthera leo``, a misspelt
    ``Vulpes vulpis``, ``Felidae``). Otherwise the English and other common
    names are searched: of the taxa with exactly that name, the one most
    sources call so wins (``oak`` is the genus *Quercus*), then a species,
    then the one with the most records. Raises ``ValueError`` when nothing has
    that name and :class:`GbifDataError` when GBIF cannot be reached.
    """
    wanted = " ".join(name.split())
    if not wanted:
        raise ValueError("Give a species name, such as 'Panthera leo' or 'red fox'.")
    cache_dir = cache_dir or _default_cache_dir()
    names = _cached_names(cache_dir)
    if wanted.lower() in names:
        return Taxon(*names[wanted.lower()])

    match = _get_json("/v1/species/match", name=wanted)
    if match.get("matchType") in ("EXACT", "FUZZY") and match.get("usageKey") and match.get("confidence", 0) >= 90:
        key = int(match.get("acceptedUsageKey") or match["usageKey"])
        taxon = Taxon(key, match.get("canonicalName") or wanted, match.get("rank", ""), _common_name(key))
        if match.get("acceptedUsageKey"):                # a synonym: name the accepted taxon
            accepted = _get_json(f"/v1/species/{key}")
            taxon = taxon._replace(name=accepted.get("canonicalName") or taxon.name,
                                   rank=accepted.get("rank", taxon.rank))
    else:
        found = _get_json("/v1/species/search", q=wanted, qField="VERNACULAR", datasetKey=BACKBONE,
                          status="ACCEPTED", limit=1000).get("results", [])
        exact = [r for r in found
                 if any(v.get("vernacularName", "").lower() == wanted.lower() for v in r.get("vernacularNames", []))]
        if not exact:
            raise ValueError(f"GBIF knows no species called {name!r}; check the spelling or try another name.")
        def sources(r: dict) -> int:                  # how many of its common names are exactly this one
            return sum(v.get("vernacularName", "").lower() == wanted.lower() for v in r.get("vernacularNames", []))

        top = max((sources(r), r.get("rank") == "SPECIES") for r in exact)
        tied = [r for r in exact if (sources(r), r.get("rank") == "SPECIES") == top]
        best = tied[0] if len(tied) == 1 else max(tied[:6], key=lambda r: _record_count(int(r["key"])))
        key = int(best["key"])
        taxon = Taxon(key, best.get("canonicalName") or best.get("scientificName", wanted), best.get("rank", ""),
                      wanted.lower())

    names[wanted.lower()] = list(taxon)
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "names.json"), "w", encoding="utf-8") as f:
        json.dump(names, f, indent=1)
    return taxon


def tile_url(key: int, x: int, y: int, years: tuple[int, int] | None = None) -> str:
    """The URL of one zoom-1 density tile of taxon *key*'s wild records."""
    params: list[tuple[str, object]] = [("srs", "EPSG:4326"), ("taxonKey", key)]
    params += [("basisOfRecord", kind) for kind in WILD_RECORDS]
    if years:
        params.append(("year", f"{years[0]},{years[1]}"))
    return f"{API}/v2/map/occurrence/density/{ZOOM}/{x}/{y}.mvt?{urllib.parse.urlencode(params)}"


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def _fields(data: bytes) -> Iterator[tuple[int, Any]]:
    """The (field, value) pairs of a protocol-buffer message; length-delimited values as bytes."""
    i = 0
    while i < len(data):
        tag, i = _varint(data, i)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 2:
            size, i = _varint(data, i)
            value, i = data[i:i + size], i + size
        elif wire in (1, 5):
            value, i = data[i:i + (8 if wire == 1 else 4)], i + (8 if wire == 1 else 4)
        else:
            raise GbifDataError(f"not a vector tile (wire type {wire})")
        yield field, value


def read_tile(data: bytes) -> list[tuple[int, int, int]]:
    """The (column, row, records) of each cell in a GBIF density tile (Mapbox vector tile of points).

    Cells in the tile's buffer, beyond its 512 x 512, belong to the next tile and are left out.
    """
    cells = []
    for field, layer in _fields(data):
        if field != 3:
            continue
        keys, values, features, extent = [], [], [], 4096
        for part, value in _fields(layer):
            if part == 2:
                features.append(value)
            elif part == 3:
                keys.append(value.decode())
            elif part == 4:
                values.append(_number(value))
            elif part == 5:
                extent = value
        total = keys.index("total") if "total" in keys else -1
        for feature in features:
            tags, geometry = b"", b""
            for part, value in _fields(feature):
                if part == 2:
                    tags = value
                elif part == 4:
                    geometry = value
            if not geometry:
                continue
            command, i = _varint(geometry, 0)
            if command & 7 != 1:                           # not a MoveTo: not a point
                continue
            zx, i = _varint(geometry, i)
            zy, i = _varint(geometry, i)
            col, row = (zx >> 1) ^ -(zx & 1), (zy >> 1) ^ -(zy & 1)
            col, row = col * TILE_CELLS // extent, row * TILE_CELLS // extent
            if not (0 <= col < TILE_CELLS and 0 <= row < TILE_CELLS):
                continue
            count = next((int(values[v]) for k, v in _tag_pairs(tags) if k == total), 0)
            if count > 0:
                cells.append((col, row, count))
    return cells


def _number(value: bytes) -> float:
    """The number in a vector tile's value message (int, uint or zigzag sint); 0 for text."""
    for field, number in _fields(value):
        if field in (4, 5):
            return number
        if field == 6:
            return (number >> 1) ^ -(number & 1)
    return 0


def _tag_pairs(tags: bytes):
    i, numbers = 0, []
    while i < len(tags):
        n, i = _varint(tags, i)
        numbers.append(n)
    return zip(numbers[0::2], numbers[1::2])


def load_occurrences(taxon: Taxon, years: tuple[int, int] | None = None,
                     cache_dir: str | None = None) -> Occurrences:
    """The cells where *taxon* has wild records (in *years*, if given), reading eight tiles from GBIF once."""
    cache_dir = cache_dir or _default_cache_dir()
    span = f"_{years[0]}-{years[1]}" if years else ""
    path = os.path.join(cache_dir, f"taxon_{taxon.key}{span}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Reading the GBIF records of %s (one-time, eight tiles) …", taxon.name)
        lon, lat, count = [], [], []
        for x in range(2 ** (ZOOM + 1)):
            for y in range(2 ** ZOOM):
                try:
                    cells = read_tile(_get(tile_url(taxon.key, x, y, years)))
                except Exception as exc:              # network and tile errors alike
                    raise GbifDataError(f"Could not read the GBIF records of {taxon.name}: {exc}") from exc
                for col, row, n in cells:
                    lon.append(-180 + (x * TILE_CELLS + col + 0.5) * CELL_DEGREES)
                    lat.append(90 - (y * TILE_CELLS + row + 0.5) * CELL_DEGREES)
                    count.append(n)
        part = path + ".part.npz"
        np.savez_compressed(part, lon=np.array(lon, float), lat=np.array(lat, float), records=np.array(count, np.int64),
                            retrieved=np.array(time.strftime("%Y-%m-%d")))
        os.replace(part, path)
    with np.load(path) as data:
        return Occurrences(taxon, data["lon"], data["lat"], data["records"], years, str(data["retrieved"]))


def taxon_label(taxon: Taxon) -> str:
    """``Panthera leo (lion)``: the scientific name, with the common one and a rank above species."""
    notes = [taxon.common] if taxon.common and taxon.common.lower() != taxon.name.lower() else []
    if taxon.rank and taxon.rank not in ("SPECIES", "SUBSPECIES", "VARIETY", "FORM"):
        notes.append(taxon.rank.lower())                 # "Quercus (oak, genus)"
    return f"{taxon.name} ({', '.join(notes)})" if notes else taxon.name


def count_classes(count: np.ndarray) -> np.ndarray:
    """The class (0 for 1 record … 4 for over 1,000) of each cell's number of records."""
    return np.digitize(count, COUNT_STEPS) - 1


def _points_per_metre(ax: GeoAxes) -> float:
    """Points on the page per metre of *ax*'s projection, near the centre."""
    x0, x1 = ax.get_xlim()
    width_px = ax.get_window_extent().width
    return width_px / abs(x1 - x0) * 72 / ax.figure.dpi if width_px and x1 != x0 else 0.0


def draw_occurrences(ax: GeoAxes, found: Occurrences, alpha: float = 0.9, min_points: float = 2.2) -> None:
    """Fill each cell with records, darker for more, the busiest cells on top.

    Cells are drawn as their true shape. On a whole-globe map a ~20 km cell is
    a few pixels across, so each is widened by an outline of its own colour to
    at least *min_points*, and a lone record stays visible.
    """
    half = CELL_DEGREES / 2
    lon = np.repeat(found.lon[:, None], 4, axis=1) + np.array([-half, half, half, -half])
    lat = np.clip(np.repeat(found.lat[:, None], 4, axis=1) + np.array([-half, -half, half, half]), -90, 90)
    x, y = project(ax, lon.ravel(), lat.ravel())
    x, y = x.reshape(-1, 4), y.reshape(-1, 4)
    seen = np.isfinite(x).all(axis=1) & np.isfinite(y).all(axis=1)
    if not seen.any():
        logger.info("No GBIF records of %s on this side of the globe.", found.taxon.name)
        return
    order = np.flatnonzero(seen)[np.argsort(found.records[seen], kind="stable")]
    colours = np.array(COUNT_COLOURS)[count_classes(found.records[order])]
    cell_points = CELL_DEGREES * 111_320 * _points_per_metre(ax)
    outline = max(0.0, min_points - cell_points)
    cells = PolyCollection(np.stack([x[order], y[order]], axis=2), facecolors=colours,
                           edgecolors=colours if outline else "none", linewidths=outline, alpha=alpha,
                           antialiaseds=False, zorder=7.25)
    ax.add_collection(cells)
    logger.info("GBIF records of %s drawn: %s cells.", found.taxon.name, f"{int(seen.sum()):,}")


def add_occurrences_legend(ax: GeoAxes, found: Occurrences, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the squares' shades, with the number of records and cells."""
    labels = ["1 record", "2-10", "11-100", "101-1,000", "over 1,000"]
    handles = [Line2D([], [], linestyle="none", marker="s", markersize=9, markerfacecolor=colour,
                      markeredgecolor="none") for colour in COUNT_COLOURS]
    span = f", {found.years[0]}-{found.years[1]}" if found.years else ""
    title = (f"Records of {taxon_label(found.taxon)}{span}: {int(found.records.sum()):,} worldwide in "
             f"{len(found.records):,} cells of ~20 km (GBIF, wild records; not a range map)")
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=len(handles), fontsize=10,
                    title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=1.2, columnspacing=1.4, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend


def gbif_attribution(found: Occurrences) -> str:
    return f"Species records: GBIF.org occurrence data, retrieved {found.retrieved}"
