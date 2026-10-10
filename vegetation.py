"""Vegetation layers from NASA GIBS: MODIS land cover and monthly NDVI greenness.

Data sources
------------
- **Land cover:** MODIS Land Cover Type (MCD12Q1, Terra and Aqua combined),
  IGBP classification of 17 classes, yearly from 2001, 500 m. Friedl, M. &
  Sulla-Menashe, D., NASA EOSDIS Land Processes DAAC,
  https://doi.org/10.5067/MODIS/MCD12Q1.061.
- **NDVI:** MODIS Terra Vegetation Indices monthly (MOD13A3, 1 km), the
  Normalized Difference Vegetation Index from 2000 to the latest month,
  https://doi.org/10.5067/MODIS/MOD13A3.061.

Both are served by NASA's Global Imagery Browse Services (GIBS) as Web
Mercator PNG tiles, NASA open data (no restrictions; credit NASA). Tiles are
cached under ``~/.cache/ortho_tiles/gibs/``; a month or year never changes
once published.

Land cover tiles are coloured with the standard IGBP palette, one exact
colour per class, so classes can be picked out again; water and
unclassified land are left clear. NDVI tiles are drawn as served.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from collections.abc import Sequence

import numpy as np
from PIL import Image

import cartopy.io.img_tiles as cimgt
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

logger = logging.getLogger(__name__)

GIBS_URL = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{time}/"
            "{matrix}/{z}/{y}/{x}.png")
LAND_COVER_LAYER = ("MODIS_Combined_L3_IGBP_Land_Cover_Type_Annual", "GoogleMapsCompatible_Level8")
NDVI_LAYER = ("MODIS_Terra_L3_NDVI_Monthly", "GoogleMapsCompatible_Level7")
FIRST_LAND_COVER_YEAR, LATEST_LAND_COVER_YEAR = 2001, 2024
FIRST_NDVI_MONTH = (2000, 3)
# Monthly NDVI is published about two months after the month ends
NDVI_LAG_MONTHS = 2
# Tile zoom of both layers: ~5 km pixels at the equator (MODIS land cover is 500 m)
VEGETATION_ZOOM = 5

# IGBP class -> (name, (R, G, B)) in GIBS's palette; water (17) and unclassified are left clear
LAND_COVER_CLASSES: dict[int, tuple[str, tuple[int, int, int]]] = {
    1: ("Evergreen needleleaf forests", (33, 138, 33)),
    2: ("Evergreen broadleaf forests", (49, 204, 49)),
    3: ("Deciduous needleleaf forests", (152, 204, 49)),
    4: ("Deciduous broadleaf forests", (150, 250, 150)),
    5: ("Mixed forests", (141, 186, 141)),
    6: ("Closed shrublands", (186, 141, 141)),
    7: ("Open shrublands", (245, 222, 179)),
    8: ("Woody savannas", (218, 235, 157)),
    9: ("Savannas", (255, 213, 0)),
    10: ("Grasslands", (240, 185, 103)),
    11: ("Permanent wetlands", (71, 131, 181)),
    12: ("Croplands", (250, 239, 115)),
    13: ("Urban and built-up lands", (255, 0, 0)),
    14: ("Cropland/natural vegetation mosaics", (153, 147, 86)),
    15: ("Permanent snow and ice", (255, 255, 255)),
    16: ("Barren", (191, 191, 189)),
}
# Short names for groups of classes
LAND_COVER_GROUPS: dict[str, tuple[int, ...]] = {
    "forest": (1, 2, 3, 4, 5),
    "shrubland": (6, 7),
    "savanna": (8, 9),
    "grassland": (10,),
    "wetland": (11,),
    "cropland": (12, 14),
    "urban": (13,),
    "ice": (15,),
    "barren": (16,),
}

# NDVI key: lower bound and GIBS colour at the middle of each 0.1 band
NDVI_KEY: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (225, 217, 213)), (0.1, (200, 181, 166)), (0.2, (169, 137, 111)), (0.3, (164, 198, 61)),
    (0.4, (120, 173, 1)), (0.5, (78, 148, 1)), (0.6, (46, 128, 0)), (0.7, (18, 110, 0)),
    (0.8, (0, 96, 0)), (0.9, (0, 54, 0)),
]

MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")


def land_cover_attribution(year: int) -> str:
    return f"Land cover: MODIS MCD12Q1 IGBP {year} (NASA LP DAAC), via NASA GIBS"


def ndvi_attribution(year: int, month: int) -> str:
    return f"NDVI: MODIS Terra monthly, {MONTHS[month - 1].title()} {year} (NASA), via NASA GIBS"


# ---------------------------------------------------------------------------
# Names and dates
# ---------------------------------------------------------------------------


def resolve_land_cover_classes(specs: Sequence[str]) -> tuple[int, ...]:
    """IGBP classes named by *specs*, in class order.

    Each spec is a group (``"forest"``, ``"cropland"``, see
    :data:`LAND_COVER_GROUPS`, singular or plural) or the start of class
    names (``"evergreen"`` is both evergreen forests); matching ignores case.
    Raises ``ValueError`` for a spec that matches nothing.
    """
    selected: set[int] = set()
    for spec in specs:
        key = spec.strip().lower()
        group = LAND_COVER_GROUPS.get(key) or LAND_COVER_GROUPS.get(key.removesuffix("s"))
        matches = list(group) if group else [
            code for code, (name, _) in LAND_COVER_CLASSES.items() if len(key) >= 3 and name.lower().startswith(key)
        ]
        if not matches:
            raise ValueError(
                f"Unknown land cover class {spec!r}. Use a group ({', '.join(LAND_COVER_GROUPS)}) "
                f"or the start of a class name: {', '.join(n for n, _ in LAND_COVER_CLASSES.values())}."
            )
        selected.update(matches)
    return tuple(sorted(selected))


def latest_ndvi_month(today: datetime.date | None = None) -> tuple[int, int]:
    """The newest month whose NDVI should be published by *today*."""
    today = today or datetime.date.today()
    index = today.year * 12 + today.month - 1 - NDVI_LAG_MONTHS
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def resolve_ndvi_month(spec: str, today: datetime.date | None = None) -> tuple[int, int]:
    """``(year, month)`` for *spec*: ``"2026-07"``, or a month (``"july"``, ``"jul"``, ``"7"``).

    A month alone means its latest published one. Raises ``ValueError`` for
    anything else, or a month outside the record.
    """
    key = spec.strip().lower()
    latest = latest_ndvi_month(today)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", key)
    if match:
        year, month = int(match[1]), int(match[2])
    else:
        if key.isdigit():
            month = int(key)
        else:
            names = [i for i, name in enumerate(MONTHS, 1) if len(key) >= 3 and name.startswith(key)]
            month = names[0] if len(names) == 1 else 0
        year = latest[0] if (latest[0], month) <= latest else latest[0] - 1
    if not 1 <= month <= 12:
        raise ValueError(f"Unknown NDVI month {spec!r}: use YYYY-MM, a month name or 1-12.")
    if not FIRST_NDVI_MONTH <= (year, month) <= latest:
        raise ValueError(f"NDVI runs from {FIRST_NDVI_MONTH[0]}-{FIRST_NDVI_MONTH[1]:02d} to "
                         f"{latest[0]}-{latest[1]:02d}, not {year}-{month:02d}.")
    return year, month


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------


class GibsTiles(cimgt.GoogleWTS):
    """One time step of a GIBS layer as a Cartopy tile source, cached on disk.

    ``get_image`` raises on a failed download (``ortho.BufferedTileSource``
    makes the tile transparent); failures are never cached.
    """

    def __init__(self, layer: str, matrix: str, time: str, cache_dir: str | None = None,
                 timeout: float = 30, use_cache: bool = True) -> None:
        # The time goes into the URL and the cache path, so only a plain date is accepted
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", time):
            raise ValueError(f"GIBS time must be YYYY-MM-DD, got {time!r}")
        super().__init__(desired_tile_form="RGBA", user_agent="ortho/1.0")
        self.use_cache = use_cache
        self.layer, self.matrix, self.time = layer, matrix, time
        self.tile_cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles")
        self.timeout = timeout

    def _image_url(self, tile: tuple[int, int, int]) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        x, y, z = tile
        return GIBS_URL.format(layer=self.layer, time=self.time, matrix=self.matrix, z=z, y=y, x=x)

    def get_image(self, tile: tuple[int, int, int]):  # same (image, extent, origin) shape as Cartopy
        """The tile from the cache or GIBS; a clear tile if GIBS serves no layer data for it.

        Both layers are served as palette PNGs. GIBS sometimes answers with
        a true-colour satellite image instead (seen for NDVI where a month
        has no data, such as the polar night), and sometimes with a server
        error; either is retried twice. A tile still not in a palette is
        returned clear and not cached, so a later render tries again.
        """
        x, y, z = tile
        path = Path(self.tile_cache_dir) / "gibs" / self.layer / self.time / f"{z}_{x}_{y}.png"
        if self.use_cache and path.is_file():
            image = Image.open(io.BytesIO(path.read_bytes()))
        else:
            image = None
            for attempt in range(self.ATTEMPTS):
                try:
                    data = self._download(tile)
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:                       # GIBS has no tile here (e.g. its column x=31 at zoom 5)
                        break
                    if exc.code < 500 or attempt == self.ATTEMPTS - 1:
                        raise
                    continue
                image = Image.open(io.BytesIO(data))
                if image.mode == "P" and not self.use_cache:
                    break
                if image.mode == "P":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    part = path.with_name(path.name + ".part")
                    part.write_bytes(data)
                    os.replace(part, path)
                    break
            if image is None or image.mode != "P":
                logger.debug("GIBS served no %s data for tile %s", self.layer, tile)
                return np.zeros((256, 256, 4), np.uint8), self.tileextent(tile), "lower"
        return np.asarray(image.convert("RGBA")), self.tileextent(tile), "lower"

    ATTEMPTS = 3

    def _download(self, tile: tuple[int, int, int]) -> bytes:
        request = urllib.request.Request(self._image_url(tile), headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            return resp.read()


def land_cover_tiles(year: int, cache_dir: str | None = None, use_cache: bool = True) -> GibsTiles:
    layer, matrix = LAND_COVER_LAYER
    return GibsTiles(layer, matrix, f"{int(year):04d}-01-01", cache_dir, use_cache=use_cache)


def ndvi_tiles(year: int, month: int, cache_dir: str | None = None, use_cache: bool = True) -> GibsTiles:
    layer, matrix = NDVI_LAYER
    return GibsTiles(layer, matrix, f"{int(year):04d}-{int(month):02d}-01", cache_dir, use_cache=use_cache)


# ---------------------------------------------------------------------------
# Colouring the merged mosaics (``ortho.BufferedTileSource`` postprocess hooks)
# ---------------------------------------------------------------------------


def _pack(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.int32)
    return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]


def land_cover_codes(mosaic: np.ndarray) -> np.ndarray:
    """IGBP class of each pixel of a land cover mosaic; 0 for water, gaps and unknown colours."""
    keys = np.array([(r << 16) | (g << 8) | b for _, (r, g, b) in LAND_COVER_CLASSES.values()])
    codes = np.array(list(LAND_COVER_CLASSES), np.uint8)
    order = np.argsort(keys)
    keys, codes = keys[order], codes[order]
    packed = _pack(mosaic[..., :3])
    index = np.clip(np.searchsorted(keys, packed), 0, len(keys) - 1)
    found = (keys[index] == packed) & (mosaic[..., 3] > 0)
    return np.where(found, codes[index], 0).astype(np.uint8)


def land_cover_rgba(mosaic: np.ndarray, extent: object = None, alpha: float = 0.7,
                    classes: Sequence[int] | None = None) -> np.ndarray:
    """The land cover mosaic with only *classes* (default all) shown, at *alpha*."""
    codes = land_cover_codes(mosaic)
    palette = np.zeros((256, 4), np.uint8)
    for code, (_, rgb) in LAND_COVER_CLASSES.items():
        if not classes or code in classes:
            palette[code] = (*rgb, round(alpha * 255))
    return palette[codes]


def ndvi_rgba(mosaic: np.ndarray, extent: object = None, alpha: float = 0.7) -> np.ndarray:
    """The NDVI mosaic as served, at *alpha*; no data stays clear."""
    out = mosaic.copy()
    out[..., 3] = np.where(mosaic[..., 3] > 0, round(alpha * 255), 0)
    return out


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def _key(ax: GeoAxes, handles: list, labels: list[str], title: str, ncol: int,
         y: float, x: float) -> Legend:
    legend = Legend(
        ax, handles, labels,
        loc="upper center", bbox_to_anchor=(x, y), ncol=ncol,
        fontsize=10, title=title, title_fontsize=10,
        frameon=True, fancybox=True, framealpha=0.85, edgecolor="#444444",
        handlelength=1.6, columnspacing=1.4, borderpad=0.7,
    )
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend


def _patch(rgb: tuple[int, int, int]) -> mpatches.Patch:
    r, g, b = rgb
    return mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#333333", linewidth=0.6)


def add_land_cover_legend(ax: GeoAxes, year: int, classes: Sequence[int] | None = None,
                          y: float = -0.01, x: float = 0.5) -> Legend:
    """A key naming each land cover class shown."""
    shown = [code for code in LAND_COVER_CLASSES if not classes or code in classes]
    return _key(ax, [_patch(LAND_COVER_CLASSES[c][1]) for c in shown],
                [LAND_COVER_CLASSES[c][0] for c in shown],
                f"Land cover {year} (MODIS, IGBP classes)", min(len(shown), 4), y, x)


def add_ndvi_legend(ax: GeoAxes, year: int, month: int, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key of NDVI from bare (0) to dense vegetation (0.9+)."""
    labels = [f"{low:.1f}" for low, _ in NDVI_KEY]
    labels[-1] += "+"
    return _key(ax, [_patch(rgb) for _, rgb in NDVI_KEY], labels,
                f"Vegetation greenness (NDVI), {MONTHS[month - 1].title()} {year}: 0 bare, 1 dense",
                len(NDVI_KEY), y, x)
