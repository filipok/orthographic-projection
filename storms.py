"""Storm tracks: the lows of the Northern Hemisphere, by how deep their centre fell.

Data source
-----------
Crawford, A. D., Schreiber, E. A. P., Sommer, N., Serreze, M. C., Stroeve, J. C.
& Barber, D. G. (2021). Sensitivity of Northern Hemisphere cyclone detection and
tracking results to fine spatial and temporal resolution using ERA5. *Monthly
Weather Review* 149, 2581-2598. https://doi.org/10.1175/MWR-D-20-0417.1 —
"Northern Hemisphere Extratropical Cyclone Tracks from ERA-5", Centre for Earth
Observation Science, University of Manitoba (CanWIN), CC BY 4.0: every low in
ERA5's sea-level pressure, 1940-2024, every three hours, with its central
pressure. It is built from ERA5 (Hersbach et al. 2020, Copernicus Climate
Change Service).

These are the storms of the mid-latitudes and the Arctic, the winter lows of
the Aleutians, Iceland and the Sea of Okhotsk, that ``--cyclones`` (tropical
storms only) leaves out; a typhoon or hurricane is a low in ERA5 too and is
tracked here as well, coarser than in IBTrACS. Each year (~5 MB) is downloaded
once and cached compactly under ``~/.cache/ortho_tiles/storms/``, with only the
storms that lasted a day and fell to 1,000 hPa or below. A storm counts in the
year of its deepest point; its whole track is drawn, each step coloured by its
central pressure, its deepest point marked and the deepest few named.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import zipfile
from collections.abc import Iterable
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

import frame
from biomes import _download

logger = logging.getLogger(__name__)

STORMS_URL = ("https://canwinerddap.ad.umanitoba.ca/erddap/files/"
              "Alex_Crawford_NH_cyclone_data_3e70_09c6_75db/{year}.zip")
FIRST_YEAR = 1940
LAST_YEAR = 2024
DATA_VERSION = 1
STORMS_ATTRIBUTION = ("Storm tracks: Crawford et al. (2021), CEOS University of Manitoba, CC BY 4.0, "
                      "from ERA5 (Copernicus C3S/ECMWF)")

DEFAULT_MAX_PRESSURE = 970.0        # hPa: the storms whose centre fell this low or lower are drawn
MIN_PRESSURE, MAX_PRESSURE = 920.0, 1000.0
CACHE_PRESSURE = 1000.0             # the storms kept in the cache: those that fell to this or lower …
MIN_STEPS = 9                       # … and lasted a day (nine three-hourly steps)
MAX_STORM_LABELS = 5
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Lowest central pressure of a class (hPa), its key label, colour and line width; from the weakest
LEVELS: tuple[tuple[float, str, str, float], ...] = (
    (990.0, "990 hPa or more", "#8e9aa6", 0.35),
    (980.0, "980-990 hPa", "#4f86b8", 0.5),
    (970.0, "970-980 hPa", "#1f9e89", 0.7),
    (960.0, "960-970 hPa", "#e8c400", 0.9),
    (950.0, "950-960 hPa", "#f08c00", 1.1),
    (940.0, "940-950 hPa", "#d6336c", 1.3),
    (-math.inf, "Below 940 hPa", "#7b1fa2", 1.6),
)


class StormDataError(RuntimeError):
    """The storm tracks could not be downloaded or read."""


class Storms(NamedTuple):
    """Storm tracks, one after another: storm *i* is steps ``starts[i]`` to ``starts[i + 1]``.

    *deepest* is the step at which each storm's centre was lowest; *date* is
    each step's day as ``YYYYMMDD``.
    """
    lon: np.ndarray
    lat: np.ndarray
    pressure: np.ndarray
    date: np.ndarray
    starts: np.ndarray
    deepest: np.ndarray


def level(pressure: float) -> int:
    """The index in :data:`LEVELS` of a central pressure (hPa)."""
    for i, (lowest, _, _, _) in enumerate(LEVELS):
        if pressure >= lowest:
            return i
    return len(LEVELS) - 1


def parse_storm_years(text: str | int | None) -> tuple[int, int]:
    """``"2020"`` or ``"2010-2020"`` as (first, last) years; the latest year when blank. Raises ``ValueError``."""
    if text is None or not str(text).strip():
        return LAST_YEAR, LAST_YEAR
    parts = [p.strip() for p in str(text).split("-")]
    try:
        first, last = (int(parts[0]), int(parts[-1])) if len(parts) <= 2 else (0, 0)
    except ValueError:
        first = last = 0
    if not FIRST_YEAR <= first <= last <= LAST_YEAR:
        raise ValueError(f"storms must be YEAR or FROM-TO between {FIRST_YEAR} and {LAST_YEAR}, not {text!r}")
    return first, last


def describe_years(years: tuple[int, int]) -> str:
    first, last = years
    return str(first) if first == last else f"{first}-{last}"


def read_track(rows: Iterable[dict[str, str]]) -> list[tuple[float, float, float, int]] | None:
    """The steps (lon, lat, central pressure, YYYYMMDD) of one track's CSV rows, or ``None`` if it is not kept.

    Rows without a position or pressure (where the algorithm lost the centre)
    are skipped; a track is kept if it has :data:`MIN_STEPS` steps and fell to
    :data:`CACHE_PRESSURE`.
    """
    steps = []
    for row in rows:
        try:
            lon, lat, pressure = float(row["lon"]), float(row["lat"]), float(row["p_cent"])
            day = int(row["year"]) * 10_000 + int(row["month"]) * 100 + int(row["day"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(lon) and math.isfinite(lat) and math.isfinite(pressure):
            steps.append((lon, lat, pressure, day))
    if len(steps) < MIN_STEPS or min(s[2] for s in steps) > CACHE_PRESSURE:
        return None
    return steps


def read_year(archive: zipfile.ZipFile) -> dict[str, np.ndarray]:
    """The kept tracks of one year's archive (one CSV per track) as step arrays with a storm number."""
    rows: list[tuple[float, float, float, int, int]] = []
    storm = 0
    for name in sorted(archive.namelist()):
        if not name.lower().endswith(".csv"):
            continue
        with archive.open(name) as raw:
            steps = read_track(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline="")))
        if steps is None:
            continue
        rows += [(lon, lat, pressure, day, storm) for lon, lat, pressure, day in steps]
        storm += 1
    table = np.array(rows, float).reshape(-1, 5)
    return {"lon": table[:, 0].astype(np.float32), "lat": table[:, 1].astype(np.float32),
            "pressure": table[:, 2].astype(np.float32), "date": table[:, 3].astype(np.int32),
            "storm": table[:, 4].astype(np.int32)}


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "storms")


def _year_path(year: int, cache_dir: str) -> str:
    return os.path.join(cache_dir, f"storms_{year}_v{DATA_VERSION}.npz")


def ensure_year(year: int, cache_dir: str) -> str:
    """The cached tracks of the archive for *year*, downloading it once."""
    path = _year_path(year, cache_dir)
    if os.path.isfile(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    download = os.path.join(cache_dir, f"storms_{year}.zip")
    logger.info("Downloading the ERA5 storm tracks of %d (one-time, ~5 MB) …", year)
    try:
        _download(STORMS_URL.format(year=year), download, label=f"storm tracks {year}")
        with zipfile.ZipFile(download) as archive:
            table = read_year(archive)
    except Exception as exc:              # network, archive and CSV errors alike
        raise StormDataError(f"Could not read the storm tracks of {year}: {exc}") from exc
    finally:
        if os.path.exists(download):
            os.remove(download)
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    return path


def load_storms(years: tuple[int, int], max_pressure: float = DEFAULT_MAX_PRESSURE,
                cache_dir: str | None = None) -> Storms:
    """The storms whose centre was deepest in *years* (first, last) and fell to *max_pressure* (hPa) or below.

    A year's archive holds the tracks that ended in it, so the next year's is
    read too, for the storms that were deepest in late December.
    """
    first, last = years
    if not FIRST_YEAR <= first <= last <= LAST_YEAR:
        raise ValueError(f"storm tracks run from {FIRST_YEAR} to {LAST_YEAR}, not {first}-{last}")
    cache_dir = cache_dir or _default_cache_dir()
    parts: dict[str, list[np.ndarray]] = {k: [] for k in ("lon", "lat", "pressure", "date", "storm")}
    numbered = 0
    for year in range(first, min(last + 1, LAST_YEAR) + 1):
        with np.load(ensure_year(year, cache_dir)) as data:
            for key in parts:
                parts[key].append(data[key] + numbered if key == "storm" else data[key])
            if len(data["storm"]):
                numbered += int(data["storm"].max()) + 1
    table = {key: np.concatenate(values) for key, values in parts.items()}
    return select_storms(table, years, max_pressure)


def select_storms(table: dict[str, np.ndarray], years: tuple[int, int], max_pressure: float) -> Storms:
    """The storms of the step *table* (numbered by ``storm``, each contiguous) deepest in *years* and that low."""
    storm = table["storm"]
    if not len(storm):
        empty = np.zeros(0)
        return Storms(empty, empty, empty, empty.astype(np.int32), np.zeros(1, int), np.zeros(0, int))
    starts = np.flatnonzero(np.r_[True, storm[1:] != storm[:-1]])
    lowest = np.minimum.reduceat(table["pressure"], starts)
    ends = np.r_[starts[1:], len(storm)]
    deepest = np.array([s + int(np.argmin(table["pressure"][s:e])) for s, e in zip(starts, ends)], int)
    year = table["date"][deepest] // 10_000
    keep = (lowest <= max_pressure) & (year >= years[0]) & (year <= years[1])
    steps = np.concatenate([np.arange(s, e) for s, e in zip(starts[keep], ends[keep])] or [np.zeros(0, int)])
    lengths = (ends - starts)[keep]
    new_starts = np.r_[0, np.cumsum(lengths)]
    return Storms(table["lon"][steps], table["lat"][steps], table["pressure"][steps], table["date"][steps],
                  new_starts, new_starts[:-1] + (deepest - starts)[keep])


def format_date(day: int) -> str:
    """``YYYYMMDD`` as ``26 Dec 2020``."""
    return f"{day % 100} {MONTHS[day // 100 % 100 - 1]} {day // 10_000}"


def _project(ax: GeoAxes, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    return ax.projection.transform_points(ccrs.PlateCarree(), lon.astype(float), lat.astype(float))[:, :2]


def draw_storms(ax: GeoAxes, storms: Storms, labels: bool = True) -> int:
    """Draw *storms* on *ax*, each step coloured by its central pressure, the deeper on top.

    Each storm's deepest point is marked, and the :data:`MAX_STORM_LABELS`
    deepest in sight are named with their pressure and day, leaving out a
    name that would overlap another or run off the map. Returns how many
    storms are in sight.
    """
    if not len(storms.lon):
        return 0
    xy = _project(ax, storms.lon, storms.lat)
    seen = np.isfinite(xy).all(axis=1)
    # A step joins two points of the same storm, both in sight; it takes the lower of their pressures
    same = np.ones(len(xy) - 1, bool)
    same[storms.starts[1:-1] - 1] = False
    first = np.flatnonzero(same & seen[:-1] & seen[1:])
    step_pressure = np.minimum(storms.pressure[first], storms.pressure[first + 1])
    step_level = np.array([level(p) for p in step_pressure], int)
    for i, (_, _, colour, width) in enumerate(LEVELS):
        mine = first[step_level == i]
        if len(mine):
            ax.add_collection(LineCollection(list(np.stack([xy[mine], xy[mine + 1]], axis=1)), colors=colour,
                                             linewidths=width, alpha=0.8, zorder=7.0 + i / 100,
                                             capstyle="round"))
    deepest = storms.deepest[seen[storms.deepest]]
    if not len(deepest):
        return 0
    colours = [LEVELS[level(p)][2] for p in storms.pressure[deepest]]
    ax.scatter(xy[deepest, 0], xy[deepest, 1], s=9, c=colours, edgecolors="white", linewidths=0.4,
               transform=ax.projection, zorder=7.1)
    if labels:
        _name_deepest(ax, storms, deepest, xy)
    return len(deepest)


def _name_deepest(ax: GeoAxes, storms: Storms, deepest: np.ndarray, xy: np.ndarray) -> None:
    dpi = ax.figure.dpi
    fontsize = 8
    placed: list[tuple[float, float, float, float]] = []
    for i in deepest[np.argsort(storms.pressure[deepest], kind="stable")]:
        if len(placed) >= MAX_STORM_LABELS:
            break
        name = f"{storms.pressure[i]:.0f} hPa, {format_date(int(storms.date[i]))}"
        px, py = ax.transData.transform(xy[i])
        offset = 4 * dpi / 72
        half_h = 0.6 * fontsize * dpi / 72
        box = (px + offset, py - half_h, px + offset + 0.55 * fontsize * len(name) * dpi / 72, py + half_h)
        if not frame.box_inside(ax, box, 0.97):
            continue
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
            continue
        placed.append(box)
        ax.annotate(name, tuple(xy[i]), xycoords="data", xytext=(4, 0), textcoords="offset points",
                    fontsize=fontsize, color="#2b2b2b", ha="left", va="center", zorder=8)


def add_storms_legend(ax: GeoAxes, years: tuple[int, int], max_pressure: float, y: float = -0.01,
                      x: float = 0.5) -> Legend:
    """A key to the track colours and the deepest points."""
    handles: list[Line2D] = [Line2D([], [], color=colour, linewidth=max(width * 1.8, 1.5))
                             for _, _, colour, width in LEVELS]
    labels = [label for _, label, _, _ in LEVELS]
    handles.append(Line2D([], [], linestyle="none", marker="o", markersize=4, markerfacecolor=LEVELS[3][2],
                          markeredgecolor="white", markeredgewidth=0.4))
    labels.append("Deepest point")
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=4, fontsize=10,
                    title=f"Storm tracks of {describe_years(years)} that fell to {max_pressure:g} hPa or below "
                          "(ERA5), by central pressure",
                    title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85, edgecolor="#444444",
                    handlelength=2.0, columnspacing=1.4, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
