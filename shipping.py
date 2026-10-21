"""Shipping lanes: how busy the sea is with ships, from the World Bank and IMF.

Data source
-----------
Global Shipping Traffic Density (World Bank, 2021), from the IMF's analysis
of hourly AIS positions of ships, January 2015 to February 2021.
https://datacatalog.worldbank.org/search/dataset/0037580 — CC BY 4.0.

Each 0.005° cell (~500 m) counts the hourly positions of ships in it, so
the busy lanes stand out from the empty ocean. There is a grid for all
ships and one for each kind (commercial, fishing, passenger, oil and gas,
leisure). A grid is a 9.8 GB TIFF inside a zip of 20-530 MB; the zip is
downloaded once into ``~/.cache/ortho_tiles/shipping/`` and read through
once, without unpacking it, into 0.01° tiles and a 0.04° world grid of the
traffic on a log scale. The zip is then deleted.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import struct
import urllib.request
import zipfile
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from biomes import _key
from climate import View, visible_box

logger = logging.getLogger(__name__)

CATALOG_URL = "https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/"
DATA_VERSION = 1


class ShipKind(NamedTuple):
    label: str
    file: str              # path under CATALOG_URL
    megabytes: int         # size of the zip


KINDS: dict[str, ShipKind] = {
    "commercial": ShipKind("Commercial ships", "DR0045405/shipdensity_commercial_.zip", 460),
    "all": ShipKind("All ships", "DR0045406/shipdensity_global.zip", 510),
    "fishing": ShipKind("Fishing boats", "DR0045403/ShipDensity_Fishing.zip", 97),
    "passenger": ShipKind("Passenger ships and ferries", "DR0045404/ShipDensity_Passenger.zip", 22),
    "oil-gas": ShipKind("Oil and gas vessels", "DR0045402/ShipDensity_OilGas.zip", 21),
    "leisure": ShipKind("Leisure craft", "DR0045401/ShipDensity_Leisure.zip", 28),
}
DEFAULT_KIND = "commercial"

FINE = 2                     # the TIFF's 0.005° cells per fine cell: 0.01°
COARSE = 8                   # per coarse cell: 0.04°
TILE = 1000                  # fine cells per tile side: 10°
STEPS_PER_DECADE = 32        # the stored traffic: round(32 · log10(1 + mean hourly positions per cell))
NODATA = 2147483647

SHIPPING_ATTRIBUTION = "Shipping: Global Shipping Traffic Density (World Bank, IMF, AIS 2015-2021), CC BY 4.0"


class ShippingDataError(RuntimeError):
    """The ship density could not be downloaded or read."""


class Traffic(NamedTuple):
    """Ship traffic over a lon/lat box, on the stored log scale (0 = none)."""
    kind: str
    values: np.ndarray                     # uint8, rows north to south
    extent: tuple[float, float, float, float]   # west, east, south, north (degrees)
    cell: float


def resolve_ship_kind(kind: str | None) -> str:
    """*kind* as a key of :data:`KINDS`; blank or ``"shipping"`` is commercial. Raises ``ValueError``."""
    text = (kind or "").strip().lower().replace("_", "-").replace(" ", "-")
    text = {"": DEFAULT_KIND, "shipping": DEFAULT_KIND, "global": "all", "oil-and-gas": "oil-gas", "oil": "oil-gas",
            "ferries": "passenger", "ferry": "passenger", "cargo": "commercial", "fishing-boats": "fishing"
            }.get(text, text)
    if text not in KINDS:
        raise ValueError(f"Unknown kind of ships {kind!r}: choose {', '.join(KINDS)}.")
    return text


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "shipping")


def _tls() -> ssl.SSLContext:
    from indicators import _tls as tls
    return tls()


def _download(url: str, dest: str, label: str) -> None:
    """Download *url* to *dest* through a ``.part`` file, logging progress."""
    part = dest + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120, context=_tls()) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done, shown = 0, -1
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and done * 10 // total != shown:
                    shown = done * 10 // total
                    logger.info("  %s: %d of %d MB", label, done >> 20, total >> 20)
        if total and done != total:
            raise ShippingDataError(f"incomplete download of {url} ({done} of {total} bytes)")
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


# ---------------------------------------------------------------------------
# Reading the BigTIFF inside the zip
# ---------------------------------------------------------------------------

_TIFF_TYPES = {3: "<u2", 4: "<u4", 16: "<u8", 12: "<f8"}


class TiffLayout(NamedTuple):
    width: int
    height: int
    tile_width: int
    tile_height: int
    offsets: np.ndarray
    scale: float               # degrees per cell
    west: float                # the outer edge of the first column
    north: float


def _tiff_layout(head: bytes, tail: bytes, ifd: int) -> TiffLayout:
    """Width, height, tile size, sample type, origin and tile offsets of a BigTIFF's first image.

    *head* is the file's first bytes and *tail* everything from its IFD at *ifd* on.
    """
    if head[:4] != b"II+\x00":
        raise ShippingDataError("the ship density is not a little-endian BigTIFF")
    count = struct.unpack("<Q", tail[:8])[0]
    tags: dict[int, np.ndarray] = {}
    for k in range(count):
        tag, kind, n = struct.unpack("<HHQ", tail[8 + 20 * k: 20 + 20 * k])
        if kind not in _TIFF_TYPES:
            continue
        size = np.dtype(_TIFF_TYPES[kind]).itemsize * n
        raw = tail[20 + 20 * k: 28 + 20 * k]
        if size > 8:
            at = struct.unpack("<Q", raw)[0] - ifd
            raw = tail[at: at + size]
        tags[tag] = np.frombuffer(raw[:size], _TIFF_TYPES[kind])
    try:
        if int(tags[259][0]) != 1 or int(tags[258][0]) != 32:
            raise ShippingDataError("the ship density TIFF is not uncompressed 32-bit tiles")
        return TiffLayout(int(tags[256][0]), int(tags[257][0]), int(tags[322][0]), int(tags[323][0]),
                          np.asarray(tags[324], np.int64), float(tags[33550][0]), float(tags[33922][3]),
                          float(tags[33922][4]))
    except KeyError as exc:
        raise ShippingDataError(f"the ship density TIFF lacks tag {exc}") from exc


def _steps(mean: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(np.log10(1 + mean) * STEPS_PER_DECADE), 0, 255).astype(np.uint8)


def _tile_name(kind: str) -> str:
    return f"{kind}_tiles_v{DATA_VERSION}.npz"


def _world_name(kind: str) -> str:
    return f"{kind}_world_v{DATA_VERSION}.npz"


def build_cache(zip_path: str, kind: str, cache_dir: str) -> None:
    """Read the ship density TIFF in *zip_path* once, into 0.01° tiles and a 0.04° world grid.

    The deflated member can only be read front to back, so it is read twice:
    once to its end for the IFD, then through its tiles in the order they are
    stored (Esri's files store them in blocks, not row by row).
    """
    with zipfile.ZipFile(zip_path) as archive:
        member = next((n for n in archive.namelist() if n.lower().endswith(".tif")), None)
        if member is None:
            raise ShippingDataError(f"{zip_path} holds no TIFF")
        with archive.open(member) as fh:
            head = fh.read(16)
            ifd = struct.unpack("<Q", head[8:16])[0]
            fh.seek(ifd)
            layout = _tiff_layout(head, fh.read(), ifd)
        tw, th = layout.tile_width, layout.tile_height
        across, down = -(-layout.width // tw), -(-layout.height // th)
        if len(layout.offsets) != across * down or tw % COARSE or th % COARSE:
            raise ShippingDataError("the ship density TIFF's tiles are not as expected")
        tile_bytes = tw * th * 4
        fine = np.zeros((down * th // FINE, across * tw // FINE), np.uint8)
        coarse = np.zeros((down * th // COARSE, across * tw // COARSE), np.float32)
        ftw, fth, ctw, cth = tw // FINE, th // FINE, tw // COARSE, th // COARSE
        order = np.argsort(layout.offsets, kind="stable")
        with archive.open(member) as fh:
            at, done = 0, 0
            while done < len(order):
                # A run of tiles stored back to back, read in one go
                first = int(layout.offsets[order[done]])
                run = 1
                while done + run < len(order) and run < 512 and \
                        layout.offsets[order[done + run]] == first + run * tile_bytes:
                    run += 1
                if first < at:
                    raise ShippingDataError("the ship density TIFF's tiles overlap")
                while at < first:                       # skip forward, reading what lies between
                    at += len(fh.read(min(first - at, 1 << 24)))
                raw = fh.read(run * tile_bytes)
                at += len(raw)
                if len(raw) != run * tile_bytes:
                    raise ShippingDataError("the ship density TIFF ends early")
                block = np.frombuffer(raw, "<i4").reshape(run, th, tw).astype(np.float32)
                block[(block == NODATA) | (block < 0)] = 0
                means = block.reshape(run, fth, FINE, ftw, FINE).mean(axis=(2, 4))
                steps = _steps(means)
                wide = means.reshape(run, cth, COARSE // FINE, ctw, COARSE // FINE).mean(axis=(2, 4))
                for k in range(run):
                    r, c = divmod(int(order[done + k]), across)
                    fine[r * fth:(r + 1) * fth, c * ftw:(c + 1) * ftw] = steps[k]
                    coarse[r * cth:(r + 1) * cth, c * ctw:(c + 1) * ctw] = wide[k]
                done += run
                if done * 10 // len(order) != (done - run) * 10 // len(order):
                    logger.info("  ship density: %d%% read", done * 100 // len(order))
    rows, cols = -(-layout.height // FINE), -(-layout.width // FINE)
    fine = fine[:rows, :cols]
    world = _steps(coarse[: -(-layout.height // COARSE), : -(-layout.width // COARSE)])
    tiles = {f"r{r // TILE}c{c // TILE}": fine[r:r + TILE, c:c + TILE]
             for r in range(0, rows, TILE) for c in range(0, cols, TILE) if fine[r:r + TILE, c:c + TILE].any()}
    meta = {"north": layout.north, "west": layout.west, "fine": layout.scale * FINE, "coarse": layout.scale * COARSE,
            "rows": rows, "cols": cols, "tile": TILE}
    os.makedirs(cache_dir, exist_ok=True)
    for name, arrays in ((_tile_name(kind), tiles), (_world_name(kind), {"world": world})):
        part = os.path.join(cache_dir, name + ".part.npz")
        np.savez_compressed(part, meta=np.array(json.dumps(meta)), **arrays)
        os.replace(part, os.path.join(cache_dir, name))


def ensure_shipping_data(kind: str, cache_dir: str | None = None) -> str:
    """The cache directory holding *kind*'s tiles, downloading and reading its zip once.

    Raises :class:`ShippingDataError`.
    """
    kind = resolve_ship_kind(kind)
    cache_dir = cache_dir or _default_cache_dir()
    if os.path.isfile(os.path.join(cache_dir, _world_name(kind))) and \
            os.path.isfile(os.path.join(cache_dir, _tile_name(kind))):
        return cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    spec = KINDS[kind]
    zip_path = os.path.join(cache_dir, os.path.basename(spec.file))
    try:
        if not os.path.isfile(zip_path):
            logger.info("Downloading the ship density of %s (one-time, ~%d MB) …", spec.label.lower(), spec.megabytes)
            _download(CATALOG_URL + spec.file, zip_path, label="ship density")
        logger.info("Reading the ship density (one-time, about a minute) …")
        build_cache(zip_path, kind, cache_dir)
    except ShippingDataError:
        raise
    except Exception as exc:                  # network, zip and file errors alike
        raise ShippingDataError(f"Could not get the ship density: {exc}") from exc
    os.remove(zip_path)
    return cache_dir


# ---------------------------------------------------------------------------
# Reading the cache over a view
# ---------------------------------------------------------------------------


def load_traffic(kind: str, view: View, cell: float, cache_dir: str | None = None) -> Traffic:
    """The traffic of *kind* over *view* (see :func:`climate.visible_box`), in cells of about *cell* degrees.

    The 0.01° tiles serve views that need cells finer than the 0.04° world
    grid; cells coarser than the stored ones keep the busiest value in them,
    so a lane a cell wide still shows. The extent is in longitudes relative to
    ``view.centre_lon``. Raises :class:`ShippingDataError`.
    """
    kind = resolve_ship_kind(kind)
    cache_dir = ensure_shipping_data(kind, cache_dir)
    path = os.path.join(cache_dir, _world_name(kind))
    try:
        store = np.load(path)
        meta = json.loads(str(store["meta"]))
        fine = cell < 0.75 * meta["coarse"]
        if fine:
            path = os.path.join(cache_dir, _tile_name(kind))
            store = np.load(path)
    except Exception as exc:
        raise ShippingDataError(f"Could not read the cached ship density {path}: {exc}") from exc
    step = meta["fine"] if fine else meta["coarse"]
    rows = meta["rows"] if fine else store["world"].shape[0]
    period = int(round(360 / step))
    c0 = int(np.floor((view.centre_lon + view.west - meta["west"]) / step))
    c1 = int(np.ceil((view.centre_lon + view.east - meta["west"]) / step))
    r0 = max(0, int(np.floor((meta["north"] - view.north) / step)))
    r1 = min(rows, int(np.ceil((meta["north"] - view.south) / step)))
    cols = np.arange(c0, max(c1, c0 + 1)) % period
    if r1 <= r0:
        values = np.zeros((1, len(cols)), np.uint8)
        r1 = r0 + 1
    elif fine:
        values = np.zeros((r1 - r0, len(cols)), np.uint8)
        size = meta["tile"]
        for tr in range(r0 // size, (r1 - 1) // size + 1):
            top = tr * size
            for tc in np.unique(cols // size):
                key = f"r{tr}c{tc}"
                if key not in store.files:      # no ships there
                    continue
                tile = store[key]
                where = np.nonzero(cols // size == tc)[0]
                local = cols[where] - tc * size
                where, local = where[local < tile.shape[1]], local[local < tile.shape[1]]
                lines = np.arange(max(r0, top), min(r1, top + size)) - top
                lines = lines[lines < tile.shape[0]]
                values[(lines + top - r0)[:, None], where[None, :]] = tile[lines[:, None], local[None, :]]
    else:
        values = store["world"][r0:r1][:, cols]
    west = meta["west"] + c0 * step - view.centre_lon
    extent = (west, west + len(cols) * step, meta["north"] - r1 * step, meta["north"] - r0 * step)
    factor = int(cell // step)
    if factor > 1:                          # the mean traffic of each block, back on the log scale
        h, w = values.shape[0] // factor * factor, values.shape[1] // factor * factor
        if h and w:
            linear = 10 ** (values[:h, :w].astype(np.float32) / STEPS_PER_DECADE) - 1
            values = _steps(linear.reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3)))
            extent = (extent[0], extent[0] + w * step, extent[3] - h * step, extent[3])
            step *= factor
    return Traffic(kind, values, extent, step)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

PALETTE = ("#2c7fb8", "#6a51a3", "#ae017e", "#e31a1c", "#fd8d3c")
KEY_LABELS = ("Occasional", "Regular", "Busy", "Very busy", "Busiest lanes")
# The colours rank the traffic in view: the quieter half of the sea with ships is left clear, and the
# busiest 2% get the last colour. Ranks, not a fixed scale, as the traffic differs a hundredfold between
# seas (and kinds of ship): where it is all heavy, as in the Channel, its lanes still stand out.
CLEAR_SHARE = 0.5
TOP_SHARE = 0.02
RANK_POWER = 2.5            # the ramp's warm end for the busiest few percent, not the busiest seas whole


def colour_ranks(values: np.ndarray) -> np.ndarray:
    """For each of the 256 stored values, where it falls on the colour ramp (0-1), or -1 to leave it clear."""
    counts = np.bincount(values[values > 0].ravel(), minlength=256)[:256].astype(float)
    if counts.sum() < 100:                          # too little to rank: a fixed scale
        t = (np.arange(256) - 5.0 * STEPS_PER_DECADE) / (2.5 * STEPS_PER_DECADE)
    else:
        below = (np.cumsum(counts) - counts / 2) / counts.sum()   # the share of the traffic quieter than each value
        t = (below - CLEAR_SHARE) / (1 - CLEAR_SHARE - TOP_SHARE)
        t = np.sign(t) * np.abs(t) ** RANK_POWER
    t = np.where(t < 0, -1.0, np.minimum(t, 1.0))
    t[0] = -1.0
    return t


def _lookup(alpha: float, ranks: np.ndarray) -> np.ndarray:
    """RGBA for each of the 256 stored values from :func:`colour_ranks`: through :data:`PALETTE`, more opaque as
    busier."""
    from matplotlib.colors import LinearSegmentedColormap

    ramp = LinearSegmentedColormap.from_list("ships", PALETTE)
    rgba = ramp(np.clip(ranks, 0, 1))
    rgba[:, 3] = np.where(ranks < 0, 0.0, alpha * (0.15 + 0.85 * np.clip(ranks, 0, 1)))
    return (rgba * 255).round().astype(np.uint8)


def add_shipping_overlay(ax: GeoAxes, kind: str, alpha: float = 0.85, regrid_shape: int = 750,
                         cache_dir: str | None = None) -> Traffic:
    """Colour the sea of *ax* by how busy it is with ships of *kind*, relative to the traffic in view.

    Raises :class:`ShippingDataError`.
    """
    view = visible_box(ax, margin=0.1)
    cell = max(view.east - view.west, view.north - view.south) / regrid_shape
    traffic = load_traffic(kind, view, cell, cache_dir)
    ax.imshow(_lookup(alpha, colour_ranks(traffic.values))[traffic.values], origin="upper", extent=traffic.extent,
              transform=ccrs.PlateCarree(central_longitude=view.centre_lon), interpolation="nearest",
              regrid_shape=regrid_shape, zorder=5.2)
    logger.info("Ship traffic drawn: %s, %d × %d cells of %.3f°.", KINDS[traffic.kind].label.lower(),
                traffic.values.shape[1], traffic.values.shape[0], traffic.cell)
    return traffic


def add_shipping_legend(ax: GeoAxes, kind: str, alpha: float = 0.85, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key from occasional to the busiest lanes, relative to the map."""
    lookup = _lookup(alpha, np.linspace(0.1, 1, len(KEY_LABELS)))
    handles = [mpatches.Patch(facecolor=tuple(rgba / 255), edgecolor="#333333", linewidth=0.6) for rgba in lookup]
    title = (f"Ship traffic in view: {KINDS[resolve_ship_kind(kind)].label.lower()}, from their AIS positions "
             "2015-2021 (World Bank, IMF)")
    return _key(ax, handles, list(KEY_LABELS), title, len(handles), x, y)
