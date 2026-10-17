"""Malaria: the parasite rate of *Plasmodium falciparum* or *P. vivax*, 2000-2025.

Data source
-----------
The Malaria Atlas Project (2026 release): the share of people carrying
detectable parasites each year from 2000, modelled from surveys and routine
case reports on a 5 km grid. For *P. falciparum* the rate is of children aged
2 to 10 (PfPR2-10); for *P. vivax*, of people of all ages. Each map has a
mask: for *P. falciparum*, sparsely populated cells of South America; for
*P. vivax*, cells where transmission is very low. Both are left blank, as
MAP draws them. https://malariaatlas.org, CC BY 3.0.

Each map is read at a quarter of its resolution (~10 km, ~40 MB) from MAP's
WCS service, once, and cached in classed bands under
``~/.cache/ortho_tiles/malaria/``.
"""

from __future__ import annotations

import io
import logging
import os
import urllib.parse
import urllib.request
from typing import NamedTuple

import numpy as np
from PIL import TiffImagePlugin

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from biomes import _key

logger = logging.getLogger(__name__)

WCS_URL = "https://data.malariaatlas.org/geoserver/ows"
RELEASE = "202608"
FIRST_YEAR, LATEST_YEAR = 2000, 2025
SCALE = 0.25                    # of the 5 km grid: ~10 km cells
EXTENT = (-180.0, 180.0, -60.0, 85.0)
DATA_VERSION = 1
MALARIA_ATTRIBUTION = "Malaria: Malaria Atlas Project (2026 release), CC BY 3.0"

# Parasite -> (coverage, key title, where each band starts as a share of people)
PARASITES: dict[str, tuple[str, str, tuple[float, ...]]] = {
    "pf": (f"Malaria__{RELEASE}_Global_Pf_Parasite_Rate",
           "Plasmodium falciparum parasite rate, children aged 2-10",
           (0.001, 0.01, 0.05, 0.1, 0.2, 0.4, 0.6)),
    "pv": (f"Malaria__{RELEASE}_Global_Pv_Parasite_Rate",
           "Plasmodium vivax parasite rate, all ages",
           (0.001, 0.005, 0.01, 0.02, 0.03, 0.05, 0.07)),
}
BAND_COLOURS: tuple[tuple[int, int, int], ...] = (
    (255, 245, 190), (254, 217, 118), (254, 178, 76), (253, 141, 60),
    (240, 59, 32), (189, 0, 38), (110, 0, 40),
)


class MalariaDataError(RuntimeError):
    """A malaria map could not be downloaded or read."""


class MalariaGrid(NamedTuple):
    parasite: str
    year: int
    bands: np.ndarray     # (rows, cols) uint8 over EXTENT from 85°N, 255 where below the first band or no data


def resolve_parasite(name: str) -> str:
    """*name* as ``"pf"`` or ``"pv"`` (``"falciparum"`` or ``"vivax"`` will do); raises ``ValueError``."""
    key = name.strip().lower().replace("p. ", "").replace("plasmodium ", "")
    key = {"falciparum": "pf", "vivax": "pv"}.get(key, key)
    if key not in PARASITES:
        raise ValueError(f"Unknown malaria parasite {name!r}; use pf (falciparum) or pv (vivax).")
    return key


def resolve_year(year: int) -> int:
    if not FIRST_YEAR <= year <= LATEST_YEAR:
        raise ValueError(f"Malaria is mapped for {FIRST_YEAR}-{LATEST_YEAR}, not {year}.")
    return year


def coverage_url(parasite: str, year: int) -> str:
    """The WCS request for *parasite*'s map of *year*: all four bands, at :data:`SCALE`."""
    query = {"service": "WCS", "version": "2.0.1", "request": "GetCoverage", "coverageId": PARASITES[parasite][0],
             "format": "image/tiff", "SCALEFACTOR": SCALE, "subset": f'time("{year}-01-01T00:00:00.000Z")'}
    return f"{WCS_URL}?{urllib.parse.urlencode(query)}"


def read_tiff(data: bytes) -> np.ndarray:
    """The (rows, cols, samples) float array of an uncompressed float32 TIFF, tiled or in strips.

    Pillow parses the tags but cannot decode several float samples a pixel,
    which MAP's maps have (the rate, the mask and two confidence bounds).
    """
    if data[:2] not in (b"II", b"MM"):
        raise MalariaDataError(f"not a TIFF: {data[:80]!r}")
    order = "<" if data[:2] == b"II" else ">"
    tags = TiffImagePlugin.ImageFileDirectory_v2(ifh=data[:8])
    stream = io.BytesIO(data)
    stream.seek(tags.next)
    tags.load(stream)
    width, height = tags[256], tags[257]
    samples = tags.get(277, 1)
    bits = tags.get(258, (32,))
    if tags.get(259, 1) != 1 or tags.get(339, (3,))[0] != 3 or set(np.atleast_1d(bits)) != {32} \
            or tags.get(284, 1) != 1:
        raise MalariaDataError("expected an uncompressed float32 TIFF with its samples together")
    if 322 in tags:
        tile_w, tile_h, offsets = tags[322], tags[323], tags[324]
    else:
        tile_w, tile_h, offsets = width, tags.get(278, height), tags[273]
    out = np.empty((height, width, samples), np.float32)
    across = -(-width // tile_w)
    for k, offset in enumerate(np.atleast_1d(offsets)):
        row, col = divmod(k, across)
        h, w = min(tile_h, height - row * tile_h), min(tile_w, width - col * tile_w)
        rows = tile_h if 322 in tags else h          # a strip holds only the rows it has
        tile = np.frombuffer(data, f"{order}f4", rows * tile_w * samples, int(offset)).reshape(rows, tile_w, samples)
        out[row * tile_h:row * tile_h + h, col * tile_w:col * tile_w + w] = tile[:h, :w]
    return out


def classify(rate: np.ndarray, mask: np.ndarray, parasite: str) -> np.ndarray:
    """Band of each cell's parasite rate; 255 below the first band, masked or without data."""
    with np.errstate(invalid="ignore"):
        bands = (np.digitize(rate, PARASITES[parasite][2]) - 1).astype(np.int16)
        blank = (bands < 0) | ~np.isfinite(rate) | (rate < 0) | ((mask != 0) & (mask > -9000))
    bands[blank] = 255
    return bands.astype(np.uint8)


def _get(url: str, timeout: float = 600) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "malaria")


def load_malaria(parasite: str, year: int = LATEST_YEAR, cache_dir: str | None = None) -> MalariaGrid:
    """*parasite*'s parasite rate bands in *year*, downloading its map once (~40 MB)."""
    parasite, year = resolve_parasite(parasite), resolve_year(year)
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"map_{parasite}_{year}_{RELEASE}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Downloading the %s parasite rate of %d from the Malaria Atlas Project (one-time, ~40 MB) …",
                    parasite.upper(), year)
        try:
            grid = read_tiff(_get(coverage_url(parasite, year)))
            bands = classify(grid[..., 0], grid[..., 1], parasite)
        except Exception as exc:              # network and file errors alike
            raise MalariaDataError(f"Could not read the {parasite.upper()} map of {year}: {exc}") from exc
        part = path + ".part.npz"
        np.savez_compressed(part, bands=bands)
        os.replace(part, path)
    with np.load(path) as data:
        return MalariaGrid(parasite, year, data["bands"])


def malaria_rgba(bands: np.ndarray, alpha: float = 0.75) -> np.ndarray:
    palette = np.zeros((256, 4), np.uint8)
    for i, rgb in enumerate(BAND_COLOURS):
        palette[i] = (*rgb, round(alpha * 255))
    return palette[bands]


def add_malaria_overlay(ax: GeoAxes, grid: MalariaGrid, alpha: float = 0.75, regrid_shape: int = 750) -> None:
    """Draw the parasite rate bands on *ax*."""
    ax.imshow(malaria_rgba(grid.bands, alpha), origin="upper", extent=EXTENT, transform=ccrs.PlateCarree(),
              interpolation="nearest", regrid_shape=regrid_shape, zorder=5)
    logger.info("Malaria (%s, %d) drawn (alpha=%.2f).", grid.parasite.upper(), grid.year, alpha)


def _percent(share: float) -> str:
    return f"{share * 100:g}%"


def add_malaria_legend(ax: GeoAxes, parasite: str, year: int, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the parasite rate bands."""
    _, title, steps = PARASITES[parasite]
    handles = [mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#333333", linewidth=0.6)
               for r, g, b in BAND_COLOURS]
    labels = [f"{_percent(a)}-{_percent(b)}" for a, b in zip(steps, steps[1:])] + [f"{_percent(steps[-1])} and more"]
    return _key(ax, handles, labels, f"Malaria, {year}: {title} (Malaria Atlas Project)", len(handles), x, y)
