"""A map drawn from OpenStreetMap's data instead of its tiles: land, lakes, rivers, borders and places,
named in type sized for the page.

OSM's tiles are drawn for screens, one tile pixel to a screen pixel. Shrunk
onto a printed page, a map of Romania at zoom 9 puts about 700 tile pixels
in an inch, and the names in them come out 1-2 points high. This provider
draws the same features as lines and fills, and names them as the other
layers do (see :func:`cities.label_space`): at a set size in points, clear
of one another.

Data sources
------------
Lakes, rivers and places: OpenStreetMap, © OpenStreetMap contributors
(ODbL), from Geofabrik's free shapefiles of Romania and Moldova —
https://download.geofabrik.de/europe/romania.html. Each archive is
~200-700 MB, mostly buildings, roads and land use; only the places, the
waterways and the water areas are read from it (~50 MB for Romania), with
HTTP range requests. A river is the ways of one name that join up; a name
in two languages (``Dunărea - Дунав``, ``Dunav/Dunărea``) keeps its
Romanian part, and ``Râul Mureș`` is ``Mureș`` where OSM names other parts
of it so. Names in another script only (the Serbian and Bulgarian rivers,
Transnistria's towns) are left out.

Land and borders: Eurostat GISCO, Countries 2024, 1:1 million,
© EuroGeographics for the administrative boundaries —
https://gisco-services.ec.europa.eu/distribution/v2/countries/. The whole
world, so the land and the borders between countries show beyond the two
countries too, but rivers and places only within them.

Both are downloaded once and cached in a compact form (~10 MB).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import time
import unicodedata
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Any, NamedTuple

import numpy as np
import shapefile
import shapely
from shapely.geometry import shape

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection

import frame
from biomes import _download, visible_points
from borders import _pack, _unpack
from cities import LabelSpace, label_space, side_offset
from hydrology import project_lines
from relief import _points_to_data
from ro_admin import _COMMA_BELOW, SIRUTA_URL, _fold, written_name

logger = logging.getLogger(__name__)

GEOFABRIK_URL = "https://download.geofabrik.de/europe/{region}-latest-free.shp.zip"
REGIONS = ("romania", "moldova")                                # places, rivers and lakes, named
NEIGHBOURS = ("hungary", "serbia", "bulgaria", "ukraine")        # rivers and lakes near Romania, unnamed
NEIGHBOUR_BOX = (18.5, 41.5, 32.5, 50.5)                         # (west, south, east, north), degrees
COUNTRIES_URL = "https://gisco-services.ec.europa.eu/distribution/v2/countries/shp/CNTR_RG_01M_2024_4326.shp.zip"
BORDERS_URL = "https://gisco-services.ec.europa.eu/distribution/v2/countries/shp/CNTR_BN_01M_2024_4326.shp.zip"
DATA_VERSION = 2          # bump when the cache changes, so old ones are rebuilt
# OSM's Romanian names (name:ro) of Moldova's places, which Geofabrik's shapefiles leave out: Transnistria's
# towns and villages are named only in Russian there. Asked of the Overpass API, the next address if one fails.
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
OVERPASS_QUERY = """[out:json][timeout:120];
area["ISO3166-1"="MD"][admin_level=2]->.md;
node(area.md)["place"~"^(city|town|village)$"]["name:ro"];
out tags;"""

ATTRIBUTION = ("Map data © OpenStreetMap contributors (ODbL), Geofabrik extracts of {date}; "
               "land and borders © EuroGeographics (GISCO)")

# OSM's places, most important first; hamlets, suburbs and localities are left out
PLACE_KINDS = ("national_capital", "city", "town", "village")
WATER_KINDS = frozenset({"water", "reservoir", "riverbank"})
RIVER_KINDS = frozenset({"river", "canal"})
MIN_WATER_KM2 = 0.1       # smaller lakes and ponds are not kept
# Places OSM spells otherwise than SIRUTA, by more than case and hyphens (checked against SIRUTA 2026, first half)
SPELLINGS = {"Roșiorii de Vede": "Roșiori de Vede"}
SIMPLIFY = 0.002          # degrees (~200 m): the land and borders, finer than any page shows
JOIN = 0.002              # degrees: river ways of one name this close are one river

# OSM Carto's colours, for land and water
LAND_COLOUR = "#f2efe9"
WATER_COLOUR = "#aad3df"
RIVER_COLOUR = "#4f8fc0"
WATER_LABEL_COLOUR = "#2a5f8f"
BORDER_COLOUR = "#8e7a96"
PLACE_COLOUR = "#2f2f2f"
VILLAGE_COLOUR = "#555555"
COUNTRY_COLOUR = "#5f4b6b"

# Font sizes (points) of the names, as the other layers name places (see ro_admin.UAT_SIZES)
CAPITAL_SIZE = 9.5
CITY_SIZES = ((200_000, 8.0), (0, 7.5))        # a city of at least so many people, and its size
TOWN_SIZE, VILLAGE_SIZE = 6.5, 5.5
RIVER_SIZES = ((300, 7.5), (100, 7.0), (0, 6.5))  # a river of at least so many km
LAKE_SIZE = 6.5
COUNTRY_SIZES = ((1_000_000, 10.0), (100_000, 9.0), (0, 8.0))   # a country of at least so many km², and its size
OCEAN_SIZE, SEA_SIZE = 10.0, 7.5
# On a map up to CAPITALS_KM across, a capital is named with this share of the people a city needs (see
# min_population): Luxembourg on a map of Europe, not Vaduz or Monaco; on a wider one (the world), as a city
CAPITAL_SHARE = 0.1
CAPITALS_KM = 6000
# Maps wider than this draw their rivers thinner and only the longer ones (Europe, the world)
WIDE_KM = 1500
# A place this close (points) to a dot another layer marked is that layer's (GHSL's Bucharest is București)
NEAR_A_DOT = 8.0
MAX_RIVER_TILT = 65       # degrees: a river is named only where it runs this close to level

# Where along a stretch of river to try its name: the middle first, then out toward the ends
_FRACTIONS = sorted(np.linspace(0.1, 0.9, 17), key=lambda f: abs(f - 0.5))
_LATIN = re.compile(r"[^\W\d_]")
_ROMANIAN_LETTERS = frozenset("ăâîșțĂÂÎȘȚ")
_RIVER_WORD = re.compile(r"^(?:Râul|Pârâul)\s+(?=\S)")


class OSMDataError(RuntimeError):
    """The OpenStreetMap or GISCO data could not be downloaded or read."""


class Place(NamedTuple):
    name: str
    kind: str                       # one of PLACE_KINDS
    population: int
    lon: float
    lat: float


class Water(NamedTuple):
    name: str                       # a river's or lake's name, "" for one without
    km: float                       # a river's length, a lake's area in km²
    geometry: shapely.Geometry


class Label(NamedTuple):
    """A country's or a sea's name and where it may be written, best first."""
    name: str
    kind: str                       # "country", "ocean" or "sea"
    rank: float                     # a country's area in km²; a sea's scale rank (0 for the oceans, 1 the great seas)
    points: tuple[tuple[float, float], ...]     # (lon, lat)


class Base(NamedTuple):
    land: list[shapely.Geometry]
    borders: list[shapely.Geometry]
    places: list[Place]             # most important first
    rivers: list[Water]             # longest first
    lakes: list[Water]              # largest first
    date: str                       # the date of Geofabrik's extracts, "22 Oct 2026"
    labels: Sequence[Label] = ()    # the countries, largest first, then the seas (see ne_local)
    credit: str = ""                # the data credit, if not OSM's
    reservoirs: Sequence[Water] = ()    # the lakes behind dams, largest first, if told apart (see ne_local)


class Named(NamedTuple):
    """How many names :func:`name_base` wrote, by what they name."""
    places: int = 0
    rivers: int = 0
    lakes: int = 0
    areas: int = 0                  # countries and seas


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def latin(text: str) -> bool:
    """True if every letter of *text* is in the Latin script (and it has one)."""
    letters = _LATIN.findall(text)
    return bool(letters) and all(unicodedata.name(c, "").startswith("LATIN") for c in letters)


def written(name: str) -> str:
    """*name* with single spaces and ş, ţ written with the comma below."""
    return " ".join(name.split()).translate(_COMMA_BELOW)


def river_name(raw: str) -> str:
    """The name a map writes for a river section OSM names *raw*; ``""`` for none it can write.

    A Danube branch is named for the branch (``Dunărea (Brațul Sulina)`` →
    ``Brațul Sulina``); of a name in two languages (``Dunărea - Дунав``,
    ``Dunav/Dunărea``), the part in the Latin script, Romanian if one is.
    """
    name = written(raw)
    paren = re.fullmatch(r"(.+?)\s*\((.+)\)", name)
    if paren:
        outside, inside = paren.groups()
        name = inside if inside.startswith("Brațul") else outside
    parts = [p.strip() for p in re.split(r"\s+-\s+|\s*/\s*", name) if p.strip()]
    parts = [p for p in parts if latin(p)]
    romanian = [p for p in parts if _ROMANIAN_LETTERS & set(p)]
    return (romanian or parts or [""])[0]


def shorter_names(names: Sequence[str]) -> dict[str, str]:
    """``Râul Mureș`` → ``Mureș`` for each name of *names* whose river OSM names elsewhere without the word."""
    known = set(names)
    return {n: _RIVER_WORD.sub("", n) for n in known if _RIVER_WORD.sub("", n) in known - {n}}


def _km(line: shapely.Geometry) -> float:
    """The length of *line* (degrees) in km."""
    total = 0.0
    for part in shapely.get_parts(line):
        xy = np.asarray(part.coords)[:, :2]
        if len(xy) < 2:
            continue
        mid = np.radians((xy[1:, 1] + xy[:-1, 1]) / 2)
        dx, dy = np.diff(xy[:, 0]) * np.cos(mid) * 111.32, np.diff(xy[:, 1]) * 110.57
        total += float(np.hypot(dx, dy).sum())
    return total


def _km2(polygon: shapely.Geometry) -> float:
    """The area of *polygon* (degrees) in km²."""
    lat = float(shapely.get_y(shapely.centroid(polygon)))
    return float(shapely.area(polygon)) * 111.32 * 110.57 * float(np.cos(np.radians(lat)))


def _groups(lines: Sequence[shapely.Geometry], gap: float) -> list[list[int]]:
    """*lines* in groups that join up, at most *gap* apart."""
    parent = list(range(len(lines)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    tree = shapely.STRtree(lines)
    pairs = tree.query(lines, predicate="dwithin", distance=gap)
    for i, j in zip(*pairs):
        parent[root(int(i))] = root(int(j))
    groups: defaultdict[int, list[int]] = defaultdict(list)
    for i in range(len(lines)):
        groups[root(i)].append(i)
    return list(groups.values())


def merge_rivers(pieces: Sequence[tuple[str, str, shapely.Geometry]]) -> list[Water]:
    """The rivers *pieces* (key, name, line) make up, longest first.

    The pieces of one key that join up are one river, named as the first of
    them; pieces without a key, one for each run between the places where
    they branch.
    """
    by_key: defaultdict[str, list[tuple[str, shapely.Geometry]]] = defaultdict(list)
    for key, name, line in pieces:
        by_key[key].append((name, line))
    rivers = []
    for key, named in by_key.items():
        lines = [line for _, line in named]
        if not key:
            rivers += [Water("", _km(run), run)
                       for run in shapely.get_parts(shapely.line_merge(shapely.union_all(lines)))]
            continue
        for group in _groups(lines, JOIN):
            line = shapely.line_merge(shapely.union_all([lines[i] for i in group]))
            rivers.append(Water(named[group[0]][0], _km(line), line))
    return sorted(rivers, key=lambda r: -r.km)


# ---------------------------------------------------------------------------
# Download and caching
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "osm_local")


class RemoteFile(io.RawIOBase):
    """A file on a web server read in parts, with HTTP range requests, so a zip's few members can be read
    without downloading all of it."""

    def __init__(self, url: str, timeout: float = 120, retries: int = 3) -> None:
        super().__init__()
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ortho/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            self.url = resp.url                  # "latest" redirects to a dated file, read from one throughout
            self.size = int(resp.headers["Content-Length"])
        self.timeout, self.retries, self.pos = timeout, retries, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + offset
        return self.pos

    def readinto(self, buffer: Any) -> int:
        n = min(len(buffer), self.size - self.pos)
        if n <= 0:
            return 0
        headers = {"User-Agent": "ortho/1.0", "Range": f"bytes={self.pos}-{self.pos + n - 1}"}
        data = b""
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(urllib.request.Request(self.url, headers=headers),
                                            timeout=self.timeout) as resp:
                    if resp.status != 206:
                        raise OSMDataError(f"{self.url} cannot be read in parts (HTTP {resp.status})")
                    data = resp.read()
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.retries - 1:
                    raise
                time.sleep(2 ** attempt)
        buffer[:len(data)] = data
        self.pos += len(data)
        return len(data)


def _reader(archive: zipfile.ZipFile, stem: str) -> shapefile.Reader:
    """The shapefile *stem* (``gis_osm_places_free_1``, ``CNTR_RG_01M_2024_4326``) in *archive*."""
    names = {n.rsplit("/", 1)[-1]: n for n in archive.namelist()}
    try:
        files = {ext: io.BytesIO(archive.read(names[f"{stem}.{ext}"])) for ext in ("shp", "shx", "dbf")}
    except KeyError as exc:
        raise OSMDataError(f"the archive holds no {stem} shapefile") from exc
    return shapefile.Reader(shp=files["shp"], shx=files["shx"], dbf=files["dbf"], encoding="utf-8")


def _shapes(reader: shapefile.Reader, fields: Sequence[str]):
    """(record, geometry) for each of *reader*'s shapes."""
    for i, record in enumerate(reader.iterRecords(fields=list(fields))):
        found = reader.shape(i)
        if record is None or found is None or not found.points:
            continue
        yield record, shape(dict(found.__geo_interface__))


def _spelling_key(name: str) -> str:
    """*name* in lower case without spaces or hyphens, its letters and their diacritics kept."""
    return re.sub(r"[\s\-]", "", name.lower())


def siruta_spellings(text: str) -> dict[str, str]:
    """SIRUTA's spelling of each UAT and locality in its CSV *text*, by :func:`_spelling_key`, where the
    key has only one (``Poienile de sub Munte``, ``Strâmba-Jiu``)."""
    spellings: defaultdict[str, set[str]] = defaultdict(set)
    try:
        for row in csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"):
            if row["NIV"].strip() in ("2", "3"):
                name = written_name(row["DENLOC"])
                spellings[_spelling_key(name)].add(name)
    except (KeyError, TypeError) as exc:
        raise OSMDataError(f"SIRUTA's table is not as expected: {exc!r}") from exc
    return {key: next(iter(names)) for key, names in spellings.items() if len(names) == 1}


def official(name: str, spellings: dict[str, str]) -> str:
    """*name* as SIRUTA writes it where the two differ only in case, spaces and hyphens, or in
    :data:`SPELLINGS`; else as OSM writes it (SIRUTA leaves out some diacritics: ``Ilva Mica``)."""
    name = SPELLINGS.get(name, name)
    return spellings.get(_spelling_key(name), name)


def romanian_place_names(urls: Sequence[str] = OVERPASS_URLS, timeout: float = 180) -> dict[str, str]:
    """OSM's Romanian names (``name:ro``) of Moldova's places, by OSM id; ``{}`` if no server answers."""
    request_data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    for url in urls:
        try:
            request = urllib.request.Request(url, data=request_data, headers={"User-Agent": "ortho/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                elements = json.load(resp)["elements"]
            return {str(e["id"]): str(e["tags"]["name:ro"]) for e in elements if e.get("tags", {}).get("name:ro")}
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as exc:
            logger.info("Overpass at %s did not answer: %s", url, exc)
    logger.warning("No Romanian names for Transnistria's places (the Overpass API did not answer): "
                   "they are left unnamed.")
    return {}


def read_osm(archives: Sequence[tuple[zipfile.ZipFile, bool]], spellings: dict[str, str] | None = None,
             romanian: dict[str, str] | None = None) -> dict[str, np.ndarray]:
    """The places, rivers and lakes of Geofabrik's *archives*, each OSM feature once (a border river is in both
    countries').

    Each archive comes with whether it is named: Romania's and Moldova's give
    their places, rivers and lakes with their names; the neighbours' only
    their rivers and lakes within :data:`NEIGHBOUR_BOX`, unnamed (in Serbian,
    Bulgarian, Ukrainian or Hungarian, not as a Romanian map writes them).
    Unnamed canals are left out: the plains' irrigation and drainage canals.
    Places are spelled as SIRUTA's *spellings* spell them (see :func:`official`);
    a place named in another script only takes its Romanian name from
    *romanian* (OSM id -> ``name:ro``), without a note after a comma
    (``Novosavițcaia, loc. st. cf``), or is left out.
    """
    spellings, romanian = spellings or {}, romanian or {}
    box = shapely.box(*NEIGHBOUR_BOX)
    seen: set[str] = set()
    places: list[Place] = []
    pieces: list[tuple[str, str, shapely.Geometry]] = []
    lakes: list[Water] = []
    for archive, named in archives:
        if named:
            for record, point in _shapes(_reader(archive, "gis_osm_places_free_1"),
                                         ("osm_id", "fclass", "population", "name")):
                name, kind, osm_id = written(str(record["name"] or "")), str(record["fclass"]), str(record["osm_id"])
                if not latin(name):
                    name = written(romanian.get(osm_id, "").split(",")[0])
                if kind in PLACE_KINDS and latin(name) and osm_id not in seen:
                    seen.add(osm_id)
                    places.append(Place(official(name, spellings), kind, int(str(record["population"] or 0)),
                                        float(shapely.get_x(point)), float(shapely.get_y(point))))
        for record, line in _shapes(_reader(archive, "gis_osm_waterways_free_1"), ("osm_id", "fclass", "name")):
            kind, osm_id = str(record["fclass"]), str(record["osm_id"])
            if kind not in RIVER_KINDS or osm_id in seen or not (named or shapely.intersects(line, box)):
                continue
            raw = written(str(record["name"] or ""))
            name = river_name(raw) if named else ""
            if kind == "canal" and not name:
                continue
            seen.add(osm_id)
            pieces.append((name or raw, name, line))     # the pieces of a name in another script join up too
        for record, polygon in _shapes(_reader(archive, "gis_osm_water_a_free_1"), ("osm_id", "fclass", "name")):
            kind, osm_id = str(record["fclass"]), str(record["osm_id"])
            if kind not in WATER_KINDS or osm_id in seen or not (named or shapely.intersects(polygon, box)):
                continue
            seen.add(osm_id)
            area = _km2(polygon)
            if area >= MIN_WATER_KM2:
                name = written(str(record["name"] or "")) if named and kind != "riverbank" else ""
                lakes.append(Water(name if latin(name) else "", area, polygon))
    shorter = shorter_names([key for key, _, _ in pieces])
    rivers = merge_rivers([(shorter.get(key, key), shorter.get(name, name), line) for key, name, line in pieces])
    # A wide river's water is mapped as an area with the river's name (the Olt's reservoirs, the Nistru): unnamed
    river_names = {r.name for r in rivers}
    lakes = [w._replace(name="") if w.name in river_names else w for w in lakes]
    order = {kind: k for k, kind in enumerate(PLACE_KINDS)}
    places.sort(key=lambda p: (order[p.kind], -p.population))
    lakes.sort(key=lambda w: -w.km)
    packed_rivers, packed_lakes = _pack([r.geometry for r in rivers]), _pack([w.geometry for w in lakes])
    return {"place_names": np.array([p.name for p in places], dtype=str),
            "place_kinds": np.array([p.kind for p in places], dtype=str),
            "place_population": np.array([p.population for p in places], dtype=np.int64),
            "place_lon": np.array([p.lon for p in places], dtype=float),
            "place_lat": np.array([p.lat for p in places], dtype=float),
            "river_names": np.array([r.name for r in rivers], dtype=str),
            "river_km": np.array([r.km for r in rivers], dtype=float),
            "river_wkb": packed_rivers["wkb"], "river_wkb_ends": np.asarray(packed_rivers["wkb_ends"]),
            "lake_names": np.array([w.name for w in lakes], dtype=str),
            "lake_km2": np.array([w.km for w in lakes], dtype=float),
            "lake_wkb": packed_lakes["wkb"], "lake_wkb_ends": np.asarray(packed_lakes["wkb_ends"])}


def read_countries(countries: zipfile.ZipFile, borders: zipfile.ZipFile) -> dict[str, np.ndarray]:
    """The land (GISCO's countries) and the borders between countries (not the coasts), simplified."""
    land = [shapely.simplify(shapely.make_valid(g), SIMPLIFY, preserve_topology=True)
            for _, g in _shapes(_reader(countries, "CNTR_RG_01M_2024_4326"), ("CNTR_ID",))]
    lines = [shapely.simplify(g, SIMPLIFY) for record, g in
             _shapes(_reader(borders, "CNTR_BN_01M_2024_4326"), ("COAS_FLAG",)) if record["COAS_FLAG"] == "F"]
    if not land:
        raise OSMDataError("GISCO's countries file holds no countries")
    packed_land, packed_borders = _pack([g for g in land if not g.is_empty]), _pack(lines)
    return {"land_wkb": packed_land["wkb"], "land_wkb_ends": np.asarray(packed_land["wkb_ends"]),
            "border_wkb": packed_borders["wkb"], "border_wkb_ends": np.asarray(packed_borders["wkb_ends"])}


def _extract_date(url: str) -> str:
    """``22 Oct 2026`` from a Geofabrik extract's address (``romania-261022-free.shp.zip``), else ``""``."""
    found = re.search(r"-(\d{2})(\d{2})(\d{2})-free", url)
    if not found:
        return ""
    year, month, day = (int(v) for v in found.groups())
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{day} {months[month - 1]} {2000 + year}" if 1 <= month <= 12 else ""


def ensure_osm_data(cache_dir: str | None = None) -> str:
    """Path to the cached base map data, downloading and reading it if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"osm_local_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached OpenStreetMap data: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Reading the places, rivers and lakes of %s, and the rivers and lakes of their neighbours, "
                "from Geofabrik, and GISCO's countries (one-time, ~300 MB) …",
                " and ".join(r.capitalize() for r in REGIONS))
    logger.info("Places are spelled as INS's register SIRUTA spells them where the two differ in case or hyphens.")
    downloads = [os.path.join(cache_dir, f"{name}.download") for name in ("countries", "borders", "siruta")]
    try:
        _download(SIRUTA_URL, downloads[2], label="SIRUTA")
        with open(downloads[2], "rb") as fh:
            spellings = siruta_spellings(fh.read().decode("utf-8-sig"))
        regions = [(region, True) for region in REGIONS] + [(region, False) for region in NEIGHBOURS]
        remote = [RemoteFile(GEOFABRIK_URL.format(region=region)) for region, _ in regions]
        archives = [(zipfile.ZipFile(io.BufferedReader(f, 1 << 20)), named) for f, (_, named) in zip(remote, regions)]
        table = read_osm(archives, spellings, romanian_place_names())
        for url, dest in zip((COUNTRIES_URL, BORDERS_URL), downloads):
            _download(url, dest, timeout=300, label="GISCO countries")
        with zipfile.ZipFile(downloads[0]) as countries, zipfile.ZipFile(downloads[1]) as borders:
            table |= read_countries(countries, borders)
        dates = sorted({d for d in (_extract_date(f.url) for f in remote) if d})
        table["date"] = np.array(" and ".join(dates))
    except OSMDataError:
        raise
    except Exception as exc:                  # network, archive and geometry errors alike
        raise OSMDataError(f"Could not read the OpenStreetMap data: {exc}") from exc
    finally:
        for download in downloads:
            if os.path.exists(download):
                os.remove(download)
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    logger.info("OpenStreetMap data ready: %s", path)
    return path


def _unpacked(table: dict[str, np.ndarray], prefix: str) -> list[shapely.Geometry]:
    return _unpack({"wkb": table[f"{prefix}_wkb"], "wkb_ends": table[f"{prefix}_wkb_ends"]})


def load_base(cache_dir: str | None = None) -> Base:
    """The land, borders, places, rivers and lakes to draw."""
    with np.load(ensure_osm_data(cache_dir)) as data:
        table = {key: data[key] for key in data.files}
    places = [Place(str(n), str(k), int(p), float(x), float(y)) for n, k, p, x, y in
              zip(table["place_names"], table["place_kinds"], table["place_population"], table["place_lon"],
                  table["place_lat"])]
    rivers = [Water(str(n), float(k), g) for n, k, g in
              zip(table["river_names"], table["river_km"], _unpacked(table, "river"))]
    lakes = [Water(str(n), float(k), g) for n, k, g in
             zip(table["lake_names"], table["lake_km2"], _unpacked(table, "lake"))]
    return Base(_unpacked(table, "land"), _unpacked(table, "border"), places, rivers, lakes, str(table["date"]))


def attribution(base: Base) -> str:
    return base.credit or ATTRIBUTION.format(date=base.date or "unknown date")


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def map_km(ax: GeoAxes) -> float:
    """How many km the map on *ax* spans across (the projection's units are metres)."""
    x0, x1 = ax.get_xlim()
    return abs(x1 - x0) / 1000


def min_river_km(across: float) -> float:
    """The shortest river drawn on a map *across* km wide: 5 km on a map 150 km wide, 30 on one 900 km wide;
    on a wider one, longer still: 300 km on one 5,000 km wide (Europe), 1,250 on the world's."""
    return across / 30 * max(1.0, across / WIDE_KM) ** 0.5


def min_lake_km2(across: float) -> float:
    """The smallest lake drawn on a map *across* km wide: 0.25 km² on one 150 km wide, 9 on one 900 km wide."""
    return (across / 300) ** 2


def min_population(across: float) -> float:
    """The fewest people of a place named on a map *across* km wide, so about as many show on any map: 24,300 on
    one 900 km wide (Romania), 1,200 on one 200 km wide. Growing with the map's area."""
    return 0.03 * across ** 2


def place_kinds(across: float) -> frozenset[str]:
    """What places a map *across* km wide names: villages on one up to 300 km wide, towns up to 1,500 km."""
    if across <= 300:
        return frozenset(PLACE_KINDS)
    if across <= 1500:
        return frozenset(PLACE_KINDS[:3])
    return frozenset(PLACE_KINDS[:2])


def min_country_km2(across: float) -> float:
    """The smallest country named on a map *across* km wide: 6,900 km² on one 5,000 km wide (Europe), 45,000 on
    the world's."""
    return (across / 60) ** 2


def max_sea_rank(across: float) -> int:
    """The least of the seas named on a map *across* km wide, as Natural Earth ranks them: the oceans and the
    great seas on the world's (rank 1), the North Sea and the Adriatic on Europe's (3)."""
    return 1 if across > 8000 else 3 if across > 3000 else 4 if across > 1000 else 6


def river_width(km: float) -> float:
    """Line width (points) of a river *km* long."""
    return float(np.clip(0.35 + 0.25 * np.log2(max(km, 1) / 25), 0.35, 1.6))


def _view_box(ax: GeoAxes) -> shapely.Geometry | None:
    """The longitudes and latitudes in view as a box, a degree wider; ``None`` when that is most of the world."""
    lon, lat, _, _ = visible_points(ax, 120)
    if not len(lon) or np.ptp(lon) > 180:
        return None
    return shapely.box(lon.min() - 1, max(lat.min() - 1, -90), lon.max() + 1, min(lat.max() + 1, 90))


def _in_view(geometries: Sequence[shapely.Geometry], view: shapely.Geometry | None) -> list[shapely.Geometry]:
    if view is None:
        return list(geometries)
    clipped = shapely.intersection(np.array(geometries, dtype=object), view)
    return [g for g in clipped if not g.is_empty]


def _lakes(base: Base, reservoirs: bool) -> list[Water]:
    """*base*'s lakes and, if *reservoirs*, its reservoirs, the largest first."""
    return sorted([*base.lakes, *(base.reservoirs if reservoirs else ())], key=lambda w: -w.km)


def draw_base(ax: GeoAxes, base: Base, borders: bool = True, reservoirs: bool = True) -> None:
    """Draw the land, lakes, rivers and, with *borders*, the borders between countries of *base* on *ax*, for
    its scale; without *reservoirs*, not the lakes behind dams (on a map of borders older than they are).

    The land goes under every other layer (as tiles do); the water and borders
    over the colours other layers lay on the land, under their lines.
    """
    across = map_km(ax)
    view = _view_box(ax)
    ax.add_geometries(_in_view(base.land, view), crs=ccrs.PlateCarree(), facecolor=LAND_COLOUR,
                      edgecolor="none", zorder=0.5)
    lakes = [w.geometry for w in _lakes(base, reservoirs) if w.km >= min_lake_km2(across)]
    if lakes:
        ax.add_geometries(_in_view(lakes, view), crs=ccrs.PlateCarree(), facecolor=WATER_COLOUR,
                          edgecolor=RIVER_COLOUR, linewidth=0.2, zorder=5.3)
    segments, widths, drawn = [], [], 0
    thinner = min(1.0, WIDE_KM / across) ** 0.35
    for river in base.rivers:
        if river.km < min_river_km(across):
            break
        if view is not None and not shapely.intersects(river.geometry, view):
            continue
        runs = project_lines(ax, river.geometry)
        segments += runs
        widths += [river_width(river.km) * thinner] * len(runs)
        drawn += bool(runs)
    ax.add_collection(LineCollection(segments, colors=RIVER_COLOUR, linewidths=widths, zorder=5.35,
                                     capstyle="round", joinstyle="round"))
    lines = _in_view(base.borders, view) if borders else []
    if lines:
        ax.add_geometries(lines, crs=ccrs.PlateCarree(), facecolor="none", edgecolor=BORDER_COLOUR,
                          linewidth=1.1, linestyle=(0, (4, 1.5, 1, 1.5)), zorder=5.45)
    logger.info("Base map drawn: %d rivers, %d lakes (map %s km across).", drawn, len(lakes), f"{across:,.0f}")


def place_size(place: Place) -> float:
    """Font size (points) of *place*'s name."""
    if place.kind == "national_capital":
        return CAPITAL_SIZE
    if place.kind == "city":
        return next(size for people, size in CITY_SIZES if place.population >= people)
    return TOWN_SIZE if place.kind == "town" else VILLAGE_SIZE


def river_size(km: float) -> float:
    return next(size for length, size in RIVER_SIZES if km >= length)


def _claim_box(space: LabelSpace, box: tuple[float, float, float, float]) -> bool:
    """Whether *box* (display pixels) is on the map and clear of the names placed; if so it is taken."""
    if not frame.box_inside(space.ax, box, 0.97):
        return False
    if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in space.placed):
        return False
    space.placed.append(box)
    return True


def _along(run: np.ndarray, pixels: np.ndarray, fraction: float, half: float
           ) -> tuple[float, float, float] | None:
    """The point *fraction* of the way along *run* (map coordinates) and the angle (degrees) of the line
    from *half* pixels before it to *half* after, turned to read left to right; ``None`` past either end."""
    steps = np.hypot(*np.diff(pixels, axis=0).T)
    along = np.concatenate([[0.0], np.cumsum(steps)])
    at = fraction * along[-1]
    if at - half < 0 or at + half > along[-1]:
        return None
    x, y = (float(np.interp(at, along, run[:, k])) for k in (0, 1))
    before, after = (np.array([np.interp(d, along, pixels[:, k]) for k in (0, 1)]) for d in (at - half, at + half))
    angle = float(np.degrees(np.arctan2(after[1] - before[1], after[0] - before[0])))
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    return x, y, angle


def _rotated_box(px: float, py: float, width: float, height: float, angle: float
                 ) -> tuple[float, float, float, float]:
    """The display box around a *width* by *height* label standing on (*px*, *py*), turned *angle* degrees."""
    a = np.radians(angle)
    along, up = np.array([np.cos(a), np.sin(a)]), np.array([-np.sin(a), np.cos(a)])
    corners = np.array([[px, py]]) + np.array([s * width / 2 * along + h * height * up
                                               for s in (-1, 1) for h in (0, 1)])
    return (float(corners[:, 0].min()), float(corners[:, 1].min()),
            float(corners[:, 0].max()), float(corners[:, 1].max()))


def _name_river(ax: GeoAxes, space: LabelSpace, river: Water) -> bool:
    """Name *river* along its longest stretch in sight, where it runs near level and the name has room."""
    size = river_size(river.km)
    scale = ax.figure.dpi / 72
    width, height = 0.6 * size * len(river.name) * scale, 1.2 * size * scale
    runs = sorted(project_lines(ax, river.geometry), key=len, reverse=True)
    for run in runs[:5]:
        pixels = ax.transData.transform(run)
        for fraction in _FRACTIONS:
            found = _along(run, pixels, fraction, width / 2)
            if found is None:
                continue
            x, y, angle = found
            if abs(angle) > MAX_RIVER_TILT:
                continue
            px, py = ax.transData.transform((x, y))
            if not _claim_box(space, _rotated_box(px, py, width, height, angle)):
                continue
            ax.text(x, y, river.name, transform=ax.projection, fontsize=size, fontstyle="italic",
                    color=WATER_LABEL_COLOUR, rotation=angle, rotation_mode="anchor", ha="center", va="bottom",
                    zorder=7.65)
            return True
    return False


def _mark_place(ax: GeoAxes, x: float, y: float, place: Place) -> None:
    capital = place.kind == "national_capital"
    size = 4.2 if capital else 3.2 if place.kind == "city" else 2.6 if place.kind == "town" else 2.0
    ax.plot(x, y, "o", transform=ax.projection, markersize=size, markerfacecolor="white" if capital else PLACE_COLOUR,
            markeredgecolor=PLACE_COLOUR, markeredgewidth=0.9 if capital else 0.0, zorder=7.6)
    if capital:
        ax.plot(x, y, "o", transform=ax.projection, markersize=1.8, markerfacecolor=PLACE_COLOUR,
                markeredgecolor="none", zorder=7.61)


def _name_place(ax: GeoAxes, space: LabelSpace, place: Place) -> bool:
    size = place_size(place)
    px, py = ax.projection.transform_point(place.lon, place.lat, ccrs.PlateCarree())
    if not (np.isfinite(px) and np.isfinite(py)) or not frame.inside(ax, px, py, 0.99):
        return False
    side = space.claim_around(px, py, place.name, size, bold=place.kind == "national_capital")
    if side is None:
        return False
    _mark_place(ax, px, py, place)
    village = place.kind == "village"
    ax.annotate(place.name, (px, py), xycoords="data", textcoords="offset points", **side_offset(side, size),
                fontsize=size, fontweight="bold" if place.kind == "national_capital" else "normal",
                color=VILLAGE_COLOUR if village else PLACE_COLOUR, zorder=7.65,
                annotation_clip=True)
    return True


def _name_lake(ax: GeoAxes, space: LabelSpace, lake: Water) -> bool:
    point = shapely.point_on_surface(lake.geometry)
    px, py = ax.projection.transform_point(shapely.get_x(point), shapely.get_y(point), ccrs.PlateCarree())
    if not (np.isfinite(px) and np.isfinite(py)) or not frame.inside(ax, px, py, 0.99):
        return False
    lift = _points_to_data(ax, 0.85 * LAKE_SIZE)
    if not space.claim(px, py - lift, lake.name, LAKE_SIZE, point=0):
        return False
    ax.annotate(lake.name, (px, py - lift), xycoords="data", xytext=(0, 0.25 * LAKE_SIZE),
                textcoords="offset points", fontsize=LAKE_SIZE, fontstyle="italic", color=WATER_LABEL_COLOUR,
                ha="center", va="bottom", zorder=7.65, annotation_clip=True)
    return True


def area_size(label: Label) -> float:
    """Font size (points) of a country's or a sea's name."""
    if label.kind == "country":
        return next(size for km2, size in COUNTRY_SIZES if label.rank >= km2)
    return OCEAN_SIZE if label.kind == "ocean" else SEA_SIZE


def _name_area(ax: GeoAxes, space: LabelSpace, label: Label) -> bool:
    """Name a country or a sea at the first of its points in sight where the name has room."""
    size = area_size(label)
    scale = ax.figure.dpi / 72
    width, height = 0.6 * size * len(label.name) * scale, 1.2 * size * scale
    for lon, lat in label.points:
        x, y = ax.projection.transform_point(lon, lat, ccrs.PlateCarree())
        if not (np.isfinite(x) and np.isfinite(y)) or not frame.inside(ax, x, y, 0.99):
            continue
        px, py = ax.transData.transform((x, y))
        if not _claim_box(space, (px - width / 2, py - height / 2, px + width / 2, py + height / 2)):
            continue
        country = label.kind == "country"
        ax.text(x, y, label.name, transform=ax.projection, fontsize=size, ha="center", va="center",
                fontstyle="normal" if country else "italic", color=COUNTRY_COLOUR if country else WATER_LABEL_COLOUR,
                zorder=7.65, clip_on=True)
        return True
    return False


def _dots(ax: GeoAxes) -> np.ndarray:
    """Where the other layers marked places on *ax* (their one-point markers), in display pixels."""
    found = [line.get_transform().transform(np.column_stack([line.get_xdata(), line.get_ydata()]))
             for line in ax.lines
             if line.get_marker() not in (None, "", " ", "None") and len(np.atleast_1d(line.get_xdata())) == 1]
    return np.vstack(found) if found else np.empty((0, 2))


def _marked(ax: GeoAxes, place: Place, dots: np.ndarray) -> bool:
    """True if another layer marked a place within :data:`NEAR_A_DOT` points of *place*."""
    if not len(dots):
        return False
    x, y = ax.projection.transform_point(place.lon, place.lat, ccrs.PlateCarree())
    if not (np.isfinite(x) and np.isfinite(y)):
        return False
    px, py = ax.transData.transform((x, y))
    return bool((np.hypot(dots[:, 0] - px, dots[:, 1] - py) < NEAR_A_DOT * ax.figure.dpi / 72).any())


def _keep_clear_of_texts(ax: GeoAxes, space: LabelSpace) -> None:
    """Keep the room of every name already written on *ax* (a state's name on the borders layer), whether or not
    its layer took it in *space*."""
    for text in ax.texts:
        if text.get_visible() and text.get_text().strip():
            box = text.get_window_extent()
            space.placed.append((float(box.x0), float(box.y0), float(box.x1), float(box.y1)))


def name_base(ax: GeoAxes, base: Base, countries: bool = True, places: bool = True) -> Named:
    """Name the countries, seas, places, rivers and lakes of *base* on *ax* in the room the other layers' names
    leave.

    With *countries*, the countries first (of at least :func:`min_country_km2`),
    the largest first; then the capitals and cities, the most populous first;
    then the seas (up to :func:`max_sea_rank`); then the rivers, longest
    first, along a stretch in sight; then the lakes, largest first; then the
    towns and, on a map up to 300 km across, the villages. Only places of at
    least :func:`min_population` people are named, capitals of at least
    :data:`CAPITAL_SHARE` of that on a map up to :data:`CAPITALS_KM` across.
    Rivers are named from 4/30 of the map's width long. A name a layer
    already wrote (a county seat) is not written again, nor a place another
    layer marked with a dot under another name (GHSL's ``Bucharest``), and no
    name goes over another layer's. Without *places* (on a map of borders long
    gone, where today's cities, capitals and reservoirs do not belong), only
    the countries, seas, rivers and lakes are named.
    """
    across = map_km(ax)
    kinds = place_kinds(across)
    already = {_fold(t.get_text()) for t in ax.texts}
    space = label_space(ax)
    _keep_clear_of_texts(ax, space)
    dots = _dots(ax)
    fewest = min_population(across)
    share = CAPITAL_SHARE if across <= CAPITALS_KM else 1.0
    shown = [p for p in (base.places if places else []) if p.kind in kinds
             and p.population >= fewest * (share if p.kind == "national_capital" else 1)
             and _fold(p.name) not in already and not _marked(ax, p, dots)]
    big = [p for p in shown if p.kind in ("national_capital", "city")]
    small = [p for p in shown if p.kind not in ("national_capital", "city")]
    view = _view_box(ax)
    areas = [a for a in base.labels if _fold(a.name) not in already
             and (a.rank >= min_country_km2(across) and countries if a.kind == "country"
                  else a.rank <= max_sea_rank(across))]
    named_areas: set[str] = set()

    def name_areas(kinds: Collection[str]) -> None:
        for area in areas:
            if area.kind in kinds and area.name not in named_areas and _name_area(ax, space, area):
                named_areas.add(area.name)
    name_areas({"country"})
    places_named = sum(_name_place(ax, space, p) for p in big)
    name_areas({"ocean", "sea"})            # after the cities: a sea's name has room elsewhere, a city's has not
    rivers = [r for r in base.rivers if r.name and r.km >= max(min_river_km(across), 4 * across / 30)
              and (view is None or shapely.intersects(r.geometry, view))]
    named_rivers: set[str] = set()
    for river in rivers:
        if river.name not in named_rivers and _name_river(ax, space, river):
            named_rivers.add(river.name)
    lakes = [w for w in _lakes(base, places) if w.name and w.km >= 4 * min_lake_km2(across)
             and (view is None or shapely.intersects(w.geometry, view))]
    lakes_named = sum(_name_lake(ax, space, w) for w in lakes)
    places_named += sum(_name_place(ax, space, p) for p in small)
    logger.info("Base map names: %d countries and seas, %d places, %d rivers, %d lakes.", len(named_areas),
                places_named, len(named_rivers), lakes_named)
    return Named(places_named, len(named_rivers), lakes_named, len(named_areas))
