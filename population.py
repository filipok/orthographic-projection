"""Population density, from NASA's Gridded Population of the World.

Data source
-----------
Center for International Earth Science Information Network (CIESIN),
Columbia University (2018). Gridded Population of the World, Version 4
(GPWv4): Population Density, Revision 11. NASA SEDAC.
https://doi.org/10.7927/H49C6VHW — people per km² in 2000, 2005, 2010, 2015
and 2020, CC BY 4.0, served by NASA GIBS as map tiles (zoom 7, ~1.2 km).

GIBS colours the tiles with a scale of 36 steps; each colour is turned back
into its density, and the densities are drawn in the classed bands of
:data:`DENSITY_BANDS`. Tiles are cached with the other GIBS tiles.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from pathlib import Path

import numpy as np

import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from vegetation import GibsTiles, _key, _pack

logger = logging.getLogger(__name__)

YEARS = (2000, 2005, 2010, 2015, 2020)
LATEST_YEAR = YEARS[-1]
LAYER = "GPW_Population_Density_{year}"
MATRIX = "GoogleMapsCompatible_Level7"
COLORMAP_URL = "https://gibs.earthdata.nasa.gov/colormaps/v1.3/GPW_Population_Density_{year}.xml"
POPULATION_ZOOM = 5

# People per km²: (lowest density of the band, key label, colour)
DENSITY_BANDS: list[tuple[float, str, tuple[int, int, int]]] = [
    (0, "Under 1", (255, 250, 228)),
    (1, "1", (254, 232, 170)),
    (10, "10", (254, 204, 116)),
    (25, "25", (253, 168, 76)),
    (50, "50", (246, 122, 52)),
    (100, "100", (226, 76, 38)),
    (250, "250", (190, 32, 38)),
    (500, "500", (140, 10, 40)),
    (1000, "1,000 and more", (80, 0, 40)),
]


class PopulationDataError(RuntimeError):
    """The population colour scale could not be read."""


def population_attribution(year: int) -> str:
    return f"Population density: GPWv4.11 {year} (CIESIN 2018), NASA SEDAC, via NASA GIBS, CC BY 4.0"


def resolve_population_year(year: int | str | None) -> int:
    """*year* as one of :data:`YEARS` (default the latest); raises ``ValueError`` otherwise."""
    if year is None or year == "":
        return LATEST_YEAR
    try:
        value = int(year)
    except (TypeError, ValueError):
        value = -1
    if value not in YEARS:
        raise ValueError(f"Population density is mapped for {', '.join(map(str, YEARS))}, not {year!r}.")
    return value


def population_tiles(year: int, cache_dir: str | None = None, use_cache: bool = True) -> GibsTiles:
    return GibsTiles(LAYER.format(year=year), MATRIX, "default", cache_dir, use_cache=use_cache)


def parse_colormap(xml: str) -> tuple[np.ndarray, np.ndarray]:
    """``(packed RGB, lowest density)`` of each step of a GIBS colour map, sorted by colour."""
    entries = re.findall(r'rgb="(\d+),(\d+),(\d+)" transparent="false" sourceValue="\[([\d.]+),', xml)
    if not entries:
        raise PopulationDataError("the GIBS colour map has no steps")
    keys = np.array([(int(r) << 16) | (int(g) << 8) | int(b) for r, g, b, _ in entries])
    lows = np.array([float(low) for _, _, _, low in entries])
    order = np.argsort(keys)
    return keys[order], lows[order]


def load_colormap(year: int, cache_dir: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """The colour map of *year*'s tiles, from the cache or GIBS."""
    path = Path(cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles")) / "gibs" / \
        LAYER.format(year=year) / "colormap.xml"
    if not path.is_file():
        try:
            request = urllib.request.Request(COLORMAP_URL.format(year=year), headers={"User-Agent": "ortho/1.0"})
            with urllib.request.urlopen(request, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:
            raise PopulationDataError(f"Could not read the population colour map: {exc}") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(data)
        os.replace(part, path)
    return parse_colormap(path.read_text(encoding="utf-8"))


def densities(mosaic: np.ndarray, colormap: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """The lowest density of each pixel's step; NaN for no data and unknown colours."""
    keys, lows = colormap
    packed = _pack(mosaic[..., :3])
    index = np.clip(np.searchsorted(keys, packed), 0, len(keys) - 1)
    found = (keys[index] == packed) & (mosaic[..., 3] > 0)
    return np.where(found, lows[index], np.nan)


def population_rgba(mosaic: np.ndarray, extent: object = None, alpha: float = 0.7,
                    colormap: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
    """The mosaic in :data:`DENSITY_BANDS`; no data stays clear."""
    if colormap is None:
        raise ValueError("population_rgba needs the year's colour map")
    density = densities(mosaic, colormap)
    bounds = np.array([b for b, _, _ in DENSITY_BANDS[1:]])
    colours = np.array([rgb for _, _, rgb in DENSITY_BANDS], np.uint8)
    out = np.zeros(mosaic.shape[:2] + (4,), np.uint8)
    known = np.isfinite(density)
    out[known, :3] = colours[np.digitize(density[known], bounds)]
    out[known, 3] = round(alpha * 255)
    return out


def add_population_legend(ax: GeoAxes, year: int, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the density bands."""
    handles = [mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#333333", linewidth=0.6)
               for _, _, (r, g, b) in DENSITY_BANDS]
    labels = [label for _, label, _ in DENSITY_BANDS]
    return _key(ax, handles, labels, f"Population density, {year}: people per km² (GPWv4)", len(handles), y, x)
