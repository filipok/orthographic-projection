"""Big cities: urban centres (metropolitan built-up areas) with their names, from the GHSL.

Data source
-----------
GHS Urban Centre Database 2025 (GHS-UCDB R2024A, version 1.2), multi-temporal
urban centres: European Commission, Joint Research Centre (JRC).
https://human-settlement.emergency.copernicus.eu/ghs_ucdb_2024.php
doi:10.2905/1a338be6-7eaf-480c-9664-3a8ade88cbcd — CC BY 4.0.

An urban centre is a cluster of contiguous 1 km cells of at least 1,500 people
per km² and 50,000 people in all: a metropolitan area as it is built, across
city limits ("Guangzhou" is the whole Pearl River Delta). The database names
11,686 of them and gives their extent and population every five years from
1975 to 2030 (2030 projected). The 35 MB file is downloaded once into
``~/.cache/ortho_tiles/cities/``; each year's outlines are turned from
Mollweide metres into degrees, simplified to about 200 m, and cached.

The database lost every letter outside Latin-1 from its names (``Gala?i``,
``Klaip?da``, whole names in other scripts as ``?????``). They are restored
from GeoNames (https://www.geonames.org, cities of 5,000 people and more,
CC BY 4.0): a name is matched against the places near its centre, each ``?``
standing for one such letter (``Galați``); a name lost entirely takes the
romanised name of the place it matches, or of the nearest place within
~10 km. Names that cannot be restored are left out of the labels, and names
GHSL spelt without their accents take them (``Brasov``, ``Brașov``). The 6 MB
GeoNames file is downloaded once and the restored names cached.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import unicodedata
import zipfile
from typing import NamedTuple

import numpy as np
import shapely

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from biomes import _download, _key, on_globe
from iucn import parse_geopackage_geometry, project

logger = logging.getLogger(__name__)

GHSL_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_UCDB_GLOBE_R2024A/"
            "GHS_UCDB_MTUC_GLOBE_R2024A/V1-2/GHS_UCDB_MTUC_GLOBE_R2024A_V1_2.zip")
GPKG_NAME = "GHS_UCDB_MTUC_GLOBE_R2024A.gpkg"
CENTRES_TABLE = "GHSL_UCDB_MTUC_GLOBE_R2024"
OUTLINE_TABLE = "GHSL_UCDB_MTUC_{year}_GLOBE_R2024"
DATA_VERSION = 1
GEONAMES_URL = "https://download.geonames.org/export/dump/cities5000.zip"
NAMES_VERSION = 1
NEAR_DEGREES = 0.5                       # how far from a centre to look for its name in GeoNames
NEAREST_DEGREES = 0.1                    # a name lost entirely takes the nearest place's this close
YEARS = tuple(range(1975, 2031, 5))
DEFAULT_YEAR = 2025
PROJECTED_FROM = 2030                    # years from this one on are GHSL's projections
SMALLEST = 50_000                        # GHSL's smallest urban centre
SIMPLIFY_DEGREES = 0.002                 # ~200 m
CITY_COLOUR = "#b2182b"
LABEL_MAX = 80

CITIES_ATTRIBUTION = ("Urban centres: GHS-UCDB R2024A (European Commission, JRC), CC BY 4.0; "
                      "names restored from GeoNames, CC BY 4.0")


class CitiesDataError(RuntimeError):
    """The GHSL urban centres could not be downloaded or read."""


class Cities(NamedTuple):
    year: int
    names: list[str]          # main name of each centre
    countries: list[str]
    lon: np.ndarray           # where to name each centre
    lat: np.ndarray
    population: np.ndarray    # people in each centre in *year*
    outlines: list            # each centre's built-up extent in *year* (degrees); None if it was not one then


def resolve_cities_year(year: int | str | None) -> int:
    """The year of urban centres to map: 1975-2030 in steps of five. Raises ``ValueError``."""
    if year is None or str(year).strip() == "":
        return DEFAULT_YEAR
    try:
        value = int(str(year).strip())
    except ValueError:
        value = None
    if value not in YEARS:
        raise ValueError(f"Cities are mapped for {YEARS[0]}-{YEARS[-1]} every five years "
                         f"({', '.join(map(str, YEARS))}), not {year!r}.")
    return value


def default_min_population(radius_km: float | None) -> int:
    """The smallest centre worth showing: a million on a whole globe, fewer people on a closer map."""
    if radius_km is None:
        return 1_000_000
    return int(np.clip(1_000_000 * (radius_km / 5000) ** 2, SMALLEST, 1_000_000) // 10_000 * 10_000)


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "cities")


def ensure_cities_data(cache_dir: str | None = None) -> str:
    """Path to the GHSL GeoPackage, downloading the 35 MB zip once. Raises :class:`CitiesDataError`."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, GPKG_NAME)
    if os.path.isfile(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    zip_path = os.path.join(cache_dir, "ghs_ucdb_mtuc.zip")
    try:
        if not os.path.isfile(zip_path):
            logger.info("Downloading the GHSL urban centres (one-time, ~35 MB) …")
            _download(GHSL_URL, zip_path, label="urban centres")
        with zipfile.ZipFile(zip_path) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith(GPKG_NAME.lower()))
            with archive.open(member) as src, open(path + ".part", "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
        os.replace(path + ".part", path)
    except Exception as exc:                  # network, zip and file errors alike
        raise CitiesDataError(f"Could not get the GHSL urban centres: {exc}") from exc
    os.remove(zip_path)
    return path


def _to_degrees(geometry: shapely.Geometry) -> shapely.Geometry:
    """*geometry* from Mollweide metres (ESRI:54009, GHSL's frame) to longitude and latitude."""
    from pyproj import Transformer

    to_lonlat = Transformer.from_crs("ESRI:54009", "EPSG:4326", always_xy=True)
    return shapely.transform(geometry, lambda xy: np.column_stack(to_lonlat.transform(xy[:, 0], xy[:, 1])))


def _build_centres(db: sqlite3.Connection) -> dict[str, np.ndarray]:
    pops = ", ".join(f"MT_POP_TOT_{y}" for y in YEARS)
    rows = db.execute(f'SELECT ID_MTUC_G0, GC_UCN_MAI_2025, GC_CNT_GAD_2025, geom, {pops} FROM "{CENTRES_TABLE}"'
                      ).fetchall()
    points = [parse_geopackage_geometry(r[3]) for r in rows]
    centre = shapely.points([(p.x, p.y) if p is not None else (np.nan, np.nan) for p in points])
    lonlat = shapely.get_coordinates(_to_degrees(centre), include_z=False)
    return {
        "ids": np.array([r[0] for r in rows], np.int64),
        "names": np.array([(r[1] or "").strip() for r in rows]),
        "countries": np.array([(r[2] or "").strip() for r in rows]),
        "lon": lonlat[:, 0], "lat": lonlat[:, 1],
        "population": np.array([[v or 0 for v in r[4:]] for r in rows], float),
    }


def _build_outlines(db: sqlite3.Connection, year: int, now: dict[int, shapely.Geometry] | None = None
                    ) -> dict[str, np.ndarray]:
    """Each centre's outlines in *year*, by the id of the centre it is in 2025.

    A town that later grew into a bigger centre has its own id in an earlier
    year; it goes to the 2025 centre (*now*, by id) it lies in, so a 1975 map
    shows the separate towns that became the Pearl River Delta. Outlines in no
    2025 centre (new ones of 2030) are left out.
    """
    rows = db.execute(f'SELECT ID_UC_G0, geom FROM "{OUTLINE_TABLE.format(year=year)}"').fetchall()
    parts: dict[int, list] = {}
    known = set(now) if now is not None else None
    tree = shapely.STRtree(list(now.values())) if now else None
    now_ids = list(now) if now else []
    for centre_id, blob in rows:
        geometry = parse_geopackage_geometry(blob)
        if geometry is None:
            continue
        geometry = shapely.make_valid(shapely.simplify(_to_degrees(geometry), SIMPLIFY_DEGREES))
        if known is not None and centre_id not in known:
            hits = tree.query(geometry.representative_point(), predicate="within") if tree is not None else []
            if not len(hits):
                continue
            centre_id = now_ids[int(hits[0])]
        parts.setdefault(int(centre_id), []).append(geometry)
    ids = sorted(parts)
    wkb = [bytes(shapely.to_wkb(shapely.union_all(parts[i]) if len(parts[i]) > 1 else parts[i][0])) for i in ids]
    return {"ids": np.array(ids, np.int64), "wkb": np.frombuffer(b"".join(wkb), np.uint8),
            "ends": np.cumsum([len(w) for w in wkb])}


def _read_outlines(saved: dict[str, np.ndarray]) -> dict[int, shapely.Geometry]:
    blob, ends = saved["wkb"].tobytes(), saved["ends"]
    return {int(i): shapely.from_wkb(blob[a:b]) for i, a, b in zip(saved["ids"], [0, *ends[:-1]], ends)}


def _fold(name: str) -> str:
    """*name* composed (NFC), with runs of spaces and hyphens as one space, for comparing names."""
    return re.sub(r"[\s\-\u2010]+", " ", unicodedata.normalize("NFC", name)).strip()


# Romanian and Moldovan s and t take a comma below, which GeoNames often writes as a cedilla
_COMMA_BELOW = str.maketrans("şŞţŢ", "șȘțȚ")
# Marks of scholarly transliteration (Kolhāpur, Ţarţūs, Al Ḩasakah), not of how a place's name is written
_TRANSLITERATION = set("āēīōūĀĒĪŌŪḩḨḑḐḍḌṭṬṯṮẕẔẓẒṣṢḥḤ‘’ʻʼ")


# Countries whose names are written in another script: GeoNames' accents there are a transliteration's
# (Chóngfú, Wŏnsan, Umm Qaşr), so GHSL's plain spelling stays
OTHER_SCRIPTS = frozenset((
    "Afghanistan", "Armenia", "Bahrain", "Bangladesh", "Belarus", "Bhutan", "Bulgaria", "Cambodia", "China",
    "Cyprus", "Egypt", "Eritrea", "Ethiopia", "Georgia", "Greece", "India", "Iran", "Iraq", "Israel", "Japan",
    "Jordan", "Kazakhstan", "Kuwait", "Kyrgyzstan", "Laos", "Lebanon", "Libya", "Maldives", "Mongolia", "Myanmar",
    "Nepal", "North Korea", "North Macedonia", "Oman", "Pakistan", "Palestine", "Qatar", "Russia", "Saudi Arabia",
    "South Korea", "Sri Lanka", "Sudan", "Syria", "Taiwan", "Tajikistan", "Thailand", "Ukraine",
    "United Arab Emirates", "Uzbekistan", "Yemen",
))


def _transliterated(name: str, cedilla: bool = True) -> bool:
    """Whether *name* is spelt in a scholarly transliteration (also with a t cedilla, if *cedilla*)."""
    return any(c in _TRANSLITERATION or (cedilla and c in "ţŢ") for c in name)


def repair_names(names: list[str], countries: list[str], lon: np.ndarray, lat: np.ndarray,
                 places: list[list[str]]) -> list[str]:
    """*names* with the letters GHSL lost (each a ``?``) restored from the GeoNames *places* near each centre.

    *places* are GeoNames rows (name, ASCII name, alternate names, lat, lon).
    A name keeping some Latin letters is matched against each nearby place's
    names, nearest first, a ``?`` standing for one letter outside Latin-1;
    failing that against its ASCII name, a ``?`` standing for at most one
    letter (``Gwadar``). A name lost entirely takes the romanised name of the
    place it matches, or of the nearest place within :data:`NEAREST_DEGREES`.
    Names that cannot be restored become ``""``. A name GHSL spelt without
    its accents takes them from a nearby place spelt the same (``Brasov``
    becomes ``Brașov``).
    """
    out = list(names)
    if not places:
        return [n if "?" not in n else "" for n in out]
    plat = np.array([float(p[3]) for p in places])
    plon = np.array([float(p[4]) for p in places])
    for i, raw in enumerate(names):
        if not raw or ("?" not in raw and not raw.isascii()):
            continue
        box = np.flatnonzero((np.abs(plat - lat[i]) < NEAR_DEGREES) & (np.abs(plon - lon[i]) < 2 * NEAR_DEGREES))
        dist = np.hypot((plon[box] - lon[i]) * np.cos(np.radians(lat[i])), plat[box] - lat[i])
        near = box[np.argsort(dist)][np.sort(dist) < NEAR_DEGREES]
        if "?" not in raw:
            if countries[i] in OTHER_SCRIPTS:
                continue
            plain = _fold(raw).casefold()
            local = countries[i] in ("Romania", "Moldova")
            accented = next((places[j][0] for j in near if not places[j][0].isascii()
                             and _fold(places[j][1]).casefold() == plain
                             and not _transliterated(places[j][0], cedilla=not local)), None)
            if accented is not None:
                out[i] = accented.translate(_COMMA_BELOW) if countries[i] in ("Romania", "Moldova") else accented
            continue
        name = _fold(raw)
        latin = bool(re.search(r"[A-Za-z\u00c0-\u00ff]", name))
        lost = re.compile(re.escape(name).replace(r"\?", r"[^\x00-\xff]"))
        found = ""
        for j in near:
            forms = [places[j][0], *(a for a in places[j][2].split(",") if a)]
            hit = next((f for f in forms if lost.fullmatch(_fold(f))), None)
            if hit is not None:
                found = hit if latin else places[j][0]
                break
        if not found and latin:
            loose = re.compile(re.escape(name).replace(r"\?", "[A-Za-z]?"), re.IGNORECASE)
            found = next((places[j][1] for j in near if loose.fullmatch(_fold(places[j][1]))), "")
        if not found and not latin and len(near) and np.min(dist) < NEAREST_DEGREES:
            found = places[near[0]][0]
        if countries[i] in ("Romania", "Moldova"):
            found = found.translate(_COMMA_BELOW)
        out[i] = found
    return out


def _geonames(cache_dir: str) -> list[list[str]]:
    """GeoNames' places of 5,000 people and more: (name, ASCII name, alternate names, lat, lon) each."""
    zip_path = os.path.join(cache_dir, "geonames_cities5000.zip")
    if not os.path.isfile(zip_path):
        logger.info("Downloading GeoNames' place names (one-time, ~6 MB) …")
        _download(GEONAMES_URL, zip_path, label="place names")
    with zipfile.ZipFile(zip_path) as archive:
        text = archive.read("cities5000.txt").decode("utf-8")
    rows = [line.split("\t") for line in text.splitlines()]
    return [[r[1], r[2], r[3], r[4], r[5]] for r in rows if len(r) > 5]


def _restored_names(centres: dict[str, np.ndarray], cache_dir: str) -> list[str]:
    """The centres' names with their lost letters restored (see :func:`repair_names`), cached once made.

    Without GeoNames (offline), names that lost letters are left out of the labels for now.
    """
    names = centres["names"].tolist()
    path = os.path.join(cache_dir, f"names_v{NAMES_VERSION}.npz")
    try:
        with np.load(path) as saved:
            if len(saved["names"]) == len(names):
                return saved["names"].tolist()
    except (OSError, ValueError, KeyError):
        pass
    try:
        places = _geonames(cache_dir)
    except Exception as exc:                  # network, zip and file errors alike
        logger.warning("Could not restore the city names GHSL garbled (%s); leaving them unnamed.", exc)
        return [n if "?" not in n else "" for n in names]
    fixed = repair_names(names, centres["countries"].tolist(), centres["lon"], centres["lat"], places)
    np.savez_compressed(path + ".part.npz", names=np.array(fixed))
    os.replace(path + ".part.npz", path)
    try:
        os.remove(os.path.join(cache_dir, "geonames_cities5000.zip"))
    except OSError:
        pass
    return fixed


def _cached(path: str, build) -> dict[str, np.ndarray]:
    if not os.path.isfile(path):
        data = build()
        np.savez_compressed(path + ".part.npz", **data)
        os.replace(path + ".part.npz", path)
    with np.load(path) as saved:
        return {k: saved[k] for k in saved.files}


def load_cities(year: int = DEFAULT_YEAR, cache_dir: str | None = None) -> Cities:
    """The urban centres of *year*, with their names, populations and outlines. Raises :class:`CitiesDataError`."""
    year = resolve_cities_year(year)
    cache_dir = cache_dir or _default_cache_dir()
    gpkg = ensure_cities_data(cache_dir)
    try:
        with sqlite3.connect(f"file:{os.path.abspath(gpkg)}?mode=ro", uri=True) as db:
            centres = _cached(os.path.join(cache_dir, f"centres_v{DATA_VERSION}.npz"), lambda: _build_centres(db))

            def outlines_of(when: int) -> dict[int, shapely.Geometry]:
                now = None if when == DEFAULT_YEAR else outlines_of(DEFAULT_YEAR)
                return _read_outlines(_cached(os.path.join(cache_dir, f"outlines_{when}_v{DATA_VERSION}.npz"),
                                              lambda: _build_outlines(db, when, now)))

            shapes = outlines_of(year)
        names = _restored_names(centres, cache_dir)
    except CitiesDataError:
        raise
    except Exception as exc:                  # SQLite, geometry and file errors alike
        raise CitiesDataError(f"Could not read the GHSL urban centres: {exc}") from exc
    return Cities(year, names, centres["countries"].tolist(), centres["lon"], centres["lat"],
                  centres["population"][:, YEARS.index(year)], [shapes.get(int(i)) for i in centres["ids"]])


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def label_size(population: float) -> float:
    """Font size (points) of a centre's name: 7 for a million people, 13 for 30 million and more."""
    return float(np.clip(7 + 1.8 * np.log2(max(population, 1) / 1e6), 6.5, 13))


def anchor(cities: Cities, i: int) -> tuple[float, float]:
    """Where to name centre *i*: GHSL's point for it, or, if that lies outside the centre's extent in the map's
    year (the 2025 centre's middle, on a 1975 map), inside its largest built-up patch."""
    lon, lat = float(cities.lon[i]), float(cities.lat[i])
    outline = cities.outlines[i]
    if outline is None or shapely.intersects_xy(outline, lon, lat):
        return lon, lat
    largest = max(shapely.get_parts(outline), key=lambda p: p.area)
    point = largest.representative_point()
    return point.x, point.y


class LabelSpace:
    """Room for names on a globe: a name above a point fits if it stays on the disc and clear of every name and
    point already placed (the sizes of names are estimated from their length)."""

    def __init__(self, ax: GeoAxes):
        self.to_pixels = ax.transData
        self.dpi = ax.figure.dpi
        (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
        (cx0, cy0), (cx1, cy1) = self.to_pixels.transform([(x0, y0), (x1, y1)])
        self.centre, self.radius = ((cx0 + cx1) / 2, (cy0 + cy1) / 2), abs(cx1 - cx0) / 2
        self.placed: list[tuple[float, float, float, float]] = []

    def claim(self, x: float, y: float, text: str, size: float, point: float | None = None) -> bool:
        """Whether *text* in *size* points fits above (*x*, *y*) (map coordinates); if so it is placed, with the
        point under it (*point* pixels across; about a quarter of the type size by default)."""
        px, py = self.to_pixels.transform((x, y))
        gap = 0.25 * size * self.dpi / 72
        half_w = 0.3 * size * len(text) * self.dpi / 72
        box = (px - half_w, py + gap, px + half_w, py + gap + 1.2 * size * self.dpi / 72)
        corners = [(box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3])]
        if any((cx - self.centre[0]) ** 2 + (cy - self.centre[1]) ** 2 > (0.97 * self.radius) ** 2
               for cx, cy in corners):
            return False
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in self.placed):
            return False
        half = (point / 2) if point is not None else gap
        self.placed += [box, (px - half, py - half, px + half, py + half)]
        return True


def label_space(ax: GeoAxes) -> LabelSpace:
    """The :class:`LabelSpace` every layer naming places on *ax* shares, so their names never overlap."""
    space = getattr(ax, "_ortho_label_space", None)
    if space is None:
        space = LabelSpace(ax)
        setattr(ax, "_ortho_label_space", space)
    return space


def draw_cities(ax: GeoAxes, cities: Cities, min_population: int, alpha: float = 0.6,
                labels: bool = True, limit: int = LABEL_MAX) -> list[int]:
    """Fill the built-up extent of each centre of at least *min_population* on *ax* and name the largest.

    Names go biggest first, each above a dot at the centre; one that would
    overlap a name already placed, or run off the globe, is left out. Returns
    the centres that show (index into *cities*), biggest first.
    """
    order = np.argsort(-cities.population)
    shown = [int(i) for i in order if cities.population[i] >= min_population
             and on_globe(ax, float(cities.lon[i]), float(cities.lat[i]), margin=1.0)]
    pieces = [p for i in shown if cities.outlines[i] is not None for p in project(ax, cities.outlines[i])]
    if pieces:
        ax.add_geometries(pieces, crs=ax.projection, facecolor=CITY_COLOUR, edgecolor=CITY_COLOUR,
                          linewidth=0.4, alpha=alpha, zorder=7.3)
    if not labels:
        return shown
    space = label_space(ax)
    named = 0
    for i in shown:
        if named >= limit:
            break
        lon, lat = anchor(cities, i)
        x, y = ax.projection.transform_point(lon, lat, ccrs.PlateCarree())
        size = label_size(cities.population[i])
        if not cities.names[i] or not space.claim(x, y, cities.names[i], size):
            continue
        named += 1
        ax.plot(x, y, "o", transform=ax.projection, markersize=0.45 * size, markerfacecolor="white",
                markeredgecolor="#222222", markeredgewidth=0.8, zorder=8)
        ax.annotate(cities.names[i], (x, y), xycoords="data", xytext=(0, 0.35 * size), textcoords="offset points",
                    fontsize=size, fontweight="bold" if cities.population[i] >= 10e6 else "normal",
                    color="#222222", ha="center", va="bottom", zorder=8, annotation_clip=True,
                    path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
    logger.info("Urban centres drawn: %d of over %s people, %d named.", len(shown), f"{min_population:,}", named)
    return shown


def _people(n: float) -> str:
    return f"{n / 1e6:g} million" if n >= 1e6 else f"{n:,.0f}"


def add_cities_legend(ax: GeoAxes, cities: Cities, min_population: int, shown: int,
                      y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the urban areas and the named centres."""
    when = f"{cities.year} (projected)" if cities.year >= PROJECTED_FROM else str(cities.year)
    handles = [mpatches.Patch(facecolor=CITY_COLOUR, edgecolor=CITY_COLOUR, alpha=0.6),
               Line2D([], [], linestyle="none", marker="o", markersize=5, markerfacecolor="white",
                      markeredgecolor="#222222")]
    labels = ["Built-up urban centre", "Largest, named"]
    title = (f"Urban centres of over {_people(min_population)} people in {when}: "
             f"{shown:,} in view (GHS Urban Centre Database 2025)")
    return _key(ax, handles, labels, title, 2, x, y)
