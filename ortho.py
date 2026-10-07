from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import logging
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
from koppen import KOPPEN_ATTRIBUTION, KoppenDataError, add_koppen_overlay, add_koppen_legend
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
    Only OSM tiles are cached; Google's terms do not allow caching its tiles.
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
    "Sydney": {"lat": -33.8688, "lon": 151.2093, "slug": "sydney"},
    "Lisbon": {"lat": 38.7223, "lon": -9.1393, "slug": "lisbon"},
    "Honolulu": {"lat": 21.3069, "lon": -157.8583, "slug": "honolulu"},
    "Papeete": {"lat": -17.5516, "lon": -149.5585, "slug": "papeete"},
    "San Francisco": {"lat": 37.7749, "lon": -122.4194, "slug": "san_francisco"},
}

TILE_PROVIDERS = [
    "osm",
    "google",
    "google_satellite"
]

GOOGLE_MAP_TYPES = {"google": "roadmap", "google_satellite": "satellite"}

# Fixed credit lines for providers that don't supply their own. Google's
# credit depends on the data shown and comes from the Map Tiles API instead.
TILE_ATTRIBUTIONS = {
    "osm": "Map tiles © OpenStreetMap contributors",
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

    OSM tiles are cached under *cache_dir* when one is given. Google tiles
    are never cached. Google providers create a Map Tiles API session here,
    so a missing or invalid API key raises :class:`GoogleTilesError` before
    any rendering.
    """
    provider = tile_provider.lower()

    if provider == "osm":
        tile_source = CachedOSM(cache=cache_dir or False, **tile_kwargs)
    elif provider in GOOGLE_MAP_TYPES:
        if cache_dir:
            logger.info("Google tiles are not cached (Google Maps Platform terms).")
        tile_source = GoogleMapTiles(map_type=GOOGLE_MAP_TYPES[provider], **tile_kwargs)
    else:
        raise ValueError(
            "Unsupported tile_provider. Choose one of: "
            "osm, google, google_satellite."
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


def validate_render_options(dpi: int, koppen_alpha: float) -> None:
    """Raise ``ValueError`` for options that would only fail after tiles are fetched."""
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise ValueError(f"dpi must be between {MIN_DPI} and {MAX_DPI}, got {dpi}")
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

    Returns
    -------
    str
        Absolute path of the saved PNG.
    """

    validate_render_options(dpi=dpi, koppen_alpha=koppen_alpha)

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

    # Step 2: Define the Orthographic projection
    ortho_proj = ccrs.Orthographic(central_longitude=lon, central_latitude=lat)

    # Step 3: Create a high-resolution figure and axes. The Figure is built
    # directly rather than through pyplot, so it is never registered in
    # pyplot's global state and is freed even if rendering fails.
    fig = Figure(figsize=(20, 20))
    ax = fig.add_subplot(projection=ortho_proj)

    if not isinstance(ax, GeoAxes):
        raise RuntimeError("Failed to create GeoAxes")

    # Step 4: Full hemisphere view
    ax.set_global()

    # Warp resolution for tiles and overlays: the output's pixel size, capped
    # at max_regrid_shape (beyond that the source imagery has no more detail)
    regrid_shape = min(
        max(750, int(min(fig.get_size_inches()) * dpi)),
        max_regrid_shape,
    )

    # Fallback land/ocean so blank areas aren't white
    ax.add_feature(cfeature.OCEAN, facecolor=background_color, edgecolor="none", zorder=0)
    ax.add_feature(cfeature.LAND, facecolor="#f1efe6", edgecolor="none", zorder=0)

    # Step 5: Register the tiles. Cartopy downloads them later, inside savefig;
    # failed tiles come out transparent (see BufferedTileSource).
    ax.add_image(
        tiles,
        zoom,
        regrid_shape=regrid_shape,
        interpolation="nearest",
    )

    # Step 5b: Köppen-Geiger overlay (above tiles, below gridlines)
    koppen_drawn = False
    if koppen:
        logger.info("Applying Köppen-Geiger climate overlay (alpha=%.2f) …", koppen_alpha)
        try:
            add_koppen_overlay(ax, alpha=koppen_alpha, regrid_shape=regrid_shape)
        except (KoppenDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping Köppen-Geiger overlay: %s", e)
        else:
            add_koppen_legend(ax)
            koppen_drawn = True

    # Step 6: Gridlines
    ax.gridlines(draw_labels=False, color='black', alpha=0.3, linestyle='--')

    # Step 7: City marker & label
    if city_name:
        ax.plot(
            lon, lat,
            marker="o", markersize=10, markeredgewidth=2,
            color="#e74c3c", markeredgecolor="white",
            transform=ccrs.PlateCarree(), zorder=10,
        )
        ax.text(
            lon, lat, f"  {city_name}",
            transform=ccrs.PlateCarree(),
            fontsize=14, fontweight="bold", color="white",
            va="center", ha="left", zorder=10,
            path_effects=[
                pe.withStroke(linewidth=3, foreground="black")
            ],
        )

    # Step 7b: Concentric distance circles (2 500 km and 5 000 km)
    _draw_distance_circles(ax, lon, lat)

    # Step 7c: Route overlays
    if routes:
        draw_routes(ax, routes)
        if route_legend:
            # Below the Köppen-Geiger key when that is drawn too
            add_route_legend(ax, routes, y=-0.07 if koppen_drawn else -0.01)

    # Step 7d: Data credits required by the tile and dataset licences
    credits = tile_attribution_lines(tiles, tile_provider, zoom)
    if koppen_drawn:
        credits.append(KOPPEN_ATTRIBUTION)
    _add_attribution(ax, credits)

    # Step 8: Export
    logger.info("Fetching '%s' tiles at zoom %d and saving to '%s' at %d DPI …",
                tile_provider, zoom, output_filename, dpi)
    fig.savefig(output_filename, dpi=dpi, bbox_inches="tight", transparent=True)
    _report_tile_failures(tiles)

    output_path = os.path.abspath(output_filename)
    logger.info("Map successfully created: %s", output_path)
    return output_path



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


def build_output_filename(city_slug: str, tile_provider: str, zoom: int) -> str:
    """Build a descriptive output filename from the render parameters."""
    sanitized_provider = tile_provider.replace(" ", "_")
    return f"orthographic_map_{city_slug}_{sanitized_provider}_z{zoom}.png"


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
        help=f"OSM tile cache directory (default: {DEFAULT_CACHE_DIR}). "
             "Google tiles are never cached.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Download OSM tiles fresh instead of using the tile cache.",
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
    output_file = build_output_filename(city_slug, tile_provider, zoom)

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

    try:
        validate_render_options(dpi=args.dpi, koppen_alpha=args.koppen_alpha)
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
        output_file = build_output_filename(city_slug, args.provider, args.zoom)
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
        run_cli(parsed_args)


if __name__ == "__main__":
    main()
