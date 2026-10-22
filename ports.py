"""Ports: the world's seaports as dots sized by how many ships call there, the busiest named.

Data source
-----------
The IMF's PortWatch ports database (https://portwatch.imf.org): 2,065 ports
with their location and, from the ships' AIS positions, the yearly average
number of ships calling over 2019-2025, in total and by kind (container
ships, dry bulk carriers, general cargo ships, ro-ro ships and tankers).
IMF data may be reused with credit (https://www.imf.org/external/terms.htm,
"Special Terms for Statistical Data").

The table (~0.5 MB) is downloaded once, as PortWatch's own download button
gives it, into ``~/.cache/ortho_tiles/ports/`` and read again after a year.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

import frame
from air_routes import near_side
from cities import label_space
from indicators import _tls

logger = logging.getLogger(__name__)

DATA_URL = "https://portwatch.imf.org/api/download/v1/items/acc668d199d1472abaaf2467133d4ca4/csv?layers=0"
FILE = "portwatch_ports.csv"
REFRESH_DAYS = 365
DEFAULT_KIND = "all"
LABEL_MAX = 30
# Dot area per ship calling a year: Singapore's ~44,000 ships get a dot of ~21 points across on a 15-inch globe
POINTS2_PER_SHIP = 0.01
MIN_POINTS2 = 5
KEY_SHIPS = (1_000, 5_000, 20_000)
# One dark colour for every kind of port: it stands out from the ship traffic's blues, purples and oranges
PORT_COLOUR = "#08306b"


class PortKind(NamedTuple):
    column: str                   # PortWatch's column of ships calling a year
    label: str


KINDS: dict[str, PortKind] = {
    "all": PortKind("vessel_count_total", "Ships of every kind"),
    "container": PortKind("vessel_count_container", "Container ships"),
    "tanker": PortKind("vessel_count_tanker", "Tankers"),
    "dry-bulk": PortKind("vessel_count_dry_bulk", "Dry bulk carriers"),
    "general-cargo": PortKind("vessel_count_general_cargo", "General cargo ships"),
    "roro": PortKind("vessel_count_RoRo", "Ro-ro ships (vehicles, ferries)"),
}

ALIASES = {
    "": "all", "ports": "all", "total": "all", "every": "all", "ships": "all",
    "containers": "container", "box": "container",
    "tankers": "tanker", "oil": "tanker",
    "bulk": "dry-bulk", "dry-bulk-carriers": "dry-bulk", "bulk-carriers": "dry-bulk",
    "general": "general-cargo", "cargo": "general-cargo",
    "ro-ro": "roro", "ferries": "roro", "cars": "roro", "vehicles": "roro",
}

PORTS_ATTRIBUTION = "Ports: IMF PortWatch (portwatch.imf.org), ships calling 2019-2025"


class PortsDataError(RuntimeError):
    """PortWatch could not be reached or sent something unreadable."""


class Ports(NamedTuple):
    kind: str                     # a key of KINDS
    names: list[str]
    countries: list[str]
    lon: np.ndarray
    lat: np.ndarray
    ships: np.ndarray             # ships of the kind calling a year (2019-2025 average)


def resolve_port_kind(kind: str | None) -> str:
    """The key of :data:`KINDS` for *kind* (blank for every ship). Raises ``ValueError`` for an unknown kind."""
    key = (kind or "").strip().lower().replace("_", "-").replace(" ", "-")
    key = ALIASES.get(key, key)
    if key not in KINDS:
        key = ALIASES.get(key.removesuffix("s"), key.removesuffix("s"))
    if key not in KINDS:
        raise ValueError(f"Unknown kind of port traffic {kind!r}; choose from {', '.join(KINDS)}.")
    return key


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "ports")


def _download(timeout: float = 120) -> str:
    request = urllib.request.Request(DATA_URL, headers={"User-Agent": "ortho/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_tls()) as resp:
            text = resp.read().decode("utf-8-sig")
    except (urllib.error.URLError, OSError, UnicodeDecodeError) as exc:
        raise PortsDataError(f"Could not reach IMF PortWatch: {exc}") from exc
    header = text.split("\n", 1)[0]
    if "portname" not in header or "vessel_count_total" not in header:
        raise PortsDataError(f"IMF PortWatch sent something other than its ports table: {text[:200]!r}")
    return text


def _table(cache_dir: str | None) -> str:
    """PortWatch's ports table as text, from the cache, downloaded when missing or a year old."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, FILE)
    cached = None
    try:
        with open(path, encoding="utf-8") as f:
            cached = f.read()
    except OSError:
        pass
    if cached is not None and time.time() - os.path.getmtime(path) <= REFRESH_DAYS * 86400:
        return cached
    try:
        logger.info("Downloading the ports from IMF PortWatch …")
        text = _download()
    except PortsDataError as exc:
        if cached is None:
            raise
        logger.warning("Using the ports downloaded on %s: %s",
                       time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path))), exc)
        return cached
    os.makedirs(cache_dir, exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(path + ".part", path)
    return text


def _number(text: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return 0.0
    return value if np.isfinite(value) else 0.0


def short_name(name: str) -> str:
    """A port's name for a label: without a trailing "Port" (``Qingdao Port``), at most 28 letters."""
    text = re.sub(r"\s+", " ", (name or "").strip())
    text = re.sub(r"\s+(Port|Pt\.?)$", "", text, flags=re.IGNORECASE) or text
    if len(text) <= 28:
        return text
    cut = text[:27].rsplit(" ", 1)[0] if " " in text[:27] else text[:27]
    return cut.rstrip(" -,(") + "…"


def load_ports(kind: str = DEFAULT_KIND, cache_dir: str | None = None) -> Ports:
    """The ports where ships of *kind* (a key of :data:`KINDS`) call, busiest first.

    Raises :class:`PortsDataError` when PortWatch cannot be reached and nothing is cached.
    """
    column = KINDS[kind].column
    rows = []
    for row in csv.DictReader(io.StringIO(_table(cache_dir))):
        ships = _number(row.get(column, ""))
        lon, lat = _number(row.get("lon", "")), _number(row.get("lat", ""))
        if ships > 0 and (lon or lat) and row.get("portname"):
            rows.append((ships, row["portname"].strip(), (row.get("country") or "").strip(), lon, lat))
    if not rows:
        raise PortsDataError("IMF PortWatch's ports table has no ports in it")
    rows.sort(key=lambda r: -r[0])
    return Ports(kind, [r[1] for r in rows], [r[2] for r in rows], np.array([r[3] for r in rows], float),
                 np.array([r[4] for r in rows], float), np.array([r[0] for r in rows], float))


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _scale(ax: GeoAxes) -> float:
    """How much larger dots are drawn than on a 15-inch whole globe: with the map's size, and up to twice as wide
    on a close map, where ports would otherwise look lost."""
    width_in = ax.get_window_extent().width / ax.figure.dpi
    x0, x1 = ax.get_xlim()
    zoom = 2 * 6_371_000 / abs(x1 - x0) if x1 != x0 else 1.0
    return float(np.clip(width_in / 15, 0.25, 4) * np.clip(zoom ** 0.25, 1, 2))


def dot_size(ships: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Marker area (points²) for ports *ships* ships call at a year: proportional, with a floor to stay visible."""
    return np.maximum(np.asarray(ships, float) * POINTS2_PER_SHIP, MIN_POINTS2) * scale ** 2


def in_view(ax: GeoAxes, lon: np.ndarray, lat: np.ndarray, margin: float = 1.0) -> np.ndarray:
    """True for the points that show on *ax*: on its side of the globe and within *margin* of the map's size."""
    if len(lon) == 0:
        return np.zeros(0, bool)
    xy = ax.projection.transform_points(ccrs.PlateCarree(), lon, lat)[:, :2]
    seen = near_side(ax, lon, lat, xy)
    return seen & frame.inside(ax, xy[:, 0], xy[:, 1], margin)


def draw_ports(ax: GeoAxes, ports: Ports, labels: bool = True, limit: int = LABEL_MAX, alpha: float = 0.85) -> int:
    """Draw the ports as dots sized by the ships calling, the busiest on top, and name the busiest.

    Returns how many ports show.
    """
    seen = np.flatnonzero(in_view(ax, ports.lon, ports.lat))
    if not len(seen):
        return 0
    seen = seen[np.argsort(-ports.ships[seen], kind="stable")]                # the busiest first
    scale = _scale(ax)
    sizes = dot_size(ports.ships[seen], scale)
    order = seen[::-1]                            # the quietest first, so the busiest are drawn on top
    ax.scatter(ports.lon[order], ports.lat[order], s=sizes[::-1], c=PORT_COLOUR, alpha=alpha,
               edgecolors="white", linewidths=0.6 * scale, transform=ccrs.PlateCarree(), zorder=7.5)
    if labels:
        space = label_space(ax)
        named = 0
        width = float(np.clip(ax.get_window_extent().width / ax.figure.dpi / 15, 0.25, 4))   # type grows with the map only
        for i, size in zip(seen, sizes):
            if named >= limit:
                break
            name = short_name(ports.names[i])
            x, y = ax.projection.transform_point(float(ports.lon[i]), float(ports.lat[i]), ccrs.PlateCarree())
            font = float(np.clip(6 + np.log2(max(ports.ships[i], 1) / 1_000), 6, 10)) * width ** 0.5
            if not name or not space.claim(x, y, name, font, point=np.sqrt(size) * ax.figure.dpi / 72):
                continue
            named += 1
            ax.annotate(name, (x, y), xycoords="data", xytext=(0, 0.5 * np.sqrt(size) + 1),
                        textcoords="offset points", fontsize=font, color="#0b2545", ha="center", va="bottom",
                        zorder=8, annotation_clip=True, path_effects=[pe.withStroke(linewidth=2.2, foreground="white")])
    logger.info("Ports drawn: %d.", len(seen))
    return len(seen)


def add_ports_legend(ax: GeoAxes, ports: Ports, shown: int, y: float = -0.01, x: float = 0.5,
                     alpha: float = 0.85) -> Legend:
    """A key to the dot sizes, titled with the kind of ships and how many ports are in view."""
    scale = _scale(ax)
    handles, labels = [], []
    for ships in KEY_SHIPS:
        handles.append(Line2D([], [], linestyle="none", marker="o", markerfacecolor=PORT_COLOUR,
                              markeredgecolor="white", alpha=alpha,
                              markersize=float(np.sqrt(dot_size(np.array([ships]), scale)[0]))))
        labels.append(f"{ships:,} ships a year")
    what = "Ports" if ports.kind == "all" else f"Ports of {KINDS[ports.kind].label.split(' (')[0].lower()}"
    title = f"{what} in view ({shown:,}), sized by the ships calling a year, 2019-2025 (IMF PortWatch)"
    widest = float(np.sqrt(dot_size(np.array([max(KEY_SHIPS)]), scale)[0])) / 10   # in font sizes
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=len(handles),
                    fontsize=10, title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=max(2.2, widest + 0.4), columnspacing=1.6,
                    borderpad=max(0.8, 0.5 * widest))
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
