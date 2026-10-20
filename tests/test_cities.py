"""Tests for cities.py (no network; a small GHSL-style GeoPackage written here)."""

import os
import sqlite3
import zipfile

import numpy as np
import pytest
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure
from pyproj import Transformer

import cities
from rotation import globe_projection

TO_MOLLWEIDE = Transformer.from_crs("EPSG:4326", "ESRI:54009", always_xy=True)


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _blob(geometry):
    """A GeoPackage geometry in Mollweide metres, as GHSL's: the header, then WKB."""
    metres = shapely.transform(geometry, lambda xy: np.column_stack(TO_MOLLWEIDE.transform(xy[:, 0], xy[:, 1])))
    return b"GP\x00\x01" + (54009).to_bytes(4, "little") + shapely.to_wkb(metres)


def _box(lon, lat, half=0.2):
    return shapely.box(lon - half, lat - half, lon + half, lat + half)


# id, name, country, (lon, lat), population in 1975 and 2025
CENTRES = [
    (1, "Bigtown", "Somewhere", (10.0, 45.0), 3_000_000, 12_000_000),
    (2, "Midtown", "Somewhere", (30.0, 50.0), 400_000, 1_500_000),
    (3, "Smalltown", "Elsewhere", (45.0, 40.0), 60_000, 90_000),
]


def _write_gpkg(path):
    pops = [f"MT_POP_TOT_{y}" for y in cities.YEARS]
    db = sqlite3.connect(path)
    with db:
        db.execute(f'CREATE TABLE "{cities.CENTRES_TABLE}" (fid INTEGER PRIMARY KEY, geom BLOB, ID_MTUC_G0 INT, '
                   f'GC_UCN_MAI_2025 TEXT, GC_CNT_GAD_2025 TEXT, {", ".join(p + " REAL" for p in pops)})')
        for year in (1975, 2025):
            db.execute(f'CREATE TABLE "{cities.OUTLINE_TABLE.format(year=year)}" (fid INTEGER PRIMARY KEY, '
                       'geom BLOB, ID_UC_G0 INT)')
        for centre_id, name, country, (lon, lat), p1975, p2025 in CENTRES:
            values = [p1975 + (p2025 - p1975) * (y - 1975) / 50 for y in cities.YEARS]
            db.execute(f'INSERT INTO "{cities.CENTRES_TABLE}" VALUES (?, ?, ?, ?, ?, {", ".join("?" * len(pops))})',
                       (centre_id, _blob(shapely.Point(lon, lat)), centre_id, name, country, *values))
            db.execute(f'INSERT INTO "{cities.OUTLINE_TABLE.format(year=2025)}" VALUES (?, ?, ?)',
                       (centre_id, _blob(_box(lon, lat, 0.5)), centre_id))
        # 1975: Bigtown was two towns, one under its own id, both off its 2025 middle
        table = cities.OUTLINE_TABLE.format(year=1975)
        db.execute(f'INSERT INTO "{table}" VALUES (1, ?, 1)', (_blob(_box(10.3, 45.3, 0.1)),))
        db.execute(f'INSERT INTO "{table}" VALUES (2, ?, 901)', (_blob(_box(9.7, 44.7, 0.05)),))
        db.execute(f'INSERT INTO "{table}" VALUES (3, ?, 902)', (_blob(_box(40.0, 0.0, 0.05)),))   # in no centre
    db.close()
    return path


@pytest.fixture
def cache(tmp_path):
    folder = tmp_path / "cities"
    folder.mkdir()
    _write_gpkg(str(folder / cities.GPKG_NAME))
    return str(folder)


@pytest.mark.parametrize("year, value", [(None, 2025), ("", 2025), (1975, 1975), (" 2030 ", 2030)])
def test_years(year, value):
    assert cities.resolve_cities_year(year) == value


@pytest.mark.parametrize("year", [1977, 2035, "soon"])
def test_bad_years(year):
    with pytest.raises(ValueError, match="Cities are mapped for 1975-2030"):
        cities.resolve_cities_year(year)


@pytest.mark.parametrize("radius, floor", [(None, 1_000_000), (5000, 1_000_000), (2500, 250_000), (300, 50_000)])
def test_default_min_population(radius, floor):
    assert cities.default_min_population(radius) == floor


class TestData:
    def test_downloaded_once_and_unzipped(self, tmp_path, monkeypatch):
        gpkg = _write_gpkg(str(tmp_path / "source.gpkg"))
        calls = []

        def fake_download(url, dest, label=""):
            calls.append(url)
            with zipfile.ZipFile(dest, "w") as z:
                z.write(gpkg, f"GHSL/{cities.GPKG_NAME}")

        monkeypatch.setattr(cities, "_download", fake_download)
        folder = str(tmp_path / "c")
        path = cities.ensure_cities_data(folder)
        assert os.path.isfile(path) and os.listdir(folder) == [cities.GPKG_NAME]          # the zip is gone
        cities.ensure_cities_data(folder)
        assert calls == [cities.GHSL_URL]

    def test_download_failure(self, tmp_path, monkeypatch):
        def offline(url, dest, label=""):
            raise OSError("no network")

        monkeypatch.setattr(cities, "_download", offline)
        with pytest.raises(cities.CitiesDataError, match="Could not get the GHSL urban centres: no network"):
            cities.ensure_cities_data(str(tmp_path))

    def test_centres_in_degrees(self, cache):
        found = cities.load_cities(2025, cache)
        assert found.names == ["Bigtown", "Midtown", "Smalltown"] and found.countries[2] == "Elsewhere"
        assert found.lon.tolist() == pytest.approx([10, 30, 45], abs=1e-6)
        assert found.lat.tolist() == pytest.approx([45, 50, 40], abs=1e-6)
        assert found.population.tolist() == [12_000_000, 1_500_000, 90_000]
        assert shapely.bounds(found.outlines[0]).tolist() == pytest.approx([9.5, 44.5, 10.5, 45.5], abs=1e-6)

    def test_earlier_towns_join_their_later_centre(self, cache):
        found = cities.load_cities(1975, cache)
        assert found.population.tolist() == [3_000_000, 400_000, 60_000]
        assert len(shapely.get_parts(found.outlines[0])) == 2                 # Bigtown's two towns
        assert found.outlines[1] is None and found.outlines[2] is None         # not urban centres yet
        assert cities.anchor(found, 0) != (10.0, 45.0)                         # named on its 1975 patch
        assert shapely.intersects_xy(found.outlines[0], *cities.anchor(found, 0))

    def test_cached(self, cache, monkeypatch):
        cities.load_cities(2025, cache)
        monkeypatch.setattr(cities, "_build_outlines", lambda *a: pytest.fail("built again"))
        monkeypatch.setattr(cities, "_build_centres", lambda *a: pytest.fail("built again"))
        assert cities.load_cities(2025, cache).names[0] == "Bigtown"

    def test_unreadable(self, tmp_path):
        with open(tmp_path / cities.GPKG_NAME, "wb") as f:
            f.write(b"not a database")
        with pytest.raises(cities.CitiesDataError, match="Could not read the GHSL urban centres"):
            cities.load_cities(2025, str(tmp_path))


class TestDrawing:
    def test_sizes(self):
        assert cities.label_size(1e6) == 7 and cities.label_size(50e6) == 13 and cities.label_size(5e4) == 6.5

    def test_drawn_named_and_keyed(self, cache):
        found = cities.load_cities(2025, cache)
        ax = _globe(45, 25)
        ax.figure.set_size_inches(12, 12)                                       # room for both names
        shown = cities.draw_cities(ax, found, 1_000_000)
        assert shown == [0, 1]                                                  # biggest first, Smalltown too small
        assert [t.get_text() for t in ax.texts] == ["Bigtown", "Midtown"]
        assert ax.texts[0].get_fontweight() == "bold" and ax.texts[1].get_fontweight() == "normal"
        legend = cities.add_cities_legend(ax, found, 1_000_000, len(shown))
        assert legend.get_title().get_text() == ("Urban centres of over 1 million people in 2025: 2 in view "
                                                 "(GHS Urban Centre Database 2025)")

    def test_overlapping_names_left_out(self, cache):
        found = cities.load_cities(2025, cache)
        ax = _globe(0, 0)                                                       # far off, all close together
        ax.figure.set_size_inches(1, 1)
        cities.draw_cities(ax, found, 50_000)
        assert len(ax.texts) < 3

    def test_beyond_the_horizon(self, cache):
        found = cities.load_cities(2025, cache)
        ax = _globe(-45, -170)
        assert cities.draw_cities(ax, found, 50_000) == [] and not ax.texts

    def test_projected_year_in_key(self, cache):
        found = cities.load_cities(2025, cache)._replace(year=2030)
        legend = cities.add_cities_legend(_globe(), found, 250_000, 0)
        assert "250,000 people in 2030 (projected): 0 in view" in legend.get_title().get_text()
