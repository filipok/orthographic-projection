"""Romania's relief units: the geomorphological regions of Posea & Badea, as plains, depressions and mountains.

Data source
-----------
Candrea, B., Candrea, P. & Niță, M. D. (2008). Harta unităților de relief din
România. geo-spatial.org, CC BY-SA 3.0 —
https://geo-spatial.org/vechi/download/harta-unitati-relief-romania. A digital
map, on the SRTM elevation model, of the geomorphological regionalisation of
Posea, G. & Badea, L. (1984), *România. Unitățile de relief (Regionarea
geomorfologică)*, 1:800,000, Editura Științifică și Enciclopedică, București:
820 units, from the major regions (Carpații Orientali, Câmpia Română …) down to
single depressions, corridors and plains (Depresiunea Hațegului, Câmpia
Ierului …), each coded with the region it belongs to.

The shapefile (~1.3 MB) mostly lost its diacritics (``Podisul Tîrnaveni``);
its legend, an Excel sheet (~90 KB), has them, and the names are restored from
it: each unit's name without its first word (Depresiunea, Câmpia …) is matched
against the legend's, ignoring accents, and the rest word by word. ``î`` inside
a word becomes ``â`` and ş, ţ take the comma below, as written since 1993. 52
small units have no name of their own (50 ``Depresiune``, 2 ``Deal``) and are
drawn unnamed.

The legend also tells which units make up which larger ones: Câmpia Careiului
is Câmpia Valea lui Mihai and Câmpia Pișcolțului, and with Câmpia Ierului and
others it makes up Câmpia Someșului. The map has only the smallest units, so
each larger one is drawn as the outline of the units the legend puts in it and
named in bold. Both files are downloaded once and cached in a compact form.

The low ground inside the mountains and hills, the depressions, corridors and
floodplains (Depresiunea, Culoarul, Lunca …), is told by the kind its name
begins with and drawn in a paler, greener shade of its region's colour.
"""

from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import NamedTuple

import numpy as np
import shapefile
import shapely
import shapely.affinity
import xlrd
from shapely.geometry import shape

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

import frame
from biomes import _download, _key, visible_points
from borders import _pack, _unpack
from cities import label_space

logger = logging.getLogger(__name__)

SHAPES_URL = "https://geo-spatial.org/vechi/file_download/24263"    # the shapefile in degrees (WGS 84), zipped
LEGEND_URL = "https://geo-spatial.org/vechi/file_download/24264"    # its legend, an MS Excel sheet
DATA_VERSION = 3          # bump when the cache changes, so old ones are rebuilt

RELIEF_ATTRIBUTION = ("Relief units: Candrea, Candrea & Niță 2008 (geo-spatial.org), after Posea & Badea 1984, "
                      "CC BY-SA 3.0")

LEVELS = ("units", "regions")
DEFAULT_LEVEL = "units"
_LEVEL_ALIASES = {"": "units", "all": "units", "unit": "units", "subunits": "units",
                  "region": "regions", "major": "regions"}

# Each major region (Roman numeral, as Posea & Badea number them) in the colours of a physical map:
# the Carpathians brown, the Subcarpathians, hills and plateaus ochre, the plains green, the Delta blue-green
REGION_COLOURS: dict[str, tuple[int, int, int]] = {
    "I": (196, 140, 98), "II": (214, 160, 112), "III": (181, 124, 88), "IV": (206, 154, 110),
    "V": (190, 136, 100),
    "VI": (236, 206, 140), "VII": (242, 220, 160), "VIII": (234, 212, 150), "IX": (226, 184, 124),
    "X": (222, 192, 134), "XI": (240, 204, 138), "XII": (245, 224, 158), "XV": (230, 202, 152),
    "XIII": (186, 214, 152), "XIV": (200, 224, 164),
    "XVI": (160, 206, 192),
}
OTHER_COLOUR = (220, 220, 210)
# Depressions, corridors and floodplains: their region's colour, this far toward the green-yellow of low ground
LOW_KINDS = frozenset({"Depresiunea", "Depresiunile", "Culoarul", "Culoarele", "Lunca", "Balta"})
LOW_GROUND = (214, 232, 170)
LOW_BLEND = 0.65
UNIT_EDGE = "#7a5c40"
GROUP_EDGE = "#5e4430"
REGION_EDGE = "#4a3520"
LABEL_COLOUR = "#3d2a17"
GROUP_LABEL_COLOUR = "#5a3a1c"
# A unit is named when it covers at least this share of the map, at one of this many places at most
LABEL_MIN_SHARE = 0.0004
ANCHORS = 6

# The kinds of relief unit that begin the names (folded, see _fold) and how they are written in full
GENERIC = {
    "depresiunea": "Depresiunea", "depresiune": "Depresiunea", "depr": "Depresiunea", "dep": "Depresiunea",
    "deal": "Dealul", "dealul": "Dealul", "dealull": "Dealul", "dealurile": "Dealurile", "bealurile": "Dealurile",
    "d": "Dealurile", "dl": "Dealul", "culmea": "Culmea", "cl": "Culmea", "muntii": "Munții", "munti": "Munții",
    "mtii": "Munții", "mti": "Munții", "muntele": "Muntele", "muntela": "Muntele", "m": "Muntele",
    "masivul": "Masivul", "ms": "Masivul", "masivele": "Masivele", "obcina": "Obcina", "ob": "Obcina",
    "obcinele": "Obcinele", "culoarul": "Culoarul", "cul": "Culoarul", "culoarele": "Culoarele",
    "piemontul": "Piemontul", "piem": "Piemontul", "podisul": "Podișul", "pod": "Podișul",
    "campia": "Câmpia", "c": "Câmpia", "campul": "Câmpul", "cp": "Câmpul", "subcarpatii": "Subcarpații",
    "subc": "Subcarpații", "colinele": "Colinele", "colinelei": "Colinele", "col": "Colinele",
    "lunca": "Lunca", "lc": "Lunca", "glacisul": "Glacisul", "gl": "Glacisul", "grindul": "Grindul",
    "gr": "Grindul", "grindurile": "Grindurile", "balta": "Balta", "magura": "Măgura", "mg": "Măgura",
    "magurile": "Măgurile", "plaiul": "Plaiul", "lacul": "Lacul", "l": "Lacul", "gruiurile": "Gruiurile",
    "muchia": "Muchia", "conul": "Conul", "clabucetele": "Clăbucetele", "muscelele": "Muscelele",
    "deple": "Depresiunile", "oble": "Obcinele", "msle": "Masivele", "mgle": "Măgurile", "grle": "Grindurile",
}
# The legend's "Dl." and "D." stand for both Dealul and Dealurile, so the names of groups keep them short
_AMBIGUOUS_KINDS = {"dl": "Dl.", "d": "Dl."}
# How a kind is shortened when the full name does not fit, as on Romanian maps
SHORT = {"Depresiunea": "Dep.", "Munții": "M-ții", "Muntele": "M.", "Masivul": "Ms.", "Câmpia": "C.",
         "Câmpul": "Cp.", "Podișul": "Pod.", "Dealurile": "Dl.", "Dealul": "Dl.", "Culoarul": "Cul.",
         "Culmea": "Cl.", "Subcarpații": "Subc.", "Obcina": "Ob.", "Piemontul": "Piem.", "Colinele": "Col.",
         "Glacisul": "Gl.", "Măgura": "Mg."}
_COMMA_BELOW = str.maketrans("şŞţŢ", "șȘțȚ")
_ABBREVIATED = re.compile(r"[^\W\d_]{1,5}\.?(-[^\W\d_]{1,4}\.?)?")
_ROMAN = re.compile(r"[IVX]+")


class ReliefError(RuntimeError):
    """The relief units could not be downloaded or read."""


class Unit(NamedTuple):
    """A relief unit (or, at the level of regions, a major region): its name, region and outline."""
    name: str           # "" for a unit without a name of its own
    region: str         # Roman numeral of its major region ("I" … "XVI")
    outline: shapely.Geometry
    low: bool = False   # a depression, corridor or floodplain (see LOW_KINDS)


class Relief(NamedTuple):
    """The relief units to draw, largest first, with the names of the major regions."""
    level: str                      # "units" or "regions"
    units: list[Unit]
    regions: dict[str, str]         # "I" -> "Carpații Orientali (Răsăriteni)"
    borders: Sequence[shapely.Geometry]  # the outline of each major region, drawn over the units
    groups: Sequence[Unit] = ()     # the larger units the smallest make up (Câmpia Careiului), largest first
    low: Sequence[Unit] = ()        # at the level of regions, the depressions, corridors and floodplains in them


def resolve_relief_level(level: str | None) -> str:
    """``"units"`` or ``"regions"`` for *level* (``None`` or ``""`` for units). Raises ``ValueError`` otherwise."""
    text = (level or "").strip().lower()
    text = _LEVEL_ALIASES.get(text, text)
    if text not in LEVELS:
        raise ValueError(f"Unknown level of relief units {level!r}; choose from {', '.join(LEVELS)}.")
    return text


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _fold(text: str) -> str:
    """*text* lower-case without accents or anything but letters and digits (``â`` and ``î`` alike)."""
    text = re.sub("[îÎâÂ]", "a", text)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def split_kind(name: str) -> tuple[str, str]:
    """``(kind, rest)`` of a unit's *name*: ``("Dep.", "Hațegului")``; ``("", name)`` when it starts with no kind."""
    name = re.sub(r"\s+", " ", name.strip())
    first, _, rest = name.partition(" ")
    if _fold(first) in GENERIC or ("." in first or "-" in first) and _ABBREVIATED.fullmatch(first):
        return first, rest
    return "", name


def _modern(word: str) -> str:
    """*word* as written since 1993: ``î`` inside a word is ``â`` (Tîrgu → Târgu), not at its start (Întorsura)."""
    return (word[:1] + word[1:].replace("î", "â")).translate(_COMMA_BELOW)


class Entry(NamedTuple):
    """A unit in the legend: its subregion's letter and the names from the subregion down to it."""
    subregion: str                  # "A" …, or "" in a region without subregions
    path: tuple[str, ...]           # ("Câmpia Someșului", "C. Careiului", "C. Valea lui Mihai")


class Key(NamedTuple):
    """What the legend (the Excel sheet) tells: the major regions' names, the names with their diacritics, and
    which units each is part of."""
    regions: dict[str, str]
    rests: dict[str, str]           # a name without its kind, folded -> as written
    words: dict[str, str]           # a word, folded -> as written most often
    entries: dict[tuple[str, str], list[Entry]]   # (region, name without its kind, folded) -> the units so named


def read_legend(xls: bytes) -> Key:
    """The legend of the relief units, from its Excel sheet (see :func:`legend_from_rows`)."""
    try:
        sheet = xlrd.open_workbook(file_contents=xls).sheet_by_index(0)
    except Exception as exc:                  # xlrd raises its own errors for anything not a workbook
        raise ReliefError(f"The legend of the relief units is not an Excel sheet: {exc}") from exc
    return legend_from_rows([[str(v) for v in sheet.row_values(i)] for i in range(sheet.nrows)])


def legend_from_rows(rows: Iterable[Sequence[str]]) -> Key:
    """The legend from its rows: pairs of columns, a code and a name, a major region's (``I``, ``II`` …) first,
    then its subregion's (``A`` …) and the units within, each a column further right. A row leaves blank
    the columns of the units it is part of, named on the rows above."""
    regions: dict[str, str] = {}
    rests: dict[str, str] = {}
    forms: defaultdict[str, Counter[str]] = defaultdict(Counter)
    entries: defaultdict[tuple[str, str], list[Entry]] = defaultdict(list)
    codes: list[str] = []
    names: list[str] = []
    for row in rows:
        row = [v.strip() for v in row]
        for k in range(0, len(row) - 1, 2):
            code, name = row[k].rstrip("."), row[k + 1].translate(_COMMA_BELOW)
            if not name:
                continue
            level = k // 2
            codes = (codes + [""] * level)[:level] + [code]
            names = (names + [""] * level)[:level] + [name]
            if level == 0 and _ROMAN.fullmatch(code):
                regions[code] = name
            elif codes[0] in regions:
                entry = Entry(codes[1] if level > 1 else code, tuple(n for n in names[1:] if n))
                entries[(codes[0], _fold(split_kind(name)[1]))].append(entry)
            rests.setdefault(_fold(split_kind(name)[1]), split_kind(name)[1])
            for word in re.findall(r"[^\W\d_]+", name):
                forms[_fold(word)][word] += 1
    if not regions:
        raise ReliefError("The legend of the relief units names no regions")
    return Key(regions, rests, {key: c.most_common(1)[0][0] for key, c in forms.items()}, dict(entries))


def restore_name(raw: str, legend: Key) -> str:
    """*raw*, a unit's name in the shapefile, with its diacritics: ``Muntii Sacarîmbului`` → ``Munții Săcărâmbului``.

    ``""`` for a unit named only by its kind (``Depresiune``).
    """
    kind, rest = split_kind(raw)
    kind = GENERIC.get(_fold(kind), kind) if kind else ""
    rest = re.sub(r"\s*-\s*", " - ", rest).strip()
    if not rest:
        return ""
    if _fold(rest) in legend.rests:
        rest = legend.rests[_fold(rest)]
    else:
        def restored(match: re.Match[str]) -> str:
            word = match.group(0)
            if _fold(word) in GENERIC and len(word) > 2:
                return GENERIC[_fold(word)]
            if re.search("[ăâșțşţ]", word, re.I):
                return _modern(word)
            return _modern(legend.words.get(_fold(word), word))
        rest = re.sub(r"[^\W\d_]+", restored, rest)
        rest = re.sub(r"\b([A-Z])([A-Z])(?=[a-z])", lambda m: m.group(1) + m.group(2).lower(), rest)  # MAsivul
        rest = re.sub(r"\s+\)", ")", rest)
    return f"{kind} {rest}" if kind else rest


def unit_kind(raw: str) -> str:
    """The kind of relief unit, in full, that the shapefile's name *raw* begins with (``Depresiunea``); ``""``
    for the few names that begin with none (``Bistrita Bargaului``, ``Valea Ianca``)."""
    kind = split_kind(raw)[0]
    return GENERIC.get(_fold(kind), kind) if kind else ""


def group_name(name: str) -> str:
    """The legend's *name* of a group of units with its kind in full: ``Dep.-le Gheorgheni-Ciuc`` →
    ``Depresiunile Gheorgheni-Ciuc``; ``Dl.`` stays short, being Dealul or Dealurile."""
    kind, rest = split_kind(name)
    if not kind:
        return name
    full = _AMBIGUOUS_KINDS.get(_fold(kind)) or GENERIC.get(_fold(kind), kind)
    return f"{full} {rest}"


def find_entry(raw: str, region: str, subregion: str, legend: Key) -> Entry | None:
    """The legend's entry for the unit the shapefile names *raw* in *region* (and *subregion*), if any."""
    rest = re.sub(r"\s*-\s*", " - ", split_kind(raw)[1]).strip()
    found = legend.entries.get((region, _fold(rest)), []) if rest else []
    return next((e for e in found if e.subregion == subregion), found[0] if found else None)


def short_names(name: str) -> list[str]:
    """Ways to write *name* on the map, longest first: in full, without what is in brackets, then the kind
    shortened (``Dep. Hațegului``)."""
    plain = re.sub(r"\s*\([^)]*\)", "", name).strip()
    kind, rest = split_kind(plain)
    shorter = f"{SHORT[kind]} {rest}" if kind in SHORT else plain
    return list(dict.fromkeys([name, plain, shorter]))


# ---------------------------------------------------------------------------
# Download and caching
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "relief")


def read_units(shapes_zip: bytes, legend: Key) -> dict[str, np.ndarray]:
    """The table of the zipped shapefile: each unit's restored name, region and outline."""
    with zipfile.ZipFile(io.BytesIO(shapes_zip)) as archive:
        members = {os.path.splitext(n)[1].lower(): n for n in archive.namelist()}
        if not {".shp", ".shx", ".dbf"} <= members.keys():
            raise ReliefError("The relief units' archive holds no shapefile")
        files = {ext: io.BytesIO(archive.read(members[ext])) for ext in (".shp", ".shx", ".dbf")}
    reader = shapefile.Reader(shp=files[".shp"], shx=files[".shx"], dbf=files[".dbf"], encoding="iso8859_16")
    names, regions, outlines, paths, low = [], [], [], [], []
    for item in reader.iterShapeRecords():
        if item.shape is None or item.record is None:
            continue
        row = item.record.as_dict()
        outline = shapely.make_valid(shape(dict(item.shape.__geo_interface__)))
        if outline.is_empty:
            continue
        raw, region = str(row["DENUMIRE"] or ""), str(row["REGIUNE"] or "").strip()
        names.append(restore_name(raw, legend))
        regions.append(region)
        outlines.append(outline)
        entry = find_entry(raw, region, str(row["SUBREGIUNE"] or "").strip(), legend)
        paths.append((region, entry.path) if entry else None)
        low.append(unit_kind(raw) in LOW_KINDS)
    if not outlines:
        raise ReliefError("The relief units' shapefile is empty")
    groups = unit_groups(paths)
    group_outlines = [shapely.union_all([outlines[i] for i in units], grid_size=1e-6) for units in groups.values()]
    packed = _pack(group_outlines)
    codes = sorted(legend.regions)
    return {"names": np.array(names), "regions": np.array(regions), "low": np.array(low, dtype=bool),
            "region_codes": np.array(codes),
            "region_names": np.array([legend.regions[c] for c in codes]), **_pack(outlines),
            "group_names": np.array([group_name(path[-1]) for _, path in groups], dtype=str),
            "group_regions": np.array([region for region, _ in groups], dtype=str),
            "group_wkb": packed["wkb"], "group_wkb_ends": np.asarray(packed["wkb_ends"], dtype=np.int64)}


def unit_groups(paths: Sequence[tuple[str, tuple[str, ...]] | None]) -> dict[tuple[str, tuple[str, ...]], list[int]]:
    """The larger units the mapped ones make up, as ``(region, legend path) -> the mapped units in it``.

    *paths* are each mapped unit's region and legend path (``None`` when it is
    not in the legend). A larger unit counts when at least two different
    units are mapped in it and it is not mapped itself.
    """
    mapped = {p for p in paths if p is not None}
    members: defaultdict[tuple[str, tuple[str, ...]], list[int]] = defaultdict(list)
    for i, found in enumerate(paths):
        if found is None:
            continue
        region, path = found
        for depth in range(1, len(path)):
            members[(region, path[:depth])].append(i)
    return {group: units for group, units in members.items()
            if group not in mapped and len({paths[i] for i in units}) >= 2}


def ensure_relief_data(cache_dir: str | None = None) -> str:
    """Path to the cached relief units, downloading and reading them if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"relief_units_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached relief units: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Downloading Romania's relief units from geo-spatial.org (one-time, ~1.4 MB) …")
    shapes, sheet = os.path.join(cache_dir, "units.download"), os.path.join(cache_dir, "legend.download")
    try:
        _download(SHAPES_URL, shapes, label="relief units")
        _download(LEGEND_URL, sheet, label="their legend")
        with open(shapes, "rb") as fh, open(sheet, "rb") as legend_fh:
            table = read_units(fh.read(), read_legend(legend_fh.read()))
    except ReliefError:
        raise
    except Exception as exc:                  # network, archive and geometry errors alike
        raise ReliefError(f"Could not read the relief units: {exc}") from exc
    finally:
        for download in (shapes, sheet):
            if os.path.exists(download):
                os.remove(download)
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    logger.info("Relief units ready: %s", path)
    return path


def load_relief(level: str = DEFAULT_LEVEL, cache_dir: str | None = None) -> Relief:
    """Romania's relief units at *level*: every unit, or the major regions they make up."""
    with np.load(ensure_relief_data(cache_dir)) as data:
        table = {key: data[key] for key in data.files}
    regions = {str(c): str(n) for c, n in zip(table["region_codes"], table["region_names"])}
    outlines = _unpack(table)
    by_region: defaultdict[str, list[shapely.Geometry]] = defaultdict(list)
    for code, outline in zip(table["regions"], outlines):
        by_region[str(code)].append(outline)
    borders = [shapely.union_all(parts, grid_size=1e-6) for parts in by_region.values()]
    groups: list[Unit] = []
    every = [Unit(str(n), str(r), o, bool(k))
             for n, r, o, k in zip(table["names"], table["regions"], outlines, table["low"])]
    low: list[Unit] = []
    if level == "regions":
        units = [Unit(regions.get(code, ""), code, border) for code, border in zip(by_region, borders)]
        low = [u for u in every if u.low]
    else:
        units = every
        group_outlines = _unpack({"wkb": table["group_wkb"], "wkb_ends": table["group_wkb_ends"]})
        groups = [Unit(str(n), str(r), o)
                  for n, r, o in zip(table["group_names"], table["group_regions"], group_outlines)]
        groups.sort(key=lambda u: float(shapely.area(u.outline)), reverse=True)
    units.sort(key=lambda u: float(shapely.area(u.outline)), reverse=True)
    return Relief(level, units, regions, borders, groups, low)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def region_colour(code: str, low: bool = False) -> tuple[float, float, float]:
    """The colour of region *code*, or with *low* of its depressions, corridors and floodplains."""
    rgb = np.array(REGION_COLOURS.get(code, OTHER_COLOUR), float)
    if low:
        rgb += LOW_BLEND * (np.array(LOW_GROUND, float) - rgb)
    r, g, b = rgb / 255
    return float(r), float(g), float(b)


def visible_units(ax: GeoAxes, units: Sequence[Unit], samples: int = 300) -> dict[int, np.ndarray]:
    """The sample points (see :func:`biomes.visible_points`) each unit covers on *ax*, as ``index -> points``,
    the points' indices into ``visible_points(ax, samples)``."""
    lon, lat, _, _ = visible_points(ax, samples)
    if not len(lon) or not units:
        return {}
    tree = shapely.STRtree(np.array([u.outline for u in units], dtype=object))
    point_index, unit_index = tree.query(shapely.points(lon, lat), predicate="within")
    return {int(i): point_index[unit_index == i] for i in np.unique(unit_index)}


def anchors(unit: Unit, lon: np.ndarray, lat: np.ndarray, count: int = ANCHORS) -> list[int]:
    """Where to try naming *unit*, among its visible points (*lon*, *lat*), best first: the one deepest inside
    it, clear of its edges as a name of a long, bent unit needs to be, the one nearest the middle of what
    shows, then others less deep, in case names already placed cover those (*count* in all, at most)."""
    # Distances in degrees of latitude: longitudes shrink by the cosine of the unit's latitude
    shrink = float(np.cos(np.radians(np.mean(lat))))
    edge = shapely.affinity.scale(unit.outline.boundary, shrink, 1, origin=(0, 0))
    depth = shapely.distance(shapely.points(lon * shrink, lat), edge)
    middle = (lon - lon.mean()) ** 2 + (lat - lat.mean()) ** 2
    by_depth = np.argsort(-depth, kind="stable")
    others = by_depth[:: max(1, len(by_depth) // count)][1:count]
    return list(dict.fromkeys([int(by_depth[0]), int(np.argmin(middle)), *(int(k) for k in others)]))


def label_size(share: float, level: str) -> float:
    """Font size (points) of the name of a unit covering *share* of the map."""
    if level == "regions":
        return float(np.clip(9 + 40 * np.sqrt(share), 9, 16))
    return float(np.clip(5.5 + 30 * np.sqrt(share), 5.5, 12))


def draw_relief(ax: GeoAxes, relief: Relief, alpha: float = 0.55, labels: bool = True,
                samples: int = 300) -> dict[int, int]:
    """Fill *relief*'s units on *ax* in their region's colour, outline them, the groups they make up and the
    regions, and name them.

    Names go in the middle of what shows of each unit or group, largest
    first (so a group before the units in it), in the room the other layers'
    names leave (see :func:`cities.label_space`), shortened when the full name
    does not fit; the groups' names are bold. Returns how many sample points
    each unit covers (index into ``relief.units``).
    """
    # Each unit in its region's colour, its low ground paler; a region's own fill leaves out the low ground in it
    by_colour: defaultdict[tuple[str, bool], list[shapely.Geometry]] = defaultdict(list)
    low_in: defaultdict[str, list[shapely.Geometry]] = defaultdict(list)
    for unit in relief.low:
        low_in[unit.region].append(unit.outline)
        by_colour[(unit.region, True)].append(unit.outline)
    for unit in relief.units:
        outline = unit.outline
        if low_in.get(unit.region):
            outline = shapely.difference(outline, shapely.union_all(low_in[unit.region], grid_size=1e-6),
                                         grid_size=1e-6)
        by_colour[(unit.region, unit.low)].append(outline)
    for (code, low), outlines in by_colour.items():
        ax.add_geometries(outlines, crs=ccrs.PlateCarree(), facecolor=(*region_colour(code, low), alpha),
                          edgecolor="none", zorder=5)
    if relief.level == "units":
        ax.add_geometries([u.outline for u in relief.units], crs=ccrs.PlateCarree(), facecolor="none",
                          edgecolor=UNIT_EDGE, linewidth=0.3, zorder=5.5)
    if relief.groups:
        ax.add_geometries([g.outline for g in relief.groups], crs=ccrs.PlateCarree(), facecolor="none",
                          edgecolor=GROUP_EDGE, linewidth=0.55, zorder=5.55)
    ax.add_geometries(relief.borders, crs=ccrs.PlateCarree(), facecolor="none", edgecolor=REGION_EDGE,
                      linewidth=0.9, zorder=5.6)

    found = visible_units(ax, relief.units, samples)
    counts = {i: len(points) for i, points in found.items()}
    if not labels or not counts:
        return counts
    found_groups = visible_units(ax, relief.groups, samples)
    total = frame.coverage(ax) * samples ** 2
    lon, lat, x, y = visible_points(ax, samples)
    space = label_space(ax)
    named = groups_named = 0
    # Units and groups alike, largest first; a group is larger than each unit in it, so it comes first
    shown = [(len(points), True, i, points) for i, points in found_groups.items()]
    shown += [(len(points), False, i, points) for i, points in found.items()]
    shown.sort(key=lambda item: (-item[0], not item[1]))
    for count, group, i, points in shown:
        share = count / total
        if share < LABEL_MIN_SHARE:
            break
        unit = relief.groups[i] if group else relief.units[i]
        if not unit.name:
            continue
        size = label_size(share, relief.level)
        # A name sits a little above its anchor, so the anchor goes that much below the point it is centred on
        lift = _points_to_data(ax, 0.85 * size)
        place = next(((float(x[points[k]]), float(y[points[k]]) - lift, text)
                      for k in anchors(unit, lon[points], lat[points])
                      for text in short_names(unit.name)
                      if space.claim(float(x[points[k]]), float(y[points[k]]) - lift, text, size, point=0,
                                     bold=group or relief.level == "regions")), None)
        if place is None:
            continue
        if group:
            groups_named += 1
        else:
            named += 1
        px, py, text = place
        ax.annotate(text, (px, py), xycoords="data", xytext=(0, 0.25 * size), textcoords="offset points",
                    fontsize=size, fontstyle="italic",
                    fontweight="bold" if group or relief.level == "regions" else "normal",
                    color=GROUP_LABEL_COLOUR if group else LABEL_COLOUR, ha="center", va="bottom", zorder=7.9,
                    annotation_clip=True)
    logger.info("Relief units drawn: %d in view, %d named, and %d of the groups they make up.", len(counts),
                named, groups_named)
    return counts


def _points_to_data(ax: GeoAxes, points: float) -> float:
    """*points* of type as a height in *ax*'s map coordinates."""
    (_, y0), (_, y1) = ax.transData.inverted().transform([(0, 0), (0, points * ax.figure.dpi / 72)])
    return float(y1 - y0)


def add_relief_legend(ax: GeoAxes, relief: Relief, counts: Iterable[dict[int, int]],
                      y: float = -0.01, x: float = 0.5) -> Legend | None:
    """A key to the major regions on the map, in their colours, largest first, and to their low ground when
    some shows. ``None`` when none shows."""
    shares: Counter[str] = Counter()
    units: Counter[str] = Counter()
    low: Counter[str] = Counter()           # the low ground in view, by region
    for per_globe in counts:
        for i, n in per_globe.items():
            shares[relief.units[i].region] += n
            units[relief.units[i].region] += 1
            if relief.units[i].low:
                low[relief.units[i].region] += n
    if not shares:
        return None
    for i, points in (visible_units(ax, relief.low) if relief.low else {}).items():
        low[relief.low[i].region] += len(points)
    top = [code for code, _ in shares.most_common()]
    handles = [mpatches.Patch(facecolor=region_colour(c), edgecolor=REGION_EDGE, linewidth=0.6) for c in top]
    labels = [f"{c}. {relief.regions.get(c, c)}" for c in top]
    if low:
        handles.append(mpatches.Patch(facecolor=region_colour(low.most_common(1)[0][0], low=True),
                                      edgecolor=REGION_EDGE, linewidth=0.6))
        labels.append("Depressions, corridors and floodplains: a paler, greener shade of their region")
    what = "Major relief regions" if relief.level == "regions" else f"Relief units ({sum(units.values()):,})"
    title = f"{what} of Romania in view, after Posea & Badea 1984 (Candrea et al. 2008)"
    if relief.groups:
        title += "\nin bold: the larger units they make up"
    return _key(ax, handles, labels, title, min(len(top), 3), x, y)
