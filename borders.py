"""Historical borders: the states of any year, from Cliopatria or CShapes 2.0.

Data sources
------------
Cliopatria (Seshat Global History Databank): Bennett, J. S., Mutch, J.,
Tollefson, J., et al. (2025). Cliopatria - A geospatial database of world-wide
political entities from 3400BCE to 2024CE. *Scientific Data* 12, 247.
https://doi.org/10.1038/s41597-025-04516-9 — release v0.2.1, CC BY 4.0. Over
1,600 states and empires, each with the years its borders held; an empire's
parts (British Raj, British Africa …) name it as what they belong to.

CShapes 2.0: Schvitz, G., Girardin, L., Rüegger, S., Weidmann, N. B.,
Cederman, L.-E. & Gleditsch, K. S. (2022). Mapping the International System,
1886-2019: The CShapes 2.0 Dataset. *Journal of Conflict Resolution* 66(1),
144-161. https://doi.org/10.1177/00220027211013563 — independent states and
their colonies, protectorates, mandates and occupied lands, to the day, from
1886 to 2019. Read from the copy in the ``cshapes`` R package on CRAN
(GPL >= 2), which carries each territory's status and ruler; ETH Zurich's own
downloads leave those out.

Both are downloaded once (~46 MB and ~4 MB) and cached in a compact form.

Names in Romanian (``language="ro"``, CShapes only): Unicode CLDR 47, the
territory names of its Romanian locale (Unicode License v3), matched to
CShapes' English names through CLDR's English ones (:data:`CSHAPES_CODES` where
they differ). States that no longer exist, or bore another name at the map's
date, take their Romanian name from :data:`ROMANIAN_HISTORY` (the Ottoman
Empire before 1923, the Soviet Union, Czechoslovakia); a colony with no
Romanian name in either is drawn without one.
"""

from __future__ import annotations

import json
import logging
import lzma
import os
import re
import tarfile
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

import numpy as np
import shapely
from shapely.geometry import shape

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

import frame
from biomes import _download, _key, visible_points

logger = logging.getLogger(__name__)

CLIOPATRIA_VERSION = "v0.2.1"
CLIOPATRIA_URL = ("https://raw.githubusercontent.com/Seshat-Global-History-Databank/cliopatria/"
                  f"{CLIOPATRIA_VERSION}/cliopatria.geojson.zip")
# CRAN keeps the current release at the first address and moves it to the archive when superseded
CSHAPES_URLS = ("https://cran.r-project.org/src/contrib/cshapes_2.0.tar.gz",
                "https://cran.r-project.org/src/contrib/Archive/cshapes/cshapes_2.0.tar.gz")
CSHAPES_MEMBER = "cshapes/inst/extdata/cshapes_2_gw.topojson.xz"   # Gleditsch & Ward system membership
DATA_VERSION = 1          # bump when a cache changes, so old ones are rebuilt

CLIOPATRIA_ATTRIBUTION = (f"Borders: Cliopatria {CLIOPATRIA_VERSION}, Seshat Global History Databank "
                          "(Bennett et al. 2025), CC BY 4.0")
CSHAPES_ATTRIBUTION = "Borders: CShapes 2.0 (Schvitz et al. 2022), ETH Zurich"
CLDR_VERSION = "47.0.0"
CLDR_URL = ("https://raw.githubusercontent.com/unicode-org/cldr-json/" + CLDR_VERSION
            + "/cldr-json/cldr-localenames-full/main/{language}/territories.json")
CLDR_ATTRIBUTION = f"country names in Romanian: Unicode CLDR {CLDR_VERSION.rsplit('.', 1)[0]}"
LANGUAGES = ("en", "ro")

# CShapes' names whose ISO code CLDR's English names do not give
CSHAPES_CODES = {
    "Rumania": "RO", "German Federal Republic": "DE", "Italy/Sardinia": "IT", "Czech Republic": "CZ",
    "Bosnia-Herzegovina": "BA", "Congo": "CG", "Congo, Democratic Republic of (Zaire)": "CD",
    "Cote D'Ivoire": "CI", "East Timor": "TL", "French Guyana": "GF", "Korea, People's Republic of": "KP",
    "Korea, Republic of": "KR", "Kyrgyz Republic": "KG", "Macedonia (FYROM/North Macedonia)": "MK",
    "New Caledonia and Dependencies": "NC", "Palestine": "PS", "Reunion": "RE", "Surinam": "SR",
    "Swaziland (Eswatini)": "SZ", "Trinidad and Tobago": "TT", "Turkey (Ottoman Empire)": "TR",
    "United States of America": "US", "Vietnam, Democratic Republic of": "VN",
}
# Romanian names of states before a date (ISO, the day the name changed): (until, name) pairs, earliest first,
# "" for CLDR's name; after the last, CLDR's. Places Romanian writes as English does (Alaska, Tibet) run to 9999.
ROMANIAN_HISTORY: dict[str, tuple[tuple[str, str], ...]] = {
    "Turkey (Ottoman Empire)": (("1923-10-29", "Imperiul Otoman"),),
    "Russia (Soviet Union)": (("1917-11-07", "Imperiul Rus"), ("1922-12-30", "Rusia Sovietică"),
                              ("1991-12-26", "Uniunea Sovietică")),
    "Germany (Prussia)": (("1918-11-09", "Imperiul German"), ("9999-12-31", "Germania")),
    "German Federal Republic": (("1990-10-03", "Republica Federală Germania"),),
    "German Democratic Republic": (("9999-12-31", "Republica Democrată Germană"),),
    "Austria-Hungary": (("9999-12-31", "Austro-Ungaria"),),
    "Czechoslovakia": (("9999-12-31", "Cehoslovacia"),),
    "Yugoslavia": (("9999-12-31", "Iugoslavia"),),
    "Iran (Persia)": (("1935-03-21", "Persia"),),
    "Thailand": (("1939-06-24", "Siam"),),
    "Sri Lanka (Ceylon)": (("1972-05-22", "Ceylon"),),
    "Myanmar (Burma)": (("1989-06-18", "Birmania"),),
    "Congo, Democratic Republic of (Zaire)": (("1960-06-30", "Congo Belgian"), ("1971-10-27", ""),
                                              ("1997-05-17", "Zair")),
    "Zimbabwe (Rhodesia)": (("1980-04-18", "Rhodesia"),),
    "Tanzania (Tanganyika)": (("1964-04-26", "Tanganyika"),),
    "Swaziland (Eswatini)": (("2018-04-19", "Swaziland"),),
    "Palestine": (("1948-05-15", "Palestina"),),
    "Vietnam, Democratic Republic of": (("1976-07-02", "Republica Democrată Vietnam"),),
    "Korea": (("9999-12-31", "Coreea"),),
    **{same: (("9999-12-31", same),) for same in ("Aden", "Alaska", "Hawaii", "Tibet", "Zanzibar", "Sarawak",
                                                   "Danzig")},
}

SOURCES = ("auto", "cliopatria", "cshapes")
CLIOPATRIA_YEARS = (-3400, 2024)
CSHAPES_YEARS = (1886, 2019)

# Muted atlas colours; neighbouring states get different ones
PALETTE: tuple[tuple[int, int, int], ...] = (
    (232, 160, 160), (240, 200, 120), (170, 205, 140), (200, 170, 220),
    (245, 225, 130), (150, 200, 200), (215, 175, 140), (190, 210, 110),
)
# A dependency is drawn in its ruler's colour, this far toward white
DEPENDENCY_LIGHTEN = 0.45
# States sharing a border, or this close across a strait (degrees), get different colours
NEIGHBOUR_DEGREES = 0.1
# A state is named on the map when it covers at least this share of it
LABEL_MIN_SHARE = 0.002
BORDERS_KEY_MAX = 10


class BordersError(RuntimeError):
    """The border data could not be downloaded or read."""


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


class When(NamedTuple):
    """A moment to draw the borders of: its year (negative BCE) and, for CShapes, its day."""
    year: int
    date: str          # ISO date, mid-year unless given; "" before year 1
    label: str         # "1500", "500 BCE", "1 August 1914"


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def parse_when(spec: str | int) -> When:
    """The moment *spec* names: a year (``1500``, ``-500``, ``500 BC``, ``500 BCE``, ``AD 800``,
    ``800 CE``) or, from year 1, a date (``1914-08-01``).

    A year alone stands for 1 July of it. Raises ``ValueError`` for anything else.
    """
    text = str(spec).strip()
    if m := re.fullmatch(r"(\d{3,4})-(\d{2})-(\d{2})", text):
        year, month, day = (int(v) for v in m.groups())
        if not (1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError(f"Not a date: {spec!r}.")
        return When(year, f"{year:04d}-{month:02d}-{day:02d}", f"{day} {_MONTHS[month - 1]} {year}")
    m = re.fullmatch(r"(?i)(?:(AD|CE)\s*)?(-?\d{1,4})\s*(BCE|BC|AD|CE)?", text)
    if not m or (m.group(1) and m.group(2).startswith("-")):
        raise ValueError(f"Not a year: {spec!r}. Give a year (1500, -500 or 500 BC) or a date (1914-08-01).")
    year = int(m.group(2))
    era = (m.group(3) or m.group(1) or "").upper()
    if era in ("BC", "BCE"):
        if year <= 0:
            raise ValueError(f"Not a year: {spec!r}.")
        year = -year
    if year == 0:
        raise ValueError("There is no year 0: 1 BCE is followed by 1 CE.")
    label = f"{-year} BCE" if year < 0 else str(year)
    return When(year, f"{year:04d}-07-01" if year > 0 else "", label)


def choose_source(when: When, source: str = "auto") -> str:
    """``"cliopatria"`` or ``"cshapes"`` for *when*: CShapes in its years (1886-2019) unless told otherwise.

    Raises ``ValueError`` for a source that does not cover *when*.
    """
    if source not in SOURCES:
        raise ValueError(f"Unknown borders source {source!r}; use one of {', '.join(SOURCES)}.")
    if source == "auto":
        source = "cshapes" if CSHAPES_YEARS[0] <= when.year <= CSHAPES_YEARS[1] else "cliopatria"
    first, last = CSHAPES_YEARS if source == "cshapes" else CLIOPATRIA_YEARS
    if not first <= when.year <= last:
        span = f"{-first} BCE" if first < 0 else str(first)
        raise ValueError(f"{'CShapes 2.0' if source == 'cshapes' else 'Cliopatria'} covers {span} to {last}, "
                         f"not {when.label}.")
    return source


# ---------------------------------------------------------------------------
# Download and caching
# ---------------------------------------------------------------------------


class Polity(NamedTuple):
    """A state at a moment: its name, what it belongs to and its outline."""
    name: str
    group: str          # states of one group share a colour: an empire, or a ruler and its dependencies
    group_name: str
    dependent: bool     # a colony, protectorate, mandate or occupied land (CShapes)
    outline: shapely.Geometry


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "borders")


def _pack(outlines: Sequence[shapely.Geometry]) -> dict[str, np.ndarray]:
    """*outlines* as one WKB blob and where each ends, cleaned for Cartopy.

    Cartopy fills the outside of a clockwise ring (TopoJSON winds them that
    way), and of some rings with points a hair apart (where TopoJSON arcs
    join), which would tint every sea: outer rings are turned anticlockwise
    and points closer than 1e-7° merged.
    """
    oriented = shapely.orient_polygons(shapely.remove_repeated_points(np.array(outlines, dtype=object), 1e-7))
    wkb = [bytes(shapely.to_wkb(g)) for g in oriented]
    return {"wkb": np.frombuffer(b"".join(wkb), np.uint8), "wkb_ends": np.cumsum([len(w) for w in wkb])}


def _unpack(data: Any) -> list[shapely.Geometry]:
    blob = data["wkb"].tobytes()
    ends = data["wkb_ends"]
    return [shapely.from_wkb(blob[a:b]) for a, b in zip([0, *ends[:-1]], ends)]


def read_cliopatria(geojson: Any) -> dict[str, np.ndarray]:
    """The table of a Cliopatria GeoJSON (bytes or a binary file): each state's rows, without the
    groupings (named in brackets)."""
    data = json.loads(geojson) if isinstance(geojson, bytes) else json.load(geojson)
    names, starts, ends, groups, outlines = [], [], [], [], []
    group_types = {f["properties"]["Name"]: f["properties"]["Type"] for f in data["features"]
                   if f["properties"]["Name"].startswith("(")}
    for feature in data["features"]:
        props = feature["properties"]
        if props["Name"].startswith("(") or not feature.get("geometry"):
            continue
        # An empire's parts share its colour; an alliance or allegiance does not make one
        member = next((g for g in (props.get("MemberOf") or "").split(";")
                       if group_types.get(g.strip()) == "POLITY"), "")
        names.append(props["Name"])
        starts.append(int(props["FromYear"]))
        ends.append(int(props["ToYear"]))
        groups.append(member.strip())
        outlines.append(shape(feature["geometry"]))
    return {"names": np.array(names), "starts": np.array(starts), "ends": np.array(ends),
            "groups": np.array(groups), **_pack(outlines)}


def _topojson_geometries(topology: dict[str, Any], name: str) -> list[tuple[dict[str, Any], shapely.Geometry]]:
    """``(properties, outline)`` of each polygon in object *name* of a TopoJSON *topology* (untransformed)."""
    arcs = topology["arcs"]

    def ring(indices: list[int]) -> list[list[float]]:
        points: list[list[float]] = []
        for k, i in enumerate(indices):
            arc = arcs[i] if i >= 0 else arcs[~i][::-1]
            points.extend(arc if k == 0 else arc[1:])
        return points

    out = []
    for geometry in topology["objects"][name]["geometries"]:
        if geometry["type"] == "Polygon":
            coords: Any = [ring(r) for r in geometry["arcs"]]
        elif geometry["type"] == "MultiPolygon":
            coords = [[ring(r) for r in polygon] for polygon in geometry["arcs"]]
        else:
            continue
        out.append((geometry["properties"], shape({"type": geometry["type"], "coordinates": coords})))
    return out


def read_cshapes(topojson: bytes) -> dict[str, np.ndarray]:
    """The table of the CShapes TopoJSON (as in the R package)."""
    rows = _topojson_geometries(json.loads(topojson), "cshapes_2_gw")
    return {
        "names": np.array([p["country_name"] for p, _ in rows]),
        "starts": np.array([p["start"] for p, _ in rows]),
        "ends": np.array([p["end"] for p, _ in rows]),
        "codes": np.array([str(p["gwcode"]) for p, _ in rows]),
        "owners": np.array([str(p["owner"]) for p, _ in rows]),
        "dependent": np.array([p["status"] not in ("independent", "N/A") for p, _ in rows]),
        **_pack([g for _, g in rows]),
    }


def _fetch(urls: Sequence[str], dest: str, label: str) -> None:
    errors = []
    for url in urls:
        try:
            _download(url, dest, label=label)
            return
        except Exception as exc:              # the next address may have it
            errors.append(f"{url}: {exc}")
    raise BordersError("; ".join(errors))


def ensure_borders_data(source: str, cache_dir: str | None = None) -> str:
    """Path to the cached table of *source* (``"cliopatria"`` or ``"cshapes"``), downloading it if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    tag = f"cliopatria_{CLIOPATRIA_VERSION}" if source == "cliopatria" else "cshapes_2.0"
    path = os.path.join(cache_dir, f"{tag}_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached borders: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    download = os.path.join(cache_dir, f"{tag}.download")
    try:
        if source == "cliopatria":
            logger.info("Downloading Cliopatria %s (one-time, ~46 MB) …", CLIOPATRIA_VERSION)
            _fetch([CLIOPATRIA_URL], download, "Cliopatria")
            with zipfile.ZipFile(download) as archive:
                member = next(n for n in archive.namelist() if n.endswith(".geojson"))
                with archive.open(member) as fh:
                    table = read_cliopatria(fh)
        else:
            logger.info("Downloading CShapes 2.0 from CRAN (one-time, ~4 MB) …")
            _fetch(CSHAPES_URLS, download, "CShapes")
            with tarfile.open(download) as archive:
                member = archive.extractfile(CSHAPES_MEMBER)
                if member is None:
                    raise BordersError(f"{CSHAPES_MEMBER} is missing from the cshapes package")
                table = read_cshapes(lzma.decompress(member.read()))
    except BordersError:
        raise
    except Exception as exc:                  # network, archive, JSON and geometry errors alike
        raise BordersError(f"Could not read the {source} borders: {exc}") from exc
    finally:
        if os.path.exists(download):
            os.remove(download)
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    logger.info("Borders ready: %s", path)
    return path


def load_polities(when: When, source: str, cache_dir: str | None = None) -> list[Polity]:
    """The states of *source* at *when*, largest first."""
    with np.load(ensure_borders_data(source, cache_dir)) as data:
        table = {key: data[key] for key in data.files}
    if source == "cliopatria":
        active = np.nonzero((table["starts"] <= when.year) & (when.year <= table["ends"]))[0]
        outlines = _unpack(table)
        polities = [Polity(str(table["names"][i]), str(table["groups"][i]) or str(table["names"][i]),
                           (str(table["groups"][i]).strip("()") or str(table["names"][i])), False, outlines[i])
                    for i in active]
    else:
        active = np.nonzero((table["starts"] <= when.date) & (when.date <= table["ends"]))[0]
        outlines = _unpack(table)
        # A dependency takes its ruler's colour and name: the ruler as it was then, or as last known
        ruler_names: dict[str, str] = {}
        for i in np.argsort(table["starts"]):
            if not table["dependent"][i]:
                ruler_names.setdefault(str(table["codes"][i]), str(table["names"][i]))
                if table["starts"][i] <= when.date:
                    ruler_names[str(table["codes"][i])] = str(table["names"][i])
        polities = []
        for i in active:
            dependent = bool(table["dependent"][i])
            group = str(table["owners"][i]) if dependent else str(table["codes"][i])
            polities.append(Polity(str(table["names"][i]), group, ruler_names.get(group, str(table["names"][i])),
                                   dependent, outlines[i]))
    return sorted(polities, key=lambda p: float(shapely.area(p.outline)), reverse=True)


# ---------------------------------------------------------------------------
# Names in Romanian
# ---------------------------------------------------------------------------


def cldr_territories(language: str, cache_dir: str | None = None) -> dict[str, str]:
    """CLDR's names of the countries (ISO 3166 codes) in *language*, downloaded once."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"cldr_{CLDR_VERSION}_{language}_territories.json")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        try:
            _fetch([CLDR_URL.format(language=language)], path, f"CLDR {language}")
        except BordersError as exc:
            raise BordersError(f"Could not read CLDR's country names: {exc}") from exc
    try:
        with open(path, encoding="utf-8") as fh:
            names = json.load(fh)["main"][language]["localeDisplayNames"]["territories"]
    except (OSError, ValueError, KeyError) as exc:
        raise BordersError(f"CLDR's country names are not as expected: {exc!r}") from exc
    return {code: name for code, name in names.items() if re.fullmatch(r"[A-Z]{2}", code)}


def romanian_name(name: str, date: str, romanian: dict[str, str], english: dict[str, str]) -> str:
    """The Romanian name of the state CShapes calls *name* on *date* (ISO), ``""`` if none is known.

    :data:`ROMANIAN_HISTORY` first, for a state that bore another name then;
    else CLDR's, through the ISO code of :data:`CSHAPES_CODES` or of CLDR's
    English name (*name* without what it says in brackets: ``Iran (Persia)``).
    """
    for until, written in ROMANIAN_HISTORY.get(name, ()):
        if date < until:
            if written:
                return written
            break
    codes = {n.lower(): code for code, n in english.items()}
    code = (CSHAPES_CODES.get(name) or codes.get(name.lower())
            or codes.get(re.sub(r"\s*\(.*\)$", "", name).strip().lower()))
    return romanian.get(code, "") if code else ""


def in_language(polities: Sequence[Polity], when: When, language: str, source: str,
                cache_dir: str | None = None) -> list[Polity]:
    """*polities* with their names, and their groups', in *language* (``"en"``: as the data has them).

    Only CShapes' are put into Romanian; a state with no Romanian name is left
    unnamed on the map, and a group keeps its English name in the key.
    Cliopatria's names stay as they are.
    """
    if language == "en" or source != "cshapes":
        if language != "en":
            logger.info("Cliopatria's names are given as it has them, in English.")
        return list(polities)
    romanian, english = cldr_territories("ro", cache_dir), cldr_territories("en", cache_dir)
    names = {n: romanian_name(n, when.date, romanian, english)
             for n in {p.name for p in polities} | {p.group_name for p in polities}}
    unnamed = sorted(n for n, ro in names.items() if not ro)
    if unnamed:
        logger.info("No Romanian name for %d states, left unnamed: %s", len(unnamed), ", ".join(unnamed[:8]))
    return [p._replace(name=names[p.name], group_name=names[p.group_name] or p.group_name) for p in polities]


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------


def colour_groups(polities: Sequence[Polity], palette: Sequence[tuple[int, int, int]] = PALETTE
                  ) -> dict[str, tuple[int, int, int]]:
    """A colour for each group, different from its neighbours' where the palette allows.

    Groups are coloured largest first, each with the colour its neighbours
    use least and, among those, the one used least so far, so the palette is
    spread over the map.
    """
    outlines = np.array([p.outline for p in polities], dtype=object)
    tree = shapely.STRtree(outlines)
    left, right = tree.query(outlines, predicate="dwithin", distance=NEIGHBOUR_DEGREES)
    neighbours: dict[str, set[str]] = {p.group: set() for p in polities}
    for a, b in zip(left, right):
        ga, gb = polities[a].group, polities[b].group
        if ga != gb:
            neighbours[ga].add(gb)
            neighbours[gb].add(ga)
    area: Counter[str] = Counter()
    for p in polities:
        area[p.group] += float(shapely.area(p.outline))
    colours: dict[str, tuple[int, int, int]] = {}
    overall: Counter[tuple[int, int, int]] = Counter()
    for group, _ in area.most_common():
        used = Counter(colours[n] for n in neighbours[group] if n in colours)
        colours[group] = min(palette, key=lambda c: (used[c], overall[c], palette.index(c)))
        overall[colours[group]] += 1
    return colours


def _rgb(colour: Sequence[int]) -> tuple[float, float, float]:
    return colour[0] / 255, colour[1] / 255, colour[2] / 255


def fill_colour(polity: Polity, colours: dict[str, tuple[int, int, int]]) -> tuple[float, float, float]:
    """*polity*'s fill: its group's colour, lighter for a dependency."""
    rgb = np.array(colours[polity.group], float) / 255
    if polity.dependent:
        rgb = rgb + (1 - rgb) * DEPENDENCY_LIGHTEN
    return float(rgb[0]), float(rgb[1]), float(rgb[2])


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def visible_polities(ax: GeoAxes, polities: Sequence[Polity], samples: int = 200
                     ) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """The sample points (see :func:`biomes.visible_points`) each state covers on *ax*, as ``index -> (x, y)``."""
    lon, lat, x, y = visible_points(ax, samples)
    if not len(lon) or not polities:
        return {}
    tree = shapely.STRtree(np.array([p.outline for p in polities], dtype=object))
    point_index, polity_index = tree.query(shapely.points(lon, lat), predicate="within")
    found: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for i in np.unique(polity_index):
        mine = point_index[polity_index == i]
        found[int(i)] = (x[mine], y[mine])
    return found


def _label_size(share: float) -> float:
    """Font size (points) for a state covering *share* of the map."""
    return float(np.clip(8 + 40 * np.sqrt(share), 8, 16))


def draw_borders(ax: GeoAxes, polities: Sequence[Polity], colours: dict[str, tuple[int, int, int]],
                 alpha: float = 0.55, labels: bool = True, samples: int = 200) -> dict[int, int]:
    """Fill and outline *polities* on *ax* and name those that cover enough of it.

    Names go at the middle of what shows of each state, biggest first, and
    one that would overlap a name already placed is left out. Returns how
    many sample points each state covers (index into *polities*).
    """
    by_colour: dict[tuple[float, float, float], list[shapely.Geometry]] = {}
    for polity in polities:
        by_colour.setdefault(fill_colour(polity, colours), []).append(polity.outline)
    for rgb, outlines in by_colour.items():
        ax.add_geometries(outlines, crs=ccrs.PlateCarree(), facecolor=(*rgb, alpha), edgecolor="none", zorder=5)
    ax.add_geometries([p.outline for p in polities], crs=ccrs.PlateCarree(), facecolor="none",
                      edgecolor="#3a3a3a", linewidth=0.7, zorder=6)

    found = visible_polities(ax, polities, samples)
    counts = {i: len(xy[0]) for i, xy in found.items()}
    if not labels or not counts:
        return counts
    total = frame.coverage(ax) * samples ** 2
    placed: list[tuple[float, float, float, float]] = []
    to_pixels = ax.transData
    dpi = ax.figure.dpi
    for i in sorted(counts, key=counts.__getitem__, reverse=True):
        share = counts[i] / total
        if share < LABEL_MIN_SHARE:
            break
        if not polities[i].name:
            continue
        xs, ys = found[i]
        # The sample point nearest the middle of the state's visible part lies inside it
        k = int(np.argmin((xs - xs.mean()) ** 2 + (ys - ys.mean()) ** 2))
        size = _label_size(share)
        px, py = to_pixels.transform((xs[k], ys[k]))
        half_w = 0.3 * size * len(polities[i].name) * dpi / 72
        half_h = 0.6 * size * dpi / 72
        box = (px - half_w, py - half_h, px + half_w, py + half_h)
        if not frame.box_inside(ax, box, 0.97):  # names must fit on the map as drawn
            continue
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
            continue
        placed.append(box)
        ax.text(xs[k], ys[k], polities[i].name, transform=ax.projection, fontsize=size, color="#222222",
                ha="center", va="center", zorder=8, clip_on=True)
    return counts


def add_borders_legend(ax: GeoAxes, polities: Sequence[Polity], counts: Iterable[dict[int, int]],
                       colours: dict[str, tuple[int, int, int]], when: When, source: str,
                       y: float = -0.01, x: float = 0.5, limit: int = BORDERS_KEY_MAX) -> Legend | None:
    """A key naming the largest states and empires on the map, in their colours.

    *counts* are :func:`draw_borders`'s results, one per globe. Returns
    ``None`` when no state shows.
    """
    shares: Counter[str] = Counter()
    names: dict[str, str] = {}
    for per_globe in counts:
        for i, n in per_globe.items():
            shares[polities[i].group] += n
            names[polities[i].group] = polities[i].group_name
    if not shares:
        return None
    top = [group for group, _ in shares.most_common(limit)]
    handles: list[mpatches.Patch] = [
        mpatches.Patch(facecolor=_rgb(colours[g]), edgecolor="#333333", linewidth=0.6) for g in top]
    labels = [names[g] for g in top]
    dataset = "Cliopatria" if source == "cliopatria" else "CShapes 2.0"
    title = f"Borders in {when.label} ({dataset})"
    if len(shares) > limit:
        title += f": the {limit} largest of {len(shares)} on the map"
    if source == "cshapes" and any(p.dependent for p in polities):
        title += "\nlighter shades: their colonies, protectorates, mandates and occupied lands"
    return _key(ax, handles, labels, title, min(len(top), 3), x, y)


def attribution(source: str) -> str:
    """The credit line for *source*."""
    return CLIOPATRIA_ATTRIBUTION if source == "cliopatria" else CSHAPES_ATTRIBUTION
