"""Elevation from the open Terrarium terrain tiles: a relief layer, and heights for other layers.

Data source
-----------
Mapzen / Tilezen terrain tiles, AWS Open Data (``s3://elevation-tiles-prod``):
256 px Web Mercator PNG tiles whose colours encode height in metres,
``R * 256 + G + B / 256 - 32768``, land and sea floor, up to ±85.05°. At the
zooms used here the heights come from GMTED2010 and SRTM (USGS) on land and
ETOPO1 (NOAA NCEI) at sea; the sources must be credited (see
https://github.com/tilezen/joerd/blob/master/docs/attribution.md).

Tiles are cached as downloaded, under ``~/.cache/ortho_tiles/terrarium/``.
They never change, so the cache never expires.

The relief layer colours land by height in classic atlas bands (layer
tints) and shades it with light from the north-west; the sea is left to
the base map. :func:`elevation_grid` samples the heights onto a latitude /
longitude grid, which :mod:`trewartha` uses for its highland group.
"""

from __future__ import annotations

import concurrent.futures
import io
import logging
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from collections.abc import Callable

import numpy as np
import shapely
from PIL import Image, ImageDraw

import cartopy.io.img_tiles as cimgt
import cartopy.io.shapereader as shapereader
import matplotlib.patches as mpatches
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend

logger = logging.getLogger(__name__)

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ELEVATION_ATTRIBUTION = ("Elevation: Mapzen Terrain Tiles (AWS Open Data); GMTED2010 and SRTM "
                         "courtesy of the USGS; ETOPO1, NOAA NCEI")

# Zoom of the relief layer's tiles: ~5 km pixels at the equator, ~500 tiles a hemisphere
RELIEF_ZOOM = 5

# Layer tints: (lowest height in m, key label, (R, G, B)); land below 0 m takes the first
ELEVATION_BANDS: list[tuple[float, str, tuple[int, int, int]]] = [
    (-math.inf, "0", (118, 168, 108)),
    (200, "200", (166, 196, 128)),
    (500, "500", (222, 214, 150)),
    (1000, "1,000", (214, 176, 118)),
    (2000, "2,000", (180, 132, 92)),
    (3000, "3,000", (150, 110, 90)),
    (4000, "4,000", (186, 170, 166)),
    (5000, "5,000 m", (240, 240, 240)),
]

_EARTH_RADIUS = 6378137.0                  # Web Mercator sphere, m
_MERCATOR_MAX_LAT = 85.0511287798066


class ElevationDataError(RuntimeError):
    """Terrain tiles could not be downloaded or read."""


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles")


def decode_terrarium(rgb: np.ndarray) -> np.ndarray:
    """Heights in metres from a Terrarium tile's RGB(A) pixels."""
    rgb = np.asarray(rgb, dtype=np.float32)
    return rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256 - 32768


def fetch_tile(tile: tuple[int, int, int], cache_dir: str | None = None, timeout: float = 30,
               use_cache: bool = True) -> np.ndarray:
    """Terrarium tile ``(x, y, z)`` as an RGBA ``uint8`` array, from the cache or the network.

    With *use_cache* False the cache is neither read nor written. Raises
    ``OSError`` if it cannot be downloaded; failures are never cached.
    """
    x, y, z = tile
    path = Path(cache_dir or _default_cache_dir()) / "terrarium" / f"{z}_{x}_{y}.png"
    if use_cache and path.is_file():
        data = path.read_bytes()
    else:
        request = urllib.request.Request(TERRARIUM_URL.format(x=x, y=y, z=z),
                                         headers={"User-Agent": "ortho/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = resp.read()
        if not use_cache:
            return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(data)
        os.replace(part, path)
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


class TerrariumTiles(cimgt.GoogleWTS):
    """Terrarium tiles as a Cartopy tile source; images are the raw encoded heights.

    :func:`relief_rgba` turns a merged mosaic into the coloured, shaded layer.
    ``get_image`` raises on a failed download, like ``ortho.CachedOSM``.
    """

    def __init__(self, cache_dir: str | None = None, timeout: float = 30, use_cache: bool = True) -> None:
        super().__init__(desired_tile_form="RGBA", user_agent="ortho/1.0")
        self.tile_cache_dir = cache_dir
        self.timeout = timeout
        self.use_cache = use_cache

    def _image_url(self, tile: tuple[int, int, int]) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        x, y, z = tile
        return TERRARIUM_URL.format(x=x, y=y, z=z)

    def get_image(self, tile: tuple[int, int, int]):  # same (image, extent, origin) shape as Cartopy
        return (fetch_tile(tile, self.tile_cache_dir, self.timeout, use_cache=self.use_cache),
                self.tileextent(tile), "lower")


# ---------------------------------------------------------------------------
# Heights on a latitude / longitude grid
# ---------------------------------------------------------------------------


def elevation_grid(
    shape: tuple[int, int],
    west: float, north: float, cell: float,
    zoom: int = 4,
    cache_dir: str | None = None,
) -> np.ndarray:
    """Height (m) at the centre of each cell of a latitude / longitude grid.

    Every tile of *zoom* is used (4**zoom of them; 256 at zoom 4, ~10 km
    pixels, about 25 MB the first time). Cells beyond ±85.05° are NaN.
    Raises :class:`ElevationDataError` if a tile cannot be downloaded.
    """
    n = 2 ** zoom
    size = 256
    mosaic = np.empty((n * size, n * size), np.float32)

    def load(tile: tuple[int, int, int]) -> None:
        x, y, _ = tile
        mosaic[y * size:(y + 1) * size, x * size:(x + 1) * size] = decode_terrarium(fetch_tile(tile, cache_dir))

    logger.info("Reading elevation (%d terrain tiles at zoom %d) …", n * n, zoom)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        try:
            for job in [pool.submit(load, (x, y, zoom)) for x in range(n) for y in range(n)]:
                job.result()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise ElevationDataError(f"Could not download terrain tiles: {exc}") from exc

    rows, cols = shape
    lons = west + (np.arange(cols) + 0.5) * cell
    lats = north - (np.arange(rows) + 0.5) * cell
    px = np.clip(((lons + 180) / 360 * n * size).astype(int), 0, n * size - 1)
    inside = np.abs(lats) < _MERCATOR_MAX_LAT
    merc_y = np.arcsinh(np.tan(np.radians(np.clip(lats, -_MERCATOR_MAX_LAT, _MERCATOR_MAX_LAT))))
    py = np.clip(((1 - merc_y / math.pi) / 2 * n * size).astype(int), 0, n * size - 1)
    grid = mosaic[py[:, None], px[None, :]]
    grid[~inside] = np.nan
    return grid


# ---------------------------------------------------------------------------
# The relief layer
# ---------------------------------------------------------------------------


def hillshade(height: np.ndarray, extent: tuple[float, float, float, float],
              vertical_exaggeration: float = 8.0, azimuth: float = 315, altitude: float = 45) -> np.ndarray:
    """Lambertian shading (0-1) of a Web Mercator height mosaic, light from *azimuth*.

    *height* rows run from south to north over *extent* ``(x0, x1, y0, y1)``
    in Mercator metres; pixel sizes are scaled to true ground distance.
    """
    rows, cols = height.shape
    az, alt = math.radians(azimuth), math.radians(altitude)
    if rows < 2 or cols < 2:                                   # too small for a slope: level ground
        return np.full(height.shape, math.sin(alt))
    x0, x1, y0, y1 = extent
    pixel = (x1 - x0) / cols                                   # Mercator m per pixel
    merc_y = y0 + (np.arange(rows) + 0.5) * (y1 - y0) / rows
    ground = pixel * np.cos(np.arctan(np.sinh(merc_y / _EARTH_RADIUS)))   # true m per pixel
    dz_dy, dz_dx = np.gradient(height * vertical_exaggeration)
    dz_dx = dz_dx / ground[:, None]
    dz_dy = dz_dy / ground[:, None]                            # rows increase northward
    # Light direction (east, north, up); the surface normal is (-dz/dx, -dz/dy, 1)
    light = (math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt))
    shade = (-dz_dx * light[0] - dz_dy * light[1] + light[2]) / np.sqrt(dz_dx ** 2 + dz_dy ** 2 + 1)
    return np.clip(shade, 0, 1)


def _mercator_xy(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat = np.clip(lat, -_MERCATOR_MAX_LAT, _MERCATOR_MAX_LAT)
    return _EARTH_RADIUS * np.radians(lon), _EARTH_RADIUS * np.arcsinh(np.tan(np.radians(lat)))


# Natural Earth land and lake polygons, by scale ("50m", "10m"), read once
_land_polygons: dict[str, list[Any]] = {}
_lake_polygons: dict[str, list[Any]] = {}


def land_and_lakes(
    scale: str = "50m", natural_earth: Callable[..., str] = shapereader.natural_earth,
) -> tuple[list[Any], list[Any]]:
    """Natural Earth's land and lake polygons at *scale*, read once."""
    if scale not in _land_polygons:
        land: list[Any] = []
        lakes: list[Any] = []
        for name, polygons in (("land", land), ("lakes", lakes)):
            for geom in shapereader.Reader(natural_earth(scale, "physical", name)).geometries():
                polygons.extend(shapely.get_parts(geom))
        _land_polygons[scale], _lake_polygons[scale] = land, lakes
    return _land_polygons[scale], _lake_polygons[scale]


def land_raster(
    shape: tuple[int, int], extent: tuple[float, float, float, float],
    natural_earth: Callable[..., str] = shapereader.natural_earth, scale: str = "50m",
) -> np.ndarray:
    """True where a Web Mercator raster (rows south to north) is on land.

    Natural Earth land minus its lakes at *scale* (1:50m, or 1:10m for close
    views), so land below sea level, like the Caspian Depression or the Dutch
    polders, still counts as land, while the lakes are left to the base map.
    """
    land, lakes = land_and_lakes(scale, natural_earth)
    rows, cols = shape
    x0, x1, y0, y1 = extent
    image = Image.new("1", (cols, rows), 0)
    draw = ImageDraw.Draw(image)

    def pixels(ring: Any, shift: float = 0.0) -> list[tuple[float, float]]:
        lon, lat = np.asarray(ring.coords).T[:2]
        x, y = _mercator_xy(lon + shift, lat)
        # Image rows run north to south; flipped below to match the mosaic
        return list(zip((x - x0) / (x1 - x0) * cols, (y1 - y) / (y1 - y0) * rows))

    # A mosaic across the date line runs past 180°; land beyond it is drawn shifted by 360°
    west, south, east, north = _lon_lat_box(extent)
    views = [(shift, shapely.box(west - shift, south, east - shift, north))
             for shift in (-360.0, 0.0, 360.0) if west - shift < 180 and east - shift > -180]
    for shift, view in views:
        for polygon in land:
            if not polygon.intersects(view):
                continue
            draw.polygon(pixels(polygon.exterior, shift), fill=1)
            for hole in polygon.interiors:
                draw.polygon(pixels(hole, shift), fill=0)
    for shift, view in views:
        for lake in lakes:
            if lake.intersects(view):
                draw.polygon(pixels(lake.exterior, shift), fill=0)
    return np.asarray(image, dtype=bool)[::-1]


def _lon_lat_box(extent: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """``(west, south, east, north)`` of a Web Mercator *extent*, in degrees."""
    x0, x1, y0, y1 = extent
    lon0, lon1 = np.degrees(np.array([x0, x1]) / _EARTH_RADIUS)
    lat0, lat1 = np.degrees(np.arctan(np.sinh(np.array([y0, y1]) / _EARTH_RADIUS)))
    return float(lon0), float(lat0), float(lon1), float(lat1)


# Hill shading is tuned for zoom 5 tiles. Finer tiles resolve steeper slopes,
# so the shading's vertical exaggeration falls as the zoom rises.
_SHADE_ZOOM = 5
_SHADE_EXAGGERATION = 8.0
# From this zoom the land mask uses the 1:10m coastline
_FINE_COAST_ZOOM = 7


def shade_exaggeration(zoom: int) -> float:
    """Vertical exaggeration for hill shading terrain tiles at *zoom*."""
    return max(1.0, _SHADE_EXAGGERATION * 0.6 ** max(0, zoom - _SHADE_ZOOM))


# Beyond these latitudes the ice-sheet surface may replace the tiles' bedrock
_ICE_SHEET_LAT = 59.0
_ICE_SHEET_GAP_M = 500.0


def with_ice_surface(height: np.ndarray, extent: tuple[float, float, float, float],
                     cache_dir: str | None = None, use_cache: bool = True) -> np.ndarray:
    """*height* with the Greenland and Antarctic ice sheets at their surface.

    From zoom 5 the terrain tiles hold the bedrock under the ice sheets
    (central Greenland about -50 m), while zoom 4 holds the ice surface
    (about 2,900 m). Near the poles, wherever the zoom 4 tile is more than
    500 m higher, its height is used. *height* is a mosaic at zoom 5 or more
    (rows south to north over *extent*); it is returned unchanged if the
    zoom 4 tiles cannot be downloaded.
    """
    rows, cols = height.shape
    x0, x1, y0, y1 = extent
    merc_y = y0 + (np.arange(rows) + 0.5) * (y1 - y0) / rows
    polar = np.abs(np.degrees(np.arctan(np.sinh(merc_y / _EARTH_RADIUS)))) >= _ICE_SHEET_LAT
    if not polar.any():
        return height
    world = 2 * math.pi * _EARTH_RADIUS
    n = 2 ** 4 * 256                                         # zoom 4 pixels around the world
    merc_x = x0 + (np.arange(cols) + 0.5) * (x1 - x0) / cols
    px = np.clip(((merc_x + world / 2) / world * n).astype(int), 0, n - 1)
    py = np.clip(((world / 2 - merc_y[polar]) / world * n).astype(int), 0, n - 1)
    try:
        coarse = {
            (tx, ty): decode_terrarium(fetch_tile((int(tx), int(ty), 4), cache_dir, use_cache=use_cache))
            for tx in np.unique(px // 256) for ty in np.unique(py // 256)
        }
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        logger.warning("Ice sheets are shown at bedrock height (%s)", exc)
        return height
    surface = np.empty((len(py), cols), np.float32)
    for i, y in enumerate(py):
        for tx in np.unique(px // 256):
            in_tile = px // 256 == tx
            surface[i, in_tile] = coarse[(tx, y // 256)][y % 256, px[in_tile] % 256]
    out = height.copy()
    patch = out[polar]
    out[polar] = np.where(surface - patch > _ICE_SHEET_GAP_M, surface, patch)
    return out


def relief_rgba(mosaic: np.ndarray, extent: tuple[float, float, float, float], alpha: float = 0.8,
                land: np.ndarray | None = None, cache_dir: str | None = None,
                use_cache: bool = True, zoom: int = RELIEF_ZOOM) -> np.ndarray:
    """Colour and shade a merged Terrarium mosaic; the sea, lakes and missing tiles are clear.

    *mosaic* is the RGBA mosaic Cartopy merges (rows south to north) from
    tiles at *zoom*, whose fully transparent pixels mark tiles that failed to
    download. *land* defaults to :func:`land_raster`. Ice sheets are raised to
    their surface with :func:`with_ice_surface`.
    """
    height = with_ice_surface(decode_terrarium(mosaic[..., :3]), extent, cache_dir, use_cache)
    if land is None:
        land = land_raster(mosaic.shape[:2], extent, scale="10m" if zoom >= _FINE_COAST_ZOOM else "50m")
    land = land & (mosaic[..., 3] > 0)
    bounds = np.array([band[0] for band in ELEVATION_BANDS[1:]])
    colours = np.array([band[2] for band in ELEVATION_BANDS], np.float32)
    rgb = colours[np.digitize(height, bounds)]
    shade = hillshade(np.maximum(height, 0), extent, vertical_exaggeration=shade_exaggeration(zoom))
    flat = math.sin(math.radians(45))                         # shade of level ground
    # Darken slopes facing away from the light and lighten those facing it
    factor = np.clip(1 + 0.9 * (shade - flat), 0.35, 1.3)
    rgb = np.clip(rgb * factor[..., None], 0, 255)
    out = np.zeros(mosaic.shape[:2] + (4,), np.uint8)
    out[..., :3] = rgb.astype(np.uint8)
    out[..., 3] = np.where(land, round(alpha * 255), 0)
    return out


def add_elevation_legend(ax: GeoAxes, y: float = -0.01, x: float = 0.5) -> Legend:
    """Add a key below the globe with the colour of each height band.

    *y* is the key's top edge and *x* its horizontal centre, in axes
    coordinates. Added as a separate artist like the crop and route keys.
    """
    handles = [mpatches.Patch(facecolor=(r / 255, g / 255, b / 255), edgecolor="#333333", linewidth=0.6)
               for _, _, (r, g, b) in ELEVATION_BANDS]
    legend = Legend(
        ax,
        handles,
        [label for _, label, _ in ELEVATION_BANDS],
        loc="upper center",
        bbox_to_anchor=(x, y),
        ncol=len(handles),
        fontsize=11,
        title="Elevation above sea level (each colour from the height shown)",
        title_fontsize=10,
        frameon=True,
        fancybox=True,
        framealpha=0.85,
        edgecolor="#444444",
        handlelength=1.6,
        columnspacing=1.2,
        borderpad=0.7,
    )
    # add_artist would clip the key to the globe disc, which it sits outside
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
