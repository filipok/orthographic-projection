"""Köppen-Geiger climate classification overlay for orthographic maps.

Data source
-----------
Beck, H. E. et al. (2023).  High-resolution (1 km) Köppen-Geiger maps for
1901–2099 based on constrained CMIP6 projections.  *Scientific Data* 10, 724.
https://doi.org/10.1038/s41597-023-02549-6

Colour table follows the official ``legend.txt`` shipped with the dataset.
Licensed under CC BY 4.0.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
import urllib.request
import zipfile
from typing import Any

import numpy as np
from PIL import Image

import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Figshare direct-download URL for the V3 archive (Beck et al. 2023)
# ---------------------------------------------------------------------------

FIGSHARE_URL = "https://figshare.com/ndownloader/files/10808306"  # Beck et al. (2018) V1 0.1-deg Present

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


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "koppen")


def _find_tif_in_zip(zf: zipfile.ZipFile, resolution: str, period: str) -> str | None:
    """Return the first ZIP member matching *resolution* and *period*."""
    candidates: list[str] = []
    for name in zf.namelist():
        low = name.lower()
        if not low.endswith(".tif"):
            continue
        if resolution in low and period in low:
            candidates.append(name)
    # Prefer the shortest match (avoids confidence-layer variants)
    if candidates:
        candidates.sort(key=len)
        return candidates[0]
    return None


def _download_with_progress(url: str, dest: str, label: str = "Downloading") -> None:
    """Download *url* to *dest* with a console progress bar."""
    max_retries = 30
    retry_delay = 10  # seconds
    
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
        try:
            with urllib.request.urlopen(req) as resp:
                if resp.status == 202:
                    if attempt % 3 == 0:
                        logger.info("  Server returned 202 (Accepted). Waiting for file generation... (Attempt %d/%d)", 
                                    attempt + 1, max_retries)
                    time.sleep(retry_delay)
                    continue
                
                total = resp.headers.get("Content-Length")
                total = int(total) if total else None
                chunk_size = 1 << 16  # 64 KiB
                downloaded = 0

                with open(dest, "wb") as fh:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)

                        if total:
                            pct = downloaded / total * 100
                            mb = downloaded / 1e6
                            total_mb = total / 1e6
                            print(
                                f"\r  {label}: {mb:.1f} / {total_mb:.1f} MB ({pct:.0f}%)",
                                end="", flush=True,
                            )
                        else:
                            mb = downloaded / 1e6
                            print(f"\r  {label}: {mb:.1f} MB", end="", flush=True)
                
                print()  # newline after progress
                
                # Verify that we actually got some data
                if os.path.exists(dest) and os.path.getsize(dest) > 100_000:
                    return
                else:
                    logger.warning("  Downloaded file is empty or corrupted. Retrying...")
                    if os.path.exists(dest):
                        os.remove(dest)
                        
        except Exception as e:
            logger.error("  Download failed: %s", e)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise
    
    raise RuntimeError(f"Failed to download {url} after {max_retries} attempts.")


def ensure_koppen_data(
    cache_dir: str | None = None,
    resolution: str = "0p083",  # V1's ~10km resolution string
    period: str = "present",
) -> str:
    """Return path to the Köppen-Geiger GeoTIFF, checking local folder and cache.
    """
    # 1. First check the user's manual download folder in the workspace
    local_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Beck_KG_V1")
    if os.path.isdir(local_dir):
        for fname in os.listdir(local_dir):
            if fname.endswith(".tif") and period in fname and resolution in fname and "_conf_" not in fname:
                path = os.path.abspath(os.path.join(local_dir, fname))
                logger.info("Using local manual Köppen-Geiger data: %s", path)
                return path

    # 2. Fallback to cache directory
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)

    # Check if we already have a suitable tif in cache
    for fname in os.listdir(cache_dir):
        if fname.endswith(".tif") and resolution in fname and period in fname:
            path = os.path.join(cache_dir, fname)
            logger.info("Using cached Köppen-Geiger data: %s", path)
            return path

    # 3. Download if missing (optional fallback if user deletes folders)
    # Using the Beck 2018 individual file link if we have it
    # We'll use the 0.083 (V1) file ID 10808306
    download_path = os.path.join(cache_dir, f"Beck_KG_V1_{period}_{resolution}.tif")
    if not os.path.isfile(download_path) or os.path.getsize(download_path) == 0:
        logger.info("Downloading Köppen-Geiger data from Figshare (backup mirror) …")
        _download_with_progress(FIGSHARE_URL, download_path, label="Köppen-Geiger data")

    return download_path


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
        zorder=5,
    )

    logger.info("Köppen-Geiger overlay rendered (alpha=%.2f).", alpha)


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------


def add_koppen_legend(fig: plt.Figure, ax: GeoAxes) -> None:
    """Add a compact Köppen-Geiger legend strip below the globe.

    The legend is organised by major climate group (A–E) and shows each
    sub-class with its canonical colour swatch and abbreviation.
    """
    handles: list[mpatches.Patch] = []
    labels: list[str] = []

    for _group_letter, _group_name, codes in _GROUPS:
        for code in codes:
            sym, _desc, rgb = KOPPEN_CLASSES[code]
            colour = tuple(c / 255 for c in rgb)
            handles.append(mpatches.Patch(facecolor=colour, edgecolor="white", linewidth=0.4))
            labels.append(sym)

    legend = ax.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.06),
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

    # CC BY 4.0 attribution (required by the dataset license)
    fig.text(
        0.5, 0.01,
        "Climate data: Beck et al. (2023) · CC BY 4.0",
        ha="center", va="bottom",
        fontsize=6, color="#888888", style="italic",
    )
