"""Tests for currents.py — reducing the drifter climatology, sampling and drawing the arrows (no network)."""

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import currents as c
from rotation import globe_projection


def _field():
    """A 1° field: eastward 0.5 m/s everywhere at sea, land west of 0°; warm north of the equator."""
    lon = np.arange(-179.5, 180)
    lat = np.arange(-72.5, 85)
    u = np.where(lon[None, :] >= 0, 0.5, np.nan) * np.ones((len(lat), 1))
    v = np.where(np.isfinite(u), 0.0, np.nan)
    warmth = np.where(lat[:, None] > 0, 5.0, -5.0) * np.ones((1, len(lon)))
    return c.CurrentField(lon, lat, u, v, warmth)


class TestReduce:
    def test_block_mean_ignores_gaps(self):
        grid = np.array([[1.0, np.nan], [3.0, np.nan]])
        assert c.block_mean(grid, 2).tolist() == [[2.0]]
        assert np.isnan(c.block_mean(np.full((2, 2), np.nan), 2)[0, 0])

    def test_warmth_is_against_the_latitude(self):
        lon = np.arange(8) * 0.25
        lat = np.arange(4) * 0.25
        sst = np.tile([10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0], (4, 1))
        table = c.reduce_drifters(lon, lat, np.zeros((4, 8)), np.zeros((4, 8)), sst, block=4)
        assert table["warmth"].tolist() == [[-5.0, 5.0]]
        assert table["lon"].tolist() == [0.375, 1.375] and table["lat"].tolist() == [0.375]


class TestSampling:
    def test_cells_and_the_sea_only(self):
        u, v, warmth = c.sample(_field(), np.array([10.2, -10.0, 10.2]), np.array([20.0, 20.0, -30.0]))
        assert u[0] == 0.5 and np.isnan(u[1]) and warmth.tolist()[0::2] == [5.0, -5.0]

    def test_square_root_lengths(self):
        su, sv = c.scaled(np.array([0.25, 0.0]), np.array([0.0, 0.0]))
        assert su.tolist() == [0.5, 0.0] and sv.tolist() == [0.0, 0.0]


class TestDrawing:
    def _globe(self, lat, lon):
        ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
        assert isinstance(ax, GeoAxes)
        ax.set_global()
        return ax

    def test_arrows_at_sea(self):
        quiver = c.add_currents_overlay(self._globe(0, 30), _field(), across=20)
        assert quiver is not None
        assert np.all(quiver.U > 0)                                 # all eastward, none over the "land"

    def test_no_sea_in_sight(self):
        assert c.add_currents_overlay(self._globe(0, -90), _field(), across=10) is None

    def test_key(self):
        legend = c.add_currents_legend(self._globe(0, 0))
        labels = [t.get_text() for t in legend.get_texts()]
        assert labels[:3] == ["0.1 m/s", "0.5 m/s", "1 m/s"] and len(labels) == 3 + len(c.WARMTH_BANDS)

    def test_download_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(c.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
        with pytest.raises(c.CurrentsDataError, match="drifter climatology"):
            c.ensure_currents_data(str(tmp_path))
