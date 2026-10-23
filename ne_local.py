"""A map of Europe or the world drawn from Natural Earth's data instead of tiles: land, lakes, rivers, borders,
countries, seas and cities, named in type sized for the page.

OSM's tiles are drawn for screens: on a map of Europe or a hemisphere their
pixels are stretched over the page and their names blur, in the local script
(Burmese, Thai) where it is not Latin. This provider draws the base as
:mod:`osm_local` draws Romania's, from Natural Earth's 1:10m data, and names
it as the other layers do, in English or in Romanian.

Data sources
------------
Land, lakes, rivers, borders, countries, seas and populated places: Natural
Earth, 1:10m (public domain) — https://www.naturalearthdata.com/. Cartopy
downloads the files once (~30 MB); the rivers and lakes of Europe are its
finer supplement. A river is the pieces of one Wikidata item (or name) that
join up.

Names in English are Natural Earth's. Names in Romanian are Wikidata's (CC0):
the title of the item's Romanian Wikipedia article (``Marea Chinei de Est``)
without its note in brackets (``Ohře (râu)``), else its Romanian label;
countries are named as CLDR names them, as the borders are (see
:func:`borders.cldr_territories`). A river is written without the word
``Râul`` or ``Fluviul`` before its name (``Mureș``), unless what follows is
an adjective (``Fluviul Galben``). A feature without a Romanian name is left
unnamed, a city without one keeps Natural Earth's name.

Both are read once and cached in a compact form (~4 MB) under
``~/.cache/ortho_tiles/ne_local/``; the Romanian names take a few minutes
to ask of Wikidata the first time.
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
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import shapely
from shapely.ops import polylabel

import cartopy.io.shapereader as shapereader

from borders import CLDR_VERSION, BordersError, cldr_territories, _pack, _unpack
from osm_local import Base, Label, OSMDataError, Place, Water, _km2, latin, merge_rivers, written

logger = logging.getLogger(__name__)

DATA_VERSION = 6          # bump when the cache changes, so old ones are rebuilt
LANGUAGES = ("en", "ro")
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_BATCH = 50       # items asked at once, as many as the API allows
ATTRIBUTION = "Map data: Natural Earth"
ROMANIAN_ATTRIBUTION = f", Wikidata, CLDR {CLDR_VERSION.rsplit('.', 1)[0]}"      # short: it sits by the globe

MIN_POPULATION = 50_000   # fewer people than this, a place is not kept (unless a capital)
SEA_KINDS = frozenset({"ocean", "sea", "gulf", "bay", "strait", "channel", "sound"})
SEA_POINTS = 5            # a sea's name may go at so many points of its largest part, and in so many parts
SIMPLIFY = {"land": 0.01, "border": 0.005, "river": 0.005, "lake": 0.005}   # degrees (~1 km and ~500 m)

# Natural Earth's pieces of river whose Wikidata item is another river's: a name for each, "" to leave it unnamed
RIVER_NAMES = {"Crisul Alb": ("Crișul Alb", "Crișul Alb")}       # tagged as the Körös (Criș), into which it flows
# Natural Earth's capitals that are not: a former one (Yangon, to 2006) and the seat of a court (Johannesburg)
NOT_CAPITALS = frozenset({"Johannesburg", "Yangon"})
# Lakes no more: the Kakhovka Reservoir, drained when its dam was destroyed in June 2023
GONE = frozenset({"Q1540436"})
# Cities whose Wikidata item (Tokyo's 23 wards, the City of Brussels) is titled otherwise than the city, in Romanian
PLACE_NAMES = {"Tokyo": "Tokyo", "Brussels": "Bruxelles"}
# Words a river's Romanian name is written without, and the adjectives that keep them (Fluviul Galben)
_RIVER_WORD = re.compile(r"^(?:râul|fluviul|fluviu)\s+(?=\S)", re.IGNORECASE)
_ADJECTIVES = frozenset({"Alb", "Albastru", "Galben", "Mare", "Mic", "Negru", "Roșu", "Verde"})


class NEDataError(OSMDataError):
    """Natural Earth's data or Wikidata's names could not be downloaded or read."""


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def romanian(title: str, label: str, river: bool = False) -> str:
    """The Romanian name of an item whose Romanian Wikipedia article is *title* and label *label* (either may be
    ``""``): the title without its note in brackets or after a comma, else the label; with a capital, and for a
    *river* without ``Râul`` or ``Fluviul`` before the name."""
    name = written(title or label)
    name = re.sub(r"\s*\([^)]*\)$", "", name).split(",")[0].strip()
    if river:
        rest = _RIVER_WORD.sub("", name)
        if rest != name and rest.split()[0] not in _ADJECTIVES:
            name = rest
    return name[:1].upper() + name[1:]


def ask_wikidata(items: Sequence[str], url: str = WIKIDATA_URL, timeout: float = 60, pause: float = 1.0,
                 retries: int = 6) -> dict[str, tuple[str, str]]:
    """Each of Wikidata's *items*' (``Q1653``) Romanian Wikipedia title and Romanian label, ``""`` for none.

    Asked :data:`WIKIDATA_BATCH` at a time, *pause* seconds apart, waiting as
    the server asks when it is busy (HTTP 429). Raises :class:`NEDataError`
    if it does not answer.
    """
    found: dict[str, tuple[str, str]] = {}
    items = sorted(set(items))
    for start in range(0, len(items), WIKIDATA_BATCH):
        batch = items[start:start + WIKIDATA_BATCH]
        query = urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(batch), "props": "labels|sitelinks",
                                        "languages": "ro", "sitefilter": "rowiki", "format": "json"})
        request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "ortho/1.0"})
        entities: dict[str, Any] = {}
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as resp:
                    entities = json.load(resp)["entities"]
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == retries - 1:
                    raise NEDataError(f"Wikidata did not answer: {exc}") from exc
                time.sleep(float(exc.headers.get("Retry-After") or 10))
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as exc:
                if attempt == retries - 1:
                    raise NEDataError(f"Wikidata did not answer: {exc}") from exc
                time.sleep(2 ** attempt)
        for item in batch:
            entity = entities.get(item, {})
            found[item] = (str(entity.get("sitelinks", {}).get("rowiki", {}).get("title", "")),
                           str(entity.get("labels", {}).get("ro", {}).get("value", "")))
        if start // WIKIDATA_BATCH % 10 == 9:
            logger.info("  Wikidata: %d of %d names", start + len(batch), len(items))
        time.sleep(pause)
    return found


# ---------------------------------------------------------------------------
# Reading Natural Earth
# ---------------------------------------------------------------------------


def _records(natural_earth: Callable[..., str], category: str, name: str) -> list[tuple[dict[str, Any], Any]]:
    """(attributes with lower-case keys, geometry) of each of Natural Earth's 1:10m *name* features."""
    reader = shapereader.Reader(natural_earth("10m", category, name))
    return [({k.lower(): v for k, v in r.attributes.items()}, r.geometry) for r in reader.records()
            if r.geometry is not None and not r.geometry.is_empty]


def _item(attributes: dict[str, Any]) -> str:
    return str(attributes.get("wikidataid") or "")


def _english(attributes: dict[str, Any]) -> str:
    return written(str(attributes.get("name_en") or attributes.get("name") or ""))


def _simplified(geometry: Any, kind: str) -> shapely.Geometry:
    return shapely.simplify(shapely.make_valid(geometry), SIMPLIFY[kind], preserve_topology=True)


def _inner_points(part: shapely.Polygon, count: int) -> list[tuple[float, float]]:
    """Up to *count* points well inside the polygon *part*, spread out: the one farthest inside first, then the
    farthest inside of a grid's points at least a quarter of its size from those taken."""
    first = polylabel(part, tolerance=max(part.area ** 0.5 / 50, 0.01))
    x0, y0, x1, y1 = part.bounds
    xs, ys = np.meshgrid(np.linspace(x0, x1, 24), np.linspace(y0, y1, 24))
    grid = np.asarray(shapely.points(xs.ravel(), ys.ravel()))
    grid = grid[shapely.contains(part, grid)]
    inside = shapely.distance(grid, part.exterior)
    apart = part.area ** 0.5 / 4
    taken = [(first.x, first.y)]
    for i in np.argsort(-inside):
        if len(taken) >= count:
            break
        x, y = shapely.get_x(grid[i]), shapely.get_y(grid[i])
        if all(np.hypot(x - tx, y - ty) >= apart for tx, ty in taken):
            taken.append((float(x), float(y)))
    return taken


def _label_points(geometry: Any, count: int) -> tuple[tuple[float, float], ...]:
    """Where in *geometry* a name goes, best first: *count* points spread over its largest part (the farthest
    inside first), then the point farthest inside each of its next largest parts, up to *count* parts."""
    parts: list[shapely.Polygon] = [g for g in sorted(shapely.get_parts(shapely.make_valid(geometry)),
                                                      key=lambda g: -shapely.area(g))
                                    if isinstance(g, shapely.Polygon) and g.area > 0]
    points = _inner_points(parts[0], count) if parts else []
    points += [_inner_points(part, 1)[0] for part in parts[1:count]]
    return tuple((round(x, 3), round(y, 3)) for x, y in points)


def read_natural_earth(natural_earth: Callable[..., str] = shapereader.natural_earth) -> dict[str, Any]:
    """Natural Earth's features, each with its English name and Wikidata item (see :func:`build_table`)."""
    land = [_simplified(g, "land") for _, g in _records(natural_earth, "physical", "land")]
    borders = [shapely.simplify(g, SIMPLIFY["border"]) for _, g in
               _records(natural_earth, "cultural", "admin_0_boundary_lines_land")]
    places = []
    for a, g in _records(natural_earth, "cultural", "populated_places"):
        capital = a["featurecla"] == "Admin-0 capital" and a["name"] not in NOT_CAPITALS
        if int(a["pop_max"] or 0) >= MIN_POPULATION or capital:
            places.append({"en": _english(a), "local": written(str(a["name"] or "")), "item": _item(a),
                           "kind": "national_capital" if capital else "city", "population": int(a["pop_max"] or 0),
                           "lon": float(g.x), "lat": float(g.y)})
    rivers, items = [], set()
    for layer in ("rivers_lake_centerlines", "rivers_europe"):          # the world's, then Europe's finer ones
        records = [(a, g) for a, g in _records(natural_earth, "physical", layer)
                   if not re.search("Lake Centerline|Intermittent", str(a["featurecla"]))]   # nor wadis
        if layer == "rivers_europe":
            records = [(a, g) for a, g in records if _item(a) not in items]
        items |= {_item(a) for a, _ in records}
        rivers += [{"en": _english(a), "local": str(a["name"] or ""), "item": _item(a),
                    "line": _simplified(g, "river")} for a, g in records]
    lakes, items = [], set()
    for layer in ("lakes", "lakes_europe"):
        records = [(a, g) for a, g in _records(natural_earth, "physical", layer)
                   if (_item(a) not in items or not _item(a)) and _item(a) not in GONE]
        items |= {_item(a) for a, _ in records}
        lakes += [{"en": _english(a), "item": _item(a), "polygon": _simplified(g, "lake"),
                   "reservoir": a["featurecla"] == "Reservoir"} for a, g in records]
    countries = [{"en": _english(a), "item": _item(a), "iso": str(a.get("iso_a2_eh") or ""),
                  "km2": _km2(g), "points": ((float(a["label_x"]), float(a["label_y"])),)}
                 for a, g in _records(natural_earth, "cultural", "admin_0_countries")
                 if str(a.get("iso_a2_eh") or "-99") != "-99"]    # not Somaliland or Northern Cyprus, in no ISO list
    seas = [{"en": _english(a), "item": _item(a), "kind": "ocean" if a["featurecla"] == "ocean" else "sea",
             "rank": int(a["scalerank"]), "points": _label_points(g, SEA_POINTS)}
            for a, g in _records(natural_earth, "physical", "geography_marine_polys")
            if a["featurecla"] in SEA_KINDS]
    if not land or not places:
        raise NEDataError("Natural Earth's files hold no land or no places")
    return {"land": land, "borders": borders, "places": places, "rivers": rivers, "lakes": lakes,
            "countries": countries, "seas": seas}


def _names(features: Sequence[dict[str, Any]], wikidata: dict[str, tuple[str, str]], river: bool = False
           ) -> list[str]:
    return [romanian(*wikidata.get(f["item"], ("", "")), river=river) for f in features]


def build_table(features: dict[str, Any], wikidata: dict[str, tuple[str, str]], cldr: dict[str, str]
                ) -> dict[str, np.ndarray]:
    """The cache's arrays: *features* (see :func:`read_natural_earth`) with their names in English and in
    Romanian, from Wikidata's titles and labels (*wikidata*, by item) and CLDR's country names (*cldr*, by ISO
    code)."""
    places = features["places"]
    ro_places = [PLACE_NAMES.get(p["en"]) or ro or (p["local"] if latin(p["local"]) else "")
                 for p, ro in zip(places, _names(places, wikidata))]
    order = sorted(range(len(places)), key=lambda i: (places[i]["kind"] != "national_capital",
                                                      -places[i]["population"]))
    pieces = []
    for river, ro in zip(features["rivers"], _names(features["rivers"], wikidata, river=True)):
        en, fixed = RIVER_NAMES.get(river["local"], (river["en"], ro))
        key = river["local"] if river["local"] in RIVER_NAMES else river["item"] or river["en"]
        pieces.append((key, f"{en}\t{fixed}" if en or fixed else "", river["line"]))
    rivers = [r for r in merge_rivers(pieces) if r.km > 0]
    lakes = sorted(zip(features["lakes"], _names(features["lakes"], wikidata)), key=lambda w: -_km2(w[0]["polygon"]))
    countries = sorted(features["countries"], key=lambda c: -c["km2"])
    ro_countries = [cldr.get(c["iso"]) or ro for c, ro in zip(countries, _names(countries, wikidata))]
    seas = sorted(features["seas"], key=lambda s: s["rank"])
    labels = [(c["en"], ro, "country", c["km2"], c["points"]) for c, ro in zip(countries, ro_countries)]
    labels += [(s["en"], ro, s["kind"], s["rank"], s["points"]) for s, ro in zip(seas, _names(seas, wikidata))]
    labels = [l for l in labels if l[4]]
    packed = {k: _pack(v) for k, v in (("land", features["land"]), ("border", features["borders"]),
                                       ("river", [r.geometry for r in rivers]),
                                       ("lake", [w["polygon"] for w, _ in lakes]))}
    table: dict[str, np.ndarray] = {}
    for k, v in packed.items():
        table[f"{k}_wkb"], table[f"{k}_wkb_ends"] = v["wkb"], np.asarray(v["wkb_ends"])
    point_counts = [len(l[4]) for l in labels]
    return table | {
        "place_en": np.array([places[i]["en"] for i in order], dtype=str),
        "place_ro": np.array([ro_places[i] for i in order], dtype=str),
        "place_kinds": np.array([places[i]["kind"] for i in order], dtype=str),
        "place_population": np.array([places[i]["population"] for i in order], dtype=np.int64),
        "place_lon": np.array([places[i]["lon"] for i in order], dtype=float),
        "place_lat": np.array([places[i]["lat"] for i in order], dtype=float),
        "river_en": np.array([r.name.partition("\t")[0] for r in rivers], dtype=str),
        "river_ro": np.array([r.name.partition("\t")[2] for r in rivers], dtype=str),
        "river_km": np.array([r.km for r in rivers], dtype=float),
        "lake_en": np.array([w["en"] for w, _ in lakes], dtype=str),
        "lake_ro": np.array([ro for _, ro in lakes], dtype=str),
        "lake_km2": np.array([_km2(w["polygon"]) for w, _ in lakes], dtype=float),
        "lake_reservoir": np.array([w["reservoir"] for w, _ in lakes], dtype=bool),
        "label_en": np.array([l[0] for l in labels], dtype=str),
        "label_ro": np.array([l[1] for l in labels], dtype=str),
        "label_kinds": np.array([l[2] for l in labels], dtype=str),
        "label_rank": np.array([l[3] for l in labels], dtype=float),
        "label_points": np.array([p for l in labels for p in l[4]], dtype=float).reshape(-1, 2),
        "label_point_ends": np.cumsum(point_counts),
    }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "ne_local")


def ensure_ne_data(cache_dir: str | None = None) -> str:
    """Path to the cached base map data, reading Natural Earth and asking Wikidata if needed.

    Wikidata's answers are kept beside it (``wikidata_ro.json``), so the data
    is built again without asking twice.
    """
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"ne_local_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached Natural Earth data: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Reading Natural Earth's 1:10m land, water, borders and places (one-time, ~30 MB), and their "
                "Romanian names from Wikidata (a few minutes) …")
    try:
        features = read_natural_earth()
        items = [f["item"] for key in ("places", "rivers", "lakes", "countries", "seas")
                 for f in features[key] if f["item"]]
        table = build_table(features, _wikidata(items, cache_dir), cldr_territories("ro"))
    except NEDataError:
        raise
    except BordersError as exc:
        raise NEDataError(str(exc)) from exc
    except Exception as exc:                  # network, file and geometry errors alike
        raise NEDataError(f"Could not read Natural Earth's data: {exc}") from exc
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    logger.info("Natural Earth data ready: %s", path)
    return path


def _wikidata(items: Sequence[str], cache_dir: str) -> dict[str, tuple[str, str]]:
    """Wikidata's answers for *items* (see :func:`ask_wikidata`), those asked before read from *cache_dir*."""
    path = os.path.join(cache_dir, "wikidata_ro.json")
    known: dict[str, tuple[str, str]] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            known = {item: (title, label) for item, (title, label) in json.load(fh).items()}
    missing = sorted(set(items) - set(known))
    if missing:
        known |= ask_wikidata(missing)
        with open(path + ".part", "w", encoding="utf-8") as fh:
            json.dump(known, fh, ensure_ascii=False)
        os.replace(path + ".part", path)
    return known


def _unpacked(table: dict[str, np.ndarray], prefix: str) -> list[shapely.Geometry]:
    return _unpack({"wkb": table[f"{prefix}_wkb"], "wkb_ends": table[f"{prefix}_wkb_ends"]})


def load_base(language: str = "en", cache_dir: str | None = None) -> Base:
    """The land, borders, places, rivers, lakes, countries and seas to draw, named in *language*."""
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {', '.join(LANGUAGES)}, got {language!r}")
    with np.load(ensure_ne_data(cache_dir)) as data:
        table = {key: data[key] for key in data.files}
    places = [Place(str(n), str(k), int(p), float(x), float(y)) for n, k, p, x, y in
              zip(table[f"place_{language}"], table["place_kinds"], table["place_population"], table["place_lon"],
                  table["place_lat"]) if n]
    rivers = [Water(str(n), float(k), g) for n, k, g in
              zip(table[f"river_{language}"], table["river_km"], _unpacked(table, "river"))]
    water = [(Water(str(n), float(k), g), bool(r)) for n, k, g, r in
             zip(table[f"lake_{language}"], table["lake_km2"], _unpacked(table, "lake"), table["lake_reservoir"])]
    lakes, reservoirs = [w for w, r in water if not r], [w for w, r in water if r]
    points = np.split(table["label_points"], table["label_point_ends"][:-1])
    labels = [Label(str(n), str(k), float(r), tuple((float(x), float(y)) for x, y in p)) for n, k, r, p in
              zip(table[f"label_{language}"], table["label_kinds"], table["label_rank"], points) if n]
    credit = ATTRIBUTION + (ROMANIAN_ATTRIBUTION if language == "ro" else "")
    return Base(_unpacked(table, "land"), _unpacked(table, "border"), places, rivers, lakes, "", labels, credit,
                reservoirs)
