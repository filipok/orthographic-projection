"""Species ranges: where a species (or genus, family …) lives, from the IUCN Red List and BirdLife.

Data sources
------------
The IUCN Red List of Threatened Species, spatial data (range polygons):
https://www.iucnredlist.org/resources/spatial-data-download

BirdLife International and Handbook of the Birds of the World, Bird species
distribution maps of the world (a GeoPackage), with their digital checklist of
the birds of the world (families, Red List categories, English names):
https://datazone.birdlife.org/species/requestdis

Terms: both forbid passing the data on, so nothing is downloaded or shipped
here. Each user requests the files (free, non-commercial use) and leaves them
in ``~/.cache/ortho_tiles/iucn/``: IUCN's zips of shapefiles as they come
(``MAMMALS.zip``), BirdLife's GeoPackage unzipped (``BOTW_2025.gpkg``; SQLite
cannot read inside a zip) beside its checklist. Shapefiles are read straight
from the zip. The names in each file are indexed once; one taxon's range is
merged by kind, simplified to about 1 km and kept under
``~/.cache/ortho_tiles/iucn/ranges/`` so later maps need not read the
gigabytes again. It is kept against the files that hold it, so adding another
group keeps it, and replacing its own file rebuilds it.

Each part of a range carries IUCN's codes for presence (extant, possibly
extinct …), origin (native, introduced …) and season (resident, breeding …);
the map shades the parts by these.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import struct
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import shapely
from shapely.geometry import Polygon

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from matplotlib.colors import to_rgb
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from biomes import _key

logger = logging.getLogger(__name__)

DATA_VERSION = 6
SIMPLIFY_DEGREES = 0.01          # ~1 km: finer than a zoomed-in map shows
TILE_DEGREES = 30.0              # ranges are drawn in pieces this size (see tiles)
GAP_DEGREES = 1e-4               # ~10 m: gaps this narrow between parts of a range are closed
SPECK_DEGREES = 1e-4             # ~1 km²: parts of a range smaller than this (in square degrees) are dropped
POLYGON_TYPES = (5, 15, 25)      # Polygon, PolygonZ, PolygonM
FIELDS = ("sci_name", "presence", "origin", "seasonal", "category", "genus", "family", "order_")

# Kinds of range, bottom to top: (label, colour, hatch)
RANGE_KINDS: tuple[tuple[str, str, str | None], ...] = (
    ("Extinct", "#7a7a7a", "\\\\"),
    ("Possibly extinct", "#a50f15", "//"),
    ("Vagrant", "#80cdc1", ".."),
    ("Introduced", "#01665e", None),
    ("Possibly extant", "#e6550d", "//"),
    ("Passage", "#9e9ac8", None),
    ("Non-breeding season", "#3182bd", None),
    ("Breeding season", "#e6b800", None),
    ("Extant (resident)", "#e6550d", None),
)
EXTINCT, POSSIBLY_EXTINCT, VAGRANT, INTRODUCED, POSSIBLY_EXTANT, PASSAGE, NONBREEDING, BREEDING, RESIDENT = \
    range(len(RANGE_KINDS))

CATEGORIES = {
    "EX": "Extinct", "EW": "Extinct in the Wild", "CR": "Critically Endangered", "EN": "Endangered",
    "VU": "Vulnerable", "NT": "Near Threatened", "LR/nt": "Near Threatened", "LR/cd": "Conservation Dependent",
    "LC": "Least Concern", "LR/lc": "Least Concern", "DD": "Data Deficient",
    "CR (PE)": "Critically Endangered (Possibly Extinct)",
    "CR (PEW)": "Critically Endangered (Possibly Extinct in the Wild)",
}
RANKS = (("sci_name", "SPECIES"), ("genus", "GENUS"), ("family", "FAMILY"), ("order_", "ORDER"))
IUCN, BIRDLIFE = "IUCN Red List", "BirdLife International"

# The checklist's columns, by their heading
CHECKLIST_COLUMNS = {"Order": "order_", "Family name": "family", "Common name": "common",
                     "Scientific name": "sci_name", "Alternative common names": "aliases"}
_XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class IucnDataError(RuntimeError):
    """A range file could not be read."""


@dataclass(frozen=True)
class Source:
    """One table of ranges: a shapefile (*member*, without ``.shp``) in the zip at *path* or loose at
    *path* (*member* ``None``), or a feature table (*member*) of the GeoPackage at *path*."""
    path: str
    member: str | None

    @property
    def geopackage(self) -> bool:
        return self.path.lower().endswith(".gpkg")

    @property
    def publisher(self) -> str:
        return BIRDLIFE if self.geopackage else IUCN

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{os.path.abspath(self.path)}?mode=ro", uri=True)

    def read(self, ext: str) -> bytes:
        if self.member is None:
            with open(f"{os.path.splitext(self.path)[0]}.{ext}", "rb") as f:
                return f.read()
        with zipfile.ZipFile(self.path) as archive:
            return archive.read(self._name(archive, ext))

    def open_shp(self):
        if self.member is None:
            return open(self.path, "rb")
        archive = zipfile.ZipFile(self.path)
        return archive.open(self._name(archive, "shp"))

    def _name(self, archive: zipfile.ZipFile, ext: str) -> str:
        wanted = f"{self.member}.{ext}".lower()
        for name in archive.namelist():
            if name.lower() == wanted:
                return name
        raise IucnDataError(f"{os.path.basename(wanted)} is missing from {self.path}")

    def dated(self) -> str:
        """The day the data was made, YYYY-MM-DD: the date of the shapefile in the zip."""
        if self.member is None or self.geopackage:
            return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(self.path)))
        with zipfile.ZipFile(self.path) as archive:
            y, m, d = archive.getinfo(self._name(archive, "shp")).date_time[:3]
        return f"{y:04d}-{m:02d}-{d:02d}"


class Range(NamedTuple):
    name: str                 # scientific name of the taxon
    rank: str                 # "SPECIES", "GENUS" …
    common: str | None        # the common name asked for, if any
    species: int              # species in the range
    category: str | None      # Red List category code of a species ("VU")
    kinds: list[int]          # index into RANGE_KINDS of each geometry
    geometries: list          # shapely (multi)polygons, in degrees
    publisher: str            # IUCN or BIRDLIFE
    credit: str               # the citation, with the version or date of the data


def default_data_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "iucn")


def _geopackage_tables(path: str) -> list[str]:
    with sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True) as db:
        return [t for (t,) in db.execute("SELECT table_name FROM gpkg_contents WHERE data_type = 'features'")]


def find_sources(data: str | None = None) -> list[Source]:
    """The range tables in *data*: a folder of zips, shapefiles and GeoPackages, or one such file.

    Raises ``ValueError`` when there are none, saying where to get them.
    """
    data = os.path.expanduser(data or default_data_dir())
    if os.path.isdir(data):
        files = [os.path.join(data, f) for f in sorted(os.listdir(data))
                 if f.lower().endswith((".zip", ".shp", ".gpkg"))]
    else:
        files = [data] if os.path.isfile(data) else []
    loose = {os.path.basename(f).lower() for f in files}
    sources = []
    for path in files:
        try:
            if path.lower().endswith(".shp"):
                sources.append(Source(path, None))
            elif path.lower().endswith(".gpkg"):
                sources += [Source(path, table) for table in _geopackage_tables(path)]
            else:
                with zipfile.ZipFile(path) as archive:
                    names = archive.namelist()
                sources += [Source(path, name[:-4]) for name in names if name.lower().endswith(".shp")]
                for name in names:
                    if name.lower().endswith(".gpkg") and os.path.basename(name).lower() not in loose:
                        logger.warning("Unzip %s from %s into %s to use it: a GeoPackage cannot be read "
                                       "inside a zip.", os.path.basename(name), os.path.basename(path),
                                       os.path.dirname(path))
        except (OSError, zipfile.BadZipFile, sqlite3.Error) as exc:
            logger.warning("Skipping %s, which cannot be read: %s", path, exc)
    if not sources:
        raise ValueError(f"No IUCN range files in {data}. Download range data (shapefiles) from "
                         "https://www.iucnredlist.org/resources/spatial-data-download and put the zip there, "
                         "or give its path with --iucn-data.")
    return sources


def _stamp(path: str) -> tuple:
    return os.path.abspath(path), os.path.getsize(path), int(os.path.getmtime(path))


def _signature(sources: list[Source], extra: list[str] = ()) -> str:
    """A short hash of the range files (and *extra* files), so what is cached from them is rebuilt when they
    change."""
    stamp = sorted({_stamp(s.path) for s in sources} | {_stamp(p) for p in extra})
    return hashlib.sha1(repr(stamp).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# BirdLife's checklist: families, orders, Red List categories and English names
# ---------------------------------------------------------------------------


def find_checklist(data: str | None = None) -> str | None:
    """BirdLife's checklist spreadsheet in *data* (a folder, or the folder of a file), if there is one.

    A loose ``*Checklist*.xlsx`` is preferred; one inside a zip in the folder
    is unpacked beside it once.
    """
    data = os.path.expanduser(data or default_data_dir())
    folder = data if os.path.isdir(data) else os.path.dirname(data)
    if not os.path.isdir(folder):
        return None
    names = sorted(os.listdir(folder))
    for name in names:
        if name.lower().endswith(".xlsx") and "checklist" in name.lower():
            return os.path.join(folder, name)
    for name in names:
        if not name.lower().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(os.path.join(folder, name)) as archive:
                for member in archive.namelist():
                    if member.lower().endswith(".xlsx") and "checklist" in member.lower():
                        target = os.path.join(folder, os.path.basename(member))
                        with open(target + ".part", "wb") as f:
                            f.write(archive.read(member))
                        os.replace(target + ".part", target)
                        return target
        except (OSError, zipfile.BadZipFile):
            continue
    return None


def read_checklist(path: str) -> list[dict]:
    """The rows of BirdLife's checklist, as dicts keyed by :data:`CHECKLIST_COLUMNS`' values.

    The spreadsheet is read with the standard library: its shared strings and
    first sheet are XML inside the ``.xlsx`` zip.
    """
    with zipfile.ZipFile(path) as book:
        strings = ["".join(t.text or "" for t in si.iter(f"{_XLSX}t"))
                   for si in ET.fromstring(book.read("xl/sharedStrings.xml")).iter(f"{_XLSX}si")]
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    columns: dict[str, str] = {}
    rows = []
    for row in sheet.iter(f"{_XLSX}row"):
        cells = {}
        for cell in row.iter(f"{_XLSX}c"):
            value = cell.find(f"{_XLSX}v")
            if value is not None and value.text is not None:
                text = strings[int(value.text)] if cell.get("t") == "s" else value.text
            else:
                text = "".join(t.text or "" for t in cell.iter(f"{_XLSX}t"))
            cells[re.match(r"[A-Z]+", cell.get("r", "A")).group(0)] = text.strip()
        if not columns:                  # the heading row: "Scientific name", "2025 IUCN Red List category" …
            columns = {col: CHECKLIST_COLUMNS.get(text, "category" if "red list category" in text.lower() else "")
                       for col, text in cells.items()}
            columns = {col: key for col, key in columns.items() if key}
            if "sci_name" not in columns.values():
                columns = {}
            continue
        entry = {key: cells.get(col, "") for col, key in columns.items()}
        if entry.get("sci_name"):
            rows.append(entry)
    return rows


# ---------------------------------------------------------------------------
# Indexing the names in each file
# ---------------------------------------------------------------------------


def _shapefile_records(source: Source) -> list[dict]:
    """The attributes of every shape in a shapefile (only :data:`FIELDS`); empty if it is not IUCN's."""
    import shapefile  # pyshp, which Cartopy depends on

    try:
        encoding = source.read("cpg").decode("ascii", "replace").strip() or "utf-8"
    except (OSError, KeyError, IucnDataError):
        encoding = "utf-8"
    reader = shapefile.Reader(dbf=io.BytesIO(source.read("dbf")), encoding=encoding, encodingErrors="replace")
    names = [f[0] for f in reader.fields[1:]]
    if "sci_name" not in names:
        return []
    wanted = [f for f in FIELDS if f in names]
    return [{k: v for k, v in rec.as_dict().items() if k in wanted} if rec is not None else {}
            for rec in reader.iterRecords(fields=wanted)]


def _geopackage_records(source: Source, checklist: dict[str, dict]) -> list[dict]:
    """The attributes of every range part in a GeoPackage table, with the family, order and category from the
    *checklist* (by scientific name); the row's ``fid`` is kept to fetch its shape."""
    with source.connect() as db:
        columns = {row[1] for row in db.execute(f'PRAGMA table_info("{source.member}")')}
        if "sci_name" not in columns:
            return []
        wanted = [c for c in ("presence", "origin", "seasonal", "version") if c in columns]
        rows = db.execute(f'SELECT fid, sci_name, {", ".join(wanted)} FROM "{source.member}"').fetchall()
    records = []
    for fid, name, *values in rows:
        rec = {"fid": fid, "sci_name": name or "", **dict(zip(wanted, values))}
        rec["genus"] = rec["sci_name"].split(" ")[0]
        known = checklist.get(rec["sci_name"].lower(), {})
        rec.update({k: known[k] for k in ("family", "order_", "category") if known.get(k)})
        records.append(rec)
    return records


def _index(source: Source, cache_dir: str, checklist_path: str | None) -> dict:
    """The names in *source*: ``{"records": [...], "common": {name: scientific name}, "version": str}``.

    A GeoPackage's names take seconds to read (they sit after the shapes in
    each row), so its index is cached against the file and the checklist.
    """
    if not source.geopackage:
        return {"records": _shapefile_records(source), "common": {}, "version": ""}
    extra = [checklist_path] if checklist_path else []
    path = os.path.join(cache_dir, f"index_{_signature([source], extra)}_{source.member}_v{DATA_VERSION}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    logger.info("Indexing the names in %s (one-time) …", os.path.basename(source.path))
    rows = read_checklist(checklist_path) if checklist_path else []
    by_name: dict[str, dict] = {}
    for r in rows:                       # a name listed twice: the row with a category, not "NR" (not recognised)
        key, before = r["sci_name"].lower(), by_name.get(r["sci_name"].lower())
        if before is None or (before.get("category") in ("", "NR") and r.get("category") not in ("", "NR")):
            by_name[key] = r
    records = _geopackage_records(source, by_name)
    held = {r["sci_name"].lower() for r in records}
    common: dict[str, str] = {}
    for r in rows:
        if r["sci_name"].lower() in held:
            for alias in [r.get("common", ""), *r.get("aliases", "").split(",")]:
                if alias.strip():
                    common.setdefault(" ".join(alias.split()).lower(), r["sci_name"])
    index = {"records": records, "common": common, "version": dataset_version(r.get("version") for r in records)}
    os.makedirs(cache_dir, exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump(index, f)
    os.replace(path + ".part", path)
    return index


def dataset_version(versions) -> str:
    """The newest of BirdLife's versions (``2025.2``), skipping others (``unpublished``); ``""`` if none."""
    numbered = [v for v in map(str, versions) if re.fullmatch(r"\d{4}\.\d+", v)]
    return max(numbered, key=lambda v: tuple(map(int, v.split("."))), default="")


Record = tuple[Source, int, dict]      # a shape: its table, its index (or fid) there, its attributes


class Names(NamedTuple):
    records: list[Record]
    common: dict[str, str]             # English names (from BirdLife's checklist) -> scientific name
    versions: dict[Source, str]        # each GeoPackage table's version


def _all_records(sources: list[Source], cache_dir: str, checklist_path: str | None = None) -> Names:
    """Every range part's attributes, from the small tables beside the shapes and the cached indexes."""
    records: list[Record] = []
    common: dict[str, str] = {}
    versions: dict[Source, str] = {}
    for source in sources:
        try:
            index = _index(source, cache_dir, checklist_path)
        except IucnDataError:
            raise
        except Exception as exc:                 # zip, pyshp, SQLite and file errors alike
            raise IucnDataError(f"Could not read {source.path}: {exc}") from exc
        key = "fid" if source.geopackage else None
        records += [(source, int(rec[key]) if key else i, rec) for i, rec in enumerate(index["records"])]
        for name, scientific in index["common"].items():
            common.setdefault(name, scientific)
        versions[source] = index["version"]
    if not records:
        raise IucnDataError("The range files have no sci_name field; are they IUCN or BirdLife range polygons?")
    return Names(records, common, versions)


def range_kind(presence: int, origin: int, seasonal: int) -> int:
    """Which of :data:`RANGE_KINDS` a part of a range with IUCN's codes is."""
    if presence == 5:
        return EXTINCT
    if presence == 4:
        return POSSIBLY_EXTINCT
    if origin == 3:
        return INTRODUCED
    if origin == 4:
        return VAGRANT
    if presence in (3, 6):              # possibly extant, presence uncertain
        return POSSIBLY_EXTANT
    return {2: BREEDING, 3: NONBREEDING, 4: PASSAGE}.get(seasonal, RESIDENT)


# ---------------------------------------------------------------------------
# Reading the shapes
# ---------------------------------------------------------------------------


def parse_polygon(content: bytes) -> shapely.Geometry | None:
    """A shapefile polygon record (after its 8-byte header) as a shapely geometry; ``None`` if empty.

    Outer rings run clockwise and holes anticlockwise; each hole goes to the
    outer ring around it.
    """
    shape_type, = struct.unpack_from("<i", content, 0)
    if shape_type not in POLYGON_TYPES:
        return None
    n_parts, n_points = struct.unpack_from("<2i", content, 36)
    parts = np.frombuffer(content, "<i4", n_parts, 44)
    xy = np.frombuffer(content, "<f8", 2 * n_points, 44 + 4 * n_parts).reshape(-1, 2)
    outers, holes = [], []
    for ring in np.split(xy, parts[1:]):
        if len(ring) < 4:
            continue
        x, y = ring[:, 0], ring[:, 1]
        area = np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])
        (outers if area < 0 else holes).append(ring)
    if not outers:                       # rings the wrong way round: take them all as outer
        outers, holes = holes, []
    shells = [Polygon(ring) for ring in outers]
    inside: list[list[np.ndarray]] = [[] for _ in shells]
    for hole in holes:
        x, y = hole[0]
        for i, shell in enumerate(shells):
            if shapely.intersects_xy(shell, x, y):
                inside[i].append(hole)
                break
    polygons = [Polygon(ring, inner) for ring, inner in zip(outers, inside)]
    return shapely.make_valid(shapely.MultiPolygon(polygons) if len(polygons) > 1 else polygons[0])


def parse_geopackage_geometry(blob: bytes | None) -> shapely.Geometry | None:
    """A GeoPackage geometry (a short header, an optional envelope, then WKB) as shapely; ``None`` if empty."""
    if not blob or blob[:2] != b"GP":
        return None
    flags = blob[3]
    if flags & 0b10000:                  # the empty-geometry flag
        return None
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get((flags >> 1) & 0b111, 0)
    geometry = shapely.from_wkb(bytes(blob[8 + envelope:]))
    return None if geometry is None or geometry.is_empty else shapely.make_valid(shapely.force_2d(geometry))


def _read_shapes(source: Source, indices: list[int]) -> dict[int, shapely.Geometry | None]:
    """The shapes at *indices* (fids, in a GeoPackage) of *source*.

    A shapefile's are read in file order: seeking forward in the zip is cheap.
    """
    if source.geopackage:
        found: dict[int, shapely.Geometry | None] = {}
        with source.connect() as db:
            column = db.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
                                (source.member,)).fetchone()[0]
            for start in range(0, len(indices), 500):
                chunk = indices[start:start + 500]
                query = f'SELECT fid, "{column}" FROM "{source.member}" WHERE fid IN ({",".join("?" * len(chunk))})'
                for fid, blob in db.execute(query, chunk):
                    found[fid] = parse_geopackage_geometry(blob)
        return found
    shx = source.read("shx")
    found = {}
    with source.open_shp() as shp:
        for i in sorted(indices):
            offset, length = struct.unpack_from(">2i", shx, 100 + 8 * i)
            shp.seek(2 * offset + 8)
            found[i] = parse_polygon(shp.read(2 * length))
    return found


# ---------------------------------------------------------------------------
# Finding and building a range
# ---------------------------------------------------------------------------


def _match(records: list[Record], wanted: str) -> tuple[str, str, list[Record]] | None:
    """The records of the taxon called *wanted*: (its name, rank, records), trying species, genus, family, order."""
    for field, rank in RANKS:
        hits = [r for r in records if str(r[2].get(field, "")).strip().lower() == wanted]
        if hits:
            name = str(hits[0][2][field]).strip()
            return (name.capitalize() if rank != "SPECIES" else name), rank, hits
    return None


def _suggestions(names: Names, wanted: str) -> str:
    known = {str(r[2].get(f, "")).strip().lower(): str(r[2].get(f, "")).strip()
             for r in names.records for f in ("sci_name", "genus")}
    known.update({c: c for c in names.common})
    close = difflib.get_close_matches(wanted, list(known), n=3, cutoff=0.75)
    return f" Did you mean {', '.join(repr(known[c]) for c in close)}?" if close else ""


def _resolve(names: Names, name: str) -> tuple[str, str, str | None, list[Record]]:
    """The taxon *name* stands for: (scientific name, rank, common name, its records).

    A scientific name is matched in the files, then an English name in
    BirdLife's checklist; anything else is looked up in GBIF and its
    scientific name matched. Raises ``ValueError`` when nothing has that name.
    """
    wanted = " ".join(name.split()).lower()
    matched = _match(names.records, wanted)
    common = None
    if matched is None and wanted in names.common:          # a bird's English name
        matched = _match(names.records, names.common[wanted].lower())
        common = wanted if matched else None
    if matched is None:                                     # a common name, or a synonym: ask GBIF
        from gbif import GbifDataError, resolve_taxon
        try:
            taxon = resolve_taxon(name)
        except (ValueError, GbifDataError):
            taxon = None
        if taxon is not None:
            matched = _match(names.records, taxon.name.lower())
            common = taxon.common if matched else None
    if matched is None:
        raise ValueError(f"The range files hold no species, genus or family called {name!r}."
                         + _suggestions(names, wanted))
    scientific, rank, hits = matched
    return scientific, rank, common, hits


def merge(parts: list) -> shapely.Geometry:
    """*parts* as one geometry, closing hairline gaps between them.

    IUCN splits a wide range into pieces (an ocean at the prime meridian)
    that do not quite meet, metres apart: merged as they are, the seam would
    be drawn as an outline across the range.
    """
    grown = shapely.buffer(parts, GAP_DEGREES, quad_segs=1, join_style="mitre")
    return drop_specks(shapely.make_valid(shapely.buffer(shapely.union_all(grown), -GAP_DEGREES, quad_segs=1,
                                                         join_style="mitre")))


def drop_specks(geometry: shapely.Geometry, smallest: float = SPECK_DEGREES) -> shapely.Geometry:
    """*geometry*'s polygons of at least *smallest* square degrees, and nothing else.

    Merging and cutting leave crumbs along coasts, a few metres across: too
    small to see, and cartopy turns some of them inside out, filling the whole
    globe.
    """
    polygons = [p for p in shapely.get_parts(geometry)
                if p.geom_type == "Polygon" and p.area >= smallest]
    if not polygons:
        return shapely.MultiPolygon()
    return polygons[0] if len(polygons) == 1 else shapely.MultiPolygon(polygons)


def credit(sources: set[Source], versions: dict[Source, str]) -> tuple[str, str]:
    """(publisher, citation) for a range read from *sources*, as each publisher asks to be cited."""
    birdlife = sorted({versions.get(s, "") for s in sources if s.geopackage})
    iucn = sorted({s.dated() for s in sources if not s.geopackage})
    parts = []
    if iucn:
        parts.append(f"IUCN ({iucn[-1][:4]}), The IUCN Red List of Threatened Species, spatial data of {iucn[-1]}")
    if birdlife:
        version = dataset_version(birdlife)
        year = f" ({version[:4]})" if version else ""
        parts.append(f"BirdLife International and Handbook of the Birds of the World{year}, "
                     f"Bird species distribution maps of the world" + (f", version {version}" if version else ""))
    return (IUCN if iucn else BIRDLIFE), "Range: " + "; ".join(parts)


def _build(scientific: str, rank: str, hits: list[Record], versions: dict[Source, str]) -> Range:
    """The range from the shapes of *hits*, merged by kind."""
    logger.info("Reading the range of %s (%d parts; one-time) …", scientific, len(hits))
    parts: dict[int, list] = {}
    sources = {h[0] for h in hits}
    for source in sources:
        mine = [h for h in hits if h[0] == source]
        try:
            shapes = _read_shapes(source, [h[1] for h in mine])
        except Exception as exc:                 # zip, seek, SQLite and geometry errors alike
            raise IucnDataError(f"Could not read the range of {scientific}: {exc}") from exc
        for _, i, rec in mine:
            shape = shapes.get(i)
            if shape is not None:
                kind = range_kind(int(rec.get("presence") or 1), int(rec.get("origin") or 1),
                                  int(rec.get("seasonal") or 1))
                parts.setdefault(kind, []).append(shapely.simplify(shape, SIMPLIFY_DEGREES))
    kinds = sorted(parts)
    geometries = [merge(parts[k]) for k in kinds]
    categories = Counter(str(h[2].get("category", "")).strip() for h in hits)
    species = len({str(h[2]["sci_name"]).strip().lower() for h in hits})
    category = categories.most_common(1)[0][0] or None if species == 1 else None
    publisher, citation = credit(sources, versions)
    return Range(scientific, rank, None, species, category, kinds, geometries, publisher, citation)


def load_range(name: str, data: str | None = None, cache_dir: str | None = None) -> Range:
    """The range of the species, genus, family or order *name*, from the range files in *data*.

    A scientific name is matched in the files, then a bird's English name in
    BirdLife's checklist; anything else (``lion``) is looked up in GBIF and its
    scientific name matched. The range is cached against the files that hold
    it, so adding other groups keeps it. Raises ``ValueError`` when there are
    no range files or no taxon of that name, and :class:`IucnDataError` when a
    file cannot be read.
    """
    if not " ".join(name.split()):
        raise ValueError("Give a species name for the range, such as 'Panthera leo'.")
    cache_dir = cache_dir or os.path.join(default_data_dir(), "ranges")
    sources = find_sources(data)
    checklist = find_checklist(data) if any(s.geopackage for s in sources) else None
    names = _all_records(sources, cache_dir, checklist)
    scientific, rank, common, hits = _resolve(names, name)
    slug = "".join(c if c.isalnum() else "_" for c in scientific.lower())
    held_in = list({h[0] for h in hits})
    path = os.path.join(cache_dir, f"{rank.lower()}_{slug}_{_signature(held_in)}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        found = _build(scientific, rank, hits, names.versions)
        wkb = [bytes(shapely.to_wkb(g)) for g in found.geometries]
        os.makedirs(cache_dir, exist_ok=True)
        part = path + ".part.npz"
        np.savez_compressed(part, name=np.array(found.name), rank=np.array(found.rank),
                            species=np.array(found.species), category=np.array(found.category or ""),
                            kinds=np.array(found.kinds, int), wkb=np.frombuffer(b"".join(wkb), np.uint8),
                            ends=np.cumsum([len(w) for w in wkb]), publisher=np.array(found.publisher),
                            credit=np.array(found.credit))
        os.replace(part, path)
    with np.load(path) as saved:
        blob, ends = saved["wkb"].tobytes(), saved["ends"]
        geometries = [shapely.from_wkb(blob[a:b]) for a, b in zip([0, *ends[:-1]], ends)]
        return Range(str(saved["name"]), str(saved["rank"]), common, int(saved["species"]),
                     str(saved["category"]) or None, saved["kinds"].tolist(), geometries,
                     str(saved["publisher"]), str(saved["credit"]))


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------




def range_label(found: Range) -> str:
    """``Panthera leo (lion), Vulnerable`` or ``Panthera (genus, 4 species)``."""
    notes = [found.common] if found.common and found.common.lower() != found.name.lower() else []
    if found.rank != "SPECIES":
        notes.append(f"{found.rank.lower()}, {found.species:,} species")
    label = f"{found.name} ({', '.join(notes)})" if notes else found.name
    status = CATEGORIES.get(found.category or "")
    return f"{label}, {status}" if status else label


def _patch_style(kind: int, alpha: float) -> dict:
    _, colour, hatch = RANGE_KINDS[kind]
    if hatch:                            # a light wash, hatched in the full colour
        return dict(facecolor=(*to_rgb(colour), alpha * 0.4),
                    edgecolor=colour, hatch=hatch, linewidth=0.8)
    return dict(facecolor=(*to_rgb(colour), alpha), edgecolor=colour,
                linewidth=0.8)


def tiles(geometry: shapely.Geometry, size: float = TILE_DEGREES) -> list:
    """*geometry* cut into pieces no larger than *size* degrees square.

    Cartopy clips a polygon spanning half the world (an ocean-wide sea turtle's
    range) wrongly at the globe's rim, leaving wedges unfilled; pieces this
    size are clipped right.
    """
    x0, y0, x1, y1 = shapely.bounds(geometry)
    xs = np.arange(np.floor(x0 / size) * size, x1, size)
    ys = np.arange(np.floor(y0 / size) * size, y1, size)
    boxes = shapely.box(*np.meshgrid(xs, ys), *np.meshgrid(xs + size, ys + size))
    pieces = [drop_specks(p) for p in shapely.intersection(geometry, boxes.ravel())]
    return [p for p in pieces if not p.is_empty]


def draw_range(ax: GeoAxes, found: Range, alpha: float = 0.45) -> None:
    """Fill each kind of range on *ax* in its colour, extinct parts beneath, the living range on top.

    The fill is drawn tile by tile without edges (so the seams do not show)
    and the outline over it.
    """
    for kind, geometry in zip(found.kinds, found.geometries):
        style = _patch_style(kind, alpha)
        linewidth = style.pop("linewidth")
        pieces = [p for tile in tiles(geometry) for p in project(ax, tile)]
        outline = project(ax, geometry.boundary)
        if pieces:
            ax.add_geometries(pieces, crs=ax.projection, zorder=7.1, linewidth=0, antialiased=False, **style)
        if outline:
            ax.add_geometries(outline, crs=ax.projection, zorder=7.1, facecolor="none",
                              edgecolor=style["edgecolor"], linewidth=linewidth)
    logger.info("Range of %s drawn: %s.", found.name, ", ".join(RANGE_KINDS[k][0] for k in found.kinds))


def project(ax: GeoAxes, geometry: shapely.Geometry, size: float = TILE_DEGREES, depth: int = 0) -> list:
    """*geometry* (in degrees) in *ax*'s projection, as a list of what shows; empty beyond the horizon.

    Cartopy fails on the odd piece that grazes the globe's rim (its clipping
    makes a shape it cannot build); such a piece is cut in four and each
    quarter tried again, three times at most.
    """
    polygonal = geometry.geom_type in ("Polygon", "MultiPolygon")
    try:
        projected = ax.projection.project_geometry(geometry, ccrs.PlateCarree())
        if polygonal and projected.area > 1.1 * _largest_area(geometry):
            raise ValueError("projected inside out")
    except (TypeError, ValueError, shapely.errors.GEOSException):
        if depth >= 3 or not polygonal:
            logger.debug("Could not project a piece of range of %.1f square degrees.", geometry.area)
            return []
        return [p for tile in tiles(geometry, size / 2) for p in project(ax, tile, size / 2, depth + 1)]
    return [] if projected.is_empty else [projected]


def _largest_area(geometry: shapely.Geometry) -> float:
    """The most square metres *geometry* (in degrees) can cover on the globe.

    An orthographic map only shrinks areas, so a piece projected larger than
    this has come out inside out: the globe's disc but for the piece, which
    cartopy does at times for a piece cut by the horizon.
    """
    _, y0, _, y1 = shapely.bounds(geometry)
    widest = 1.0 if y0 <= 0 <= y1 else np.cos(np.radians(min(abs(y0), abs(y1))))
    return geometry.area * (6_378_137 * np.pi / 180) ** 2 * widest


def add_range_legend(ax: GeoAxes, found: Range, alpha: float = 0.45, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the kinds of range shown, the living range first."""
    kinds = sorted(found.kinds, reverse=True)
    handles = [mpatches.Patch(**_patch_style(k, alpha)) for k in kinds]
    labels = [RANGE_KINDS[k][0] for k in kinds]
    return _key(ax, handles, labels, f"Range of {range_label(found)} ({found.publisher})", min(len(kinds), 4), x, y)


def iucn_attribution(found: Range) -> str:
    return found.credit
