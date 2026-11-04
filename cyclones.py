"""Tropical cyclone tracks: hurricanes, typhoons and cyclones, by strength.

Data source
-----------
Knapp, K. R., Kruk, M. C., Levinson, D. H., Diamond, H. J. & Neumann, C. J.
(2010). The International Best Track Archive for Climate Stewardship (IBTrACS).
*Bulletin of the American Meteorological Society* 91, 363-376.
https://doi.org/10.1175/2009BAMS2755.1 — IBTrACS v04r01, NOAA NCEI, public
domain: every tropical cyclone's track, every three hours, with its wind.

The tracks since 1980 (~22 MB) are downloaded once and cached compactly
under ``~/.cache/ortho_tiles/cyclones/``. Only the tropical stage of each
storm is drawn, from tropical storm strength (63 km/h) up, each step of the
track coloured by its Saffir-Simpson category. On request, the extratropical
stage of the storms that reached that strength is drawn too, in one colour: the
agencies often stop recording the wind once a storm has turned extratropical,
and the Saffir-Simpson scale is for tropical storms only.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from typing import Any, NamedTuple

import numpy as np

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from biomes import _download

logger = logging.getLogger(__name__)

IBTRACS_URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
               "v04r01/access/shapefile/IBTrACS.since1980.list.v04r01.lines.zip")
FIRST_YEAR = 1980
DATA_VERSION = 2
CYCLONES_ATTRIBUTION = "Cyclone tracks: IBTrACS v04r01 (Knapp et al. 2010), NOAA NCEI"

# Category -> (lowest sustained wind, knots; key label; colour; line width)
CATEGORIES: dict[int, tuple[int, str, str, float]] = {
    0: (34, "Tropical storm (63-118 km/h)", "#4fa3d9", 0.5),
    1: (64, "Category 1 (119-153 km/h)", "#e8c400", 0.7),
    2: (83, "Category 2 (154-177 km/h)", "#f59a00", 0.9),
    3: (96, "Category 3 (178-208 km/h)", "#e8590c", 1.1),
    4: (113, "Category 4 (209-251 km/h)", "#c41e3a", 1.3),
    5: (137, "Category 5 (252 km/h or more)", "#6e0a3c", 1.6),
}


# The category of a step in the extratropical stage, and how it is drawn
EXTRATROPICAL = -2
ET_LABEL, ET_COLOUR, ET_WIDTH = "Extratropical stage", "#6c5b9e", 0.6


class CycloneDataError(RuntimeError):
    """The cyclone tracks could not be downloaded or read."""


class Tracks(NamedTuple):
    """Track steps: from (lon0, lat0) to (lon1, lat1), the storm's season and Saffir-Simpson category.

    The category is :data:`EXTRATROPICAL` for a step in the extratropical stage.
    """
    lon0: np.ndarray
    lat0: np.ndarray
    lon1: np.ndarray
    lat1: np.ndarray
    season: np.ndarray
    category: np.ndarray


def category(wind_knots: float | None) -> int:
    """The Saffir-Simpson category of a sustained wind (knots): -1 below tropical storm strength."""
    if wind_knots is None or not np.isfinite(wind_knots):
        return -1
    found = -1
    for cat, (lowest, _, _, _) in CATEGORIES.items():
        if wind_knots >= lowest:
            found = cat
    return found


def read_tracks(records: list[dict[str, Any]], lines: list[list[tuple[float, float]]]) -> dict[str, np.ndarray]:
    """The table of IBTrACS line records: the tropical steps of tropical storm strength and above, and the
    extratropical steps (category :data:`EXTRATROPICAL`) of the storms that had such tropical steps."""
    rows = []
    extratropical = []
    strong = set()
    for record, points in zip(records, lines):
        if len(points) < 2:
            continue
        (lon0, lat0), (lon1, lat1) = points[0], points[-1]
        if abs((lon1 - lon0 + 180) % 360 - 180) > 30:      # not a three-hour step: a broken record
            continue
        step = (lon0, lat0, lon1, lat1, int(record["SEASON"]))
        if record.get("NATURE") == "ET":
            extratropical.append((record.get("SID"), step))
            continue
        if record.get("NATURE") != "TS":
            continue
        winds = [w for w in (record.get("USA_WIND"), record.get("WMO_WIND")) if w is not None]
        cat = category(max(winds) if winds else None)
        if cat < 0:
            continue
        rows.append((*step, cat))
        strong.add(record.get("SID"))
    rows += [(*step, EXTRATROPICAL) for sid, step in extratropical if sid in strong]
    table = np.array(rows, float).reshape(-1, 6)
    return {"lon0": table[:, 0].astype(np.float32), "lat0": table[:, 1].astype(np.float32),
            "lon1": table[:, 2].astype(np.float32), "lat1": table[:, 3].astype(np.float32),
            "season": table[:, 4].astype(np.int16), "category": table[:, 5].astype(np.int8)}


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "cyclones")


def load_tracks(since: int = FIRST_YEAR, cache_dir: str | None = None, extratropical: bool = False) -> Tracks:
    """The tracks of the seasons from *since*, downloading the archive once; with *extratropical*, their
    extratropical stage too."""
    if since < FIRST_YEAR:
        raise ValueError(f"cyclone tracks start in {FIRST_YEAR}, not {since}")
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"ibtracs_since{FIRST_YEAR}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        import shapefile  # pyshp, which Cartopy depends on

        os.makedirs(cache_dir, exist_ok=True)
        download = os.path.join(cache_dir, "ibtracs_lines.zip")
        logger.info("Downloading the IBTrACS cyclone tracks since %d (one-time, ~22 MB) …", FIRST_YEAR)
        try:
            _download(IBTRACS_URL, download, label="IBTrACS")
            with zipfile.ZipFile(download) as archive:
                members = {n.rsplit(".", 1)[-1].lower(): n for n in archive.namelist()}
                reader = shapefile.Reader(shp=io.BytesIO(archive.read(members["shp"])),
                                          shx=io.BytesIO(archive.read(members["shx"])),
                                          dbf=io.BytesIO(archive.read(members["dbf"])))
                with reader:
                    fields = ("SID", "SEASON", "NATURE", "USA_WIND", "WMO_WIND")
                    records: list[dict[str, Any]] = [dict(r.as_dict()) for r in reader.iterRecords(fields=list(fields))
                                                     if r is not None]
                    lines = [[(p[0], p[1]) for p in s.points] if s is not None else [] for s in reader.iterShapes()]
            table = read_tracks(records, lines)
        except Exception as exc:              # network, archive and shapefile errors alike
            raise CycloneDataError(f"Could not read the cyclone tracks: {exc}") from exc
        finally:
            if os.path.exists(download):
                os.remove(download)
        part = path + ".part.npz"
        np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
        os.replace(part, path)
    with np.load(path) as data:
        keep = (data["season"] >= since) & (extratropical | (data["category"] != EXTRATROPICAL))
        return Tracks(*(data[k][keep] for k in Tracks._fields))


def draw_tracks(ax: GeoAxes, tracks: Tracks) -> None:
    """Draw the track steps on *ax*, the stronger on top and the extratropical stage below them all."""
    styles = [(EXTRATROPICAL, ET_COLOUR, ET_WIDTH, 7.09)]
    styles += [(cat, colour, width, 7.1 + cat / 100) for cat, (_, _, colour, width) in CATEGORIES.items()]
    for cat, colour, width, zorder in styles:
        mine = tracks.category == cat
        if not mine.any():
            continue
        start = ax.projection.transform_points(ccrs.PlateCarree(), tracks.lon0[mine].astype(float),
                                               tracks.lat0[mine].astype(float))[:, :2]
        end = ax.projection.transform_points(ccrs.PlateCarree(), tracks.lon1[mine].astype(float),
                                             tracks.lat1[mine].astype(float))[:, :2]
        ok = np.isfinite(start).all(axis=1) & np.isfinite(end).all(axis=1)
        segments = np.stack([start[ok], end[ok]], axis=1)
        ax.add_collection(LineCollection(list(segments), colors=colour, linewidths=width, alpha=0.75,
                                         zorder=zorder, capstyle="round"))


def add_cyclones_legend(ax: GeoAxes, since: int, y: float = -0.01, x: float = 0.5,
                        extratropical: bool = False) -> Legend:
    """A key to the track colours, and to the extratropical stage's with *extratropical*."""
    handles = [Line2D([], [], color=colour, linewidth=max(width * 1.8, 1.5)) for _, _, colour, width in CATEGORIES.values()]
    labels = [label for _, label, _, _ in CATEGORIES.values()]
    if extratropical:
        handles.append(Line2D([], [], color=ET_COLOUR, linewidth=max(ET_WIDTH * 1.8, 1.5)))
        labels.append(ET_LABEL)
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=4 if extratropical else 3,
                    fontsize=10,
                    title=f"Tropical cyclone tracks since {since} (IBTrACS), by Saffir-Simpson category",
                    title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85, edgecolor="#444444",
                    handlelength=2.0, columnspacing=1.4, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
