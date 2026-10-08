"""Köppen-Geiger climate classification overlay for orthographic maps.

Data source
-----------
Beck, H. E., Zimmermann, N. E., McVicar, T. R., Vergopolan, N., Berg, A. &
Wood, E. F. (2018).  Present and future Köppen-Geiger climate classification
maps at 1-km resolution.  *Scientific Data* 5, 180214.
https://doi.org/10.1038/sdata.2018.214

This module uses the V1 "present" (1980–2016) map at 0.083° (~10 km).
Colour table follows the official ``legend.txt`` shipped with the dataset.
Licensed under CC BY 4.0.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.error
import urllib.request
import zipfile

import numpy as np
from PIL import Image

import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V1 data archive on Figshare (Beck et al. 2018)
# https://doi.org/10.6084/m9.figshare.6396959 — file "Beck_KG_V1.zip"
# ---------------------------------------------------------------------------

FIGSHARE_ARTICLE_URL = "https://doi.org/10.6084/m9.figshare.6396959"
KOPPEN_ZIP_URL = "https://ndownloader.figshare.com/files/12407516"
KOPPEN_ZIP_NAME = "Beck_KG_V1.zip"
KOPPEN_ZIP_MD5 = "69594689d3cdd8323a0f74ce658125a1"  # published by Figshare

RESOLUTIONS = ("0p5", "0p083", "0p0083")  # 0.5°, 0.083° (~10 km), 0.0083° (~1 km)
PERIODS = ("present", "future")

# Credit line required by the dataset's CC BY 4.0 licence
KOPPEN_ATTRIBUTION = "Climate data: Beck et al. (2018), CC BY 4.0"

DEFAULT_RESOLUTION = "0p083"  # ~10 km for V1
DEFAULT_PERIOD = "present"

# ---------------------------------------------------------------------------
# Official 30-class colour table
# Grid-code → (symbol, description, (R, G, B))
# ---------------------------------------------------------------------------

KOPPEN_CLASSES: dict[int, tuple[str, str, tuple[int, int, int]]] = {
    1:  ("Af",    "Tropical rainforest",              (  0,   0, 255)),
    2:  ("Am",    "Tropical monsoon",                 (  0, 120, 255)),
    3:  ("As/Aw", "Tropical savanna",                 ( 70, 170, 250)),
    4:  ("BWh",   "Hot desert",                       (255,   0,   0)),
    5:  ("BWk",   "Cold desert",                      (255, 150, 150)),
    6:  ("BSh",   "Hot semi-arid",                    (245, 165,   0)),
    7:  ("BSk",   "Cold semi-arid",                   (255, 220, 100)),
    8:  ("Csa",   "Mediterranean hot summer",         (255, 255,   0)),
    9:  ("Csb",   "Mediterranean warm summer",        (200, 200,   0)),
    10: ("Csc",   "Mediterranean cold summer",        (150, 150,   0)),
    11: ("Cwa",   "Humid subtropical dry winter",     (150, 255, 150)),
    12: ("Cwb",   "Subtropical highland dry winter",  (100, 200, 100)),
    13: ("Cwc",   "Subpolar oceanic dry winter",      ( 50, 150,  50)),
    14: ("Cfa",   "Humid subtropical",                (200, 255,  80)),
    15: ("Cfb",   "Oceanic",                          (100, 255,  80)),
    16: ("Cfc",   "Subpolar oceanic",                 ( 50, 200,   0)),
    17: ("Dsa",   "Continental hot dry summer",       (255,   0, 255)),
    18: ("Dsb",   "Continental warm dry summer",      (200,   0, 200)),
    19: ("Dsc",   "Continental subarctic dry summer",  (150,  50, 150)),
    20: ("Dsd",   "Continental extreme dry summer",   (150, 100, 150)),
    21: ("Dwa",   "Continental hot dry winter",       (170, 175, 255)),
    22: ("Dwb",   "Continental warm dry winter",      ( 90, 120, 220)),
    23: ("Dwc",   "Continental subarctic dry winter",  ( 75,  80, 180)),
    24: ("Dwd",   "Continental extreme dry winter",   ( 50,   0, 135)),
    25: ("Dfa",   "Continental hot summer",           (  0, 255, 255)),
    26: ("Dfb",   "Continental warm summer",          ( 55, 200, 255)),
    27: ("Dfc",   "Continental subarctic",            (  0, 125, 125)),
    28: ("Dfd",   "Continental extreme cold",         (  0,  70,  95)),
    29: ("ET",    "Tundra",                           (178, 178, 178)),
    30: ("EF",    "Ice cap",                          (102, 102, 102)),
}

# Major group labels for the legend header row
_GROUPS: list[tuple[str, str, list[int]]] = [
    ("A", "Tropical",      [1, 2, 3]),
    ("B", "Arid",          [4, 5, 6, 7]),
    ("C", "Temperate",     [8, 9, 10, 11, 12, 13, 14, 15, 16]),
    ("D", "Continental",   [17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28]),
    ("E", "Polar",         [29, 30]),
]


# ---------------------------------------------------------------------------
# Data download & caching
# ---------------------------------------------------------------------------


class KoppenDataError(RuntimeError):
    """The Köppen-Geiger raster could not be found, downloaded or read."""


_MANUAL_HINT = (
    f"Download {KOPPEN_ZIP_NAME} from {FIGSHARE_ARTICLE_URL} and extract it into a "
    "'Beck_KG_V1' folder next to koppen.py."
)

# Errors worth retrying: rate limiting and server-side failures
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "koppen")


def _is_valid_tif(path: str) -> bool:
    """True if *path* exists and starts with a TIFF / BigTIFF header."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(4)
    except OSError:
        return False
    return header in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def _md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_with_progress(
    url: str,
    dest: str,
    label: str = "Downloading",
    attempts: int = 3,
    timeout: float = 60,
) -> None:
    """Download *url* to *dest* atomically, retrying only transient failures.

    The data is written to ``dest + ".part"`` and moved into place only once
    the transfer completes, so an interrupted run never leaves a truncated
    file at *dest*.
    """
    part = dest + ".part"
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.headers.get("x-amzn-waf-action"):
                    # Bot challenge: waiting will not help a non-browser client
                    raise KoppenDataError(
                        f"The download server answered with a bot challenge. {_MANUAL_HINT}"
                    )
                if resp.status != 200:
                    raise KoppenDataError(f"Unexpected HTTP {resp.status} from {url}")

                total = int(resp.headers.get("Content-Length") or 0) or None
                downloaded = 0
                shown = None
                with open(part, "wb") as fh:
                    while chunk := resp.read(1 << 16):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        # Redraw only when the displayed value changes
                        step = downloaded * 100 // total if total else downloaded >> 20
                        if step != shown:
                            shown = step
                            mb = downloaded / 1e6
                            text = f"{mb:.1f} / {total / 1e6:.1f} MB ({step}%)" if total else f"{mb:.1f} MB"
                            print(f"\r  {label}: {text}", end="", flush=True)
                print()

            if total and downloaded != total:
                raise urllib.error.URLError(f"incomplete download ({downloaded} of {total} bytes)")
            os.replace(part, dest)
            return

        except urllib.error.HTTPError as exc:
            retryable = exc.code in _RETRYABLE_HTTP
            error: Exception = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            retryable = True
            error = exc
        finally:
            if os.path.exists(part):
                os.remove(part)

        if not retryable or attempt == attempts:
            raise KoppenDataError(f"Download of {url} failed: {error}. {_MANUAL_HINT}") from error
        delay = 2 ** attempt
        logger.warning("  Download failed (%s); retrying in %d s (%d/%d) …", error, delay, attempt, attempts)
        time.sleep(delay)


def _ensure_zip(cache_dir: str) -> str:
    """Return the path to a verified copy of the V1 archive, downloading it if needed."""
    zip_path = os.path.join(cache_dir, KOPPEN_ZIP_NAME)
    if os.path.isfile(zip_path) and _md5(zip_path) == KOPPEN_ZIP_MD5:
        return zip_path

    logger.info("Downloading Köppen-Geiger data (%s, ~71 MB) from Figshare …", KOPPEN_ZIP_NAME)
    _download_with_progress(KOPPEN_ZIP_URL, zip_path, label="Köppen-Geiger data")
    if _md5(zip_path) != KOPPEN_ZIP_MD5:
        os.remove(zip_path)
        raise KoppenDataError(f"{KOPPEN_ZIP_NAME} failed its MD5 check. {_MANUAL_HINT}")
    return zip_path


def _extract_member(zip_path: str, member: str, dest: str) -> None:
    """Extract *member* of *zip_path* to *dest* atomically."""
    part = dest + ".part"
    try:
        with zipfile.ZipFile(zip_path) as zf, zf.open(member) as src, open(part, "wb") as out:
            while chunk := src.read(1 << 20):
                out.write(chunk)
        os.replace(part, dest)
    except KeyError:
        raise KoppenDataError(f"{member} is missing from {zip_path}") from None
    finally:
        if os.path.exists(part):
            os.remove(part)


def ensure_koppen_data(
    cache_dir: str | None = None,
    resolution: str = DEFAULT_RESOLUTION,
    period: str = DEFAULT_PERIOD,
) -> str:
    """Return the path to the Köppen-Geiger GeoTIFF for *period* / *resolution*.

    Looks in, in order: the manual ``Beck_KG_V1/`` folder next to this file,
    the cache directory, and finally downloads and verifies the V1 archive
    into the cache and extracts the requested raster. Raises
    :class:`KoppenDataError` if the data cannot be obtained.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}, got {resolution!r}")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got {period!r}")
    tif_name = f"Beck_KG_V1_{period}_{resolution}.tif"

    # 1. Manual download folder in the workspace
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Beck_KG_V1", tif_name)
    if _is_valid_tif(local_path):
        logger.info("Using local Köppen-Geiger data: %s", local_path)
        return local_path

    # 2. Cache directory
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    cached_path = os.path.join(cache_dir, tif_name)
    if _is_valid_tif(cached_path):
        logger.info("Using cached Köppen-Geiger data: %s", cached_path)
        return cached_path
    if os.path.exists(cached_path):
        logger.warning("Discarding invalid cached file %s", cached_path)
        os.remove(cached_path)

    # 3. Download the archive and extract the requested raster
    zip_path = _ensure_zip(cache_dir)
    _extract_member(zip_path, tif_name, cached_path)
    if not _is_valid_tif(cached_path):
        os.remove(cached_path)
        raise KoppenDataError(f"{tif_name} extracted from {zip_path} is not a valid GeoTIFF")
    logger.info("Köppen-Geiger data ready: %s", cached_path)
    return cached_path


# ---------------------------------------------------------------------------
# Colormap
# ---------------------------------------------------------------------------


def build_koppen_colormap() -> tuple[mcolors.ListedColormap, mcolors.BoundaryNorm]:
    """Build a ``ListedColormap`` + ``BoundaryNorm`` for the 30 KG classes.

    Grid-code 0 (ocean / no-data) maps to fully transparent.

    Returns
    -------
    cmap : ListedColormap
    norm : BoundaryNorm
    """
    # Index 0 = transparent (ocean / nodata)
    rgba_list: list[tuple[float, float, float, float]] = [(0, 0, 0, 0)]

    for code in range(1, 31):
        r, g, b = KOPPEN_CLASSES[code][2]
        rgba_list.append((r / 255, g / 255, b / 255, 1.0))

    cmap = mcolors.ListedColormap(rgba_list, name="koppen_geiger", N=31)
    boundaries = list(range(32))          # [0, 1, 2, …, 31]
    norm = mcolors.BoundaryNorm(boundaries, cmap.N)
    return cmap, norm


# ---------------------------------------------------------------------------
# Reading the GeoTIFF with Pillow
# ---------------------------------------------------------------------------


def _read_koppen_tif(tif_path: str) -> np.ndarray:
    """Read the first band of a KG GeoTIFF as a uint8 numpy array.

    The Beck et al. GeoTIFFs are global WGS-84 grids covering
    (−180, 180, −90, 90), stored as unsigned 8-bit integers.
    """
    img = Image.open(tif_path)
    data = np.array(img, dtype=np.uint8)
    logger.debug("Loaded KG raster: shape=%s, dtype=%s", data.shape, data.dtype)
    return data


# ---------------------------------------------------------------------------
# Overlay rendering
# ---------------------------------------------------------------------------


def add_koppen_overlay(
    ax: GeoAxes,
    alpha: float = 0.45,
    cache_dir: str | None = None,
    resolution: str = DEFAULT_RESOLUTION,
    period: str = DEFAULT_PERIOD,
    regrid_shape: int = 750,
) -> None:
    """Render the Köppen-Geiger overlay on *ax*.

    Parameters
    ----------
    ax : GeoAxes
        The target Cartopy axes (any projection).
    alpha : float
        Overlay opacity (0 = invisible, 1 = opaque).
    cache_dir : str or None
        Cache directory (passed to :func:`ensure_koppen_data`).
    resolution : str
        Grid resolution tag.
    period : str
        Historical / scenario tag.
    regrid_shape : int
        Resolution (pixels along the longer side) the raster is warped to in
        the target projection. Cartopy's default of 750 looks blocky on large
        renders; match the tiles' regrid shape for consistent sharpness.
    """
    tif_path = ensure_koppen_data(cache_dir, resolution, period)
    data = _read_koppen_tif(tif_path)

    cmap, norm = build_koppen_colormap()

    # The Beck GeoTIFFs cover the full globe in WGS-84.
    extent = (-180, 180, -90, 90)

    ax.imshow(
        data,
        origin="upper",
        extent=extent,
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        norm=norm,
        alpha=alpha,
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=5,
    )

    logger.info("Köppen-Geiger overlay rendered (alpha=%.2f).", alpha)


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------


def add_koppen_legend(ax: GeoAxes, x: float = 0.5) -> None:
    """Add a compact Köppen-Geiger legend strip below the globe.

    The legend lists all 30 sub-classes in climate-group order (A–E) as a
    flat 15-column grid, each with its canonical colour swatch and
    abbreviation.  *x* is the strip's horizontal centre in axes coordinates.
    The dataset credit (:data:`KOPPEN_ATTRIBUTION`) is drawn by the caller.
    """
    handles: list[mpatches.Patch] = []
    labels: list[str] = []

    for _group_letter, _group_name, codes in _GROUPS:
        for code in codes:
            sym, _desc, (r, g, b) = KOPPEN_CLASSES[code]
            colour = (r / 255, g / 255, b / 255)
            handles.append(mpatches.Patch(facecolor=colour, edgecolor="white", linewidth=0.4))
            labels.append(sym)

    legend = ax.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(x, -0.06),
        ncol=15,
        fontsize=6.5,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=1.2,
        handleheight=1.0,
        columnspacing=0.6,
        handletextpad=0.3,
        borderpad=0.5,
        title="Köppen-Geiger Climate Classification",
        title_fontsize=7.5,
    )
    legend.get_title().set_fontweight("bold")
