"""Tests for wind.py — periods, reading and caching the means, sampling, arrows and key (no network)."""

import io
import urllib.error

import h5py
import numpy as np
import pytest
from matplotlib.figure import Figure

import cartopy.crs as ccrs

import wind

LAT = np.array([60.0, 0.0, -60.0])               # north to south, as in the PSL files
LON = np.array([0.0, 120.0, 240.0])


def _nc(var, values, lat=LAT, lon=LON, missing=-9.96921e36, scale=1.0):
    """Bytes of a minimal PSL-style netCDF-4 file holding *var*."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        f["lat"], f["lon"] = lat, lon
        ds = f.create_dataset(var, data=np.asarray(values, np.float32))
        ds.attrs["missing_value"] = np.array([missing], np.float32)
        ds.attrs["scale_factor"] = np.array([scale], np.float32)
        ds.attrs["add_offset"] = np.array([0.0], np.float32)
    return buf.getvalue()


def _monthly(fill):
    """(12, 3, 3) array whose month m (1-12) holds fill(m)."""
    return np.stack([np.full((3, 3), fill(m), np.float32) for m in range(1, 13)])


class TestPeriods:
    @pytest.mark.parametrize("spec", [None, "", "year", "Annual"])
    def test_the_year(self, spec):
        assert wind.resolve_wind_period(spec) == ("year", "the year", tuple(range(1, 13)))

    @pytest.mark.parametrize("spec, label, months", [
        ("DJF", "December–February", (12, 1, 2)),
        ("jja", "June–August", (6, 7, 8)),
        ("jul", "July", (7,)),
        ("July", "July", (7,)),
        ("7", "July", (7,)),
        ("sept", "September", (9,)),
    ])
    def test_seasons_and_months(self, spec, label, months):
        period = wind.resolve_wind_period(spec)
        assert (period.label, period.months) == (label, months)

    @pytest.mark.parametrize("spec", ["ju", "13", "0", "winter", "monsoon"])
    def test_unknown(self, spec):
        with pytest.raises(ValueError, match="Unknown wind period"):
            wind.resolve_wind_period(spec)


class TestReadVariable:
    def test_missing_values_and_scaling(self):
        values = _monthly(lambda m: m)
        values[0, 0, 0] = -9.96921e36
        out, lat, lon = wind.read_variable(_nc("uwnd", values, scale=0.5), "uwnd")
        assert np.isnan(out[0, 0, 0]) and out[1, 0, 0] == 1.0 and out[11, 2, 2] == 6.0
        assert lat.tolist() == LAT.tolist() and lon.tolist() == LON.tolist()

    def test_wrong_shape(self):
        with pytest.raises(wind.WindDataError, match="unexpected shape"):
            wind.read_variable(_nc("uwnd", np.zeros((3, 3, 3))), "uwnd")


@pytest.fixture
def files(monkeypatch):
    """Serve synthetic u, v and speed files; returns the list of URLs fetched."""
    data = {
        "uwnd": _nc("uwnd", _monthly(lambda m: m)),              # month m blows east at m m/s
        "vwnd": _nc("vwnd", _monthly(lambda m: 0.0)),
        "wspd": _nc("wspd", _monthly(lambda m: 2.0 * m)),
    }
    fetched = []

    def get(url, timeout=120):
        fetched.append(url)
        return data[url.rsplit("/", 1)[1].split(".")[0]]

    monkeypatch.setattr(wind, "_get", get)
    return fetched


class TestEnsureWindData:
    def test_downloaded_once_then_cached(self, tmp_path, files):
        first = wind.ensure_wind_data(str(tmp_path))
        assert wind.ensure_wind_data(str(tmp_path)) == first
        assert len(files) == 3 and all("10m.mon.ltm.1991-2020" in url for url in files)
        with np.load(first) as cached:
            assert cached["uwnd"].shape == (12, 3, 3)

    def test_download_failure(self, tmp_path, monkeypatch):
        def fail(url, timeout=120):
            raise urllib.error.URLError("offline")
        monkeypatch.setattr(wind, "_get", fail)
        with pytest.raises(wind.WindDataError, match="offline"):
            wind.ensure_wind_data(str(tmp_path))
        assert not list(tmp_path.iterdir())

    def test_files_on_different_grids(self, tmp_path, monkeypatch):
        data = {"uwnd": _nc("uwnd", _monthly(lambda m: 1)),
                "vwnd": _nc("vwnd", _monthly(lambda m: 1), lat=LAT + 1)}
        monkeypatch.setattr(wind, "_get", lambda url, timeout=120: data[url.rsplit("/", 1)[1].split(".")[0]])
        with pytest.raises(wind.WindDataError, match="different grids"):
            wind.ensure_wind_data(str(tmp_path))


class TestLoadWind:
    def test_months_are_weighted_by_length(self, tmp_path, files):
        field = wind.load_wind(wind.resolve_wind_period("djf"), str(tmp_path))
        expected = (31 * 12 + 31 * 1 + 28.25 * 2) / (31 + 31 + 28.25)
        assert field.u[0, 0] == pytest.approx(expected)
        assert field.speed[0, 0] == pytest.approx(2 * expected)

    def test_grid_runs_south_to_north_and_wraps(self, tmp_path, files):
        field = wind.load_wind(wind.resolve_wind_period("year"), str(tmp_path))
        assert field.lat.tolist() == [-60.0, 0.0, 60.0]
        assert field.lon.tolist() == [0.0, 120.0, 240.0, 360.0]
        assert field.u.shape == (3, 4)


def _field(u, v, speed):
    lat = np.array([-80.0, 0.0, 80.0])
    lon = np.array([0.0, 180.0, 360.0])
    return wind.WindField(lat, lon, *(np.asarray(a, float) for a in (u, v, speed)))


class TestSampling:
    def test_steadiness_is_mean_wind_over_mean_speed(self):
        u, v, steadiness = wind.sample_wind(
            _field(np.full((3, 3), 3), np.full((3, 3), 4), np.full((3, 3), 10)), np.array([10.0]), np.array([5.0]))
        assert (u[0], v[0], steadiness[0]) == pytest.approx((3, 4, 0.5))

    def test_calm_and_rounding_stay_between_0_and_1(self):
        u = np.array([[0, 0, 0], [5, 5, 5], [0, 0, 0]])         # calm at the edges
        speed = np.array([[0, 0, 0], [4, 4, 4], [0, 0, 0]])     # speeds rounded below the mean wind
        _, _, steadiness = wind.sample_wind(
            _field(u, np.zeros((3, 3)), speed), np.array([0.0, 0.0]), np.array([-80.0, 0.0]))
        assert steadiness.tolist() == [0.0, 1.0]

    def test_poles_and_the_date_line(self):
        u = np.array([[1, 3, 1], [1, 3, 1], [1, 3, 1]])         # 1 at 0° and 360°, 3 at 180°
        field = _field(u, np.zeros((3, 3)), np.full((3, 3), 5))
        got, _, _ = wind.sample_wind(field, np.array([-90.0, 270.0, 0.0]), np.array([89.9, 0.0, -89.9]))
        assert got.tolist() == pytest.approx([2.0, 2.0, 1.0])

    def test_arrows_fill_the_visible_disc_evenly(self):
        proj = ccrs.Orthographic(20, 40)
        a = proj.x_limits[1]
        lon, lat, spacing = wind.arrow_points(proj, (-a, a, -a, a), across=20)
        assert spacing == pytest.approx(a / 10)
        assert 250 < len(lon) < 315                              # about π/4 of the 400 grid points
        xy = proj.transform_points(ccrs.PlateCarree(), lon, lat)
        assert np.all(np.hypot(xy[:, 0], xy[:, 1]) <= a - spacing / 2 + 1)

    def test_arrows_on_a_zoomed_map(self):
        proj = ccrs.Orthographic(10, 46)
        half = 1_000_000.0
        lon, lat, spacing = wind.arrow_points(proj, (-half, half, -half, half), across=10)
        assert spacing == 200_000 and len(lon) == 60             # the 10 × 10 grid's points within 900 km
        assert np.all(np.abs(lat - 46) < 12) and np.all(np.abs(lon - 10) < 16)


class TestDrawing:
    def _ax(self):
        fig = Figure(figsize=(8, 8))
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        ax.set_global()
        return ax

    def test_overlay_draws_one_arrow_per_point_coloured_by_steadiness(self, monkeypatch):
        u = np.full((3, 3), 4.0)
        speed = np.array([[20.0] * 3, [5.0] * 3, [4.0] * 3])     # variable south, steady north
        monkeypatch.setattr(wind, "load_wind", lambda period, cache_dir=None: _field(u, np.zeros((3, 3)), speed))
        ax = self._ax()
        quiver = wind.add_wind_overlay(ax, wind.resolve_wind_period("year"), across=12)
        assert quiver.N > 50 and quiver.get_zorder() == 7.5
        steadiness = np.asarray(quiver.get_array())
        assert steadiness.min() < 0.3 and steadiness.max() > 0.8
        cmap, norm = wind.steadiness_colours()
        assert cmap.N == len(wind.STEADINESS_BANDS) and norm(0.95) == 3 and norm(0.1) == 0

    def test_key(self):
        ax = self._ax()
        key = wind.add_wind_legend(ax, wind.resolve_wind_period("djf"))
        assert "December–February" in key.get_title().get_text()
        assert [t.get_text() for t in key.get_texts()] == [
            "2 m/s", "5 m/s", "10 m/s", "variable", "changeable", "steady", "very steady"]
        assert key in ax.artists
        ax.get_figure().savefig(io.BytesIO(), format="png", dpi=20)   # the arrow handles draw
