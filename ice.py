"""Polar ice overlay: sea ice at its winter maximum plus permanent land ice.

Data sources
------------
Sea ice: Fetterer, F., Knowles, K., Meier, W. N., Savoie, M., Windnagel, A. K.
& Stafford, T. (2025). Sea Ice Index (G02135, Version 4). National Snow and
Ice Data Center, Boulder, Colorado, USA. https://doi.org/10.7265/a98x-0f50
NSIDC requires this data set to be cited. Used: the monthly extent polygons for
the month each hemisphere's ice usually peaks: March in the Arctic and
September in the Antarctic. Extent is the area with at least 15% ice cover.

Land ice: Natural Earth 1:10m glaciated areas and Antarctic ice shelves
(public domain), limited to the polar regions.
"""

from __future__ import annotations

import datetime
import logging
import os
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

import cartopy.crs as ccrs
import cartopy.io.shapereader as shapereader
from cartopy.mpl.geoaxes import GeoAxes

logger = logging.getLogger(__name__)

NSIDC_BASE_URL = "https://noaadata.apps.nsidc.org/NOAA/G02135"
SEA_ICE_VERSION = "v4.0"

# Month of the usual winter maximum, per hemisphere
MAX_MONTH = {"N": 3, "S": 9}
_MONTH_DIRS = {3: "03_Mar", 9: "09_Sep"}
_HEMISPHERE_DIRS = {"N": "north", "S": "south"}

# The satellite record starts in November 1978, so 1979 has the first maxima
FIRST_ICE_YEAR = 1979

# How many years back to look for the latest published maximum
_YEARS_TO_TRY = 3

# Only glaciers poleward of this latitude count as polar land ice
POLAR_LATITUDE = 60.0

# Natural Earth has zero-area slivers (one in northern Greenland) that Cartopy
# projects as a polygon covering the whole globe, so tiny shapes are skipped.
_MIN_AREA_DEG2 = 1e-6

# The extent polygons trace NSIDC's 25 km grid cells; simplifying them by
# less than a cell turns the staircase outline into smooth diagonals.
SEA_ICE_SIMPLIFY_M = 20_000

# Opaque fills: any translucency lets the plain disc that web tiles leave
# around each pole show through. Sea ice is a shade bluer than land ice.
SEA_ICE_COLOR = "#e2eef6"
SEA_ICE_EDGE = "#9fcbe3"
LAND_ICE_COLOR = "#f7fafc"

# NSIDC sea ice polar stereographic grids (Hughes 1980 ellipsoid)
# (an explicit axis and flattening override Globe's default WGS84 ellipsoid)
_HUGHES = ccrs.Globe(semimajor_axis=6378273.0, inverse_flattening=298.279411123064)
SEA_ICE_CRS = {
    "N": ccrs.Stereographic(central_latitude=90, central_longitude=-45,
                            true_scale_latitude=70, globe=_HUGHES),
    "S": ccrs.Stereographic(central_latitude=-90, central_longitude=0,
                            true_scale_latitude=-70, globe=_HUGHES),
}
_POLAR_CRS = {"N": ccrs.NorthPolarStereo(), "S": ccrs.SouthPolarStereo()}

_MONTH_NAMES = {3: "March", 9: "September"}


class IceDataError(RuntimeError):
    """The sea ice extent data could not be found, downloaded or read."""


@dataclass(frozen=True)
class IceLayers:
    """Ice polygons ready to draw, each with the CRS its coordinates are in."""

    sea_ice: tuple[tuple[tuple[BaseGeometry, ...], ccrs.CRS], ...]
    land_ice: tuple[tuple[tuple[BaseGeometry, ...], ccrs.CRS], ...]
    years: dict[str, int]

    @property
    def attribution(self) -> str:
        """Credit line naming the sea ice maxima shown."""
        north, south = self.years["N"], self.years["S"]
        return (f"Sea ice: NSIDC Sea Ice Index v4, extent {_MONTH_NAMES[MAX_MONTH['N']]} {north} "
                f"(Arctic) and {_MONTH_NAMES[MAX_MONTH['S']]} {south} (Antarctic)")


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "sea_ice")


def _extent_name(hemisphere: str, year: int) -> str:
    return f"extent_{hemisphere}_{year}{MAX_MONTH[hemisphere]:02d}_polygon_{SEA_ICE_VERSION}"


def _extent_url(hemisphere: str, year: int) -> str:
    month_dir = _MONTH_DIRS[MAX_MONTH[hemisphere]]
    return (f"{NSIDC_BASE_URL}/{_HEMISPHERE_DIRS[hemisphere]}/monthly/shapefiles/"
            f"shp_extent/{month_dir}/{_extent_name(hemisphere, year)}.zip")


def _is_valid_zip(path: str) -> bool:
    return os.path.isfile(path) and zipfile.is_zipfile(path)


def _download(url: str, dest: str, timeout: float = 60) -> None:
    """Download *url* to *dest* atomically (a ``.part`` file moved into place)."""
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as fh:
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.remove(part)


def ensure_sea_ice_extent(
    hemisphere: str,
    year: int | None = None,
    cache_dir: str | None = None,
    today: datetime.date | None = None,
) -> tuple[str, int]:
    """Return ``(zip_path, year)`` for a hemisphere's winter-maximum extent.

    With *year* given, that year's file is used. Otherwise the most recent
    published one is found by trying this year and then up to two earlier
    years, preferring a cached copy and skipping years NSIDC has not
    published yet (HTTP 404). Raises :class:`IceDataError` if none can be
    obtained.
    """
    if hemisphere not in MAX_MONTH:
        raise ValueError(f"hemisphere must be 'N' or 'S', got {hemisphere!r}")
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)

    current = (today or datetime.date.today()).year
    candidates = [year] if year is not None else list(range(current, current - _YEARS_TO_TRY, -1))
    last_error: Exception | None = None
    for candidate in candidates:
        path = os.path.join(cache_dir, _extent_name(hemisphere, candidate) + ".zip")
        if _is_valid_zip(path):
            logger.info("Using cached sea ice extent: %s", path)
            return path, candidate
        url = _extent_url(hemisphere, candidate)
        try:
            logger.info("Downloading sea ice extent from %s …", url)
            _download(url, path)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise IceDataError(f"Download of {url} failed: {exc}") from exc
            last_error = exc  # not published (yet): try the year before
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc  # offline: an older cached year may still be there
            continue
        if not _is_valid_zip(path):
            os.remove(path)
            raise IceDataError(f"{url} did not return a valid zip file")
        return path, candidate

    tried = ", ".join(str(c) for c in candidates)
    raise IceDataError(
        f"No {_MONTH_NAMES[MAX_MONTH[hemisphere]]} sea ice extent could be obtained for "
        f"{tried} ({last_error}). Get it from {NSIDC_BASE_URL} and pass --ice-year."
    )


def _read_zip_shapefile(zip_path: str) -> tuple[BaseGeometry, ...]:
    """Read every geometry of the shapefile inside *zip_path*."""
    extract_dir = zip_path[:-len(".zip")]
    try:
        with zipfile.ZipFile(zip_path) as zf:
            shp_names = [n for n in zf.namelist() if n.lower().endswith(".shp")]
            if not shp_names:
                raise IceDataError(f"{zip_path} contains no shapefile")
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise IceDataError(f"{zip_path} is not a valid zip file") from exc
    reader = shapereader.Reader(os.path.join(extract_dir, shp_names[0]))
    # Coordinates are metres; a zero-area shape would project as the whole globe
    geoms = (g.simplify(SEA_ICE_SIMPLIFY_M) for g in reader.geometries() if g is not None)
    return tuple(g for g in geoms if not g.is_empty and g.area > 0)


def _to_polar(geom: BaseGeometry, hemisphere: str) -> BaseGeometry:
    """Reproject a lon/lat polygon that touches a pole into polar stereographic.

    Drawn in lon/lat such a polygon (Antarctica) runs along the pole from
    180° to -180° and Cartopy fills the wrong side of it on some views.
    """
    proj = _POLAR_CRS[hemisphere]

    def to_polar(lonlat: np.ndarray) -> np.ndarray:
        return proj.transform_points(ccrs.PlateCarree(), lonlat[:, 0], lonlat[:, 1])[:, :2]

    return shapely.make_valid(shapely.transform(geom, to_polar))


def polar_land_ice(
    natural_earth: Callable[..., str] = shapereader.natural_earth,
) -> tuple[tuple[tuple[BaseGeometry, ...], ccrs.CRS], ...]:
    """Natural Earth glaciers and Antarctic ice shelves poleward of POLAR_LATITUDE."""
    plain: list[BaseGeometry] = []
    polar: dict[str, list[BaseGeometry]] = {"N": [], "S": []}
    for layer in ("glaciated_areas", "antarctic_ice_shelves_polys"):
        reader = shapereader.Reader(natural_earth("10m", "physical", layer))
        for geom in reader.geometries():
            if geom is None or geom.is_empty or geom.area < _MIN_AREA_DEG2:
                continue
            if abs(geom.representative_point().y) < POLAR_LATITUDE:
                continue
            _, miny, _, maxy = geom.bounds
            if miny <= -89.9:
                polar["S"].append(_to_polar(geom, "S"))
            elif maxy >= 89.9:
                polar["N"].append(_to_polar(geom, "N"))
            else:
                plain.append(geom)
    layers = [(tuple(plain), ccrs.PlateCarree())]
    layers += [(tuple(geoms), _POLAR_CRS[h]) for h, geoms in polar.items() if geoms]
    return tuple(layers)


def load_ice_layers(year: int | None = None, cache_dir: str | None = None) -> IceLayers:
    """Load both hemispheres' winter-maximum sea ice and the polar land ice.

    Raises :class:`IceDataError` or ``OSError`` if the data cannot be obtained.
    """
    sea_ice = []
    years = {}
    for hemisphere in ("N", "S"):
        zip_path, years[hemisphere] = ensure_sea_ice_extent(hemisphere, year, cache_dir)
        sea_ice.append((_read_zip_shapefile(zip_path), SEA_ICE_CRS[hemisphere]))
    return IceLayers(sea_ice=tuple(sea_ice), land_ice=polar_land_ice(), years=years)


def draw_ice(ax: GeoAxes, layers: IceLayers, zorder: float = 3) -> None:
    """Draw *layers* on *ax* above the map imagery: sea ice, then land ice.

    Land ice goes on top so the Antarctic ice shelves stay whole where the
    sea ice extent overlaps them. Both fills are opaque, which also covers
    the plain disc the web tiles leave around each pole.
    """
    for geoms, crs in layers.sea_ice:
        ax.add_geometries(geoms, crs=crs, facecolor=SEA_ICE_COLOR,
                          edgecolor=SEA_ICE_EDGE, linewidth=0.6, zorder=zorder)
    for geoms, crs in layers.land_ice:
        # A hairline in the fill colour hides seams between adjoining pieces
        ax.add_geometries(geoms, crs=crs, facecolor=LAND_ICE_COLOR,
                          edgecolor=LAND_ICE_COLOR, linewidth=0.4, zorder=zorder)
