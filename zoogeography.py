"""Zoogeographic realms and regions of Holt et al. (2013), the update of Wallace's map.

Data source
-----------
Holt, B. G., Lessard, J.-P., Borregaard, M. K., et al. (2013). An Update of
Wallace's Zoogeographic Regions of the World. *Science* 339(6115), 74-78.
https://doi.org/10.1126/science.1228282 — GIS files from the Center for
Macroecology, Evolution and Climate (CMEC), University of Copenhagen:
https://macroecology.ku.dk/resources/wallace

From the ranges and family trees of 21,037 species of amphibians, birds and
mammals, it divides the land (north of 56°S) into 11 realms and 20 regions.

Terms: CMEC asks that any use of the map credit *Science*/AAAS and gives no
other licence, so the files are not shipped with this project: each user's
copy is downloaded from CMEC (~18 MB, once), and every map carries the credit.
The realms are filled into a 0.05° grid; the regions are kept as outlines.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from collections import Counter
from typing import NamedTuple

import numpy as np
import shapely
from shapely.geometry import Polygon, shape

import cartopy.crs as ccrs
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from biomes import NODATA, _download, _key, _swatch, burn

logger = logging.getLogger(__name__)

HOLT_URL = "https://macroecology.ku.dk/resources/wallace/cmec_regions___realms.zip"
CELLS_PER_DEGREE = 20     # 0.05° cells, as for the biomes
DATA_VERSION = 1          # bump when the cache changes, so old caches are rebuilt
REALM_FILE = "newRealms"  # realms, in degrees (WGS 84)
REGION_FILE = "Regions"   # regions, in metres on a plate carrée centred on 12°E

HOLT_ATTRIBUTION = "Zoogeographic realms and regions: Holt et al. (2013), Science 339:74-78, credit Science/AAAS"

# The regions file's frame: plate carrée in metres about this meridian, on the WGS 84 sphere radius
REGION_CENTRAL_LONGITUDE = 12.0
_METRES_PER_DEGREE = 6378137.0 * np.pi / 180
# Region outlines are simplified to about a cell; finer detail is lost at this scale anyway
_SIMPLIFY_DEGREES = 0.02

# A region is named on a part of it at least this share of its largest part's area
_LABEL_MIN_SHARE = 0.05

# Realm (as the data names it) -> (where, (R, G, B)), in key order
HOLT_REALMS: dict[str, tuple[str, tuple[int, int, int]]] = {
    "Nearctic":       ("North America and the Mexican highlands", (86, 140, 205)),
    "Panamanian":     ("Central America and the Caribbean", (150, 210, 90)),
    "Neotropical":    ("South America", (60, 170, 90)),
    "Palearctic":     ("Europe, northern and central Asia", (230, 160, 60)),
    "Saharo-Arabian": ("North Africa, Arabia, the Middle East", (235, 205, 140)),
    "Afrotropical":   ("Africa south of the Sahara", (205, 85, 70)),
    "Madagascan":     ("Madagascar and nearby islands", (150, 60, 50)),
    "Sino-Japanese":  ("China, Tibet, Japan", (220, 110, 150)),
    "Oriental":       ("South and Southeast Asia", (165, 95, 190)),
    "Australian":     ("Australia and New Zealand", (230, 205, 70)),
    "Oceanina":       ("New Guinea, Melanesia, Polynesia", (70, 190, 200)),
}


class HoltDataError(RuntimeError):
    """The Holt et al. map could not be downloaded or read."""


class Region(NamedTuple):
    """A zoogeographic region: its name and outline (degrees about :data:`REGION_CENTRAL_LONGITUDE`)."""
    name: str
    outline: shapely.Geometry


class HoltMap(NamedTuple):
    """The realm grid (values index :data:`HOLT_REALMS`, :data:`biomes.NODATA` elsewhere) and the regions."""
    realms: np.ndarray
    regions: list[Region]


# ---------------------------------------------------------------------------
# Download and reading
# ---------------------------------------------------------------------------


def _reader(archive: zipfile.ZipFile, name: str):
    """A pyshp reader for shapefile *name* in *archive*, wherever in it the file is."""
    import shapefile  # pyshp, which Cartopy depends on

    members = {os.path.basename(m).lower(): m for m in archive.namelist()}

    def member(ext: str) -> io.BytesIO:
        key = f"{name}.{ext}".lower()
        if key not in members:
            raise HoltDataError(f"{name}.{ext} is missing from the Holt et al. archive")
        return io.BytesIO(archive.read(members[key]))

    return shapefile.Reader(shp=member("shp"), shx=member("shx"), dbf=member("dbf"), encoding="latin-1")


def build(archive: zipfile.ZipFile, cells_per_degree: int = CELLS_PER_DEGREE) -> dict[str, np.ndarray]:
    """The realm grid and the region outlines (as WKB) from the CMEC archive."""
    order = list(HOLT_REALMS)
    grid = np.full((180 * cells_per_degree, 360 * cells_per_degree), NODATA, np.uint16)
    with _reader(archive, REALM_FILE) as realms:
        for record, polygon in zip(realms.iterRecords(), realms.iterShapes()):
            name = str(record.as_dict()["Realm"])
            if name not in HOLT_REALMS:
                raise HoltDataError(f"unknown realm {name!r} in the Holt et al. map")
            if polygon is not None:
                burn(grid, np.asarray(polygon.points, float).reshape(-1, 2), list(polygon.parts),
                     order.index(name), cells_per_degree)
    names, outlines = [], []
    with _reader(archive, REGION_FILE) as regions:
        for record, polygon in zip(regions.iterRecords(), regions.iterShapes()):
            if polygon is None:
                continue
            metres = shape(dict(polygon.__geo_interface__))
            degrees = shapely.transform(metres, lambda xy: xy / _METRES_PER_DEGREE)
            outlines.append(shapely.make_valid(degrees.simplify(_SIMPLIFY_DEGREES)))
            names.append(str(record.as_dict()["Regions"]))
    wkb = [bytes(shapely.to_wkb(outline)) for outline in outlines]
    return {
        "realms": grid,
        "region_names": np.array(names),
        "region_wkb": np.frombuffer(b"".join(wkb), np.uint8),
        "region_ends": np.cumsum([len(w) for w in wkb]),
    }


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "zoogeography")


def ensure_holt_data(cache_dir: str | None = None) -> str:
    """Path to the cached realm grid and region outlines, downloading the CMEC files if needed.

    The first run downloads ~18 MB. Raises :class:`HoltDataError` if the data
    cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"holt2013_{CELLS_PER_DEGREE}pd_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached zoogeographic realms: %s", path)
        return path
    os.makedirs(cache_dir, exist_ok=True)
    zip_path = os.path.join(cache_dir, "cmec_regions_realms.zip")
    try:
        if not os.path.isfile(zip_path):
            logger.info("Downloading Holt et al. (2013) zoogeographic realms from CMEC (one-time, ~18 MB) …")
            _download(HOLT_URL, zip_path, label="zoogeographic realms")
        with zipfile.ZipFile(zip_path) as archive:
            data = build(archive, CELLS_PER_DEGREE)
    except HoltDataError:
        raise
    except Exception as exc:                  # network, zip, pyshp and geometry errors alike
        raise HoltDataError(f"Could not read the Holt et al. map: {exc}") from exc

    part = path + ".part.npz"
    np.savez_compressed(part, realms=data["realms"], region_names=data["region_names"],
                        region_wkb=data["region_wkb"], region_ends=data["region_ends"])
    os.replace(part, path)
    os.remove(zip_path)
    logger.info("Zoogeographic realms ready: %s", path)
    return path


def load_holt(cache_dir: str | None = None) -> HoltMap:
    """The realm grid and the region outlines."""
    with np.load(ensure_holt_data(cache_dir)) as data:
        wkb = data["region_wkb"].tobytes()
        starts = [0, *data["region_ends"][:-1]]
        outlines = [shapely.from_wkb(wkb[a:b]) for a, b in zip(starts, data["region_ends"])]
        regions = [Region(str(n), o) for n, o in zip(data["region_names"], outlines)]
        return HoltMap(data["realms"], regions)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def realm_rgba(realms: np.ndarray, alpha: float) -> np.ndarray:
    """An RGBA image of the realm grid; outside the realms is clear."""
    palette = np.zeros((NODATA + 1, 4), np.uint8)
    for value, (_, rgb) in enumerate(HOLT_REALMS.values()):
        palette[value] = (*rgb, round(alpha * 255))
    return palette[realms]


def add_holt_realm_overlay(ax: GeoAxes, holt: HoltMap, alpha: float = 0.6, regrid_shape: int = 750) -> None:
    """Render Holt's realms on *ax*."""
    ax.imshow(
        realm_rgba(holt.realms, alpha),
        origin="upper",
        extent=(-180, 180, -90, 90),
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )
    logger.info("Zoogeographic realm overlay rendered (alpha=%.2f).", alpha)


def _on_globe(ax: GeoAxes, lon: float, lat: float, margin: float = 0.9) -> bool:
    """True if (*lon*, *lat*) shows on *ax*, within *margin* of the disc's radius (clear of the rim)."""
    x, y = ax.projection.transform_point(lon, lat, ccrs.PlateCarree())
    if not (np.isfinite(x) and np.isfinite(y)):
        return False
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    return ((2 * x - x0 - x1) / (x1 - x0)) ** 2 + ((2 * y - y0 - y1) / (y1 - y0)) ** 2 <= margin ** 2


def label_point(ax: GeoAxes, region: Region) -> tuple[float, float] | None:
    """Where to name *region* on *ax*: inside its largest part that shows, as (lon, lat), or ``None``.

    Specks (under :data:`_LABEL_MIN_SHARE` of the largest part) are not
    labelled: a region's outlying islet would name it far from home.
    """
    parts = sorted((p for p in getattr(region.outline, "geoms", [region.outline]) if isinstance(p, Polygon)),
                   key=lambda p: p.area, reverse=True)
    for part in parts:
        if part.area < _LABEL_MIN_SHARE * parts[0].area:
            break
        point = part.representative_point()
        lon = (point.x + REGION_CENTRAL_LONGITUDE + 180) % 360 - 180
        if _on_globe(ax, lon, point.y):
            return lon, point.y
    return None


def draw_holt_regions(ax: GeoAxes, holt: HoltMap, labels: bool = True) -> None:
    """Outline Holt's 20 regions on *ax* and, with *labels*, name those that show."""
    frame = ccrs.PlateCarree(central_longitude=REGION_CENTRAL_LONGITUDE)
    ax.add_geometries([r.outline for r in holt.regions], crs=frame, facecolor="none",
                      edgecolor="#222222", linewidth=1.2, zorder=6)
    if not labels:
        return
    for region in holt.regions:
        where = label_point(ax, region)
        if where is None:
            continue
        ax.text(*where, region.name, transform=ccrs.PlateCarree(), fontsize=12, fontweight="bold",
                color="white", ha="center", va="center", zorder=8,
                path_effects=[pe.withStroke(linewidth=3, foreground="black")])


def add_holt_realm_legend(ax: GeoAxes, visible: Counter[int], y: float = -0.01, x: float = 0.5) -> Legend | None:
    """A key naming the realms on the map and where each lies.

    *visible* counts each realm's share of the map (see
    :func:`biomes.visible_values`). Returns ``None`` when no realm shows.
    """
    shown = [name for value, name in enumerate(HOLT_REALMS) if value in visible]
    if not shown:
        return None
    handles = [_swatch(HOLT_REALMS[name][1]) for name in shown]
    labels = [f"{name}: {HOLT_REALMS[name][0]}" for name in shown]
    return _key(ax, handles, labels, "Zoogeographic realms (Holt et al. 2013, an update of Wallace's)",
                min(len(shown), 2), x, y)

