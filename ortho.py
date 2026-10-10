from __future__ import annotations

import argparse
import concurrent.futures
import difflib
import functools
import getpass
import logging
import math
import os
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from matplotlib.figure import Figure
import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
import cartopy.feature as cfeature
import matplotlib.patheffects as pe
from cartopy.geodesic import Geodesic
from cartopy.mpl.geoaxes import GeoAxes
from shapely.geometry import Polygon

from google_tiles import (
    API_KEY_ENV,
    API_KEY_ENVS,
    GoogleMapTiles,
    GoogleTilesError,
    resolve_api_key,
)
from crops import (
    CROP_ATTRIBUTION, CROP_NAMES, CropDataError, add_crop_legend, crop_label, draw_crops,
    load_crop_layer, resolve_crops,
)
from ice import FIRST_ICE_YEAR, IceDataError, draw_ice, load_ice_layers
from koppen import (
    KOPPEN_ATTRIBUTION, KoppenDataError, add_koppen_overlay, add_koppen_legend,
    resolve_koppen_classes,
)
from rotation import far_side_up, globe_projection, initial_bearing, normalise_bearing
from elevation import (
    ELEVATION_ATTRIBUTION, RELIEF_ZOOM, TerrariumTiles, add_elevation_legend, relief_rgba,
)
from soil import (
    SOIL_ATTRIBUTION, SoilDataError, add_soil_legend, add_soil_overlay, resolve_soil_classes,
)
from soil_properties import (
    DEPTHS as SOIL_DEPTHS, SOIL_PROPERTIES, SOIL_PROPERTY_ATTRIBUTION, SoilPropertyError,
    add_soil_property_legend, add_soil_property_overlay, resolve_soil_property,
)
from vegetation import (
    FIRST_LAND_COVER_YEAR, LATEST_LAND_COVER_YEAR, VEGETATION_ZOOM, add_land_cover_legend,
    add_ndvi_legend, land_cover_attribution, land_cover_rgba, land_cover_tiles, ndvi_attribution,
    ndvi_rgba, ndvi_tiles, resolve_land_cover_classes, resolve_ndvi_month,
)
from trewartha import (
    TREWARTHA_ATTRIBUTION, TrewarthaDataError, add_trewartha_legend, add_trewartha_overlay,
    resolve_trewartha_classes,
)
from routes import Route, add_route_legend, draw_routes, load_routes
from tile_fetch import download_tile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tile cache configuration
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles")

# How long a cached OSM tile is reused before it is fetched again
TILE_CACHE_MAX_AGE_DAYS = 7


def configure_tile_cache(cache_dir: str | None = None) -> str:
    """Create the tile cache directory (default ``~/.cache/ortho_tiles``) and return it.

    Pass the result to :func:`generate_orthographic_map` as ``tile_cache_dir``.
    OSM and NASA tiles are cached; Google's terms do not allow caching its tiles.
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    logger.debug("Tile cache directory: %s", cache_dir)
    return cache_dir


class CachedOSM(cimgt.OSM):
    """OSM tile source with a safer on-disk cache than Cartopy's built-in one.

    Cartopy's cache stores the grey placeholder it substitutes for a failed
    download, so one network error leaves a permanent hole in every later
    render, and it never expires tiles. This version caches only successful
    downloads, writes cache files atomically, and refetches tiles older than
    *max_age_days*. With ``cache=False`` (the default) nothing is cached.
    """

    def __init__(self, *args: Any, max_age_days: float = TILE_CACHE_MAX_AGE_DAYS,
                 timeout: float = 30, **kwargs: Any) -> None:
        self.max_age_days = max_age_days
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    @property
    def _cache_dir(self) -> Path:  # pyright: ignore[reportIncompatibleMethodOverride]
        assert self.cache_path is not None
        return Path(self.cache_path) / "osm"

    def _cache_file(self, tile: tuple[int, int, int]) -> Path | None:
        if self.cache_path is None:
            return None
        return self._cache_dir / ("_".join(str(i) for i in tile) + ".npy")

    def _is_fresh(self, path: Path) -> bool:
        try:
            age_days = (time.time() - path.stat().st_mtime) / 86400
        except OSError:
            return False
        return age_days < self.max_age_days

    def get_image(self, tile: tuple[int, int, int]):  # same (image, extent, origin) shape as Cartopy
        """Return a tile from the cache or the network; raises if the download fails.

        Failures are never cached. :class:`BufferedTileSource` turns them
        into transparent tiles.
        """
        cached = self._cache_file(tile)
        if cached is not None and cached in self.cache and self._is_fresh(cached):
            return np.load(cached, allow_pickle=False), self.tileextent(tile), "lower"

        img = download_tile(self._image_url(tile), self.user_agent, self.timeout)

        if cached is not None:
            part = cached.with_name(cached.name + ".part")
            with open(part, "wb") as fh:
                np.save(fh, img, allow_pickle=False)
            os.replace(part, cached)
            self.cache.add(cached)
        return img, self.tileextent(tile), "lower"


class CachedNASA(CachedOSM):
    """NASA Blue Marble imagery from GIBS, with the same safe cache as OSM.

    Blue Marble: Next Generation true-colour land (a 2004 composite at
    500 m) over shaded relief and ocean-floor bathymetry, served in Web
    Mercator by NASA's Global Imagery Browse Services up to zoom 8. Public
    domain, no key, no regional limits. The imagery never changes, so cached
    tiles are kept far longer than OSM's.
    """

    URL = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/"
           "default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg")

    def __init__(self, *args: Any, max_age_days: float = 365, **kwargs: Any) -> None:
        super().__init__(*args, max_age_days=max_age_days, **kwargs)

    @property
    def _cache_dir(self) -> Path:  # pyright: ignore[reportIncompatibleMethodOverride]
        assert self.cache_path is not None
        return Path(self.cache_path) / "nasa"

    def _image_url(self, tile: tuple[int, int, int]) -> str:
        x, y, z = tile
        return self.URL.format(x=x, y=y, z=z)


def load_env_files() -> list[str]:
    """Load API keys from .env-style files into the process environment.

    Keys can live in one central file, ``~/myapikeys.env``.
    Existing environment variables are never overridden, so the first file
    that sets a variable wins. Priority (highest first):

      1. ``$ORTHO_ENV_FILE`` — explicit override
      2. ``~/myapikeys.env`` — central key file
      3. ``./.env``          — local fallback

    Returns the files actually loaded.
    """
    from dotenv import load_dotenv

    candidates = [
        os.environ.get("ORTHO_ENV_FILE"),
        os.path.expanduser("~/myapikeys.env"),
        ".env",
    ]
    loaded: list[str] = []
    for path in candidates:
        if path and os.path.isfile(path):
            load_dotenv(dotenv_path=path)
            loaded.append(path)
    logger.debug("Loaded env files: %s", loaded)
    return loaded


MAJOR_METROPOLISES = {
    "NYC": {"lat": 40.7128, "lon": -74.0060, "slug": "nyc"},
    "Moscow": {"lat": 55.7558, "lon": 37.6173, "slug": "moscow"},
    "Shanghai": {"lat": 31.2304, "lon": 121.4737, "slug": "shanghai"},
    "London": {"lat": 51.5074, "lon": -0.1278, "slug": "london"},
    "Paris": {"lat": 48.8566, "lon": 2.3522, "slug": "paris"},
    "Berlin": {"lat": 52.5200, "lon": 13.4050, "slug": "berlin"},
    "Ankara": {"lat": 39.9334, "lon": 32.8597, "slug": "ankara"},
    "New Delhi": {"lat": 28.6139, "lon": 77.2090, "slug": "new_delhi"},
    "Tokyo": {"lat": 35.6762, "lon": 139.6503, "slug": "tokyo"},
    "Jakarta": {"lat": -6.2088, "lon": 106.8456, "slug": "jakarta"},
    "Manila": {"lat": 14.5995, "lon": 120.9842, "slug": "manila"},
    "Sao Paulo": {"lat": -23.5505, "lon": -46.6333, "slug": "sao_paulo"},
    "Lagos": {"lat": 6.5244, "lon": 3.3792, "slug": "lagos"},
    "Johannesburg": {"lat": -26.2041, "lon": 28.0473, "slug": "johannesburg"},
    "Lusaka": {"lat": -15.3875, "lon": 28.3228, "slug": "lusaka"},
    "Sydney": {"lat": -33.8688, "lon": 151.2093, "slug": "sydney"},
    "Lisbon": {"lat": 38.7223, "lon": -9.1393, "slug": "lisbon"},
    "Honolulu": {"lat": 21.3069, "lon": -157.8583, "slug": "honolulu"},
    "Papeete": {"lat": -17.5516, "lon": -149.5585, "slug": "papeete"},
    "San Francisco": {"lat": 37.7749, "lon": -122.4194, "slug": "san_francisco"},
}

TILE_PROVIDERS = [
    "osm",
    "google",
    "google_satellite",
    "nasa",
]

GOOGLE_MAP_TYPES = {"google": "roadmap", "google_satellite": "satellite"}

# Fixed credit lines for providers that don't supply their own. Google's
# credit depends on the data shown and comes from the Map Tiles API instead.
TILE_ATTRIBUTIONS = {
    "osm": "Map tiles © OpenStreetMap contributors",
    "nasa": "Imagery: NASA Blue Marble, via NASA GIBS (ESDIS)",
}


def _to_rgba(img: Any) -> np.ndarray:
    """Return *img* as an RGBA ``uint8`` array (cached tiles may be RGB)."""
    arr = np.asarray(img, dtype=np.uint8)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.shape[2] == 3:
        arr = np.dstack([arr, np.full(arr.shape[:2], 255, dtype=np.uint8)])
    return arr


class BufferedTileSource:
    """Tile factory that over-fetches near the globe edge and survives failed tiles.

    Cartopy fetches tiles lazily inside ``savefig``. Its own
    ``image_for_domain`` drops tiles whose download raises, and the merged
    image fills those holes with opaque white; if every tile fails it raises
    ``ValueError`` and the save crashes. This factory fetches the tiles
    itself and makes each failed tile fully transparent, so the land/ocean
    fallback drawn underneath shows through. Failures are recorded in
    :attr:`failed_tiles` (``(tile, error)`` pairs) for reporting.

    *postprocess*, if given, turns the merged RGBA mosaic into the image
    drawn: ``postprocess(mosaic, extent) -> RGBA`` (the elevation layer
    colours and shades raw height tiles this way, without seams).
    """

    def __init__(self, tile_source: Any, tile_buffer_factor: float = 0.5,
                 postprocess: Callable[[np.ndarray, Any], np.ndarray] | None = None) -> None:
        self.tile_source = tile_source
        self.tile_buffer_factor = tile_buffer_factor
        self.postprocess = postprocess
        self.crs = tile_source.crs
        self.total_tiles = 0
        self.failed_tiles: list[tuple[Any, BaseException]] = []

    def __getattr__(self, name: str) -> Any:
        if name == "tile_source":  # not set yet (e.g. during copy/unpickling)
            raise AttributeError(name)
        return getattr(self.tile_source, name)

    def image_for_domain(self, target_domain: Any, target_z: int) -> Any:
        x0, x1 = self.crs.x_limits
        tile_width = (x1 - x0) / (2 ** target_z)
        buffered_domain = target_domain.buffer(tile_width * self.tile_buffer_factor)

        source = self.tile_source
        tiles = list(source.find_images(buffered_domain, target_z))
        results: dict[Any, tuple[Any, Any, str]] = {}
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=getattr(source, "_MAX_THREADS", 8)) as executor:
            futures = {executor.submit(source.get_image, tile): tile for tile in tiles}
            for future in concurrent.futures.as_completed(futures):
                tile = futures[future]
                try:
                    results[tile] = future.result()
                except Exception as err:  # one bad tile must not sink the render
                    self.failed_tiles.append((tile, err))
        self.total_tiles += len(tiles)

        size = next((np.asarray(img).shape[:2] for img, _, _ in results.values()), (256, 256))
        pieces = []
        for tile in tiles:
            if tile in results:
                img, extent, origin = results[tile]
                img = _to_rgba(img)
            else:
                img = np.zeros(size + (4,), dtype=np.uint8)  # transparent placeholder
                extent, origin = source.tileextent(tile), "lower"
            x = np.linspace(extent[0], extent[1], img.shape[1])
            y = np.linspace(extent[2], extent[3], img.shape[0])
            pieces.append([img, x, y, origin])
        img, extent, origin = cimgt._merge_tiles(pieces)
        if self.postprocess is not None:
            img = self.postprocess(img, extent)
        return img, extent, origin


def create_tile_source(
    tile_provider: str = "osm",
    tile_buffer_factor: float = 2,
    cache_dir: str | None = None,
    **tile_kwargs: Any,
) -> BufferedTileSource:
    """Create a Cartopy tile source from a simple provider name.

    OSM and NASA tiles are cached under *cache_dir* when one is given.
    Google tiles are never cached. Google providers create a Map Tiles API session here,
    so a missing or invalid API key raises :class:`GoogleTilesError` before
    any rendering.
    """
    provider = tile_provider.lower()

    if provider == "osm":
        tile_source = CachedOSM(cache=cache_dir or False, **tile_kwargs)
    elif provider == "nasa":
        tile_source = CachedNASA(cache=cache_dir or False, **tile_kwargs)
    elif provider in GOOGLE_MAP_TYPES:
        if cache_dir:
            logger.info("Google tiles are not cached (Google Maps Platform terms).")
        tile_source = GoogleMapTiles(map_type=GOOGLE_MAP_TYPES[provider], **tile_kwargs)
    else:
        raise ValueError(
            f"Unsupported tile_provider. Choose one of: {', '.join(TILE_PROVIDERS)}."
        )

    logger.debug("Created tile source: %s (buffer_factor=%.1f)", provider, tile_buffer_factor)
    return BufferedTileSource(tile_source, tile_buffer_factor=tile_buffer_factor)


def tile_attribution_lines(tiles: BufferedTileSource, tile_provider: str, zoom: int) -> list[str]:
    """Return the credit lines the tile provider requires for a render at *zoom*."""
    if isinstance(tiles.tile_source, GoogleMapTiles):
        return tiles.tile_source.attribution_lines(zoom)
    return [TILE_ATTRIBUTIONS[tile_provider.lower()]]


# 20 in × 300 dpi = 6000 px. Map imagery holds only ~2000-4000 px of real
# detail at zoom 3-4 (and is regridded at most at max_regrid_shape), so
# higher DPIs mostly upscale it; they still sharpen text and lines.
DEFAULT_DPI = 300
MIN_DPI = 10
# 20 in × 1200 dpi is already 24 000 px square (~2.3 GB of RGBA in memory)
MAX_DPI = 1200


def validate_render_options(
    dpi: int, koppen_alpha: float, both_hemispheres: bool = False, ice_year: int | None = None,
    elevation_alpha: float = 0.8, soil_alpha: float = 0.6, vegetation_alpha: float = 0.7,
    land_cover_year: int | None = None,
) -> None:
    """Raise ``ValueError`` for options that would only fail after tiles are fetched."""
    if ice_year is not None and not FIRST_ICE_YEAR <= ice_year <= time.localtime().tm_year:
        raise ValueError(
            f"ice_year must be between {FIRST_ICE_YEAR} and this year, got {ice_year}"
        )
    # Two globes double the width, so halve the cap to keep the same pixel budget
    max_dpi = MAX_DPI // 2 if both_hemispheres else MAX_DPI
    if not MIN_DPI <= dpi <= max_dpi:
        layout = " with both hemispheres" if both_hemispheres else ""
        raise ValueError(f"dpi must be between {MIN_DPI} and {max_dpi}{layout}, got {dpi}")
    if not 0.0 <= koppen_alpha <= 1.0:
        raise ValueError(f"koppen_alpha must be between 0 and 1, got {koppen_alpha}")
    if not 0.0 <= elevation_alpha <= 1.0:
        raise ValueError(f"elevation_alpha must be between 0 and 1, got {elevation_alpha}")
    if not 0.0 <= soil_alpha <= 1.0:
        raise ValueError(f"soil_alpha must be between 0 and 1, got {soil_alpha}")
    if not 0.0 <= vegetation_alpha <= 1.0:
        raise ValueError(f"vegetation_alpha must be between 0 and 1, got {vegetation_alpha}")
    if land_cover_year is not None and not FIRST_LAND_COVER_YEAR <= land_cover_year <= LATEST_LAND_COVER_YEAR:
        raise ValueError(f"land_cover_year must be between {FIRST_LAND_COVER_YEAR} and "
                         f"{LATEST_LAND_COVER_YEAR}, got {land_cover_year}")


def generate_orthographic_map(
    lat: float,
    lon: float,
    output_filename: str,
    zoom: int = 3,
    dpi: int = DEFAULT_DPI,
    background_color: str = "#a6d3e0",
    tile_provider: str = "osm",
    tile_kwargs: dict[str, Any] | None = None,
    tile_buffer_factor: float = 2,
    max_regrid_shape: int = 4096,
    output_dir: str | None = None,
    city_name: str | None = None,
    koppen: bool = False,
    koppen_alpha: float = 0.45,
    routes: Sequence[Route] | None = None,
    route_legend: bool = False,
    tile_cache_dir: str | None = None,
    both_hemispheres: bool = False,
    ice: bool = False,
    ice_year: int | None = None,
    crops: Sequence[str] | None = None,
    koppen_classes: Sequence[str] | None = None,
    up: float = 0.0,
    trewartha: bool = False,
    trewartha_classes: Sequence[str] | None = None,
    elevation: bool = False,
    elevation_alpha: float = 0.8,
    soil: bool = False,
    soil_classes: Sequence[str] | None = None,
    soil_alpha: float = 0.6,
    soil_property: str | None = None,
    soil_depth: str | None = None,
    land_cover: bool = False,
    land_cover_classes: Sequence[str] | None = None,
    land_cover_year: int | None = None,
    ndvi: str | None = None,
    vegetation_alpha: float = 0.7,
) -> str:
    """
    Generate an orthographic map projection centered at a specific point.

    Parameters
    ----------
    lat : float
        Central latitude (e.g. 40.7128 for New York).
    lon : float
        Central longitude (e.g. -74.0060 for New York).
    output_filename : str
        Name/path of the output PNG file.
    zoom : int
        Tile zoom level (1–4). Higher values fetch exponentially more tiles.
    dpi : int
        Dots per inch for the output image. 300+ is high resolution.
    background_color : str
        Ocean colour (the fallback ocean under the tiles). The PNG itself is
        saved with a transparent background.
    tile_provider : str
        One of ``"osm"``, ``"google"``, ``"google_satellite"``.
    tile_kwargs : dict, optional
        Extra keyword arguments forwarded to the tile source constructor.
        Google providers accept ``api_key`` (default: ``$GOOGLE_MAPS_API_KEY``),
        ``language`` and ``region``.
    tile_buffer_factor : float
        How aggressively to over-fetch tiles near the globe edge.
    max_regrid_shape : int
        Upper bound on the re-gridding resolution (pixels).
    output_dir : str or None
        Optional directory to save the output file into. Created if needed.
    city_name : str or None
        If provided, a marker and label are drawn at the centre point.
    koppen : bool
        When True, render a Köppen-Geiger climate classification overlay.
    trewartha : bool
        When True, render a Trewartha climate classification overlay instead,
        computed from CHELSA v2.1 (see :mod:`trewartha`). Only one climate
        classification can be drawn; *koppen_alpha* sets its opacity too.
    trewartha_classes : sequence of str, optional
        Show only these Trewartha classes or groups (``"Do"``, ``"C"``, see
        :func:`trewartha.resolve_trewartha_classes`). Implies *trewartha*.
    koppen_alpha : float
        Opacity of the Köppen-Geiger overlay (0–1).
    routes : sequence of Route, optional
        Polylines to draw on the globe, e.g. from :func:`routes.load_routes`.
    route_legend : bool
        When True and *routes* are given, add a key below the globe naming
        each route next to its colour.
    tile_cache_dir : str or None
        Directory for cached OSM tiles (see :func:`configure_tile_cache`).
        ``None`` disables caching. Google tiles are never cached.
    both_hemispheres : bool
        When True, draw a second globe beside the first, centred on the
        antipode, so the whole Earth is shown. Its distance circles are
        measured from (*lat*, *lon*) too. Limits *dpi* to ``MAX_DPI // 2``.
    ice : bool
        When True, draw polar ice: sea ice at its winter maximum (NSIDC
        March extent in the Arctic, September in the Antarctic) plus
        permanent polar land ice. This also covers the plain disc the map
        tiles leave around each pole.
    ice_year : int or None
        Year of the sea ice maxima to show; ``None`` uses the latest published.
    crops : sequence of str, optional
        CROPGRIDS crops to shade by the share of land they cover, each
        ``"wheat"`` or ``"wheat:#f2b705"`` (see :data:`crops.CROP_NAMES`).
        With several, each cell shows the crop with the largest share. A
        key below the globe names them.
    koppen_classes : sequence of str, optional
        Show only these Köppen-Geiger classes or groups (``"Cfb"``, ``"Cs"``,
        ``"C"``, see :func:`koppen.resolve_koppen_classes`). Implies *koppen*.
    up : float
        Compass bearing (degrees clockwise from north) to put at the top of
        the globe; 0 keeps north up, 180 puts south up. Text stays upright.
        With *both_hemispheres*, the far globe turns to match.
    elevation : bool
        When True, colour the land by height and shade its relief (Mapzen
        terrain tiles, see :mod:`elevation`), above the map tiles and below
        every other layer, with a key below the globe.
    elevation_alpha : float
        Opacity of the elevation layer (0–1).
    soil : bool
        When True, colour the land by its most probable soil group
        (SoilGrids 2.0, see :mod:`soil`), above the climate colours, with a key.
    soil_classes : sequence of str, optional
        Show only these soil groups, by name or WRB code (``"Chernozems"``,
        ``"CH"``, see :func:`soil.resolve_soil_classes`). Implies *soil*.
    soil_alpha : float
        Opacity of the soil overlay (0–1), groups or property.
    soil_property : str, optional
        Draw a soil property instead of the groups: one of
        :data:`soil_properties.SOIL_PROPERTIES` (``"ph"``, ``"organic-carbon"``,
        ``"clay"`` …), in classed bands with a key.
    soil_depth : str, optional
        Depth of *soil_property*, one of :data:`soil_properties.DEPTHS`
        (default ``"0-5cm"``; carbon stock is mapped for ``"0-30cm"`` only).
    land_cover : bool
        When True, colour the land by its MODIS land cover class (IGBP, see
        :mod:`vegetation`), above the elevation layer and below the climate
        colours, with a key.
    land_cover_classes : sequence of str, optional
        Show only these classes or groups (``"forest"``, ``"cropland"``,
        ``"evergreen"``, see :func:`vegetation.resolve_land_cover_classes`).
        Implies *land_cover*.
    land_cover_year : int, optional
        Year of the land cover map (default the latest). Implies *land_cover*.
    ndvi : str, optional
        Draw monthly NDVI greenness instead of land cover, for this month:
        ``"2026-07"``, or a month name or number for its latest year.
    vegetation_alpha : float
        Opacity of the land cover or NDVI layer (0–1).

    Returns
    -------
    str
        Absolute path of the saved PNG.
    """

    validate_render_options(
        dpi=dpi, koppen_alpha=koppen_alpha, both_hemispheres=both_hemispheres, ice_year=ice_year,
        elevation_alpha=elevation_alpha, soil_alpha=soil_alpha, vegetation_alpha=vegetation_alpha,
        land_cover_year=land_cover_year,
    )
    land_cover_codes = resolve_land_cover_classes(land_cover_classes or [])
    land_cover = land_cover or bool(land_cover_codes) or land_cover_year is not None
    ndvi_month = resolve_ndvi_month(ndvi) if ndvi else None
    if land_cover and ndvi_month:
        raise ValueError("choose one vegetation layer: land cover or NDVI")
    soil_codes = resolve_soil_classes(soil_classes or [])
    soil = soil or bool(soil_codes)
    prop, prop_depth = resolve_soil_property(soil_property, soil_depth) if soil_property else (None, None)
    if soil_depth and not soil_property:
        raise ValueError("soil_depth needs a soil_property")
    if soil and prop:
        raise ValueError("choose one soil layer: soil groups or a soil property")
    resolve_crops(crops or [])  # unknown names and bad colours fail before any download
    koppen_codes = resolve_koppen_classes(koppen_classes or [])
    koppen = koppen or bool(koppen_codes)
    trewartha_codes = resolve_trewartha_classes(trewartha_classes or [])
    trewartha = trewartha or bool(trewartha_codes)
    if koppen and trewartha:
        raise ValueError("choose one climate classification: Köppen-Geiger or Trewartha")
    if not math.isfinite(up):
        raise ValueError(f"up must be a compass bearing in degrees, got {up}")
    up = normalise_bearing(up)

    # Resolve output path and create its folder now, not after all the work
    if output_dir:
        output_filename = os.path.join(output_dir, output_filename)
    os.makedirs(os.path.dirname(os.path.abspath(output_filename)), exist_ok=True)

    logger.info("Setting up the map centred at lat=%.4f, lon=%.4f", lat, lon)

    # Step 1: Initialize the requested image tile source
    tile_kwargs = tile_kwargs or {}
    tiles = create_tile_source(
        tile_provider=tile_provider,
        tile_buffer_factor=tile_buffer_factor,
        cache_dir=tile_cache_dir,
        **tile_kwargs,
    )

    # Steps 2-4: One full-hemisphere Orthographic globe per centre: the
    # requested point and, with both_hemispheres, its antipode beside it.
    # The Figure is built directly rather than through pyplot, so it is never
    # registered in pyplot's global state and is freed even if rendering fails.
    # Each globe is (lat, lon, bearing at the top); a turned far globe keeps
    # the near globe's top point at its own top.
    if up:
        logger.info("Turning the globe so bearing %.1f° points up", up)
    centres = [(lat, lon, up)]
    if both_hemispheres:
        anti_lat, anti_lon = antipode(lat, lon)
        centres.append((anti_lat, anti_lon, far_side_up(lat, lon, up, anti_lat, anti_lon)))
        logger.info("Adding the opposite hemisphere, centred at lat=%.4f, lon=%.4f", anti_lat, anti_lon)
    fig = Figure(figsize=(20 * len(centres), 20))
    axes = [
        _add_globe_axes(fig, centre_lat, centre_lon, index, len(centres), centre_up)
        for index, (centre_lat, centre_lon, centre_up) in enumerate(centres)
    ]
    near = axes[0]
    far = axes[1] if both_hemispheres else None

    # Keys go under the middle of the row, given in the first globe's axes coordinates
    if far is None:
        key_x = 0.5
    else:
        first = near.get_position()
        key_x = (0.5 - first.x0) / first.width

    # Warp resolution for tiles and overlays: the output's pixel size, capped
    # at max_regrid_shape (beyond that the source imagery has no more detail)
    regrid_shape = min(
        max(750, int(min(fig.get_size_inches()) * dpi)),
        max_regrid_shape,
    )

    for ax in axes:
        # Fallback land/ocean so blank areas aren't white
        ax.add_feature(cfeature.OCEAN, facecolor=background_color, edgecolor="none", zorder=0)
        ax.add_feature(cfeature.LAND, facecolor="#f1efe6", edgecolor="none", zorder=0)

        # Step 5: Register the tiles. Cartopy downloads them later, inside
        # savefig; failed tiles come out transparent (see BufferedTileSource).
        ax.add_image(
            tiles,
            zoom,
            regrid_shape=regrid_shape,
            interpolation="nearest",
        )

    # Step 5a: Elevation, coloured and shaded (above the tiles, below everything else).
    # Its tiles are fetched in savefig too; failed ones leave the map tiles showing.
    relief = None
    if elevation:
        logger.info("Adding elevation (alpha=%.2f) …", elevation_alpha)
        relief = BufferedTileSource(
            TerrariumTiles(cache_dir=tile_cache_dir),
            tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(relief_rgba, alpha=elevation_alpha, cache_dir=tile_cache_dir),
        )
        for ax in axes:
            ax.add_image(relief, RELIEF_ZOOM, regrid_shape=regrid_shape, interpolation="bilinear", zorder=1)

    # Step 5a2: Vegetation, land cover or NDVI (above the elevation, below the climate
    # colours); NASA GIBS tiles, fetched in savefig like the map tiles
    vegetation = None
    vegetation_credit = None
    if land_cover:
        year = land_cover_year or LATEST_LAND_COVER_YEAR
        logger.info("Adding MODIS land cover %d (alpha=%.2f) …", year, vegetation_alpha)
        vegetation = BufferedTileSource(
            land_cover_tiles(year, tile_cache_dir), tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(land_cover_rgba, alpha=vegetation_alpha,
                                          classes=land_cover_codes or None),
        )
        vegetation_credit = land_cover_attribution(year)
    elif ndvi_month:
        logger.info("Adding MODIS NDVI for %d-%02d (alpha=%.2f) …", *ndvi_month, vegetation_alpha)
        vegetation = BufferedTileSource(
            ndvi_tiles(*ndvi_month, tile_cache_dir), tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(ndvi_rgba, alpha=vegetation_alpha),
        )
        vegetation_credit = ndvi_attribution(*ndvi_month)
    if vegetation is not None:
        for ax in axes:
            ax.add_image(vegetation, VEGETATION_ZOOM, regrid_shape=regrid_shape,
                         interpolation="nearest", zorder=2)

    # Step 5b: Climate overlay, Köppen-Geiger or Trewartha (above tiles, below
    # gridlines), with one key; its credit lines are kept for step 7e
    climate_credits: list[str] = []
    if koppen:
        logger.info("Applying Köppen-Geiger climate overlay (alpha=%.2f) …", koppen_alpha)
        try:
            for ax in axes:
                add_koppen_overlay(ax, alpha=koppen_alpha, regrid_shape=regrid_shape,
                                   classes=koppen_codes or None)
        except (KoppenDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping Köppen-Geiger overlay: %s", e)
        else:
            add_koppen_legend(near, x=key_x, classes=koppen_codes or None)
            climate_credits = [KOPPEN_ATTRIBUTION]
    elif trewartha:
        logger.info("Applying Trewartha climate overlay (alpha=%.2f) …", koppen_alpha)
        try:
            for ax in axes:
                add_trewartha_overlay(ax, alpha=koppen_alpha, regrid_shape=regrid_shape,
                                      classes=trewartha_codes or None)
        except (TrewarthaDataError, OSError) as e:
            logger.warning("Skipping Trewartha overlay: %s", e)
        else:
            add_trewartha_legend(near, x=key_x, classes=trewartha_codes or None)
            # Its highland group uses the terrain tiles' heights
            climate_credits = [TREWARTHA_ATTRIBUTION, ELEVATION_ATTRIBUTION]

    # Step 5b2: Soil groups or a soil property (above the climate colours, below crops)
    soil_drawn = False
    property_drawn = False
    if prop is not None and prop_depth is not None:
        logger.info("Applying soil %s overlay at %s (alpha=%.2f) …", prop.code, prop_depth, soil_alpha)
        try:
            for ax in axes:
                add_soil_property_overlay(ax, prop, prop_depth, alpha=soil_alpha, regrid_shape=regrid_shape)
        except (SoilPropertyError, OSError) as e:
            logger.warning("Skipping soil property overlay: %s", e)
        else:
            property_drawn = True
    if soil:
        logger.info("Applying soil overlay (alpha=%.2f) …", soil_alpha)
        try:
            for ax in axes:
                add_soil_overlay(ax, alpha=soil_alpha, regrid_shape=regrid_shape,
                                 classes=soil_codes or None)
        except (SoilDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping soil overlay: %s", e)
        else:
            soil_drawn = True

    # Step 5c: Crop areas (above climate colours, below ice)
    crop_layer = None
    if crops:
        logger.info("Adding crop areas: %s …", ", ".join(crop_label(c.split(":")[0]) for c in crops))
        try:
            crop_layer = load_crop_layer(crops)
        except (CropDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping crop areas: %s", e)
        else:
            for ax in axes:
                draw_crops(ax, crop_layer, regrid_shape=regrid_shape)

    # Step 5d: Polar ice at its winter maximum (above tiles, climate and crop colours)
    ice_attribution = None
    if ice:
        logger.info("Adding polar ice at its winter maximum …")
        try:
            ice_layers = load_ice_layers(year=ice_year)
        except (IceDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping polar ice: %s", e)
        else:
            for ax in axes:
                draw_ice(ax, ice_layers)
            ice_attribution = ice_layers.attribution

    # Step 6: Gridlines
    for ax in axes:
        ax.gridlines(draw_labels=False, color='black', alpha=0.3, linestyle='--')

    # Step 7: City marker & label, and the antipode on the far globe
    if city_name:
        near.plot(
            lon, lat,
            marker="o", markersize=10, markeredgewidth=2,
            color="#e74c3c", markeredgecolor="white",
            transform=ccrs.PlateCarree(), zorder=10,
        )
        near.text(
            lon, lat, f"  {city_name}",
            transform=ccrs.PlateCarree(),
            fontsize=14, fontweight="bold", color="white",
            va="center", ha="left", zorder=10,
            path_effects=[
                pe.withStroke(linewidth=3, foreground="black")
            ],
        )
    if far is not None:
        anti_lat, anti_lon, _ = centres[1]
        far.plot(
            anti_lon, anti_lat,
            marker="o", markersize=10, markeredgewidth=2.5,
            markerfacecolor="none", markeredgecolor="white",
            transform=ccrs.PlateCarree(), zorder=10,
        )
        far.text(
            anti_lon, anti_lat, f"  Antipode of {city_name}" if city_name else "  Antipode",
            transform=ccrs.PlateCarree(),
            fontsize=14, fontweight="bold", color="white",
            va="center", ha="left", zorder=10,
            path_effects=[pe.withStroke(linewidth=3, foreground="black")],
        )

    # Step 7b: Concentric distance circles, all measured from the requested
    # point: 2 500 and 5 000 km on its globe, 17 500 and 15 000 km on the far one
    _draw_distance_circles(near, lon, lat)
    if far is not None:
        _draw_distance_circles(far, lon, lat, radii_km=FAR_SIDE_RADII_KM)

    # Step 7c: Route overlays
    if routes:
        for ax in axes:
            draw_routes(ax, routes)

    # Step 7d: Keys below the globe(s): the elevation, soil, crop and route
    # keys, under the climate key when that is drawn too
    keys = []
    if relief is not None:
        keys.append(add_elevation_legend(near, x=key_x))
    if land_cover:
        keys.append(add_land_cover_legend(near, land_cover_year or LATEST_LAND_COVER_YEAR,
                                          classes=land_cover_codes or None, x=key_x))
    elif ndvi_month:
        keys.append(add_ndvi_legend(near, *ndvi_month, x=key_x))
    if soil_drawn:
        keys.append(add_soil_legend(near, x=key_x, classes=soil_codes or None))
    if property_drawn and prop is not None and prop_depth is not None:
        keys.append(add_soil_property_legend(near, prop, prop_depth, x=key_x))
    if crop_layer is not None:
        keys.append(add_crop_legend(near, crop_layer, x=key_x))
    if routes and route_legend:
        keys.append(add_route_legend(near, routes, x=key_x))
    _stack_keys(near, [k for k in keys if k is not None], top=-0.07 if climate_credits else -0.01, x=key_x)

    # Step 7e: Data credits required by the tile and dataset licences
    credits = tile_attribution_lines(tiles, tile_provider, zoom)
    if vegetation_credit:
        credits.append(vegetation_credit)
    for credit in climate_credits + ([ELEVATION_ATTRIBUTION] if relief is not None else []):
        if credit not in credits:
            credits.append(credit)
    if soil_drawn:
        credits.append(SOIL_ATTRIBUTION)
    if property_drawn:
        credits.append(SOIL_PROPERTY_ATTRIBUTION)
    if crop_layer is not None:
        credits.append(CROP_ATTRIBUTION)
    if ice_attribution:
        credits.append(ice_attribution)
    _add_attribution(axes[-1], credits)

    # Step 8: Export
    logger.info("Fetching '%s' tiles at zoom %d and saving to '%s' at %d DPI …",
                tile_provider, zoom, output_filename, dpi)
    fig.savefig(output_filename, dpi=dpi, bbox_inches="tight", transparent=True)
    _report_tile_failures(tiles)
    if vegetation is not None and vegetation.failed_tiles:
        logger.warning("%d of %d vegetation tiles could not be downloaded (first error: %s).",
                       len(vegetation.failed_tiles), vegetation.total_tiles, vegetation.failed_tiles[0][1])
    if relief is not None and relief.failed_tiles:
        logger.warning("%d of %d terrain tiles could not be downloaded (first error: %s); "
                       "those areas have no elevation colours.", len(relief.failed_tiles),
                       relief.total_tiles, relief.failed_tiles[0][1])

    output_path = os.path.abspath(output_filename)
    logger.info("Map successfully created: %s", output_path)
    return output_path


def antipode(lat: float, lon: float) -> tuple[float, float]:
    """Return the point diametrically opposite (*lat*, *lon*), lon in [-180, 180]."""
    return -lat, lon + 180 if lon <= 0 else lon - 180


# Side-by-side globes are each the size of a single render's globe (the
# default subplot height of a 20 in figure), with a 1 in gap between them.
_GLOBE_SIZE_IN = 0.77 * 20
_GLOBE_GAP_IN = 1.0


def _add_globe_axes(
    fig: Figure, lat: float, lon: float, index: int, count: int, up: float = 0.0,
) -> GeoAxes:
    """Add globe *index* of *count* to *fig*, centred on (*lat*, *lon*), bearing *up* at the top."""
    proj = globe_projection(lat, lon, up)
    if count == 1:
        ax = fig.add_subplot(projection=proj)
    else:
        # A centred row of equal square globes
        fig_w, fig_h = fig.get_size_inches()
        row_w = count * _GLOBE_SIZE_IN + (count - 1) * _GLOBE_GAP_IN
        left = (fig_w - row_w) / 2 + index * (_GLOBE_SIZE_IN + _GLOBE_GAP_IN)
        ax = fig.add_axes(
            (left / fig_w, 0.11, _GLOBE_SIZE_IN / fig_w, _GLOBE_SIZE_IN / fig_h),
            projection=proj,
        )
    if not isinstance(ax, GeoAxes):
        raise RuntimeError("Failed to create GeoAxes")
    ax.set_global()  # full hemisphere view
    return ax



# Vertical gap between stacked keys, in axes coordinates
_KEY_GAP = 0.008


def _stack_keys(ax: GeoAxes, keys: Sequence[Any], top: float, x: float = 0.5) -> None:
    """Place *keys* below *ax*, the first with its top edge at *top*, each under the last.

    Positions are axes coordinates; each key's height is measured once drawn,
    so keys of any size stack without overlapping.
    """
    y = top
    axes_height = ax.get_window_extent().height
    for key in keys:
        key.set_bbox_to_anchor((x, y), transform=ax.transAxes)
        y -= key.get_window_extent().height / axes_height + _KEY_GAP


def _report_tile_failures(tiles: BufferedTileSource) -> None:
    """Log how many tiles failed to download during the render, if any."""
    failed, total = len(tiles.failed_tiles), tiles.total_tiles
    if not failed:
        return
    first_error = tiles.failed_tiles[0][1]
    if failed == total:
        logger.warning(
            "No map tiles could be downloaded (%s). The map was saved with the "
            "fallback land/ocean features only.", first_error,
        )
    else:
        logger.warning(
            "%d of %d map tiles could not be downloaded (first error: %s). Those "
            "areas show the fallback land/ocean features.", failed, total, first_error,
        )


def _add_attribution(ax: GeoAxes, credits: Sequence[str]) -> None:
    """Draw *credits*, one per line, in the bottom-right corner of *ax*.

    That corner lies outside the globe disc, so the text never covers map
    content. ``clip_on=False`` stops GeoAxes clipping it to the globe.
    """
    ax.text(
        1.0, 0.0, "\n".join(credits),
        transform=ax.transAxes,
        ha="right", va="bottom", multialignment="right",
        fontsize=8, color="#888888", style="italic",
        clip_on=False, zorder=10,
    )


def _geodesic_circle(lon: float, lat: float, radius_m: float, n_points: int = 180) -> Polygon:
    """Return a Shapely Polygon tracing a geodesic circle on WGS-84.

    Parameters
    ----------
    lon, lat : float
        Centre of the circle in degrees.
    radius_m : float
        Radius in **metres**.
    n_points : int
        Number of vertices (more = smoother).
    """
    geod = Geodesic()
    coords = geod.circle(lon=lon, lat=lat, radius=radius_m, n_samples=n_points, endpoint=False)
    return Polygon(coords)


def _visible_runs(xy: np.ndarray) -> list[np.ndarray]:
    """Split a closed ring of projected vertices into its visible stretches.

    Vertices on the far side of the globe are non-finite in Orthographic.
    Dropping them and drawing the rest as one line would join the two ends
    of the gap with a straight chord across the globe, so each contiguous
    visible stretch becomes its own run instead. A fully visible ring is
    returned whole (still closed).
    """
    finite = np.asarray(np.isfinite(xy).all(axis=1), dtype=bool)
    if finite.all():
        return [xy]
    # Drop the closing duplicate and rotate so the array starts inside a
    # gap; then no visible stretch is split by the array's start/end seam.
    pts, fin = xy[:-1], finite[:-1]
    start = int(np.argmin(fin))
    pts, fin = np.roll(pts, -start, axis=0), np.roll(fin, -start)

    runs, current = [], []
    for point, ok in zip(pts, fin):
        if ok:
            current.append(point)
        elif current:
            runs.append(np.array(current))
            current = []
    if current:
        runs.append(np.array(current))
    return [run for run in runs if len(run) >= 2]


# Distance circles on the far globe, still measured from the requested point.
# The antipode is ~20 000 km away, so these sit 2 500 and 5 000 km from it,
# mirroring the near globe's rings. Inner ring first, as on the near globe.
FAR_SIDE_RADII_KM = (17_500, 15_000)


def _draw_distance_circles(
    ax: GeoAxes,
    lon: float,
    lat: float,
    radii_km: tuple[float, ...] = (2_500, 5_000),
) -> None:
    """Draw concentric geodesic circles on *ax* at the given radii.

    The ring vertices are projected straight into the axes' own projection
    and drawn as a closed line there. Going through lon/lat polygons breaks
    whenever a circle encloses a pole: the ring then spans all 360° of
    longitude and has to cross the CRS seam, producing a spurious edge.
    """
    proj = ax.projection
    colors = ["#ffffff", "#ffffff"]
    alphas = [0.7, 0.5]

    for idx, radius_km in enumerate(radii_km):
        circle_poly = _geodesic_circle(lon, lat, radius_km * 1_000)
        ring = np.array(circle_poly.exterior.coords)

        xyz = proj.transform_points(ccrs.Geodetic(), ring[:, 0], ring[:, 1])
        runs = _visible_runs(xyz[:, :2])
        if not runs:
            continue

        for run in runs:
            ax.plot(
                run[:, 0], run[:, 1],
                transform=proj,
                color=colors[idx % len(colors)],
                linewidth=1.4,
                linestyle="--",
                alpha=alphas[idx % len(alphas)],
                zorder=9,
            )
        # Place a small label at the top of the circle as drawn on the map
        visible = np.vstack(runs)
        label_x, label_y = visible[int(np.argmax(visible[:, 1]))]
        ax.text(
            label_x, label_y, f" {int(radius_km):,} km",
            transform=proj,
            fontsize=9, color="white", alpha=alphas[idx % len(alphas)],
            fontweight="bold", va="bottom", ha="center", zorder=10,
            path_effects=[pe.withStroke(linewidth=2, foreground="black")],
        )


def prompt_for_selection(prompt_text: str, options: list[str]) -> str:
    """Prompt the user to choose one option from a numbered list."""
    while True:
        print(prompt_text)
        for index, option in enumerate(options, start=1):
            print(f"  {index}. {option}")

        choice = input("Enter the number of your choice: ").strip()
        if not choice.isdigit():
            print("Please enter a valid number.\n")
            continue

        selected_index = int(choice) - 1
        if 0 <= selected_index < len(options):
            return options[selected_index]

        print("Choice out of range. Try again.\n")


def prompt_for_zoom(default_zoom: int = 3, min_zoom: int = 1, max_zoom: int = 4) -> int:
    """Prompt the user for a zoom level within a safe range."""
    while True:
        raw_zoom = input(
            f"Enter zoom level ({min_zoom}-{max_zoom}) [default: {default_zoom}]: "
        ).strip()

        if not raw_zoom:
            return default_zoom

        if raw_zoom.isdigit():
            zoom = int(raw_zoom)
            if min_zoom <= zoom <= max_zoom:
                return zoom

        print(f"Please enter an integer between {min_zoom} and {max_zoom}.\n")


def build_output_filename(
    city_slug: str, tile_provider: str, zoom: int, both_hemispheres: bool = False, up: float = 0.0,
) -> str:
    """Build a descriptive output filename from the render parameters."""
    sanitized_provider = tile_provider.replace(" ", "_")
    suffix = "_hemispheres" if both_hemispheres else ""
    up = normalise_bearing(up)
    if up:
        suffix += f"_up{round(up) % 360}"
    return f"orthographic_map_{city_slug}_{sanitized_provider}_z{zoom}{suffix}.png"


# --- Custom coordinate prompt ---


def prompt_for_coordinates() -> tuple[float, float]:
    """Prompt the user for custom latitude and longitude."""
    while True:
        try:
            lat = float(input("Enter latitude (-90 to 90): ").strip())
            lon = float(input("Enter longitude (-180 to 180): ").strip())
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
            print("Values out of range.\n")
        except ValueError:
            print("Please enter valid numbers.\n")


def prompt_for_google_api_key() -> str:
    """Return the Google Maps API key from the environment, or ask for it (hidden input)."""
    try:
        return resolve_api_key()
    except GoogleTilesError:
        pass
    print(f"Google tiles need a Google Maps Platform API key ({' / '.join(API_KEY_ENVS)} not set).")
    while True:
        key = getpass.getpass("Google Maps API key (input hidden): ").strip()
        if key:
            return key
        print("A key is required for Google tiles.\n")


def resolve_place(place: str) -> tuple[float, float]:
    """(lat, lon) of a pre-defined city name (any case) or a "LAT,LON" string.

    Raises ``ValueError`` if *place* is neither.
    """
    for name, city in MAJOR_METROPOLISES.items():
        if name.lower() == place.strip().lower():
            return city["lat"], city["lon"]
    try:
        lat_text, lon_text = place.split(",")
        lat, lon = float(lat_text), float(lon_text)
    except ValueError:
        raise ValueError(
            f"{place!r} is not a known city or LAT,LON. Cities: {', '.join(MAJOR_METROPOLISES)}"
        ) from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{place!r} is out of range (latitude -90 to 90, longitude -180 to 180)")
    return lat, lon


def prompt_for_up() -> float:
    """Prompt for the compass bearing to put at the top; blank keeps north up."""
    while True:
        raw = input("Compass bearing to put at the top (0 = north, 180 = south) [default: 0]: ").strip()
        if not raw:
            return 0.0
        try:
            up = float(raw)
        except ValueError:
            up = math.nan
        if math.isfinite(up):
            return normalise_bearing(up)
        print("Please enter a number of degrees.\n")


def prompt_for_koppen_classes() -> list[str]:
    """Prompt for Köppen-Geiger classes or groups to show (comma-separated); blank shows all."""
    return prompt_for_climate_classes(resolve_koppen_classes, "e.g. Cfb, Cs")


def prompt_for_climate_classes(resolve: Any, example: str, what: str = "Climate classes") -> list[str]:
    """Prompt for classes to show (*what*), checked with *resolve*; blank shows all."""
    while True:
        raw = input(f"{what} to show, comma-separated ({example}) [blank for all]: ").strip()
        specs = [part.strip() for part in raw.split(",") if part.strip()]
        try:
            resolve(specs)
        except ValueError as e:
            print(f"{e}\n")
            continue
        return specs


def print_crop_names() -> None:
    """Print the CROPGRIDS crop names, several per line."""
    print(f"{len(CROP_NAMES)} crops (\"nes\" = not elsewhere specified, \"for\" = fodder):")
    for start in range(0, len(CROP_NAMES), 8):
        print("  " + "  ".join(CROP_NAMES[start:start + 8]))


def prompt_for_crops() -> list[str]:
    """Prompt for crops to shade (comma-separated); blank skips."""
    while True:
        raw = input("Crops to shade, comma-separated (e.g. wheat, rice) [blank for none, ? to list]: ").strip()
        if raw == "?":
            print_crop_names()
            continue
        specs = [part.strip() for part in raw.split(",") if part.strip()]
        try:
            resolve_crops(specs)
        except ValueError as e:
            print(f"{e}\n")
            continue
        return specs


def prompt_for_routes() -> list[Route]:
    """Prompt for an optional GeoJSON route file; blank skips."""
    while True:
        path = input("Route GeoJSON file to overlay [blank for none]: ").strip().strip('"')
        if not path:
            return []
        try:
            return load_routes(path)
        except (OSError, ValueError) as e:
            print(f"Could not load route: {e}\n")


# --- CLI argument parser ---


def build_cli_parser() -> argparse.ArgumentParser:
    """Build and return the argparse parser, its options grouped by topic for ``--help``."""
    parser = argparse.ArgumentParser(
        description="Generate high-resolution orthographic globe maps.",
        epilog="Run without arguments for interactive mode. Settings can also come from "
               "a recipe file (--config); options given on the command line override it.",
    )

    where = parser.add_argument_group("Location", "Where the globe is centred.")
    location = where.add_mutually_exclusive_group()
    location.add_argument(
        "--city",
        type=str.lower,
        choices=[k.lower() for k in MAJOR_METROPOLISES],
        metavar="CITY",
        help=f"Pre-defined city ({', '.join(MAJOR_METROPOLISES.keys())})",
    )
    location.add_argument(
        "--lat",
        type=float,
        help="Custom latitude (-90 to 90). Must be used with --lon.",
    )
    where.add_argument(
        "--lon",
        type=float,
        help="Custom longitude (-180 to 180). Must be used with --lat.",
    )

    imagery = parser.add_argument_group("Imagery and output")
    imagery.add_argument(
        "--provider",
        choices=TILE_PROVIDERS,
        default="osm",
        help="Tile provider (default: osm)",
    )
    imagery.add_argument(
        "--zoom",
        type=int,
        default=3,
        choices=range(1, 5),
        metavar="ZOOM",
        help="Tile zoom level 1-4 (default: 3)",
    )
    imagery.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Output DPI, {MIN_DPI}-{MAX_DPI} (default: {DEFAULT_DPI})",
    )
    imagery.add_argument(
        "-o", "--output",
        help="Explicit output filepath (overrides auto-naming)",
    )
    imagery.add_argument(
        "--output-dir",
        help="Directory to save auto-named files into (default: current dir)",
    )
    imagery.add_argument(
        "--cache-dir",
        default=None,
        help=f"OSM and NASA tile cache directory (default: {DEFAULT_CACHE_DIR}). "
             "Google tiles are never cached.",
    )
    imagery.add_argument(
        "--no-cache",
        action="store_true",
        help="Download OSM and NASA tiles fresh instead of using the tile cache.",
    )

    layout = parser.add_argument_group("Globe layout")
    layout.add_argument(
        "--both-hemispheres",
        action="store_true",
        help="Draw a second globe centred on the antipode, showing the whole Earth "
             f"(max --dpi {MAX_DPI // 2}).",
    )
    orientation = layout.add_mutually_exclusive_group()
    orientation.add_argument(
        "--up",
        type=float,
        default=0.0,
        metavar="BEARING",
        help="Compass bearing to put at the top, in degrees clockwise from north "
             "(default: 0; 180 = south up).",
    )
    orientation.add_argument(
        "--up-toward",
        metavar="PLACE",
        help="Put the direction toward PLACE at the top: a city name or LAT,LON.",
    )

    climate = parser.add_argument_group(
        "Climate (Köppen-Geiger or Trewartha)", "One climate classification at a time.")
    system = climate.add_mutually_exclusive_group()
    system.add_argument(
        "--koppen",
        action="store_true",
        default=False,
        help="Enable Köppen-Geiger climate classification overlay.",
    )
    system.add_argument(
        "--trewartha",
        action="store_true",
        help="Enable the Trewartha climate classification overlay, computed from CHELSA v2.1 "
             "(one-time download of ~52 MB).",
    )
    climate.add_argument(
        "--climate-alpha", "--koppen-alpha",
        dest="koppen_alpha",
        type=float,
        default=0.45,
        metavar="ALPHA",
        help="Opacity of the climate overlay (0-1, default: 0.45).",
    )
    climate.add_argument(
        "--koppen-class",
        action="append",
        default=None,
        metavar="CLASS",
        help="Show only this Köppen-Geiger class (e.g. Cfb) or group (e.g. C, Cs); "
             "repeat for several. Implies --koppen.",
    )
    climate.add_argument(
        "--trewartha-class",
        action="append",
        default=None,
        metavar="CLASS",
        help="Show only this Trewartha class (e.g. Do) or group (e.g. C); "
             "repeat for several. Implies --trewartha.",
    )

    relief = parser.add_argument_group("Elevation")
    relief.add_argument(
        "--elevation",
        action="store_true",
        help="Colour the land by height and shade its relief (Mapzen terrain tiles), with a key.",
    )
    relief.add_argument(
        "--elevation-alpha",
        type=float,
        default=0.8,
        metavar="ALPHA",
        help="Opacity of the elevation layer (0-1, default: 0.8).",
    )

    veg = parser.add_argument_group("Vegetation (MODIS, via NASA GIBS)", "Land cover or NDVI, one at a time.")
    veg.add_argument(
        "--landcover",
        action="store_true",
        help=f"Colour the land by its MODIS land cover class (17 IGBP classes, {FIRST_LAND_COVER_YEAR}-"
             f"{LATEST_LAND_COVER_YEAR}), with a key.",
    )
    veg.add_argument(
        "--landcover-class",
        action="append",
        default=None,
        metavar="CLASS",
        help="Show only this class or group: forest, shrubland, savanna, grassland, wetland, cropland, "
             "urban, ice, barren, or the start of a class name (e.g. evergreen); repeat for several. "
             "Implies --landcover.",
    )
    veg.add_argument(
        "--landcover-year",
        type=int,
        default=None,
        metavar="YEAR",
        help=f"Year of the land cover ({FIRST_LAND_COVER_YEAR}-{LATEST_LAND_COVER_YEAR}; "
             f"default: {LATEST_LAND_COVER_YEAR}). Implies --landcover.",
    )
    veg.add_argument(
        "--ndvi",
        metavar="MONTH",
        help="Draw MODIS vegetation greenness (NDVI) for a month: YYYY-MM, or a month name or "
             "number for its latest year (e.g. january, 7).",
    )
    veg.add_argument(
        "--vegetation-alpha",
        type=float,
        default=0.7,
        metavar="ALPHA",
        help="Opacity of the land cover or NDVI layer (0-1, default: 0.7).",
    )

    soils = parser.add_argument_group("Soil (SoilGrids)")
    soils.add_argument(
        "--soil",
        action="store_true",
        help="Colour the land by its most probable soil group (WRB, SoilGrids 2.0; "
             "one-time download of ~220 MB).",
    )
    soils.add_argument(
        "--soil-class",
        action="append",
        default=None,
        metavar="GROUP",
        help="Show only this soil group, by name or WRB code (e.g. Chernozems or CH); "
             "repeat for several. Implies --soil.",
    )
    soils.add_argument(
        "--soil-alpha",
        type=float,
        default=0.6,
        metavar="ALPHA",
        help="Opacity of the soil overlay (0-1, default: 0.6).",
    )
    soils.add_argument(
        "--soil-property",
        choices=list(SOIL_PROPERTIES),
        metavar="PROPERTY",
        help="Draw a soil property instead of the groups (SoilGrids, 5 km; ~4 MB each): "
             + ", ".join(SOIL_PROPERTIES) + ".",
    )
    soils.add_argument(
        "--soil-depth",
        choices=list(SOIL_DEPTHS) + ["0-30cm"],
        metavar="DEPTH",
        help="Depth of --soil-property: " + ", ".join(SOIL_DEPTHS)
             + " (default: 0-5cm; carbon-stock is 0-30cm only).",
    )

    ice = parser.add_argument_group("Polar ice")
    ice.add_argument(
        "--ice",
        action="store_true",
        help="Draw polar ice: sea ice at its winter maximum (NSIDC) and polar land ice.",
    )
    ice.add_argument(
        "--ice-year",
        type=int,
        default=None,
        metavar="YEAR",
        help=f"Year of the sea ice maxima ({FIRST_ICE_YEAR} on; default: latest published). "
             "Implies --ice.",
    )

    crops = parser.add_argument_group("Crops (CROPGRIDS)")
    crops.add_argument(
        "--crop",
        action="append",
        default=None,
        metavar="NAME[:COLOUR]",
        help="Shade where a crop is grown (CROPGRIDS, c. 2020), e.g. wheat or wheat:#f2b705. "
             "Repeat for several crops; each place then shows the one with the largest share.",
    )
    crops.add_argument(
        "--list-crops",
        action="store_true",
        help="List the crop names --crop accepts, then exit.",
    )

    routes = parser.add_argument_group("Routes and areas")
    routes.add_argument(
        "--route",
        action="append",
        default=None,
        metavar="GEOJSON",
        help="GeoJSON file of routes and areas to draw. Repeat for multiple files.",
    )
    routes.add_argument(
        "--route-legend",
        action="store_true",
        help="Add a key below the globe naming each route next to its colour.",
    )

    recipes = parser.add_argument_group("Recipes")
    recipes.add_argument(
        "--config",
        metavar="FILE",
        help="Read settings from a TOML recipe file; options on the command line override it.",
    )

    return parser


# Options a recipe file cannot set: they are about the run, not the map
_NOT_IN_RECIPES = {"config", "list_crops", "help"}
# Recipe keys holding input files, resolved relative to the recipe file
_RECIPE_PATH_KEYS = {"route"}


def _option_actions(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """Each option's action by every long name without dashes, e.g. ``"route-legend"``."""
    actions = {}
    for action in parser._actions:  # argparse has no public list of its actions
        if action.dest in _NOT_IN_RECIPES:
            continue
        for name in action.option_strings:
            if name.startswith("--"):
                actions[name[2:]] = action
    return actions


def _given_dests(parser: argparse.ArgumentParser, argv: Sequence[str]) -> set[str]:
    """Dests of the options named in *argv*, including abbreviated long options."""
    option_strings = parser._option_string_actions  # argparse's own name → action map
    given = set()
    for token in argv:
        if token == "--":
            break
        if not token.startswith("-") or token == "-":
            continue
        name = token.split("=", 1)[0]
        action = option_strings.get(name)
        if action is None and name.startswith("--"):
            matches = {a for s, a in option_strings.items() if s.startswith(name)}
            action = matches.pop() if len(matches) == 1 else None
        if action is not None:
            given.add(action.dest)
    return given


def _recipe_tokens(
    parser: argparse.ArgumentParser, path: str, cli_argv: Sequence[str],
) -> list[str]:
    """Turn the TOML recipe at *path* into command-line tokens for *parser*.

    Keys are option names (``provider``, ``route-legend`` or ``route_legend``).
    Lists repeat an option; ``true`` turns a flag on. When the command line
    names an option of a mutually exclusive pair (``--lat``/``--lon`` against
    ``city``, ``--up-toward`` against ``up``), the recipe's side is dropped.
    Errors exit through ``parser.error`` like any bad command line.
    """
    try:
        with open(path, "rb") as fh:
            recipe = tomllib.load(fh)
    except OSError as e:
        parser.error(f"cannot read recipe {path}: {e.strerror or e}")
    except tomllib.TOMLDecodeError as e:
        parser.error(f"recipe {path} is not valid TOML: {e}")

    actions = _option_actions(parser)
    given = _given_dests(parser, cli_argv)
    # Location counts as one choice: city, or lat with lon
    overridden = {"city", "lat", "lon"} if given & {"city", "lat", "lon"} else set()
    for group in parser._mutually_exclusive_groups:
        dests = {a.dest for a in group._group_actions}
        if dests & given:
            overridden |= dests

    base = os.path.dirname(os.path.abspath(path))
    tokens: list[str] = []
    for key, value in recipe.items():
        name = key.replace("_", "-")
        action = actions.get(name)
        if action is None:
            close = difflib.get_close_matches(name, actions, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            parser.error(f"recipe {path}: unknown option {key!r}.{hint}")
        if action.dest in overridden:
            continue
        option = f"--{name}"
        if action.nargs == 0:  # a flag such as --ice
            if not isinstance(value, bool):
                parser.error(f"recipe {path}: {key} must be true or false")
            if value:
                tokens.append(option)
            continue
        values = value if isinstance(value, list) else [value]
        if len(values) > 1 and not isinstance(action, argparse._AppendAction):
            parser.error(f"recipe {path}: {key} takes a single value, not a list")
        for item in values:
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                parser.error(f"recipe {path}: {key} must be text or a number, got {item!r}")
            if action.dest in _RECIPE_PATH_KEYS:
                item = os.path.normpath(os.path.join(base, item))
            tokens.append(f"{option}={item}")  # "=" keeps values like -33.9 attached
    return tokens


def parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse *argv* (default ``sys.argv[1:]``), reading a ``--config`` recipe first.

    The recipe's settings come first, so the command line overrides them;
    repeatable options (``--route``, ``--crop``, ``--koppen-class``) add to
    the recipe's lists.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_cli_parser()
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    config_path = pre.parse_known_args(argv)[0].config
    if not config_path:
        return parser.parse_args(argv)
    args = parser.parse_args(_recipe_tokens(parser, config_path, argv) + argv)
    logger.info("Using recipe %s", config_path)
    return args


def load_route_files(paths: Sequence[str]) -> list[Route]:
    """Load and concatenate the routes from every file in *paths*."""
    return [route for path in paths for route in load_routes(path)]


# --- Entry point modes ---


def run_interactive(tile_cache_dir: str | None = None) -> None:
    """Interactive prompt flow with custom coordinate support."""
    city_options = ["[Custom coordinates]"] + list(MAJOR_METROPOLISES.keys())
    selection = prompt_for_selection(
        "Choose a location to center the orthographic map on:",
        city_options,
    )

    if selection == "[Custom coordinates]":
        lat, lon = prompt_for_coordinates()
        city_slug = "custom"
        city_label = f"({lat}, {lon})"
    else:
        city = MAJOR_METROPOLISES[selection]
        lat, lon = city["lat"], city["lon"]
        city_slug = city["slug"]
        city_label = selection

    tile_provider = prompt_for_selection(
        "Choose a tile provider:",
        TILE_PROVIDERS,
    )
    tile_kwargs = {}
    if tile_provider in GOOGLE_MAP_TYPES:
        tile_kwargs["api_key"] = prompt_for_google_api_key()
    zoom = prompt_for_zoom(default_zoom=3)
    both_hemispheres = input(
        "Also draw the opposite hemisphere, centred on the antipode? [y/N]: "
    ).strip().lower() in ("y", "yes")
    up = prompt_for_up()
    output_file = build_output_filename(city_slug, tile_provider, zoom, both_hemispheres, up)

    print(
        f"\nGenerating map for {city_label} using '{tile_provider}' at zoom level {zoom}."
    )
    print(f"Output file: {output_file}\n")

    # Climate overlay prompt: Köppen-Geiger, Trewartha or none
    climate_input = input(
        "Climate overlay: [k]öppen-Geiger, [t]rewartha, or blank for none: "
    ).strip().lower()
    enable_koppen = climate_input in ("k", "koppen", "köppen", "y", "yes")
    enable_trewartha = climate_input in ("t", "trewartha")

    koppen_alpha = 0.45
    koppen_classes: list[str] = []
    trewartha_classes: list[str] = []
    if enable_koppen or enable_trewartha:
        raw_alpha = input("Climate overlay opacity (0-1) [default: 0.45]: ").strip()
        if raw_alpha:
            try:
                val = float(raw_alpha)
                if 0.0 <= val <= 1.0:
                    koppen_alpha = val
                else:
                    print("Out of range. Using default 0.45.")
            except ValueError:
                print("Invalid number. Using default 0.45.")
        if enable_koppen:
            koppen_classes = prompt_for_koppen_classes()
        else:
            trewartha_classes = prompt_for_climate_classes(
                resolve_trewartha_classes, "e.g. Do, C")

    enable_elevation = input(
        "Colour the land by height, with relief shading? [y/N]: "
    ).strip().lower() in ("y", "yes")

    land_cover_classes: list[str] = []
    ndvi_month = None
    veg_input = input(
        "Vegetation (MODIS): [l]and cover, [n]DVI greenness, or blank for none: "
    ).strip().lower()
    enable_land_cover = veg_input in ("l", "landcover", "land cover")
    if enable_land_cover:
        land_cover_classes = prompt_for_climate_classes(
            resolve_land_cover_classes, "e.g. forest, cropland", what="Land cover classes")
    elif veg_input in ("n", "ndvi"):
        while True:
            ndvi_month = input("NDVI month (YYYY-MM, or a month name for its latest year): ").strip()
            try:
                resolve_ndvi_month(ndvi_month)
                break
            except ValueError as e:
                print(f"{e}\n")

    soil_classes: list[str] = []
    soil_property = None
    soil_input = input(
        "Soil overlay (SoilGrids): [g]roups, [p]roperty such as pH, or blank for none: "
    ).strip().lower()
    enable_soil = soil_input in ("g", "groups", "y", "yes")
    if enable_soil:
        soil_classes = prompt_for_climate_classes(
            resolve_soil_classes, "e.g. Chernozems, PZ", what="Soil groups")
    elif soil_input in ("p", "property"):
        soil_property = prompt_for_selection("Soil property", list(SOIL_PROPERTIES))

    enable_ice = input(
        "Add polar ice at its winter maximum? [y/N]: "
    ).strip().lower() in ("y", "yes")

    crops = prompt_for_crops()

    routes = prompt_for_routes()
    route_legend = bool(routes) and (
        input("Add a key naming each route? [y/N]: ").strip().lower() in ("y", "yes")
    )

    try:
        generate_orthographic_map(
            lat=lat,
            lon=lon,
            output_filename=output_file,
            tile_provider=tile_provider,
            tile_kwargs=tile_kwargs,
            zoom=zoom,
            dpi=DEFAULT_DPI,
            city_name=city_label if city_slug != "custom" else None,
            koppen=enable_koppen,
            koppen_alpha=koppen_alpha,
            routes=routes,
            route_legend=route_legend,
            tile_cache_dir=tile_cache_dir,
            both_hemispheres=both_hemispheres,
            ice=enable_ice,
            crops=crops,
            koppen_classes=koppen_classes,
            trewartha=enable_trewartha,
            trewartha_classes=trewartha_classes,
            elevation=enable_elevation,
            soil=enable_soil,
            soil_classes=soil_classes,
            soil_property=soil_property,
            land_cover=enable_land_cover,
            land_cover_classes=land_cover_classes,
            ndvi=ndvi_month,
            up=up,
        )
    except GoogleTilesError as e:
        print(f"\n{e}")
        sys.exit(1)


def run_cli(args: argparse.Namespace) -> None:
    """Non-interactive CLI mode driven by argparse namespace."""
    # Validate coordinate pairing first
    if args.lon is not None and args.lat is None:
        logger.error("--lon requires --lat.")
        sys.exit(1)
    if args.lat is not None and args.lon is None:
        logger.error("--lat requires --lon.")
        sys.exit(1)

    # Resolve coordinates
    if args.lat is not None:
        if not (-90 <= args.lat <= 90):
            logger.error("--lat must be between -90 and 90.")
            sys.exit(1)
        if not (-180 <= args.lon <= 180):
            logger.error("--lon must be between -180 and 180.")
            sys.exit(1)
        lat, lon = args.lat, args.lon
        city_slug = "custom"
        city_label = f"({lat}, {lon})"
    elif args.city:
        # Find the city (case-insensitive match)
        city_name = next(
            k for k in MAJOR_METROPOLISES if k.lower() == args.city.lower()
        )
        city = MAJOR_METROPOLISES[city_name]
        lat, lon = city["lat"], city["lon"]
        city_slug = city["slug"]
        city_label = city_name
    else:
        logger.error("Provide --city or --lat/--lon.")
        sys.exit(1)

    # Load routes up front so a bad file fails before any tiles are fetched
    try:
        routes = load_route_files(args.route or [])
    except (OSError, ValueError) as e:
        logger.error("Could not load route: %s", e)
        sys.exit(1)
    if args.route_legend and not routes:
        logger.warning("--route-legend is ignored because no --route was given.")

    # Check crop and climate class names now, before any tiles or data are fetched
    try:
        resolve_crops(args.crop or [])
        resolve_koppen_classes(args.koppen_class or [])
        resolve_trewartha_classes(args.trewartha_class or [])
        resolve_soil_classes(args.soil_class or [])
        resolve_land_cover_classes(args.landcover_class or [])
        if args.ndvi:
            resolve_ndvi_month(args.ndvi)
        if args.soil_property:
            resolve_soil_property(args.soil_property, args.soil_depth)
        elif args.soil_depth:
            raise ValueError("--soil-depth needs --soil-property.")
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)
    if (args.landcover or args.landcover_class or args.landcover_year is not None) and args.ndvi:
        logger.error("Choose one vegetation layer: --landcover or --ndvi.")
        sys.exit(1)
    if (args.soil or args.soil_class) and args.soil_property:
        logger.error("Choose one soil layer: --soil/--soil-class or --soil-property.")
        sys.exit(1)
    if (args.koppen or args.koppen_class) and (args.trewartha or args.trewartha_class):
        logger.error("Choose one climate classification: Köppen-Geiger or Trewartha.")
        sys.exit(1)

    # Orientation: a bearing, or the direction toward a place
    up = args.up
    if args.up_toward:
        try:
            target_lat, target_lon = resolve_place(args.up_toward)
            up = initial_bearing(lat, lon, target_lat, target_lon)
        except ValueError as e:
            logger.error("--up-toward: %s", e)
            sys.exit(1)
        logger.info("Direction toward %s: bearing %.1f°", args.up_toward, up)
    if not math.isfinite(up):
        logger.error("--up must be a number of degrees.")
        sys.exit(1)

    try:
        validate_render_options(
            dpi=args.dpi, koppen_alpha=args.koppen_alpha, both_hemispheres=args.both_hemispheres,
            ice_year=args.ice_year, elevation_alpha=args.elevation_alpha, soil_alpha=args.soil_alpha,
            vegetation_alpha=args.vegetation_alpha, land_cover_year=args.landcover_year,
        )
    except ValueError as e:
        logger.error("Invalid option: %s.", e)
        sys.exit(1)

    if args.provider in GOOGLE_MAP_TYPES:
        try:
            resolve_api_key()
        except GoogleTilesError as e:
            logger.error("%s", e)
            sys.exit(1)

    tile_cache_dir = None if args.no_cache else configure_tile_cache(args.cache_dir)

    # Determine output filename
    if args.output:
        if args.output_dir:
            logger.warning("--output-dir is ignored because -o/--output gives the full path.")
        output_file = args.output
        output_dir = None  # explicit path, don't prepend output_dir
    else:
        output_file = build_output_filename(
            city_slug, args.provider, args.zoom, args.both_hemispheres, up,
        )
        output_dir = args.output_dir

    logger.info(
        "Generating map for %s using '%s' at zoom level %d.",
        city_label, args.provider, args.zoom,
    )
    logger.info("Output file: %s", output_file)

    try:
        generate_orthographic_map(
            lat=lat,
            lon=lon,
            output_filename=output_file,
            tile_provider=args.provider,
            zoom=args.zoom,
            dpi=args.dpi,
            output_dir=output_dir,
            city_name=city_label if city_slug != "custom" else None,
            koppen=args.koppen,
            koppen_alpha=args.koppen_alpha,
            routes=routes,
            route_legend=args.route_legend,
            tile_cache_dir=tile_cache_dir,
            both_hemispheres=args.both_hemispheres,
            ice=args.ice or args.ice_year is not None,
            ice_year=args.ice_year,
            crops=args.crop,
            koppen_classes=args.koppen_class,
            trewartha=args.trewartha,
            trewartha_classes=args.trewartha_class,
            elevation=args.elevation,
            elevation_alpha=args.elevation_alpha,
            soil=args.soil,
            soil_classes=args.soil_class,
            soil_alpha=args.soil_alpha,
            soil_property=args.soil_property,
            soil_depth=args.soil_depth,
            land_cover=args.landcover,
            land_cover_classes=args.landcover_class,
            land_cover_year=args.landcover_year,
            ndvi=args.ndvi,
            vegetation_alpha=args.vegetation_alpha,
            up=up,
        )
    except GoogleTilesError as e:
        logger.error("%s", e)
        sys.exit(1)


def main() -> None:
    """Entry point for console_scripts and direct invocation."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    load_env_files()

    if len(sys.argv) == 1:
        run_interactive(tile_cache_dir=configure_tile_cache())
    else:
        parsed_args = parse_cli_args()
        if parsed_args.list_crops:
            print_crop_names()
            return
        run_cli(parsed_args)


if __name__ == "__main__":
    main()
