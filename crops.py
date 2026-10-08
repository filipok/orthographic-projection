"""Crop-area overlay from CROPGRIDS: where each crop is grown, circa 2020.

Data source
-----------
Tang, F. H. M., Nguyen, T. H., Conchedda, G., Casse, L., Tubiello, F. N. &
Maggi, F. (2024). CROPGRIDS: a global geo-referenced dataset of 173 crops.
*Scientific Data* 11, 413. https://doi.org/10.1038/s41597-024-03247-7
Data: https://doi.org/10.6084/m9.figshare.22491997 (v1.08), CC BY 4.0.

Each crop is a 0.05° grid of physical crop area in hectares. The overlay
shows the share of each cell's area planted with the crop. With several
crops, each cell takes the colour of whichever of them covers the most of it.

All 173 crops ship in one 807 MB zip, so a crop's file is read straight out
of the remote archive with HTTP range requests (a few MB per crop) and cached.
"""

from __future__ import annotations

import difflib
import io
import logging
import os
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import cartopy.crs as ccrs
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

logger = logging.getLogger(__name__)

CROPGRIDS_ZIP_URL = "https://ndownloader.figshare.com/files/44950942"
CROPGRIDS_VERSION = "v1.08"
_MEMBER = "CROPGRIDS{v}_NC_maps/CROPGRIDS{v}_{name}.nc"

CROP_ATTRIBUTION = "Crop areas: CROPGRIDS v1.08, Tang et al. (2024), CC BY 4.0"

# The 173 crops of CROPGRIDS v1.08, by their file names ("nes" = not
# elsewhere specified, "for" = grown for fodder, "etc" = and similar crops)
CROP_NAMES = (
    "abaca", "agave", "alfalfa", "almond", "aniseetc", "apple", "apricot", "areca",
    "artichoke", "asparagus", "avocado", "bambara", "banana", "barley", "bean", "beetfor",
    "berrynes", "blueberry", "brazil", "broadbean", "buckwheat", "cabbage", "cabbagefor",
    "canaryseed", "carob", "carrot", "carrotfor", "cashew", "cashewapple", "cassava",
    "castor", "cauliflower", "cerealnes", "cherry", "chestnut", "chickpea", "chicory",
    "chilleetc", "cinnamon", "citrusnes", "clove", "clover", "cocoa", "coconut", "coffee",
    "cotton", "cowpea", "cranberry", "cucumberetc", "currant", "date", "eggplant",
    "fibrenes", "fig", "flax", "fonio", "fornes", "fruitnes", "garlic", "ginger",
    "gooseberry", "grape", "grapefruitetc", "grassnes", "greenbean", "greenbroadbean",
    "greencorn", "greenonion", "greenpea", "groundnut", "hazelnut", "hemp", "hempseed",
    "hop", "jute", "jutelikefiber", "kapokfiber", "kapokseed", "karite", "kiwi", "kolanut",
    "legumenes", "lemonlime", "lentil", "lettuce", "linseed", "lupin", "maize", "maizefor",
    "mango", "mate", "melonetc", "melonseed", "millet", "mixedgrain", "mixedgrass",
    "mushroom", "mustard", "nutmeg", "nutnes", "oats", "oilpalm", "oilseedfor",
    "oilseednes", "okra", "olive", "onion", "orange", "papaya", "pea", "peachetc", "pear",
    "pepper", "peppermint", "persimmon", "pigeonpea", "pimento", "pineapple", "pistachio",
    "plantain", "plum", "popcorn", "poppy", "potato", "pulsenes", "pumpkinetc", "pyrethrum",
    "quince", "quinoa", "ramie", "rapeseed", "rasberry", "rice", "rootnes", "rubber", "rye",
    "ryefor", "safflower", "sesame", "sisal", "sorghum", "sorghumfor", "sourcherry",
    "soybean", "spicenes", "spinach", "stonefruitnes", "strawberry", "stringbean",
    "sugarbeet", "sugarcane", "sugarnes", "sunflower", "swedefor", "sweetpotato", "tangetc",
    "taro", "tea", "tobacco", "tomato", "triticale", "tropicalnes", "tung", "turnipfor",
    "vanilla", "vegetablenes", "vegfor", "vetch", "walnut", "watermelon", "wheat", "yam",
    "yautia",
)

# Colours for common crops; others take the next unused colour of _FALLBACK_COLORS
CROP_COLORS = {
    "wheat": "#f2b705",
    "rice": "#18b5a4",
    "maize": "#ff6b35",
    "soybean": "#7cb518",
    "barley": "#c08552",
    "sorghum": "#b23a48",
    "millet": "#f4a261",
    "potato": "#9b5de5",
    "cassava": "#f15bb5",
    "sugarcane": "#e056fd",
    "coffee": "#8b5a2b",
    "cocoa": "#6f1d1b",
    "tea": "#2d6a4f",
    "cotton": "#4cc9f0",
    "oilpalm": "#d00000",
    "rapeseed": "#ffe14d",
    "grape": "#7b2cbf",
    "olive": "#6b8f71",
}
_FALLBACK_COLORS = ("#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46f0f0",
                    "#f032e6", "#bcf60c", "#008080", "#9a6324")

# Opacity ramp: cells below MIN_SHARE stay clear; colour deepens up to SATURATION.
# Half of all wheat cells are under 2% wheat and only 1% exceed 54%, so the
# ramp saturates early and is lifted at the low end with GAMMA.
MIN_SHARE = 0.005
SATURATION = 0.4
GAMMA = 0.6
ALPHA_RANGE = (0.25, 0.9)

_EARTH_RADIUS_KM = 6371.0088
_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"


class CropDataError(RuntimeError):
    """A crop's CROPGRIDS data could not be found, downloaded or read."""


# ---------------------------------------------------------------------------
# Crop names and colours
# ---------------------------------------------------------------------------


def parse_crop_spec(spec: str) -> tuple[str, str | None]:
    """Split ``"wheat"`` or ``"wheat:#f2b705"`` into a known crop name and an optional colour.

    Raises ``ValueError`` for an unknown crop (suggesting close names) or an
    invalid colour.
    """
    name, _, colour = spec.partition(":")
    name = name.strip().lower()
    if name not in CROP_NAMES:
        close = difflib.get_close_matches(name, CROP_NAMES, n=3)
        hint = f" Did you mean {', '.join(close)}?" if close else " Use --list-crops to see them all."
        raise ValueError(f"Unknown crop {name!r}.{hint}")
    colour = colour.strip() or None
    if colour is not None and not mcolors.is_color_like(colour):
        raise ValueError(f"Invalid colour {colour!r} for crop {name!r}")
    return name, colour


def resolve_crops(specs: Sequence[str]) -> list[tuple[str, str]]:
    """Parse *specs* into unique ``(name, colour)`` pairs, filling in default colours."""
    parsed: list[tuple[str, str | None]] = []
    for spec in specs:
        name, colour = parse_crop_spec(spec)
        if name in (n for n, _ in parsed):
            raise ValueError(f"Crop {name!r} is listed twice")
        parsed.append((name, colour))

    used = {c for _, c in parsed if c} | {CROP_COLORS[n] for n, c in parsed if not c and n in CROP_COLORS}
    spare = (c for c in _FALLBACK_COLORS if c not in used)
    resolved = []
    for name, colour in parsed:
        resolved.append((name, colour or CROP_COLORS.get(name) or next(spare, "#888888")))
    return resolved


def crop_label(name: str) -> str:
    """A crop's display name, e.g. ``"Sugarcane"``."""
    return name.capitalize()


# ---------------------------------------------------------------------------
# Reading one member of the remote zip
# ---------------------------------------------------------------------------


def _remote_size(url: str, timeout: float = 60) -> int:
    """Total size of *url*, from a one-byte range request.

    The download link redirects to a storage URL that is valid for seconds
    only, so every request goes through *url* again.
    """
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_range = resp.headers.get("Content-Range", "")
        if resp.status != 206 or "/" not in content_range:
            raise CropDataError(f"{url} does not support partial downloads")
    return int(content_range.rsplit("/", 1)[1])


def _fetch_range(url: str, start: int, end: int, timeout: float = 120) -> bytes:
    """Bytes *start* to *end* (inclusive) of *url*."""
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 206:
            # A full 200 response would be the whole 807 MB archive
            raise CropDataError(f"{url} ignored the range request (HTTP {resp.status})")
        return resp.read()


class _HTTPRangeFile(io.RawIOBase):
    """A read-only, seekable file whose reads are HTTP range requests."""

    def __init__(self, url: str, size: int) -> None:
        self.url, self.size, self._pos = url, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = base + offset
        return self._pos

    def readinto(self, buffer) -> int:
        n = min(len(buffer), self.size - self._pos)
        if n <= 0:
            return 0
        data = _fetch_range(self.url, self._pos, self._pos + n - 1)
        buffer[:len(data)] = data
        self._pos += len(data)
        return len(data)


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "cropgrids")


def _is_hdf5(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == _HDF5_SIGNATURE
    except OSError:
        return False


def ensure_crop_file(name: str, cache_dir: str | None = None) -> str:
    """Return the path to *name*'s CROPGRIDS NetCDF file, fetching it if needed.

    Raises :class:`CropDataError` if it cannot be obtained.
    """
    if name not in CROP_NAMES:
        raise ValueError(f"Unknown crop {name!r}")
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    member = _MEMBER.format(v=CROPGRIDS_VERSION, name=name)
    path = os.path.join(cache_dir, os.path.basename(member))
    if _is_hdf5(path):
        logger.info("Using cached CROPGRIDS data for %s: %s", name, path)
        return path

    logger.info("Fetching CROPGRIDS data for %s from the figshare archive …", name)
    part = path + ".part"
    try:
        remote = io.BufferedReader(
            _HTTPRangeFile(CROPGRIDS_ZIP_URL, _remote_size(CROPGRIDS_ZIP_URL)),
            buffer_size=1 << 20,
        )
        with zipfile.ZipFile(remote) as zf, zf.open(member) as src, open(part, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
        os.replace(part, path)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise CropDataError(f"Could not download the {name} crop data: {exc}") from exc
    except KeyError:
        raise CropDataError(f"{member} is missing from the CROPGRIDS archive") from None
    except (zipfile.BadZipFile, EOFError) as exc:
        raise CropDataError(f"The CROPGRIDS archive could not be read: {exc}") from exc
    finally:
        if os.path.exists(part):
            os.remove(part)
    if not _is_hdf5(path):
        os.remove(path)
        raise CropDataError(f"The {name} file from the CROPGRIDS archive is not NetCDF4")
    return path


# ---------------------------------------------------------------------------
# Shares and the overlay image
# ---------------------------------------------------------------------------


def read_crop_share(path: str) -> np.ndarray:
    """Share (0-1) of each 0.05° cell under the crop, rows from south to north.

    Ocean cells (-1) and cells without the crop are 0.
    """
    import h5py  # only needed for crops; keeps `import ortho` light

    try:
        with h5py.File(path, "r") as f:
            area_ha = f["croparea"][:]
            lat = f["lat"][:]
    except (OSError, KeyError) as exc:
        raise CropDataError(f"{path} could not be read: {exc}") from exc
    if lat[0] > lat[-1]:
        area_ha, lat = area_ha[::-1], lat[::-1]
    step = abs(float(lat[1] - lat[0]))
    cell_ha = (np.radians(step) * _EARTH_RADIUS_KM) ** 2 * np.cos(np.radians(lat)) * 100
    share = np.clip(area_ha, 0, None) / cell_ha[:, None].astype(np.float32)
    return np.clip(share, 0, 1).astype(np.float32)


def share_to_alpha(share: np.ndarray) -> np.ndarray:
    """Opacity for each share: clear below MIN_SHARE, deepening up to SATURATION."""
    ramp = np.clip(share / SATURATION, 0, 1) ** GAMMA
    alpha = ALPHA_RANGE[0] + (ALPHA_RANGE[1] - ALPHA_RANGE[0]) * ramp
    return np.where(share >= MIN_SHARE, alpha, 0).astype(np.float32)


@dataclass(frozen=True)
class CropLayer:
    """An RGBA overlay of the chosen crops plus what its key needs."""

    rgba: np.ndarray                    # (rows south→north, 7200, 4) uint8, global
    crops: tuple[tuple[str, str], ...]  # (name, colour) in the order given


def build_crop_layer(crops: Sequence[tuple[str, str]], shares: Sequence[np.ndarray]) -> CropLayer:
    """Colour each cell by the crop with the largest share, opacity by that share."""
    if not crops or len(crops) != len(shares):
        raise ValueError("need one share grid per crop")
    best = np.zeros_like(shares[0])
    which = np.zeros(shares[0].shape, dtype=np.uint8)
    for index, share in enumerate(shares):
        better = share > best
        best = np.where(better, share, best)
        which[better] = index

    palette = np.array([mcolors.to_rgb(c) for _, c in crops]) * 255
    rgba = np.empty(best.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = palette[which].astype(np.uint8)
    rgba[..., 3] = np.round(share_to_alpha(best) * 255).astype(np.uint8)
    return CropLayer(rgba=rgba, crops=tuple(crops))


def load_crop_layer(specs: Sequence[str], cache_dir: str | None = None) -> CropLayer:
    """Fetch and combine the crops named in *specs* (``"wheat"`` or ``"wheat:#hex"``).

    Raises ``ValueError`` for bad specs and :class:`CropDataError` if data
    cannot be obtained.
    """
    crops = resolve_crops(specs)
    shares = [read_crop_share(ensure_crop_file(name, cache_dir)) for name, _ in crops]
    return build_crop_layer(crops, shares)


def draw_crops(ax: GeoAxes, layer: CropLayer, regrid_shape: int = 750, zorder: float = 5.5) -> None:
    """Draw *layer* on *ax*, above the Köppen-Geiger overlay and below polar ice."""
    ax.imshow(
        layer.rgba,
        origin="lower",
        extent=(-180, 180, -90, 90),
        transform=ccrs.PlateCarree(),
        interpolation="nearest",
        regrid_shape=regrid_shape,
        zorder=zorder,
    )


def add_crop_legend(ax: GeoAxes, layer: CropLayer, y: float = -0.01, x: float = 0.5) -> Legend:
    """Add a key below the globe naming each crop next to its colour.

    *y* is the key's top edge and *x* its horizontal centre, in axes
    coordinates. Added as a separate artist like the route key.
    """
    single = len(layer.crops) == 1
    title = ("Share of land under the crop, c. 2020 (deeper = larger)" if single else
             "Crop with the largest share of land, c. 2020 (deeper = larger share)")
    handles = [mpatches.Patch(facecolor=colour, edgecolor="#333333", linewidth=0.6)
               for _, colour in layer.crops]
    legend = Legend(
        ax,
        handles,
        [crop_label(name) for name, _ in layer.crops],
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=min(len(handles), 5),
        fontsize=11,
        title=title,
        title_fontsize=10,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=1.6,
        columnspacing=1.6,
        borderpad=0.7,
    )
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
