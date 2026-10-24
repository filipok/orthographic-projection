"""Industry: refineries, petrochemical plants, steelworks, mines, power stations … as circles sized by emissions.

Data source
-----------
Climate TRACE (https://climatetrace.org), its emissions inventory of
individual plants worldwide — CC BY 4.0, read from its public API
(https://api.climatetrace.org/v6; beta, no key). Each plant has a location,
a name, a type (``Deep Conversion`` refinery, ``BF/BOF`` steelworks …), a
capacity and its estimated yearly greenhouse-gas emissions (CO₂ equivalent,
100-year), which size the circles: a fair measure of how big a plant is
across sectors. Emissions are Climate TRACE's estimates for the last full
year.

Oil and gas production is counted by basin (``Russia_Central Sub-basin - West
Siberia``), not by field, so its circles stand for whole basins. What a
country produces outside its named basins ("OtherBasins", nearly a third of
the emissions) is one figure at one point in the country: it is drawn as a
hollow ring, named "Russia, other basins". The ``all`` group leaves the
basins out, as they would swamp the plants.

Each sector is read once (a few hundred to ~12,000 plants, a request per
thousand, gently paced as Climate TRACE asks) and cached under
``~/.cache/ortho_tiles/industry/``; it is read again after 90 days.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from biomes import on_globe
from cities import label_space
from indicators import _tls

logger = logging.getLogger(__name__)

API = "https://api.climatetrace.org/v6"
DATA_VERSION = 1
PAGE = 1000
PAUSE = 0.3                     # seconds between pages: Climate TRACE asks for a light touch
REFRESH_DAYS = 90
LABEL_MAX = 30
# Circle area per tonne of CO2e: a plant of 10 Mt a year gets a circle of 16 points across on a 15-inch globe
POINTS2_PER_TONNE = 200 / 10e6
MIN_POINTS2 = 6
KEY_SIZES = (1e6, 5e6, 20e6)


class Sector(NamedTuple):
    subsectors: tuple[str, ...]   # Climate TRACE's names
    label: str
    colour: str


SECTORS: dict[str, Sector] = {
    "refineries": Sector(("oil-and-gas-refining",), "Oil refineries", "#3f007d"),
    "petrochemicals": Sector(("petrochemical-steam-cracking",), "Petrochemical plants (steam crackers)", "#e7298a"),
    "chemicals": Sector(("chemicals",), "Chemical plants (ammonia, methanol, soda ash)", "#807dba"),
    "steel": Sector(("iron-and-steel",), "Steelworks", "#2171b5"),
    "aluminium": Sector(("aluminum",), "Aluminium smelters and refineries", "#6baed6"),
    "cement": Sector(("cement",), "Cement works", "#8c6d31"),
    "glass": Sector(("glass",), "Glassworks", "#41b6c4"),
    "paper": Sector(("pulp-and-paper",), "Pulp and paper mills", "#238b45"),
    "oil-gas": Sector(("oil-and-gas-production",), "Oil and gas basins (each a whole basin)", "#fd8d3c"),
    "coal-mines": Sector(("coal-mining",), "Coal mines", "#252525"),
    "copper-mines": Sector(("copper-mining",), "Copper mines", "#b35806"),
    "iron-mines": Sector(("iron-mining",), "Iron ore mines", "#a50f15"),
    "bauxite-mines": Sector(("bauxite-mining",), "Bauxite mines", "#f16913"),
    "power": Sector(("electricity-generation",), "Fossil-fuel power stations", "#e31a1c"),
}

GROUPS: dict[str, tuple[str, ...]] = {
    "petrochemical": ("refineries", "petrochemicals", "chemicals"),
    "heavy": ("refineries", "petrochemicals", "chemicals", "steel", "aluminium", "cement"),
    "mining": ("coal-mines", "copper-mines", "iron-mines", "bauxite-mines"),
    "all": tuple(name for name in SECTORS if name != "oil-gas"),   # basins would swamp the plants
}

INDUSTRY_ATTRIBUTION = "Industry: Climate TRACE (climatetrace.org), CC BY 4.0, retrieved {retrieved}"


class IndustryDataError(RuntimeError):
    """Climate TRACE could not be reached or sent something unreadable."""


class Plants(NamedTuple):
    sector: str                   # a key of SECTORS
    names: list[str]
    kinds: list[str]              # Climate TRACE's asset type
    lon: np.ndarray
    lat: np.ndarray
    emissions: np.ndarray         # tonnes CO2e a year (0 where unknown)
    year: int
    retrieved: str                # YYYY-MM-DD
    remainder: np.ndarray | None = None   # True for a country's other basins, gathered at one point


def resolve_sectors(text: str | None) -> tuple[str, ...]:
    """The sectors named in *text* (comma-separated sectors and groups), in :data:`SECTORS`' order.

    Raises ``ValueError`` for an unknown name.
    """
    wanted: set[str] = set()
    for item in (part.strip().lower().replace("_", "-").replace(" ", "-") for part in (text or "").split(",")):
        if not item:
            continue
        forms = [item, item + "s", item[:-1] + "ies" if item.endswith("y") else item, item.removesuffix("s"),
                 item.replace("aluminum", "aluminium")]
        group = next((f for f in forms if f in GROUPS), None)
        sector = next((f for f in forms if f in SECTORS), None)
        if group:
            wanted.update(GROUPS[group])
        elif sector:
            wanted.add(sector)
        else:
            raise ValueError(f"Unknown industry {item!r}; choose from {', '.join(SECTORS)}, "
                             f"or the groups {', '.join(GROUPS)}.")
    if not wanted:
        raise ValueError(f"Name the industries, e.g. petrochemical, or some of {', '.join(SECTORS)}.")
    return tuple(name for name in SECTORS if name in wanted)


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "industry")


def _get_json(url: str, timeout: float = 120):
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_tls()) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise IndustryDataError(f"Could not reach Climate TRACE: {exc}") from exc


def _short_name(name: str) -> str:
    """A plant's name for a label: up to its first comma (``Zhejiang Petroleum & Chemical``), without bracketed
    asides, country prefixes (``CHN-``) or underscores, at most 32 letters."""
    text = (name or "").split(",")[0]
    if "_OtherBasins_" in text:                  # a country's remainder: Country_OtherBasins_Resource
        return f"{text.split('_')[0]}, other basins"
    if text.count("_") >= 2:                     # Climate TRACE's basins: Country_Basin_Resource
        text = text.split("_")[1]
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"^[A-Z]{3}-", "", text.strip()).replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 32:
        return text
    cut = text[:31].rsplit(" ", 1)[0] if " " in text[:31] else text[:31]
    return cut.rstrip(" -&") + "…"


def _fetch(subsector: str, year: int) -> list[list]:
    """Every plant of *subsector*: ``[[name, kind, lon, lat, tonnes CO2e], ...]``."""
    rows: list[list] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"subsectors": subsector, "year": year, "limit": PAGE, "offset": offset})
        answer = _get_json(f"{API}/assets?{query}")
        assets = answer.get("assets") if isinstance(answer, dict) else None
        if assets is None:
            raise IndustryDataError(f"Climate TRACE sent no plants for {subsector}: {str(answer)[:200]}")
        for asset in assets:
            point = (asset.get("Centroid") or {}).get("Geometry") or []
            if len(point) != 2:
                continue
            tonnes = sum(float(e.get("EmissionsQuantity") or 0) for e in asset.get("EmissionsSummary") or []
                         if e.get("Gas") == "co2e_100yr")
            rows.append([asset.get("Name") or "", asset.get("AssetType") or "", float(point[0]), float(point[1]),
                         tonnes])
        if len(assets) < PAGE:
            return rows
        offset += PAGE
        time.sleep(PAUSE)


def load_plants(sector: str, cache_dir: str | None = None) -> Plants:
    """The plants of *sector* (a key of :data:`SECTORS`), read from Climate TRACE once and every 90 days.

    Raises :class:`IndustryDataError` when Climate TRACE cannot be reached and nothing is cached (a stale cache
    is used instead, with a warning).
    """
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"{sector}_v{DATA_VERSION}.json")
    cached = None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        pass
    if cached is None or time.time() - os.path.getmtime(path) > REFRESH_DAYS * 86400:
        year = time.localtime().tm_year - 1
        try:
            logger.info("Reading the %s from Climate TRACE …", SECTORS[sector].label.lower())
            rows = [row for sub in SECTORS[sector].subsectors for row in _fetch(sub, year)]
            fresh = {"year": year, "retrieved": time.strftime("%Y-%m-%d"), "rows": rows}
            os.makedirs(cache_dir, exist_ok=True)
            with open(path + ".part", "w", encoding="utf-8") as f:
                json.dump(fresh, f)
            os.replace(path + ".part", path)
            cached = fresh
        except IndustryDataError as exc:
            if cached is None:
                raise
            logger.warning("Using the Climate TRACE plants of %s: %s", cached.get("retrieved"), exc)
    rows = cached["rows"]
    return Plants(sector, [r[0] for r in rows], [r[1] for r in rows], np.array([r[2] for r in rows], float),
                  np.array([r[3] for r in rows], float), np.array([r[4] for r in rows], float), int(cached["year"]),
                  cached["retrieved"], np.array(["_OtherBasins_" in r[0] for r in rows], bool))


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _scale(ax: GeoAxes) -> float:
    """How much larger circles are drawn than on a 15-inch whole globe: with the map's size, and up to twice as
    wide on a close map (a 300 km disc), where plants would otherwise look lost."""
    width_in = ax.get_window_extent().width / ax.figure.dpi
    x0, x1 = ax.get_xlim()
    zoom = 2 * 6_371_000 / abs(x1 - x0) if x1 != x0 else 1.0
    return float(np.clip(width_in / 15, 0.25, 4) * np.clip(zoom ** 0.25, 1, 2))


def circle_size(tonnes: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Marker area (points²) for plants emitting *tonnes* CO2e a year: proportional, with a floor to stay visible."""
    return np.maximum(np.asarray(tonnes, float) * POINTS2_PER_TONNE, MIN_POINTS2) * scale ** 2


def draw_industry(ax: GeoAxes, plants: list[Plants], labels: bool = True, limit: int = LABEL_MAX,
                  alpha: float = 0.8) -> dict[str, int]:
    """Draw the plants of each sector as circles sized by emissions, the biggest underneath, and name the largest.

    A country's other oil and gas basins, one figure at one point, are hollow
    rings. Returns how many plants of each sector show and, under
    ``"SECTOR:other"``, how many of them are such rings.
    """
    scale = _scale(ax)
    everything = []
    counts: dict[str, int] = {}
    for group in plants:
        seen = [i for i in range(len(group.names))
                if on_globe(ax, float(group.lon[i]), float(group.lat[i]), margin=1.0)]
        counts[group.sector] = len(seen)
        if group.remainder is not None and group.remainder[seen].any():
            counts[f"{group.sector}:other"] = int(group.remainder[seen].sum())
        everything += [(group.emissions[i], group, i) for i in seen]
    if not everything:
        return counts
    everything.sort(key=lambda e: -e[0])
    lon = np.array([g.lon[i] for _, g, i in everything])
    lat = np.array([g.lat[i] for _, g, i in everything])
    colours = [SECTORS[g.sector].colour for _, g, _ in everything]
    sizes = circle_size(np.array([t for t, _, _ in everything]), scale)
    ring = np.array([g.remainder is not None and bool(g.remainder[i]) for _, g, i in everything], bool)
    if (~ring).any():
        ax.scatter(lon[~ring], lat[~ring], s=sizes[~ring], c=[c for c, r in zip(colours, ring) if not r], alpha=alpha,
                   edgecolors="white", linewidths=0.5 * scale, transform=ccrs.PlateCarree(), zorder=7.4)
    if ring.any():
        ax.scatter(lon[ring], lat[ring], s=sizes[ring], facecolors="none", alpha=alpha,
                   edgecolors=[c for c, r in zip(colours, ring) if r], linewidths=1.8 * scale,
                   transform=ccrs.PlateCarree(), zorder=7.45)
    if labels:
        space = label_space(ax)
        named = 0
        for (tonnes, group, i), size in zip(everything, sizes):
            if named >= limit or tonnes <= 0:
                break
            name = _short_name(group.names[i])
            x, y = ax.projection.transform_point(float(group.lon[i]), float(group.lat[i]), ccrs.PlateCarree())
            font = float(np.clip(6 + 1.5 * np.log2(max(tonnes, 1) / 1e6), 6, 10)) * scale ** 0.5
            if not name or not space.claim(x, y, name, font, point=np.sqrt(size) * ax.figure.dpi / 72):
                continue
            named += 1
            ax.annotate(name, (x, y), xycoords="data", xytext=(0, 0.5 * np.sqrt(size) + 1),
                        textcoords="offset points", fontsize=font, color="#222222", ha="center", va="bottom",
                        zorder=8, annotation_clip=True)
    logger.info("Industry drawn: %s.", ", ".join(f"{n:,} {SECTORS[s].label.lower()}" for s, n in counts.items()
                                                 if s in SECTORS))
    return counts


def _tonnes(n: float) -> str:
    return f"{n / 1e6:g} Mt"


def add_industry_legend(ax: GeoAxes, plants: list[Plants], counts: dict[str, int],
                        y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the sectors (with how many plants of each are in view) and, in a last column, to the circle
    sizes."""
    scale = _scale(ax)
    handles, labels = [], []
    for group in plants:
        handles.append(Line2D([], [], linestyle="none", marker="o", markersize=8,
                              markerfacecolor=SECTORS[group.sector].colour, markeredgecolor="white", alpha=0.8))
        labels.append(f"{SECTORS[group.sector].label} ({counts.get(group.sector, 0):,})")
        if counts.get(f"{group.sector}:other"):
            handles.append(Line2D([], [], linestyle="none", marker="o", markersize=8, markerfacecolor="none",
                                  markeredgecolor=SECTORS[group.sector].colour, markeredgewidth=1.8, alpha=0.8))
            labels.append(f"Rest of a country's basins, at one point ({counts[f'{group.sector}:other']:,})")
    rows = max(len(KEY_SIZES), -(-len(handles) // 3))          # at most three columns of sectors
    columns = -(-len(handles) // rows)

    def pad(to: int) -> None:                     # blank entries, so the legend's columns fall as planned
        while len(handles) < to:
            handles.append(Line2D([], [], linestyle="none"))
            labels.append("")

    pad(columns * rows)
    for tonnes in KEY_SIZES:
        handles.append(Line2D([], [], linestyle="none", marker="o", markerfacecolor="none", markeredgecolor="#555555",
                              markersize=float(np.sqrt(circle_size(np.array([tonnes]), scale)[0]))))
        labels.append(f"{_tonnes(tonnes)} CO₂e a year")
    pad((columns + 1) * rows)
    year = max((p.year for p in plants), default=0)
    title = f"Industry in view, circles sized by greenhouse-gas emissions in {year} (Climate TRACE)"
    widest = float(np.sqrt(circle_size(np.array([max(KEY_SIZES)]), scale)[0])) / 10   # in font sizes
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=columns + 1,
                    fontsize=10, title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=max(2.2, widest + 0.4), columnspacing=1.6,
                    borderpad=max(0.8, 0.5 * widest), labelspacing=max(1.0, 0.6 * widest))
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend


def industry_attribution(plants: list[Plants]) -> str:
    return INDUSTRY_ATTRIBUTION.format(retrieved=max((p.retrieved for p in plants), default=""))
