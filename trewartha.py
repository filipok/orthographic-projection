"""Trewartha climate classification overlay, computed from CHELSA v2.1.

Data source
-----------
Karger, D. N., Conrad, O., Böhner, J., Kawohl, T., Kreft, H., Soria-Auza,
R. W., Zimmermann, N. E., Linder, H. P. & Kessler, M. (2017). Climatologies at
high resolution for the earth's land surface areas. *Scientific Data* 4,
170122. https://doi.org/10.1038/sdata.2017.122 — version 2.1 monthly mean
temperature and precipitation for 1981-2010, CC0 1.0 (public domain).

The 1 km files are Cloud-Optimised GeoTIFFs with reduced copies inside, so
only a ~15 km copy of each month is fetched (about 52 MB for all 24 files),
classified once and cached. The oceans are masked out with Natural Earth land.

Rules
-----
The Köppen-Trewartha classification as used by Belda et al. (2014), Climate
Research 59, 1-13 (https://doi.org/10.3354/cr01204), with Patton's (1962)
dry-climate threshold. Dry climates are tested first:

- **B** (dry): annual precipitation R (cm) < 2.3 T - 0.64 Pw + 41, where T is
  the mean annual temperature (°C) and Pw the percentage of the year's
  precipitation that falls in the six winter months. **BW** (desert) below
  half the threshold, **BS** (steppe) otherwise.
- **A** (tropical): every month >= 18 °C. **Ar** has at most two dry months
  (< 60 mm); otherwise **Aw** (dry months mostly in winter) or **As** (mostly
  in summer).
- **C** (subtropical): 8-12 months >= 10 °C. **Cs** (dry summer) and **Cw**
  (dry winter) need annual precipitation < 890 mm and the season's driest
  month < 30 mm and under a third of the other season's wettest; **Cf**
  otherwise (criteria as tabulated by Huang et al. 2023, Frontiers in Earth
  Science 10, 1083899, https://doi.org/10.3389/feart.2022.1083899).
- **D** (temperate): 4-7 months >= 10 °C; **Do** coldest month > 0 °C, else **Dc**.
- **E** (boreal): 1-3 months >= 10 °C; **Eo** coldest month > -10 °C, else **Ec**.
- **F** (polar): no month >= 10 °C; **Ft** warmest month > 0 °C, else **Fi**.

Winter is October-March in the northern hemisphere and April-September in
the southern.

Highlands
---------
Trewartha's highland group **H** marks places whose climate group is set by
their altitude, but he gave no numerical test. Here a cell is H when its
ground is at least 1,500 m high and taking away the height above 1,500 m,
warming every month by 6.5 °C per km (the standard atmosphere's lapse
rate), would put it in another group. Tibet, the high Andes and the
Ethiopian Highlands become H; Denver, Johannesburg and the Iranian plateau,
high but with the climate of the lowlands around them, keep their class.
Heights come from the Mapzen terrain tiles (see :mod:`elevation`), sampled
at each cell's centre at zoom 4 (~10 km).
"""

from __future__ import annotations

import concurrent.futures
import io
import logging
import os
import struct
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

import numpy as np
import shapely
from PIL import Image

import matplotlib.patches as mpatches
import cartopy.crs as ccrs
import cartopy.io.shapereader as shapereader
from cartopy.mpl.geoaxes import GeoAxes

from elevation import ElevationDataError, elevation_grid

logger = logging.getLogger(__name__)

CHELSA_URL = ("https://os.unil.cloud.switch.ch/chelsa02/chelsa/global/climatologies/{var}/1981-2010/"
              "CHELSA_{var}_{month:02d}_1981-2010_V.2.1.tif")
# Reduced copy inside each file: 4 = 1/16 of 1 km, 2700 x 1305 cells of 0.133° (~15 km)
OVERVIEW_LEVEL = 4
DATA_VERSION = 2  # bump when the rules change, so old caches are rebuilt

TREWARTHA_ATTRIBUTION = "Climate data: CHELSA v2.1 (Karger et al. 2017), CC0; Trewartha classes computed"
# The highland group uses elevation.py's heights, so maps also carry ELEVATION_ATTRIBUTION

# Highlands: ground at least this high whose group the height above it changes
HIGHLAND_MIN_M = 1500.0
LAPSE_RATE_C_PER_KM = 6.5

# Grid code -> (symbol, description, (R, G, B)); 0 is ocean / no data
TREWARTHA_CLASSES: dict[int, tuple[str, str, tuple[int, int, int]]] = {
    1:  ("Ar", "Tropical wet",                (  0,  60, 160)),
    2:  ("Aw", "Tropical, dry winter",        ( 70, 150, 230)),
    3:  ("As", "Tropical, dry summer",        (130, 190, 250)),
    4:  ("BW", "Desert",                      (230,  40,  40)),
    5:  ("BS", "Steppe",                      (245, 165,  80)),
    6:  ("Cs", "Subtropical, dry summer",     (240, 230,  60)),
    7:  ("Cw", "Subtropical, dry winter",     (150, 210,  80)),
    8:  ("Cf", "Subtropical humid",           ( 50, 160,  60)),
    9:  ("Do", "Temperate oceanic",           ( 60, 200, 190)),
    10: ("Dc", "Temperate continental",       ( 40, 120, 130)),
    11: ("Eo", "Boreal oceanic",              (170, 140, 210)),
    12: ("Ec", "Boreal continental",          (110,  70, 160)),
    13: ("Ft", "Tundra",                      (175, 175, 175)),
    14: ("Fi", "Ice cap",                     (235, 240, 245)),
    15: ("H",  "Highland",                    (140,  90,  60)),
}
_CODES = {symbol: code for code, (symbol, _, _) in TREWARTHA_CLASSES.items()}

# Up to this many classes, the key spells out each class's name
_NAMED_KEY_MAX = 8

_NODATA = 65535


class TrewarthaDataError(RuntimeError):
    """The CHELSA data could not be downloaded or read."""


# ---------------------------------------------------------------------------
# Class names
# ---------------------------------------------------------------------------


def resolve_trewartha_classes(specs: Sequence[str]) -> tuple[int, ...]:
    """Grid codes of the classes named by *specs*, in code order.

    Each spec is a class symbol (``"Do"``) or a group letter (``"C"`` is Cs,
    Cw and Cf). Matching ignores case. Raises ``ValueError`` for a spec that
    matches no class.
    """
    selected: set[int] = set()
    for spec in specs:
        key = spec.strip().lower()
        matches = [code for code, (symbol, _, _) in TREWARTHA_CLASSES.items()
                   if key and symbol.lower().startswith(key)]
        if not matches:
            valid = ", ".join(symbol for symbol, _, _ in TREWARTHA_CLASSES.values())
            raise ValueError(f"Unknown Trewartha class {spec!r}. Use a class ({valid}) "
                             "or a group letter such as C or D.")
        selected.update(matches)
    return tuple(sorted(selected))


# ---------------------------------------------------------------------------
# Reading one reduced level of a remote Cloud-Optimised GeoTIFF
# ---------------------------------------------------------------------------

_TIFF_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 12: ("d", 8), 16: ("Q", 8)}


def _fetch_range(url: str, start: int, end: int, timeout: float = 120) -> bytes:
    """Bytes *start* to *end* (inclusive) of *url*."""
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 206:
            raise TrewarthaDataError(f"{url} ignored the range request (HTTP {resp.status})")
        return resp.read()


def _read_ifds(head: bytes) -> list[dict[int, tuple]]:
    """Every image directory of a little-endian classic TIFF whose directories fit in *head*."""
    if head[:4] != b"II*\x00":
        raise TrewarthaDataError("not a little-endian TIFF")
    ifds = []
    offset = struct.unpack("<I", head[4:8])[0]
    while offset:
        if offset + 2 > len(head):
            raise TrewarthaDataError("TIFF directories do not fit in the header read")
        count = struct.unpack("<H", head[offset:offset + 2])[0]
        tags: dict[int, tuple] = {}
        for i in range(count):
            entry = head[offset + 2 + 12 * i: offset + 14 + 12 * i]
            tag, typ, n = struct.unpack("<HHI", entry[:8])
            fmt, size = _TIFF_TYPES.get(typ, ("B", 1))
            total = size * n
            if total <= 4:
                raw = entry[8:8 + total]
            else:
                start = struct.unpack("<I", entry[8:12])[0]
                raw = head[start:start + total]
            if len(raw) != total:
                continue  # a large tag beyond the header read; not needed here
            tags[tag] = (raw.decode(errors="replace").rstrip("\x00"),) if typ == 2 else struct.unpack("<" + fmt * n, raw)
        ifds.append(tags)
        offset = struct.unpack("<I", head[offset + 2 + 12 * count: offset + 6 + 12 * count])[0]
    return ifds


def _single_tile_tiff(tile: bytes, width: int, height: int, ifd: dict[int, tuple]) -> bytes:
    """Wrap one compressed tile in a minimal one-tile TIFF, for Pillow to decode."""
    entries = [
        (256, 4, width), (257, 4, height), (258, 3, ifd[258][0]), (259, 3, ifd[259][0]),
        (262, 3, 1), (277, 3, 1), (284, 3, 1), (317, 3, ifd.get(317, (1,))[0]),
        (322, 4, width), (323, 4, height), (324, 4, None), (325, 4, len(tile)),
        (339, 3, ifd.get(339, (1,))[0]),
    ]
    data_offset = 8 + 2 + 12 * len(entries) + 4
    out = bytearray(b"II*\x00" + struct.pack("<IH", 8, len(entries)))
    for tag, typ, value in entries:
        value = data_offset if value is None else value
        out += struct.pack("<HHI", tag, typ, 1)
        out += struct.pack("<HH", value, 0) if typ == 3 else struct.pack("<I", value)
    out += struct.pack("<I", 0) + tile
    return bytes(out)


def read_cog_level(url: str, level: int, header_bytes: int = 262144) -> tuple[np.ndarray, tuple[float, float, float]]:
    """Read reduced level *level* of the Cloud-Optimised GeoTIFF at *url*.

    Returns ``(grid, (west, north, cell_size))``: the uint16 grid (rows from
    north to south) and its geographic origin and cell size in degrees.
    Only the directories and that level's tiles are downloaded.
    """
    try:
        ifds = _read_ifds(_fetch_range(url, 0, header_bytes - 1))
        if level >= len(ifds):
            raise TrewarthaDataError(f"{url} has no reduced level {level}")
        full, ifd = ifds[0], ifds[level]
        width, height = ifd[256][0], ifd[257][0]
        tile_w, tile_h = ifd[322][0], ifd[323][0]
        across = -(-width // tile_w)
        grid = np.full((-(-height // tile_h) * tile_h, across * tile_w), _NODATA, dtype=np.uint16)
        for index, (offset, count) in enumerate(zip(ifd[324], ifd[325])):
            if not count:
                continue
            tiff = _single_tile_tiff(_fetch_range(url, offset, offset + count - 1), tile_w, tile_h, ifd)
            row, col = divmod(index, across)
            grid[row * tile_h:(row + 1) * tile_h, col * tile_w:(col + 1) * tile_w] = np.asarray(
                Image.open(io.BytesIO(tiff)), dtype=np.uint16)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, KeyError) as exc:
        if isinstance(exc, TrewarthaDataError):
            raise
        raise TrewarthaDataError(f"Could not read {url}: {exc}") from exc

    west, north = full[33922][3], full[33922][4]
    cell = full[33550][0] * full[256][0] / width
    return grid[:height, :width], (west, north, cell)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TrewarthaAccumulator:
    """Monthly temperature and precipitation reduced to what the rules need.

    Months can be added in any order; *winter* is a boolean grid per month
    marking the cells where that month is in the winter half-year. *offset*
    (°C, per cell) is added to every month's temperature, to classify the
    climate a cell would have at another height.
    """

    def __init__(self, shape: tuple[int, int], offset: np.ndarray | None = None) -> None:
        f32 = np.float32
        self.offset = offset
        self.months = 0
        self.temp_sum = np.zeros(shape, f32)
        self.temp_min = np.full(shape, np.inf, f32)
        self.temp_max = np.full(shape, -np.inf, f32)
        self.warm_months = np.zeros(shape, np.uint8)      # months >= 10 °C
        self.precip = np.zeros(shape, f32)
        self.precip_winter = np.zeros(shape, f32)
        self.dry_winter = np.zeros(shape, np.uint8)       # months < 60 mm
        self.dry_summer = np.zeros(shape, np.uint8)
        self.min_winter = np.full(shape, np.inf, f32)
        self.max_winter = np.zeros(shape, f32)
        self.min_summer = np.full(shape, np.inf, f32)
        self.max_summer = np.zeros(shape, f32)

    def add_month(self, temp_c: np.ndarray, precip_mm: np.ndarray, winter: np.ndarray) -> None:
        if self.offset is not None:
            temp_c = temp_c + self.offset
        self.months += 1
        self.temp_sum += temp_c
        np.minimum(self.temp_min, temp_c, out=self.temp_min)
        np.maximum(self.temp_max, temp_c, out=self.temp_max)
        self.warm_months += temp_c >= 10
        self.precip += precip_mm
        self.precip_winter += np.where(winter, precip_mm, 0)
        dry = precip_mm < 60
        self.dry_winter += dry & winter
        self.dry_summer += dry & ~winter
        self.min_winter = np.where(winter, np.minimum(self.min_winter, precip_mm), self.min_winter)
        self.max_winter = np.where(winter, np.maximum(self.max_winter, precip_mm), self.max_winter)
        self.min_summer = np.where(winter, self.min_summer, np.minimum(self.min_summer, precip_mm))
        self.max_summer = np.where(winter, self.max_summer, np.maximum(self.max_summer, precip_mm))

    def classify(self) -> np.ndarray:
        """Grid codes (see :data:`TREWARTHA_CLASSES`) for every cell."""
        if self.months != 12:
            raise ValueError(f"need 12 months, got {self.months}")
        temp = self.temp_sum / 12
        precip_cm = self.precip / 10
        winter_pct = np.where(self.precip > 0, 100 * self.precip_winter / np.maximum(self.precip, 1e-9), 50)
        threshold = 2.3 * temp - 0.64 * winter_pct + 41            # Patton (1962), cm
        codes = np.zeros(temp.shape, np.uint8)

        # Temperature groups first, then B overrides them where it is dry
        warm = self.warm_months
        tropical = self.temp_min >= 18
        dry_months = self.dry_winter + self.dry_summer
        codes[tropical] = np.where(dry_months[tropical] <= 2, _CODES["Ar"],
                                   np.where(self.dry_winter[tropical] >= self.dry_summer[tropical],
                                            _CODES["Aw"], _CODES["As"]))

        sub = ~tropical & (warm >= 8)
        seasonal = self.precip < 890
        dry_summer = seasonal & (self.min_summer < 30) & (self.min_summer < self.max_winter / 3)
        dry_winter = seasonal & (self.min_winter < 30) & (self.min_winter < self.max_summer / 3)
        codes[sub] = np.where(dry_summer[sub], _CODES["Cs"],
                              np.where(dry_winter[sub], _CODES["Cw"], _CODES["Cf"]))

        temperate = (warm >= 4) & (warm <= 7)
        codes[temperate] = np.where(self.temp_min[temperate] > 0, _CODES["Do"], _CODES["Dc"])
        boreal = (warm >= 1) & (warm <= 3)
        codes[boreal] = np.where(self.temp_min[boreal] > -10, _CODES["Eo"], _CODES["Ec"])
        polar = warm == 0
        codes[polar] = np.where(self.temp_max[polar] > 0, _CODES["Ft"], _CODES["Fi"])

        dry = precip_cm < threshold
        codes[dry] = np.where(precip_cm[dry] < threshold[dry] / 2, _CODES["BW"], _CODES["BS"])
        return codes


# Group letter of each code: A=1, B=2 … F=6, H=7; 0 for ocean
_GROUP = np.zeros(256, np.uint8)
for _code, (_symbol, _, _) in TREWARTHA_CLASSES.items():
    _GROUP[_code] = "ABCDEFH".index(_symbol[0]) + 1


def highland_offset(height: np.ndarray) -> np.ndarray:
    """Warming (°C) from taking away each cell's height above :data:`HIGHLAND_MIN_M`."""
    above = np.nan_to_num(height - HIGHLAND_MIN_M, nan=0.0)
    return (np.maximum(above, 0) * LAPSE_RATE_C_PER_KM / 1000).astype(np.float32)


def highlands(codes: np.ndarray, lowered: np.ndarray, height: np.ndarray) -> np.ndarray:
    """True where the ground is high enough and its height changes the climate group.

    *lowered* holds the classes of the same cells with :func:`highland_offset`
    applied to their temperatures.
    """
    high = np.nan_to_num(height, nan=0.0) >= HIGHLAND_MIN_M
    return high & (codes > 0) & (_GROUP[codes] != _GROUP[lowered])


def winter_months(lat: np.ndarray) -> dict[int, np.ndarray]:
    """For each month 1-12, which rows (by latitude) are in their winter half-year."""
    north = lat >= 0
    return {m: north if m in (10, 11, 12, 1, 2, 3) else ~north for m in range(1, 13)}


def land_mask(
    shape: tuple[int, int],
    west: float, north: float, cell: float,
    natural_earth: Callable[..., str] = shapereader.natural_earth,
) -> np.ndarray:
    """True for cells whose centre is on land (Natural Earth 1:50m land)."""
    land = shapely.union_all(list(shapereader.Reader(natural_earth("50m", "physical", "land")).geometries()))
    shapely.prepare(land)
    rows, cols = shape
    lons = west + (np.arange(cols) + 0.5) * cell
    lats = north - (np.arange(rows) + 0.5) * cell
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    return shapely.contains_xy(land, lon_grid, lat_grid)


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "trewartha")


def _read_month(var: str, month: int) -> tuple[str, int, np.ndarray, tuple[float, float, float]]:
    grid, geo = read_cog_level(CHELSA_URL.format(var=var, month=month), OVERVIEW_LEVEL)
    return var, month, grid, geo


def ensure_trewartha_data(cache_dir: str | None = None) -> str:
    """Path to the cached Trewartha class grid, computing it from CHELSA if needed.

    The first run downloads about 75 MB: a ~15 km copy of 24 monthly files
    (52 MB) and the terrain tiles for the highlands (25 MB, shared with the
    elevation layer's cache).
    Raises :class:`TrewarthaDataError` if the data cannot be obtained.
    """
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"trewartha_chelsa21_1981-2010_level{OVERVIEW_LEVEL}_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        logger.info("Using cached Trewartha classes: %s", path)
        return path

    logger.info("Computing Trewartha classes from CHELSA v2.1 (one-time download, ~75 MB) …")
    months: dict[tuple[str, int], np.ndarray] = {}
    geo = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(_read_month, var, m) for var in ("tas", "pr") for m in range(1, 13)]
        for job in concurrent.futures.as_completed(jobs):
            var, month, grid, month_geo = job.result()
            months[(var, month)] = grid
            geo = geo or month_geo
            logger.info("  CHELSA %s %02d read (%d of 24)", var, month, len(months))
    assert geo is not None
    west, north, cell = geo

    shape = months[("tas", 1)].shape
    try:
        height = elevation_grid(shape, west, north, cell, zoom=4)
    except ElevationDataError as exc:
        raise TrewarthaDataError(str(exc)) from exc
    lat = north - (np.arange(shape[0]) + 0.5) * cell
    winter = winter_months(lat)
    acc = TrewarthaAccumulator(shape)
    lowered = TrewarthaAccumulator(shape, offset=highland_offset(height))
    for month in range(1, 13):
        temp_raw, precip_raw = months.pop(("tas", month)), months.pop(("pr", month))
        temp_c = temp_raw.astype(np.float32) / 10 - 273.15
        precip_mm = precip_raw.astype(np.float32) / 10
        month_winter = np.broadcast_to(winter[month][:, None], shape)
        acc.add_month(temp_c, precip_mm, month_winter)
        lowered.add_month(temp_c, precip_mm, month_winter)
    codes = acc.classify()
    codes[~land_mask(shape, west, north, cell)] = 0
    codes[highlands(codes, lowered.classify(), height)] = _CODES["H"]

    part = path + ".part.npz"
    np.savez_compressed(part, codes=codes, geo=np.array([west, north, cell]))
    os.replace(part, path)
    logger.info("Trewartha classes ready: %s", path)
    return path


def load_trewartha(cache_dir: str | None = None) -> tuple[np.ndarray, tuple[float, float, float]]:
    """The class grid (rows north to south) and its ``(west, north, cell)``."""
    with np.load(ensure_trewartha_data(cache_dir)) as data:
        west, north, cell = (float(v) for v in data["geo"])
        return data["codes"], (west, north, cell)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def trewartha_rgba(codes: np.ndarray, alpha: float, classes: Sequence[int] | None = None) -> np.ndarray:
    """An RGBA image of *codes*; ocean and unselected classes are clear."""
    palette = np.zeros((256, 4), np.uint8)
    for code, (_, _, rgb) in TREWARTHA_CLASSES.items():
        if not classes or code in classes:
            palette[code] = (*rgb, round(alpha * 255))
    return palette[codes]


def add_trewartha_overlay(
    ax: GeoAxes,
    alpha: float = 0.45,
    cache_dir: str | None = None,
    regrid_shape: int = 750,
    classes: Sequence[int] | None = None,
) -> None:
    """Render the Trewartha overlay on *ax* (above the tiles, like Köppen-Geiger)."""
    codes, (west, north, cell) = load_trewartha(cache_dir)
    rows, cols = codes.shape
    ax.imshow(
        trewartha_rgba(codes, alpha, classes),
        origin="upper",
        extent=(west, west + cols * cell, north - rows * cell, north),
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )
    logger.info("Trewartha overlay rendered (alpha=%.2f).", alpha)


def add_trewartha_legend(ax: GeoAxes, x: float = 0.5, classes: Sequence[int] | None = None) -> None:
    """Add a compact Trewartha legend strip below the globe, like the Köppen-Geiger one."""
    shown = [code for code in TREWARTHA_CLASSES if not classes or code in classes]
    named = len(shown) <= _NAMED_KEY_MAX
    handles, labels = [], []
    for code in shown:
        symbol, description, (r, g, b) = TREWARTHA_CLASSES[code]
        handles.append(mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#555555", linewidth=0.4))
        labels.append(f"{symbol}: {description}" if named else symbol)
    legend = ax.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(x, -0.06),
        ncol=min(len(handles), 4) if named else len(handles),
        fontsize=8 if named else 6.5,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=1.2,
        handleheight=1.0,
        columnspacing=0.6,
        handletextpad=0.3,
        borderpad=0.5,
        title="Trewartha Climate Classification",
        title_fontsize=7.5,
    )
    legend.get_title().set_fontweight("bold")
