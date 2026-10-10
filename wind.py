"""Prevailing winds: the long-term mean surface wind, by season or month.

Data source
-----------
Kalnay, E. et al. (1996). The NCEP/NCAR 40-Year Reanalysis Project.
*Bull. Amer. Meteor. Soc.* 77, 437-471. NCEP-NCAR Reanalysis 1 data provided
by the NOAA PSL, Boulder, Colorado, USA, from their website at
https://psl.noaa.gov — public domain.

NOAA PSL publishes the 1991-2020 long-term mean of each month's 10 m wind,
as its eastward (u) and northward (v) components, and of each month's mean
wind speed, on the reanalysis's T62 Gaussian grid (192 × 94 points, about
1.9°). The three files (~1 MB each, netCDF-4) are downloaded once and cached.

What is drawn
-------------
The prevailing wind over the chosen months is their mean wind vector. An
arrow points the way that wind blows (downwind), its length is the mean
vector's speed and its colour is the wind's *steadiness*: the mean vector's
speed over the mean wind speed. Trade winds, which blow from one quarter
almost all the time, come close to 1; where the wind comes from every
direction in turn the mean vector nearly cancels out and steadiness is near
0. A monsoon shows as a steady wind in its season and a weak, unsteady one
over the whole year.
"""

from __future__ import annotations

import calendar
import io
import logging
import os
import urllib.error
import urllib.request
from typing import Any, NamedTuple

import numpy as np
from scipy.interpolate import RegularGridInterpolator

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.artist import Artist
from matplotlib.legend import Legend
from matplotlib.legend_handler import HandlerBase

logger = logging.getLogger(__name__)

WIND_URL = ("https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.derived/surface_gauss/"
            "{var}.10m.mon.ltm.1991-2020.nc")
WIND_VARIABLES = ("uwnd", "vwnd", "wspd")
DATA_VERSION = 1
WIND_ATTRIBUTION = "Winds: NCEP/NCAR Reanalysis 1, 1991–2020 means, NOAA PSL"

# Mean days per month, to weight months in a season or the year
DAYS_IN_MONTH = (31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

SEASONS = {
    "djf": ("December–February", (12, 1, 2)),
    "mam": ("March–May", (3, 4, 5)),
    "jja": ("June–August", (6, 7, 8)),
    "son": ("September–November", (9, 10, 11)),
}

# Steadiness bands: upper edges, labels and arrow colours, light to dark so
# the steadiest winds stand out on both land and sea. London's westerlies are
# about 0.35-0.45, Hawaii's trade winds about 0.9.
STEADINESS_BANDS = (
    (0.3, "variable", "#9b9b9b"),
    (0.6, "changeable", "#e08a2c"),
    (0.8, "steady", "#c2311a"),
    (1.01, "very steady", "#5a0b3c"),
)

# Arrow spacing: arrows across the map's width, whatever its scale
ARROWS_ACROSS = 42
# A wind of this speed (m/s) gets an arrow as long as the gap between arrows
REFERENCE_SPEED = 8.0
KEY_SPEEDS = (2, 5, 10)


class WindPeriod(NamedTuple):
    key: str            # "year", "djf" … or the month's name in lower case
    label: str          # "the year", "December–February", "July"
    months: tuple[int, ...]


class WindField(NamedTuple):
    lat: np.ndarray     # ascending, degrees
    lon: np.ndarray     # 0 … 360, the first column repeated at the end
    u: np.ndarray       # eastward mean wind (m/s), rows by *lat*
    v: np.ndarray       # northward mean wind (m/s)
    speed: np.ndarray   # mean wind speed (m/s)


class WindDataError(RuntimeError):
    """The reanalysis wind means could not be downloaded or read."""


def resolve_wind_period(spec: str | None, kind: str = "wind") -> WindPeriod:
    """The months to average for *spec*.

    ``None``, ``""``, ``"year"`` or ``"annual"`` mean the whole year;
    ``"djf"``, ``"mam"``, ``"jja"`` and ``"son"`` the meteorological seasons;
    a month by name (at least its first three letters) or number one month.
    Raises ``ValueError`` for anything else, naming the layer as *kind*.
    """
    text = (spec or "").strip().lower()
    if text in ("", "year", "annual", "all"):
        return WindPeriod("year", "the year", tuple(range(1, 13)))
    if text in SEASONS:
        label, months = SEASONS[text]
        return WindPeriod(text, label, months)
    names = [calendar.month_name[m].lower() for m in range(1, 13)]
    if text.isdigit() and 1 <= int(text) <= 12:
        month = int(text)
    else:
        matches = [i + 1 for i, name in enumerate(names) if len(text) >= 3 and name.startswith(text)]
        if len(matches) != 1:
            raise ValueError(
                f"Unknown {kind} period {spec!r}. Use year, a season (djf, mam, jja, son), "
                "or a month by name or number.")
        month = matches[0]
    return WindPeriod(names[month - 1], calendar.month_name[month], (month,))


# ---------------------------------------------------------------------------
# Download and cache
# ---------------------------------------------------------------------------


def _get(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def read_variable(data: bytes, var: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(values, lat, lon)`` of *var* in a PSL netCDF-4 file; missing values become NaN.

    *values* has shape (12 months, lat, lon).
    """
    import h5py  # only needed for winds; keeps `import ortho` light

    with h5py.File(io.BytesIO(data), "r") as f:
        ds = f[var]
        values = ds[...].astype(np.float32)
        missing = ds.attrs.get("missing_value")
        if missing is not None:
            values[np.isclose(values, np.float32(np.asarray(missing).ravel()[0]))] = np.nan
        values = values * float(np.asarray(ds.attrs.get("scale_factor", 1.0)).ravel()[0]) \
            + float(np.asarray(ds.attrs.get("add_offset", 0.0)).ravel()[0])
        lat = f["lat"][...].astype(np.float64)
        lon = f["lon"][...].astype(np.float64)
    if values.shape != (12, lat.size, lon.size):
        raise WindDataError(f"unexpected shape {values.shape} for {var}")
    return values, lat, lon


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "wind")


def ensure_wind_data(cache_dir: str | None = None) -> str:
    """Path to the cached monthly means, downloading them (~3.5 MB) if needed.

    Raises :class:`WindDataError` if they cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"ncep_wind_10m_ltm_1991-2020_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached wind means: %s", path)
        return path
    logger.info("Downloading NCEP/NCAR 1991-2020 wind means (one-time, ~3.5 MB) …")
    arrays: dict[str, np.ndarray] = {}
    try:
        for var in WIND_VARIABLES:
            values, lat, lon = read_variable(_get(WIND_URL.format(var=var)), var)
            if "lat" in arrays and not (np.array_equal(lat, arrays["lat"]) and np.array_equal(lon, arrays["lon"])):
                raise WindDataError("the wind files are on different grids")
            arrays.update({var: values, "lat": lat, "lon": lon})
    except WindDataError:
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, KeyError, ValueError) as exc:
        raise WindDataError(f"Could not read the NCEP/NCAR wind means: {exc}") from exc
    part = path + ".part.npz"
    np.savez_compressed(part, uwnd=arrays["uwnd"], vwnd=arrays["vwnd"], wspd=arrays["wspd"],
                        lat=arrays["lat"], lon=arrays["lon"])
    os.replace(part, path)
    return path


def load_wind(period: WindPeriod, cache_dir: str | None = None) -> WindField:
    """The mean wind over *period*'s months, each weighted by its length."""
    with np.load(ensure_wind_data(cache_dir)) as data:
        u, v, speed = data["uwnd"], data["vwnd"], data["wspd"]
        lat, lon = data["lat"], data["lon"]
    index = [m - 1 for m in period.months]
    weights = np.array([DAYS_IN_MONTH[i] for i in index])[:, None, None]
    weights = weights / weights.sum()
    mean = [np.sum(a[index] * weights, axis=0) for a in (u, v, speed)]
    order = np.argsort(lat)                          # the files run north to south
    lat = lat[order]
    mean = [a[order] for a in mean]
    # Repeat the first column at 360° so interpolation wraps round the date line
    lon = np.append(lon, lon[0] + 360.0)
    mean = [np.concatenate([a, a[:, :1]], axis=1) for a in mean]
    return WindField(lat, lon, *mean)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def arrow_points(projection: ccrs.Projection, extent: tuple[float, float, float, float],
                 across: int = ARROWS_ACROSS) -> tuple[np.ndarray, np.ndarray, float]:
    """Longitudes and latitudes of an even grid of arrows over the visible disc.

    *extent* is the map's ``(x0, x1, y0, y1)`` in *projection* coordinates,
    centred on the globe's centre. Returns ``(lon, lat, spacing)``, spacing in
    projection units. Points outside the disc the map shows are left out.
    """
    x0, x1, y0, y1 = extent
    spacing = (x1 - x0) / across
    xs = np.arange(x0 + spacing / 2, x1, spacing)
    ys = np.arange(y0 + spacing / 2, y1, spacing)
    xx, yy = np.meshgrid(xs, ys)
    radius = min(x1 - x0, y1 - y0) / 2 - spacing / 2
    inside = np.hypot(xx - (x0 + x1) / 2, yy - (y0 + y1) / 2) <= radius
    xx, yy = xx[inside], yy[inside]
    lonlat = ccrs.PlateCarree().transform_points(projection, xx, yy)
    ok = np.isfinite(lonlat[:, :2]).all(axis=1)
    return lonlat[ok, 0], lonlat[ok, 1], spacing


def sample_wind(field: WindField, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, ...]:
    """``(u, v, steadiness)`` at the given points, interpolated linearly.

    Points poleward of the grid's last row take that row's values.
    """
    points = np.column_stack([
        np.clip(lat, field.lat[0], field.lat[-1]),
        np.mod(lon, 360.0),
    ])
    u, v, speed = (RegularGridInterpolator((field.lat, field.lon), a)(points)
                   for a in (field.u, field.v, field.speed))
    with np.errstate(invalid="ignore", divide="ignore"):
        steadiness = np.clip(np.hypot(u, v) / speed, 0.0, 1.0)
    steadiness = np.where(np.isfinite(steadiness), steadiness, 0.0)
    return u, v, steadiness


def steadiness_colours() -> tuple[ListedColormap, BoundaryNorm]:
    """The arrows' colormap and band boundaries."""
    edges = [0.0] + [upper for upper, _, _ in STEADINESS_BANDS]
    cmap = ListedColormap([colour for _, _, colour in STEADINESS_BANDS])
    return cmap, BoundaryNorm(edges, cmap.N)


def add_wind_overlay(ax: GeoAxes, period: WindPeriod, cache_dir: str | None = None,
                     across: int = ARROWS_ACROSS, zorder: float = 7.5) -> Any:
    """Draw the prevailing-wind arrows for *period* on *ax*; returns the Quiver."""
    field = load_wind(period, cache_dir)
    x0, x1, y0, y1 = ax.get_extent(ax.projection)
    lon, lat, _ = arrow_points(ax.projection, (x0, x1, y0, y1), across)
    u, v, steadiness = sample_wind(field, lon, lat)
    cmap, norm = steadiness_colours()
    quiver = ax.quiver(
        lon, lat, u, v, steadiness,
        transform=ccrs.PlateCarree(),
        cmap=cmap, norm=norm,
        pivot="middle",
        units="width", width=0.0016, headwidth=3.6, headlength=4.0, headaxislength=3.6,
        scale_units="width", scale=REFERENCE_SPEED * across, minlength=0.5,
        edgecolor="white", linewidth=0.35,
        zorder=zorder,
    )
    logger.info("Prevailing winds for %s drawn (%d arrows).", period.label, len(lon))
    return quiver


class _ArrowHandle(Artist):
    """Stands for a key entry: an arrow for a wind of *speed* m/s."""

    def __init__(self, speed: float) -> None:
        super().__init__()
        self.speed = speed


class _ArrowHandler(HandlerBase):
    """Draws an :class:`_ArrowHandle` at the map's arrow scale."""

    def __init__(self, points_per_ms: float) -> None:
        super().__init__()
        self.points_per_ms = points_per_ms

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        assert isinstance(orig_handle, _ArrowHandle)
        length = orig_handle.speed * self.points_per_ms
        y = height / 2 - ydescent
        x = -xdescent + (width - length) / 2
        arrow = mpatches.FancyArrow(
            x, y, length, 0, width=1.6, head_width=5.0, head_length=min(5.0, length * 0.45),
            length_includes_head=True, facecolor="#333333", edgecolor="white", linewidth=0.4,
            transform=trans)
        return [arrow]


class _SwatchHandler(HandlerBase):
    """Draws a colour patch as a small square swatch, however wide the key's handles are."""

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        assert isinstance(orig_handle, mpatches.Patch)
        side = 1.4 * fontsize
        swatch = mpatches.Rectangle(
            (-xdescent + (width - side) / 2, -ydescent + (height - 0.7 * fontsize) / 2), side, 0.7 * fontsize,
            facecolor=orig_handle.get_facecolor(), edgecolor=orig_handle.get_edgecolor(),
            linewidth=orig_handle.get_linewidth(), transform=trans)
        return [swatch]


def add_wind_legend(ax: GeoAxes, period: WindPeriod, y: float = -0.01, x: float = 0.5,
                    across: int = ARROWS_ACROSS) -> Legend:
    """Add a key below the globe: arrow lengths for a few speeds, and the steadiness colours."""
    fig = ax.get_figure()
    axes_width_pt = ax.get_position().width * fig.get_figwidth() * 72  # type: ignore[union-attr]
    points_per_ms = axes_width_pt / (REFERENCE_SPEED * across)
    arrows = [_ArrowHandle(s) for s in KEY_SPEEDS]
    patches = [mpatches.Patch(facecolor=colour, edgecolor="#333333", linewidth=0.6)
               for _, _, colour in STEADINESS_BANDS]
    labels = [f"{s} m/s" for s in KEY_SPEEDS] + [label for _, label, _ in STEADINESS_BANDS]
    fontsize = 10
    legend = Legend(
        ax,
        arrows + patches,
        labels,
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=len(labels),
        fontsize=fontsize,
        title=f"Prevailing wind, {period.label}, 1991–2020 mean\n"
              "Arrows show the mean wind; colours its steadiness (mean wind ÷ average speed)",
        title_fontsize=10,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=max(1.6, (max(KEY_SPEEDS) * points_per_ms + 4) / fontsize),
        handler_map={_ArrowHandle: _ArrowHandler(points_per_ms), mpatches.Patch: _SwatchHandler()},
        columnspacing=1.2,
        borderpad=0.7,
    )
    legend.get_title().set_multialignment("center")
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
