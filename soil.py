"""Soil overlay: the most probable World Reference Base soil group, from SoilGrids 2.0.

Data source
-----------
Poggio, L., de Sousa, L. M., Batjes, N. H., Heuvelink, G. B. M., Kempen, B.,
Ribeiro, E. & Rossiter, D. (2021). SoilGrids 2.0: producing soil information
for the globe with quantified spatial uncertainty. *SOIL* 7, 217-240.
https://doi.org/10.5194/soil-7-217-2021 — the ``wrb/MostProbable`` layer,
ISRIC World Soil Information, CC BY 4.0.

It gives, for every 250 m cell from 56°S to 84°N, the most probable of 30
Reference Soil Groups of the World Reference Base (WRB 2006). The oceans,
inland water, ice and Antarctica have no data.

ISRIC's web services can deliver a coarse grid quickly, but they shrink it
from overviews that average the class *numbers* (Cambisols and Luvisols
averaged become "Cryosols"), so here the 459 full-resolution files (about
220 MB) are downloaded once and reduced to 1/32 of their resolution (0.067°,
~7 km) by majority vote. Each file's result is cached as it arrives, so an
interrupted download resumes where it stopped.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from trewartha import TrewarthaDataError, _read_ifds

logger = logging.getLogger(__name__)

SOILGRIDS_URL = "https://files.isric.org/soilgrids/latest/data/wrb/"
VRT_NAME = "MostProbable.vrt"
FACTOR = 32               # 250 m cells per output cell, along each side (~7 km)
DATA_VERSION = 1          # bump when the reduction changes, so old caches are rebuilt
# An output cell needs at least this share of soil (not water or no data) to get a group
MIN_SOIL_SHARE = 1 / 3

SOIL_ATTRIBUTION = "Soil groups: SoilGrids 2.0, ISRIC (Poggio et al. 2021), CC BY 4.0"

# Grid value -> (WRB code, Reference Soil Group, what it is, (R, G, B)); 255 is no data
SOIL_GROUPS: dict[int, tuple[str, str, str, tuple[int, int, int]]] = {
    0:  ("AC", "Acrisols", "acid, clay-enriched, weathered", (235, 120, 60)),
    1:  ("AB", "Albeluvisols", "bleached tongues into a clay subsoil", (205, 190, 225)),
    2:  ("AL", "Alisols", "acid, clay-enriched, high-activity clays", (245, 175, 125)),
    3:  ("AN", "Andosols", "young volcanic", (150, 40, 60)),
    4:  ("AR", "Arenosols", "sandy", (250, 235, 170)),
    5:  ("CL", "Calcisols", "lime-rich, dry lands", (255, 215, 90)),
    6:  ("CM", "Cambisols", "young, little-developed", (230, 190, 120)),
    7:  ("CH", "Chernozems", "black earths, deep humus", (55, 45, 40)),
    8:  ("CR", "Cryosols", "permafrost", (150, 200, 230)),
    9:  ("DU", "Durisols", "silica hardpan", (225, 225, 195)),
    10: ("FR", "Ferralsols", "deeply weathered, red tropical", (200, 60, 30)),
    11: ("FL", "Fluvisols", "river and delta deposits", (80, 160, 230)),
    12: ("GL", "Gleysols", "waterlogged by groundwater", (100, 130, 190)),
    13: ("GY", "Gypsisols", "gypsum-rich, dry lands", (255, 250, 215)),
    14: ("HS", "Histosols", "peat", (90, 55, 35)),
    15: ("KS", "Kastanozems", "chestnut steppe soils", (165, 115, 65)),
    16: ("LP", "Leptosols", "shallow or stony", (180, 180, 180)),
    17: ("LX", "Lixisols", "clay-enriched, low-activity clays", (250, 150, 180)),
    18: ("LV", "Luvisols", "clay-enriched, fertile", (190, 120, 190)),
    19: ("NT", "Nitisols", "deep, red, nut-shaped structure", (150, 60, 95)),
    20: ("PH", "Phaeozems", "dark, humus-rich, leached", (110, 95, 85)),
    21: ("PL", "Planosols", "bleached over a dense subsoil", (180, 150, 120)),
    22: ("PT", "Plinthosols", "iron-rich, hardening", (220, 100, 90)),
    23: ("PZ", "Podzols", "acid, bleached, under conifers", (150, 180, 150)),
    24: ("RG", "Regosols", "weakly developed, loose material", (230, 215, 180)),
    25: ("SC", "Solonchaks", "salty", (230, 120, 220)),
    26: ("SN", "Solonetz", "sodium-rich", (195, 85, 165)),
    27: ("ST", "Stagnosols", "waterlogged by rain", (125, 170, 170)),
    28: ("UM", "Umbrisols", "dark, acid topsoil", (95, 130, 85)),
    29: ("VR", "Vertisols", "swelling, cracking clays", (125, 60, 150)),
}

NODATA = 255
_NAMED_KEY_MAX = 8


class SoilDataError(RuntimeError):
    """The SoilGrids data could not be downloaded or read."""


# ---------------------------------------------------------------------------
# Group names
# ---------------------------------------------------------------------------


def resolve_soil_classes(specs: Sequence[str]) -> tuple[int, ...]:
    """Grid values of the soil groups named by *specs*, in value order.

    Each spec is a group's WRB code (``"CH"``) or its name, singular or
    plural, or the start of it (``"Chernozem"``, ``"chern"``); matching
    ignores case. Raises ``ValueError`` for a spec that matches no group or
    more than one.
    """
    selected: set[int] = set()
    for spec in specs:
        key = spec.strip().lower()
        matches = [value for value, (code, name, _, _) in SOIL_GROUPS.items()
                   if key and (key == code.lower() or (len(key) >= 3 and name.lower().startswith(key)))]
        if len(matches) != 1:
            names = ", ".join(f"{name} ({code})" for code, name, _, _ in SOIL_GROUPS.values())
            problem = "Ambiguous" if matches else "Unknown"
            raise ValueError(f"{problem} soil group {spec!r}. Use a name or code: {names}.")
        selected.update(matches)
    return tuple(sorted(selected))


# ---------------------------------------------------------------------------
# Download and reduction
# ---------------------------------------------------------------------------


def _get(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def parse_vrt(text: str | bytes) -> tuple[tuple[float, float, float], list[tuple[str, int, int, int, int]]]:
    """``((west, north, cell), sources)`` of the SoilGrids mosaic.

    Each source is ``(file, x_offset, y_offset, width, height)`` in the
    mosaic's 250 m pixels.
    """
    root = ET.fromstring(text)
    geo = [float(v) for v in (root.findtext("GeoTransform") or "").split(",")]
    sources = []
    for source in root.iter("ComplexSource"):
        name = source.findtext("SourceFilename")
        dst = source.find("DstRect")
        if not name or dst is None:
            continue
        x, y, w, h = (int(float(dst.get(key, "0"))) for key in ("xOff", "yOff", "xSize", "ySize"))
        sources.append((name, x, y, w, h))
    return (geo[0], geo[3], geo[1]), sources


def decode_strips(tiff: bytes) -> np.ndarray:
    """The 8-bit band of a strip-organised, deflate- or un-compressed TIFF."""
    ifd = _read_ifds(tiff)[0]
    width, height = ifd[256][0], ifd[257][0]
    compression = ifd.get(259, (1,))[0]
    if ifd.get(258, (8,))[0] != 8 or compression not in (1, 8, 32946):
        raise SoilDataError(f"unexpected TIFF layout (bits {ifd.get(258)}, compression {compression})")
    data = b"".join(
        zlib.decompress(tiff[offset:offset + count]) if compression != 1 else tiff[offset:offset + count]
        for offset, count in zip(ifd[273], ifd[279])
    )
    grid = np.frombuffer(data, np.uint8)[:width * height].reshape(height, width)
    if ifd.get(317, (1,))[0] == 2:                      # horizontal differencing
        grid = np.cumsum(grid, axis=1, dtype=np.uint8)
    return grid


def majority(grid: np.ndarray, factor: int = FACTOR) -> np.ndarray:
    """Reduce *grid* by *factor* along each side, keeping each block's commonest group.

    Blocks with less than :data:`MIN_SOIL_SHARE` soil become :data:`NODATA`.
    *grid*'s sides must be multiples of *factor*.
    """
    rows, cols = grid.shape[0] // factor, grid.shape[1] // factor
    blocks = grid[:rows * factor, :cols * factor].reshape(rows, factor, cols, factor)
    best = np.full((rows, cols), NODATA, np.uint8)
    best_count = np.zeros((rows, cols), np.int32)
    soil = np.zeros((rows, cols), np.int32)
    # One pass per group present (a file holds only some), so memory stays at the file's size
    for value in np.unique(grid):
        if value not in SOIL_GROUPS:
            continue
        count = (blocks == value).sum(axis=(1, 3), dtype=np.int32)
        soil += count
        better = count > best_count
        best[better] = value
        best_count[better] = count[better]
    best[soil < MIN_SOIL_SHARE * factor * factor] = NODATA
    return best


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "soil")


def _reduce_tile(name: str, tile_dir: Path) -> np.ndarray:
    """One SoilGrids file reduced by :data:`FACTOR`, from the cache or the network."""
    cached = tile_dir / (Path(name).stem + ".npy")
    if cached.is_file():
        return np.load(cached)
    reduced = majority(decode_strips(_get(SOILGRIDS_URL + name)))
    part = cached.with_name(cached.name + ".part.npy")
    np.save(part, reduced)
    os.replace(part, cached)
    return reduced


def ensure_soil_data(cache_dir: str | None = None) -> str:
    """Path to the cached soil group grid, building it from SoilGrids if needed.

    The first run downloads about 220 MB (459 files) and takes a few minutes.
    Raises :class:`SoilDataError` if the data cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"soilgrids_wrb_mostprobable_x{FACTOR}_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached soil groups: %s", path)
        return path
    tile_dir = Path(cache_dir) / f"tiles_x{FACTOR}"
    tile_dir.mkdir(parents=True, exist_ok=True)

    try:
        (west, north, cell), sources = parse_vrt(_get(SOILGRIDS_URL + VRT_NAME))
        width = max(x + w for _, x, _, w, _ in sources)
        height = max(y + h for _, _, y, _, h in sources)
        grid = np.full((height // FACTOR, width // FACTOR), NODATA, np.uint8)
        logger.info("Building the soil map from SoilGrids 2.0 (one-time download, ~220 MB in %d files) …",
                    len(sources))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = {pool.submit(_reduce_tile, name, tile_dir): (x, y) for name, x, y, _, _ in sources}
            for done, job in enumerate(concurrent.futures.as_completed(jobs), 1):
                x, y = jobs[job]
                reduced = job.result()
                r, c = y // FACTOR, x // FACTOR
                grid[r:r + reduced.shape[0], c:c + reduced.shape[1]] = reduced
                if done % 50 == 0 or done == len(sources):
                    logger.info("  soil files read: %d of %d", done, len(sources))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, zlib.error,
            ET.ParseError, TrewarthaDataError, KeyError, ValueError) as exc:
        if isinstance(exc, SoilDataError):
            raise
        raise SoilDataError(f"Could not read SoilGrids: {exc}") from exc

    part = path + ".part.npz"
    np.savez_compressed(part, groups=grid, geo=np.array([west, north, cell * FACTOR]))
    os.replace(part, path)
    shutil.rmtree(tile_dir, ignore_errors=True)        # only needed to resume a download
    logger.info("Soil groups ready: %s", path)
    return path


def load_soil(cache_dir: str | None = None) -> tuple[np.ndarray, tuple[float, float, float]]:
    """The soil group grid (rows north to south) and its ``(west, north, cell)``."""
    with np.load(ensure_soil_data(cache_dir)) as data:
        west, north, cell = (float(v) for v in data["geo"])
        return data["groups"], (west, north, cell)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def soil_rgba(groups: np.ndarray, alpha: float, classes: Sequence[int] | None = None) -> np.ndarray:
    """An RGBA image of *groups*; no data and unselected groups are clear."""
    palette = np.zeros((256, 4), np.uint8)
    for value, (_, _, _, rgb) in SOIL_GROUPS.items():
        if not classes or value in classes:
            palette[value] = (*rgb, round(alpha * 255))
    return palette[groups]


def add_soil_overlay(
    ax: GeoAxes,
    alpha: float = 0.6,
    cache_dir: str | None = None,
    regrid_shape: int = 750,
    classes: Sequence[int] | None = None,
) -> None:
    """Render the soil groups on *ax*, above the tiles and climate colours."""
    groups, (west, north, cell) = load_soil(cache_dir)
    rows, cols = groups.shape
    ax.imshow(
        soil_rgba(groups, alpha, classes),
        origin="upper",
        extent=(west, west + cols * cell, north - rows * cell, north),
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )
    logger.info("Soil overlay rendered (alpha=%.2f).", alpha)


def add_soil_legend(ax: GeoAxes, y: float = -0.01, x: float = 0.5,
                    classes: Sequence[int] | None = None) -> Legend:
    """Add a key below the globe naming each soil group shown.

    *y* is the key's top edge and *x* its horizontal centre, in axes
    coordinates. With a few groups, each is also described.
    """
    shown = [value for value in SOIL_GROUPS if not classes or value in classes]
    described = len(shown) <= _NAMED_KEY_MAX
    handles, labels = [], []
    for value in shown:
        _, name, about, (r, g, b) = SOIL_GROUPS[value]
        handles.append(mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#333333", linewidth=0.6))
        labels.append(f"{name}: {about}" if described else name)
    legend = Legend(
        ax,
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=min(len(handles), 3 if described else 6),
        fontsize=10,
        title="Soil: most probable WRB Reference Soil Group (SoilGrids 2.0)",
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
