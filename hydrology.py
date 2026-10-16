"""Rivers and river basins.

Data sources
------------
- **Rivers:** Natural Earth's 1:10m rivers and lake centrelines (public
  domain), ranked by importance; Cartopy downloads them once.
- **Basins:** Lehner, B. & Grill, G. (2013). Global river hydrography and
  network routing: baseline data and new approaches to study the world's large
  river systems. *Hydrological Processes* 27(15), 2171-2186.
  https://doi.org/10.1002/hyp.9740 — HydroBASINS level 4 (HydroSHEDS, WWF),
  free for scientific, educational and commercial use with this citation.

HydroBASINS splits the land into nested sub-basins; those sharing an outlet
to the sea or an inland sink make up one river's basin. The nine regional
level 4 files (~55 MB) are downloaded once and merged into the basins of at
least :data:`MIN_BASIN_KM2`, cached (~5 MB) under
``~/.cache/ortho_tiles/hydrology/``. HydroBASINS has no names, so each basin
takes the name of the most important Natural Earth river flowing through it,
corrected where that is a headwater, a tributary or a name in another
language (:data:`BASIN_NAMES`).
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, NamedTuple

import numpy as np
import shapely
from shapely.geometry import shape

import cartopy.crs as ccrs
import cartopy.io.shapereader as shapereader
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend

from biomes import _download, _key
from borders import Polity, _pack, _rgb, _unpack, colour_groups, draw_borders

logger = logging.getLogger(__name__)

HYDROBASINS_URL = "https://data.hydrosheds.org/file/hydrobasins/standard/hybas_{region}_lev04_v1c.zip"
REGIONS = ("af", "ar", "as", "au", "eu", "gr", "na", "sa", "si")
DATA_VERSION = 1
MIN_BASIN_KM2 = 100_000

RIVERS_ATTRIBUTION = "Rivers: Natural Earth"
BASINS_ATTRIBUTION = "River basins: HydroBASINS (Lehner & Grill 2013), HydroSHEDS / WWF"

RIVER_COLOUR = "#2a63b0"
# Rivers of these Natural Earth ranks (0 most important) are drawn; those up to NAMED_RIVER_RANK
# may be named, the longest on the map first, at most MAX_RIVER_LABELS of them
MAX_RIVER_RANK = 6
NAMED_RIVER_RANK = 4
MAX_RIVER_LABELS = 25
# Basins draining to an inland sink (the Caspian, Lake Chad, the Aral) share this colour
INLAND_RGB = (190, 182, 166)
INLAND_GROUP = "(inland)"
BASINS_KEY_MAX = 10


# Basin names to correct, each by a point inside the basin, as the rule picking the main river
# from Natural Earth sometimes takes a headwater or tributary of the same rank, a local name or
# a delta branch. By point, not name: two basins come out "Volga" and two "Mackenzie".
BASIN_NAMES: tuple[tuple[float, float, str], ...] = (
    # A headwater or tributary instead of the river at the mouth
    (108.5, 30.2, "Yangtze"),               # Jinsha
    (108.2, 24.2, "Pearl"),                 # Nanpan
    (99.3, 24.4, "Salween"),                # Nu
    (114.6, 38.3, "Hai"),                   # Sanggan
    (121.2, 43.1, "Liao"),                  # Xar Moron
    (99.3, 69.6, "Khatanga"),               # Kheta
    (99.7, 74.2, "Taymyra"),                # Verkhnyaya Taymyra
    (12.1, -0.2, "Ogooué"),                 # Aïna
    (-104.5, 56.3, "Churchill"),            # Beaver
    (-68.6, 55.4, "Koksoak"),               # Caniapiscau
    (-82.1, 49.4, "Moose"),                 # Mattagami
    (-93.8, 55.1, "Hayes"),                 # God's
    (-87.1, 32.9, "Mobile"),                # Alabama
    (-99.6, 22.2, "Pánuco"),                # Verde
    (148.9, -23.8, "Fitzroy"),              # the Australian Mackenzie
    (118.3, -31.8, "Swan"),                 # Avon
    (32.5, 60.0, "Neva"),                   # came out "Volga"
    # Two rivers sharing a mouth, a delta branch, or a name in another language
    (85.2, 27.0, "Ganges–Brahmaputra"),
    (43.3, 34.0, "Tigris–Euphrates"),       # Shatt al-Arab
    (95.8, 22.6, "Irrawaddy"),              # Irrawaddy Delta
    (105.7, 37.0, "Yellow"),                # Huang
    (103.6, 22.9, "Red"),                   # Hong
    (-64.4, -7.6, "Amazon"),                # Amazonas
    (-9.6, 14.4, "Senegal"),                # Sénégal
    (78.0, 19.6, "Godavari"),               # Godävari in Natural Earth
    (82.5, 21.4, "Mahanadi"),               # Mahäna Nadï
    (48.5, 61.7, "Northern Dvina"),         # Severnaya Dvina
    # Inland basins, by their sink or main river
    (137.8, -26.1, "Lake Eyre"),
    (81.7, 38.8, "Tarim"),                  # Yarkant
    (20.6, -18.6, "Okavango"),              # Cubango
    (21.6, 10.8, "Lake Chad"),              # Chari
    (76.6, 45.7, "Ili"),                    # Ile
)


class HydrologyDataError(RuntimeError):
    """River or basin data could not be downloaded or read."""


class River(NamedTuple):
    name: str                  # in English: one name for a river's sections in different languages
    rank: int                  # Natural Earth's scale rank, 0 the most important
    line: shapely.Geometry
    labelled: bool = True      # False for delta branches and lines through lakes


# Delta branches and channels ("Damietta Branch", "Sulina branch", the Lena's "Protoka") are drawn, not named
_BRANCH_WORDS = frozenset({"branch", "delta", "protoka"})


def river_name(attributes: dict[str, Any]) -> str:
    """A river section's name in English (Natural Earth's ``name_en``, else ``name``), without "River".

    A Rio keeps its local name: the English one drops the "Rio" ("Grande" for the Rio Grande).
    """
    local = str(attributes.get("name") or "").strip()
    if local.split(" ", 1)[0] in ("Rio", "Río"):
        return local
    name = str(attributes.get("name_en") or local).strip()
    return re.sub(r"\s+river$", "", name, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# Rivers
# ---------------------------------------------------------------------------


def load_rivers(max_rank: int = MAX_RIVER_RANK) -> list[River]:
    """Natural Earth's rivers up to *max_rank*, most important first."""
    try:
        reader = shapereader.Reader(shapereader.natural_earth("10m", "physical", "rivers_lake_centerlines"))
        rivers = []
        for record in reader.records():
            attributes: dict[str, Any] = dict(record.attributes)
            rank = int(attributes["scalerank"])
            if record.geometry is not None and rank <= max_rank:
                name = river_name(attributes)
                labelled = (attributes.get("featurecla") != "Lake Centerline"
                            and not _BRANCH_WORDS & set(name.lower().split()))
                rivers.append(River(name, rank, record.geometry, labelled))
    except Exception as exc:
        raise HydrologyDataError(f"Could not read the Natural Earth rivers: {exc}") from exc
    return sorted(rivers, key=lambda r: r.rank)


def project_lines(ax: GeoAxes, line: shapely.Geometry) -> list[np.ndarray]:
    """The visible runs of *line* in *ax*'s projection."""
    runs = []
    for part in shapely.get_parts(line):
        xy = np.asarray(part.coords)[:, :2]
        p = ax.projection.transform_points(ccrs.PlateCarree(), xy[:, 0], xy[:, 1])[:, :2]
        ok = np.isfinite(p).all(axis=1)
        # Split where points go out of sight
        breaks = np.flatnonzero(np.diff(ok.astype(int)) != 0) + 1
        for chunk, good in zip(np.split(p, breaks), np.split(ok, breaks)):
            if good[0] and len(chunk) > 1:
                runs.append(chunk)
    return runs


def river_width(rank: int) -> float:
    return max(0.5, 2.4 - 0.3 * rank)


def _length(run: np.ndarray) -> float:
    return float(np.hypot(*np.diff(run, axis=0).T).sum())


def _midpoint(run: np.ndarray) -> tuple[float, float]:
    """The point halfway along a projected line."""
    steps = np.hypot(*np.diff(run, axis=0).T)
    along = np.concatenate([[0.0], np.cumsum(steps)])
    half = along[-1] / 2
    i = min(int(np.searchsorted(along, half)), len(run) - 1)
    if i == 0:
        return float(run[0, 0]), float(run[0, 1])
    t = (half - along[i - 1]) / max(steps[i - 1], 1e-12)
    x, y = run[i - 1] + t * (run[i] - run[i - 1])
    return float(x), float(y)


def draw_rivers(ax: GeoAxes, rivers: Sequence[River], labels: bool = True) -> None:
    """Draw *rivers* on *ax*, wider for the more important, and name the longest in sight.

    Rivers are named in order of how far they run on the map, each once,
    halfway along its longest stretch in sight; a name that would overlap
    another, run off the globe or sit on a stretch too short to read is left
    out, and at most :data:`MAX_RIVER_LABELS` are placed.
    """
    segments: list[np.ndarray] = []
    widths: list[float] = []
    runs_by_name: dict[str, list[np.ndarray]] = defaultdict(list)
    for river in rivers:
        runs = project_lines(ax, river.line)
        segments += runs
        widths += [river_width(river.rank)] * len(runs)
        if river.labelled and river.name and river.rank <= NAMED_RIVER_RANK:
            runs_by_name[river.name] += runs
    ax.add_collection(LineCollection(segments, colors=RIVER_COLOUR, linewidths=widths, zorder=6.5,
                                     capstyle="round", joinstyle="round"))
    if not labels:
        return
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    to_pixels = ax.transData
    (px0, py0), (px1, py1) = to_pixels.transform([(x0, y0), (x1, y1)])
    centre, radius = ((px0 + px1) / 2, (py0 + py1) / 2), abs(px1 - px0) / 2
    dpi = ax.figure.dpi
    fontsize = 10
    placed: list[tuple[float, float, float, float]] = []
    # Keep clear of names already on the map (the basins'), sized as estimated for the rivers'
    for text in ax.texts:
        tx, ty = text.get_position()
        transform = text.get_transform()
        px, py = transform.transform((tx, ty))
        if not (np.isfinite(px) and np.isfinite(py)):
            continue
        size = text.get_fontsize()
        half_w, half_h = 0.3 * size * len(text.get_text()) * dpi / 72, 0.6 * size * dpi / 72
        placed.append((px - half_w, py - half_h, px + half_w, py + half_h))
    already = len(placed)
    by_length = sorted(runs_by_name, key=lambda n: sum(_length(r) for r in runs_by_name[n]), reverse=True)
    for name in by_length:
        runs = runs_by_name[name]
        if not runs or len(placed) - already >= MAX_RIVER_LABELS:
            continue
        run = max(runs, key=_length)
        if _length(run) < 0.04 * (x1 - x0):          # too short a stretch to name
            continue
        x, y = _midpoint(run)
        px, py = to_pixels.transform((x, y))
        half_w, half_h = 0.3 * fontsize * len(name) * dpi / 72, 0.6 * fontsize * dpi / 72
        box = (px - half_w, py, px + half_w, py + 2 * half_h)
        corners = [(box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3])]
        if any((cx - centre[0]) ** 2 + (cy - centre[1]) ** 2 > (0.95 * radius) ** 2 for cx, cy in corners):
            continue
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
            continue
        placed.append(box)
        ax.text(x, y, name, transform=ax.projection, fontsize=fontsize, fontstyle="italic", color=RIVER_COLOUR,
                ha="center", va="bottom", zorder=8, path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])


# ---------------------------------------------------------------------------
# Basins
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "hydrology")


class Basin(NamedTuple):
    area: float                 # km²
    inland: bool                # drains to an inland sink, not the sea
    outline: shapely.Geometry
    outlet: shapely.Geometry    # the sub-basin at its mouth


def merge_basins(records: Sequence[dict[str, Any]], outlines: Sequence[shapely.Geometry],
                 min_km2: float = MIN_BASIN_KM2) -> list[Basin]:
    """Each river basin of at least *min_km2*.

    Sub-basins sharing an outlet (``MAIN_BAS``) make one basin; HydroBASINS'
    coastal strips (``COAST``), many small rivers lumped together, are left out.
    A basin is inland when its outlet is a sink (``ENDO`` 2); closed hollows
    inside a basin that reaches the sea do not count.
    """
    groups: dict[int, list[int]] = defaultdict(list)
    for i, record in enumerate(records):
        if not record["COAST"]:
            groups[int(record["MAIN_BAS"])].append(i)
    basins = []
    for members in groups.values():
        area = sum(float(records[i]["SUB_AREA"]) for i in members)
        if area < min_km2:
            continue
        mouth = next((i for i in members if int(records[i]["HYBAS_ID"]) == int(records[i]["MAIN_BAS"])), members[0])
        inland = int(records[mouth]["ENDO"]) == 2
        outline = shapely.union_all([outlines[i] for i in members])
        basins.append(Basin(area, inland, outline, outlines[mouth]))
    return basins


def name_basins(basins: Sequence[Basin], rivers: Sequence[River]) -> list[str]:
    """Each basin's name: the main river reaching its mouth.

    Among the named rivers through the outlet sub-basin, the most important,
    then the one running furthest through the basin; failing those, any named
    river in the basin. Unnamed when there is none.
    """
    named = [r for r in rivers if r.name]
    tree = shapely.STRtree(np.array([r.line for r in named], dtype=object))

    def best(area: shapely.Geometry, basin: shapely.Geometry) -> str:
        choice: tuple[int, float] | None = None
        name = ""
        for i in tree.query(area, predicate="intersects"):
            river = named[int(i)]
            score = (river.rank, -float(shapely.length(shapely.intersection(river.line, basin))))
            if choice is None or score < choice:
                choice, name = score, river.name
        return name

    return [best(b.outlet, b.outline) or best(b.outline, b.outline) for b in basins]


def ensure_basins_data(cache_dir: str | None = None) -> str:
    """Path to the cached basins, built from HydroBASINS (~55 MB, once) if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"basins_lev04_{MIN_BASIN_KM2}_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Building the river basins from HydroBASINS (one-time, ~55 MB in %d files) …", len(REGIONS))
    try:
        basins: list[Basin] = []
        for region in REGIONS:
            download = os.path.join(cache_dir, f"hybas_{region}_lev04.zip")
            if not os.path.isfile(download):
                _download(HYDROBASINS_URL.format(region=region), download, label=f"HydroBASINS {region}")
            basins += read_hydrobasins(download)
        rivers = load_rivers(max_rank=8)
        names = name_basins(basins, rivers)
    except HydrologyDataError:
        raise
    except Exception as exc:                  # network, archive and geometry errors alike
        raise HydrologyDataError(f"Could not build the river basins: {exc}") from exc
    part = path + ".part.npz"
    np.savez_compressed(part, names=np.array(names), areas=np.array([b.area for b in basins]),  # pyright: ignore[reportArgumentType]
                        inland=np.array([b.inland for b in basins]), **_pack([b.outline for b in basins]))
    os.replace(part, path)
    for region in REGIONS:
        os.remove(os.path.join(cache_dir, f"hybas_{region}_lev04.zip"))
    return path


def read_hydrobasins(zip_path: str) -> list[Basin]:
    """The merged basins of one HydroBASINS level 4 regional archive."""
    import shapefile  # pyshp, which Cartopy depends on

    with zipfile.ZipFile(zip_path) as archive:
        members = {n.rsplit(".", 1)[-1].lower(): n for n in archive.namelist()
                   if n.lower().rsplit(".", 1)[-1] in ("shp", "shx", "dbf")}
        reader = shapefile.Reader(shp=io.BytesIO(archive.read(members["shp"])),
                                  shx=io.BytesIO(archive.read(members["shx"])),
                                  dbf=io.BytesIO(archive.read(members["dbf"])))
        records: list[dict[str, Any]] = []
        outlines: list[shapely.Geometry] = []
        with reader:
            for record, polygon in zip(reader.iterRecords(), reader.iterShapes()):
                if polygon is not None and record is not None:
                    records.append(record.as_dict())
                    outlines.append(shapely.make_valid(shape(dict(polygon.__geo_interface__))))
    return merge_basins(records, outlines)


def load_basins(cache_dir: str | None = None) -> list[Polity]:
    """The basins as :class:`borders.Polity` (one group each; inland basins share a group), largest first."""
    with np.load(ensure_basins_data(cache_dir)) as data:
        table = {k: data[k] for k in data.files}
    outlines = _unpack(table)
    names = corrected_names([str(n) for n in table["names"]], outlines)
    basins = [Polity(name, INLAND_GROUP if inland else f"basin{i}",
                     "Inland drainage (no outlet to the sea)" if inland else (name or "Unnamed basin"),
                     False, outline)
              for i, (name, inland, outline) in enumerate(zip(names, table["inland"], outlines))]
    order = np.argsort(-table["areas"])
    return [basins[i] for i in order]


def corrected_names(names: Sequence[str], outlines: Sequence[shapely.Geometry],
                    corrections: Sequence[tuple[float, float, str]] = BASIN_NAMES) -> list[str]:
    """*names* with :data:`BASIN_NAMES` applied: each correction renames the basin holding its point."""
    names = list(names)
    tree = shapely.STRtree(np.array(outlines, dtype=object))
    for lon, lat, name in corrections:
        for i in tree.query(shapely.Point(lon, lat), predicate="within"):
            names[int(i)] = name
    return names


def basin_colours(basins: Sequence[Polity]) -> dict[str, tuple[int, int, int]]:
    """Colours apart from the neighbours', the inland basins all grey."""
    rivers = [b for b in basins if b.group != INLAND_GROUP]
    colours = colour_groups(rivers) if rivers else {}
    colours[INLAND_GROUP] = INLAND_RGB
    return colours


def draw_basins(ax: GeoAxes, basins: Sequence[Polity], colours: dict[str, tuple[int, int, int]],
                alpha: float = 0.5) -> dict[int, int]:
    """Fill, outline and name the basins (see :func:`borders.draw_borders`)."""
    return draw_borders(ax, basins, colours, alpha=alpha)


def add_basins_legend(ax: GeoAxes, basins: Sequence[Polity], counts: Sequence[dict[int, int]],
                      colours: dict[str, tuple[int, int, int]], y: float = -0.01, x: float = 0.5,
                      limit: int = BASINS_KEY_MAX) -> Legend | None:
    """A key naming the largest basins on the map, and the inland drainage colour."""
    shares: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    for per_globe in counts:
        for i, n in per_globe.items():
            shares[basins[i].group] += n
            names[basins[i].group] = basins[i].group_name
    if not shares:
        return None
    top = [g for g in sorted(shares, key=shares.__getitem__, reverse=True) if g != INLAND_GROUP][:limit]
    if INLAND_GROUP in shares:
        top.append(INLAND_GROUP)
    handles = [mpatches.Patch(facecolor=_rgb(colours[g]), edgecolor="#333333", linewidth=0.6) for g in top]
    labels = [names[g] for g in top]
    river_count = len([g for g in shares if g != INLAND_GROUP])
    title = f"River basins of {MIN_BASIN_KM2:,} km² or more (HydroBASINS)"
    if river_count > limit:
        title += f": the {limit} largest of {river_count} on the map"
    return _key(ax, handles, labels, title, min(len(top), 3), x, y)
