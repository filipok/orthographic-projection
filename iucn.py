"""Species ranges: where a species (or genus, family …) lives, from the IUCN Red List.

Data source
-----------
The IUCN Red List of Threatened Species, spatial data (range polygons):
https://www.iucnredlist.org/resources/spatial-data-download

Terms: IUCN's terms forbid passing the data on, so nothing is downloaded or
shipped here. Each user downloads range files from the Red List (a free
account, non-commercial use) and leaves them, zipped, in
``~/.cache/ortho_tiles/iucn/``: a whole group (``MAMMALS.zip``) or the
ranges of a search (any zip of shapefiles with IUCN's fields). The shapes are
read straight from the zip; one taxon's range is merged by kind, simplified to
about 1 km and kept under ``~/.cache/ortho_tiles/iucn/ranges/`` so later maps
need not read the gigabytes again.

Each part of a range carries IUCN's codes for presence (extant, possibly
extinct …), origin (native, introduced …) and season (resident, breeding …);
the map shades the parts by these.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import logging
import os
import struct
import time
import zipfile
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

DATA_VERSION = 1
SIMPLIFY_DEGREES = 0.01          # ~1 km: finer than a zoomed-in map shows
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
}
RANKS = (("sci_name", "SPECIES"), ("genus", "GENUS"), ("family", "FAMILY"), ("order_", "ORDER"))


class IucnDataError(RuntimeError):
    """An IUCN range file could not be read."""


@dataclass(frozen=True)
class Source:
    """One shapefile of ranges: *member* (without ``.shp``) inside the zip at *path*, or a loose file."""
    path: str
    member: str | None

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
        if self.member is None:
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
    dated: str                # date of the IUCN data, YYYY-MM-DD


def default_data_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "iucn")


def find_sources(data: str | None = None) -> list[Source]:
    """The range shapefiles in *data*: a folder of zips and shapefiles, one zip, or one ``.shp``.

    Raises ``ValueError`` when there are none, saying where to get them.
    """
    data = os.path.expanduser(data or default_data_dir())
    if os.path.isdir(data):
        files = [os.path.join(data, f) for f in sorted(os.listdir(data)) if f.lower().endswith((".zip", ".shp"))]
    else:
        files = [data] if os.path.isfile(data) else []
    sources = []
    for path in files:
        if path.lower().endswith(".shp"):
            sources.append(Source(path, None))
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                sources += [Source(path, name[:-4]) for name in archive.namelist() if name.lower().endswith(".shp")]
        except (OSError, zipfile.BadZipFile) as exc:
            logger.warning("Skipping %s, which is not a readable zip: %s", path, exc)
    if not sources:
        raise ValueError(f"No IUCN range files in {data}. Download range data (shapefiles) from "
                         "https://www.iucnredlist.org/resources/spatial-data-download and put the zip there, "
                         "or give its path with --iucn-data.")
    return sources


def _signature(sources: list[Source]) -> str:
    """A short hash of the range files, so a cached range is rebuilt when they change."""
    stamp = sorted({(os.path.abspath(s.path), os.path.getsize(s.path), int(os.path.getmtime(s.path)))
                    for s in sources})
    return hashlib.sha1(repr(stamp).encode()).hexdigest()[:10]


def _records(source: Source) -> list[dict]:
    """The attributes of every shape in *source* (only :data:`FIELDS`); empty if it is not IUCN's."""
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


def _read_shapes(source: Source, indices: list[int]) -> dict[int, shapely.Geometry | None]:
    """The shapes at *indices* of *source*, read in file order (seeking forward in the zip is cheap)."""
    shx = source.read("shx")
    found = {}
    with source.open_shp() as shp:
        for i in sorted(indices):
            offset, length = struct.unpack_from(">2i", shx, 100 + 8 * i)
            shp.seek(2 * offset + 8)
            found[i] = parse_polygon(shp.read(2 * length))
    return found


def _match(records: list[tuple[Source, int, dict]], wanted: str) -> tuple[str, str, list] | None:
    """The records of the taxon called *wanted*: (its name, rank, records), trying species, genus, family, order."""
    for field, rank in RANKS:
        hits = [r for r in records if str(r[2].get(field, "")).strip().lower() == wanted]
        if hits:
            name = str(hits[0][2][field]).strip()
            return (name.capitalize() if rank != "SPECIES" else name), rank, hits
    return None


def _suggestions(records: list[tuple[Source, int, dict]], wanted: str) -> str:
    names = {str(r[2].get(f, "")).strip().lower(): str(r[2].get(f, "")).strip()
             for r in records for f in ("sci_name", "genus")}
    close = difflib.get_close_matches(wanted, list(names), n=3, cutoff=0.75)
    return f" Did you mean {', '.join(repr(names[c]) for c in close)}?" if close else ""


def _build(sources: list[Source], name: str) -> Range:
    wanted = " ".join(name.split()).lower()
    records = []
    for source in sources:
        try:
            records += [(source, i, rec) for i, rec in enumerate(_records(source))]
        except IucnDataError:
            raise
        except Exception as exc:                 # zip, pyshp and file errors alike
            raise IucnDataError(f"Could not read {source.path}: {exc}") from exc
    if not records:
        raise IucnDataError("The IUCN range files have no sci_name field; are they IUCN range polygons?")

    common = None
    matched = _match(records, wanted)
    if matched is None:                          # a common name, or a synonym: ask GBIF
        from gbif import GbifDataError, resolve_taxon
        try:
            taxon = resolve_taxon(name)
        except (ValueError, GbifDataError):
            taxon = None
        if taxon is not None:
            matched = _match(records, taxon.name.lower())
            common = taxon.common if matched else None
    if matched is None:
        raise ValueError(f"The IUCN range files hold no species, genus or family called {name!r}."
                         + _suggestions(records, wanted))
    scientific, rank, hits = matched

    logger.info("Reading the IUCN range of %s (%d parts; one-time) …", scientific, len(hits))
    parts: dict[int, list] = {}
    for source in {h[0] for h in hits}:
        mine = [h for h in hits if h[0] == source]
        try:
            shapes = _read_shapes(source, [h[1] for h in mine])
        except Exception as exc:                 # zip, seek and geometry errors alike
            raise IucnDataError(f"Could not read the IUCN range of {scientific}: {exc}") from exc
        for _, i, rec in mine:
            shape = shapes[i]
            if shape is not None:
                kind = range_kind(int(rec.get("presence") or 1), int(rec.get("origin") or 1),
                                  int(rec.get("seasonal") or 1))
                parts.setdefault(kind, []).append(shapely.simplify(shape, SIMPLIFY_DEGREES))
    kinds = sorted(parts)
    geometries = [shapely.make_valid(shapely.union_all(parts[k])) for k in kinds]
    categories = Counter(str(h[2].get("category", "")).strip() for h in hits)
    species = len({str(h[2]["sci_name"]).strip().lower() for h in hits})
    category = categories.most_common(1)[0][0] or None if species == 1 else None
    return Range(scientific, rank, common, species, category, kinds, geometries,
                 max(s.dated() for s in {h[0] for h in hits}))


def load_range(name: str, data: str | None = None, cache_dir: str | None = None) -> Range:
    """The IUCN range of the species, genus, family or order *name*, from the range files in *data*.

    A scientific name is matched in the files; anything else (``lion``) is
    looked up in GBIF and its scientific name matched. Raises ``ValueError``
    when there are no range files or no taxon of that name, and
    :class:`IucnDataError` when a file cannot be read.
    """
    if not " ".join(name.split()):
        raise ValueError("Give a species name for the IUCN range, such as 'Panthera leo'.")
    sources = find_sources(data)
    cache_dir = cache_dir or os.path.join(default_data_dir(), "ranges")
    slug = "".join(c if c.isalnum() else "_" for c in " ".join(name.split()).lower())
    path = os.path.join(cache_dir, f"{slug}_{_signature(sources)}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        found = _build(sources, name)
        wkb = [bytes(shapely.to_wkb(g)) for g in found.geometries]
        os.makedirs(cache_dir, exist_ok=True)
        part = path + ".part.npz"
        np.savez_compressed(part, name=np.array(found.name), rank=np.array(found.rank),
                            common=np.array(found.common or ""), species=np.array(found.species),
                            category=np.array(found.category or ""), kinds=np.array(found.kinds, int),
                            wkb=np.frombuffer(b"".join(wkb), np.uint8), ends=np.cumsum([len(w) for w in wkb]),
                            dated=np.array(found.dated))
        os.replace(part, path)
    with np.load(path) as saved:
        blob, ends = saved["wkb"].tobytes(), saved["ends"]
        geometries = [shapely.from_wkb(blob[a:b]) for a, b in zip([0, *ends[:-1]], ends)]
        return Range(str(saved["name"]), str(saved["rank"]), str(saved["common"]) or None, int(saved["species"]),
                     str(saved["category"]) or None, saved["kinds"].tolist(), geometries, str(saved["dated"]))


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


def draw_range(ax: GeoAxes, found: Range, alpha: float = 0.45) -> None:
    """Fill each kind of range on *ax* in its colour, extinct parts beneath, the living range on top."""
    for kind, geometry in zip(found.kinds, found.geometries):
        ax.add_geometries([geometry], crs=ccrs.PlateCarree(), zorder=7.1, **_patch_style(kind, alpha))
    logger.info("IUCN range of %s drawn: %s.", found.name, ", ".join(RANGE_KINDS[k][0] for k in found.kinds))


def add_range_legend(ax: GeoAxes, found: Range, alpha: float = 0.45, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the kinds of range shown, the living range first."""
    kinds = sorted(found.kinds, reverse=True)
    handles = [mpatches.Patch(**_patch_style(k, alpha)) for k in kinds]
    labels = [RANGE_KINDS[k][0] for k in kinds]
    return _key(ax, handles, labels, f"Range of {range_label(found)} (IUCN Red List)", min(len(kinds), 4), x, y)


def iucn_attribution(found: Range) -> str:
    return (f"Range: IUCN ({found.dated[:4]}), The IUCN Red List of Threatened Species, "
            f"spatial data of {found.dated}")
