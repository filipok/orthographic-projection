"""Livestock density: cattle, buffaloes, horses, sheep, goats, pigs, chickens or ducks.

Data source
-----------
Gilbert, M., Nicolas, G., Cinardi, G., et al. — Gridded Livestock of the
World, version 4 (GLW 4), FAO: the head of each kind of livestock in 2015 in
every 5 arc-minute cell (~10 km), from subnational census counts spread by a
model of where animals are kept (the "dasymetric" maps). Harvard Dataverse,
CC0; each species is its own dataset (see :data:`SPECIES`).

Each species' map (~10 MB) is downloaded once, turned into head per km² and
cached in classed bands under ``~/.cache/ortho_tiles/livestock/``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.request
from typing import NamedTuple

import numpy as np
from PIL import Image

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from biomes import _key

logger = logging.getLogger(__name__)

DATAVERSE_URL = "https://dataverse.harvard.edu"
DATA_VERSION = 1
YEAR = 2015
LIVESTOCK_ATTRIBUTION = f"Livestock: Gridded Livestock of the World v4, {YEAR} (FAO), CC0"

# Species -> (dataset DOI on Harvard Dataverse, plural label, poultry)
SPECIES: dict[str, tuple[str, str, bool]] = {
    "cattle": ("doi:10.7910/DVN/LHBICE", "Cattle", False),
    "buffaloes": ("doi:10.7910/DVN/I1WCAB", "Buffaloes", False),
    "horses": ("doi:10.7910/DVN/JJGCTX", "Horses", False),
    "sheep": ("doi:10.7910/DVN/VZOYHM", "Sheep", False),
    "goats": ("doi:10.7910/DVN/YYG6ET", "Goats", False),
    "pigs": ("doi:10.7910/DVN/CIVCPB", "Pigs", False),
    "chickens": ("doi:10.7910/DVN/SXHLF3", "Chickens", True),
    "ducks": ("doi:10.7910/DVN/S9ONXV", "Ducks", True),
}
# Head per km² where each band starts (band 0: fewer than the first); poultry are counted in far greater numbers
MAMMAL_BANDS = (1, 5, 10, 25, 50, 100, 250)
POULTRY_BANDS = (10, 50, 100, 250, 500, 1000, 2500)
BAND_COLOURS: tuple[tuple[int, int, int], ...] = (
    (247, 240, 220), (228, 214, 170), (206, 184, 120), (180, 150, 80),
    (150, 115, 55), (118, 82, 38), (86, 54, 26),
)


class LivestockDataError(RuntimeError):
    """A livestock map could not be downloaded or read."""


class LivestockGrid(NamedTuple):
    species: str
    bands: np.ndarray     # (rows, cols) uint8 north to south from 90°N, 255 where none or no data


def resolve_species(name: str) -> str:
    """*name* as one of :data:`SPECIES` (``"cow"`` or ``"chicken"`` will do); raises ``ValueError``."""
    key = name.strip().lower()
    aliases = {"cow": "cattle", "cows": "cattle", "buffalo": "buffaloes", "horse": "horses", "goat": "goats",
               "pig": "pigs", "chicken": "chickens", "duck": "ducks", "poultry": "chickens"}
    key = aliases.get(key, key)
    if key not in SPECIES:
        raise ValueError(f"Unknown livestock {name!r}; use one of {', '.join(SPECIES)}.")
    return key


def bands_of(species: str) -> tuple[int, ...]:
    return POULTRY_BANDS if SPECIES[species][2] else MAMMAL_BANDS


def cell_area_km2(rows: int, cols: int) -> np.ndarray:
    """Area (km²) of each row's cells on a global grid of *rows* × *cols* from 90°N."""
    radius = 6371.0072
    edges = np.radians(90 - np.arange(rows + 1) * 180 / rows)
    return radius ** 2 * np.radians(360 / cols) * (np.sin(edges[:-1]) - np.sin(edges[1:]))


def classify(heads: np.ndarray, species: str) -> np.ndarray:
    """Band of each cell's density (head per km²); 255 where there are none, or no data."""
    rows, cols = heads.shape
    with np.errstate(invalid="ignore"):
        density = heads / cell_area_km2(rows, cols)[:, None]
        bands = np.digitize(density, bands_of(species)).astype(np.uint8)
        bands[~(density > 0) | ~np.isfinite(density)] = 255
    return bands


def _get(url: str, timeout: float = 300) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "livestock")


def load_livestock(species: str, cache_dir: str | None = None) -> LivestockGrid:
    """*species*' density bands, downloading its map once (~10 MB)."""
    species = resolve_species(species)
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"glw4_{species}_{YEAR}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        doi = SPECIES[species][0]
        logger.info("Downloading the %s map from Gridded Livestock of the World (one-time, ~10 MB) …", species)
        try:
            listing = json.loads(_get(f"{DATAVERSE_URL}/api/datasets/:persistentId/?persistentId={doi}"))
            files = listing["data"]["latestVersion"]["files"]
            file_id = next(f["dataFile"]["id"] for f in files
                           if f["dataFile"]["filename"].endswith(f"_{YEAR}_Da.tif"))
            image = Image.open(io.BytesIO(_get(f"{DATAVERSE_URL}/api/access/datafile/{file_id}")))
            heads = np.asarray(image, dtype=np.float32)
        except Exception as exc:              # network, listing and image errors alike
            raise LivestockDataError(f"Could not read the {species} map: {exc}") from exc
        part = path + ".part.npz"
        np.savez_compressed(part, bands=classify(heads, species))
        os.replace(part, path)
    with np.load(path) as data:
        return LivestockGrid(species, data["bands"])


def livestock_rgba(bands: np.ndarray, alpha: float = 0.7) -> np.ndarray:
    palette = np.zeros((256, 4), np.uint8)
    for i, rgb in enumerate(BAND_COLOURS):
        palette[i + 1] = (*rgb, round(alpha * 255))      # band 0 (fewer than the first step) stays clear
    return palette[bands]


def add_livestock_overlay(ax: GeoAxes, grid: LivestockGrid, alpha: float = 0.7, regrid_shape: int = 750) -> None:
    """Draw the density bands on *ax*."""
    ax.imshow(livestock_rgba(grid.bands, alpha), origin="upper", extent=(-180, 180, -90, 90),
              transform=ccrs.PlateCarree(), interpolation="nearest", regrid_shape=regrid_shape, zorder=5)
    logger.info("Livestock (%s) drawn (alpha=%.2f).", grid.species, alpha)


def add_livestock_legend(ax: GeoAxes, species: str, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the density bands of *species*."""
    steps = bands_of(species)
    handles = [mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#333333", linewidth=0.6)
               for r, g, b in BAND_COLOURS]
    labels = [f"{a:,}-{b:,}" for a, b in zip(steps, steps[1:])] + [f"{steps[-1]:,} and more"]
    title = f"{SPECIES[species][1]}, {YEAR}: head per km² (Gridded Livestock of the World v4)"
    return _key(ax, handles, labels, title, len(handles), x, y)
