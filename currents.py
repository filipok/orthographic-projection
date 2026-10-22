"""Ocean surface currents: arrows of the mean flow, coloured warm or cold.

Data source
-----------
Laurindo, L. C., Mariano, A. J. & Lumpkin, R. (2017). An improved
near-surface velocity climatology for the global ocean from drifter
observations. *Deep-Sea Research I* 124, 73-92.
https://doi.org/10.1016/j.dsr.2017.04.009 — the NOAA Global Drifter Program's
annual mean velocity and sea surface temperature at 0.25°, from the satellite-
tracked drifting buoys of 1979 on (NOAA AOML, public domain).

The 51 MB file is downloaded once and averaged to 1° cells, which keeps the
great current systems and smooths away eddies; the result, under 1 MB, is
cached under ``~/.cache/ortho_tiles/currents/``.

Warm and cold: a current is warm when its water is warmer than the average
of its latitude around the world (the Gulf Stream, the Kuroshio), cold when
colder (the Humboldt, the Benguela, the California and Canary currents).
"""

from __future__ import annotations

import io
import logging
import os
import urllib.request
import warnings
from typing import Any, NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.legend import Legend

import frame
from wind import _ArrowHandle, _ArrowHandler, _SwatchHandler, arrow_points

logger = logging.getLogger(__name__)

DRIFTER_URL = "https://www.aoml.noaa.gov/phod/dac/drifter_annualmeans.nc"
DATA_VERSION = 1
BLOCK = 4                 # 0.25° cells averaged per side: 1° cells
CURRENTS_ATTRIBUTION = ("Ocean currents: NOAA Global Drifter Program climatology "
                        "(Laurindo et al. 2017), NOAA AOML")

ARROWS_ACROSS = 42
# Arrow length grows with the square root of speed, so slow currents still show beside fast ones
REFERENCE_SPEED = 0.1     # m/s whose arrow is one arrow spacing long
MIN_SPEED = 0.02          # slower than this (m/s) is left out
KEY_SPEEDS = (0.1, 0.5, 1.0)

# Temperature against the latitude's average (°C): (upper bound, key label, colour)
WARMTH_BANDS: tuple[tuple[float, str, str], ...] = (
    (-3.0, "Cold: 3 °C or more below its latitude's average", "#1f5fb4"),
    (-1.0, "Cool: 1-3 °C below", "#7fb0de"),
    (1.0, "Near the average", "#8a8a8a"),
    (3.0, "Mild: 1-3 °C above", "#f08a5d"),
    (np.inf, "Warm: 3 °C or more above", "#c8281e"),
)


class CurrentsDataError(RuntimeError):
    """The drifter climatology could not be downloaded or read."""


class CurrentField(NamedTuple):
    """Mean surface velocity (m/s) and temperature anomaly (°C) on a 1° grid, rows south to north."""
    lon: np.ndarray       # cell centres, -179.5 … 179.5
    lat: np.ndarray       # cell centres, south to north
    u: np.ndarray         # (lat, lon), NaN on land and where no drifters went
    v: np.ndarray
    warmth: np.ndarray    # temperature minus the latitude's average


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "currents")


def block_mean(grid: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Average of each *block* × *block* cells, ignoring NaN; NaN where all are NaN."""
    rows, cols = grid.shape[0] // block * block, grid.shape[1] // block * block
    blocks = grid[:rows, :cols].reshape(rows // block, block, cols // block, block)
    with np.errstate(invalid="ignore"):
        count = np.isfinite(blocks).sum(axis=(1, 3))
        total = np.nansum(blocks, axis=(1, 3))
        return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def reduce_drifters(lon: np.ndarray, lat: np.ndarray, u: np.ndarray, v: np.ndarray, sst: np.ndarray,
                    block: int = BLOCK) -> dict[str, np.ndarray]:
    """The 1° field from the 0.25° climatology (arrays (lat, lon), rows south to north)."""
    mean_u, mean_v, mean_sst = (block_mean(a, block) for a in (u, v, sst))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)          # rows that are all land
        warmth = mean_sst - np.nanmean(mean_sst, axis=1, keepdims=True)
    return {
        "lon": lon[: len(lon) // block * block].reshape(-1, block).mean(axis=1),
        "lat": lat[: len(lat) // block * block].reshape(-1, block).mean(axis=1),
        "u": mean_u, "v": mean_v, "warmth": warmth,
    }


def ensure_currents_data(cache_dir: str | None = None) -> str:
    """Path to the cached 1° field, downloading and reducing the climatology if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"drifter_annual_1deg_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Downloading the NOAA drifter climatology (one-time, ~51 MB) …")
    try:
        import h5py
        request = urllib.request.Request(DRIFTER_URL, headers={"User-Agent": "ortho/1.0"})
        with urllib.request.urlopen(request, timeout=300) as resp:
            data = resp.read()
        with h5py.File(io.BytesIO(data)) as f:
            lon, lat, u, v, sst = (np.asarray(f[name]) for name in ("Lon", "Lat", "U", "V", "SST"))
        # Stored (lon, lat): transposed to rows of latitude
        table = reduce_drifters(lon, lat, u.T, v.T, sst.T)
    except Exception as exc:                  # network and file errors alike
        raise CurrentsDataError(f"Could not read the drifter climatology: {exc}") from exc
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    return path


def load_currents(cache_dir: str | None = None) -> CurrentField:
    with np.load(ensure_currents_data(cache_dir)) as data:
        return CurrentField(data["lon"], data["lat"], data["u"], data["v"], data["warmth"])


def sample(field: CurrentField, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(u, v, warmth)`` of the 1° cell holding each point; NaN off the grid."""
    step_lon = field.lon[1] - field.lon[0]
    step_lat = field.lat[1] - field.lat[0]
    col = np.floor((np.mod(lon + 180, 360) - 180 - (field.lon[0] - step_lon / 2)) / step_lon).astype(int)
    row = np.floor((lat - (field.lat[0] - step_lat / 2)) / step_lat).astype(int)
    ok = (row >= 0) & (row < len(field.lat)) & (col >= 0) & (col < len(field.lon))
    out = [np.full(len(lon), np.nan) for _ in range(3)]
    for a, grid in zip(out, (field.u, field.v, field.warmth)):
        a[ok] = grid[row[ok], col[ok]]
    return out[0], out[1], out[2]


def warmth_colours() -> tuple[ListedColormap, BoundaryNorm]:
    cmap = ListedColormap([colour for _, _, colour in WARMTH_BANDS])
    bounds = [-1e9] + [b for b, _, _ in WARMTH_BANDS[:-1]] + [1e9]
    return cmap, BoundaryNorm(bounds, cmap.N)


def scaled(u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """*u*, *v* with their length replaced by the square root of the speed."""
    speed = np.hypot(u, v)
    with np.errstate(invalid="ignore", divide="ignore"):
        factor = np.where(speed > 0, np.sqrt(speed) / speed, 0.0)
    return u * factor, v * factor


def add_currents_overlay(ax: GeoAxes, field: CurrentField, across: int = ARROWS_ACROSS,
                         zorder: float = 7.4) -> Any:
    """Draw the current arrows on *ax*; returns the Quiver (``None`` when no sea is in sight)."""
    x0, x1, y0, y1 = ax.get_extent(ax.projection)
    lon, lat, _ = arrow_points(ax.projection, (x0, x1, y0, y1), across, frame.is_rectangle(ax))
    u, v, warmth = sample(field, lon, lat)
    keep = np.isfinite(u) & np.isfinite(v) & (np.hypot(u, v) >= MIN_SPEED)
    if not keep.any():
        return None
    su, sv = scaled(u[keep], v[keep])
    cmap, norm = warmth_colours()
    quiver = ax.quiver(
        lon[keep], lat[keep], su, sv, np.nan_to_num(warmth[keep]),
        transform=ccrs.PlateCarree(), cmap=cmap, norm=norm, pivot="middle",
        units="width", width=0.0022, headwidth=3.6, headlength=4.0, headaxislength=3.6,
        scale_units="width", scale=np.sqrt(REFERENCE_SPEED) * across, minlength=0.4,
        edgecolor="white", linewidth=0.35, zorder=zorder,
    )
    logger.info("Ocean currents drawn (%d arrows).", int(keep.sum()))
    return quiver


class _CurrentArrowHandler(_ArrowHandler):
    """Draws a key arrow at the map's square-root scale."""

    def create_artists(self, legend, orig_handle, xdescent, ydescent, width, height, fontsize, trans):
        root = _ArrowHandle(float(np.sqrt(orig_handle.speed)))
        return super().create_artists(legend, root, xdescent, ydescent, width, height, fontsize, trans)


def add_currents_legend(ax: GeoAxes, y: float = -0.01, x: float = 0.5, across: int = ARROWS_ACROSS) -> Legend:
    """A key below the globe: arrows for a few speeds, and the warm and cold colours."""
    fig = ax.get_figure()
    axes_width_pt = ax.get_position().width * fig.get_figwidth() * 72  # type: ignore[union-attr]
    points_per_root = axes_width_pt / (np.sqrt(REFERENCE_SPEED) * across)
    arrows = [_ArrowHandle(s) for s in KEY_SPEEDS]
    patches = [mpatches.Patch(facecolor=colour, edgecolor="#333333", linewidth=0.6) for _, _, colour in WARMTH_BANDS]
    labels = [f"{s:g} m/s" for s in KEY_SPEEDS] + [label for _, label, _ in WARMTH_BANDS]
    fontsize = 10
    legend = Legend(
        ax, arrows + patches, labels,
        loc="upper center", bbox_to_anchor=(x, y), ncol=4, fontsize=fontsize,
        title="Ocean surface currents, mean flow from drifting buoys (1979 on)\n"
              "Colour: the water's temperature against its latitude's average, warm and cold currents",
        title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85, edgecolor="#444444",
        handlelength=max(1.6, (np.sqrt(max(KEY_SPEEDS)) * points_per_root + 4) / fontsize),
        handler_map={_ArrowHandle: _CurrentArrowHandler(points_per_root), mpatches.Patch: _SwatchHandler()},
        columnspacing=1.2, borderpad=0.7,
    )
    legend.get_title().set_multialignment("center")
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
