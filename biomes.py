"""Biome and ecoregion overlay, from RESOLVE Ecoregions 2017.

Data source
-----------
Dinerstein, E., Olson, D., Joshi, A., et al. (2017). An Ecoregion-Based
Approach to Protecting Half the Terrestrial Realm. *BioScience* 67(6),
534-545. https://doi.org/10.1093/biosci/bix014 — the RESOLVE Ecoregions 2017
map, CC BY 4.0.

It divides the land into 846 ecoregions, each in one of 14 biomes, plus
"Rock & Ice" for ice caps and bare high mountains, and in one of WWF's eight
biogeographic realms. Large lakes are left out.
The shapefile (~150 MB) is downloaded once and burned into a 0.05° (~5.5 km)
grid of ecoregion numbers, under 1 MB once cached; the download is then
deleted.
"""

from __future__ import annotations

import io
import logging
import os
import re
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

import frame

logger = logging.getLogger(__name__)

ECOREGIONS_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"
ECOREGIONS_NAME = "Ecoregions2017"
CELLS_PER_DEGREE = 20     # 0.05° cells
DATA_VERSION = 1          # bump when the grid changes, so old caches are rebuilt
NODATA = 65535            # sea, large lakes

BIOME_ATTRIBUTION = "Biomes: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0"
ECOREGION_ATTRIBUTION = "Ecoregions: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0"
REALM_ATTRIBUTION = "Realms: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017), CC BY 4.0"

# What the layer is coloured by: each cell's biome, ecoregion or realm
COLOUR_BY = ("biome", "ecoregion", "realm")

# Biome number -> (name, (R, G, B)), in RESOLVE's colours. Rock & Ice is
# ecoregion 0 and has no biome of its own; RESOLVE colours it like sand,
# which reads as desert, so it is a pale grey here.
ROCK_AND_ICE = 0
BIOMES: dict[int, tuple[str, tuple[int, int, int]]] = {
    1:  ("Tropical & Subtropical Moist Broadleaf Forests", (0x38, 0xA7, 0x00)),
    2:  ("Tropical & Subtropical Dry Broadleaf Forests", (0xCC, 0xCD, 0x65)),
    3:  ("Tropical & Subtropical Coniferous Forests", (0x88, 0xCE, 0x66)),
    4:  ("Temperate Broadleaf & Mixed Forests", (0x00, 0x73, 0x4C)),
    5:  ("Temperate Conifer Forests", (0x45, 0x89, 0x70)),
    6:  ("Boreal Forests/Taiga", (0x7A, 0xB6, 0xF5)),
    7:  ("Tropical & Subtropical Grasslands, Savannas & Shrublands", (0xFE, 0xAA, 0x01)),
    8:  ("Temperate Grasslands, Savannas & Shrublands", (0xFE, 0xFF, 0x73)),
    9:  ("Flooded Grasslands & Savannas", (0xBE, 0xE7, 0xFF)),
    10: ("Montane Grasslands & Shrublands", (0xD6, 0xC3, 0x9D)),
    11: ("Tundra", (0x9E, 0xD7, 0xC2)),
    12: ("Mediterranean Forests, Woodlands & Scrub", (0xFE, 0x00, 0x00)),
    13: ("Deserts & Xeric Shrublands", (0xCC, 0x67, 0x67)),
    14: ("Mangroves", (0xFE, 0x01, 0xC4)),
    ROCK_AND_ICE: ("Rock & Ice", (0xE4, 0xE8, 0xEC)),
}

# WWF's biogeographic realms (Olson et al. 2001), as RESOLVE names them -> (where, (R, G, B)).
# Rock & Ice has no realm and stays clear.
REALMS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "Nearctic":    ("North America and Greenland", (86, 140, 205)),
    "Neotropic":   ("South and Central America, the Caribbean", (60, 170, 90)),
    "Palearctic":  ("Europe, North Africa, the Middle East, northern and central Asia", (230, 160, 60)),
    "Afrotropic":  ("Africa south of the Sahara, southern Arabia, Madagascar", (205, 85, 70)),
    "Indomalayan": ("South and Southeast Asia to Wallace's Line", (165, 95, 190)),
    "Australasia": ("Australia, New Guinea, New Zealand, Wallacea", (230, 205, 70)),
    "Oceania":     ("The Pacific islands", (70, 190, 200)),
    "Antarctica":  ("Antarctica and the sub-Antarctic islands", (150, 165, 190)),
}

# The ecoregion key names the largest ecoregions on the map, at most this many
ECOREGION_KEY_MAX = 12


class BiomeDataError(RuntimeError):
    """The ecoregion map could not be downloaded or read."""


class Ecoregions(NamedTuple):
    """The ecoregion grid and what each ecoregion number stands for."""
    ids: np.ndarray            # (rows, cols) uint16, north to south from 90°N, west to east from 180°W
    names: np.ndarray          # ecoregion number -> name
    biomes: np.ndarray         # ecoregion number -> biome number (ROCK_AND_ICE for Rock & Ice)
    colours: np.ndarray        # ecoregion number -> (R, G, B)
    realms: np.ndarray         # ecoregion number -> biogeographic realm


# ---------------------------------------------------------------------------
# Biome names
# ---------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z]+", text.lower()) if w and w != "and"]


def resolve_biome_classes(specs: Sequence[str]) -> tuple[int, ...]:
    """Biome numbers named by *specs*, in key order (Rock & Ice last).

    Each spec is a biome's number (``"4"``), ``"rock"`` or ``"ice"`` for
    Rock & Ice, or words that all appear in the names of the biomes wanted:
    ``"temperate"`` is the three temperate biomes, ``"forest"`` the seven
    forest biomes, ``"tropical grass"`` one. Matching ignores case. Raises
    ``ValueError`` for a spec that matches no biome.
    """
    selected: set[int] = set()
    for spec in specs:
        key = spec.strip()
        words = _words(key)
        if key.isdigit() and int(key) in BIOMES:
            matches = [int(key)]
        else:
            matches = [number for number, (name, _) in BIOMES.items()
                       if words and all(any(part.startswith(w) for part in _words(name)) for w in words)]
        if not matches:
            names = "; ".join(f"{number} {name}" for number, (name, _) in sorted(BIOMES.items()) if number)
            raise ValueError(f"Unknown biome {spec!r}. Use a number or words from a name: {names}; "
                             "or rock (Rock & Ice).")
        selected.update(matches)
    return tuple(number for number in BIOMES if number in selected)


# ---------------------------------------------------------------------------
# Download and rasterisation
# ---------------------------------------------------------------------------


_BLOCK_ROWS = 256         # rows of cells filled at a time


def burn(grid: np.ndarray, points: np.ndarray, parts: Sequence[int], value: int,
         cells_per_degree: int = CELLS_PER_DEGREE) -> None:
    """Set the cells of *grid* whose centres lie inside one shapefile polygon to *value*.

    *points* are its (lon, lat) vertices and *parts* the index where each
    ring starts. A scanline fill by the even-odd rule: along each row of
    cell centres, the rings' crossings pair up into runs of inside cells, so
    holes (lakes) and islands in them come out right in any ring order.
    """
    rows, cols = grid.shape
    starts, ends = [], []
    bounds = list(parts) + [len(points)]
    for first, stop in zip(bounds[:-1], bounds[1:]):
        if stop - first < 3:
            continue
        ring = np.arange(first, stop)
        starts.append(ring)
        ends.append(np.roll(ring, -1))       # an open ring is closed by its last edge
    if not starts:
        return
    a, b = points[np.concatenate(starts)], points[np.concatenate(ends)]
    # Grid coordinates: cell (r, c) covers [c, c + 1) x [r, r + 1), its centre at (c + 0.5, r + 0.5)
    x0, x1 = (a[:, 0] + 180) * cells_per_degree, (b[:, 0] + 180) * cells_per_degree
    y0, y1 = (90 - a[:, 1]) * cells_per_degree, (90 - b[:, 1]) * cells_per_degree
    # Each edge crosses the rows whose centre is in [its top, its bottom): none if level
    first_row = np.ceil(np.minimum(y0, y1) - 0.5).astype(np.int64)
    end_row = np.ceil(np.maximum(y0, y1) - 0.5).astype(np.int64)
    count = end_row - first_row
    if count.sum() == 0:
        return
    edge = np.repeat(np.arange(len(count)), count)
    row = first_row[edge] + np.arange(len(edge)) - np.repeat(np.cumsum(count) - count, count)
    t = (row + 0.5 - y0[edge]) / (y1[edge] - y0[edge])
    x = x0[edge] + t * (x1[edge] - x0[edge])
    # Sorted along each row, the crossings pair up: in at one, out at the next
    order = np.lexsort((x, row))
    row, x = row[order], x[order]
    run_row = row[0::2]
    run_start = np.clip(np.ceil(x[0::2] - 0.5), 0, cols).astype(np.int64)
    run_end = np.clip(np.ceil(x[1::2] - 0.5), 0, cols).astype(np.int64)
    keep = (run_row >= 0) & (run_row < rows) & (run_end > run_start)
    run_row, run_start, run_end = run_row[keep], run_start[keep], run_end[keep]
    if not len(run_row):
        return
    # Mark each run's ends in its row and add along the rows: inside cells come out
    # positive. A block of rows at a time, so a polygon spanning the globe stays small.
    left = run_start.min()
    width = run_end.max() - left + 1
    for top in range(run_row.min(), run_row.max() + 1, _BLOCK_ROWS):
        lo, hi = np.searchsorted(run_row, [top, top + _BLOCK_ROWS])
        if lo == hi:
            continue
        r, size = run_row[lo:hi] - top, _BLOCK_ROWS * width
        marks = np.bincount(r * width + run_start[lo:hi] - left, minlength=size)
        marks -= np.bincount(r * width + run_end[lo:hi] - left, minlength=size)
        inside = np.cumsum(marks.reshape(_BLOCK_ROWS, width), axis=1)[:, :width - 1] > 0
        block = grid[top:top + _BLOCK_ROWS, left:left + width - 1]
        block[inside[:block.shape[0]]] = value


def _hex_rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.strip().lstrip("#")
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16)


def rasterise(archive: zipfile.ZipFile, cells_per_degree: int = CELLS_PER_DEGREE) -> dict[str, np.ndarray]:
    """The ecoregion grid and attribute table from the RESOLVE shapefile in *archive*."""
    import shapefile  # pyshp, which Cartopy depends on

    def member(ext: str) -> io.BytesIO:
        return io.BytesIO(archive.read(f"{ECOREGIONS_NAME}.{ext}"))

    reader = shapefile.Reader(shp=archive.open(f"{ECOREGIONS_NAME}.shp"), shx=member("shx"),
                              dbf=member("dbf"), encoding="latin-1")
    with reader:
        # (ecoregion number, name, biome, colour, realm) of each shape, in file order
        table: list[tuple[int, str, int, str, str]] = []
        for record in reader.iterRecords():
            row: dict[str, Any] = record.as_dict()
            table.append((int(row["ECO_ID"]), str(row["ECO_NAME"]), int(row["BIOME_NUM"]),
                          str(row["COLOR"]), str(row["REALM"])))
        count = max(number for number, *_ in table) + 1
        names = np.full(count, "", dtype=object)
        biomes = np.zeros(count, np.uint8)
        colours = np.zeros((count, 3), np.uint8)
        realms = np.full(count, "", dtype=object)
        for number, name, biome, colour, realm in table:
            names[number] = name
            biomes[number] = ROCK_AND_ICE if number == 0 else biome
            colours[number] = _hex_rgb(colour)
            realms[number] = realm
        grid = np.full((180 * cells_per_degree, 360 * cells_per_degree), NODATA, np.uint16)
        for shape, (number, *_) in zip(reader.iterShapes(), table):
            if shape is not None:
                burn(grid, np.asarray(shape.points, float).reshape(-1, 2), list(shape.parts), number,
                     cells_per_degree)
    return {"ids": grid, "names": names.astype(str), "biomes": biomes, "colours": colours,
            "realms": realms.astype(str)}


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "biomes")


def _download(url: str, dest: str, timeout: float = 120, label: str = "ecoregions") -> None:
    """Download *url* to *dest*, through a ``.part`` file so a broken transfer leaves nothing."""
    part = dest + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            shown = -1
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and done * 10 // total != shown:
                    shown = done * 10 // total
                    logger.info("  %s: %d of %d MB", label, done >> 20, total >> 20)
        if total and done != total:
            raise BiomeDataError(f"incomplete download of {url} ({done} of {total} bytes)")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def ensure_ecoregion_data(cache_dir: str | None = None) -> str:
    """Path to the cached ecoregion grid, building it from the RESOLVE shapefile if needed.

    The first run downloads ~150 MB. Raises :class:`BiomeDataError` if the
    data cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"ecoregions2017_{CELLS_PER_DEGREE}pd_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached ecoregions: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    zip_path = os.path.join(cache_dir, f"{ECOREGIONS_NAME}.zip")
    try:
        if not os.path.isfile(zip_path):
            logger.info("Downloading RESOLVE Ecoregions 2017 (one-time, ~150 MB) …")
            _download(ECOREGIONS_URL, zip_path)
        logger.info("Mapping the ecoregions onto a %g° grid …", 1 / CELLS_PER_DEGREE)
        with zipfile.ZipFile(zip_path) as archive:
            data = rasterise(archive, CELLS_PER_DEGREE)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, zipfile.BadZipFile,
            KeyError, ValueError) as exc:
        raise BiomeDataError(f"Could not read the RESOLVE ecoregions: {exc}") from exc
    except Exception as exc:                  # pyshp raises its own errors for a damaged file
        if isinstance(exc, BiomeDataError):
            raise
        raise BiomeDataError(f"Could not read the RESOLVE ecoregions: {exc}") from exc

    part = path + ".part.npz"
    np.savez_compressed(part, **data)
    os.replace(part, path)
    os.remove(zip_path)                       # only needed to build the grid
    logger.info("Ecoregions ready: %s", path)
    return path


def load_ecoregions(cache_dir: str | None = None) -> Ecoregions:
    """The ecoregion grid and its attribute table."""
    with np.load(ensure_ecoregion_data(cache_dir)) as data:
        return Ecoregions(data["ids"], data["names"], data["biomes"], data["colours"], data["realms"])


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def ecoregion_rgba(layer: Ecoregions, alpha: float, classes: Sequence[int] | None = None,
                   by: str = "biome") -> np.ndarray:
    """An RGBA image of *layer*, coloured *by* biome, ecoregion or realm (see :data:`COLOUR_BY`).

    Only the ecoregions in the biomes *classes* are coloured (all by
    default); the rest, the sea and lakes are clear, and so is Rock & Ice
    by realm, having none.
    """
    if by not in COLOUR_BY:
        raise ValueError(f"by must be one of {COLOUR_BY}, got {by!r}")
    palette = np.zeros((NODATA + 1, 4), np.uint8)
    count = len(layer.biomes)
    shown = np.isin(layer.biomes, classes) if classes else np.ones(count, bool)
    if by == "ecoregion":
        rgb = layer.colours
    elif by == "realm":
        rgb = np.array([REALMS.get(str(r), ("", (0, 0, 0)))[1] for r in layer.realms], np.uint8)
        shown &= np.isin(layer.realms, list(REALMS))
    else:
        rgb = np.array([BIOMES[int(b)][1] for b in layer.biomes], np.uint8)
    palette[:count, :3] = rgb
    palette[:count, 3] = np.where(shown, round(alpha * 255), 0)
    return palette[layer.ids]


def add_ecoregion_overlay(ax: GeoAxes, layer: Ecoregions, alpha: float = 0.6, regrid_shape: int = 750,
                          classes: Sequence[int] | None = None, by: str = "biome") -> None:
    """Render the layer on *ax*, coloured *by* biome, ecoregion or realm."""
    ax.imshow(
        ecoregion_rgba(layer, alpha, classes, by),
        origin="upper",
        extent=(-180, 180, -90, 90),
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )
    logger.info("%s overlay rendered (alpha=%.2f).", by.capitalize(), alpha)


def visible_points(ax: GeoAxes, samples: int = 300) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Points spread evenly over the globe on *ax* as drawn: ``(lon, lat, x, y)``.

    *x*, *y* are in the axes' projection. The globe, or a radius map's disc,
    is the circle inside the frame (a rectangular map the whole frame), so
    what is foreshortened near the rim gets fewer points, as it looks.
    """
    steps = (np.arange(samples) + 0.5) / samples
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    gx, gy = np.meshgrid(x0 + steps * (x1 - x0), y0 + steps * (y1 - y0))
    inside = frame.inside(ax, gx, gy)
    x, y = gx[inside], gy[inside]
    lonlat = ccrs.PlateCarree().transform_points(ax.projection, x, y)
    ok = np.isfinite(lonlat[:, 0]) & np.isfinite(lonlat[:, 1])
    return lonlat[ok, 0], lonlat[ok, 1], x[ok], y[ok]


def on_globe(ax: GeoAxes, lon: float, lat: float, margin: float = 0.9) -> bool:
    """True if (*lon*, *lat*) shows on *ax*, within *margin* of the map's size (clear of its edge)."""
    x, y = ax.projection.transform_point(lon, lat, ccrs.PlateCarree())
    if not (np.isfinite(x) and np.isfinite(y)):
        return False
    return bool(frame.inside(ax, x, y, margin))


def visible_values(axes: Iterable[GeoAxes], grid: np.ndarray, samples: int = 300) -> Counter[int]:
    """How much of the globes on *axes* each value of *grid* covers, in sample points (see :func:`visible_points`).

    *grid* is global, north to south from 90°N and west to east from 180°W;
    :data:`NODATA` is not counted.
    """
    cells = grid.shape[1] // 360
    counts: Counter[int] = Counter()
    for ax in axes:
        lon, lat, _, _ = visible_points(ax, samples)
        col = np.clip(((lon + 180) * cells).astype(int), 0, grid.shape[1] - 1)
        row = np.clip(((90 - lat) * cells).astype(int), 0, grid.shape[0] - 1)
        values, n = np.unique(grid[row, col], return_counts=True)
        counts.update({int(v): int(c) for v, c in zip(values, n) if v != NODATA})
    return counts


def visible_ecoregions(axes: Iterable[GeoAxes], layer: Ecoregions, samples: int = 300) -> Counter[int]:
    """How much of the globes on *axes* each ecoregion covers, in sample points (see :func:`visible_values`)."""
    return visible_values(axes, layer.ids, samples)


def _key(ax: GeoAxes, handles: list[mpatches.Patch], labels: list[str], title: str, ncol: int,
         x: float, y: float) -> Legend:
    legend = Legend(
        ax, handles, labels,
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=ncol,
        fontsize=10,
        title=title,
        title_fontsize=10,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=1.6,
        columnspacing=1.4,
        borderpad=0.7,
    )
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend


def _swatch(rgb: Sequence[int]) -> mpatches.Patch:
    return mpatches.Patch(facecolor=tuple(v / 255 for v in rgb), edgecolor="#333333", linewidth=0.6)


def add_biome_legend(ax: GeoAxes, visible: Counter[int], layer: Ecoregions, y: float = -0.01, x: float = 0.5,
                     classes: Sequence[int] | None = None) -> Legend | None:
    """A key naming the biomes on the map (of *classes*, if given), in RESOLVE's order.

    *visible* counts each ecoregion's share of the map (see
    :func:`visible_ecoregions`). Returns ``None`` when no biome shows.
    """
    present = {int(layer.biomes[i]) for i in visible}
    shown = [number for number in BIOMES if number in present and (not classes or number in classes)]
    if not shown:
        return None
    handles = [_swatch(BIOMES[number][1]) for number in shown]
    labels = [BIOMES[number][0] for number in shown]
    return _key(ax, handles, labels, "Biomes (RESOLVE Ecoregions 2017)", min(len(shown), 3), x, y)


def add_ecoregion_legend(ax: GeoAxes, visible: Counter[int], layer: Ecoregions, y: float = -0.01,
                         x: float = 0.5, classes: Sequence[int] | None = None,
                         limit: int = ECOREGION_KEY_MAX) -> Legend | None:
    """A key naming the largest ecoregions on the map (in *classes*' biomes, if given).

    Returns ``None`` when no ecoregion shows.
    """
    shown = [(number, n) for number, n in visible.most_common()
             if not classes or int(layer.biomes[number]) in classes]
    if not shown:
        return None
    top = shown[:limit]
    handles = [_swatch(layer.colours[number]) for number, _ in top]
    labels = [str(layer.names[number]) for number, _ in top]
    title = ("Ecoregions (RESOLVE 2017)" if len(shown) <= limit
             else f"Ecoregions (RESOLVE 2017): the {limit} largest of {len(shown)} on the map")
    return _key(ax, handles, labels, title, min(len(top), 3), x, y)


def add_realm_legend(ax: GeoAxes, visible: Counter[int], layer: Ecoregions, y: float = -0.01, x: float = 0.5,
                     classes: Sequence[int] | None = None) -> Legend | None:
    """A key naming the realms on the map (in *classes*' biomes, if given), and where each lies.

    Returns ``None`` when no realm shows.
    """
    present = {str(layer.realms[number]) for number in visible
               if not classes or int(layer.biomes[number]) in classes}
    shown = [name for name in REALMS if name in present]
    if not shown:
        return None
    handles = [_swatch(REALMS[name][1]) for name in shown]
    labels = [f"{name}: {REALMS[name][0]}" for name in shown]
    return _key(ax, handles, labels, "Biogeographic realms (WWF, via RESOLVE Ecoregions 2017)",
                min(len(shown), 2), x, y)
