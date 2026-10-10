"""Soil property overlay: pH, organic carbon, texture and more, from SoilGrids 2.0.

Data source
-----------
Poggio, L. et al. (2021). SoilGrids 2.0: producing soil information for the
globe with quantified spatial uncertainty. *SOIL* 7, 217-240.
https://doi.org/10.5194/soil-7-217-2021 — the predicted means, aggregated by
ISRIC to 5 km, CC BY 4.0.

Each property and depth is one ~4 MB GeoTIFF of 16-bit integers in the
interrupted Goode Homolosine projection. It is downloaded once, resampled
to a 0.05° latitude / longitude grid (nearest cell) and cached. Unlike the
soil groups, these are measurements, so ISRIC's averaging to 5 km is fine.

Values are drawn in classed bands with a key: pH by the USDA soil reaction
classes, coarse fragments by the FAO classes, and the rest by bands that
spread the world's range.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
import zlib
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from trewartha import TrewarthaDataError, _read_ifds

logger = logging.getLogger(__name__)

PROPERTY_URL = "https://files.isric.org/soilgrids/latest/data_aggregated/5000m/{code}/{code}_{depth}_mean_5000.tif"
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm")
DEFAULT_DEPTH = "0-5cm"
DATA_VERSION = 1
NODATA = -32768

# Output grid: 0.05° from 84°N to 56°S, the extent SoilGrids covers
GRID_NORTH, GRID_SOUTH, GRID_CELL = 84.0, -56.0, 0.05

SOIL_PROPERTY_ATTRIBUTION = "Soil properties: SoilGrids 2.0, ISRIC (Poggio et al. 2021), CC BY 4.0"


class SoilProperty(NamedTuple):
    code: str                       # SoilGrids layer name
    title: str
    unit: str
    divisor: float                  # stored integer / divisor = value in *unit*
    bounds: tuple[float, ...]       # band edges
    labels: tuple[str, ...]         # one per band
    colormap: str
    depths: tuple[str, ...] = DEPTHS


def _bands(bounds: tuple[float, ...], unit: str) -> tuple[str, ...]:
    """Labels "< a", "a–b", …, "≥ z" for the bands between *bounds*."""
    fmt = lambda v: f"{v:g}"
    labels = [f"< {fmt(bounds[0])}"]
    labels += [f"{fmt(a)}–{fmt(b)}" for a, b in zip(bounds, bounds[1:])]
    labels.append(f"≥ {fmt(bounds[-1])}")
    if unit:
        labels[-1] += f" {unit}"
    return tuple(labels)


SOIL_PROPERTIES: dict[str, SoilProperty] = {
    "ph": SoilProperty(
        "phh2o", "Soil pH (in water)", "", 10, (4.5, 5.5, 6.5, 7.5, 8.5),
        ("< 4.5 very strongly acid", "4.5–5.5 strongly acid", "5.5–6.5 acid",
         "6.5–7.5 neutral", "7.5–8.5 alkaline", "≥ 8.5 strongly alkaline"),
        "Spectral"),
    "organic-carbon": SoilProperty(
        "soc", "Soil organic carbon", "g/kg", 10, (5, 10, 20, 40, 80), (), "YlOrBr"),
    "clay": SoilProperty("clay", "Clay content", "%", 10, (10, 20, 30, 40, 50), (), "Oranges"),
    "sand": SoilProperty("sand", "Sand content", "%", 10, (20, 40, 60, 80), (), "YlOrRd"),
    "silt": SoilProperty("silt", "Silt content", "%", 10, (10, 20, 30, 40, 50), (), "Greens"),
    "nitrogen": SoilProperty("nitrogen", "Total nitrogen", "g/kg", 100, (0.5, 1, 2, 4, 8), (), "BuGn"),
    "cec": SoilProperty(
        "cec", "Cation exchange capacity (pH 7)", "cmol(c)/kg", 10, (5, 10, 20, 30, 40), (), "PuBu"),
    "bulk-density": SoilProperty(
        "bdod", "Bulk density of the fine earth", "g/cm³", 100, (1.0, 1.2, 1.4, 1.6), (), "Greys"),
    "coarse-fragments": SoilProperty(
        "cfvo", "Coarse fragments (stones and gravel)", "% by volume", 10, (5, 15, 35, 60),
        ("< 5 few", "5–15 common", "15–35 many", "35–60 abundant", "≥ 60 dominant"), "copper_r"),
    "carbon-stock": SoilProperty(
        "ocs", "Organic carbon stock", "kg/m²", 10, (2, 4, 6, 10, 15), (), "YlOrBr", ("0-30cm",)),
}
for _name, _prop in SOIL_PROPERTIES.items():
    if not _prop.labels:
        SOIL_PROPERTIES[_name] = _prop._replace(labels=_bands(_prop.bounds, _prop.unit))


class SoilPropertyError(RuntimeError):
    """A SoilGrids property map could not be downloaded or read."""


def resolve_soil_property(name: str, depth: str | None = None) -> tuple[SoilProperty, str]:
    """The property called *name* and the depth to show (its only one, or *depth*, default 0-5 cm).

    Raises ``ValueError`` for an unknown property or a depth it lacks.
    """
    prop = SOIL_PROPERTIES.get(name.strip().lower())
    if prop is None:
        raise ValueError(f"Unknown soil property {name!r}. Use one of: {', '.join(SOIL_PROPERTIES)}.")
    if depth is None:
        return prop, prop.depths[0] if DEFAULT_DEPTH not in prop.depths else DEFAULT_DEPTH
    depth = depth.strip().lower().replace(" ", "")
    if depth not in prop.depths:
        raise ValueError(f"{name} is mapped at {', '.join(prop.depths)}, not {depth!r}.")
    return prop, depth


# ---------------------------------------------------------------------------
# Reading and resampling
# ---------------------------------------------------------------------------


def _get(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def decode_int16_tiles(tiff: bytes) -> tuple[np.ndarray, tuple[float, float, float]]:
    """The int16 band of a tiled, deflate-compressed GeoTIFF and its ``(x0, y0, cell)``.

    Handles horizontal differencing (predictor 2), as SoilGrids uses.
    """
    ifd = _read_ifds(tiff)[0]
    width, height = ifd[256][0], ifd[257][0]
    tile_w, tile_h = ifd[322][0], ifd[323][0]
    if ifd[258][0] != 16 or ifd[259][0] not in (8, 32946):
        raise SoilPropertyError(f"unexpected TIFF layout (bits {ifd[258]}, compression {ifd[259]})")
    predictor = ifd.get(317, (1,))[0]
    across = -(-width // tile_w)
    grid = np.full((-(-height // tile_h) * tile_h, across * tile_w), NODATA, np.int16)
    for index, (offset, count) in enumerate(zip(ifd[324], ifd[325])):
        if not count:
            continue
        tile = np.frombuffer(zlib.decompress(tiff[offset:offset + count]), "<i2").reshape(tile_h, tile_w)
        if predictor == 2:
            tile = np.cumsum(tile, axis=1, dtype=np.int16)    # wraps like the encoder
        row, col = divmod(index, across)
        grid[row * tile_h:(row + 1) * tile_h, col * tile_w:(col + 1) * tile_w] = tile
    x0, y0 = ifd[33922][3], ifd[33922][4]
    return grid[:height, :width], (x0, y0, ifd[33550][0])


def to_lat_lon(grid: np.ndarray, x0: float, y0: float, cell: float) -> np.ndarray:
    """Resample an interrupted Goode Homolosine grid to the 0.05° output grid (nearest cell)."""
    rows = round((GRID_NORTH - GRID_SOUTH) / GRID_CELL)
    cols = round(360 / GRID_CELL)
    lats = GRID_NORTH - (np.arange(rows) + 0.5) * GRID_CELL
    lons = -180 + (np.arange(cols) + 0.5) * GRID_CELL
    out = np.full((rows, cols), NODATA, np.int16)
    igh = ccrs.InterruptedGoodeHomolosine()
    lon_row = lons.astype(np.float64)
    for r, lat in enumerate(lats):                      # a row at a time keeps memory small
        xy = igh.transform_points(ccrs.PlateCarree(), lon_row, np.full(cols, lat))
        with np.errstate(invalid="ignore"):
            c = np.floor((xy[:, 0] - x0) / cell)
            rr = np.floor((y0 - xy[:, 1]) / cell)
        ok = np.isfinite(c) & np.isfinite(rr) & (c >= 0) & (c < grid.shape[1]) & (rr >= 0) & (rr < grid.shape[0])
        out[r, ok] = grid[rr[ok].astype(int), c[ok].astype(int)]
    return out


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "soil", "properties")


def ensure_soil_property(prop: SoilProperty, depth: str, cache_dir: str | None = None) -> str:
    """Path to the cached 0.05° grid of *prop* at *depth*, downloading it (~4 MB) if needed.

    Raises :class:`SoilPropertyError` if it cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{prop.code}_{depth}_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached soil %s: %s", prop.code, path)
        return path
    url = PROPERTY_URL.format(code=prop.code, depth=depth)
    logger.info("Downloading SoilGrids %s at %s (one-time, ~4 MB) …", prop.code, depth)
    try:
        grid, (x0, y0, cell) = decode_int16_tiles(_get(url))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, zlib.error,
            TrewarthaDataError, KeyError, ValueError) as exc:
        if isinstance(exc, SoilPropertyError):
            raise
        raise SoilPropertyError(f"Could not read SoilGrids {prop.code} {depth}: {exc}") from exc
    values = to_lat_lon(grid, x0, y0, cell)
    part = path + ".part.npz"
    np.savez_compressed(part, values=values)
    os.replace(part, path)
    return path


def load_soil_property(prop: SoilProperty, depth: str, cache_dir: str | None = None) -> np.ndarray:
    """The property's values in its unit (NaN where there are none), rows north to south."""
    with np.load(ensure_soil_property(prop, depth, cache_dir)) as data:
        raw = data["values"]
    values = raw.astype(np.float32) / prop.divisor
    values[raw == NODATA] = np.nan
    return values


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def band_colours(prop: SoilProperty) -> list[tuple[float, float, float]]:
    """One RGB colour (0-1) per band, from the property's colormap."""
    cmap = matplotlib.colormaps[prop.colormap]
    n = len(prop.labels)
    colours = []
    for i in range(n):
        r, g, b, _ = cmap(0.12 + 0.83 * i / max(n - 1, 1))
        colours.append((float(r), float(g), float(b)))
    return colours


def property_rgba(values: np.ndarray, prop: SoilProperty, alpha: float) -> np.ndarray:
    """An RGBA image of *values* in the property's bands; NaN is clear."""
    palette = np.zeros((len(prop.labels) + 1, 4), np.uint8)
    for i, rgb in enumerate(band_colours(prop)):
        palette[i] = (*(round(c * 255) for c in rgb), round(alpha * 255))
    band = np.digitize(values, prop.bounds)
    band[np.isnan(values)] = len(prop.labels)          # the last palette entry is clear
    return palette[band]


def add_soil_property_overlay(
    ax: GeoAxes,
    prop: SoilProperty,
    depth: str,
    alpha: float = 0.6,
    cache_dir: str | None = None,
    regrid_shape: int = 750,
) -> None:
    """Render *prop* at *depth* on *ax*, above the tiles and climate colours."""
    values = load_soil_property(prop, depth, cache_dir)
    ax.imshow(
        property_rgba(values, prop, alpha),
        origin="upper",
        extent=(-180, 180, GRID_SOUTH, GRID_NORTH),
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )
    logger.info("Soil %s overlay rendered (alpha=%.2f).", prop.code, alpha)


def depth_label(depth: str) -> str:
    """``"0-5cm"`` -> ``"0–5 cm"``."""
    return depth.replace("-", "–").replace("cm", " cm")


def add_soil_property_legend(ax: GeoAxes, prop: SoilProperty, depth: str,
                             y: float = -0.01, x: float = 0.5) -> Legend:
    """Add a key below the globe with the colour of each band."""
    handles = [mpatches.Patch(facecolor=rgb, edgecolor="#333333", linewidth=0.6) for rgb in band_colours(prop)]
    unit = f", {prop.unit}" if prop.unit and prop.code not in ("cfvo",) else ""
    legend = Legend(
        ax,
        handles,
        list(prop.labels),
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=len(handles) if len(handles) <= 6 and max(map(len, prop.labels)) < 12 else 3,
        fontsize=10,
        title=f"{prop.title}{unit}, {depth_label(depth)} (SoilGrids 2.0)",
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
