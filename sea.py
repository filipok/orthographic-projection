"""Temperatures over the sea: the sea surface's, and the air's, by season or month.

Data sources
------------
Huang, B., Liu, C., Banzon, V., Freeman, E., Graham, G., Hankins, B., Smith,
T. & Zhang, H.-M. (2021). Improvements of the Daily Optimum Interpolation Sea
Surface Temperature (DOISST) Version 2.1. *Journal of Climate* 34, 2923-2939.
NOAA OI SST V2 High Resolution data provided by the NOAA PSL, Boulder,
Colorado, USA, from their website at https://psl.noaa.gov — public domain.
PSL publishes the 1991-2020 long-term mean of each month on a 0.25° grid
(one netCDF-4 file, ~46 MB), downloaded once and cached.

Kalnay, E. et al. (1996). The NCEP/NCAR 40-Year Reanalysis Project.
*Bull. Amer. Meteor. Soc.* 77, 437-471 — the same reanalysis as the winds
(see :mod:`wind`): the 1981-2010 long-term mean of each month's air
temperature 2 m above the surface, on its T62 Gaussian grid (about 1.9°;
one file, ~0.5 MB). That is CHELSA's period, so it carries the land's air
temperature (see :mod:`climate`) out over the sea.

What is drawn
-------------
Either one colours the sea and lakes in the temperature bands of
:mod:`climate`, cut along Natural Earth's coast. The sea surface cannot cool
much below −1.8 °C, where sea water freezes, so under sea ice it stays near
that while the air above the ice can be tens of degrees colder.
"""

from __future__ import annotations

import logging
import os
import urllib.error
from typing import NamedTuple

import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt

from wind import DAYS_IN_MONTH, WindDataError, WindPeriod, _get, read_variable, resolve_wind_period

logger = logging.getLogger(__name__)

SEA_TEMPERATURE_URL = ("https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres/"
                       "sst.mon.ltm.1991-2020.nc")
SEA_AIR_URL = ("https://downloads.psl.noaa.gov/Datasets/ncep.reanalysis.derived/surface_gauss/"
               "air.2m.mon.ltm.1981-2010.nc")
DATA_VERSION = 1
SEA_TEMPERATURE_ATTRIBUTION = "Sea surface temperature: NOAA OISST v2.1, 1991–2020 means, NOAA PSL"
SEA_AIR_ATTRIBUTION = "Air temperature over the sea: NCEP/NCAR Reanalysis 1, 1981–2010 means, NOAA PSL"

# OISST leaves a gap of up to a cell or two along the coasts; the nearest sea
# value fills that far inland, so the coast cut is clean, and no further
SEA_FILL_CELLS = 2
# The reanalysis grid is smoothed onto this regular grid once, in degrees
SEA_AIR_CELL = 0.5
# Stored as hundredths of a degree in int16
_SCALE = 0.01
_NODATA = -32768


class SeaDataError(RuntimeError):
    """The sea or air temperature means could not be downloaded or read."""


class SeaGrid(NamedTuple):
    """A temperature grid in °C, rows north to south (NaN where there is none), and its ``(west, north, cell)``."""
    values: np.ndarray
    geo: tuple[float, float, float]


def resolve_sea_period(spec: str | None) -> WindPeriod:
    """The months of *spec* (see :func:`wind.resolve_wind_period`) for a sea temperature map."""
    return resolve_wind_period(spec, kind="sea temperature")


def sea_legend_title(period: WindPeriod) -> str:
    """``"Sea surface temperature, July mean, 1991–2020 (°C)"`` and the like."""
    return f"Sea surface temperature, {_when(period)} mean, 1991–2020 (°C)"


def land_and_sea_legend_title(period: WindPeriod) -> str:
    """The key's title when the land's air temperature and the sea's surface share it."""
    return (f"Air temperature on land (1981–2010) and sea surface temperature (1991–2020), "
            f"{_when(period)} mean (°C)")


def _when(period: WindPeriod) -> str:
    return "annual" if period.key == "year" else period.label


# ---------------------------------------------------------------------------
# Download and cache
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "sea")


def _download(url: str, var: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        return read_variable(_get(url, timeout=600), var)
    except WindDataError as exc:
        raise SeaDataError(str(exc)) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, KeyError, ValueError) as exc:
        raise SeaDataError(f"Could not read {url.rsplit('/', 1)[-1]}: {exc}") from exc


def _save(path: str, values: np.ndarray, geo: tuple[float, float, float]) -> None:
    stored = np.full(values.shape, _NODATA, np.int16)
    finite = np.isfinite(values)
    stored[finite] = np.round(values[finite] / _SCALE).astype(np.int16)
    part = path + ".part.npz"
    np.savez_compressed(part, values=stored, geo=np.array(geo))
    os.replace(part, path)


def fill_coast(month: np.ndarray, cells: int = SEA_FILL_CELLS) -> np.ndarray:
    """*month* with each NaN within *cells* of a value given the nearest value."""
    missing = np.isnan(month)
    if not missing.any() or missing.all():
        return month
    distance, (rows, cols) = distance_transform_edt(missing, return_indices=True)  # type: ignore[misc]
    filled = month[rows, cols]
    return np.where(missing & (distance <= cells), filled, month)


def ensure_sea_temperature(cache_dir: str | None = None) -> str:
    """Path to the cached OISST monthly means, downloading them (~46 MB) if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"oisst21_sst_ltm_1991-2020_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        return path
    logger.info("Downloading NOAA OISST v2.1 1991-2020 sea surface temperature means (one-time, ~46 MB) …")
    values, lat, lon = _download(SEA_TEMPERATURE_URL, "sst")
    cell = abs(float(lat[1] - lat[0]))
    if values.shape[1:] != (round(180 / cell), round(360 / cell)) or abs(lon[0] - cell / 2) > 1e-6:
        raise SeaDataError(f"unexpected OISST grid {values.shape} from {lat[0]}, {lon[0]}")
    if lat[0] < lat[-1]:                                         # rows south to north
        values = values[:, ::-1]
    values = np.stack([fill_coast(month) for month in values])
    _save(path, values, (0.0, 90.0, cell))
    return path


def ensure_sea_air(cache_dir: str | None = None) -> str:
    """Path to the cached reanalysis air temperature means on a regular grid, downloading them (~0.5 MB)."""
    cache_dir = cache_dir or _default_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"ncep_air2m_ltm_1981-2010_{SEA_AIR_CELL:g}deg_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        return path
    logger.info("Downloading NCEP/NCAR 1981-2010 air temperature means (one-time, ~0.5 MB) …")
    values, lat, lon = _download(SEA_AIR_URL, "air")
    values = values - 273.15                                     # kelvin to °C
    order = np.argsort(lat)
    lat, values = lat[order], values[:, order]
    # Smoothly (bilinear) onto a regular grid: longitudes wrap, the poles take the nearest row
    lon = np.append(lon, lon[0] + 360.0)
    values = np.concatenate([values, values[:, :, :1]], axis=2)
    lats = 90.0 - (np.arange(round(180 / SEA_AIR_CELL)) + 0.5) * SEA_AIR_CELL
    lons = (np.arange(round(360 / SEA_AIR_CELL)) + 0.5) * SEA_AIR_CELL
    points = np.stack(np.meshgrid(np.clip(lats, lat[0], lat[-1]), lons, indexing="ij"), axis=-1)
    regular = np.stack([RegularGridInterpolator((lat, lon), month)(points) for month in values])
    _save(path, regular.astype(np.float32), (0.0, 90.0, SEA_AIR_CELL))
    return path


def _load(path: str, period: WindPeriod) -> SeaGrid:
    """The mean over *period*'s months, each weighted by its length, of the cached grids at *path*."""
    with np.load(path) as data:
        stored = data["values"]
        geo = tuple(float(v) for v in data["geo"])
    total = None
    weight = 0.0
    for month in period.months:
        raw = stored[month - 1]
        values = raw.astype(np.float32) * _SCALE
        values[raw == _NODATA] = np.nan
        days = DAYS_IN_MONTH[month - 1]
        total = values * days if total is None else total + values * days
        weight += days
    assert total is not None
    return SeaGrid(total / weight, geo)  # type: ignore[arg-type]


def load_sea_temperature(period: WindPeriod, cache_dir: str | None = None) -> SeaGrid:
    """The sea's mean surface temperature over *period*, 1991-2020 (NaN on land)."""
    return _load(ensure_sea_temperature(cache_dir), period)


def load_sea_air(period: WindPeriod, cache_dir: str | None = None) -> SeaGrid:
    """The mean air temperature 2 m above the surface over *period*, 1981-2010, everywhere."""
    return _load(ensure_sea_air(cache_dir), period)
