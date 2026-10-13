"""Tests for zoogeography.py — Holt et al.'s realms and regions: reading, caching, colours, labels and key (no network)."""

import io
import zipfile
from collections import Counter

import numpy as np
import pytest
import shapefile
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import biomes
import zoogeography as zoo
from rotation import globe_projection

FOLDER = "CMEC regions & realms/"
M = zoo._METRES_PER_DEGREE


def _square(west, south, east, north):
    """A closed ring round a box, clockwise on the map."""
    return [(west, south), (west, north), (east, north), (east, south), (west, south)]


def _shapefile(field, rows, scale=1.0):
    """(shp, shx, dbf) bytes of polygons, each ``(name, [rings])``, coordinates multiplied by *scale*."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf,  # pyright: ignore[reportArgumentType]
                          shapeType=shapefile.POLYGON) as writer:
        writer.field(field, "C", 50)
        for name, rings in rows:
            writer.poly([[(x * scale, y * scale) for x, y in ring] for ring in rings])
            writer.record(name)
    return shp.getvalue(), shx.getvalue(), dbf.getvalue()


def _archive(realm="Palearctic"):
    """An in-memory zip laid out like CMEC's: realms in degrees, regions in metres about 12°E."""
    files = {
        zoo.REALM_FILE: _shapefile("Realm", [(realm, [_square(0, 40, 10, 50)]),
                                             ("Oriental", [_square(100, 10, 110, 20)])]),
        # Regions about 12°E: x = -12 is the prime meridian
        zoo.REGION_FILE: _shapefile("Regions", [("Eurasian", [_square(-12, 40, -2, 50)]),
                                                ("Indo-Malayan", [_square(88, 10, 98, 20), _square(88, 0, 92, 4),
                                                                  _square(-60, -20, -59.5, -19.5)])],
                                    scale=M),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, parts in files.items():
            for ext, data in zip(("shp", "shx", "dbf"), parts):
                zf.writestr(FOLDER + f"{name}.{ext}", data)
        zf.writestr(FOLDER + "plotKML_0.2-5.tar.gz", b"unrelated")
    return buf.getvalue()


def _globe(lat, lon):
    ax = Figure(figsize=(4, 4)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


class TestBuild:
    def test_realms_gridded_and_regions_in_degrees(self):
        with zipfile.ZipFile(io.BytesIO(_archive())) as archive:
            data = zoo.build(archive, cells_per_degree=1)
        grid = data["realms"]
        order = list(zoo.HOLT_REALMS)
        assert (grid == order.index("Palearctic")).sum() == 100
        assert grid[90 - 45, 180 + 5] == order.index("Palearctic")
        assert grid[90 - 15, 180 + 105] == order.index("Oriental")
        assert (grid != biomes.NODATA).sum() == 200
        assert list(data["region_names"]) == ["Eurasian", "Indo-Malayan"]

    def test_unknown_realm(self):
        with zipfile.ZipFile(io.BytesIO(_archive(realm="Atlantis"))) as archive:
            with pytest.raises(zoo.HoltDataError, match="unknown realm 'Atlantis'"):
                zoo.build(archive, cells_per_degree=1)

    def test_missing_file(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", b"")
        with zipfile.ZipFile(buf) as archive:
            with pytest.raises(zoo.HoltDataError, match="newRealms.shp is missing"):
                zoo.build(archive, cells_per_degree=1)


class TestCache:
    def test_built_once_then_reused(self, tmp_path, monkeypatch):
        downloads = []

        def fake_download(url, dest, label):
            downloads.append(url)
            with open(dest, "wb") as fh:
                fh.write(_archive())

        monkeypatch.setattr(zoo, "_download", fake_download)
        monkeypatch.setattr(zoo, "CELLS_PER_DEGREE", 1)
        holt = zoo.load_holt(str(tmp_path))
        assert downloads == [zoo.HOLT_URL]
        assert holt.realms.shape == (180, 360)
        assert [r.name for r in holt.regions] == ["Eurasian", "Indo-Malayan"]
        assert holt.regions[0].outline.bounds == pytest.approx((-12, 40, -2, 50))   # degrees about 12°E
        assert [p.name for p in tmp_path.iterdir()] == ["holt2013_1pd_v1.npz"]      # the zip is gone
        zoo.load_holt(str(tmp_path))
        assert len(downloads) == 1

    def test_failures_are_holt_data_errors(self, tmp_path, monkeypatch):
        def broken(url, dest, label):
            with open(dest, "wb") as fh:
                fh.write(b"not a zip")

        monkeypatch.setattr(zoo, "_download", broken)
        with pytest.raises(zoo.HoltDataError, match="Could not read"):
            zoo.ensure_holt_data(str(tmp_path))
        assert not [p for p in tmp_path.iterdir() if p.suffix == ".npz"]


@pytest.fixture
def holt(tmp_path, monkeypatch):
    monkeypatch.setattr(zoo, "_download", lambda url, dest, label: open(dest, "wb").write(_archive()))
    monkeypatch.setattr(zoo, "CELLS_PER_DEGREE", 1)
    return zoo.load_holt(str(tmp_path))


class TestDrawing:
    def test_colours(self):
        rgba = zoo.realm_rgba(np.array([[0, biomes.NODATA]], np.uint16), alpha=0.5)
        assert rgba[0, 0].tolist() == [*zoo.HOLT_REALMS["Nearctic"][1], 128]
        assert rgba[0, 1, 3] == 0

    def test_labels_go_on_the_largest_part_that_shows(self, holt):
        indo = holt.regions[1]
        lon, lat = zoo.label_point(_globe(10, 100), indo)
        assert 100 < lon < 110 and 10 < lat < 20                         # the big square, shifted back by 12°
        assert zoo.label_point(_globe(-55, 101), indo) == pytest.approx((102, 2), abs=1)  # only the smaller part shows
        assert zoo.label_point(_globe(-10, -80), indo) is None            # a speck is not named

    def test_regions_outlined_and_named(self, holt):
        ax = _globe(30, 50)
        zoo.draw_holt_regions(ax, holt)
        assert [t.get_text() for t in ax.texts] == ["Eurasian", "Indo-Malayan"]
        assert len(ax.collections) == 1
        ax = _globe(30, 50)
        zoo.draw_holt_regions(ax, holt, labels=False)
        assert not ax.texts

    def test_key_names_the_realms_on_the_map(self):
        ax = _globe(0, 0)
        order = list(zoo.HOLT_REALMS)
        legend = zoo.add_holt_realm_legend(ax, Counter({order.index("Oriental"): 3, order.index("Nearctic"): 1}))
        assert legend is not None
        assert [t.get_text() for t in legend.get_texts()] == [
            f"Nearctic: {zoo.HOLT_REALMS['Nearctic'][0]}", f"Oriental: {zoo.HOLT_REALMS['Oriental'][0]}"]
        assert "Holt" in legend.get_title().get_text()
        assert zoo.add_holt_realm_legend(ax, Counter()) is None

    def test_eleven_realms_with_the_credit_science_asks_for(self):
        assert len(zoo.HOLT_REALMS) == 11
        assert "Science/AAAS" in zoo.HOLT_ATTRIBUTION
