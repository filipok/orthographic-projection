"""The ice sheets of the last ice age, from PaleoMIST 1.0.

Data source
-----------
Gowan, E. J., Zhang, X., Khosravi, S., et al. (2021). A new global ice sheet
reconstruction for the past 80 000 years. *Nature Communications* 12, 1199.
https://doi.org/10.1038/s41467-021-21469-w — PaleoMIST 1.0, PANGAEA,
https://doi.org/10.1594/PANGAEA.905800, CC BY 4.0: the margins of the North
American (with Greenland and Iceland), Eurasian, Patagonian and Antarctic ice
sheets every 2,500 years.

The dataset is one 3.7 GB archive; only its four margin files for
:data:`YEARS_AGO` (~2.3 MB) are read from it, by HTTP range requests, and
cached under ``~/.cache/ortho_tiles/iceage/``. Mountain glaciers outside those
four sheets (the Alps, the Himalaya) are not in it.
"""

from __future__ import annotations

import io
import logging
import os
import urllib.request
import zipfile

import numpy as np
import shapely
from shapely.geometry import Polygon

import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection

from biomes import burn
from borders import _pack, _unpack
from hydrology import project_lines

logger = logging.getLogger(__name__)

PALEOMIST_URL = "https://hs.pangaea.de/Maps/Global_Ice_Sheets/Gowan_ice_reconstruction.zip"
YEARS_AGO = 20000
REGIONS = ("North_America", "Eurasia", "Patagonia", "Antarctica")
MEMBER = "ice_reconstruction/margins/{region}/{years}.gmt"
DATA_VERSION = 1
CELLS_PER_DEGREE = 10

ICE_SHEETS_ATTRIBUTION = (f"Ice sheets {YEARS_AGO:,} years ago: PaleoMIST 1.0 (Gowan et al. 2021), "
                          "PANGAEA, CC BY 4.0")
ICE_RGB = (244, 248, 252)
ICE_EDGE = "#6f8fae"


class IceAgeDataError(RuntimeError):
    """The ice sheet margins could not be downloaded or read."""


class HttpRangeFile(io.RawIOBase):
    """A remote file read by HTTP range requests, so :mod:`zipfile` can take one member of a large archive."""

    def __init__(self, url: str, timeout: float = 120) -> None:
        super().__init__()
        self.url, self.timeout, self.position = url, timeout, 0
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ortho/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            self.size = int(resp.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = base + offset
        return self.position

    def readinto(self, buffer) -> int:  # pyright: ignore[reportIncompatibleMethodOverride]
        if self.position >= self.size:
            return 0
        end = min(self.position + len(buffer), self.size) - 1
        request = urllib.request.Request(self.url, headers={"User-Agent": "ortho/1.0",
                                                            "Range": f"bytes={self.position}-{end}"})
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            data = resp.read()
        buffer[:len(data)] = data
        self.position += len(data)
        return len(data)


def parse_gmt(text: str) -> list[Polygon]:
    """The polygons of a GMT/OGR text file: ``# @P`` starts a polygon, ``# @H`` a hole in it."""
    polygons: list[list[list[tuple[float, float]]]] = []
    for line in text.splitlines():
        if line.startswith("# @P"):
            polygons.append([[]])
        elif line.startswith("# @H") and polygons:
            polygons[-1].append([])
        elif line.strip() and not line.startswith(("#", ">")) and polygons:
            x, y = line.split()[:2]
            polygons[-1][-1].append((float(x), float(y)))
    if not polygons and any(line.strip() and not line.startswith(("#", ">")) for line in text.splitlines()):
        # A file with one polygon may leave out the @P mark
        return parse_gmt("# @P\n" + text)
    return [Polygon(rings[0], rings[1:]) for rings in polygons if len(rings[0]) >= 3]


def around_the_pole(ring: np.ndarray, south: bool = True) -> Polygon:
    """The polygon a margin ring encircling a pole bounds, closed through the pole.

    A ring around Antarctica, drawn in longitude and latitude, jumps at the
    date line and leaves the pole outside; cut there and walked round from
    -180° to 180°, then along the pole's edge back, it encloses the ice.
    """
    lon, lat = ring[:, 0], ring[:, 1]
    jump = int(np.argmax(np.abs(np.diff(lon)))) + 1
    lon, lat = np.roll(lon[:-1], -jump), np.roll(lat[:-1], -jump)
    if lon[0] > lon[-1]:                         # walk from west to east
        lon, lat = lon[::-1], lat[::-1]
    pole = -90.0 if south else 90.0
    points = [(-180.0, lat[0]), *zip(lon, lat), (180.0, lat[-1]), (180.0, pole), (-180.0, pole)]
    return Polygon(points)


def read_margins(texts: dict[str, str]) -> list[shapely.Geometry]:
    """The ice sheet polygons from each region's margin file, Antarctica closed through the pole."""
    sheets: list[shapely.Geometry] = []
    for region, text in texts.items():
        for polygon in parse_gmt(text):
            ring = np.asarray(polygon.exterior.coords)
            if region == "Antarctica" and np.ptp(ring[:, 0]) > 300:
                polygon = around_the_pole(ring)
            sheets.append(shapely.make_valid(polygon))
    return sheets


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "iceage")


def load_ice_sheets(cache_dir: str | None = None) -> list[shapely.Geometry]:
    """The ice sheets :data:`YEARS_AGO`, reading their margins from PaleoMIST once."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"paleomist_{YEARS_AGO}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Reading the ice sheets of %d years ago from PaleoMIST (one-time, ~2.3 MB) …", YEARS_AGO)
        try:
            archive = zipfile.ZipFile(io.BufferedReader(HttpRangeFile(PALEOMIST_URL), buffer_size=1 << 20))
            with archive:
                texts = {region: archive.read(MEMBER.format(region=region, years=YEARS_AGO)).decode("utf-8")
                         for region in REGIONS}
            sheets = read_margins(texts)
        except Exception as exc:              # network, archive and geometry errors alike
            raise IceAgeDataError(f"Could not read the PaleoMIST ice sheets: {exc}") from exc
        part = path + ".part.npz"
        np.savez_compressed(part, **_pack(sheets))
        os.replace(part, path)
    with np.load(path) as data:
        return _unpack(data)


def ice_sheet_grid(sheets: list[shapely.Geometry], cells_per_degree: int = CELLS_PER_DEGREE) -> np.ndarray:
    """True for the cells of a global grid (rows from 90°N) under the ice sheets."""
    grid = np.zeros((180 * cells_per_degree, 360 * cells_per_degree), np.uint8)
    for sheet in sheets:
        for polygon in shapely.get_parts(sheet):
            if not isinstance(polygon, Polygon):
                continue
            rings = [np.asarray(polygon.exterior.coords)[:, :2]] + [np.asarray(r.coords)[:, :2]
                                                                     for r in polygon.interiors]
            parts = list(np.cumsum([0] + [len(r) for r in rings[:-1]]))
            burn(grid, np.concatenate(rings), parts, 1, cells_per_degree)
    return grid.astype(bool)


def margin_lines(sheet: shapely.Geometry) -> shapely.Geometry:
    """*sheet*'s outline without the edges added to close it round a pole (along ±180° or a pole's latitude)."""
    pieces = []
    for ring in shapely.get_parts(shapely.boundary(sheet)):
        xy = np.asarray(ring.coords)[:, :2]
        artificial = (((np.abs(xy[:-1, 0]) == 180) & (np.abs(xy[1:, 0]) == 180))
                      | ((np.abs(xy[:-1, 1]) == 90) & (np.abs(xy[1:, 1]) == 90)))
        start = 0
        for i in np.flatnonzero(artificial):
            if i + 1 - start >= 2:
                pieces.append(xy[start:i + 1])
            start = i + 1
        if len(xy) - start >= 2:
            pieces.append(xy[start:])
    return shapely.multilinestrings(pieces) if pieces else shapely.MultiLineString()


def draw_ice_sheets(ax: GeoAxes, sheets: list[shapely.Geometry], alpha: float = 0.85, regrid_shape: int = 750) -> None:
    """Draw the ice sheets on *ax*, white within their margins, and outline them.

    Filled from a grid and outlined as lines, so the polygon round the South
    Pole never goes through Cartopy's polygon clipping.
    """
    grid = ice_sheet_grid(sheets)
    rgba = np.zeros(grid.shape + (4,), np.uint8)
    rgba[grid] = (*ICE_RGB, round(alpha * 255))
    ax.imshow(rgba, origin="upper", extent=(-180, 180, -90, 90), transform=ccrs.PlateCarree(),
              interpolation="nearest", regrid_shape=regrid_shape, zorder=5)
    lines = [run for sheet in sheets for run in project_lines(ax, margin_lines(sheet))]
    ax.add_collection(LineCollection(lines, colors=ICE_EDGE, linewidths=0.8, zorder=5.1))
    logger.info("Ice sheets of %d years ago drawn.", YEARS_AGO)
