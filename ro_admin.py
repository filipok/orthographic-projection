"""Romania's counties (județe) and administrative-territorial units (UATs): their borders and names.

Data sources
------------
Borders: Eurostat GISCO, Local Administrative Units (LAU) 2024, 1:1 million,
© EuroGeographics for the administrative boundaries —
https://gisco-services.ec.europa.eu/distribution/v2/lau/. Europe's LAUs come in
one zipped shapefile (~43 MB); Romania's are its 3,181 UATs, each with its
SIRUTA code (``RO_1017``): 103 municipalities (municipii), 216 towns (orașe)
and 2,862 communes (comune).

Names, counties and types: INS, SIRUTA, the register of Romania's
administrative units, first half of 2026 (data.gov.ro, CC BY 4.0) —
https://data.gov.ro/dataset/siruta_s1-2026. GISCO's names left out many
diacritics (``Oraş Dragomiresti``) and name no county; SIRUTA writes each UAT
in capitals with its diacritics (``ORAŞ DRAGOMIREŞTI``), its county and its
type, and is joined by the SIRUTA code. Its names are written in lower case
with capitals as Romanian writes them (``Câmpulung la Tisa``, ``Valea lui
Mihai``, ``C.A. Rosetti``), and ş, ţ take the comma below. Where the two
differ by more than diacritics, SIRUTA's are the official ones (``Sfântu
Gheorghe`` for GISCO's ``Sfântul Gheorghe``, ``Rimetea`` for ``Râmetea``).

A county is drawn as the union of its UATs, so the two levels' borders
match, and Romania's border as the union of the counties. Both files are
downloaded once and cached in a compact form.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape
from shapely.ops import polylabel

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

import frame
from biomes import _download, _key, on_globe, visible_points
from borders import _pack, _unpack
from cities import label_space, side_offset
from relief import _points_to_data, anchors, visible_units

logger = logging.getLogger(__name__)

LAU_URL = "https://gisco-services.ec.europa.eu/distribution/v2/lau/shp/LAU_RG_01M_2024_4326.shp.zip"
SIRUTA_URL = ("https://data.gov.ro/dataset/721c9059-5f87-4c79-9854-a1d5c18f58d5/resource/"
              "dac903f0-32b5-489a-89e7-2c80d96cf68d/download/siruta_s1_2026.csv")
DATA_VERSION = 1          # bump when the cache changes, so old ones are rebuilt

ADMIN_ATTRIBUTION = ("Counties and UATs: © EuroGeographics for the administrative boundaries (GISCO LAU 2024); "
                     "names: INS, SIRUTA 2026, CC BY 4.0")

LEVELS = ("counties", "uats")
DEFAULT_LEVEL = "counties"
_LEVEL_ALIASES = {"": "counties", "county": "counties", "judete": "counties", "județe": "counties",
                  "judeţe": "counties", "uat": "uats", "communes": "uats", "comune": "uats", "all": "uats"}

# SIRUTA's types of UAT (TIP): municipalities, towns and communes; a county seat is a municipality or town
KINDS = {1: "municipality", 4: "municipality", 9: "municipality", 2: "town", 5: "town", 3: "commune"}
SEATS = frozenset({1, 5, 9})                      # county seats, and Bucharest
CAPITAL = 9                                       # Bucharest
COUNTY_LEVEL, UAT_LEVEL = "1", "2"                # SIRUTA's NIV
# Pastel fills, so that no two neighbouring counties share one
PALETTE = [(246, 214, 170), (200, 226, 180), (196, 214, 238), (242, 200, 212), (234, 228, 164), (216, 202, 234)]
COUNTRY_EDGE = "#4a2b4f"
COUNTY_EDGE = "#6e3f73"
UAT_EDGE = "#9b7fa0"
COUNTY_LABEL_COLOUR = "#4a2b4f"
UAT_LABEL_COLOUR = "#2f2f2f"
COMMUNE_LABEL_COLOUR = "#555555"
# Font sizes (points) of the names of the UATs, by type
UAT_SIZES = {"municipality": 7.0, "town": 6.5, "commune": 5.5}
SEAT_SIZE, CAPITAL_SIZE = 7.5, 9.5                # the names of the county seats and the capital, by counties
# Where to try a county's name (see relief.anchors), and the smallest size it may shrink to for room
COUNTY_ANCHORS = 24
MIN_COUNTY_SIZE = 7.0
# A commune is named only when its name fits inside it: the name's box at most this share of its area on the map
COMMUNE_FILL = 0.8
SLIVER = 1e-4             # holes and parts of a union smaller than this (square degrees, ~1 km²) are gaps

# Words written in lower case inside a name (Câmpulung la Tisa, Ștefan cel Mare)
_PARTICLES = frozenset({"de", "la", "lui", "din", "pe", "cu", "sub", "cel", "și", "lângă", "peste", "între"})
_COMMA_BELOW = str.maketrans("şŞţŢ", "șȘțȚ")
_PREFIX = re.compile(r"^(JUDEȚUL|MUNICIPIUL|ORAȘ)\s+", re.IGNORECASE)


class AdminError(RuntimeError):
    """The counties and UATs could not be downloaded or read."""


class Unit(NamedTuple):
    """A county or a UAT: its name, county (SIRUTA's county number) and outline."""
    name: str
    county: int
    kind: str                       # "county", "municipality", "town" or "commune"
    outline: shapely.Geometry
    label: tuple[float, float]      # (lon, lat) inside it, as far from its edges as can be
    seat: bool = False              # a county seat (or Bucharest)
    population: int = 0             # GISCO's, for which UATs to name first
    capital: bool = False           # Bucharest


class Admin(NamedTuple):
    """The counties, and at the level of UATs the UATs, to draw."""
    level: str                      # "counties" or "uats"
    counties: list[Unit]            # largest first
    uats: list[Unit]                # municipalities, then towns, then communes, the most populous first
    country: shapely.Geometry       # Romania's border, the union of the counties
    colours: dict[int, int]         # county number -> index into PALETTE, no two neighbours alike
    total_uats: int = 0             # how many UATs the data holds, even at the level of counties
    seats: Sequence[Unit] = ()      # the county seats, the capital first, then the most populous


class Shown(NamedTuple):
    """What of the counties and UATs shows on a globe."""
    counties: frozenset[int]        # county numbers
    uats: Counter[str]              # UATs by kind
    border: bool = False            # Romania's border shows: some of the map lies outside it
    seats: int = 0                  # county seats marked (at the level of counties)
    capital: bool = False           # Bucharest among them


def resolve_admin_level(level: str | None) -> str:
    """``"counties"`` or ``"uats"`` for *level* (``None`` or ``""`` for counties). Raises ``ValueError`` otherwise."""
    text = (level or "").strip().lower()
    text = _LEVEL_ALIASES.get(text, text)
    if text not in LEVELS:
        raise ValueError(f"Unknown level of Romania's administrative units {level!r}; choose from {', '.join(LEVELS)}.")
    return text


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def written_name(raw: str) -> str:
    """SIRUTA's *raw* name in capitals as it is written: ``ORAŞ CÂMPULUNG LA TISA`` → ``Câmpulung la Tisa``,
    ``C.A.ROSETTI`` → ``C.A. Rosetti``, without ``JUDEŢUL``, ``MUNICIPIUL`` or ``ORAŞ`` before it."""
    name = re.sub(r"\s+", " ", raw.strip().translate(_COMMA_BELOW))
    name = _PREFIX.sub("", name)
    name = re.sub(r"\.(?=[^\W\d_]{2,})", ". ", name)          # C.A.ROSETTI, I.CUZA
    name = re.sub(r"\s*-\s*", "-", name)

    def cased(match: re.Match[str]) -> str:
        word = match.group(0).lower()
        return word if match.start() and word in _PARTICLES else word[:1].upper() + word[1:]
    return re.sub(r"[^\W\d_]+", cased, name)


def gisco_kind(name: str) -> int:
    """SIRUTA's type for a UAT that GISCO names *name*: a municipality (4) or town (2) by the word before it,
    else a commune (3)."""
    first = _fold(name.split(" ", 1)[0])
    return {"municipiul": 4, "oras": 2}.get(first, 3) if " " in name.strip() else 3


def _fold(text: str) -> str:
    """*text* lower-case without accents, spaces or punctuation."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text.lower())


# ---------------------------------------------------------------------------
# Download and caching
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "ro_admin")


class Entry(NamedTuple):
    """A county or UAT in SIRUTA."""
    name: str
    county: int
    kind: int                       # SIRUTA's TIP


def read_siruta(text: str) -> tuple[dict[int, str], dict[int, Entry]]:
    """The counties (county number -> name) and UATs (SIRUTA code -> entry) of SIRUTA's CSV *text*."""
    counties: dict[int, str] = {}
    uats: dict[int, Entry] = {}
    try:
        for row in csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"):
            level, county = row["NIV"].strip(), int(row["JUD"])
            if level == COUNTY_LEVEL:
                counties[county] = written_name(row["DENLOC"])
            elif level == UAT_LEVEL:
                uats[int(row["SIRUTA"])] = Entry(written_name(row["DENLOC"]), county, int(row["TIP"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AdminError(f"SIRUTA's table is not as expected: {exc!r}") from exc
    if not counties or not uats:
        raise AdminError("SIRUTA's table names no counties or no UATs")
    return counties, uats


def _without_slivers(outline: shapely.Geometry) -> shapely.Geometry:
    """*outline* without the holes and parts smaller than :data:`SLIVER`, the gaps where its pieces meet."""
    parts = [shapely.Polygon(p.exterior, [h for h in p.interiors if shapely.Polygon(h).area >= SLIVER])
             for p in shapely.get_parts(outline) if p.area >= SLIVER]
    if not parts:
        return outline
    return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)


def label_point(outline: shapely.Geometry) -> tuple[float, float]:
    """The point of *outline*'s largest part farthest from its edges (as on the ground, a degree of longitude
    shrinking with the latitude)."""
    largest = max(shapely.get_parts(outline), key=lambda p: p.area)
    shrink = float(np.cos(np.radians(largest.centroid.y)))
    squeezed = shapely.transform(largest, lambda xy: xy * [shrink, 1])
    point = polylabel(squeezed, tolerance=0.002)
    return point.x / shrink, point.y


def county_colours(outlines: dict[int, shapely.Geometry]) -> dict[int, int]:
    """A colour (index into :data:`PALETTE`) for each county, none the same as a neighbour's: the counties with
    the most neighbours first, each the first colour its neighbours left."""
    codes = list(outlines)
    tree = shapely.STRtree([outlines[c] for c in codes])
    neighbours: dict[int, set[int]] = {c: set() for c in codes}
    for i, j in zip(*tree.query(shapely.buffer([outlines[c] for c in codes], 1e-4), predicate="intersects")):
        if i != j:
            neighbours[codes[i]].add(codes[j])
            neighbours[codes[j]].add(codes[i])
    colours: dict[int, int] = {}
    for code in sorted(codes, key=lambda c: (-len(neighbours[c]), c)):
        taken = {colours[n] for n in neighbours[code] if n in colours}
        colours[code] = next((k for k in range(len(PALETTE)) if k not in taken), 0)
    return colours


def read_admin(lau_zip: bytes, siruta: str) -> dict[str, np.ndarray]:
    """The table of Romania's UATs and counties, from GISCO's zipped LAU shapefile and SIRUTA's CSV."""
    counties, entries = read_siruta(siruta)
    with zipfile.ZipFile(io.BytesIO(lau_zip)) as archive:
        members = {os.path.splitext(n)[1].lower(): n for n in archive.namelist()}
        if not {".shp", ".shx", ".dbf"} <= members.keys():
            raise AdminError("GISCO's archive holds no shapefile")
        files = {ext: io.BytesIO(archive.read(members[ext])) for ext in (".shp", ".shx", ".dbf")}
    reader = shapefile.Reader(shp=files[".shp"], shx=files[".shx"], dbf=files[".dbf"], encoding="utf-8")
    codes, names, county_of, kinds, population, outlines = [], [], [], [], [], []
    missing = []
    for i, record in enumerate(reader.iterRecords(fields=["GISCO_ID", "CNTR_CODE", "LAU_NAME", "POP_2024"])):
        if record is None or record["CNTR_CODE"] != "RO":
            continue
        found = reader.shape(i)
        outline = shapely.make_valid(shape(dict(found.__geo_interface__))) if found is not None else None
        if outline is None or outline.is_empty:
            continue
        code = int(str(record["GISCO_ID"]).removeprefix("RO_"))
        entry = entries.get(code)
        if entry is None:
            missing.append(code)
            entry = Entry(written_name(str(record["LAU_NAME"])), 0, gisco_kind(str(record["LAU_NAME"])))
        codes.append(code)
        names.append(entry.name)
        county_of.append(entry.county)
        kinds.append(entry.kind)
        population.append(int(float(str(record["POP_2024"] or 0))))
        outlines.append(outline)
    if not outlines:
        raise AdminError("GISCO's shapefile holds no Romanian UATs")
    if missing:
        # A UAT newer than SIRUTA's table: in the county of the UAT nearest it
        logger.warning("%d UATs are not in SIRUTA (%s); named as GISCO names them.", len(missing),
                       ", ".join(map(str, missing[:5])))
        known = [k for k, c in enumerate(county_of) if c]
        tree = shapely.STRtree([outlines[k] for k in known])
        for k, c in enumerate(county_of):
            if not c:
                county_of[k] = county_of[known[int(tree.nearest(outlines[k].representative_point()))]]
    by_county: defaultdict[int, list[shapely.Geometry]] = defaultdict(list)
    for county, outline in zip(county_of, outlines):
        by_county[county].append(outline)
    county_codes = sorted(by_county)
    county_outlines = [_without_slivers(shapely.union_all(by_county[c], grid_size=1e-6)) for c in county_codes]
    country = _without_slivers(shapely.union_all(county_outlines, grid_size=1e-6))
    colours = county_colours(dict(zip(county_codes, county_outlines)))
    uat_labels = np.array([label_point(o) for o in outlines])
    county_labels = np.array([label_point(o) for o in county_outlines])
    packed_counties, packed_country = _pack(county_outlines), _pack([country])
    return {"codes": np.array(codes, dtype=np.int64), "names": np.array(names), "counties": np.array(county_of),
            "kinds": np.array(kinds), "population": np.array(population, dtype=np.int64),
            "label_lon": uat_labels[:, 0], "label_lat": uat_labels[:, 1], **_pack(outlines),
            "county_codes": np.array(county_codes),
            "county_names": np.array([counties.get(c, "") for c in county_codes]),
            "county_colours": np.array([colours[c] for c in county_codes]),
            "county_label_lon": county_labels[:, 0], "county_label_lat": county_labels[:, 1],
            "county_wkb": packed_counties["wkb"], "county_wkb_ends": np.asarray(packed_counties["wkb_ends"]),
            "country_wkb": packed_country["wkb"], "country_wkb_ends": np.asarray(packed_country["wkb_ends"])}


def ensure_admin_data(cache_dir: str | None = None) -> str:
    """Path to the cached counties and UATs, downloading and reading them if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"ro_admin_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached counties and UATs of Romania: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Downloading Romania's UATs from GISCO and their names from SIRUTA (one-time, ~44 MB) …")
    lau, siruta = os.path.join(cache_dir, "lau.download"), os.path.join(cache_dir, "siruta.download")
    try:
        _download(SIRUTA_URL, siruta, label="SIRUTA")
        _download(LAU_URL, lau, timeout=300, label="GISCO LAU 2024")
        with open(lau, "rb") as fh, open(siruta, "rb") as table_fh:
            table = read_admin(fh.read(), table_fh.read().decode("utf-8-sig"))
    except AdminError:
        raise
    except Exception as exc:                  # network, archive, encoding and geometry errors alike
        raise AdminError(f"Could not read Romania's counties and UATs: {exc}") from exc
    finally:
        for download in (lau, siruta):
            if os.path.exists(download):
                os.remove(download)
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    logger.info("Counties and UATs ready: %s", path)
    return path


def _uat_order(unit: Unit) -> tuple[int, int, int]:
    return ({"municipality": 0, "town": 1}.get(unit.kind, 2), not unit.seat, -unit.population)


def load_admin(level: str = DEFAULT_LEVEL, cache_dir: str | None = None) -> Admin:
    """Romania's counties, and at *level* ``"uats"`` its UATs too."""
    with np.load(ensure_admin_data(cache_dir)) as data:
        table = {key: data[key] for key in data.files}
    county_outlines = _unpack({"wkb": table["county_wkb"], "wkb_ends": table["county_wkb_ends"]})
    country = _unpack({"wkb": table["country_wkb"], "wkb_ends": table["country_wkb_ends"]})[0]
    counties = [Unit(str(n), int(c), "county", o, (float(x), float(y)))
                for n, c, o, x, y in zip(table["county_names"], table["county_codes"], county_outlines,
                                         table["county_label_lon"], table["county_label_lat"])]
    counties.sort(key=lambda u: float(shapely.area(u.outline)), reverse=True)
    colours = {int(c): int(k) for c, k in zip(table["county_codes"], table["county_colours"])}
    every = [Unit(str(n), int(c), KINDS.get(int(k), "commune"), o, (float(x), float(y)), int(k) in SEATS, int(p),
                  int(k) == CAPITAL)
             for n, c, k, o, x, y, p in zip(table["names"], table["counties"], table["kinds"], _unpack(table),
                                            table["label_lon"], table["label_lat"], table["population"])]
    seats = sorted((u for u in every if u.seat), key=lambda u: (not u.capital, -u.population))
    uats = sorted(every, key=_uat_order) if level == "uats" else []
    return Admin(level, counties, uats, country, colours, len(table["codes"]), seats)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def county_label_size(share: float) -> float:
    """Font size (points) of the name of a county covering *share* of the map."""
    return float(np.clip(7 + 30 * np.sqrt(share), 7, 13))


def _fill_colour(index: int) -> tuple[float, float, float]:
    r, g, b = (v / 255 for v in PALETTE[index % len(PALETTE)])
    return r, g, b


def _area_on_map(ax: GeoAxes, unit: Unit, x: float, y: float) -> float:
    """*unit*'s area on *ax* in square pixels, near (*x*, *y*), its label point in map coordinates."""
    lon, lat = unit.label
    step = 0.01
    around = ax.projection.transform_points(ccrs.PlateCarree(), np.array([lon + step, lon]),
                                            np.array([lat, lat + step]))[:, :2]
    pixels = ax.transData.transform(np.vstack([[x, y], around]))
    across = np.hypot(*(pixels[1] - pixels[0])) / step         # pixels per degree, east and north
    up = np.hypot(*(pixels[2] - pixels[0])) / step
    return float(shapely.area(unit.outline) * across * up)


def draw_admin(ax: GeoAxes, admin: Admin, alpha: float = 0.0, labels: bool = True, samples: int = 300) -> Shown:
    """Draw Romania's border, its counties' and (at the level of UATs) its UATs' on *ax*, and name them.

    With *alpha*, the counties are filled in pastel colours, no two
    neighbours alike. At the level of counties, the county seats are marked
    with a dot and named first, Bucharest with a ringed dot. County names go in
    capitals in the middle of what shows of each, largest first; at the level
    of UATs, the UATs' names follow, municipalities (county seats in bold),
    towns, and communes in italic where the name fits inside the commune. Each
    goes in the room the other layers' names leave (see
    :func:`cities.label_space`) and not where a layer already wrote it (a city).
    """
    if alpha > 0:
        for index in sorted(set(admin.colours.values())):
            fills = [c.outline for c in admin.counties if admin.colours.get(c.county) == index]
            ax.add_geometries(fills, crs=ccrs.PlateCarree(), facecolor=(*_fill_colour(index), alpha),
                              edgecolor="none", zorder=5.05)
    if admin.uats:
        ax.add_geometries([u.outline for u in admin.uats], crs=ccrs.PlateCarree(), facecolor="none",
                          edgecolor=UAT_EDGE, linewidth=0.25, zorder=5.7)
    ax.add_geometries([c.outline for c in admin.counties], crs=ccrs.PlateCarree(), facecolor="none",
                      edgecolor=COUNTY_EDGE, linewidth=0.8, zorder=5.75)
    ax.add_geometries([admin.country], crs=ccrs.PlateCarree(), facecolor="none", edgecolor=COUNTRY_EDGE,
                      linewidth=1.4, zorder=5.8)

    found = visible_units(ax, admin.counties, samples)
    lon, lat, x, y = visible_points(ax, samples)
    uats_in_view = [u for u in admin.uats if on_globe(ax, *u.label, margin=1.0)]
    shown = Shown(frozenset(admin.counties[i].county for i in found), Counter(u.kind for u in uats_in_view),
                  bool(found) and sum(len(points) for points in found.values()) < len(lon))
    if not labels or not found:
        return shown
    # What other layers wrote (a city's name), before the counties' names, which a seat may share (Vaslui)
    written = {_fold(t.get_text()) for t in ax.texts}
    total = frame.coverage(ax) * samples ** 2
    space = label_space(ax)
    marked, capital = 0, False
    if admin.level == "counties":
        for seat in admin.seats:
            if not on_globe(ax, *seat.label, margin=1.0) or _fold(seat.name) in written:
                continue
            size = CAPITAL_SIZE if seat.capital else SEAT_SIZE
            px, py = ax.projection.transform_point(*seat.label, ccrs.PlateCarree())
            side = space.claim_around(px, py, seat.name, size, bold=seat.capital)
            if side is None:
                continue
            marked += 1
            capital = capital or seat.capital
            _mark_seat(ax, px, py, size, seat.capital)
            ax.annotate(seat.name, (px, py), xycoords="data", textcoords="offset points", **side_offset(side, size),
                        fontsize=size, fontweight="bold" if seat.capital else "normal", color=UAT_LABEL_COLOUR,
                        zorder=8, annotation_clip=True)
        shown = shown._replace(seats=marked, capital=capital)
    # The capital, a county of its own, is named as the capital where the seats are marked
    by_seat = {s.county for s in admin.seats if s.capital} if admin.level == "counties" else set()
    named = 0
    for i, points in sorted(found.items(), key=lambda item: -len(item[1])):
        county = admin.counties[i]
        if county.county in by_seat:
            continue
        text = county.name.upper()
        tries = anchors(county, lon[points], lat[points], COUNTY_ANCHORS)
        largest = county_label_size(len(points) / total)
        # At its size, else smaller where the seats' names leave less room
        place = next(((float(x[points[k]]), float(y[points[k]]) - _points_to_data(ax, 0.85 * size), size)
                      for size in dict.fromkeys([largest, max(MIN_COUNTY_SIZE, 0.8 * largest)])
                      for k in tries
                      if space.claim(float(x[points[k]]), float(y[points[k]]) - _points_to_data(ax, 0.85 * size),
                                     text, size, point=0, bold=True)), None)
        if place is None:
            continue
        at_x, at_y, size = place
        named += 1
        ax.annotate(text, (at_x, at_y), xycoords="data", xytext=(0, 0.25 * size), textcoords="offset points",
                    fontsize=size, fontweight="bold", color=COUNTY_LABEL_COLOUR, ha="center", va="bottom",
                    zorder=7.85, annotation_clip=True)

    alone = {c for c, n in Counter(u.county for u in admin.uats).items() if n == 1}    # Bucharest, its own county
    uats_named = 0
    for uat in uats_in_view:
        if not uat.name or uat.county in alone or _fold(uat.name) in written:
            continue
        size = UAT_SIZES[uat.kind] + (0.5 if uat.seat else 0)
        px, py = ax.projection.transform_point(*uat.label, ccrs.PlateCarree())
        if uat.kind == "commune":
            width, height = 0.6 * size * len(uat.name), 1.2 * size
            if width * height * (ax.figure.dpi / 72) ** 2 > COMMUNE_FILL * _area_on_map(ax, uat, px, py):
                continue
        lift = _points_to_data(ax, 0.85 * size)
        if not space.claim(px, py - lift, uat.name, size, point=0, bold=uat.seat):
            continue
        uats_named += 1
        written.add(_fold(uat.name))
        ax.annotate(uat.name, (px, py - lift), xycoords="data", xytext=(0, 0.25 * size), textcoords="offset points",
                    fontsize=size, fontweight="bold" if uat.seat else "normal",
                    fontstyle="italic" if uat.kind == "commune" else "normal",
                    color=COMMUNE_LABEL_COLOUR if uat.kind == "commune" else UAT_LABEL_COLOUR,
                    ha="center", va="bottom", zorder=7.8, annotation_clip=True)
    logger.info("Counties of Romania drawn: %d in view, %d named, %d seats marked; UATs: %d in view, %d named.",
                len(found), named, marked, len(uats_in_view), uats_named)
    return shown


def _mark_seat(ax: GeoAxes, x: float, y: float, size: float, capital: bool) -> None:
    """A county seat's dot at (*x*, *y*), or the capital's, a dot in a ring."""
    ax.plot(x, y, "o", transform=ax.projection, markersize=0.5 * size, markerfacecolor="white",
            markeredgecolor=COUNTY_LABEL_COLOUR, markeredgewidth=0.9, zorder=8)
    if capital:
        ax.plot(x, y, "o", transform=ax.projection, markersize=0.22 * size, markerfacecolor=COUNTY_LABEL_COLOUR,
                markeredgecolor="none", zorder=8.05)


def _seat_handle(capital: bool) -> Line2D:
    size = CAPITAL_SIZE if capital else SEAT_SIZE
    return Line2D([], [], linestyle="none", marker="o", markersize=0.5 * size, markerfacecolor="white",
                  markeredgecolor=COUNTY_LABEL_COLOUR, markeredgewidth=0.9 if not capital else 2.2)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def add_admin_legend(ax: GeoAxes, admin: Admin, shown: Sequence[Shown], alpha: float = 0.0,
                     y: float = -0.01, x: float = 0.5) -> Legend | None:
    """A key to the borders drawn, with how many counties and UATs show. ``None`` when none shows."""
    counties = frozenset().union(*(s.counties for s in shown))
    if not counties:
        return None
    uats: Counter[str] = Counter()
    for s in shown:
        uats.update(s.uats)
    handles: list[Line2D | mpatches.Patch] = []
    labels: list[str] = []
    if any(s.border for s in shown):
        handles.append(Line2D([], [], color=COUNTRY_EDGE, linewidth=1.4))
        labels.append("Border of Romania")
    handles.append(Line2D([], [], color=COUNTY_EDGE, linewidth=0.8))
    labels.append("County border (județ)")
    if admin.level == "uats":
        handles.append(Line2D([], [], color=UAT_EDGE, linewidth=0.6))
        labels.append("Border of a municipality, town or commune (UAT)")
    if alpha > 0:
        handles.append(mpatches.Patch(facecolor=(*_fill_colour(0), alpha), edgecolor=COUNTY_EDGE, linewidth=0.6))
        labels.append("Counties told apart by colour")
    if any(s.capital for s in shown):
        handles.append(_seat_handle(capital=True))
        labels.append("Capital")
    if any(s.seats - s.capital for s in shown):
        handles.append(_seat_handle(capital=False))
        labels.append("County seat")
    bucharest = any(c.name == "București" and c.county in counties for c in admin.counties)
    title = (f"Counties of Romania in view: {_plural(len(counties) - bucharest, 'county', 'counties')}"
             + (" and the municipality of Bucharest" if bucharest else ""))
    if admin.level == "uats":
        title += (f"\n{sum(uats.values()):,} of its {admin.total_uats:,} UATs: "
                  f"{_plural(uats['municipality'], 'municipality', 'municipalities')}, "
                  f"{_plural(uats['town'], 'town', 'towns')}, {_plural(uats['commune'], 'commune', 'communes')}"
                  "\nnamed: COUNTIES, municipalities (county seats in bold), towns, communes in italic")
    title += "\nGISCO LAU 2024, names from SIRUTA 2026 (INS)"
    return _key(ax, handles, labels, title, min(len(handles), 3), x, y)
