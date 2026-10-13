"""Tests for sea.py — periods, titles, reading and caching the sea and air temperature means (no network)."""

import io
import urllib.error

import h5py
import numpy as np
import pytest

import sea
from wind import resolve_wind_period

# A coarse OISST-like grid: 30° cells, rows south to north, as in the PSL file
SST_LAT = np.arange(-75.0, 90.0, 30.0)
SST_LON = np.arange(15.0, 360.0, 30.0)
# A reanalysis-like grid: uneven latitudes north to south, longitudes from 0°
AIR_LAT = np.array([60.0, 0.0, -60.0])
AIR_LON = np.array([0.0, 120.0, 240.0])
MISSING = -9.96921e36


def _nc(var, values, lat, lon):
    """Bytes of a minimal PSL-style netCDF-4 file holding *var*."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        f["lat"], f["lon"] = lat, lon
        ds = f.create_dataset(var, data=np.asarray(values, np.float32))
        ds.attrs["missing_value"] = np.array([MISSING], np.float32)
    return buf.getvalue()


def _sst():
    """Month m holds m °C everywhere but the land: the two westmost columns of the middle rows."""
    values = np.stack([np.full((SST_LAT.size, SST_LON.size), float(m), np.float32) for m in range(1, 13)])
    values[:, 1:5, :2] = MISSING
    return values


@pytest.fixture
def served(monkeypatch):
    """Serve the synthetic files; returns the list of URLs fetched."""
    air = np.stack([np.tile(273.15 + 10.0 * np.arange(3), (3, 1)) + m for m in range(1, 13)])
    data = {
        "sst": _nc("sst", _sst(), SST_LAT, SST_LON),
        "air": _nc("air", air.astype(np.float32), AIR_LAT, AIR_LON),
    }
    fetched = []

    def get(url, timeout=120):
        fetched.append(url)
        return data[url.rsplit("/", 1)[1].split(".")[0]]

    monkeypatch.setattr(sea, "_get", get)
    return fetched


class TestPeriodsAndTitles:
    def test_periods_name_the_layer(self):
        assert sea.resolve_sea_period("jja").months == (6, 7, 8)
        with pytest.raises(ValueError, match="Unknown sea temperature period 'monsoon'"):
            sea.resolve_sea_period("monsoon")

    def test_titles(self):
        assert sea.sea_legend_title(resolve_wind_period("july")) == \
            "Sea surface temperature, July mean, 1991–2020 (°C)"
        assert sea.land_and_sea_legend_title(resolve_wind_period("year")) == (
            "Air temperature on land (1981–2010) and sea surface temperature (1991–2020), annual mean (°C)")


class TestFillCoast:
    def test_fills_only_near_the_values(self):
        row = np.array([[1.0, np.nan, np.nan, np.nan, np.nan, 5.0, np.nan]])
        out = sea.fill_coast(row, cells=1)
        assert out[0, :2].tolist() == [1.0, 1.0] and out[0, 4:].tolist() == [5.0, 5.0, 5.0]
        assert np.isnan(out[0, 2:4]).all()

    def test_all_or_nothing_missing_is_unchanged(self):
        full = np.ones((2, 2))
        assert sea.fill_coast(full) is full
        assert np.isnan(sea.fill_coast(np.full((2, 2), np.nan))).all()


class TestSeaTemperature:
    def test_downloaded_once_then_cached_north_to_south(self, tmp_path, served):
        first = sea.ensure_sea_temperature(str(tmp_path))
        assert sea.ensure_sea_temperature(str(tmp_path)) == first
        assert len(served) == 1 and "sst.mon.ltm.1991-2020" in served[0]
        with np.load(first) as cached:
            assert cached["geo"].tolist() == [0.0, 90.0, 30.0]
            assert cached["values"].shape == (12, 6, 12)

    def test_months_are_weighted_by_length(self, tmp_path, served):
        grid = sea.load_sea_temperature(resolve_wind_period("djf"), str(tmp_path))
        expected = (31 * 12 + 31 * 1 + 28.25 * 2) / (31 + 31 + 28.25)
        assert grid.values[0, 5] == pytest.approx(expected, abs=0.01)

    def test_the_coast_is_filled_and_inland_stays_empty(self, tmp_path, served):
        values = sea.load_sea_temperature(resolve_wind_period("july"), str(tmp_path)).values
        # Rows run north to south now: the land is rows 1-4, columns 0-1; the sea
        # beside it fills it, two cells in, which reaches both of its columns
        assert np.isfinite(values).all() and values[2, 0] == pytest.approx(7.0)
        with np.load(sea.ensure_sea_temperature(str(tmp_path))) as cached:
            assert (cached["values"] != sea._NODATA).all()

    def test_land_beyond_the_fill_is_nan(self, tmp_path, monkeypatch):
        values = _sst()
        values[:, :, :6] = MISSING                                  # six columns of land
        monkeypatch.setattr(sea, "_get", lambda url, timeout=120: _nc("sst", values, SST_LAT, SST_LON))
        grid = sea.load_sea_temperature(resolve_wind_period("july"), str(tmp_path)).values
        assert np.isnan(grid[:, 2:4]).all() and np.isfinite(grid[:, 4:]).all()

    def test_download_failure(self, tmp_path, monkeypatch):
        def fail(url, timeout=120):
            raise urllib.error.URLError("offline")
        monkeypatch.setattr(sea, "_get", fail)
        with pytest.raises(sea.SeaDataError, match="offline"):
            sea.ensure_sea_temperature(str(tmp_path))
        assert not list(tmp_path.iterdir())

    def test_unexpected_grid(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sea, "_get", lambda url, timeout=120: _nc("sst", _sst()[:, :, :6], SST_LAT, SST_LON[:6]))
        with pytest.raises(sea.SeaDataError, match="unexpected OISST grid"):
            sea.ensure_sea_temperature(str(tmp_path))


class TestSeaAir:
    def test_regular_grid_in_celsius(self, tmp_path, served):
        path = sea.ensure_sea_air(str(tmp_path))
        assert len(served) == 1 and "air.2m.mon.ltm.1981-2010" in served[0]
        grid = sea.load_sea_air(resolve_wind_period("july"), str(tmp_path))
        assert grid.geo == (0.0, 90.0, 0.5) and grid.values.shape == (360, 720)
        # 7 °C plus 10 °C for each 120° east, smoothly in between, wrapping at the date line
        lon = (np.arange(720) + 0.5) * 0.5
        expected = 7 + np.interp(lon, [0, 120, 240, 360], [0, 10, 20, 0])
        assert grid.values[180] == pytest.approx(expected, abs=0.01)
        assert grid.values[0] == pytest.approx(grid.values[180], abs=0.01)   # the poles take the nearest row
        assert sea.ensure_sea_air(str(tmp_path)) == path and len(served) == 1
