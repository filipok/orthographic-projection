"""Permafrost: the Northern Hemisphere's permanently frozen ground, by how continuous it is.

Data source
-----------
Obu, J., Westermann, S., Bartsch, A., et al. (2019). Northern Hemisphere
permafrost map based on TTOP modelling for 2000-2016 at 1 km² scale.
*Earth-Science Reviews* 193, 299-316.
https://doi.org/10.1016/j.earscirev.2019.04.023 — the permafrost zones
(continuous, discontinuous, sporadic, isolated), ESA Permafrost CCI /
University of Oslo, via PANGAEA, CC BY 4.0.

The zones come as polygons in a north polar stereographic projection; the
~13 MB archive is downloaded once and filled into a 5 km grid in that
projection (cached under ``~/.cache/ortho_tiles/permafrost/``), drawn as it is.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

from biomes import _download, _key, burn

logger = logging.getLogger(__name__)

PERMAFROST_URL = ("https://store.pangaea.de/Publications/ObuJ-etal_2018/"
                  "UiO_PEX_PERZONES_5.0_20181128_2000_2016_NH.zip")
DATA_VERSION = 1
CELL_M = 5000.0
PERMAFROST_ATTRIBUTION = "Permafrost: Obu et al. (2019), ESA Permafrost CCI / University of Oslo, CC BY 4.0"
# The data's projection: polar stereographic, true scale at 71°N
PROJECTION = ccrs.NorthPolarStereo(true_scale_latitude=71)

# Zone code -> (name, how much of the ground is frozen, colour)
ZONES: dict[int, tuple[str, str, tuple[int, int, int]]] = {
    4: ("Continuous", "90-100% of the ground", (70, 60, 150)),
    3: ("Discontinuous", "50-90%", (120, 120, 200)),
    2: ("Sporadic", "10-50%", (165, 185, 225)),
    1: ("Isolated patches", "0-10%", (205, 222, 240)),
}


class PermafrostDataError(RuntimeError):
    """The permafrost zones could not be downloaded or read."""


class PermafrostGrid(NamedTuple):
    zones: np.ndarray          # (rows, cols) uint8, rows north (top) to south; 0 none
    extent: tuple[float, float, float, float]   # x0, x1, y0, y1 in PROJECTION metres


def rasterise(shapes: list[tuple[np.ndarray, list[int], int]], bbox: tuple[float, float, float, float],
              cell: float = CELL_M) -> PermafrostGrid:
    """Fill a grid of *cell* metres over *bbox* (x0, y0, x1, y1) with each polygon's zone code.

    Each shape is ``(points in metres, ring starts, code)``. The polygons are
    burned with :func:`biomes.burn`, its degrees standing in for grid cells.
    """
    x0, y0, x1, y1 = bbox
    cols, rows = int(np.ceil((x1 - x0) / cell)), int(np.ceil((y1 - y0) / cell))
    grid = np.zeros((rows, cols), np.uint8)
    for points, parts, code in shapes:
        # burn maps (lon, lat) to (column, row) as ((lon + 180), (90 - lat)) at one cell per degree
        cells = np.column_stack([(points[:, 0] - x0) / cell - 180, 90 - (y1 - points[:, 1]) / cell])
        burn(grid, cells, parts, code, 1)
    return PermafrostGrid(grid, (x0, x0 + cols * cell, y1 - rows * cell, y1))


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "permafrost")


def load_permafrost(cache_dir: str | None = None) -> PermafrostGrid:
    """The permafrost zone grid, built from the PANGAEA archive (~13 MB) once."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"perzones_{int(CELL_M)}m_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        import shapefile  # pyshp, which Cartopy depends on

        os.makedirs(cache_dir, exist_ok=True)
        download = os.path.join(cache_dir, "perzones.zip")
        logger.info("Downloading the permafrost zones (Obu et al. 2019; one-time, ~13 MB) …")
        try:
            _download(PERMAFROST_URL, download, label="permafrost")
            with zipfile.ZipFile(download) as archive:
                members = {n.rsplit(".", 1)[-1].lower(): n for n in archive.namelist()}
                reader = shapefile.Reader(shp=io.BytesIO(archive.read(members["shp"])),
                                          shx=io.BytesIO(archive.read(members["shx"])),
                                          dbf=io.BytesIO(archive.read(members["dbf"])))
                with reader:
                    x0, y0, x1, y1 = reader.bbox
                    shapes = []
                    for record, polygon in zip(reader.iterRecords(fields=["GRIDCODE"]), reader.iterShapes()):
                        code = int(str(record.as_dict()["GRIDCODE"])) if record is not None else 0
                        if polygon is not None and code in ZONES:
                            shapes.append((np.asarray(polygon.points, float).reshape(-1, 2), list(polygon.parts), code))
            grid = rasterise(shapes, (x0, y0, x1, y1))
        except Exception as exc:              # network, archive and shapefile errors alike
            raise PermafrostDataError(f"Could not read the permafrost zones: {exc}") from exc
        finally:
            if os.path.exists(download):
                os.remove(download)
        part = path + ".part.npz"
        np.savez_compressed(part, zones=grid.zones, extent=np.array(grid.extent))
        os.replace(part, path)
    with np.load(path) as data:
        x0, x1, y0, y1 = (float(v) for v in data["extent"])
        return PermafrostGrid(data["zones"], (x0, x1, y0, y1))


def permafrost_rgba(zones: np.ndarray, alpha: float = 0.7) -> np.ndarray:
    palette = np.zeros((256, 4), np.uint8)
    for code, (_, _, rgb) in ZONES.items():
        palette[code] = (*rgb, round(alpha * 255))
    return palette[zones]


def add_permafrost_overlay(ax: GeoAxes, grid: PermafrostGrid, alpha: float = 0.7, regrid_shape: int = 750) -> None:
    """Draw the permafrost zones on *ax*."""
    ax.imshow(permafrost_rgba(grid.zones, alpha), origin="upper", extent=grid.extent, transform=PROJECTION,
              interpolation="nearest", regrid_shape=regrid_shape, zorder=5)
    logger.info("Permafrost drawn (alpha=%.2f).", alpha)


def add_permafrost_legend(ax: GeoAxes, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the permafrost zones."""
    handles = [mpatches.Patch(facecolor=(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255), edgecolor="#333333", linewidth=0.6)
               for _, _, rgb in ZONES.values()]
    labels = [f"{name}: {share}" for name, share, _ in ZONES.values()]
    return _key(ax, handles, labels, "Permafrost, 2000-2016 (Obu et al. 2019): share of the ground frozen year-round",
                4, x, y)
