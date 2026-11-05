"""Bird tracks: where tagged birds flew, from the open studies in the Movebank Data Repository.

Data source
-----------
The Movebank Data Repository (https://datarepository.movebank.org/), run by
the Max Planck Institute of Animal Behavior, publishes the GPS, Argos and
geolocator tracks of animal-tracking studies as citable data packages, each
with a DOI. Only the packages released under CC0 1.0 or CC BY 4.0 are used:
those under CC BY-NC are left out, so the maps can be used for anything.

The repository's API lists the packages and the species each tracked; GBIF's
backbone taxonomy tells which are birds and gives their English names. The
catalogue of the bird studies (a few hundred, worldwide) is built once, in a
few minutes, and refreshed after :data:`CATALOGUE_DAYS` days. Each study's
location file is downloaded once when a map first needs it, cleaned (fixes
the authors marked as outliers, impossible positions and isolated spikes are
dropped), thinned to one fix every :data:`THIN_HOURS` hours per bird and
cached compactly under ``~/.cache/ortho_tiles/birds/``. Files larger than
``max_mb`` (default :data:`DEFAULT_MAX_MB` MB) are left out: the whole
repository is tens of GB, the files up to 50 MB about 1.7 GB.

Each bird's track is drawn as a line coloured by its species, the species
with the most birds on the map keyed. Light-level geolocator tracks, accurate
only to ~200 km, are left out unless asked for. The studies drawn are listed,
with their DOIs and licences, in a text file next to the map.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import numpy as np

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

import frame

logger = logging.getLogger(__name__)

REPOSITORY_API = "https://datarepository.movebank.org/server/api"
GBIF_API = "https://api.gbif.org/v1"
DATA_VERSION = 3                 # of the cached tracks
CATALOGUE_VERSION = 2            # of the cached catalogue
CATALOGUE_DAYS = 90
# The repository's licence names, and how the map credits them; packages under any other are left out
OPEN_LICENCES = {"CC0 1.0 Universal": "CC0 1.0", "Attribution 4.0 International": "CC BY 4.0"}

DEFAULT_MAX_MB = 50.0
MIN_MAX_MB, MAX_MAX_MB = 1.0, 5000.0
THIN_HOURS = 6                   # one fix kept per bird in each such window
GAP_DAYS = 10                    # a track is broken where no fix was kept for this long …
JUMP_KM, JUMP_KMH = 300.0, 20.0  # … and where it jumps this far more slowly than this: the tag fell silent
SPIKE_KM = 100.0                 # a fix this far from both neighbours, which are closer together than …
SPIKE_RATIO = 0.5                # … this share of the detour, is a spike
PAIR_SPIKE_KM = 1000.0           # two fixes together this far out and back (an Argos fix on its mirror side)
MAX_SPEED_KMH = 100.0            # faster than this between two fixes over SPEED_MIN_KM, the later is dropped
SPEED_MIN_KM = 50.0
DOWNLOAD_WORKERS = 4

# A location file: a CSV (or a zipped CSV) that is not one of a package's other tables
_OTHER_TABLES = re.compile(r"reference-data|readme|acceleration|accessory|light|magnet|orientation|gyro|"
                           r"barometer|raw|heart|temperature|proximity", re.IGNORECASE)
GEOLOCATOR_SENSORS = frozenset({"solar-geolocator", "solar-geolocator-raw", "solar-geolocator-twilight"})
SKIPPED_SENSORS = frozenset({"bird-ring", "natural-mark"})   # ring recoveries and sightings: no track

MAX_KEYED_SPECIES = 14
PALETTE = ("#b2182b", "#f28e2b", "#1f77b4", "#2ca02c", "#9467bd", "#17becf", "#e377c2", "#8c564b",
           "#bcbd22", "#08306b", "#6a3d9a", "#1b9e77", "#fdbf11", "#252525")
OTHER_COLOUR = "#8c8c8c"
LINE_WIDTH = 0.5
LINE_ALPHA = 0.6
OTHER_ALPHA = 0.35


class BirdDataError(RuntimeError):
    """The repository could not be reached, or sent something unreadable."""


class Study(NamedTuple):
    """One data package of the repository: a tracking study, or part of one."""
    uuid: str
    title: str
    citation: str            # the package's own citation, authors, year, title, repository, DOI
    doi: str                 # ``10.5441/…``
    licence: str             # a value of :data:`OPEN_LICENCES`
    names: tuple[str, ...]   # the taxa it tracked, as the repository lists them
    files: tuple[tuple[str, int, str], ...]   # its location files: name, size in bytes, download URL


class Catalogue(NamedTuple):
    studies: tuple[Study, ...]       # the open studies that tracked birds
    taxa: dict[str, dict[str, Any]]  # name -> {"bird": bool, "keys": [GBIF keys], "name": str, "common": str|None}
    built: str                       # YYYY-MM-DD


class Birds(NamedTuple):
    """Bird tracks one after another: track *i* is fixes ``starts[i]`` to ``starts[i + 1]``, sorted by time."""
    lon: np.ndarray                  # float32
    lat: np.ndarray                  # float32
    time: np.ndarray                 # int64 seconds since 1970 (UTC)
    starts: np.ndarray               # int64, one more than the tracks
    species: np.ndarray              # int32 per track, into *names*
    study: np.ndarray                # int32 per track, into *studies*
    geolocator: np.ndarray           # bool per track: light-level geolocation (~200 km)
    names: tuple[str, ...]           # scientific names
    common: tuple[str | None, ...]   # their English names, where GBIF has one
    studies: tuple[Study, ...]


class Drawn(NamedTuple):
    """What :func:`draw_birds` put on the map: the tracks in sight and the keyed species' colours."""
    seen: np.ndarray                 # bool per track
    colours: dict[int, str]          # species index -> colour, for the keyed species


# ---------------------------------------------------------------------------------------------------------------
# Options

def parse_birds(text: str | None) -> tuple[str, ...]:
    """The bird names in ``"white stork, black kite"``; ``()`` for every bird (``None``, blank or ``"all"``).

    Raises ``ValueError`` when ``all`` is mixed with names.
    """
    if text is None:
        return ()
    names = list(dict.fromkeys(" ".join(part.split()) for part in str(text).split(",")))
    names = [n for n in names if n]
    if not names or [n.lower() for n in names] == ["all"]:
        return ()
    if any(n.lower() == "all" for n in names):
        raise ValueError(f"birds must be 'all' or bird names separated by commas, not {text!r}")
    return tuple(names)


def check_max_mb(max_mb: float) -> None:
    if not (math.isfinite(max_mb) and MIN_MAX_MB <= max_mb <= MAX_MAX_MB):
        raise ValueError(f"bird_max_mb must be between {MIN_MAX_MB:g} and {MAX_MAX_MB:g} MB, got {max_mb:g}")


# ---------------------------------------------------------------------------------------------------------------
# The catalogue

def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "birds")


def _get_json(url: str, timeout: float = 120, attempts: int = 3) -> Any:
    """*url*'s JSON, trying again after a pause when the connection drops (the repository's often does)."""
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0", "Accept": "application/json"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if attempt == attempts - 1:
                raise BirdDataError(f"Could not read {url}: {exc}") from exc
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


def _values(metadata: dict[str, Any], key: str) -> list[str]:
    return [str(v.get("value", "")) for v in metadata.get(key, []) if v.get("value")]


def _plain(html: str) -> str:
    """A citation without its HTML link markup."""
    return " ".join(re.sub(r"<[^>]+>", "", html).split())


def read_package(item: dict[str, Any]) -> Study | None:
    """The study an API item describes, without its files; ``None`` unless it is under an open licence."""
    metadata = item.get("metadata", {})
    licence = OPEN_LICENCES.get(next(iter(_values(metadata, "dc.rights")), ""))
    if licence is None:
        return None
    title = next(iter(_values(metadata, "dc.title")), "")
    doi = next(iter(_values(metadata, "dc.identifier.doi")), "").removeprefix("doi:")
    cited = next(iter(_values(metadata, "mdr.citation.CSE")), "")
    if cited:
        citation = _plain(cited)
    else:
        authors = ", ".join(_values(metadata, "dc.contributor.author"))
        year = next(iter(_values(metadata, "dc.date.issued")), "")[:4]
        citation = f"{authors}. {year}. {title}. Movebank Data Repository. https://doi.org/{doi}"
    names = tuple(dict.fromkeys(_values(metadata, "dwc.ScientificName")))
    return Study(str(item.get("uuid", "")), title, citation, doi, licence, names, ())


def location_files(bitstreams: Iterable[dict[str, Any]]) -> tuple[tuple[str, int, str], ...]:
    """The location files among a package's files: the CSVs, plain or zipped, that are not its other tables."""
    files = []
    for b in bitstreams:
        name = str(b.get("name", ""))
        base = name[:-4] if name.lower().endswith(".zip") else name
        if not base.lower().endswith(".csv") or _OTHER_TABLES.search(base[:-4]):
            continue
        href = b.get("_links", {}).get("content", {}).get("href")
        if href:
            files.append((name, int(b.get("sizeBytes") or 0), str(href)))
    return tuple(files)


def classify(name: str) -> dict[str, Any] | None:
    """GBIF's view of a taxon *name*: whether it is a bird, its keys (species to class) and English name.

    ``None`` when GBIF cannot be reached; an unknown name is no bird.
    """
    try:
        match = _get_json(f"{GBIF_API}/species/match?{urllib.parse.urlencode({'name': name})}", attempts=2)
    except BirdDataError:
        return None
    if match.get("matchType") == "NONE" or not match.get("usageKey"):
        return {"bird": False, "keys": [], "name": name, "common": None}
    keys = [int(match[k]) for k in ("usageKey", "acceptedUsageKey", "speciesKey", "genusKey", "familyKey",
                                     "orderKey", "classKey") if match.get(k)]
    try:
        named = _get_json(f"{GBIF_API}/species/{match.get('speciesKey') or match['usageKey']}/vernacularNames"
                          "?limit=500", attempts=2).get("results", [])
    except BirdDataError:
        named = []
    return {"bird": match.get("class") == "Aves", "keys": sorted(set(keys)),
            "name": match.get("species") or match.get("canonicalName") or name, "common": english_name(named)}


def english_name(names: Iterable[dict[str, Any]]) -> str | None:
    """A bird's English name among GBIF's vernacular names: the IOC World Bird List's, else the commonest."""
    english = [n for n in names if n.get("language") == "eng" and n.get("vernacularName")]
    for n in english:
        if str(n.get("source", "")).startswith("IOC World Bird List"):
            return str(n["vernacularName"])
    if not english:
        return None
    counts: dict[str, int] = {}
    for n in english:
        key = str(n["vernacularName"]).lower()
        counts[key] = counts.get(key, 0) + 1
    best = max(counts, key=lambda k: counts[k])          # the first of the most frequent, as GBIF lists them
    spellings = [str(n["vernacularName"]) for n in english if str(n["vernacularName"]).lower() == best]
    return max(set(spellings), key=lambda v: (spellings.count(v), v != v.lower()))


def _classify_all(names: Iterable[str], taxa: dict[str, dict[str, Any]]) -> None:
    """Add GBIF's view of each new name in *names* to *taxa*, leaving out those GBIF did not answer for."""
    new = sorted({n for n in names if n and n not in taxa})
    if not new:
        return
    with ThreadPoolExecutor(8) as pool:
        for name, found in zip(new, pool.map(classify, new)):
            if found is not None:
                taxa[name] = found


def fetch_catalogue(taxa: dict[str, dict[str, Any]] | None = None) -> Catalogue:
    """List the repository's open bird studies and their location files (a few minutes). Raises BirdDataError."""
    taxa = dict(taxa or {})
    studies: list[Study] = []
    page, pages = 0, 1
    while page < pages:
        query = urllib.parse.urlencode({"dsoType": "ITEM", "f.entityType": "Datapackage,equals", "size": 100,
                                        "page": page})
        found = _get_json(f"{REPOSITORY_API}/discover/search/objects?{query}")["_embedded"]["searchResult"]
        pages = int(found["page"]["totalPages"])
        for result in found.get("_embedded", {}).get("objects", []):
            study = read_package(result["_embedded"]["indexableObject"])
            if study is not None and study.uuid:
                studies.append(study)
        page += 1
    logger.info("Bird tracks: %d open data packages; asking GBIF which tracked birds …", len(studies))
    _classify_all((n for s in studies for n in s.names), taxa)
    birds = [s for s in studies if any(taxa.get(n, {}).get("bird") for n in s.names)]
    logger.info("Bird tracks: listing the files of %d bird studies …", len(birds))

    def with_files(study: Study) -> Study:
        bundles = _get_json(f"{REPOSITORY_API}/core/items/{study.uuid}/bundles?embed=bitstreams")
        files: tuple[tuple[str, int, str], ...] = ()
        for bundle in bundles.get("_embedded", {}).get("bundles", []):
            if bundle.get("name") != "ORIGINAL":
                continue
            embedded = bundle.get("_embedded", {}).get("bitstreams", {})
            listed = embedded.get("_embedded", {}).get("bitstreams", [])
            if int(embedded.get("page", {}).get("totalElements", len(listed))) > len(listed):
                more = _get_json(f"{bundle['_links']['bitstreams']['href']}?size=500")
                listed = more.get("_embedded", {}).get("bitstreams", [])
            files += location_files(listed)
        return study._replace(files=files)

    with ThreadPoolExecutor(8) as pool:
        listed = list(pool.map(with_files, birds))
    kept = tuple(s for s in listed if s.files)
    return Catalogue(kept, taxa, datetime.date.today().isoformat())


def _catalogue_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"catalogue_v{CATALOGUE_VERSION}.json")


def _read_catalogue(path: str) -> Catalogue | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        studies = tuple(Study(s["uuid"], s["title"], s["citation"], s["doi"], s["licence"], tuple(s["names"]),
                              tuple((n, int(size), url) for n, size, url in s["files"])) for s in data["studies"])
        return Catalogue(studies, dict(data["taxa"]), str(data["built"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_catalogue(catalogue: Catalogue, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"version": CATALOGUE_VERSION, "built": catalogue.built, "taxa": catalogue.taxa,
            "studies": [s._asdict() for s in catalogue.studies]}
    part = path + ".part"
    with open(part, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(part, path)


def load_catalogue(cache_dir: str | None = None, max_age_days: int = CATALOGUE_DAYS,
                   fetch: Callable[..., Catalogue] = fetch_catalogue) -> Catalogue:
    """The cached catalogue of open bird studies, built or refreshed when missing or older than *max_age_days*.

    When the repository cannot be reached, an older catalogue is used if there
    is one. Raises :class:`BirdDataError` otherwise.
    """
    cache_dir = cache_dir or _default_cache_dir()
    path = _catalogue_path(cache_dir)
    cached = _read_catalogue(path)
    if cached is not None:
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(cached.built)).days
        except ValueError:
            age = max_age_days + 1
        if age <= max_age_days:
            return cached
    logger.info("Bird tracks: listing the open studies in the Movebank Data Repository (a few minutes, "
                "then cached for %d days) …", max_age_days)
    try:
        catalogue = fetch(cached.taxa if cached else None)
    except (BirdDataError, KeyError, TypeError, ValueError) as exc:
        if cached is not None:
            logger.warning("Could not refresh the list of bird studies (%s); using the one of %s.", exc, cached.built)
            return cached
        raise BirdDataError(f"Could not list the bird studies in the Movebank Data Repository: {exc}") from exc
    _write_catalogue(catalogue, path)
    return catalogue


# ---------------------------------------------------------------------------------------------------------------
# Reading and cleaning a location file

def _km(lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray) -> np.ndarray:
    """Great-circle distances (km) between the points, element by element."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def clean_track(t: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """The indices of one bird's fixes to keep, in time order: one per :data:`THIN_HOURS`, spikes dropped.

    A spike is a fix :data:`SPIKE_KM` or more from both its neighbours while
    they are less than :data:`SPIKE_RATIO` of that apart, and so is a pair of
    fixes :data:`PAIR_SPIKE_KM` out and back (Argos sometimes puts a few fixes
    on the wrong side of the satellite's track); a fix reached from the one before at more than
    :data:`MAX_SPEED_KMH` over :data:`SPEED_MIN_KM` or more is dropped too.
    """
    order = np.argsort(t, kind="stable")
    bucket = t[order] // (THIN_HOURS * 3600)
    keep = order[np.r_[True, bucket[1:] != bucket[:-1]]] if len(order) else order
    for _ in range(3):                                   # spikes, a few passes for neighbouring ones
        if len(keep) < 3:
            break
        x, y = lon[keep], lat[keep]
        before = _km(x[:-2], y[:-2], x[1:-1], y[1:-1])
        after = _km(x[1:-1], y[1:-1], x[2:], y[2:])
        across = _km(x[:-2], y[:-2], x[2:], y[2:])
        spike = (before > SPIKE_KM) & (after > SPIKE_KM) & (across < SPIKE_RATIO * np.minimum(before, after))
        if not spike.any():
            break
        keep = np.delete(keep, np.flatnonzero(spike) + 1)
    for _ in range(3):                                   # pairs of spikes
        if len(keep) < 4:
            break
        x, y = lon[keep], lat[keep]
        out = _km(x[:-3], y[:-3], x[1:-2], y[1:-2])
        back = _km(x[2:-1], y[2:-1], x[3:], y[3:])
        across = _km(x[:-3], y[:-3], x[3:], y[3:])
        pair = (out > PAIR_SPIKE_KM) & (back > PAIR_SPIKE_KM) & (across < SPIKE_RATIO * np.minimum(out, back))
        if not pair.any():
            break
        first = np.flatnonzero(pair)
        keep = np.delete(keep, np.unique(np.r_[first + 1, first + 2]))
    for _ in range(10):                                  # impossible speeds: drop the later fix
        if len(keep) < 2:
            break
        x, y, s = lon[keep], lat[keep], t[keep]
        km = _km(x[:-1], y[:-1], x[1:], y[1:])
        hours = np.maximum((s[1:] - s[:-1]) / 3600, 1 / 60)
        fast = (km > SPEED_MIN_KM) & (km / hours > MAX_SPEED_KMH)
        if not fast.any():
            break
        keep = np.delete(keep, np.flatnonzero(fast) + 1)
    return keep


def _seconds(stamps: Sequence[str]) -> np.ndarray:
    """Movebank timestamps (``2020-01-31 12:00:00.000``, UTC) as seconds since 1970; ``NaT`` ones as the minimum."""
    return np.array([s[:19] for s in stamps], dtype="datetime64[s]").astype(np.int64)


def read_locations(lines: Iterable[str], default_taxon: str = "") -> dict[tuple[str, str, bool], np.ndarray]:
    """The cleaned fixes of each bird in a Movebank location CSV, as (time, lon, lat) rows.

    Keyed by (taxon, bird, geolocator). Fixes marked invisible or as
    outliers, without a valid position or time, at (0, 0), or from ring
    recoveries are left out. A file without positions gives nothing.
    """
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        return {}
    column = {name.strip(): i for i, name in enumerate(header)}
    if "location-long" not in column or "location-lat" not in column or "timestamp" not in column:
        return {}

    def cell(row: list[str], name: str) -> str:
        i = column.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    fixes: dict[tuple[str, str, bool], tuple[list[str], list[float], list[float]]] = {}
    for row in reader:
        if cell(row, "visible").lower() == "false" or "true" in (cell(row, "manually-marked-outlier").lower(),
                                                                cell(row, "algorithm-marked-outlier").lower()):
            continue
        sensor = cell(row, "sensor-type").lower()
        if sensor in SKIPPED_SENSORS or cell(row, "argos:lc").upper() == "Z":
            continue
        try:
            lon, lat = float(cell(row, "location-long")), float(cell(row, "location-lat"))
        except ValueError:
            continue
        stamp = cell(row, "timestamp")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90) or (lon == 0 and lat == 0) or len(stamp) < 10:
            continue
        bird = cell(row, "individual-local-identifier") or cell(row, "tag-local-identifier") or "?"
        taxon = cell(row, "individual-taxon-canonical-name") or default_taxon
        times, lons, lats = fixes.setdefault((taxon, bird, sensor in GEOLOCATOR_SENSORS), ([], [], []))
        times.append(stamp)
        lons.append(lon)
        lats.append(lat)
    out: dict[tuple[str, str, bool], np.ndarray] = {}
    for key, (times, lons, lats) in fixes.items():
        try:
            t = _seconds(times)
        except ValueError:
            continue
        x, y = np.array(lons), np.array(lats)
        valid = t > np.iinfo(np.int64).min
        t, x, y = t[valid], x[valid], y[valid]
        keep = clean_track(t, x, y)
        if len(keep) >= 2:
            out[key] = np.column_stack([t[keep], x[keep], y[keep]])
    return out


def _download(url: str, dest: str, label: str, timeout: float = 300, attempts: int = 3) -> None:
    """Download *url* to *dest*, trying again after a pause when the connection drops (the repository's often does)."""
    for attempt in range(attempts):
        try:
            _download_once(url, dest, label, timeout)
            return
        except (urllib.error.URLError, OSError, BirdDataError) as exc:
            if attempt == attempts - 1:
                raise BirdDataError(f"Could not download {label!r}: {exc}") from exc
            time.sleep(2 ** (attempt + 1))


def _download_once(url: str, dest: str, label: str, timeout: float) -> None:
    """Download *url* to *dest* through a ``.part`` file, logging the progress of a large one."""
    part = dest + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done, shown = 0, 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total > 50 << 20 and done * 4 // total > shown:
                    shown = done * 4 // total
                    logger.info("  %s: %d of %d MB", label, done >> 20, total >> 20)
        if total and done != total:
            raise BirdDataError(f"incomplete download ({done} of {total} bytes)")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def _file_path(study: Study, url: str, cache_dir: str) -> str:
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    return os.path.join(cache_dir, "tracks", f"{study.uuid}_{digest}_v{DATA_VERSION}.npz")


def read_file(path: str, name: str, default_taxon: str = "") -> dict[tuple[str, str, bool], np.ndarray]:
    """The cleaned tracks of a downloaded location file, a CSV or a zip holding one."""
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.namelist() if m.lower().endswith(".csv")]
            if not members:
                return {}
            with archive.open(members[0]) as raw:
                return read_locations(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""),
                                      default_taxon)
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return read_locations(f, default_taxon)


def ensure_file(study: Study, file: tuple[str, int, str], cache_dir: str,
                download: Callable[[str, str, str], None] = _download) -> str:
    """The cached tracks of one of *study*'s location files, downloading and cleaning it once."""
    name, size, url = file
    path = _file_path(study, url, cache_dir)
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = path + (".zip" if name.lower().endswith(".zip") else ".csv")
    default_taxon = study.names[0] if len(study.names) == 1 else ""
    try:
        download(url, raw, name[:60])
        tracks = read_file(raw, name, default_taxon)
    except BirdDataError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
        raise BirdDataError(f"Could not read {name!r} of {study.title!r}: {exc}") from exc
    finally:
        if os.path.exists(raw):
            os.remove(raw)
    keys = sorted(tracks)
    lengths = np.array([len(tracks[k]) for k in keys], np.int64)
    table = np.concatenate([tracks[k] for k in keys]) if keys else np.zeros((0, 3))
    part = path + ".part.npz"
    np.savez_compressed(part, time=table[:, 0].astype(np.int64), lon=table[:, 1].astype(np.float32),
                        lat=table[:, 2].astype(np.float32), lengths=lengths,
                        taxon=np.array([k[0] for k in keys], dtype=str),
                        geolocator=np.array([k[2] for k in keys], dtype=bool))
    os.replace(part, path)
    return path


# ---------------------------------------------------------------------------------------------------------------
# Choosing and loading the tracks

def taxon_matches(info: dict[str, Any] | None, keys: Sequence[int]) -> bool:
    """True if a classified taxon is a bird and, with *keys* (GBIF taxa), falls within one of them."""
    if not info or not info.get("bird"):
        return False
    return not keys or bool(set(info.get("keys", [])) & set(keys))


def select_files(catalogue: Catalogue, keys: Sequence[int] = (), max_mb: float = DEFAULT_MAX_MB
                 ) -> tuple[list[tuple[Study, tuple[str, int, str]]], int, int]:
    """The location files of the studies that tracked the birds in *keys* (all birds when empty).

    Returns them, and the number and bytes of the files left out as larger than *max_mb*.
    """
    chosen, skipped, skipped_bytes = [], 0, 0
    for study in catalogue.studies:
        if not any(taxon_matches(catalogue.taxa.get(n), keys) for n in study.names):
            continue
        for file in study.files:
            if file[1] > max_mb * 1e6:
                skipped += 1
                skipped_bytes += file[1]
            else:
                chosen.append((study, file))
    return chosen, skipped, skipped_bytes


def load_birds(keys: Sequence[int] = (), max_mb: float = DEFAULT_MAX_MB, geolocators: bool = False,
               cache_dir: str | None = None, catalogue: Catalogue | None = None) -> Birds:
    """The cached tracks of the birds in *keys* (GBIF taxon keys; every bird when empty), downloading what is new.

    A file that cannot be downloaded is left out with a warning; raises
    :class:`BirdDataError` when the catalogue cannot be had or no file can.
    """
    check_max_mb(max_mb)
    cache_dir = cache_dir or _default_cache_dir()
    catalogue = catalogue or load_catalogue(cache_dir)
    files, skipped, skipped_bytes = select_files(catalogue, keys, max_mb)
    if skipped:
        logger.info("Bird tracks: left out %d files larger than %g MB (%.1f GB in all); raise --bird-max-mb to "
                    "draw them.", skipped, max_mb, skipped_bytes / 1e9)
    new = [(s, f) for s, f in files if not os.path.isfile(_file_path(s, f[2], cache_dir))]
    if new:
        logger.info("Bird tracks: downloading %d of %d study files (%.2f GB, one-time) …", len(new), len(files),
                    sum(f[1] for _, f in new) / 1e9)
    paths: dict[tuple[str, str], str] = {}
    failures: list[str] = []

    def fetch(item: tuple[Study, tuple[str, int, str]]) -> tuple[tuple[str, str], str | None, str]:
        study, file = item
        try:
            return (study.uuid, file[2]), ensure_file(study, file, cache_dir), ""
        except BirdDataError as exc:
            return (study.uuid, file[2]), None, str(exc)

    fresh = {(s.uuid, f[2]) for s, f in new}
    downloaded = 0
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        for key, path, error in pool.map(fetch, files):
            if path is None:
                failures.append(error)
            else:
                paths[key] = path
            if key in fresh:
                downloaded += 1
                if downloaded % 10 == 0:
                    logger.info("  bird tracks: %d of %d new files", downloaded, len(new))
    if failures:
        logger.warning("Bird tracks: %d of %d files could not be had (first: %s).", len(failures), len(files),
                       failures[0])
        if not paths:
            raise BirdDataError(f"No bird track could be downloaded: {failures[0]}")
    return _assemble(catalogue, files, paths, keys, geolocators)


def _assemble(catalogue: Catalogue, files: Sequence[tuple[Study, tuple[str, int, str]]],
              paths: dict[tuple[str, str], str], keys: Sequence[int], geolocators: bool) -> Birds:
    taxa = dict(catalogue.taxa)
    tables = []
    for study, file in files:
        path = paths.get((study.uuid, file[2]))
        if path is not None:
            with np.load(path) as data:
                tables.append((study, {k: data[k] for k in data.files}))
    unknown = {str(n) for _, t in tables for n in t["taxon"]} - set(taxa)
    if unknown:
        _classify_all(unknown, taxa)
    lon, lat, times, lengths, species, study_index, geo = [], [], [], [], [], [], []
    names: list[str] = []
    name_index: dict[str, int] = {}
    studies: list[Study] = []
    for study, table in tables:
        offsets = np.r_[0, np.cumsum(table["lengths"])]
        s_index = None
        for i, (taxon, is_geo) in enumerate(zip(table["taxon"], table["geolocator"])):
            taxon = str(taxon)
            info = taxa.get(taxon)
            if info is None and all(taxa.get(n, {}).get("bird") for n in study.names):
                info = {"bird": True, "keys": [], "name": taxon, "common": None}   # GBIF did not answer
            if (is_geo and not geolocators) or info is None or not taxon_matches(info, keys):
                continue
            label = str(info.get("name") or taxon)
            if label not in name_index:
                name_index[label] = len(names)
                names.append(label)
            if s_index is None:
                s_index = len(studies)
                studies.append(study)
            a, b = offsets[i], offsets[i + 1]
            lon.append(table["lon"][a:b])
            lat.append(table["lat"][a:b])
            times.append(table["time"][a:b])
            lengths.append(b - a)
            species.append(name_index[label])
            study_index.append(s_index)
            geo.append(bool(is_geo))
    common_of: dict[str, str | None] = {}
    for info in taxa.values():
        common_of.setdefault(str(info.get("name")), info.get("common"))
    def cat(parts: list[np.ndarray], dtype: Any) -> np.ndarray:
        return np.concatenate(parts).astype(dtype) if parts else np.zeros(0, dtype)
    return Birds(cat(lon, np.float32), cat(lat, np.float32), cat(times, np.int64),
                 np.r_[0, np.cumsum(np.array(lengths, np.int64))].astype(np.int64),
                 np.array(species, np.int32), np.array(study_index, np.int32), np.array(geo, bool),
                 tuple(names), tuple(common_of.get(n) for n in names), tuple(studies))


# ---------------------------------------------------------------------------------------------------------------
# Drawing

def species_label(birds: Birds, index: int) -> str:
    common = birds.common[index]
    return common[0].upper() + common[1:] if common else birds.names[index]


def _fixes_per_track(birds: Birds) -> np.ndarray:
    return np.repeat(np.arange(len(birds.species)), np.diff(birds.starts))


def _project(ax: GeoAxes, birds: Birds) -> tuple[np.ndarray, np.ndarray]:
    """The fixes in *ax*'s projection, and whether each lies on the map."""
    xy = ax.projection.transform_points(ccrs.PlateCarree(), birds.lon.astype(float),
                                        birds.lat.astype(float))[:, :2]
    finite = np.isfinite(xy).all(axis=1)
    on = finite.copy()
    on[finite] = frame.inside(ax, xy[finite, 0], xy[finite, 1])
    return xy, on


def draw_birds(axes: Sequence[GeoAxes], birds: Birds) -> Drawn:
    """Draw each bird's track on *axes*, coloured by species, the species whose tracks run longest on the map keyed.

    The rest are drawn paler, in grey. The colours are chosen over all the
    axes together, so a species keeps its colour on both globes. A track
    breaks where no fix was kept for :data:`GAP_DAYS` days, where it jumps
    :data:`JUMP_KM` or more at under :data:`JUMP_KMH` (the tag fell silent, and
    a straight line would invent the route), and where it leaves the visible
    hemisphere.
    """
    seen = np.zeros(len(birds.species), bool)
    if not len(birds.lon):
        return Drawn(seen, {})
    track = _fixes_per_track(birds)
    projected = [_project(ax, birds) for ax in axes]
    # A step joins two fixes of one track kept less than GAP_DAYS apart, both on the visible side, one on the map
    same = track[1:] == track[:-1]
    hours = (birds.time[1:] - birds.time[:-1]) / 3600
    km = _km(birds.lon[:-1].astype(float), birds.lat[:-1].astype(float), birds.lon[1:].astype(float),
             birds.lat[1:].astype(float))
    close = (hours <= GAP_DAYS * 24) & ~((km >= JUMP_KM) & (km < JUMP_KMH * hours))
    step_species = birds.species[track[:-1]]
    step_geo = birds.geolocator[track[:-1]]
    drawn_steps = []
    length = np.zeros(len(birds.names))
    for ax, (xy, on) in zip(axes, projected):
        seen[np.unique(track[on])] = True
        finite = np.isfinite(xy).all(axis=1)
        steps = same & close & finite[:-1] & finite[1:] & (on[:-1] | on[1:])
        drawn_steps.append(steps)
        mine = np.flatnonzero(steps)
        step_length = np.hypot(*(xy[mine + 1] - xy[mine]).T) / max(np.ptp(ax.get_xlim()), 1e-9)
        length += np.bincount(step_species[mine], weights=step_length, minlength=len(birds.names))
    # Ranked by how much of their tracks the map shows; a species seen at only a fix or two comes last
    counts = np.bincount(birds.species[seen], minlength=len(birds.names))
    ranked = [int(i) for i in np.lexsort((-counts, -length)) if counts[i]][:MAX_KEYED_SPECIES]
    colours = {sp: PALETTE[i % len(PALETTE)] for i, sp in enumerate(ranked)}
    for ax, (xy, _), steps in zip(axes, projected, drawn_steps):
        # The keyed species that run longest first, the shorter on top; the unkeyed ones under them, in grey
        groups: list[tuple[np.ndarray, str, float, float]] = [
            (~np.isin(step_species, ranked), OTHER_COLOUR, 6.4, OTHER_ALPHA)]
        groups += [(step_species == sp, colours[sp], 6.5 + rank / 100, LINE_ALPHA) for rank, sp in enumerate(ranked)]
        for mask, colour, zorder, opacity in groups:
            for geo, alpha in ((False, opacity), (True, opacity / 2)):
                mine = np.flatnonzero(steps & mask & (step_geo == geo))
                if len(mine):
                    segments = np.stack([xy[mine], xy[mine + 1]], axis=1)
                    ax.add_collection(LineCollection(list(segments), colors=colour, linewidths=LINE_WIDTH,
                                                     alpha=alpha, zorder=zorder, capstyle="round"))
    return Drawn(seen, colours)


def summary(birds: Birds, drawn: Drawn) -> tuple[int, int, int]:
    """The birds, species and studies in sight."""
    return (int(drawn.seen.sum()), len(set(birds.species[drawn.seen].tolist())),
            len(set(birds.study[drawn.seen].tolist())))


def add_birds_legend(ax: GeoAxes, birds: Birds, drawn: Drawn, y: float = -0.01, x: float = 0.5) -> Legend | None:
    """A key to the keyed species' colours, with the number of birds of each in sight; ``None`` if none is."""
    n_birds, n_species, n_studies = summary(birds, drawn)
    if not n_birds:
        return None
    counts = np.bincount(birds.species[drawn.seen], minlength=len(birds.names))
    handles: list[Line2D] = []
    labels: list[str] = []
    for sp, colour in drawn.colours.items():
        handles.append(Line2D([], [], color=colour, linewidth=2.2))
        labels.append(f"{species_label(birds, sp)} ({counts[sp]:,})")
    others = int(sum(c for sp, c in enumerate(counts) if sp not in drawn.colours))
    if others:
        unkeyed = sum(1 for sp, c in enumerate(counts) if c and sp not in drawn.colours)
        handles.append(Line2D([], [], color=OTHER_COLOUR, linewidth=2.2))
        labels.append(f"{unkeyed} other species ({others:,})")
    title = (f"Tracks of {n_birds:,} tagged birds of {n_species} species from {n_studies} "
             f"{'study' if n_studies == 1 else 'studies'} (Movebank), by species (birds in sight)")
    if drawn.seen[birds.geolocator].any():
        title += "; geolocator tracks, accurate to ~200 km, paler"
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=3, fontsize=10,
                    title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=2.0, columnspacing=1.4, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend


def sources_path(output_filename: str) -> str:
    """The text file listing the studies drawn on the map saved as *output_filename*.

    An atlas draws its maps as ``NAME.tmp.png`` and then renames them, so the
    list is named for the map's final name.
    """
    return os.path.splitext(output_filename)[0].removesuffix(".tmp") + "_bird_studies.txt"


def birds_attribution(birds: Birds, drawn: Drawn, sources_file: str) -> str:
    """The map's credit for the tracks in sight: the repository, the studies' licences and where they are cited."""
    used = sorted(set(birds.study[drawn.seen].tolist()))
    licences = [name for name in OPEN_LICENCES.values() if any(birds.studies[i].licence == name for i in used)]
    count = f"{len(used)} {'study' if len(used) == 1 else 'studies'}"
    return (f"Bird tracks: {count} in the Movebank Data Repository ({' / '.join(licences)}), "
            f"cited in {os.path.basename(sources_file)}")


def write_sources(path: str, birds: Birds, drawn: Drawn) -> str:
    """List the studies whose tracks are in sight, with their citations, licences and birds, in *path*."""
    counts = np.bincount(birds.study[drawn.seen], minlength=len(birds.studies))
    lines = ["Bird tracks on this map, from the Movebank Data Repository (https://datarepository.movebank.org/).",
             "Each study is cited as its authors ask; CC BY 4.0 data must be credited so when the map is shared.",
             ""]
    for i in np.argsort(-counts, kind="stable"):
        if counts[i]:
            study = birds.studies[i]
            lines.append(f"- {study.citation} [{study.licence}; {counts[i]:,} "
                         f"{'bird' if counts[i] == 1 else 'birds'} drawn]")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
