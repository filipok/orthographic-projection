"""Climate means: air temperature, precipitation and relative humidity, by season or month.

Data source
-----------
Karger, D. N., Conrad, O., Böhner, J., Kawohl, T., Kreft, H., Soria-Auza,
R. W., Zimmermann, N. E., Linder, H. P. & Kessler, M. (2017). Climatologies at
high resolution for the earth's land surface areas. *Scientific Data* 4,
170122. https://doi.org/10.1038/sdata.2017.122 — version 2.1 monthly means
for 1981-2010 of the daily mean near-surface air temperature (``tas``), the
precipitation (``pr``, bias-corrected with GPCC gauges) and the near-surface
relative humidity (``hurs``), CC0 1.0 (public domain).

As for the Trewartha layer, only the ~15 km copy inside each 1 km
Cloud-Optimised GeoTIFF is read: 2-5 MB per month and variable, fetched the
first time a map needs it and cached. CHELSA is a land dataset, so the sea is
left to the base map, cut along Natural Earth's coast.

What is drawn
-------------
For a season or the year, temperature and humidity are the mean of the
months, weighted by their length, and precipitation is the total that falls
in them. Each value is coloured by the band it falls in, with a key.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

import numpy as np
from PIL import Image, ImageDraw

import cartopy.crs as ccrs
import cartopy.io.shapereader as shapereader
import matplotlib.patches as mpatches
import matplotlib.text as mtext
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.artist import Artist
from matplotlib.legend import Legend
from matplotlib.legend_handler import HandlerBase

from elevation import land_and_lakes
from trewartha import CHELSA_URL, OVERVIEW_LEVEL, TrewarthaDataError, read_cog_level
from wind import DAYS_IN_MONTH, WindPeriod, resolve_wind_period

logger = logging.getLogger(__name__)

DATA_VERSION = 1
CLIMATE_ATTRIBUTION = "Climate means: CHELSA v2.1, 1981–2010 (Karger et al. 2017), CC0"
_NODATA = 65535

# The coast is cut from the 1:10m Natural Earth land from this map zoom
FINE_COAST_ZOOM = 7
# The grid is resampled at most this much finer than its ~15 km cells, into at most this many cells
MAX_UPSAMPLE = 4
MAX_CELLS = 8_000_000


class ClimateVariable(NamedTuple):
    key: str                    # "temperature", "precipitation" or "humidity"
    chelsa: str                 # CHELSA's variable name
    title: str
    unit: str
    scale: float                # stored integer × scale + offset = value in *unit*
    offset: float
    total: bool                 # summed over the months (else their mean)
    colours: tuple[str, ...]    # one per band, from the lowest


# 17 bands of 5 °C from below -40 °C to 35 °C and above: purples and blues
# below freezing, greens and yellows for the mild bands, oranges and reds for the hot
_TEMPERATURE_EDGES = tuple(range(-40, 40, 5))
_TEMPERATURE_COLOURS = (
    "#3f0d5c", "#561a85", "#6a35aa", "#5a55c4", "#4573cc", "#3a92d4", "#4fb0dc", "#7ccbe4", "#b0e0ec",
    "#d3eba4", "#a6d672", "#e6dc5c", "#f6bd4f", "#f19340", "#de5f2e", "#bf3326", "#7f1620",
)
# Precipitation: browns for the driest bands, then greens, blues and purples
_PRECIPITATION_COLOURS = (
    "#a8743a", "#d4a865", "#eed9a0", "#e9efb5", "#b3dd95", "#6cc08a", "#2fa198", "#2b78b3",
    "#33479e", "#4b1d82",
)
# Band edges in mm for the total of one month, a season of three and the year
_PRECIPITATION_EDGES = {
    1: (10, 25, 50, 75, 100, 150, 200, 300, 400),
    3: (25, 50, 100, 150, 250, 400, 600, 900, 1200),
    12: (100, 250, 500, 750, 1000, 1500, 2000, 3000, 4000),
}
_HUMIDITY_EDGES = (20, 30, 40, 50, 60, 70, 80, 90)
_HUMIDITY_COLOURS = (
    "#8c510a", "#b8782b", "#d6a956", "#ead08a", "#c9dd8c", "#8fc98a", "#4fae8f", "#2a8a96", "#1f5c8f",
)

CLIMATE_VARIABLES: dict[str, ClimateVariable] = {
    "temperature": ClimateVariable("temperature", "tas", "Air temperature", "°C", 0.1, -273.15, False,
                                   _TEMPERATURE_COLOURS),
    "precipitation": ClimateVariable("precipitation", "pr", "Precipitation", "mm", 0.1, 0.0, True,
                                     _PRECIPITATION_COLOURS),
    "humidity": ClimateVariable("humidity", "hurs", "Relative humidity", "%", 0.01, 0.0, False,
                                _HUMIDITY_COLOURS),
}


class ClimateDataError(RuntimeError):
    """The CHELSA climate means could not be downloaded or read."""


def resolve_climate_period(variable: str, spec: str | None) -> WindPeriod:
    """The months of *spec* (see :func:`wind.resolve_wind_period`) for a map of *variable*."""
    return resolve_wind_period(spec, kind=variable)


def band_edges(variable: ClimateVariable, period: WindPeriod) -> tuple[float, ...]:
    """Upper edges of every band but the last, for *variable* over *period*."""
    if variable.key == "temperature":
        return _TEMPERATURE_EDGES
    if variable.key == "humidity":
        return _HUMIDITY_EDGES
    return _PRECIPITATION_EDGES[len(period.months)]


# ---------------------------------------------------------------------------
# Download and cache, one file per variable and month
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "climate")


def _month_path(cache_dir: str, chelsa: str, month: int) -> str:
    return os.path.join(cache_dir, f"chelsa21_{chelsa}_{month:02d}_1981-2010_level{OVERVIEW_LEVEL}"
                                   f"_v{DATA_VERSION}.npz")


def _fetch_month(chelsa: str, month: int, path: str) -> None:
    grid, geo = read_cog_level(CHELSA_URL.format(var=chelsa, month=month), OVERVIEW_LEVEL)
    part = path + ".part.npz"
    np.savez_compressed(part, values=grid, geo=np.array(geo))
    os.replace(part, path)


def ensure_months(variable: ClimateVariable, months: Sequence[int], cache_dir: str | None = None) -> list[str]:
    """Paths of the cached grids of *variable* for *months*, downloading the missing ones.

    Raises :class:`ClimateDataError` if one cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    paths = [_month_path(cache_dir, variable.chelsa, m) for m in months]
    missing = [(m, p) for m, p in zip(months, paths) if not os.path.isfile(p)]
    if missing:
        logger.info("Downloading CHELSA v2.1 %s for %d month(s) (one-time, 2-5 MB each) …",
                    variable.chelsa, len(missing))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs = [pool.submit(_fetch_month, variable.chelsa, m, p) for m, p in missing]
            try:
                for job in jobs:
                    job.result()
            except TrewarthaDataError as exc:
                raise ClimateDataError(str(exc)) from exc
    return paths


def load_climate(
    variable: ClimateVariable, period: WindPeriod, cache_dir: str | None = None,
) -> tuple[np.ndarray, tuple[float, float, float]]:
    """*variable* over *period* in its unit (NaN where there is no data), rows north to south,
    and the grid's ``(west, north, cell)``."""
    total = None
    weight = 0.0
    geo = None
    for month, path in zip(period.months, ensure_months(variable, period.months, cache_dir)):
        with np.load(path) as data:
            raw = data["values"]
            geo = geo or tuple(float(v) for v in data["geo"])
        values = raw.astype(np.float32) * variable.scale + variable.offset
        values[raw == _NODATA] = np.nan
        days = 1.0 if variable.total else DAYS_IN_MONTH[month - 1]
        total = values * days if total is None else total + values * days
        weight += days
    assert total is not None and geo is not None
    return total if variable.total else total / weight, geo  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Resampling over the visible part of the globe
# ---------------------------------------------------------------------------


class View(NamedTuple):
    """A lon/lat box around what an axes shows, in longitudes relative to *centre_lon*."""
    centre_lon: float
    west: float                 # -180 … 180, relative to centre_lon
    east: float
    south: float
    north: float


def visible_box(ax: GeoAxes, margin: float = 0.5) -> View:
    """The lon/lat box of the disc *ax* shows, widened by *margin* degrees."""
    projection = ax.projection
    x0, x1, y0, y1 = ax.get_extent(projection)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radius = min(x1 - x0, y1 - y0) / 2
    geodetic = ccrs.PlateCarree()
    centre_lon = float(geodetic.transform_point(cx, cy, projection)[0])

    # Points over the disc and around its rim, back to lon/lat
    xs, ys = np.meshgrid(np.linspace(-1, 1, 81), np.linspace(-1, 1, 81))
    inside = np.hypot(xs, ys) <= 1
    angle = np.linspace(0, 2 * np.pi, 721)
    px = np.concatenate([xs[inside], 0.999999 * np.cos(angle)]) * radius + cx
    py = np.concatenate([ys[inside], 0.999999 * np.sin(angle)]) * radius + cy
    lonlat = geodetic.transform_points(projection, px, py)[:, :2]
    lonlat = lonlat[np.all(np.isfinite(lonlat), axis=1)]
    rel = (lonlat[:, 0] - centre_lon + 180) % 360 - 180
    south, north = lonlat[:, 1].min() - margin, lonlat[:, 1].max() + margin
    west, east = rel.min() - margin, rel.max() + margin

    # A pole on show: every longitude meets there
    for pole in (90, -90):
        xy = projection.transform_point(0, pole, geodetic)
        if np.all(np.isfinite(xy)) and np.hypot(xy[0] - cx, xy[1] - cy) < 0.999 * radius:
            west, east = -180.0, 180.0
            south, north = (south, 90.0) if pole > 0 else (-90.0, north)
    return View(centre_lon, max(west, -180.0), min(east, 180.0), max(south, -90.0), min(north, 90.0))


def resample(
    values: np.ndarray, geo: tuple[float, float, float], view: View, cell: float,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """*values* bilinearly resampled to cells of *cell* degrees over *view*.

    Returns the grid (rows north to south) and its extent ``(west, east,
    south, north)`` in longitudes relative to ``view.centre_lon``. Cells
    beyond the data's latitudes are NaN.
    """
    west0, north0, step = geo
    rows, cols = values.shape
    n_cols = max(2, int(np.ceil((view.east - view.west) / cell)))
    n_rows = max(2, int(np.ceil((view.north - view.south) / cell)))
    lons = view.centre_lon + view.west + (np.arange(n_cols) + 0.5) * cell
    lats = view.north - (np.arange(n_rows) + 0.5) * cell

    # Fractional row and column of each cell centre in the source grid; the
    # first column is repeated after the last, for the date line
    r = (north0 - lats) / step - 0.5
    c = ((lons - west0) / step - 0.5) % cols
    beyond = (r < -0.5) | (r > rows - 0.5)
    r = np.clip(r, 0, rows - 1)
    r0 = np.minimum(r.astype(int), rows - 2)
    wr = (r - r0).astype(np.float32)[:, None]
    by_row = values[r0] * (1 - wr) + values[r0 + 1] * wr
    by_row = np.concatenate([by_row, by_row[:, :1]], axis=1)
    c0 = c.astype(int)
    wc = (c - c0).astype(np.float32)
    out = by_row[:, c0] * (1 - wc) + by_row[:, c0 + 1] * wc
    out[beyond] = np.nan
    extent = (view.west, view.west + n_cols * cell, view.north - n_rows * cell, view.north)
    return out, extent


def land_mask(
    shape: tuple[int, int], extent: tuple[float, float, float, float], centre_lon: float, scale: str = "50m",
    natural_earth: Callable[..., str] = shapereader.natural_earth,
) -> np.ndarray:
    """True where a grid of *shape* over *extent* (as from :func:`resample`) is on land, not a lake."""
    rows, cols = shape
    west, east, south, north = extent
    land, lakes = land_and_lakes(scale, natural_earth)
    image = Image.new("1", (cols, rows), 0)
    draw = ImageDraw.Draw(image)

    def draw_ring(ring: Any, fill: int, shift: float) -> None:
        lon, lat = np.asarray(ring.coords).T[:2]
        x = (lon - centre_lon + shift - west) / (east - west) * cols
        y = (north - lat) / (north - south) * rows
        draw.polygon(list(zip(x, y)), fill=fill)

    def draw_polygon(polygon: Any, fill: int, holes: bool) -> None:
        lon0, lat0, lon1, lat1 = polygon.bounds
        if lat1 < south or lat0 > north:
            return
        for shift in (-360.0, 0.0, 360.0):
            if lon1 - centre_lon + shift < west or lon0 - centre_lon + shift > east:
                continue
            draw_ring(polygon.exterior, fill, shift)
            if holes:
                for hole in polygon.interiors:
                    draw_ring(hole, 0, shift)

    for polygon in land:
        draw_polygon(polygon, 1, holes=True)
    for lake in lakes:
        draw_polygon(lake, 0, holes=False)
    return np.asarray(image, dtype=bool)


def climate_rgba(values: np.ndarray, edges: Sequence[float], colours: Sequence[str], alpha: float,
                 mask: np.ndarray | None = None) -> np.ndarray:
    """An RGBA image of *values* coloured by band; NaN and cells outside *mask* are clear."""
    palette = np.zeros((len(colours) + 1, 4), np.uint8)
    for i, colour in enumerate(colours):
        rgb = tuple(int(colour[k:k + 2], 16) for k in (1, 3, 5))
        palette[i] = (*rgb, round(alpha * 255))
    band = np.digitize(values, edges)
    band[np.isnan(values)] = len(colours)              # the last palette entry is clear
    if mask is not None:
        band[~mask] = len(colours)
    return palette[band]


def add_climate_overlay(
    ax: GeoAxes,
    variable: ClimateVariable,
    period: WindPeriod,
    alpha: float = 0.6,
    cache_dir: str | None = None,
    regrid_shape: int = 750,
    zoom: int = 3,
) -> None:
    """Render *variable* over *period* on the land of *ax*, above the tiles."""
    values, geo = load_climate(variable, period, cache_dir)
    view = visible_box(ax)
    native = geo[2]
    width, height = view.east - view.west, view.north - view.south
    # Fine enough for the output's pixels, within the upsampling and size caps
    cell = max(native / MAX_UPSAMPLE, max(width, height) / regrid_shape,
               float(np.sqrt(width * height / MAX_CELLS)))
    cell = min(cell, native)
    grid, extent = resample(values, geo, view, cell)
    mask = land_mask(grid.shape, extent, view.centre_lon, scale="10m" if zoom >= FINE_COAST_ZOOM else "50m")
    ax.imshow(
        climate_rgba(grid, band_edges(variable, period), variable.colours, alpha, mask),
        origin="upper",
        extent=extent,
        transform=ccrs.PlateCarree(central_longitude=view.centre_lon),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )
    logger.info("%s overlay rendered for %s (%d × %d cells of %.3f°, alpha=%.2f).",
                variable.title, period.label, grid.shape[1], grid.shape[0], cell, alpha)


# ---------------------------------------------------------------------------
# Key
# ---------------------------------------------------------------------------


def _number(value: float) -> str:
    """``-5`` -> ``"−5"``, ``1000`` -> ``"1,000"``."""
    return f"{value:,g}".replace("-", "−")


class _ColourBar(Artist):
    """Stands for the key's one entry: the band colours with their edges labelled."""

    def __init__(self, colours: Sequence[str], edges: Sequence[float]) -> None:
        super().__init__()
        self.colours = tuple(colours)
        self.edges = tuple(edges)


class _ColourBarHandler(HandlerBase):
    """Draws a :class:`_ColourBar`: a row of bands, pointed at the open ends, labels underneath."""

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        assert isinstance(orig_handle, _ColourBar)
        n = len(orig_handle.colours)
        step = width / n
        bar = 0.9 * fontsize
        top = -ydescent + height
        x0 = -xdescent
        artists: list[Artist] = []
        for i, colour in enumerate(orig_handle.colours):
            left, right, low, high = x0 + i * step, x0 + (i + 1) * step, top - bar, top
            if i == 0:
                points = [(left, (low + high) / 2), (right, low), (right, high)]
            elif i == n - 1:
                points = [(left, low), (right, (low + high) / 2), (left, high)]
            else:
                points = [(left, low), (right, low), (right, high), (left, high)]
            artists.append(mpatches.Polygon(points, closed=True, facecolor=colour, edgecolor="#333333",
                                            linewidth=0.5, transform=trans))
        for i, edge in enumerate(orig_handle.edges):
            artists.append(mtext.Text(x0 + (i + 1) * step, top - bar - 0.3 * fontsize, _number(edge),
                                      ha="center", va="top", fontsize=0.85 * fontsize, transform=trans))
        return artists


def legend_title(variable: ClimateVariable, period: WindPeriod) -> str:
    """``"Air temperature, July mean, 1981–2010 (°C)"`` and the like."""
    when = "annual" if period.key == "year" else period.label
    return f"{variable.title}, {when} {'total' if variable.total else 'mean'}, 1981–2010 ({variable.unit})"


def add_climate_legend(ax: GeoAxes, variable: ClimateVariable, period: WindPeriod,
                       y: float = -0.01, x: float = 0.5) -> Legend:
    """Add a key below the globe: the band colours as a bar, with the edges between them."""
    edges = band_edges(variable, period)
    fontsize = 10
    widest = max(len(_number(e)) for e in edges)
    legend = Legend(
        ax,
        [_ColourBar(variable.colours, edges)],
        [""],
        loc="upper center",
        bbox_to_anchor=(x, y),
        fontsize=fontsize,
        title=legend_title(variable, period),
        title_fontsize=10,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=len(variable.colours) * max(2.4, 0.62 * widest + 0.8),
        handleheight=2.3,
        handletextpad=0,
        handler_map={_ColourBar: _ColourBarHandler()},
        borderpad=0.7,
    )
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
