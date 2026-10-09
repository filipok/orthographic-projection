from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import logging
import math
import os
import sys
import time
from collections.abc import Sequence
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

    Mirrors newsgrab's key handling so both tools share ``~/myapikeys.env``.
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
    """

    def __init__(self, tile_source: Any, tile_buffer_factor: float = 0.5) -> None:
        self.tile_source = tile_source
        self.tile_buffer_factor = tile_buffer_factor
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
        return cimgt._merge_tiles(pieces)


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

    Returns
    -------
    str
        Absolute path of the saved PNG.
    """

    validate_render_options(
        dpi=dpi, koppen_alpha=koppen_alpha, both_hemispheres=both_hemispheres, ice_year=ice_year,
    )
    resolve_crops(crops or [])  # unknown names and bad colours fail before any download
    koppen_codes = resolve_koppen_classes(koppen_classes or [])
    koppen = koppen or bool(koppen_codes)
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

    # Step 5b: Köppen-Geiger overlay (above tiles, below gridlines), one key
    koppen_drawn = False
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
            koppen_drawn = True

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

    # Step 7d: Keys below the globe(s): the crop key, then the route key,
    # under the Köppen-Geiger key when that is drawn too
    keys = []
    if crop_layer is not None:
        keys.append(add_crop_legend(near, crop_layer, x=key_x))
    if routes and route_legend:
        keys.append(add_route_legend(near, routes, x=key_x))
    _stack_keys(near, [k for k in keys if k is not None], top=-0.07 if koppen_drawn else -0.01, x=key_x)

    # Step 7e: Data credits required by the tile and dataset licences
    credits = tile_attribution_lines(tiles, tile_provider, zoom)
    if koppen_drawn:
        credits.append(KOPPEN_ATTRIBUTION)
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
    while True:
        raw = input("Climate classes to show, comma-separated (e.g. Cfb, Cs) [blank for all]: ").strip()
        specs = [part.strip() for part in raw.split(",") if part.strip()]
        try:
            resolve_koppen_classes(specs)
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
    """Build and return the argparse parser."""
    parser = argparse.ArgumentParser(
        description="Generate high-resolution orthographic globe maps.",
        epilog="Run without arguments for interactive mode.",
    )

    location = parser.add_mutually_exclusive_group()
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

    parser.add_argument(
        "--lon",
        type=float,
        help="Custom longitude (-180 to 180). Must be used with --lat.",
    )
    parser.add_argument(
        "--provider",
        choices=TILE_PROVIDERS,
        default="osm",
        help="Tile provider (default: osm)",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=3,
        choices=range(1, 5),
        metavar="ZOOM",
        help="Tile zoom level 1-4 (default: 3)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Output DPI, {MIN_DPI}-{MAX_DPI} (default: {DEFAULT_DPI})",
    )
    parser.add_argument(
        "-o", "--output",
        help="Explicit output filepath (overrides auto-naming)",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory to save auto-named files into (default: current dir)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=f"OSM and NASA tile cache directory (default: {DEFAULT_CACHE_DIR}). "
             "Google tiles are never cached.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Download OSM and NASA tiles fresh instead of using the tile cache.",
    )
    parser.add_argument(
        "--koppen",
        action="store_true",
        default=False,
        help="Enable Köppen-Geiger climate classification overlay.",
    )
    parser.add_argument(
        "--koppen-alpha",
        type=float,
        default=0.45,
        metavar="ALPHA",
        help="Opacity of the Köppen-Geiger overlay (0-1, default: 0.45).",
    )
    parser.add_argument(
        "--koppen-class",
        action="append",
        default=None,
        metavar="CLASS",
        help="Show only this Köppen-Geiger class (e.g. Cfb) or group (e.g. C, Cs); "
             "repeat for several. Implies --koppen.",
    )
    parser.add_argument(
        "--ice",
        action="store_true",
        help="Draw polar ice: sea ice at its winter maximum (NSIDC) and polar land ice.",
    )
    parser.add_argument(
        "--ice-year",
        type=int,
        default=None,
        metavar="YEAR",
        help=f"Year of the sea ice maxima ({FIRST_ICE_YEAR} on; default: latest published). "
             "Implies --ice.",
    )
    parser.add_argument(
        "--crop",
        action="append",
        default=None,
        metavar="NAME[:COLOUR]",
        help="Shade where a crop is grown (CROPGRIDS, c. 2020), e.g. wheat or wheat:#f2b705. "
             "Repeat for several crops; each place then shows the one with the largest share.",
    )
    parser.add_argument(
        "--list-crops",
        action="store_true",
        help="List the crop names --crop accepts, then exit.",
    )
    parser.add_argument(
        "--route",
        action="append",
        default=None,
        metavar="GEOJSON",
        help="GeoJSON file of LineString routes to draw. Repeat for multiple files.",
    )
    parser.add_argument(
        "--route-legend",
        action="store_true",
        help="Add a key below the globe naming each route next to its colour.",
    )
    parser.add_argument(
        "--both-hemispheres",
        action="store_true",
        help="Draw a second globe centred on the antipode, showing the whole Earth "
             f"(max --dpi {MAX_DPI // 2}).",
    )
    orientation = parser.add_mutually_exclusive_group()
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

    return parser


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

    # Köppen-Geiger overlay prompt
    koppen_input = input("Enable Köppen-Geiger climate overlay? [y/N]: ").strip().lower()
    enable_koppen = koppen_input in ("y", "yes")

    koppen_alpha = 0.45
    if enable_koppen:
        raw_alpha = input("Köppen overlay opacity (0-1) [default: 0.45]: ").strip()
        if raw_alpha:
            try:
                val = float(raw_alpha)
                if 0.0 <= val <= 1.0:
                    koppen_alpha = val
                else:
                    print("Out of range. Using default 0.45.")
            except ValueError:
                print("Invalid number. Using default 0.45.")
        koppen_classes = prompt_for_koppen_classes()
    else:
        koppen_classes = []

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
    except ValueError as e:
        logger.error("%s", e)
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
            ice_year=args.ice_year,
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
        parser = build_cli_parser()
        parsed_args = parser.parse_args()
        if parsed_args.list_crops:
            print_crop_names()
            return
        run_cli(parsed_args)


if __name__ == "__main__":
    main()
