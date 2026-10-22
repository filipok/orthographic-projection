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
from typing import Any, NamedTuple

import numpy as np

from matplotlib.figure import Figure
from matplotlib.text import Annotation, Text
import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
import cartopy.feature as cfeature
import matplotlib.path as mpath
import matplotlib.patheffects as pe
from cartopy.geodesic import Geodesic
from cartopy.mpl.geoaxes import GeoAxes
import shapely.affinity
from shapely.geometry import Polygon, box

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
from rotation import (
    EARTH_RADIUS_M, far_side_up, globe_projection, initial_bearing, normalise_bearing,
)
from elevation import (
    ELEVATION_ATTRIBUTION, RELIEF_ZOOM, TerrariumTiles, add_elevation_legend, add_ocean_legend, ocean_rgba,
    relief_rgba,
)
from currents import (
    CURRENTS_ATTRIBUTION, CurrentsDataError, add_currents_legend, add_currents_overlay, load_currents,
)
from cyclones import (
    CYCLONES_ATTRIBUTION, FIRST_YEAR as FIRST_CYCLONE_YEAR, CycloneDataError, add_cyclones_legend, draw_tracks,
    load_tracks,
)
from hydrology import (
    BASINS_ATTRIBUTION, RIVERS_ATTRIBUTION, HydrologyDataError, add_basins_legend, basin_colours, draw_basins,
    draw_rivers, load_basins, load_rivers,
)
from aedes import (
    AEDES_ATTRIBUTION, CHOICES as AEDES_CHOICES, AedesDataError, add_aedes_legend, draw_aedes, load_aedes,
    resolve_aedes,
)
from gbif import (
    GbifDataError, add_occurrences_legend, draw_occurrences, gbif_attribution, load_occurrences, parse_years,
    resolve_taxon,
)
from indicators import (
    EUROSTAT_PRESETS, PRESETS as INDICATOR_PRESETS, IndicatorDataError, add_indicator_legend, class_breaks,
    draw_indicator, indicator_attribution, load_areas, load_values, resolve_indicator, resolve_indicator_year,
)
from air_routes import (
    AirRoutesDataError, AIR_ROUTES_ATTRIBUTION, add_air_routes_legend, draw_air_routes, load_air_routes,
    resolve_air_routes,
)
from shipping import (
    KINDS as SHIP_KINDS, SHIPPING_ATTRIBUTION, ShippingDataError, add_shipping_legend, add_shipping_overlay,
    resolve_ship_kind,
)
from ports import (
    KINDS as PORT_KINDS, PORTS_ATTRIBUTION, PortsDataError, add_ports_legend, draw_ports, load_ports,
    resolve_port_kind,
)
from relief import (
    LEVELS as RELIEF_LEVELS, RELIEF_ATTRIBUTION, ReliefError, add_relief_legend, draw_relief, load_relief,
    resolve_relief_level,
)
from industry import (
    GROUPS as INDUSTRY_GROUPS, SECTORS as INDUSTRY_SECTORS, IndustryDataError, add_industry_legend, draw_industry,
    industry_attribution, load_plants, resolve_sectors,
)
from cities import (
    CITIES_ATTRIBUTION, YEARS as CITIES_YEARS, CitiesDataError, add_cities_legend, default_min_population,
    draw_cities, load_cities, resolve_cities_year,
)
from iucn import IucnDataError, add_range_legend, draw_range, iucn_attribution, load_range
from livestock import (
    LIVESTOCK_ATTRIBUTION, SPECIES as LIVESTOCK_SPECIES, LivestockDataError, add_livestock_legend,
    add_livestock_overlay, load_livestock, resolve_species,
)
from malaria import (
    FIRST_YEAR as FIRST_MALARIA_YEAR, LATEST_YEAR as LATEST_MALARIA_YEAR, MALARIA_ATTRIBUTION, MalariaDataError,
    add_malaria_legend, add_malaria_overlay, load_malaria, resolve_parasite, resolve_year as resolve_malaria_year,
)
from permafrost import (
    PERMAFROST_ATTRIBUTION, PermafrostDataError, add_permafrost_legend, add_permafrost_overlay, load_permafrost,
)
from population import (
    LATEST_YEAR as LATEST_POPULATION_YEAR, POPULATION_ZOOM, YEARS as POPULATION_YEARS, PopulationDataError,
    add_population_legend, load_colormap, population_attribution, population_rgba, population_tiles,
    resolve_population_year,
)
from tectonics import (
    DEFAULT_MIN_MAGNITUDE, MAX_MAGNITUDE, MIN_MAGNITUDE, TectonicsDataError, add_tectonics_legend,
    credits as tectonics_credits, draw_plates, draw_quakes, draw_volcanoes, load_plates, load_quakes,
    load_volcanoes,
)
from timezones import TIMEZONES_ATTRIBUTION, TimeZoneDataError, draw_time_zones, load_time_zones
from iceage import ICE_SHEETS_ATTRIBUTION, IceAgeDataError, draw_ice_sheets, load_ice_sheets
from biomes import (
    BIOME_ATTRIBUTION, ECOREGION_ATTRIBUTION, REALM_ATTRIBUTION, BiomeDataError, add_biome_legend,
    add_ecoregion_legend, add_ecoregion_overlay, add_realm_legend, load_ecoregions, resolve_biome_classes,
    visible_ecoregions, visible_values,
)
from borders import (
    SOURCES as BORDER_SOURCES, BordersError, add_borders_legend, attribution as borders_attribution,
    choose_source as choose_borders_source, colour_groups, draw_borders, load_polities, parse_when,
)
from zoogeography import (
    HOLT_ATTRIBUTION, HoltDataError, add_holt_realm_legend, add_holt_realm_overlay, draw_holt_regions, load_holt,
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
from climate import (
    CLIMATE_ATTRIBUTION, CLIMATE_VARIABLES, ClimateDataError, add_climate_legend, add_climate_overlay,
    add_sea_overlay, resolve_climate_period,
)
from sea import (
    SEA_AIR_ATTRIBUTION, SEA_TEMPERATURE_ATTRIBUTION, SeaDataError, land_and_sea_legend_title, load_sea_air,
    load_sea_temperature, resolve_sea_period, sea_legend_title,
)
from wind import (
    WIND_ATTRIBUTION, WindDataError, add_wind_legend, add_wind_overlay, resolve_wind_period,
)
from trewartha import (
    TREWARTHA_ATTRIBUTION, TrewarthaDataError, add_trewartha_legend, add_trewartha_overlay,
    resolve_trewartha_classes,
)
from routes import Route, add_route_legend, draw_routes, load_routes
from tile_fetch import download_tile
from frame import MAX_ASPECT, half_sides, inside, is_rectangle, max_rectangle_radius_km, parse_aspect, set_rectangle

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


class CachedNASANight(CachedNASA):
    """NASA's Black Marble: the Earth at night from GIBS, cached like Blue Marble.

    VIIRS night-time lights of 2016 (a cloud-free composite of the year's
    moonless nights at ~500 m), over a dark blue land and sea, in Web
    Mercator up to zoom 8. Public domain, no key.
    """

    URL = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/"
           "default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png")

    @property
    def _cache_dir(self) -> Path:  # pyright: ignore[reportIncompatibleMethodOverride]
        assert self.cache_path is not None
        return Path(self.cache_path) / "nasa_night"


# OSM Carto's plain land colour, and the land cover fills osm_plain turns into it
OSM_LAND_RGB = (0xf2, 0xef, 0xe9)
OSM_FOREST_RGB = (0xad, 0xd1, 0x9e)     # forests and woods
OSM_MEADOW_RGB = (0xcd, 0xeb, 0xb0)     # meadows, grassland and grass
OSM_HEATH_RGB = (0xd6, 0xd9, 0x9f)      # heath and dry shrubland
OSM_FARMLAND_RGB = (0xee, 0xf0, 0xd5)   # fields
OSM_SAND_RGB = (0xf5, 0xe9, 0xc6)       # sand deserts and dunes
OSM_PLAIN_FILLS = (OSM_FOREST_RGB, OSM_MEADOW_RGB, OSM_HEATH_RGB, OSM_FARMLAND_RGB, OSM_SAND_RGB)


def plain_osm_land(img: np.ndarray, near: float = 6.0, far: float = 10.0) -> np.ndarray:
    """Return the OSM tile *img* (RGB or RGBA ``uint8``) with its land cover fills turned into plain land.

    The fills are :data:`OSM_PLAIN_FILLS`. Their edges are anti-aliased
    against the land around them, so each pixel is read as a mix of a fill
    and land: it is matched to the fill whose line to land it lies closest
    to, placed on that line and moved toward land by its fill share. Pixels
    more than *far* (RGB units) off every line, such as parks, orchards,
    scrub, beaches, water, built-up areas, roads and labels, are kept; between
    *near* and *far* the change fades out. Allotments (``#c9e1bf``) lie on
    the forest line and are lightened too.
    """
    fills = np.array(OSM_PLAIN_FILLS, np.float32)                   # (fill, rgb)
    steps = np.array(OSM_LAND_RGB, np.float32) - fills
    rgb = img[..., :3].astype(np.float32)
    rel = rgb[..., None, :] - fills                                 # (..., fill, rgb)
    toward_land = np.clip((rel * steps).sum(-1) / (steps * steps).sum(-1), 0.0, 1.0)
    off_line = np.linalg.norm(rel - toward_land[..., None] * steps, axis=-1)
    nearest = off_line.argmin(-1)[..., None]
    off_line = np.take_along_axis(off_line, nearest, -1)[..., 0]
    toward_land = np.take_along_axis(toward_land, nearest, -1)[..., 0]
    weight = np.clip((far - off_line) / (far - near), 0.0, 1.0)
    out = img.copy()
    out[..., :3] = np.clip(np.rint(rgb + (weight * (1.0 - toward_land))[..., None] * steps[nearest[..., 0]]), 0, 255)
    return out


class CachedOSMPlain(CachedOSM):
    """OSM tiles with forests, meadows, heath, fields and sand deserts drawn as plain land.

    Zoomed in, OSM tints most land green, olive or yellow by what covers it,
    which clashes with the layers drawn over it (see :func:`plain_osm_land`).
    The tiles share the ``osm`` cache: they are stored as downloaded and
    recoloured on each read.
    """

    def get_image(self, tile: tuple[int, int, int]):  # same (image, extent, origin) shape as Cartopy
        img, extent, origin = super().get_image(tile)
        return plain_osm_land(img), extent, origin


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
    "Oslo": {"lat": 59.9139, "lon": 10.7522, "slug": "oslo"},
    "Honolulu": {"lat": 21.3069, "lon": -157.8583, "slug": "honolulu"},
    "Papeete": {"lat": -17.5516, "lon": -149.5585, "slug": "papeete"},
    "San Francisco": {"lat": 37.7749, "lon": -122.4194, "slug": "san_francisco"},
}

TILE_PROVIDERS = [
    "osm",
    "osm_plain",
    "google",
    "google_satellite",
    "nasa",
    "nasa_night",
]

GOOGLE_MAP_TYPES = {"google": "roadmap", "google_satellite": "satellite"}

# Fixed credit lines for providers that don't supply their own. Google's
# credit depends on the data shown and comes from the Map Tiles API instead.
TILE_ATTRIBUTIONS = {
    "osm": "Map tiles © OpenStreetMap contributors",
    "osm_plain": "Map tiles © OpenStreetMap contributors",
    "nasa": "Imagery: NASA Blue Marble, via NASA GIBS (ESDIS)",
    "nasa_night": "Imagery: NASA Black Marble 2016 (VIIRS night lights), via NASA GIBS (ESDIS)",
}

# Land and sea drawn under the tiles, so a tile that fails to download is not
# blank: the map's own colours where they differ from the light defaults
FALLBACK_COLOURS = {"nasa_night": ("#04050f", "#0b0d18")}     # (sea, land) of Black Marble


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

    A view across the date line needs tiles from both ends of the tile grid.
    Those west of the gap are moved one world width east, so the mosaic is
    one piece, and it is drawn in a Web Mercator centred on 180°: while it
    is, :attr:`crs` is that projection (Cartopy reads it after each call).
    """

    def __init__(self, tile_source: Any, tile_buffer_factor: float = 0.5,
                 postprocess: Callable[[np.ndarray, Any], np.ndarray] | None = None) -> None:
        self.tile_source = tile_source
        self.tile_buffer_factor = tile_buffer_factor
        self.postprocess = postprocess
        self.tile_crs = tile_source.crs
        self.crs = self.tile_crs
        self.total_tiles = 0
        self.failed_tiles: list[tuple[Any, BaseException]] = []

    def __getattr__(self, name: str) -> Any:
        if name == "tile_source":  # not set yet (e.g. during copy/unpickling)
            raise AttributeError(name)
        return getattr(self.tile_source, name)

    def image_for_domain(self, target_domain: Any, target_z: int) -> Any:
        x0, x1 = self.tile_crs.x_limits
        world = x1 - x0
        if self.crs is not self.tile_crs:
            # The last mosaic crossed the date line, so Cartopy gave this domain in its frame
            target_domain = _from_date_line_frame(target_domain, world)
            self.crs = self.tile_crs
        tile_width = world / (2 ** target_z)
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
        moved = _tiles_past_date_line(tiles, source, x0, x1)
        pieces = []
        for tile in tiles:
            if tile in results:
                img, extent, origin = results[tile]
                img = _to_rgba(img)
            else:
                img = np.zeros(size + (4,), dtype=np.uint8)  # transparent placeholder
                extent, origin = source.tileextent(tile), "lower"
            shift = world if tile in moved else 0.0
            x = np.linspace(extent[0] + shift, extent[1] + shift, img.shape[1])
            y = np.linspace(extent[2], extent[3], img.shape[0])
            pieces.append([img, x, y, origin])
        img, extent, origin = cimgt._merge_tiles(pieces)
        if self.postprocess is not None:
            img = self.postprocess(img, extent)
        if moved:
            extent = [extent[0] - world / 2, extent[1] - world / 2, extent[2], extent[3]]
            self.crs = _date_line_mercator(self.tile_crs)
        return img, extent, origin


def _tiles_past_date_line(tiles: Sequence[Any], source: Any, x0: float, x1: float) -> set[Any]:
    """The tiles west of the gap in a set that wraps from the tile grid's east edge to its west.

    Empty when the tiles' columns run without a gap, or the gap is not one
    the date line makes (the set must reach both edges of the grid).
    """
    if not tiles:
        return set()
    lefts = {tile: source.tileextent(tile)[0] for tile in tiles}
    width = source.tileextent(tiles[0])[1] - lefts[tiles[0]]
    columns = sorted(set(lefts.values()))
    if columns[0] > x0 + width / 2 or columns[-1] + width < x1 - width / 2:
        return set()
    gaps = [(b - a, b) for a, b in zip(columns, columns[1:]) if b - a > 1.5 * width]
    if not gaps:
        return set()
    _, gap_end = max(gaps)
    return {tile for tile, left in lefts.items() if left < gap_end}


@functools.cache
def _date_line_mercator(crs: ccrs.Mercator) -> ccrs.Mercator:
    """*crs*, a Web Mercator, centred on 180° instead of 0°."""
    limit = float(np.degrees(np.arctan(np.sinh(crs.y_limits[1] / crs.globe.semimajor_axis))))
    return ccrs.Mercator(central_longitude=180, min_latitude=-limit, max_latitude=limit, globe=crs.globe)


def _from_date_line_frame(domain: Any, world: float) -> Any:
    """*domain*, given in :func:`_date_line_mercator` coordinates, in the tile grid's own."""
    half = world / 2
    tall = 2 * world
    west = shapely.affinity.translate(domain.intersection(box(-half, -tall, 0, tall)), xoff=half)
    east = shapely.affinity.translate(domain.intersection(box(0, -tall, half, tall)), xoff=-half)
    return west.union(east)


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
    elif provider == "osm_plain":
        tile_source = CachedOSMPlain(cache=cache_dir or False, **tile_kwargs)
    elif provider == "nasa":
        tile_source = CachedNASA(cache=cache_dir or False, **tile_kwargs)
    elif provider == "nasa_night":
        tile_source = CachedNASANight(cache=cache_dir or False, **tile_kwargs)
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


# Whole-globe renders take zoom 1-4: zoom 4 already fetches ~150 tiles.
# Radius maps show less of the Earth, so they take finer tiles (see max_zoom).
DEFAULT_ZOOM = 3
MAX_GLOBE_ZOOM = 4
MAX_TILE_ZOOM = {"nasa": 8, "nasa_night": 8}   # NASA's Blue and Black Marble stop at zoom 8; the others go deeper
MAX_RADIUS_ZOOM = 12
MAX_RELIEF_ZOOM = 10                  # terrain finer than this adds nothing at the output's size
DEFAULT_RADIUS_KM = 2_500
MIN_RADIUS_KM = 100
MAX_RADIUS_KM = 10_000                # about a quarter of the way round: the whole hemisphere
_EQUATOR_KM = 2 * math.pi * 6_378.137
# A radius map's default zoom puts about this many tiles across it
_TILES_ACROSS = 12
# Radius maps over-fetch half a tile round their edge; the globe's curved
# edge needs more (see BufferedTileSource)
RADIUS_TILE_BUFFER = 0.5


def _radius_zoom(radius_km: float, lat: float) -> int:
    """Zoom with about a dozen tiles across a map *radius_km* around latitude *lat*.

    Web Mercator tiles cover less ground away from the equator (by the cosine
    of the latitude), so the same map takes a lower zoom there.
    """
    tile_km = _EQUATOR_KM * math.cos(math.radians(min(abs(lat), 75.0)))   # a zoom 0 tile, across
    return round(math.log2(_TILES_ACROSS * tile_km / (2 * radius_km)))


def zoom_for_radius(radius_km: float, lat: float = 0.0, tile_provider: str = "osm") -> int:
    """The default tile zoom for a map *radius_km* around a centre at latitude *lat*.

    About a dozen tiles across the map: for 2,500 km, zoom 7 near the equator
    and 6 at 45°.
    """
    zoom = _radius_zoom(radius_km, lat)
    return max(DEFAULT_ZOOM, min(zoom, MAX_TILE_ZOOM.get(tile_provider.lower(), MAX_RADIUS_ZOOM)))


def max_zoom(radius_km: float | None = None, lat: float = 0.0, tile_provider: str = "osm") -> int:
    """The highest tile zoom allowed: 4 for the whole globe, one above the default for a radius map."""
    if radius_km is None:
        return MAX_GLOBE_ZOOM
    zoom = _radius_zoom(radius_km, lat) + 1
    return max(MAX_GLOBE_ZOOM, min(zoom, MAX_TILE_ZOOM.get(tile_provider.lower(), MAX_RADIUS_ZOOM)))


# Default opacity of the climate layers: classes over the map, or a climate mean's colour bands
CLASSIFICATION_ALPHA = 0.45
CLIMATE_MEAN_ALPHA = 0.7


def resolve_climate_mean(
    temperature: str | None = None, precipitation: str | None = None, humidity: str | None = None,
) -> tuple[Any, Any] | None:
    """The climate variable and period asked for, or ``None``; at most one of the three."""
    asked = [(name, spec) for name, spec in
             (("temperature", temperature), ("precipitation", precipitation), ("humidity", humidity))
             if spec is not None]
    if len(asked) > 1:
        raise ValueError("choose one climate mean: temperature, precipitation or humidity")
    if not asked:
        return None
    name, spec = asked[0]
    return CLIMATE_VARIABLES[name], resolve_climate_period(name, spec)


def zoom_latitude(lat: float, beside: tuple[float, float] | None = None) -> float:
    """The latitude a radius map's zoom is chosen for: with a globe *beside*, the centre nearer the equator.

    Tiles cover the most ground there, so it needs the finest zoom.
    """
    return lat if beside is None else min(lat, beside[0], key=abs)


def validate_render_options(
    dpi: int, koppen_alpha: float | None = None, both_hemispheres: bool = False, ice_year: int | None = None,
    elevation_alpha: float = 0.8, soil_alpha: float = 0.6, vegetation_alpha: float = 0.7,
    land_cover_year: int | None = None, zoom: int | None = None, radius_km: float | None = None,
    rectangle: float | None = None, tile_provider: str = "osm", lat: float = 0.0,
    beside: tuple[float, float] | None = None,
    biome_alpha: float = 0.6, borders_alpha: float = 0.55, basins_alpha: float = 0.5,
    permafrost_alpha: float = 0.7, population_alpha: float = 0.7, livestock_alpha: float = 0.7,
    earthquakes: float | None = None, cyclones: int | None = None, malaria_alpha: float = 0.75,
    iucn_alpha: float = 0.45, indicator_alpha: float = 0.75, shipping_alpha: float = 0.85,
    relief_alpha: float = 0.55,
) -> None:
    """Raise ``ValueError`` for options that would only fail after tiles are fetched."""
    if beside is not None:
        if both_hemispheres:
            raise ValueError("the second globe shows the antipode or another place, not both")
        beside_lat, beside_lon = beside
        if not (-90 <= beside_lat <= 90 and -180 <= beside_lon <= 180):
            raise ValueError(f"beside must be a latitude (-90 to 90) and longitude (-180 to 180), got {beside}")
    lat = zoom_latitude(lat, beside)
    if radius_km is not None:
        if not (math.isfinite(radius_km) and MIN_RADIUS_KM <= radius_km <= MAX_RADIUS_KM):
            raise ValueError(f"radius_km must be between {MIN_RADIUS_KM:,} and {MAX_RADIUS_KM:,}, got {radius_km}")
        if both_hemispheres:
            raise ValueError("a radius map shows one globe; it cannot have both hemispheres")
    if rectangle is not None:
        if radius_km is None:
            raise ValueError("a rectangle is cut from a radius map; give a radius too")
        if not (math.isfinite(rectangle) and 1 / MAX_ASPECT <= rectangle <= MAX_ASPECT):
            raise ValueError(f"rectangle must be between 1:{MAX_ASPECT:g} and {MAX_ASPECT:g}:1, got {rectangle:g}")
        widest = max_rectangle_radius_km(rectangle, EARTH_RADIUS_M / 1_000)
        if radius_km > widest:
            raise ValueError(f"the corners of a {radius_km:,g} km rectangle would reach past the edge of the globe; "
                             f"use a radius of at most {math.floor(widest):,} km")
    if zoom is not None:
        top = max_zoom(radius_km, lat, tile_provider)
        if not 1 <= zoom <= top:
            where = f"a {radius_km:,g} km radius" if radius_km is not None else "the whole globe"
            raise ValueError(f"zoom must be between 1 and {top} for {where}, got {zoom}")
    if ice_year is not None and not FIRST_ICE_YEAR <= ice_year <= time.localtime().tm_year:
        raise ValueError(
            f"ice_year must be between {FIRST_ICE_YEAR} and this year, got {ice_year}"
        )
    # Two globes double the width, so halve the cap to keep the same pixel budget
    max_dpi = MAX_DPI // 2 if both_hemispheres or beside is not None else MAX_DPI
    if not MIN_DPI <= dpi <= max_dpi:
        layout = " with both hemispheres" if both_hemispheres else " with two globes" if beside is not None else ""
        raise ValueError(f"dpi must be between {MIN_DPI} and {max_dpi}{layout}, got {dpi}")
    if koppen_alpha is not None and not 0.0 <= koppen_alpha <= 1.0:
        raise ValueError(f"koppen_alpha must be between 0 and 1, got {koppen_alpha}")
    if not 0.0 <= elevation_alpha <= 1.0:
        raise ValueError(f"elevation_alpha must be between 0 and 1, got {elevation_alpha}")
    if not 0.0 <= soil_alpha <= 1.0:
        raise ValueError(f"soil_alpha must be between 0 and 1, got {soil_alpha}")
    if not 0.0 <= vegetation_alpha <= 1.0:
        raise ValueError(f"vegetation_alpha must be between 0 and 1, got {vegetation_alpha}")
    if not 0.0 <= biome_alpha <= 1.0:
        raise ValueError(f"biome_alpha must be between 0 and 1, got {biome_alpha}")
    if not 0.0 <= borders_alpha <= 1.0:
        raise ValueError(f"borders_alpha must be between 0 and 1, got {borders_alpha}")
    for name, value in (("basins_alpha", basins_alpha), ("permafrost_alpha", permafrost_alpha),
                        ("population_alpha", population_alpha), ("livestock_alpha", livestock_alpha),
                        ("malaria_alpha", malaria_alpha), ("iucn_alpha", iucn_alpha),
                        ("indicator_alpha", indicator_alpha), ("shipping_alpha", shipping_alpha),
                        ("relief_alpha", relief_alpha)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1, got {value}")
    if earthquakes is not None and not MIN_MAGNITUDE <= earthquakes <= MAX_MAGNITUDE:
        raise ValueError(f"earthquakes must be a magnitude from {MIN_MAGNITUDE:g} to {MAX_MAGNITUDE:g}, "
                         f"got {earthquakes:g}")
    if cyclones is not None and not FIRST_CYCLONE_YEAR <= cyclones <= time.localtime().tm_year:
        raise ValueError(f"cyclones must start from a year between {FIRST_CYCLONE_YEAR} and this year, "
                         f"got {cyclones}")
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
    koppen_alpha: float | None = None,
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
    biomes: bool = False,
    biome_classes: Sequence[str] | None = None,
    ecoregions: bool = False,
    realms: str | None = None,
    realm_regions: bool = False,
    biome_alpha: float = 0.6,
    wind: str | None = None,
    radius_km: float | None = None,
    rectangle: float | None = None,
    temperature: str | None = None,
    precipitation: str | None = None,
    humidity: str | None = None,
    circles_from: tuple[float, float] | None = None,
    distance_circles: bool = True,
    beside: tuple[float, float] | None = None,
    beside_name: str | None = None,
    beside_up: float | None = None,
    temperature_over_sea: bool = False,
    sea_temperature: str | None = None,
    borders: str | int | None = None,
    borders_source: str = "auto",
    borders_alpha: float = 0.55,
    ocean_depth: bool = False,
    ice_age: bool = False,
    plates: bool = False,
    earthquakes: float | None = None,
    volcanoes: bool = False,
    currents: bool = False,
    rivers: bool = False,
    basins: bool = False,
    basins_alpha: float = 0.5,
    cyclones: int | None = None,
    time_zones: bool = False,
    permafrost: bool = False,
    permafrost_alpha: float = 0.7,
    population: int | None = None,
    population_alpha: float = 0.7,
    livestock: str | None = None,
    livestock_alpha: float = 0.7,
    malaria: str | None = None,
    malaria_year: int = LATEST_MALARIA_YEAR,
    malaria_alpha: float = 0.75,
    aedes: str | None = None,
    species: str | None = None,
    species_years: str | None = None,
    iucn: str | None = None,
    iucn_data: str | None = None,
    iucn_alpha: float = 0.45,
    cities: int | None = None,
    cities_min: int | None = None,
    indicator: str | None = None,
    indicator_year: int | None = None,
    indicator_alpha: float = 0.75,
    industry: str | None = None,
    shipping: str | None = None,
    shipping_alpha: float = 0.85,
    ports: str | None = None,
    air_routes: str | None = None,
    relief_units: str | None = None,
    relief_alpha: float = 0.55,
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
        Ocean colour (the fallback ocean under the tiles; ``"nasa_night"``
        uses its own dark sea and land). The PNG itself is saved with a
        transparent background.
    tile_provider : str
        One of :data:`TILE_PROVIDERS`: ``"osm"``, ``"osm_plain"``, ``"google"``,
        ``"google_satellite"``, ``"nasa"``, ``"nasa_night"`` (the Earth at night).
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
    koppen_alpha : float, optional
        Opacity of the climate layer (0–1); by default 0.45 for a
        classification and 0.7 for a climate mean.
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
    beside : (float, float), optional
        (lat, lon) of a second globe drawn beside the first instead of the
        antipode, with every layer, its own distance circles and, with
        *radius_km*, the same radius. Not with *both_hemispheres*. Limits
        *dpi* to ``MAX_DPI // 2``.
    beside_name : str, optional
        Name to mark the second globe's centre with, as *city_name* does the first's.
    beside_up : float, optional
        Compass bearing at the top of the second globe (default: *up*).
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
    circles_from : (float, float), optional
        (lat, lon) to measure the distance circles from, marked with a hollow
        ring, instead of the centre. On a radius map the rings are spaced to
        cover the map as seen from there.
    distance_circles : bool
        When False, leave out the distance circles (and *circles_from*'s
        ring), as a thematic map in an atlas may want.
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
    biomes : bool
        When True, colour the land by its biome (RESOLVE Ecoregions 2017, see
        :mod:`biomes`), above the vegetation and below the climate colours,
        with a key naming the biomes on the map.
    biome_classes : sequence of str, optional
        Show only these biomes, by number or words from their names
        (``"temperate"``, ``"forest"``, ``"mangroves"``, see
        :func:`biomes.resolve_biome_classes`). Implies *biomes*.
    ecoregions : bool
        Colour each of the 846 ecoregions instead of their biomes (only those
        of *biome_classes*, if given), with a key naming the largest on the map.
    realms : str, optional
        Colour biogeographic realms instead of biomes, with a key: ``"wwf"``
        for WWF's eight (Nearctic, Palearctic …, from RESOLVE; with
        *biome_classes*, only in those biomes), or ``"holt"`` for the 11
        zoogeographic realms of Holt et al. (2013), the update of Wallace's map
        (see :mod:`zoogeography`; not with *biomes* or *biome_classes*). Not
        with *ecoregions*.
    realm_regions : bool
        Outline Holt et al.'s 20 zoogeographic regions and name those on the
        map, over any layer.
    biome_alpha : float
        Opacity of the biome, ecoregion or realm layer (0–1).
    wind : str, optional
        Draw the prevailing winds (NCEP/NCAR 1991–2020 mean 10 m wind, see
        :mod:`wind`) for ``"year"``, a season (``"djf"``, ``"mam"``,
        ``"jja"``, ``"son"``) or a month (``"july"``, ``"7"``), with a key.
    temperature, precipitation, humidity : str, optional
        Colour the land by the 1981–2010 mean air temperature, precipitation
        total or relative humidity (CHELSA v2.1, see :mod:`climate`) over a
        period given as for *wind*, with a key. One of the three at most, and
        not with a climate classification; *koppen_alpha* sets its opacity.
    temperature_over_sea : bool
        With *temperature*, colour the sea and lakes by the 1981–2010 mean air
        temperature over them too (NCEP/NCAR Reanalysis 1, see :mod:`sea`),
        in the same image and key as the land.
    sea_temperature : str, optional
        Colour the sea and lakes by their 1991–2020 mean surface temperature
        (NOAA OISST v2.1, see :mod:`sea`) over a period given as for *wind*,
        in the temperature bands, with a key. It goes with any climate layer;
        with *temperature* over the same period the two share one image and
        key. Not with *temperature_over_sea*. *koppen_alpha* sets its opacity
        when given (default 0.7).
    borders : str or int, optional
        Draw the states of a moment in history, filled in colours that differ
        from their neighbours', outlined and named, with a key: a year
        (``1500``, ``-500`` or ``"500 BC"``) or, from 1886, a date
        (``"1914-08-01"``), see :func:`borders.parse_when`.
    borders_source : str
        ``"cliopatria"`` (3400 BCE-2024, states and empires), ``"cshapes"``
        (1886-2019, states and their colonies, to the day) or ``"auto"``
        (CShapes in its years, Cliopatria otherwise).
    borders_alpha : float
        Opacity of the borders' fill (0–1).
    ocean_depth : bool
        Colour the sea floor by depth and shade it (terrain tiles, see
        :mod:`elevation`), with a key. *elevation_alpha* sets its opacity.
    ice_age : bool
        Draw the land the last ice age left dry (the sea ~120 m lower, about
        21,000 years ago) and its coastline, with or without *ocean_depth*.
    plates : bool
        Draw the plate boundaries (Bird 2003) by kind and name the large plates.
    earthquakes : float, optional
        Draw the earthquakes of at least this magnitude (5-9) since 1900 (USGS),
        sized by magnitude and coloured by depth.
    volcanoes : bool
        Draw the Holocene volcanoes (Smithsonian GVP), red if they have erupted since 1900.
    currents : bool
        Draw the mean ocean surface currents (NOAA drifters) as arrows,
        coloured by how warm their water is for its latitude.
    rivers : bool
        Draw the main rivers (Natural Earth) and name the greatest.
    basins : bool
        Colour the river basins of 100,000 km² or more (HydroBASINS), named,
        inland drainage in grey, with a key; *basins_alpha* sets their opacity.
    cyclones : int, optional
        Draw the tropical cyclone tracks (IBTrACS) from this year (1980 on),
        coloured by Saffir-Simpson category.
    time_zones : bool
        Shade and label the standard-time zones (Natural Earth).
    permafrost : bool
        Colour the Northern Hemisphere's permafrost zones (Obu et al. 2019);
        *permafrost_alpha* sets their opacity.
    population : int, optional
        Colour the land by population density in this year (2000, 2005, 2010,
        2015 or 2020; GPWv4 via NASA GIBS); *population_alpha* sets its opacity.
    livestock : str, optional
        Colour the land by the density of one kind of livestock in 2015
        (``"cattle"``, ``"sheep"``, ``"chickens"`` …, see :data:`livestock.SPECIES`);
        *livestock_alpha* sets its opacity.
    malaria : str, optional
        Colour the land by the malaria parasite rate in *malaria_year*
        (2000-2025, Malaria Atlas Project): ``"pf"`` for *Plasmodium
        falciparum* (children aged 2-10) or ``"pv"`` for *P. vivax* (all ages);
        *malaria_alpha* sets its opacity.
    aedes : str, optional
        Draw where *Aedes* mosquitoes have been recorded (Kraemer et al. 2015):
        ``"both"``, ``"aegypti"`` or ``"albopictus"``.
    species : str, optional
        Draw where a species (or genus, family …) has been recorded in the
        wild, from GBIF, by its scientific or common name (``"Panthera leo"``,
        ``"red fox"``); *species_years* (``"1990-2020"`` or ``"2015"``) keeps
        the records of those years.
    iucn : str, optional
        Draw the IUCN Red List range of a species (or genus, family …), from
        range files the user downloaded from IUCN into *iucn_data* (default
        ``~/.cache/ortho_tiles/iucn``), shaded by presence, origin and
        season; *iucn_alpha* sets its opacity.
    cities : int, optional
        Draw the built-up urban centres of this year (1975-2030 every five
        years; GHSL) and name the largest; *cities_min* is the fewest people
        a centre shown has (default: a million on a whole globe, fewer on a
        closer map).
    indicator : str, optional
        Shade each country by a World Bank statistic: a short name
        (``"life-expectancy"``, see :data:`indicators.PRESETS`) or a World
        Bank code (``"NY.GDP.PCAP.CD"``); or each European region by a
        Eurostat one (``"eurostat:unemployment"``, or
        ``"eurostat:DATASET?dim=CODE&level=nuts3"``). In *indicator_year*
        (default: each area's latest); *indicator_alpha* sets its opacity.
    industry : str, optional
        Draw industrial plants (Climate TRACE) as circles sized by their
        emissions, the largest named: comma-separated sectors
        (``"refineries,steel"``, see :data:`industry.SECTORS`) or groups
        (``"petrochemical"``, ``"heavy"``, ``"mining"``, ``"all"``).
    shipping : str, optional
        Colour the sea by how busy it is with ships (World Bank and IMF,
        from AIS 2015-2021): ``"commercial"``, ``"all"``, ``"fishing"``,
        ``"passenger"``, ``"oil-gas"`` or ``"leisure"`` (see
        :data:`shipping.KINDS`); *shipping_alpha* sets its opacity.
    ports : str, optional
        Draw the ports (IMF PortWatch) as dots sized by the ships calling a
        year (2019-2025), the busiest named: ``"all"`` or the ports of one
        kind of ship, ``"container"``, ``"tanker"``, ``"dry-bulk"``,
        ``"general-cargo"`` or ``"roro"`` (see :data:`ports.KINDS`).
    air_routes : str, optional
        Draw air routes (OpenFlights, 2014) as great-circle arcs, wider for
        more airlines: ``"all"`` for the whole network, or airports by code
        or city (``"LHR"``, ``"London"``) and airlines (``"airline:BA"``),
        comma-separated.
    relief_units : str, optional
        Draw Romania's relief units (Posea & Badea 1984, mapped by Candrea
        et al. 2008), filled in the colour of their major region (paler for
        depressions, corridors and floodplains) and named:
        ``"units"`` for all 820, from the Carpathians to single depressions
        and plains, or ``"regions"`` for the 16 major regions (see
        :data:`relief.LEVELS`); *relief_alpha* sets the fill's opacity.
    radius_km : float, optional
        Show only the land within this distance of the centre (100–10,000 km),
        as a disc filling the frame, instead of the whole hemisphere. Use a
        finer *zoom* to match (see :func:`zoom_for_radius`). Not with
        *both_hemispheres*; with *beside*, both globes show this radius.
    rectangle : float, optional
        Cut a radius map to a rectangle this many times wider than tall
        (below 1 for a tall one) instead of a disc, to fill a page. Its longer
        side is the disc's diameter, so the corners show more than the disc
        would. Needs *radius_km*.

    Returns
    -------
    str
        Absolute path of the saved PNG.
    """

    validate_render_options(
        dpi=dpi, koppen_alpha=koppen_alpha, both_hemispheres=both_hemispheres, ice_year=ice_year,
        elevation_alpha=elevation_alpha, soil_alpha=soil_alpha, vegetation_alpha=vegetation_alpha,
        land_cover_year=land_cover_year, zoom=zoom, radius_km=radius_km, rectangle=rectangle,
        tile_provider=tile_provider, lat=lat, beside=beside, biome_alpha=biome_alpha,
        borders_alpha=borders_alpha, basins_alpha=basins_alpha,
        permafrost_alpha=permafrost_alpha, population_alpha=population_alpha, livestock_alpha=livestock_alpha,
        earthquakes=earthquakes, cyclones=cyclones, malaria_alpha=malaria_alpha, iucn_alpha=iucn_alpha,
        indicator_alpha=indicator_alpha, shipping_alpha=shipping_alpha, relief_alpha=relief_alpha,
    )
    population_year = resolve_population_year(population) if population is not None else None
    livestock_species = resolve_species(livestock) if livestock else None
    malaria_parasite = resolve_parasite(malaria) if malaria else None
    if malaria_parasite is not None:
        resolve_malaria_year(malaria_year)
    aedes_species = resolve_aedes(aedes) if aedes else ()
    record_years = parse_years(species_years) if species else None
    taxon = None
    if species:
        try:
            taxon = resolve_taxon(species)
        except GbifDataError as e:
            logger.warning("Skipping the species records: %s", e)
    species_range = None
    if iucn:
        try:
            species_range = load_range(iucn, iucn_data)
        except IucnDataError as e:
            logger.warning("Skipping the IUCN range: %s", e)
    cities_year = resolve_cities_year(cities) if cities is not None else None
    industry_sectors = resolve_sectors(industry) if industry else ()
    ship_kind = resolve_ship_kind(shipping) if shipping else None
    landforms = None
    if relief_units:
        try:
            landforms = load_relief(resolve_relief_level(relief_units))
        except (ReliefError, OSError) as e:
            logger.warning("Skipping the relief units: %s", e)
    harbours = None
    if ports:
        try:
            harbours = load_ports(resolve_port_kind(ports))
        except (PortsDataError, OSError) as e:
            logger.warning("Skipping the ports: %s", e)
    flights = None
    if air_routes:
        try:
            flights = load_air_routes(resolve_air_routes(air_routes))
        except AirRoutesDataError as e:
            logger.warning("Skipping the air routes: %s", e)
    statistic = None
    if indicator:
        stat_year = resolve_indicator_year(indicator_year)
        try:
            statistic = load_values(resolve_indicator(indicator), stat_year)
        except IndicatorDataError as e:
            logger.warning("Skipping the statistics: %s", e)
    if cities_min is not None and cities_min < 0:
        raise ValueError(f"cities_min must be a number of people, got {cities_min}")
    wind_period = resolve_wind_period(wind) if wind is not None else None
    land_cover_codes = resolve_land_cover_classes(land_cover_classes or [])
    land_cover = land_cover or bool(land_cover_codes) or land_cover_year is not None
    ndvi_month = resolve_ndvi_month(ndvi) if ndvi else None
    if land_cover and ndvi_month:
        raise ValueError("choose one vegetation layer: land cover or NDVI")
    soil_codes = resolve_soil_classes(soil_classes or [])
    soil = soil or bool(soil_codes)
    biome_codes = resolve_biome_classes(biome_classes or [])
    if realms is True:                       # realms=True: the default source
        realms = "wwf"
    if realms not in (None, False, "wwf", "holt"):
        raise ValueError(f"realms must be 'wwf' or 'holt', got {realms!r}")
    if ecoregions and realms:
        raise ValueError("choose one: ecoregions or realms")
    holt_realms = realms == "holt"
    if holt_realms and (biomes or biome_codes):
        raise ValueError("Holt's realms are not split by biome: not with biomes or biome classes")
    biomes = biomes or ecoregions or realms == "wwf" or bool(biome_codes)
    colour_by = "ecoregion" if ecoregions else "realm" if realms else "biome"
    prop, prop_depth = resolve_soil_property(soil_property, soil_depth) if soil_property else (None, None)
    if soil_depth and not soil_property:
        raise ValueError("soil_depth needs a soil_property")
    if soil and prop:
        raise ValueError("choose one soil layer: soil groups or a soil property")
    resolve_crops(crops or [])  # unknown names and bad colours fail before any download
    border_when = parse_when(borders) if borders is not None and borders != "" else None
    border_source = choose_borders_source(border_when, borders_source) if border_when else None
    koppen_codes = resolve_koppen_classes(koppen_classes or [])
    koppen = koppen or bool(koppen_codes)
    trewartha_codes = resolve_trewartha_classes(trewartha_classes or [])
    trewartha = trewartha or bool(trewartha_codes)
    if koppen and trewartha:
        raise ValueError("choose one climate classification: Köppen-Geiger or Trewartha")
    climate_mean = resolve_climate_mean(temperature, precipitation, humidity)
    if climate_mean and (koppen or trewartha):
        raise ValueError("choose one climate layer: a classification or a climate mean")
    sea_period = resolve_sea_period(sea_temperature) if sea_temperature is not None else None
    if temperature_over_sea:
        if climate_mean is None or climate_mean[0].key != "temperature":
            raise ValueError("temperature_over_sea needs temperature")
        if sea_period is not None:
            raise ValueError("choose one layer for the sea: the air temperature over it or its surface temperature")
    # The sea's own layer keeps the climate means' opacity unless one is given
    sea_alpha = CLIMATE_MEAN_ALPHA if koppen_alpha is None else koppen_alpha
    if koppen_alpha is None:
        koppen_alpha = CLIMATE_MEAN_ALPHA if climate_mean else CLASSIFICATION_ALPHA
    if not math.isfinite(up):
        raise ValueError(f"up must be a compass bearing in degrees, got {up}")
    up = normalise_bearing(up)
    beside_up = up if beside_up is None else beside_up
    if not math.isfinite(beside_up):
        raise ValueError(f"beside_up must be a compass bearing in degrees, got {beside_up}")

    # Resolve output path and create its folder now, not after all the work
    if output_dir:
        output_filename = os.path.join(output_dir, output_filename)
    os.makedirs(os.path.dirname(os.path.abspath(output_filename)), exist_ok=True)

    logger.info("Setting up the map centred at lat=%.4f, lon=%.4f", lat, lon)

    # Step 1: Initialize the requested image tile source
    tile_kwargs = tile_kwargs or {}
    if radius_km is not None:
        tile_buffer_factor = min(tile_buffer_factor, RADIUS_TILE_BUFFER)
    tiles = create_tile_source(
        tile_provider=tile_provider,
        tile_buffer_factor=tile_buffer_factor,
        cache_dir=tile_cache_dir,
        **tile_kwargs,
    )

    # Steps 2-4: One full-hemisphere Orthographic globe per centre: the
    # requested point and, beside it, its antipode (both_hemispheres) or
    # another place (beside).
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
    elif beside is not None:
        centres.append((beside[0], beside[1], normalise_bearing(beside_up)))
        logger.info("Adding a second globe, centred at lat=%.4f, lon=%.4f", *beside)
    fig = Figure(figsize=(20 * len(centres), 20))
    if radius_km is not None:
        logger.info("Showing %s km around the centre%s", f"{radius_km:,g}",
                    f", cut to a {rectangle:.3g}:1 rectangle" if rectangle is not None else "")
    axes = [
        _add_globe_axes(fig, centre_lat, centre_lon, index, len(centres), centre_up, radius_km, rectangle)
        for index, (centre_lat, centre_lon, centre_up) in enumerate(centres)
    ]
    near = axes[0]
    far = axes[1] if both_hemispheres else None

    # Keys go under the middle of the row, given in the first globe's axes coordinates
    if len(axes) == 1:
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

    sea_colour, land_colour = FALLBACK_COLOURS.get(tile_provider.lower(), (background_color, "#f1efe6"))
    for ax in axes:
        # Fallback land/ocean so blank areas aren't white
        ax.add_feature(cfeature.OCEAN, facecolor=sea_colour, edgecolor="none", zorder=0)
        ax.add_feature(cfeature.LAND, facecolor=land_colour, edgecolor="none", zorder=0)

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
        # A radius map gets terrain as fine as its map tiles (up to zoom 10)
        relief_zoom = RELIEF_ZOOM if radius_km is None else max(RELIEF_ZOOM, min(zoom, MAX_RELIEF_ZOOM))
        logger.info("Adding elevation (alpha=%.2f, zoom %d) …", elevation_alpha, relief_zoom)
        relief = BufferedTileSource(
            TerrariumTiles(cache_dir=tile_cache_dir, use_cache=tile_cache_dir is not None),
            tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(relief_rgba, alpha=elevation_alpha, cache_dir=tile_cache_dir,
                                          use_cache=tile_cache_dir is not None, zoom=relief_zoom),
        )
        for ax in axes:
            ax.add_image(relief, relief_zoom, regrid_shape=regrid_shape, interpolation="bilinear", zorder=1)

    # Step 5a1: The sea floor by depth and the last ice age's coast, from the same terrain tiles
    ocean = None
    if ocean_depth or ice_age:
        ocean_zoom = RELIEF_ZOOM if radius_km is None else max(RELIEF_ZOOM, min(zoom, MAX_RELIEF_ZOOM))
        logger.info("Adding the %s (alpha=%.2f, zoom %d) …",
                    " and ".join(n for n, on in (("ocean depth", ocean_depth), ("ice-age coast", ice_age)) if on),
                    elevation_alpha, ocean_zoom)
        ocean = BufferedTileSource(
            TerrariumTiles(cache_dir=tile_cache_dir, use_cache=tile_cache_dir is not None),
            tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(ocean_rgba, alpha=elevation_alpha, depth=ocean_depth, ice_age=ice_age,
                                          zoom=ocean_zoom),
        )
        for ax in axes:
            ax.add_image(ocean, ocean_zoom, regrid_shape=regrid_shape, interpolation="nearest", zorder=1)
    # ... and the ice sheets of the time, over the land they covered and the shelves they grounded on
    ice_sheets_drawn = False
    if ice_age:
        try:
            sheets = load_ice_sheets()
            for ax in axes:
                draw_ice_sheets(ax, sheets, regrid_shape=regrid_shape)
        except (IceAgeDataError, OSError) as e:
            logger.warning("Skipping the ice-age ice sheets: %s", e)
        else:
            ice_sheets_drawn = True

    # Step 5a2: Vegetation, land cover or NDVI (above the elevation, below the climate
    # colours); NASA GIBS tiles, fetched in savefig like the map tiles
    vegetation = None
    vegetation_credit = None
    if land_cover:
        year = land_cover_year or LATEST_LAND_COVER_YEAR
        logger.info("Adding MODIS land cover %d (alpha=%.2f) …", year, vegetation_alpha)
        vegetation = BufferedTileSource(
            land_cover_tiles(year, tile_cache_dir, use_cache=tile_cache_dir is not None),
            tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(land_cover_rgba, alpha=vegetation_alpha,
                                          classes=land_cover_codes or None),
        )
        vegetation_credit = land_cover_attribution(year)
    elif ndvi_month:
        logger.info("Adding MODIS NDVI for %d-%02d (alpha=%.2f) …", *ndvi_month, vegetation_alpha)
        vegetation = BufferedTileSource(
            ndvi_tiles(*ndvi_month, tile_cache_dir, use_cache=tile_cache_dir is not None),
            tile_buffer_factor=tile_buffer_factor,
            postprocess=functools.partial(ndvi_rgba, alpha=vegetation_alpha),
        )
        vegetation_credit = ndvi_attribution(*ndvi_month)
    if vegetation is not None:
        # A radius map gets finer tiles, up to the layer's deepest zoom
        vegetation_zoom = VEGETATION_ZOOM if radius_km is None else max(
            VEGETATION_ZOOM, min(zoom, vegetation.tile_source.max_zoom))
        for ax in axes:
            ax.add_image(vegetation, vegetation_zoom, regrid_shape=regrid_shape,
                         interpolation="nearest", zorder=2)

    # Step 5a2b: Population density (NASA GIBS tiles, fetched in savefig)
    people = None
    if population_year is not None:
        logger.info("Adding the population density of %d (alpha=%.2f) …", population_year, population_alpha)
        try:
            colormap = load_colormap(population_year, tile_cache_dir)
        except (PopulationDataError, OSError) as e:
            logger.warning("Skipping the population density: %s", e)
        else:
            people = BufferedTileSource(
                population_tiles(population_year, tile_cache_dir, use_cache=tile_cache_dir is not None),
                tile_buffer_factor=tile_buffer_factor,
                postprocess=functools.partial(population_rgba, alpha=population_alpha, colormap=colormap),
            )
            people_zoom = POPULATION_ZOOM if radius_km is None else max(
                POPULATION_ZOOM, min(zoom, people.tile_source.max_zoom))
            for ax in axes:
                ax.add_image(people, people_zoom, regrid_shape=regrid_shape, interpolation="nearest", zorder=2)

    # Step 5a3: Biomes or ecoregions (above the vegetation, below the climate colours)
    biome_layer = None
    if biomes:
        what = colour_by + "s"
        logger.info("Applying %s (alpha=%.2f) …", what, biome_alpha)
        try:
            biome_layer = load_ecoregions()
            for ax in axes:
                add_ecoregion_overlay(ax, biome_layer, alpha=biome_alpha, regrid_shape=regrid_shape,
                                      classes=biome_codes or None, by=colour_by)
        except (BiomeDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping %s: %s", what, e)
            biome_layer = None

    # Holt et al.'s zoogeographic realms in the same place, and their regions outlined
    holt = None
    holt_drawn = False
    if holt_realms or realm_regions:
        logger.info("Applying Holt et al.'s zoogeographic %s …",
                    "realms" + (" and regions" if realm_regions else "") if holt_realms else "regions")
        try:
            holt = load_holt()
            for ax in axes:
                if holt_realms:
                    add_holt_realm_overlay(ax, holt, alpha=biome_alpha, regrid_shape=regrid_shape)
                if realm_regions:
                    draw_holt_regions(ax, holt)
        except (HoltDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping the zoogeographic realms: %s", e)
            holt = None
        else:
            holt_drawn = True

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
    # Temperatures over the sea: the air's, carrying the land's temperature out
    # over it, or the sea surface's, which joins the land's temperature in one
    # image and key when they share a period
    sea_grid = None
    sea_credit = None
    if temperature_over_sea and climate_mean is not None:
        logger.info("Adding the air temperature over the sea …")
        try:
            sea_grid = load_sea_air(climate_mean[1])
        except (SeaDataError, OSError) as e:
            logger.warning("Skipping the air temperature over the sea: %s", e)
        else:
            sea_credit = SEA_AIR_ATTRIBUTION
    elif sea_period is not None:
        logger.info("Adding the sea surface temperature for %s …", sea_period.label)
        try:
            sea_grid = load_sea_temperature(sea_period)
        except (SeaDataError, OSError) as e:
            logger.warning("Skipping the sea surface temperature: %s", e)
        else:
            sea_credit = SEA_TEMPERATURE_ATTRIBUTION
    sea_with_land = (sea_grid is not None and climate_mean is not None and climate_mean[0].key == "temperature"
                     and (temperature_over_sea or climate_mean[1] == sea_period))

    climate_mean_drawn = False
    if climate_mean is not None:
        variable, period = climate_mean
        logger.info("Applying %s for %s (alpha=%.2f) …", variable.title.lower(), period.label, koppen_alpha)
        try:
            for ax in axes:
                add_climate_overlay(ax, variable, period, alpha=koppen_alpha, regrid_shape=regrid_shape,
                                    zoom=zoom, sea=sea_grid if sea_with_land else None)
        except (ClimateDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping %s: %s", variable.title.lower(), e)
            sea_with_land = False                    # the sea is drawn on its own below
        else:
            climate_mean_drawn = True
    sea_key_period = sea_period or (climate_mean[1] if climate_mean is not None else None)
    if sea_grid is not None and not sea_with_land and sea_key_period is not None:
        alpha = koppen_alpha if temperature_over_sea else sea_alpha
        for ax in axes:
            add_sea_overlay(ax, sea_grid, sea_key_period, alpha=alpha, regrid_shape=regrid_shape, zoom=zoom)

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

    # Step 5b3: Historical borders (above the soil, below crops, ice and routes)
    polities = None
    border_colours: dict[str, tuple[int, int, int]] = {}
    border_counts: list[dict[int, int]] = []
    if border_when is not None and border_source is not None:
        logger.info("Drawing the borders of %s (%s, alpha=%.2f) …", border_when.label, border_source, borders_alpha)
        try:
            polities = load_polities(border_when, border_source)
            border_colours = colour_groups(polities) if polities else {}
            border_counts = [draw_borders(ax, polities, border_colours, alpha=borders_alpha) for ax in axes]
        except (BordersError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping the borders: %s", e)
            polities = None

    # Step 5b4: River basins (like the borders), time zones, livestock and permafrost
    basin_list = None
    basin_colour_map: dict[str, tuple[int, int, int]] = {}
    basin_counts: list[dict[int, int]] = []
    if basins:
        logger.info("Drawing the river basins (alpha=%.2f) …", basins_alpha)
        try:
            basin_list = load_basins()
            basin_colour_map = basin_colours(basin_list)
            basin_counts = [draw_basins(ax, basin_list, basin_colour_map, alpha=basins_alpha) for ax in axes]
        except (HydrologyDataError, OSError) as e:
            logger.warning("Skipping the river basins: %s", e)
            basin_list = None
    zones_drawn = False
    if time_zones:
        try:
            zones = load_time_zones()
            for ax in axes:
                draw_time_zones(ax, zones)
        except (TimeZoneDataError, OSError) as e:
            logger.warning("Skipping the time zones: %s", e)
        else:
            zones_drawn = True
    livestock_drawn = False
    if livestock_species is not None:
        try:
            herd = load_livestock(livestock_species)
            for ax in axes:
                add_livestock_overlay(ax, herd, alpha=livestock_alpha, regrid_shape=regrid_shape)
        except (LivestockDataError, OSError) as e:
            logger.warning("Skipping the livestock: %s", e)
        else:
            livestock_drawn = True
    malaria_drawn = False
    if malaria_parasite is not None:
        try:
            parasites = load_malaria(malaria_parasite, malaria_year)
            for ax in axes:
                add_malaria_overlay(ax, parasites, alpha=malaria_alpha, regrid_shape=regrid_shape)
        except (MalariaDataError, OSError) as e:
            logger.warning("Skipping the malaria map: %s", e)
        else:
            malaria_drawn = True
    mosquitoes = []
    for species in aedes_species:
        try:
            mosquitoes.append(load_aedes(species))
        except (AedesDataError, OSError) as e:
            logger.warning("Skipping the Aedes %s records: %s", species, e)
    if mosquitoes:
        for ax in axes:
            draw_aedes(ax, mosquitoes)
    if species_range is not None:
        for ax in axes:
            draw_range(ax, species_range, alpha=iucn_alpha)
    stat_breaks: list[float] = []
    stat_counts = None
    if statistic is not None:
        try:
            nations = load_areas(statistic)
        except Exception as e:                 # Natural Earth, GISCO and shapefile errors alike
            logger.warning("Skipping the statistics: could not read the countries or regions: %s", e)
            statistic = None
        else:
            stat_breaks = class_breaks(list(statistic.values.values()), bool(statistic.indicator.log))
            stat_counts = [draw_indicator(ax, statistic, nations, stat_breaks, alpha=indicator_alpha)
                           for ax in axes][0]
    ships_drawn = False
    if ship_kind is not None:
        try:
            for ax in axes:
                add_shipping_overlay(ax, ship_kind, alpha=shipping_alpha, regrid_shape=regrid_shape)
        except (ShippingDataError, OSError) as e:
            logger.warning("Skipping the ship traffic: %s", e)
        else:
            ships_drawn = True
    factories: list = []
    factory_counts: dict[str, int] = {}
    for sector in industry_sectors:
        try:
            factories.append(load_plants(sector))
        except (IndustryDataError, OSError) as e:
            logger.warning("Skipping the %s: %s", INDUSTRY_SECTORS[sector].label.lower(), e)
    # Cities are named first, then plants, ports and relief units where their names still fit
    towns = None
    town_floor = cities_min if cities_min is not None else default_min_population(radius_km)
    towns_shown = 0
    if cities_year is not None:
        try:
            towns = load_cities(cities_year)
        except (CitiesDataError, OSError) as e:
            logger.warning("Skipping the cities: %s", e)
        else:
            towns_shown = max(len(draw_cities(ax, towns, town_floor)) for ax in axes)
    if factories:
        factory_counts = [draw_industry(ax, factories) for ax in axes][0]
    harbours_shown = 0
    if harbours is not None:
        harbours_shown = max(draw_ports(ax, harbours) for ax in axes)
    landform_counts: list[dict[int, int]] = []
    if landforms is not None:
        landform_counts = [draw_relief(ax, landforms, alpha=relief_alpha) for ax in axes]
    sightings = None
    if taxon is not None:
        try:
            sightings = load_occurrences(taxon, record_years)
        except (GbifDataError, OSError) as e:
            logger.warning("Skipping the records of %s: %s", taxon.name, e)
        else:
            if not len(sightings.records):
                logger.warning("GBIF has no wild records of %s%s.", taxon.name,
                               f" in {species_years}" if record_years else "")
                sightings = None
    if sightings is not None:
        for ax in axes:
            draw_occurrences(ax, sightings)
    permafrost_drawn = False
    if permafrost:
        try:
            frozen = load_permafrost()
            for ax in axes:
                add_permafrost_overlay(ax, frozen, alpha=permafrost_alpha, regrid_shape=regrid_shape)
        except (PermafrostDataError, OSError) as e:
            logger.warning("Skipping the permafrost: %s", e)
        else:
            permafrost_drawn = True
    rivers_drawn = False
    if rivers:
        try:
            river_list = load_rivers()
            for ax in axes:
                draw_rivers(ax, river_list)
        except (HydrologyDataError, OSError) as e:
            logger.warning("Skipping the rivers: %s", e)
        else:
            rivers_drawn = True

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

    # Step 5e: Prevailing winds (above the ice, below routes and distance circles)
    wind_drawn = False
    if wind_period is not None:
        logger.info("Adding the prevailing winds for %s …", wind_period.label)
        try:
            for ax in axes:
                add_wind_overlay(ax, wind_period)
        except (WindDataError, OSError) as e:
            # Missing data should not throw away the rest of the map
            logger.warning("Skipping prevailing winds: %s", e)
        else:
            wind_drawn = True

    # Step 5f: Ocean currents, cyclone tracks and plate tectonics (above the colours, below routes)
    currents_drawn = False
    if currents:
        try:
            field = load_currents()
            for ax in axes:
                add_currents_overlay(ax, field)
        except (CurrentsDataError, OSError) as e:
            logger.warning("Skipping the ocean currents: %s", e)
        else:
            currents_drawn = True
    cyclones_drawn = False
    if cyclones is not None:
        try:
            tracks = load_tracks(cyclones)
            for ax in axes:
                draw_tracks(ax, tracks)
        except (CycloneDataError, OSError) as e:
            logger.warning("Skipping the cyclone tracks: %s", e)
        else:
            cyclones_drawn = True
    plates_drawn = False
    if plates:
        try:
            boundaries = load_plates()
            for ax in axes:
                draw_plates(ax, boundaries)
        except (TectonicsDataError, OSError) as e:
            logger.warning("Skipping the plate boundaries: %s", e)
        else:
            plates_drawn = True
    quakes = None
    if earthquakes is not None:
        try:
            quakes = load_quakes(earthquakes)
            for ax in axes:
                draw_quakes(ax, quakes)
        except (TectonicsDataError, OSError) as e:
            logger.warning("Skipping the earthquakes: %s", e)
            quakes = None
    volcanoes_drawn = False
    if volcanoes:
        try:
            vents = load_volcanoes()
            for ax in axes:
                draw_volcanoes(ax, vents)
        except (TectonicsDataError, OSError) as e:
            logger.warning("Skipping the volcanoes: %s", e)
        else:
            volcanoes_drawn = True

    # Step 6: Gridlines
    for ax in axes:
        ax.gridlines(draw_labels=False, color='black', alpha=0.3, linestyle='--')

    # Step 7: City markers & labels, and the antipode on the far globe
    named = [(near, lat, lon, city_name)]
    if beside is not None:
        named.append((axes[1], beside[0], beside[1], beside_name))
    for ax, place_lat, place_lon, name in named:
        if not name:
            continue
        ax.plot(
            place_lon, place_lat,
            marker="o", markersize=10, markeredgewidth=2,
            color="#e74c3c", markeredgecolor="white",
            transform=ccrs.PlateCarree(), zorder=10,
        )
        ax.text(
            place_lon, place_lat, f"  {name}",
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

    # Step 7b: Concentric distance circles, measured from the requested point
    # or from *circles_from*: 2 500 and 5 000 km on its globe, or round steps
    # across a radius map; 17 500 and 15 000 km on the far globe. A globe
    # beside the first gets its own, from its centre or *circles_from*.
    ring_globes = [(near, lat, lon)] if distance_circles else []
    if beside is not None and distance_circles:
        ring_globes.append((axes[1], beside[0], beside[1]))
    for ax, centre_lat, centre_lon in ring_globes:
        ring_lat, ring_lon = circles_from if circles_from is not None else (centre_lat, centre_lon)
        if radius_km is not None:
            # The map reaches this far from the circles' centre at most
            offset_km = Geodesic().inverse((centre_lon, centre_lat), (ring_lon, ring_lat))[0, 0] / 1_000
            _draw_distance_circles(ax, ring_lon, ring_lat, within=True,
                                   radii_km=ring_radii(min(radius_km + offset_km, 19_000)))
        else:
            _draw_distance_circles(ax, ring_lon, ring_lat)
    ring_lat, ring_lon = circles_from if circles_from is not None else (lat, lon)
    if far is not None and distance_circles:
        _draw_distance_circles(far, ring_lon, ring_lat, radii_km=FAR_SIDE_RADII_KM)
    if circles_from is not None and distance_circles:
        for ax in axes:  # a hollow ring where the circles are measured from
            ax.plot(ring_lon, ring_lat, marker="o", markersize=8, markeredgewidth=2,
                    markerfacecolor="none", markeredgecolor="white",
                    transform=ccrs.PlateCarree(), zorder=10)

    # Step 7c: Route overlays
    flights_shown = 0
    if flights is not None:
        flights_shown = max(draw_air_routes(ax, flights) for ax in axes)
    if routes:
        for ax in axes:
            draw_routes(ax, routes)

    # Step 7d: Keys below the globe(s): the elevation, soil, crop and route
    # keys, under the climate key when that is drawn too
    keys = []
    if climate_mean_drawn and climate_mean is not None:
        joined_surface = sea_with_land and not temperature_over_sea
        keys.append(add_climate_legend(near, *climate_mean, x=key_x,
                                       title=land_and_sea_legend_title(climate_mean[1]) if joined_surface else None))
    if sea_grid is not None and not sea_with_land and sea_key_period is not None:
        keys.append(add_climate_legend(near, CLIMATE_VARIABLES["temperature"], sea_key_period, x=key_x,
                                       title=sea_legend_title(sea_key_period) if sea_period else None))
    if relief is not None:
        keys.append(add_elevation_legend(near, x=key_x))
    if ocean is not None:
        keys.append(add_ocean_legend(near, depth=ocean_depth, ice_age=ice_age, ice_sheets=ice_sheets_drawn, x=key_x))
    if people is not None and population_year is not None:
        keys.append(add_population_legend(near, population_year, x=key_x))
    if land_cover:
        keys.append(add_land_cover_legend(near, land_cover_year or LATEST_LAND_COVER_YEAR,
                                          classes=land_cover_codes or None, x=key_x))
    elif ndvi_month:
        keys.append(add_ndvi_legend(near, *ndvi_month, x=key_x))
    if biome_layer is not None:
        add_key = {"biome": add_biome_legend, "ecoregion": add_ecoregion_legend,
                   "realm": add_realm_legend}[colour_by]
        keys.append(add_key(near, visible_ecoregions(axes, biome_layer), biome_layer, x=key_x,
                            classes=biome_codes or None))
    if holt is not None and holt_realms:
        keys.append(add_holt_realm_legend(near, visible_values(axes, holt.realms), x=key_x))
    if soil_drawn:
        keys.append(add_soil_legend(near, x=key_x, classes=soil_codes or None))
    if property_drawn and prop is not None and prop_depth is not None:
        keys.append(add_soil_property_legend(near, prop, prop_depth, x=key_x))
    if polities and border_when is not None and border_source is not None:
        keys.append(add_borders_legend(near, polities, border_counts, border_colours, border_when, border_source,
                                       x=key_x))
    if basin_list:
        keys.append(add_basins_legend(near, basin_list, basin_counts, basin_colour_map, x=key_x))
    if permafrost_drawn:
        keys.append(add_permafrost_legend(near, x=key_x))
    if livestock_drawn and livestock_species is not None:
        keys.append(add_livestock_legend(near, livestock_species, x=key_x))
    if malaria_drawn and malaria_parasite is not None:
        keys.append(add_malaria_legend(near, malaria_parasite, malaria_year, x=key_x))
    if mosquitoes:
        keys.append(add_aedes_legend(near, mosquitoes, x=key_x))
    if species_range is not None:
        keys.append(add_range_legend(near, species_range, alpha=iucn_alpha, x=key_x))
    if statistic is not None and stat_counts is not None:
        keys.append(add_indicator_legend(near, statistic, stat_breaks, stat_counts, alpha=indicator_alpha,
                                         x=key_x))
    if towns is not None:
        keys.append(add_cities_legend(near, towns, town_floor, towns_shown, x=key_x))
    if factories:
        keys.append(add_industry_legend(near, factories, factory_counts, x=key_x))
    if ships_drawn and ship_kind is not None:
        keys.append(add_shipping_legend(near, ship_kind, alpha=shipping_alpha, x=key_x))
    if harbours is not None:
        keys.append(add_ports_legend(near, harbours, harbours_shown, x=key_x))
    if landforms is not None:
        keys.append(add_relief_legend(near, landforms, landform_counts, x=key_x))
    if flights is not None:
        keys.append(add_air_routes_legend(near, flights, flights_shown, x=key_x))
    if sightings is not None:
        keys.append(add_occurrences_legend(near, sightings, x=key_x))
    if crop_layer is not None:
        keys.append(add_crop_legend(near, crop_layer, x=key_x))
    if wind_drawn and wind_period is not None:
        keys.append(add_wind_legend(near, wind_period, x=key_x))
    if currents_drawn:
        keys.append(add_currents_legend(near, x=key_x))
    if cyclones_drawn and cyclones is not None:
        keys.append(add_cyclones_legend(near, cyclones, x=key_x))
    if plates_drawn or quakes is not None or volcanoes_drawn:
        keys.append(add_tectonics_legend(near, plates_drawn, quakes, volcanoes_drawn, x=key_x))
    if routes and route_legend:
        keys.append(add_route_legend(near, routes, x=key_x))
    _stack_keys(near, [k for k in keys if k is not None], top=-0.07 if climate_credits else -0.01, x=key_x)

    # Step 7e: Data credits required by the tile and dataset licences
    credits = tile_attribution_lines(tiles, tile_provider, zoom)
    if vegetation_credit:
        credits.append(vegetation_credit)
    if biome_layer is not None:
        credits.append({"biome": BIOME_ATTRIBUTION, "ecoregion": ECOREGION_ATTRIBUTION,
                        "realm": REALM_ATTRIBUTION}[colour_by])
    if holt_drawn:
        credits.append(HOLT_ATTRIBUTION)
    if climate_mean_drawn:
        climate_credits.append(CLIMATE_ATTRIBUTION)
    if sea_credit:
        climate_credits.append(sea_credit)
    if people is not None and population_year is not None:
        credits.append(population_attribution(population_year))
    if ice_sheets_drawn:
        credits.append(ICE_SHEETS_ATTRIBUTION)
    for credit in climate_credits + ([ELEVATION_ATTRIBUTION] if relief is not None or ocean is not None else []):
        if credit not in credits:
            credits.append(credit)
    if soil_drawn:
        credits.append(SOIL_ATTRIBUTION)
    if property_drawn:
        credits.append(SOIL_PROPERTY_ATTRIBUTION)
    if polities is not None and border_source is not None:
        credits.append(borders_attribution(border_source))
    if basin_list:
        credits.append(BASINS_ATTRIBUTION)
    if rivers_drawn:
        credits.append(RIVERS_ATTRIBUTION)
    if zones_drawn:
        credits.append(TIMEZONES_ATTRIBUTION)
    if permafrost_drawn:
        credits.append(PERMAFROST_ATTRIBUTION)
    if livestock_drawn:
        credits.append(LIVESTOCK_ATTRIBUTION)
    if malaria_drawn:
        credits.append(MALARIA_ATTRIBUTION)
    if mosquitoes:
        credits.append(AEDES_ATTRIBUTION)
    if species_range is not None:
        credits.append(iucn_attribution(species_range))
    if statistic is not None:
        credits.append(indicator_attribution(statistic))
    if towns is not None:
        credits.append(CITIES_ATTRIBUTION)
    if factories:
        credits.append(industry_attribution(factories))
    if ships_drawn:
        credits.append(SHIPPING_ATTRIBUTION)
    if harbours is not None:
        credits.append(PORTS_ATTRIBUTION)
    if landforms is not None:
        credits.append(RELIEF_ATTRIBUTION)
    if flights is not None:
        credits.append(AIR_ROUTES_ATTRIBUTION)
    if sightings is not None:
        credits.append(gbif_attribution(sightings))
    if crop_layer is not None:
        credits.append(CROP_ATTRIBUTION)
    if ice_attribution:
        credits.append(ice_attribution)
    if wind_drawn:
        credits.append(WIND_ATTRIBUTION)
    if currents_drawn:
        credits.append(CURRENTS_ATTRIBUTION)
    if cyclones_drawn:
        credits.append(CYCLONES_ATTRIBUTION)
    credits += tectonics_credits(plates_drawn, quakes is not None, volcanoes_drawn)
    _add_attribution(axes[-1], credits)

    # Step 8: Export
    logger.info("Fetching '%s' tiles at zoom %d and saving to '%s' at %d DPI …",
                tile_provider, zoom, output_filename, dpi)
    fig.savefig(output_filename, dpi=dpi, bbox_inches="tight", transparent=True)
    _report_tile_failures(tiles)
    if vegetation is not None and vegetation.failed_tiles:
        logger.warning("%d of %d vegetation tiles could not be downloaded (first error: %s).",
                       len(vegetation.failed_tiles), vegetation.total_tiles, vegetation.failed_tiles[0][1])
    for layer, what in ((ocean, "terrain (ocean)"), (people, "population")):
        if layer is not None and layer.failed_tiles:
            logger.warning("%d of %d %s tiles could not be downloaded (first error: %s).",
                           len(layer.failed_tiles), layer.total_tiles, what, layer.failed_tiles[0][1])
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
    radius_km: float | None = None, rectangle: float | None = None,
) -> GeoAxes:
    """Add globe *index* of *count* to *fig*, centred on (*lat*, *lon*), bearing *up* at the top.

    With *radius_km*, the axes show only the disc within that distance of the
    centre, filling the same frame the whole globe would. With *rectangle*
    too (width over height), they show the rectangle cut from that frame
    instead, its longer side the disc's diameter.
    """
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
    if radius_km is not None:
        # A circle on the ground around the centre is a circle on an orthographic
        # globe, of radius R·sin(d / R): zoom to its square and clip to the circle
        half = proj.x_limits[1] * math.sin(radius_km * 1_000 / EARTH_RADIUS_M)
        if rectangle is not None:
            half_w, half_h = half_sides(half, rectangle)
            ax.set_extent((-half_w, half_w, -half_h, half_h), crs=proj)
            ax.set_boundary(mpath.Path([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)], closed=True),
                            transform=ax.transAxes)
            set_rectangle(ax)
            # Shrink the square frame to the rectangle now rather than when drawn, so the names' room and the
            # keys' places, measured in pixels as the layers go, are measured on the map as it will be
            ax.apply_aspect()
            return ax
        ax.set_extent((-half, half, -half, half), crs=proj)
        # A many-sided polygon: Cartopy keeps only the vertices of a curved path
        theta = np.linspace(0, 2 * np.pi, 721)
        ring = np.column_stack([0.5 + 0.5 * np.cos(theta), 0.5 + 0.5 * np.sin(theta)])
        ax.set_boundary(mpath.Path(ring, closed=True), transform=ax.transAxes)
    return ax


def ring_radii(radius_km: float) -> tuple[float, ...]:
    """Distance circles for a radius map: round steps inside its edge, two to four of them."""
    rough = radius_km / 3
    magnitude = 10 ** math.floor(math.log10(rough))
    step = max(m * magnitude for m in (1, 2, 2.5, 5) if m * magnitude <= rough)
    count = math.ceil(radius_km / step) - 1                 # a ring on the edge would hide it
    return tuple(step * i for i in range(1, count + 1))



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

    On a globe that corner lies outside the disc, so the text never covers map
    content; a rectangle has map there, so the credits get a pale backing.
    ``clip_on=False`` stops GeoAxes clipping it to the globe.
    """
    rectangle = is_rectangle(ax)
    credit = ax.text(
        0.995 if rectangle else 1.0, 0.005 if rectangle else 0.0, "\n".join(credits),
        transform=ax.transAxes,
        ha="right", va="bottom", multialignment="right",
        fontsize=8, color="#555555" if rectangle else "#888888", style="italic",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="none", alpha=0.75) if rectangle else None,
        clip_on=False, zorder=10,
    )
    if rectangle:
        _clear_names_under(ax, credit)


def _clear_names_under(ax: GeoAxes, credit: Any) -> None:
    """Leave out the names the *credit* lines' backing would cover, half hidden, in a rectangle's corner, as names
    that would run off the map are; the credits come last, once every layer has placed its names."""
    renderer = ax.figure._get_renderer()  # pyright: ignore[reportAttributeAccessIssue]  # as matplotlib measures text
    pad = 0.4 * credit.get_fontsize() * ax.figure.dpi / 72
    box = credit.get_window_extent(renderer).padded(pad)
    for text in ax.texts:
        if text is credit or not text.get_visible() or not text.get_text():
            continue
        if isinstance(text, Annotation):
            # Undrawn, an annotation has no place yet (its extent is an empty box): place it, then measure its text
            text.update_positions(renderer)
        if Text.get_window_extent(text, renderer).overlaps(box):
            text.set_visible(False)


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
    within: bool = False,
) -> None:
    """Draw concentric geodesic circles on *ax* at the given radii.

    The ring vertices are projected straight into the axes' own projection
    and drawn as a closed line there. Going through lon/lat polygons breaks
    whenever a circle encloses a pole: the ring then spans all 360° of
    longitude and has to cross the CRS seam, producing a spurious edge.

    With *within*, for a radius map, a ring's label goes on the part of it
    inside the map's disc or rectangle, and a ring wholly outside has none.
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
        visible = np.vstack(runs)
        # Place a small label at the top of the circle as drawn on the map
        ha, va = "center", "bottom"
        if within and is_rectangle(ax):
            # The ring's top, or where it runs off the top edge its east side; else none
            top, east = visible[int(np.argmax(visible[:, 1]))], visible[int(np.argmax(visible[:, 0]))]
            if inside(ax, *top, 0.96):
                label_x, label_y = top
            elif inside(ax, *east, 0.9):
                (label_x, label_y), ha, va = east, "left", "center"
            else:
                continue
        else:
            if within:
                visible = visible[inside(ax, visible[:, 0], visible[:, 1], 0.98)]
                if not len(visible):
                    continue
            label_x, label_y = visible[int(np.argmax(visible[:, 1]))]
        ax.text(
            label_x, label_y, f" {radius_km:,g} km",
            transform=proj,
            fontsize=9, color="white", alpha=alphas[idx % len(alphas)],
            fontweight="bold", va=va, ha=ha, zorder=10,
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
    radius_km: float | None = None, beside_slug: str | None = None, rectangle: float | None = None,
) -> str:
    """Build a descriptive output filename from the render parameters."""
    if beside_slug is not None:
        city_slug = f"{city_slug}_and_{beside_slug}"
    sanitized_provider = tile_provider.replace(" ", "_")
    suffix = "_hemispheres" if both_hemispheres else ""
    if radius_km is not None:
        suffix += f"_r{round(radius_km)}km"
    if rectangle is not None:
        suffix += f"_rect{rectangle:.3g}".replace(".", "p")
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


class NamedPlace(NamedTuple):
    """A place to centre a globe on: its name (``None`` for LAT,LON), file-name slug and label."""
    lat: float
    lon: float
    name: str | None
    slug: str
    label: str


def resolve_named_place(place: str) -> NamedPlace:
    """:func:`resolve_place`, with the city's name and slug, or ``"custom"`` for LAT,LON."""
    lat, lon = resolve_place(place)
    name = next((k for k in MAJOR_METROPOLISES if k.lower() == place.strip().lower()), None)
    if name is None:
        return NamedPlace(lat, lon, None, "custom", f"({lat}, {lon})")
    return NamedPlace(lat, lon, name, str(MAJOR_METROPOLISES[name]["slug"]), name)


def prompt_for_radius() -> float | None:
    """Prompt for the radius of a zoomed-in map; blank shows the whole hemisphere."""
    while True:
        raw = input(
            f"Show only a radius around the centre, in km ({MIN_RADIUS_KM:,}-{MAX_RADIUS_KM:,}; "
            f"'y' for {DEFAULT_RADIUS_KM:,}) [default: whole hemisphere]: "
        ).strip().lower().replace(",", "")
        if not raw or raw in ("n", "no"):
            return None
        if raw in ("y", "yes"):
            return float(DEFAULT_RADIUS_KM)
        try:
            radius = float(raw)
        except ValueError:
            radius = math.nan
        if math.isfinite(radius) and MIN_RADIUS_KM <= radius <= MAX_RADIUS_KM:
            return radius
        print(f"Please enter a distance between {MIN_RADIUS_KM:,} and {MAX_RADIUS_KM:,} km.\n")


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


# The extra layers the interactive prompt offers, and what each turns on
EXTRA_LAYERS: dict[str, tuple[str, Any]] = {
    "ocean-depth": ("ocean_depth", True), "ice-age": ("ice_age", True), "currents": ("currents", True),
    "plates": ("plates", True), "earthquakes": ("earthquakes", DEFAULT_MIN_MAGNITUDE),
    "volcanoes": ("volcanoes", True), "rivers": ("rivers", True), "basins": ("basins", True),
    "cyclones": ("cyclones", FIRST_CYCLONE_YEAR), "time-zones": ("time_zones", True),
    "permafrost": ("permafrost", True), "population": ("population", LATEST_POPULATION_YEAR),
    "malaria": ("malaria", "pf"), "aedes": ("aedes", "both"), "cities": ("cities", 2025),
    "shipping": ("shipping", "commercial"), "ports": ("ports", "all"), "air-routes": ("air_routes", "all"),
    "relief-units": ("relief_units", "units"),
}


def parse_extra_layers(text: str) -> dict[str, Any]:
    """The keyword arguments for a comma-separated list of :data:`EXTRA_LAYERS`.

    ``livestock:ANIMAL``, ``malaria:pf`` or ``malaria:pv`` and ``aedes:SPECIES`` choose
    which kind; ``species:NAME`` draws the GBIF records of a species and
    ``iucn:NAME`` its IUCN range.

    Raises ``ValueError`` for a name it does not know.
    """
    chosen: dict[str, Any] = {}
    for item in (part.strip().lower() for part in text.split(",")):
        if not item:
            continue
        if item.startswith("livestock:"):
            chosen["livestock"] = resolve_species(item.split(":", 1)[1])
        elif item.startswith("malaria:"):
            chosen["malaria"] = resolve_parasite(item.split(":", 1)[1])
        elif item.startswith("species:"):
            name = item.split(":", 1)[1].strip()
            if not name:
                raise ValueError("Name the species after 'species:', e.g. species:red fox.")
            chosen["species"] = name
        elif item.startswith("iucn:"):
            name = item.split(":", 1)[1].strip()
            if not name:
                raise ValueError("Name the species after 'iucn:', e.g. iucn:Panthera leo.")
            chosen["iucn"] = name
        elif item.startswith("indicator:"):
            name = item.split(":", 1)[1].strip()
            resolve_indicator(name)
            chosen["indicator"] = name
        elif item.startswith("industry:"):
            sectors = item.split(":", 1)[1].strip()
            resolve_sectors(sectors)
            chosen["industry"] = sectors
        elif item.startswith("shipping:"):
            chosen["shipping"] = resolve_ship_kind(item.split(":", 1)[1])
        elif item.startswith("ports:"):
            chosen["ports"] = resolve_port_kind(item.split(":", 1)[1])
        elif item.startswith("relief-units:"):
            chosen["relief_units"] = resolve_relief_level(item.split(":", 1)[1])
        elif item.startswith("air-routes:"):
            spec = item.split(":", 1)[1].strip()
            resolve_air_routes(spec)
            chosen["air_routes"] = spec or "all"
        elif item.startswith("cities:"):
            chosen["cities"] = resolve_cities_year(item.split(":", 1)[1])
        elif item.startswith("aedes:"):
            kind = item.split(":", 1)[1].strip()
            resolve_aedes(kind)
            chosen["aedes"] = kind
        elif item in EXTRA_LAYERS:
            key, value = EXTRA_LAYERS[item]
            chosen[key] = value
        else:
            raise ValueError(f"Unknown layer {item!r}; choose from {', '.join(EXTRA_LAYERS)}, livestock:ANIMAL, "
                             "malaria:pv, aedes:SPECIES, species:NAME, iucn:NAME, cities:YEAR, indicator:NAME, industry:SECTORS, "
                             "shipping:KIND, ports:KIND, air-routes:AIRPORTS, relief-units:regions.")
    return chosen


def prompt_for_extra_layers() -> dict[str, Any]:
    """Prompt for the extra layers, comma-separated; blank for none."""
    while True:
        raw = input("More layers, comma-separated (" + ", ".join(EXTRA_LAYERS)
                    + ", livestock:cattle, malaria:pv, aedes:albopictus, species:red fox, iucn:lion, cities:1975, indicator:life-expectancy, industry:petrochemical, shipping:fishing, ports:container, air-routes:LHR, relief-units:regions …) [blank for none]: ")
        try:
            return parse_extra_layers(raw)
        except ValueError as e:
            print(f"{e}\n")


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


def _aspect(text: str) -> float:
    """``--rectangle``'s W:H as width over height."""
    try:
        return parse_aspect(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


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
        default=None,
        metavar="ZOOM",
        help=f"Tile zoom level: 1-{MAX_GLOBE_ZOOM} for the whole globe (default: {DEFAULT_ZOOM}); with --radius, "
             "up to one above its default, which gives about a dozen tiles across the map "
             "(e.g. 6 for 2,500 km at 45°).",
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
        help="Download map, terrain and vegetation tiles fresh, without reading or filling the tile cache.",
    )

    layout = parser.add_argument_group("Globe layout")
    layout.add_argument(
        "--radius",
        type=float,
        nargs="?",
        const=float(DEFAULT_RADIUS_KM),
        default=None,
        metavar="KM",
        help=f"Zoom in: show only the area within KM of the centre ({MIN_RADIUS_KM:,}-{MAX_RADIUS_KM:,}; "
             f"default when given without a value: {DEFAULT_RADIUS_KM:,}).",
    )
    layout.add_argument(
        "--rectangle",
        type=_aspect,
        default=None,
        metavar="W:H",
        help="With --radius, cut the map to a rectangle of this shape instead of a disc, to fill a page: "
             "3:2 or 4:3 for a wide one, 2:3 for a tall one, 1.414:1 for A4 landscape. Its longer side "
             "is the disc's diameter, so the corners show a little more.",
    )
    layout.add_argument(
        "--both-hemispheres",
        action="store_true",
        help="Draw a second globe centred on the antipode, showing the whole Earth "
             f"(max --dpi {MAX_DPI // 2}).",
    )
    layout.add_argument(
        "--beside",
        metavar="PLACE",
        help="Draw a second globe beside the first, centred on PLACE, a city name or LAT,LON; "
             f"with --radius, both show that radius (max --dpi {MAX_DPI // 2}).",
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
    layout.add_argument(
        "--circles-from",
        metavar="PLACE",
        help="Centre the distance circles on PLACE, a city name or LAT,LON, instead of the map's centre.",
    )
    layout.add_argument(
        "--no-circles",
        action="store_true",
        help="Leave out the distance circles.",
    )

    climate = parser.add_argument_group(
        "Climate (Köppen-Geiger, Trewartha or CHELSA means)",
        "One climate layer at a time: a classification, or the 1981-2010 mean temperature, "
        "precipitation or humidity for a PERIOD: year (the default), a season (djf, mam, jja, son) "
        "or a month (e.g. july or 7). --sea-temperature colours the sea alongside any of them.")
    system = climate.add_mutually_exclusive_group()
    for name, what in (("temperature", "mean air temperature"),
                       ("precipitation", "precipitation total"),
                       ("humidity", "mean relative humidity")):
        system.add_argument(
            f"--{name}",
            nargs="?",
            const="year",
            default=None,
            metavar="PERIOD",
            help=f"Colour the land by its {what} over PERIOD (CHELSA v2.1, 2-5 MB per month, cached).",
        )
    climate.add_argument(
        "--temperature-over-sea",
        action="store_true",
        help="With --temperature, colour the sea and lakes by the air temperature over them too "
             "(NCEP/NCAR Reanalysis 1, 1981-2010, a ~1.9° grid smoothed; one-time ~0.5 MB).",
    )
    climate.add_argument(
        "--sea-temperature",
        nargs="?",
        const="year",
        default=None,
        metavar="PERIOD",
        help="Colour the sea and lakes by their mean surface temperature over PERIOD (NOAA OISST v2.1, "
             "1991-2020, 0.25°; one-time ~46 MB). Goes with any climate layer but --temperature-over-sea.",
    )
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
        default=None,
        metavar="ALPHA",
        help="Opacity of the climate layer (0-1, default: 0.45 for a classification, "
             "0.7 for a temperature, precipitation or humidity mean).",
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
        help="Opacity of the elevation and ocean layers (0-1, default: 0.8).",
    )
    relief.add_argument(
        "--ocean-depth",
        action="store_true",
        help="Colour the sea floor by depth and shade it (the same terrain tiles), with a key.",
    )
    relief.add_argument(
        "--ice-age",
        action="store_true",
        help="Draw the last ice age: the land it left dry, when the sea stood ~120 m lower (Doggerland, "
             "Beringia, Sundaland), its coastline, and the ice sheets of 20,000 years ago (PaleoMIST).",
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

    eco = parser.add_argument_group("Biomes, ecoregions and realms (RESOLVE Ecoregions 2017)")
    eco.add_argument(
        "--biomes",
        action="store_true",
        help="Colour the land by its biome (14 biomes and Rock & Ice; one-time download of ~150 MB), "
             "with a key.",
    )
    eco.add_argument(
        "--biome-class",
        action="append",
        default=None,
        metavar="BIOME",
        help="Show only this biome, by number (1-14) or words from its name (e.g. mangroves, "
             "temperate, forest, rock); repeat for several. Implies --biomes.",
    )
    eco.add_argument(
        "--ecoregions",
        action="store_true",
        help="Colour each of the 846 ecoregions instead of their biomes (with --biome-class, only "
             "those biomes' ecoregions); the key names the largest on the map.",
    )
    eco.add_argument(
        "--realms",
        nargs="?",
        const="wwf",
        choices=["wwf", "holt"],
        metavar="SOURCE",
        help="Colour biogeographic realms instead of the biomes: wwf (the default) for WWF's eight "
             "(Nearctic, Palearctic, Indomalayan ...; with --biome-class, only in those biomes), or holt "
             "for the 11 zoogeographic realms of Holt et al. (2013), the update of Wallace's map "
             "(downloaded once from CMEC, ~18 MB). Not with --ecoregions.",
    )
    eco.add_argument(
        "--realm-regions",
        action="store_true",
        help="Outline Holt et al.'s 20 zoogeographic regions and name those on the map, over any layer.",
    )
    eco.add_argument(
        "--biome-alpha",
        type=float,
        default=0.6,
        metavar="ALPHA",
        help="Opacity of the biome, ecoregion or realm layer (0-1, default: 0.6).",
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

    history = parser.add_argument_group("Historical borders (Cliopatria, CShapes 2.0)")
    history.add_argument(
        "--borders",
        metavar="WHEN",
        help="Draw the states of a moment in history, outlined, named and coloured apart from their "
             "neighbours: a year (1500, -500 or '500 BC') or, from 1886, a date (1914-08-01).",
    )
    history.add_argument(
        "--borders-source",
        choices=list(BORDER_SOURCES),
        default="auto",
        help="cliopatria (3400 BCE-2024: states and empires; one-time download of ~46 MB), cshapes "
             "(1886-2019: states and their colonies, to the day; ~4 MB) or auto (CShapes in its years, "
             "Cliopatria otherwise; the default).",
    )
    history.add_argument(
        "--borders-alpha",
        type=float,
        default=0.55,
        metavar="ALPHA",
        help="Opacity of the borders' fill (0-1, default: 0.55).",
    )

    landforms = parser.add_argument_group("Relief units of Romania (Posea & Badea 1984, geo-spatial.org)")
    landforms.add_argument("--relief-units", nargs="?", const="units", metavar="LEVEL",
                           help="Draw Romania's relief units, filled in the colour of their major region "
                                "(paler for depressions, corridors and floodplains) and "
                                "named: units (all 820, down to single depressions, corridors and plains; the "
                                "default) or regions (the 16 major ones). "
                                "Downloaded once (~1.4 MB).")
    landforms.add_argument("--relief-alpha", type=float, default=0.55, metavar="ALPHA",
                           help="Opacity of the relief units' fill (0-1, default: 0.55).")

    earth = parser.add_argument_group("Plate tectonics, rivers, cyclones, time zones, permafrost")
    earth.add_argument("--plates", action="store_true",
                       help="Draw the plate boundaries (Bird 2003): spreading ridges, subduction zones and "
                            "transform faults, and name the large plates.")
    earth.add_argument("--earthquakes", nargs="?", const=DEFAULT_MIN_MAGNITUDE, type=float, default=None,
                       metavar="MAGNITUDE",
                       help=f"Draw the earthquakes since 1900 of at least MAGNITUDE ({MIN_MAGNITUDE:g}-"
                            f"{MAX_MAGNITUDE:g}; default {DEFAULT_MIN_MAGNITUDE:g}; USGS), sized by magnitude "
                            "and coloured by depth.")
    earth.add_argument("--volcanoes", action="store_true",
                       help="Draw the volcanoes active in the last 11,700 years (Smithsonian GVP), red if "
                            "they have erupted since 1900.")
    earth.add_argument("--currents", action="store_true",
                       help="Draw the mean ocean surface currents (NOAA drifters; one-time download of ~51 MB), "
                            "coloured warm or cold for their latitude.")
    earth.add_argument("--rivers", action="store_true",
                       help="Draw the main rivers (Natural Earth) and name the greatest.")
    earth.add_argument("--basins", action="store_true",
                       help="Colour the river basins of 100,000 km² or more (HydroBASINS; one-time download of "
                            "~55 MB), named, inland drainage in grey, with a key.")
    earth.add_argument("--basins-alpha", type=float, default=0.5, metavar="ALPHA",
                       help="Opacity of the river basins (0-1, default: 0.5).")
    earth.add_argument("--cyclones", nargs="?", const=FIRST_CYCLONE_YEAR, type=int, default=None, metavar="SINCE",
                       help=f"Draw the tropical cyclone tracks (IBTrACS) from the year SINCE (default "
                            f"{FIRST_CYCLONE_YEAR}), coloured by Saffir-Simpson category.")
    earth.add_argument("--time-zones", action="store_true",
                       help="Shade and label the standard-time zones (Natural Earth).")
    earth.add_argument("--permafrost", action="store_true",
                       help="Colour the Northern Hemisphere's permafrost zones (Obu et al. 2019).")
    earth.add_argument("--permafrost-alpha", type=float, default=0.7, metavar="ALPHA",
                       help="Opacity of the permafrost (0-1, default: 0.7).")

    people = parser.add_argument_group("People and livestock")
    people.add_argument("--population", nargs="?", const=LATEST_POPULATION_YEAR, type=int, default=None,
                        metavar="YEAR",
                        help="Colour the land by population density (GPWv4 via NASA GIBS) in YEAR: "
                             + ", ".join(map(str, POPULATION_YEARS)) + f" (default {LATEST_POPULATION_YEAR}).")
    people.add_argument("--population-alpha", type=float, default=0.7, metavar="ALPHA",
                        help="Opacity of the population density (0-1, default: 0.7).")
    people.add_argument("--livestock", metavar="ANIMAL",
                        help="Colour the land by the density of one kind of livestock in 2015 (Gridded Livestock "
                             "of the World v4; ~10 MB each): " + ", ".join(LIVESTOCK_SPECIES) + ".")
    people.add_argument("--livestock-alpha", type=float, default=0.7, metavar="ALPHA",
                        help="Opacity of the livestock density (0-1, default: 0.7).")

    health = parser.add_argument_group("Malaria and Aedes mosquitoes")
    health.add_argument("--malaria", metavar="PARASITE",
                        help="Colour the land by the malaria parasite rate (Malaria Atlas Project; ~40 MB a year): "
                             "pf (Plasmodium falciparum, children aged 2-10) or pv (P. vivax, all ages).")
    health.add_argument("--malaria-year", type=int, default=LATEST_MALARIA_YEAR, metavar="YEAR",
                        help=f"Year of the malaria map ({FIRST_MALARIA_YEAR}-{LATEST_MALARIA_YEAR}, "
                             f"default: {LATEST_MALARIA_YEAR}).")
    health.add_argument("--malaria-alpha", type=float, default=0.75, metavar="ALPHA",
                        help="Opacity of the malaria map (0-1, default: 0.75).")
    health.add_argument("--aedes", nargs="?", const="both", metavar="SPECIES",
                        help="Draw where the dengue mosquitoes have been recorded, 1958-2014 (Kraemer et al. 2015): "
                             + ", ".join(AEDES_CHOICES) + " (default: both).")

    wild = parser.add_argument_group("Species records (GBIF)")
    wild.add_argument("--species", metavar="NAME",
                      help="Draw where a species, genus or family has been recorded in the wild, by its scientific "
                           "or common name ('Panthera leo', 'red fox', 'Quercus'), from GBIF; ~20 km cells, darker "
                           "for more records.")
    wild.add_argument("--species-years", metavar="YEARS",
                      help="Only the records of these years: FROM-TO or YEAR (e.g. 1990-2020).")

    ranges = parser.add_argument_group("Species ranges (IUCN Red List and BirdLife)")
    ranges.add_argument("--iucn", metavar="NAME",
                        help="Draw the IUCN Red List range of a species, genus or family ('Panthera leo', 'lion', "
                             "'Felidae', 'osprey'), from range files you downloaded from IUCN or BirdLife; shaded by presence, origin "
                             "and season.")
    ranges.add_argument("--iucn-data", metavar="PATH",
                        help="Folder, zip, .shp or .gpkg of range files (default: ~/.cache/ortho_tiles/iucn).")
    ranges.add_argument("--iucn-alpha", type=float, default=0.45, metavar="ALPHA",
                        help="Opacity of the IUCN range (0-1, default: 0.45).")

    towns = parser.add_argument_group("Cities (GHSL urban centres)")
    towns.add_argument("--cities", nargs="?", const=2025, type=int, metavar="YEAR",
                       help="Draw the built-up urban centres (metropolitan areas) and name the largest, as of "
                            f"YEAR ({CITIES_YEARS[0]}-{CITIES_YEARS[-1]} every five years, 2030 projected; "
                            "default: 2025). The GHSL data (~35 MB) is downloaded once.")
    towns.add_argument("--cities-min", type=int, metavar="PEOPLE",
                       help="Show only centres of at least this many people (default: 1,000,000 on a whole globe, "
                            "fewer on a closer map, down to 50,000).")

    stats = parser.add_argument_group("Statistics by country or region (World Bank, Eurostat)")
    stats.add_argument("--indicator", metavar="NAME",
                       help="Shade each country by a World Bank statistic: " + ", ".join(INDICATOR_PRESETS)
                            + ", or any World Bank code (e.g. NY.GDP.PCAP.CD; see data.worldbank.org/indicator). "
                            "Or each European region by a Eurostat one: "
                            + ", ".join("eurostat:" + n for n in EUROSTAT_PRESETS)
                            + ", or eurostat:DATASET?dim=CODE&level=nuts2.")
    stats.add_argument("--indicator-year", type=int, metavar="YEAR",
                       help="Year of the figures (default: each country's latest); a country without one that "
                            "year shows its latest from the nine years before.")
    stats.add_argument("--indicator-alpha", type=float, default=0.75, metavar="ALPHA",
                       help="Opacity of the statistics (0-1, default: 0.75).")

    plants = parser.add_argument_group("Industry (Climate TRACE)")
    plants.add_argument("--industry", metavar="SECTORS",
                        help="Draw industrial plants as circles sized by their greenhouse-gas emissions, the largest "
                             "named: comma-separated sectors (" + ", ".join(INDUSTRY_SECTORS) + ") or groups ("
                             + ", ".join(INDUSTRY_GROUPS) + "). Each sector is downloaded once.")

    traffic = parser.add_argument_group("Shipping, ports and air routes (World Bank and IMF, OpenFlights)")
    traffic.add_argument("--shipping", nargs="?", const="commercial", metavar="KIND",
                         help="Colour the sea by how busy it is with ships, from their AIS positions in 2015-2021: "
                              + ", ".join(SHIP_KINDS) + " (default: commercial). Each kind is downloaded once "
                              "(20-510 MB) and read into a cache of up to ~85 MB.")
    traffic.add_argument("--shipping-alpha", type=float, default=0.85, metavar="ALPHA",
                         help="Opacity of the ship traffic (0-1, default: 0.85).")
    traffic.add_argument("--ports", nargs="?", const="all", metavar="KIND",
                         help="Draw the ports as dots sized by the ships calling a year (IMF PortWatch, 2019-2025), "
                              "the busiest named: " + ", ".join(PORT_KINDS) + " (default: all). Downloaded once "
                              "(~0.5 MB).")
    traffic.add_argument("--air-routes", metavar="AIRPORTS",
                         help="Draw air routes as great-circle arcs, wider the more airlines fly them (OpenFlights, "
                              "2014): 'all' for the whole network, or comma-separated airports by code or city "
                              "(LHR, EGLL, London) and airlines (airline:BA, airline:Lufthansa).")

    winds = parser.add_argument_group("Winds (NCEP/NCAR Reanalysis)")
    winds.add_argument(
        "--wind",
        nargs="?",
        const="year",
        default=None,
        metavar="PERIOD",
        help="Draw the prevailing winds, the 1991-2020 mean 10 m wind, for a PERIOD: year (the default), "
             "a season (djf, mam, jja, son) or a month (e.g. july or 7).",
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
    return option_tokens(parser, recipe, f"recipe {path}", os.path.dirname(os.path.abspath(path)), cli_argv)


def option_tokens(
    parser: argparse.ArgumentParser, recipe: dict[str, Any], where: str, base: str, cli_argv: Sequence[str] = (),
) -> list[str]:
    """Turn the options in *recipe*, as a recipe file holds them, into command-line tokens for *parser*.

    Route paths are relative to the folder *base*; *where* names the options
    in error messages, which exit through ``parser.error``. Options the
    command line *cli_argv* overrides are dropped, as :func:`_recipe_tokens`
    describes.
    """
    actions = _option_actions(parser)
    given = _given_dests(parser, cli_argv)
    # Location counts as one choice: city, or lat with lon
    overridden = {"city", "lat", "lon"} if given & {"city", "lat", "lon"} else set()
    for group in parser._mutually_exclusive_groups:
        dests = {a.dest for a in group._group_actions}
        if dests & given:
            overridden |= dests

    tokens: list[str] = []
    for key, value in recipe.items():
        name = key.replace("_", "-")
        action = actions.get(name)
        if action is None:
            close = difflib.get_close_matches(name, actions, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            parser.error(f"{where}: unknown option {key!r}.{hint}")
        if action.dest in overridden:
            continue
        option = f"--{name}"
        if action.nargs == 0:  # a flag such as --ice
            if not isinstance(value, bool):
                parser.error(f"{where}: {key} must be true or false")
            if value:
                tokens.append(option)
            continue
        values = value if isinstance(value, list) else [value]
        if len(values) > 1 and not isinstance(action, argparse._AppendAction):
            parser.error(f"{where}: {key} takes a single value, not a list")
        for item in values:
            if isinstance(item, bool) or not isinstance(item, (str, int, float)):
                parser.error(f"{where}: {key} must be text or a number, got {item!r}")
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
    radius_km = prompt_for_radius()
    if radius_km is None:
        zoom = prompt_for_zoom(default_zoom=DEFAULT_ZOOM)
        both_hemispheres = input(
            "Also draw the opposite hemisphere, centred on the antipode? [y/N]: "
        ).strip().lower() in ("y", "yes")
    else:
        zoom = prompt_for_zoom(default_zoom=zoom_for_radius(radius_km, lat, tile_provider),
                               max_zoom=max_zoom(radius_km, lat, tile_provider))
        both_hemispheres = False
    up = prompt_for_up()
    output_file = build_output_filename(city_slug, tile_provider, zoom, both_hemispheres, up, radius_km)

    print(
        f"\nGenerating map for {city_label} using '{tile_provider}' at zoom level {zoom}."
    )
    print(f"Output file: {output_file}\n")

    # Climate overlay prompt: Köppen-Geiger, Trewartha, a climate mean or none
    climate_input = input(
        "Climate overlay: [k]öppen-Geiger, [t]rewartha, mean te[m]perature, [p]recipitation, "
        "[h]umidity, or blank for none: "
    ).strip().lower()
    enable_koppen = climate_input in ("k", "koppen", "köppen", "y", "yes")
    enable_trewartha = climate_input in ("t", "trewartha")
    mean_name = {"m": "temperature", "temperature": "temperature", "p": "precipitation",
                 "precipitation": "precipitation", "h": "humidity", "humidity": "humidity"}.get(climate_input)
    climate_means: dict[str, str] = {}
    if mean_name:
        while True:
            period = input(
                f"{mean_name.capitalize()} period: year, a season (djf, mam, jja, son) or a month "
                "[default: year]: "
            ).strip() or "year"
            try:
                resolve_climate_period(mean_name, period)
                break
            except ValueError as e:
                print(f"{e}\n")
        climate_means[mean_name] = period

    default_alpha = CLIMATE_MEAN_ALPHA if mean_name else CLASSIFICATION_ALPHA
    koppen_alpha = default_alpha
    koppen_classes: list[str] = []
    trewartha_classes: list[str] = []
    if enable_koppen or enable_trewartha or mean_name:
        raw_alpha = input(f"Climate overlay opacity (0-1) [default: {default_alpha}]: ").strip()
        if raw_alpha:
            try:
                val = float(raw_alpha)
                if 0.0 <= val <= 1.0:
                    koppen_alpha = val
                else:
                    print(f"Out of range. Using default {default_alpha}.")
            except ValueError:
                print(f"Invalid number. Using default {default_alpha}.")
        if enable_koppen:
            koppen_classes = prompt_for_koppen_classes()
        elif enable_trewartha:
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

    biome_input = input(
        "Biomes and realms: [b]iomes, [e]coregions, [r]ealms (WWF), [h]olt's zoogeographic realms, "
        "or blank for none: "
    ).strip().lower()
    enable_biomes = biome_input in ("b", "biomes", "y", "yes")
    enable_ecoregions = biome_input in ("e", "ecoregions")
    realm_source = {"r": "wwf", "realms": "wwf", "h": "holt", "holt": "holt"}.get(biome_input)
    biome_classes: list[str] = []
    if enable_biomes or enable_ecoregions or realm_source == "wwf":
        biome_classes = prompt_for_climate_classes(
            resolve_biome_classes, "e.g. temperate, mangroves", what="Biomes")
    realm_regions = input(
        "Outline Holt's 20 zoogeographic regions? [y/N]: "
    ).strip().lower() in ("y", "yes")

    wind_period = None
    if input("Draw the prevailing winds? [y/N]: ").strip().lower() in ("y", "yes"):
        while True:
            wind_period = input(
                "Wind period: year, a season (djf, mam, jja, son) or a month [default: year]: "
            ).strip() or "year"
            try:
                resolve_wind_period(wind_period)
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

    borders_when = None
    while True:
        raw = input("Historical borders: a year (1500, 500 BC) or a date (1914-08-01), or blank for none: ").strip()
        if not raw:
            break
        try:
            choose_borders_source(parse_when(raw))
        except ValueError as e:
            print(f"{e}\n")
            continue
        borders_when = raw
        break

    extra_layers = prompt_for_extra_layers()

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
            borders=borders_when,
            **extra_layers,
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
            biomes=enable_biomes,
            biome_classes=biome_classes,
            ecoregions=enable_ecoregions,
            realms=realm_source,
            realm_regions=realm_regions,
            wind=wind_period,
            radius_km=radius_km,
            **climate_means,
            up=up,
        )
    except GoogleTilesError as e:
        print(f"\n{e}")
        sys.exit(1)


class RenderPlan(NamedTuple):
    """A checked map request: the place's name and the arguments for :func:`generate_orthographic_map`."""
    label: str
    kwargs: dict[str, Any]


def prepare_render(args: argparse.Namespace) -> RenderPlan:
    """Check the options in *args* and turn them into the renderer's arguments.

    *args* holds the options of :func:`build_cli_parser`, from the command line
    or the web app. Everything that can be checked before any tiles or data are
    fetched is checked here. Raises ``ValueError`` with a message for the user.
    """
    # Validate coordinate pairing first
    if args.lon is not None and args.lat is None:
        raise ValueError("--lon requires --lat.")
    if args.lat is not None and args.lon is None:
        raise ValueError("--lat requires --lon.")

    # Resolve coordinates
    if args.lat is not None:
        if not (-90 <= args.lat <= 90):
            raise ValueError("--lat must be between -90 and 90.")
        if not (-180 <= args.lon <= 180):
            raise ValueError("--lon must be between -180 and 180.")
        lat, lon = args.lat, args.lon
        city_slug = "custom"
        city_label = f"({lat}, {lon})"
        city_name = None
    elif args.city:
        # Find the city (case-insensitive match)
        city_name = next((k for k in MAJOR_METROPOLISES if k.lower() == args.city.lower()), None)
        if city_name is None:
            raise ValueError(f"Unknown city {args.city!r}. Cities: {', '.join(MAJOR_METROPOLISES)}")
        city = MAJOR_METROPOLISES[city_name]
        lat, lon = city["lat"], city["lon"]
        city_slug = city["slug"]
        city_label = city_name
    else:
        raise ValueError("Provide --city or --lat/--lon.")
    if args.provider not in TILE_PROVIDERS:
        raise ValueError(f"Unknown tile provider {args.provider!r}. Providers: {', '.join(TILE_PROVIDERS)}")

    # Load routes up front so a bad file fails before any tiles are fetched
    try:
        routes = load_route_files(args.route or [])
    except (OSError, ValueError) as e:
        raise ValueError(f"Could not load route: {e}") from e
    if args.route_legend and not routes:
        logger.warning("--route-legend is ignored because no --route was given.")

    # Check crop and climate class names now, before any tiles or data are fetched
    resolve_crops(args.crop or [])
    if args.borders:
        choose_borders_source(parse_when(args.borders), args.borders_source)
    if args.population is not None:
        resolve_population_year(args.population)
    if args.livestock:
        resolve_species(args.livestock)
    if args.malaria:
        resolve_parasite(args.malaria)
        resolve_malaria_year(args.malaria_year)
    if args.aedes:
        resolve_aedes(args.aedes)
    if args.species_years and not args.species:
        logger.warning("--species-years is ignored because no --species was given.")
    elif args.species:
        parse_years(args.species_years)
    if args.iucn_data and not args.iucn:
        logger.warning("--iucn-data is ignored because no --iucn was given.")
    if args.cities is not None:
        resolve_cities_year(args.cities)
    elif args.cities_min is not None:
        logger.warning("--cities-min is ignored because no --cities was given.")
    if args.indicator:
        resolve_indicator(args.indicator)
        resolve_indicator_year(args.indicator_year)
    elif args.indicator_year is not None:
        logger.warning("--indicator-year is ignored because no --indicator was given.")
    if args.industry:
        resolve_sectors(args.industry)
    if args.shipping:
        resolve_ship_kind(args.shipping)
    elif args.shipping_alpha != 0.85:
        logger.warning("--shipping-alpha is ignored because no --shipping was given.")
    if args.ports:
        resolve_port_kind(args.ports)
    if args.relief_units:
        resolve_relief_level(args.relief_units)
    elif args.relief_alpha != 0.55:
        logger.warning("--relief-alpha is ignored because no --relief-units was given.")
    if args.air_routes:
        resolve_air_routes(args.air_routes)
    resolve_koppen_classes(args.koppen_class or [])
    resolve_trewartha_classes(args.trewartha_class or [])
    resolve_soil_classes(args.soil_class or [])
    resolve_biome_classes(args.biome_class or [])
    if args.ecoregions and args.realms:
        raise ValueError("Choose --ecoregions or --realms, not both.")
    if args.realms == "holt" and (args.biomes or args.biome_class):
        raise ValueError("--realms holt is not split by biome: not with --biomes or --biome-class.")
    resolve_land_cover_classes(args.landcover_class or [])
    if args.ndvi:
        resolve_ndvi_month(args.ndvi)
    if args.wind is not None:
        resolve_wind_period(args.wind)
    climate_mean = resolve_climate_mean(args.temperature, args.precipitation, args.humidity)
    if args.soil_property:
        resolve_soil_property(args.soil_property, args.soil_depth)
    elif args.soil_depth:
        raise ValueError("--soil-depth needs --soil-property.")
    if (args.landcover or args.landcover_class or args.landcover_year is not None) and args.ndvi:
        raise ValueError("Choose one vegetation layer: --landcover or --ndvi.")
    if (args.soil or args.soil_class) and args.soil_property:
        raise ValueError("Choose one soil layer: --soil/--soil-class or --soil-property.")
    if (args.koppen or args.koppen_class) and (args.trewartha or args.trewartha_class):
        raise ValueError("Choose one climate classification: Köppen-Geiger or Trewartha.")
    if (args.koppen or args.koppen_class or args.trewartha or args.trewartha_class) and climate_mean:
        raise ValueError("Choose one climate layer: a classification or a climate mean.")
    if args.sea_temperature is not None:
        resolve_sea_period(args.sea_temperature)
    if args.temperature_over_sea:
        if args.temperature is None:
            raise ValueError("--temperature-over-sea needs --temperature.")
        if args.sea_temperature is not None:
            raise ValueError("Choose --temperature-over-sea or --sea-temperature, not both.")

    # Orientation: a bearing, or the direction toward a place
    up = args.up
    if args.up_toward:
        try:
            target_lat, target_lon = resolve_place(args.up_toward)
        except ValueError as e:
            raise ValueError(f"--up-toward: {e}") from e
        up = initial_bearing(lat, lon, target_lat, target_lon)
        logger.info("Direction toward %s: bearing %.1f°", args.up_toward, up)
    if not math.isfinite(up):
        raise ValueError("--up must be a number of degrees.")

    # Distance circles: from the centre, or from another place
    circles_from = None
    if args.circles_from:
        try:
            circles_from = resolve_place(args.circles_from)
        except ValueError as e:
            raise ValueError(f"--circles-from: {e}") from e

    # A second globe beside the first, turned toward --up-toward from its own centre
    beside = None
    beside_up = None
    if args.beside:
        if args.both_hemispheres:
            raise ValueError("Choose --both-hemispheres or --beside, not both.")
        try:
            beside = resolve_named_place(args.beside)
        except ValueError as e:
            raise ValueError(f"--beside: {e}") from e
        if args.up_toward:
            beside_up = initial_bearing(beside.lat, beside.lon, target_lat, target_lon)
            logger.info("Direction toward %s from %s: bearing %.1f°", args.up_toward, beside.label, beside_up)
        city_label = f"{city_label} and {beside.label}"
    beside_centre = (beside.lat, beside.lon) if beside is not None else None

    # Zoom: as given, or the default for the whole globe or the radius
    zoom = args.zoom
    if zoom is None:
        zoom = DEFAULT_ZOOM if args.radius is None else zoom_for_radius(
            args.radius, zoom_latitude(lat, beside_centre), args.provider)
    try:
        validate_render_options(
            dpi=args.dpi, koppen_alpha=args.koppen_alpha, both_hemispheres=args.both_hemispheres,
            ice_year=args.ice_year, elevation_alpha=args.elevation_alpha, soil_alpha=args.soil_alpha,
            vegetation_alpha=args.vegetation_alpha, land_cover_year=args.landcover_year,
            zoom=zoom, radius_km=args.radius, rectangle=args.rectangle, tile_provider=args.provider, lat=lat,
            beside=beside_centre,
            biome_alpha=args.biome_alpha, borders_alpha=args.borders_alpha, basins_alpha=args.basins_alpha,
            permafrost_alpha=args.permafrost_alpha, population_alpha=args.population_alpha,
            livestock_alpha=args.livestock_alpha, earthquakes=args.earthquakes, cyclones=args.cyclones,
            malaria_alpha=args.malaria_alpha, iucn_alpha=args.iucn_alpha, indicator_alpha=args.indicator_alpha,
            shipping_alpha=args.shipping_alpha, relief_alpha=args.relief_alpha,
        )
    except ValueError as e:
        raise ValueError(f"Invalid option: {e}.") from e

    if args.provider in GOOGLE_MAP_TYPES:
        try:
            resolve_api_key()
        except GoogleTilesError as e:
            raise ValueError(str(e)) from e

    tile_cache_dir = None if args.no_cache else configure_tile_cache(args.cache_dir)

    # Determine output filename
    if args.output:
        if args.output_dir:
            logger.warning("--output-dir is ignored because -o/--output gives the full path.")
        output_file = args.output
        output_dir = None  # explicit path, don't prepend output_dir
    else:
        output_file = build_output_filename(
            city_slug, args.provider, zoom, args.both_hemispheres, up, args.radius,
            beside.slug if beside is not None else None, args.rectangle,
        )
        output_dir = args.output_dir

    return RenderPlan(city_label, dict(
        lat=lat,
        lon=lon,
        output_filename=output_file,
        tile_provider=args.provider,
        zoom=zoom,
        dpi=args.dpi,
        output_dir=output_dir,
        city_name=city_name if city_slug != "custom" else None,
        koppen=args.koppen,
        koppen_alpha=args.koppen_alpha,
        routes=routes,
        route_legend=args.route_legend,
        tile_cache_dir=tile_cache_dir,
        both_hemispheres=args.both_hemispheres,
        ice=args.ice or args.ice_year is not None,
        ice_year=args.ice_year,
        crops=args.crop,
        borders=args.borders,
        borders_source=args.borders_source,
        borders_alpha=args.borders_alpha,
        ocean_depth=args.ocean_depth,
        ice_age=args.ice_age,
        plates=args.plates,
        earthquakes=args.earthquakes,
        volcanoes=args.volcanoes,
        currents=args.currents,
        rivers=args.rivers,
        basins=args.basins,
        basins_alpha=args.basins_alpha,
        cyclones=args.cyclones,
        time_zones=args.time_zones,
        permafrost=args.permafrost,
        permafrost_alpha=args.permafrost_alpha,
        population=args.population,
        population_alpha=args.population_alpha,
        livestock=args.livestock,
        livestock_alpha=args.livestock_alpha,
        malaria=args.malaria,
        malaria_year=args.malaria_year,
        malaria_alpha=args.malaria_alpha,
        aedes=args.aedes,
        species=args.species,
        species_years=args.species_years,
        iucn=args.iucn,
        iucn_data=args.iucn_data,
        iucn_alpha=args.iucn_alpha,
        cities=args.cities,
        cities_min=args.cities_min,
        indicator=args.indicator,
        indicator_year=args.indicator_year,
        indicator_alpha=args.indicator_alpha,
        industry=args.industry,
        shipping=args.shipping,
        shipping_alpha=args.shipping_alpha,
        ports=args.ports,
        air_routes=args.air_routes,
        relief_units=args.relief_units,
        relief_alpha=args.relief_alpha,
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
        biomes=args.biomes,
        biome_classes=args.biome_class,
        ecoregions=args.ecoregions,
        realms=args.realms,
        realm_regions=args.realm_regions,
        biome_alpha=args.biome_alpha,
        wind=args.wind,
        temperature=args.temperature,
        precipitation=args.precipitation,
        humidity=args.humidity,
        radius_km=args.radius,
        rectangle=args.rectangle,
        up=up,
        circles_from=circles_from,
        distance_circles=not args.no_circles,
        beside=beside_centre,
        beside_name=beside.name if beside is not None else None,
        beside_up=beside_up,
        temperature_over_sea=args.temperature_over_sea,
        sea_temperature=args.sea_temperature,
    ))


def run_cli(args: argparse.Namespace) -> None:
    """Non-interactive CLI mode driven by argparse namespace."""
    try:
        plan = prepare_render(args)
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    logger.info(
        "Generating map for %s using '%s' at zoom level %d.",
        plan.label, plan.kwargs["tile_provider"], plan.kwargs["zoom"],
    )
    logger.info("Output file: %s", plan.kwargs["output_filename"])

    try:
        generate_orthographic_map(**plan.kwargs)
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
