"""Tests for cyclones.py — Saffir-Simpson categories, reading the tracks, caching and drawing (no network)."""

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

import cyclones as cy
from rotation import globe_projection


@pytest.mark.parametrize("wind, cat", [(None, -1), (20, -1), (34, 0), (64, 1), (100, 3), (140, 5), (np.nan, -1)])
def test_categories(wind, cat):
    assert cy.category(wind) == cat


def _record(nature="TS", usa=None, wmo=None, season=2000):
    return {"NATURE": nature, "USA_WIND": usa, "WMO_WIND": wmo, "SEASON": season}


def test_only_tropical_steps_of_storm_strength():
    records = [_record(usa=70), _record(nature="ET", usa=90), _record(wmo=20), _record(usa=40, wmo=120),
               _record(usa=50), _record(usa=50)]
    lines = [[(0, 0), (1, 1)], [(0, 0), (1, 1)], [(0, 0), (1, 1)], [(5, 5), (6, 6)],
             [(179.5, 10), (-179.5, 10)], [(0, 0)]]
    table = cy.read_tracks(records, lines)
    assert table["category"].tolist() == [1, 4]                    # the stronger of the two agencies' winds
    assert table["lon0"].tolist() == [0, 5]                         # across the date line and single points left out


def test_load_from_the_cache_by_season(tmp_path):
    table = {"lon0": np.zeros(3, np.float32), "lat0": np.zeros(3, np.float32), "lon1": np.ones(3, np.float32),
             "lat1": np.ones(3, np.float32), "season": np.array([1985, 2000, 2020], np.int16),
             "category": np.array([0, 3, 5], np.int8)}
    np.savez(tmp_path / f"ibtracs_since{cy.FIRST_YEAR}_v{cy.DATA_VERSION}.npz", **table)
    assert cy.load_tracks(2000, str(tmp_path)).category.tolist() == [3, 5]
    with pytest.raises(ValueError, match="start in 1980"):
        cy.load_tracks(1970, str(tmp_path))


def test_download_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cy, "_download", lambda url, dest, label: open(dest, "wb").write(b"not a zip"))
    with pytest.raises(cy.CycloneDataError):
        cy.load_tracks(cache_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_drawing_and_key():
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(15, -60))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    tracks = cy.Tracks(np.array([-60.0, 120.0]), np.array([15.0, 15.0]), np.array([-61.0, 121.0]),
                       np.array([16.0, 16.0]), np.array([2000, 2000]), np.array([3, 3]))
    cy.draw_tracks(ax, tracks)
    assert sum(len(c.get_segments()) for c in ax.collections if isinstance(c, LineCollection)) == 1   # the other side of the world is not drawn
    legend = cy.add_cyclones_legend(ax, 1990)
    assert len(legend.get_texts()) == 6 and "since 1990" in legend.get_title().get_text()
