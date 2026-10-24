"""Plate tectonics: plate boundaries, large earthquakes and volcanoes.

Data sources
------------
- **Plate boundaries:** Bird, P. (2003). An updated digital model of plate
  boundaries. *Geochemistry, Geophysics, Geosystems* 4(3), 1027.
  https://doi.org/10.1029/2001GC000252 — the PB2002 model, as cleaned and
  converted by Hugo Ahlenius (Nordpil),
  https://github.com/fraxen/tectonicplates, ODC Attribution License. Its
  5,800 boundary steps are each classed: spreading ridges and rifts,
  subduction zones and collisions, transform faults.
- **Earthquakes:** the U.S. Geological Survey's ANSS Comprehensive Catalog
  (ComCat), from 1900, through its FDSN event web service; public domain.
- **Volcanoes:** Global Volcanism Program (2025). Volcanoes of the World
  (v. 5). Smithsonian Institution. https://doi.org/10.5479/si.GVP.VOTW5-2025.5.3
  — the ~1,200 volcanoes active in the Holocene (the last 11,700 years), with
  their latest eruption; free to use with this citation.

Everything is downloaded once and cached under
``~/.cache/ortho_tiles/tectonics/``; the earthquake and volcano lists are
fetched again after :data:`REFRESH_DAYS`.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, NamedTuple

import numpy as np
import shapely
from shapely.geometry import shape

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from biomes import on_globe

logger = logging.getLogger(__name__)

PB2002_URL = "https://raw.githubusercontent.com/fraxen/tectonicplates/master/GeoJSON/{name}.json"
QUAKES_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
VOLCANOES_URL = ("https://webservices.volcano.si.edu/geoserver/GVP-VOTW/ows?service=WFS&version=1.0.0"
                 "&request=GetFeature&typeName=GVP-VOTW:Smithsonian_VOTW_Holocene_Volcanoes"
                 "&outputFormat=application/json"
                 "&propertyName=Volcano_Name,Last_Eruption_Year,Country,GeoLocation")
DATA_VERSION = 1
REFRESH_DAYS = 30
QUAKES_SINCE = 1900
DEFAULT_MIN_MAGNITUDE = 6.0
MIN_MAGNITUDE, MAX_MAGNITUDE = 5.0, 9.0
ERUPTED_SINCE = 1900

PLATES_ATTRIBUTION = "Plate boundaries: Bird (2003), PB2002, converted by Ahlenius (Nordpil), ODC-By"
QUAKES_ATTRIBUTION = "Earthquakes: USGS ANSS Comprehensive Catalog (ComCat)"
VOLCANOES_ATTRIBUTION = "Volcanoes: Global Volcanism Program, Volcanoes of the World v5, Smithsonian Institution"

# Boundary kind -> (PB2002 step classes, key label, colour)
BOUNDARY_KINDS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "divergent": (("OSR", "CRB"), "Spreading ridges and rifts (plates move apart)", "#c8321e"),
    "convergent": (("SUB", "OCB", "CCB"), "Subduction zones and collisions (plates meet)", "#1f3c96"),
    "transform": (("OTF", "CTF"), "Transform faults (plates slide past)", "#2a8c3c"),
}
# Earthquake depth classes: (deepest km, key label, colour)
DEPTH_CLASSES: list[tuple[float, str, str]] = [
    (70, "Shallow (0-70 km)", "#ff7a00"),
    (300, "Intermediate (70-300 km)", "#f2cf1d"),
    (float("inf"), "Deep (300-700 km)", "#8e3cc8"),
]
# Plates named on the map: those at least this many square degrees
PLATE_LABEL_MIN_AREA = 300.0


class TectonicsDataError(RuntimeError):
    """Plate, earthquake or volcano data could not be downloaded or read."""


class Plates(NamedTuple):
    """Boundary steps and plate names."""
    starts: np.ndarray     # (n, 2) lon, lat
    ends: np.ndarray       # (n, 2)
    kinds: np.ndarray      # (n,) "divergent" / "convergent" / "transform"
    names: np.ndarray      # plate names
    label_points: np.ndarray   # (m, 2) a point inside each plate
    areas: np.ndarray      # square degrees


class Quakes(NamedTuple):
    """Earthquakes: where, how deep (km) and how strong."""
    lon: np.ndarray
    lat: np.ndarray
    depth: np.ndarray
    magnitude: np.ndarray
    min_magnitude: float


class Volcanoes(NamedTuple):
    """Holocene volcanoes and the year each last erupted (NaN when unknown)."""
    lon: np.ndarray
    lat: np.ndarray
    names: np.ndarray
    last_eruption: np.ndarray


# ---------------------------------------------------------------------------
# Download and caching
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "tectonics")


def _get_json(url: str, timeout: float = 180) -> Any:
    # Compressed: the USGS breaks off uncompressed answers of a few MB part way through
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(gzip.decompress(body) if resp.headers.get("Content-Encoding") == "gzip" else body)


def _save(path: str, **arrays: np.ndarray) -> None:
    part = path + ".part.npz"
    np.savez_compressed(part, **arrays)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)


def _fresh(path: str, max_age_days: float) -> bool:
    try:
        return time.time() - os.path.getmtime(path) < max_age_days * 86400
    except OSError:
        return False


def read_plates(steps: dict[str, Any], plates: dict[str, Any]) -> dict[str, np.ndarray]:
    """The compact table of PB2002's boundary steps and plates (GeoJSON)."""
    kind_of = {code: kind for kind, (codes, _, _) in BOUNDARY_KINDS.items() for code in codes}
    starts, ends, kinds = [], [], []
    for feature in steps["features"]:
        props = feature["properties"]
        kind = kind_of.get(props.get("STEPCLASS", ""))
        if kind is None:
            continue
        starts.append((props["STARTLONG"], props["STARTLAT"]))
        ends.append((props["FINALLONG"], props["FINALLAT"]))
        kinds.append(kind)
    names, points, areas = [], [], []
    for feature in plates["features"]:
        outline = shape(feature["geometry"])
        # A plate cut by the date line is labelled on its larger part
        largest = max(shapely.get_parts(outline), key=lambda part: float(shapely.area(part)))
        point = largest.representative_point()
        names.append(feature["properties"]["PlateName"])
        points.append((point.x, point.y))
        areas.append(float(shapely.area(outline)))
    return {"starts": np.array(starts, float), "ends": np.array(ends, float), "kinds": np.array(kinds),
            "names": np.array(names), "label_points": np.array(points, float), "areas": np.array(areas)}


def load_plates(cache_dir: str | None = None) -> Plates:
    """PB2002's plate boundaries and plates, downloading them once (~11 MB)."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"pb2002_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Downloading the PB2002 plate boundaries (one-time, ~11 MB) …")
        try:
            table = read_plates(_get_json(PB2002_URL.format(name="PB2002_steps")),
                                _get_json(PB2002_URL.format(name="PB2002_plates")))
        except Exception as exc:              # network, JSON and geometry errors alike
            raise TectonicsDataError(f"Could not read the plate boundaries: {exc}") from exc
        _save(path, **table)
    with np.load(path) as data:
        return Plates(*(data[k] for k in Plates._fields))


def _quake_windows(min_magnitude: float, until: int) -> list[tuple[int, int]]:
    """Year ranges to query in turn, each under the service's limit of 20,000 events."""
    if min_magnitude >= 6:
        return [(QUAKES_SINCE, until)]
    # About 1,500 magnitude 5 events a year since the 1970s
    windows = [(QUAKES_SINCE, 1969)]
    windows += [(y, min(y + 9, until)) for y in range(1970, until + 1, 10)]
    return windows


def read_quakes(collection: dict[str, Any]) -> dict[str, np.ndarray]:
    """Longitude, latitude, depth and magnitude of the events in a USGS GeoJSON collection."""
    rows = [(f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1],
             f["geometry"]["coordinates"][2] or 0.0, f["properties"]["mag"])
            for f in collection["features"] if f.get("geometry") and f["properties"].get("mag") is not None]
    table = np.array(rows, float).reshape(-1, 4)
    return {"lon": table[:, 0], "lat": table[:, 1], "depth": table[:, 2], "magnitude": table[:, 3]}


def load_quakes(min_magnitude: float = DEFAULT_MIN_MAGNITUDE, cache_dir: str | None = None) -> Quakes:
    """Earthquakes of at least *min_magnitude* since 1900, cached and refreshed monthly.

    An out-of-date cache is used when the service cannot be reached.
    """
    if not MIN_MAGNITUDE <= min_magnitude <= MAX_MAGNITUDE:
        raise ValueError(f"min_magnitude must be between {MIN_MAGNITUDE:g} and {MAX_MAGNITUDE:g}, "
                         f"got {min_magnitude:g}")
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"earthquakes_m{min_magnitude:.1f}_v{DATA_VERSION}.npz")
    if not _fresh(path, REFRESH_DAYS):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Downloading earthquakes of magnitude %g and more since %d from the USGS …",
                    min_magnitude, QUAKES_SINCE)
        try:
            parts = []
            for first, last in _quake_windows(min_magnitude, time.gmtime().tm_year):
                query = urllib.parse.urlencode({"format": "geojson", "starttime": f"{first}-01-01",
                                                "endtime": f"{last}-12-31T23:59:59", "minmagnitude": min_magnitude,
                                                "orderby": "time-asc"})
                parts.append(read_quakes(_get_json(f"{QUAKES_URL}?{query}")))
            table = {k: np.concatenate([p[k] for p in parts]) for k in ("lon", "lat", "depth", "magnitude")}
            _save(path, **table)
        except Exception as exc:              # network and JSON errors alike
            if not os.path.isfile(path):
                raise TectonicsDataError(f"Could not download the earthquakes: {exc}") from exc
            logger.warning("Using earthquakes cached earlier (%s)", exc)
    with np.load(path) as data:
        return Quakes(data["lon"], data["lat"], data["depth"], data["magnitude"], min_magnitude)


def read_volcanoes(collection: dict[str, Any]) -> dict[str, np.ndarray]:
    """Position, name and latest eruption of the volcanoes in a GVP GeoJSON collection."""
    rows = [(f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1], f["properties"]["Volcano_Name"],
             f["properties"].get("Last_Eruption_Year")) for f in collection["features"] if f.get("geometry")]
    return {"lon": np.array([r[0] for r in rows], float), "lat": np.array([r[1] for r in rows], float),
            "names": np.array([r[2] for r in rows]),
            "last_eruption": np.array([np.nan if r[3] is None else r[3] for r in rows], float)}


def load_volcanoes(cache_dir: str | None = None) -> Volcanoes:
    """The Holocene volcanoes, cached and refreshed monthly; a stale cache serves when GVP is down."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"volcanoes_v{DATA_VERSION}.npz")
    if not _fresh(path, REFRESH_DAYS):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Downloading the Holocene volcanoes from the Smithsonian GVP …")
        try:
            _save(path, **read_volcanoes(_get_json(VOLCANOES_URL)))
        except Exception as exc:
            if not os.path.isfile(path):
                raise TectonicsDataError(f"Could not download the volcanoes: {exc}") from exc
            logger.warning("Using volcanoes cached earlier (%s)", exc)
    with np.load(path) as data:
        return Volcanoes(data["lon"], data["lat"], data["names"], data["last_eruption"])


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def project(ax: GeoAxes, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """*lon*, *lat* in *ax*'s projection; NaN for points out of sight."""
    xyz = ax.projection.transform_points(ccrs.PlateCarree(), np.asarray(lon, float), np.asarray(lat, float))
    x, y = xyz[:, 0], xyz[:, 1]
    bad = ~(np.isfinite(x) & np.isfinite(y))
    return np.where(bad, np.nan, x), np.where(bad, np.nan, y)


def draw_plates(ax: GeoAxes, plates: Plates, labels: bool = True, linewidth: float = 1.6) -> None:
    """Draw the plate boundaries by kind and, with *labels*, name the large plates in sight."""
    x0, y0 = project(ax, plates.starts[:, 0], plates.starts[:, 1])
    x1, y1 = project(ax, plates.ends[:, 0], plates.ends[:, 1])
    for kind, (_, _, colour) in BOUNDARY_KINDS.items():
        mine = (plates.kinds == kind) & np.isfinite(x0) & np.isfinite(x1)
        segments = np.stack([np.column_stack([x0[mine], y0[mine]]), np.column_stack([x1[mine], y1[mine]])], axis=1)
        width = linewidth * (1.4 if kind == "convergent" else 1.0)
        ax.add_collection(LineCollection(list(segments), colors=colour, linewidths=width, zorder=7,
                                         capstyle="round"))
    if not labels:
        return
    for name, (lon, lat), area in zip(plates.names, plates.label_points, plates.areas):
        if area >= PLATE_LABEL_MIN_AREA and on_globe(ax, float(lon), float(lat), margin=0.85):
            ax.text(lon, lat, f"{name} Plate", transform=ccrs.PlateCarree(), fontsize=12, fontstyle="italic",
                    color="#3a2a1a", ha="center", va="center", zorder=8)


def depth_colours(depth: np.ndarray) -> list[str]:
    """The key colour of each earthquake's depth class."""
    bounds = np.array([d for d, _, _ in DEPTH_CLASSES[:-1]])
    return [DEPTH_CLASSES[i][2] for i in np.digitize(depth, bounds)]


def quake_size(magnitude: np.ndarray | float) -> np.ndarray:
    """Marker area (points²) for *magnitude*: three times as large for each step up."""
    return 5.0 * 3.0 ** (np.asarray(magnitude, float) - 6.0)


def draw_quakes(ax: GeoAxes, quakes: Quakes) -> None:
    """Draw the earthquakes as dots sized by magnitude and coloured by depth, the largest beneath."""
    x, y = project(ax, quakes.lon, quakes.lat)
    order = np.argsort(-quakes.magnitude)
    order = order[np.isfinite(x[order])]
    ax.scatter(x[order], y[order], s=quake_size(quakes.magnitude[order]), c=depth_colours(quakes.depth[order]),
               edgecolors="#40301f", linewidths=0.3, alpha=0.85, zorder=7.2)


def draw_volcanoes(ax: GeoAxes, volcanoes: Volcanoes) -> None:
    """Draw the volcanoes as triangles, filled red if they have erupted since :data:`ERUPTED_SINCE`."""
    x, y = project(ax, volcanoes.lon, volcanoes.lat)
    recent = np.nan_to_num(volcanoes.last_eruption, nan=-1e9) >= ERUPTED_SINCE
    for mine, face in ((~recent, "white"), (recent, "#d01c1c")):
        mine = mine & np.isfinite(x)
        ax.scatter(x[mine], y[mine], s=28, marker="^", c=face, edgecolors="#3a1a0a", linewidths=0.7, zorder=7.5)


def add_tectonics_legend(ax: GeoAxes, plates: bool = False, quakes: Quakes | None = None,
                         volcanoes: bool = False, y: float = -0.01, x: float = 0.5) -> Legend | None:
    """One key for whichever of the boundaries, earthquakes and volcanoes are drawn."""
    handles: list[Any] = []
    labels: list[str] = []
    if plates:
        for _, label, colour in BOUNDARY_KINDS.values():
            handles.append(Line2D([], [], color=colour, linewidth=2.5))
            labels.append(label)
    if quakes is not None:
        for _, label, colour in DEPTH_CLASSES:
            handles.append(Line2D([], [], linestyle="none", marker="o", markersize=7, markerfacecolor=colour,
                                  markeredgecolor="#40301f", markeredgewidth=0.4))
            labels.append(f"Earthquake, {label.lower()}")
        for magnitude in range(max(int(np.ceil(quakes.min_magnitude)), 5), 10):
            size = float(np.sqrt(quake_size(magnitude)))
            handles.append(Line2D([], [], linestyle="none", marker="o", markersize=size, markerfacecolor="#bbbbbb",
                                  markeredgecolor="#40301f", markeredgewidth=0.4))
            labels.append(f"Magnitude {magnitude}")
    if volcanoes:
        for face, label in (("#d01c1c", f"Volcano, erupted since {ERUPTED_SINCE}"),
                            ("white", "Other volcano active in the last 11,700 years")):
            handles.append(Line2D([], [], linestyle="none", marker="^", markersize=8, markerfacecolor=face,
                                  markeredgecolor="#3a1a0a", markeredgewidth=0.7))
            labels.append(label)
    if not handles:
        return None
    parts = [p for p, on in (("plate boundaries", plates), ("earthquakes", quakes is not None),
                             ("volcanoes", volcanoes)) if on]
    title = "Plate tectonics: " + ", ".join(parts)
    if quakes is not None:
        title += f" (earthquakes of magnitude {quakes.min_magnitude:g}+ since {QUAKES_SINCE}, {len(quakes.lon):,})"
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=3, fontsize=10,
                    title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=1.8, columnspacing=1.4, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend


def credits(plates: bool, quakes: bool, volcanoes: bool) -> list[str]:
    """The credit lines for the parts drawn."""
    return [line for line, on in ((PLATES_ATTRIBUTION, plates), (QUAKES_ATTRIBUTION, quakes),
                                  (VOLCANOES_ATTRIBUTION, volcanoes)) if on]

