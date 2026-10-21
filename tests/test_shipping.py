"""Tests for shipping.py (no network; a small ship density BigTIFF is written here)."""

import json
import os
import struct
import zipfile

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import shipping as sh
from climate import View
from rotation import globe_projection

TILE = 128


def _bigtiff(grid, west=-180.0, north=90.0, scale=None, reverse=True):
    """A little-endian BigTIFF of int32 *grid* in uncompressed 128 × 128 tiles, stored last tile first (as Esri
    stores them out of order), with its IFD at the end."""
    height, width = grid.shape
    scale = scale or 360 / width
    across, down = -(-width // TILE), -(-height // TILE)
    padded = np.zeros((down * TILE, across * TILE), "<i4")
    padded[:height, :width] = grid
    tiles = [padded[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE].tobytes()
             for r in range(down) for c in range(across)]
    order = list(range(len(tiles)))[::-1] if reverse else list(range(len(tiles)))
    body = bytearray(b"II+\x00" + struct.pack("<HHQ", 8, 0, 0))
    offsets = [0] * len(tiles)
    for i in order:
        offsets[i] = len(body)
        body += tiles[i]
    arrays = bytearray()
    entries = [(256, 4, [width]), (257, 4, [height]), (258, 3, [32]), (259, 3, [1]), (322, 3, [TILE]),
               (323, 3, [TILE]), (324, 16, offsets), (325, 16, [TILE * TILE * 4] * len(tiles)), (339, 3, [2]),
               (33550, 12, [scale, scale, 0.0]), (33922, 12, [0.0, 0.0, 0.0, west, north, 0.0])]
    ifd = len(body)
    table_size = 8 + 20 * len(entries) + 8
    packed = []
    for tag, kind, values in entries:
        fmt = {3: "<H", 4: "<I", 16: "<Q", 12: "<d"}[kind]
        data = b"".join(struct.pack(fmt, v) for v in values)
        if len(data) <= 8:
            packed.append(struct.pack("<HHQ", tag, kind, len(values)) + data.ljust(8, b"\0"))
        else:
            packed.append(struct.pack("<HHQQ", tag, kind, len(values), ifd + table_size + len(arrays)))
            arrays += data
    body += struct.pack("<Q", len(entries)) + b"".join(packed) + struct.pack("<Q", 0) + arrays
    body[8:16] = struct.pack("<Q", ifd)
    return bytes(body)


def _zip(path, grid, **kwargs):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ShipDensity_Commercial1.tif", _bigtiff(grid, **kwargs))
        archive.writestr("ShipDensity_Commercial1.tfw", "1\n0\n0\n-1\n-180\n90\n")
    return str(path)


def _grid():
    """256 × 128 cells of 1.40625°: a lane along row 40 (east-west) of 10⁶ positions, a quiet sea elsewhere."""
    grid = np.full((128, 256), 10, np.int32)
    grid[40:42, :] = 1_000_000
    grid[100:, :] = 0                       # land
    grid[0, 0] = sh.NODATA
    return grid


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(6, 6)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


@pytest.mark.parametrize("text, kind", [(None, "commercial"), ("", "commercial"), ("Fishing", "fishing"),
                                        ("oil and gas", "oil-gas"), ("ferries", "passenger"), ("global", "all")])
def test_kinds(text, kind):
    assert sh.resolve_ship_kind(text) == kind


def test_unknown_kind():
    with pytest.raises(ValueError, match="Unknown kind of ships 'submarines'"):
        sh.resolve_ship_kind("submarines")


class TestBuild:
    def test_tiles_and_world_grid(self, tmp_path):
        sh.build_cache(_zip(tmp_path / "ships.zip", _grid()), "commercial", str(tmp_path))
        world = np.load(tmp_path / "commercial_world_v1.npz")
        tiles = np.load(tmp_path / "commercial_tiles_v1.npz")
        meta = json.loads(str(world["meta"]))
        assert meta["rows"] == 64 and meta["cols"] == 128 and meta["fine"] == pytest.approx(2 * 360 / 256)
        assert tiles.files == ["meta", "r0c0"]
        fine = tiles["r0c0"]
        assert fine.shape == (64, 128)
        lane = round(np.log10(1 + 1_000_000) * sh.STEPS_PER_DECADE)
        assert (fine[20] == lane).all() and fine[21, 5] == round(np.log10(11) * sh.STEPS_PER_DECADE)
        assert (fine[50:] == 0).all()                       # land
        assert fine[0, 0] == round(np.log10(1 + 7.5) * sh.STEPS_PER_DECADE)   # no data counts as none
        coarse = world["world"]
        assert coarse.shape == (16, 32)
        assert coarse[5, 3] == round(np.log10(1 + (2 * 1_000_000 + 6 * 10) / 8) * sh.STEPS_PER_DECADE)

    def test_tiles_in_order_too(self, tmp_path):
        sh.build_cache(_zip(tmp_path / "ships.zip", _grid(), reverse=False), "commercial", str(tmp_path))
        assert np.load(tmp_path / "commercial_tiles_v1.npz")["r0c0"][20, 7] > 0

    def test_not_a_bigtiff(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "bad.zip", "w") as archive:
            archive.writestr("x.tif", b"II*\x00" + bytes(64))
        with pytest.raises(sh.ShippingDataError, match="BigTIFF"):
            sh.build_cache(str(tmp_path / "bad.zip"), "commercial", str(tmp_path))

    def test_no_tiff(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "bad.zip", "w") as archive:
            archive.writestr("readme.txt", "nothing")
        with pytest.raises(sh.ShippingDataError, match="holds no TIFF"):
            sh.build_cache(str(tmp_path / "bad.zip"), "commercial", str(tmp_path))

    def test_downloaded_once_then_zip_removed(self, tmp_path, monkeypatch):
        source = _zip(tmp_path / "source.zip", _grid())
        fetched = []

        def fake(url, dest, label):
            fetched.append(url)
            with open(source, "rb") as src, open(dest, "wb") as out:
                out.write(src.read())

        monkeypatch.setattr(sh, "_download", fake)
        cache = tmp_path / "cache"
        assert sh.ensure_shipping_data("fishing", str(cache)) == str(cache)
        assert sh.ensure_shipping_data("fishing", str(cache)) == str(cache)
        assert fetched == [sh.CATALOG_URL + sh.KINDS["fishing"].file]
        assert sorted(os.listdir(cache)) == ["fishing_tiles_v1.npz", "fishing_world_v1.npz"]

    def test_download_failure(self, tmp_path, monkeypatch):
        def fail(url, dest, label):
            raise OSError("offline")

        monkeypatch.setattr(sh, "_download", fail)
        with pytest.raises(sh.ShippingDataError, match="Could not get the ship density: offline"):
            sh.ensure_shipping_data("leisure", str(tmp_path))


@pytest.fixture
def cache(tmp_path):
    sh.build_cache(_zip(tmp_path / "ships.zip", _grid()), "commercial", str(tmp_path))
    os.remove(tmp_path / "ships.zip")
    return str(tmp_path)


class TestLoad:
    def test_fine_tiles_for_a_close_view(self, cache):
        cell = 360 / 128
        traffic = sh.load_traffic("commercial", View(0, -30, 30, 20, 50), cell / 2, cache)
        assert traffic.cell == pytest.approx(cell)
        west, east, south, north = traffic.extent
        assert west <= -30 and east >= 30 and south <= 20 and north >= 50
        # The lane (TIFF rows 40-41, fine row 20) runs along 33.75-30.94°N, across the whole view
        lane = np.flatnonzero((traffic.values == traffic.values.max()).all(axis=1))
        assert len(lane) == 1 and north - lane[0] * cell == pytest.approx(90 - 20 * cell)

    def test_world_grid_for_a_globe(self, cache):
        traffic = sh.load_traffic("commercial", View(0, -180, 180, -90, 90), 12.0, cache)
        assert traffic.cell == pytest.approx(360 / 32 * 1)       # 11.25°: coarser than asked is not pooled
        assert traffic.values.shape == (16, 32)

    def test_pooled_to_the_cell_asked(self, cache):
        traffic = sh.load_traffic("commercial", View(0, -180, 180, -90, 90), 23.0, cache)
        assert traffic.cell == pytest.approx(22.5) and traffic.values.shape == (8, 16)

    def test_across_the_date_line(self, cache):
        traffic = sh.load_traffic("commercial", View(180, -20, 20, 20, 50), 360 / 128, cache)
        west, east, _, _ = traffic.extent
        assert west <= -20 and east >= 20 and traffic.values.shape[1] >= 14 and traffic.values.max() > 0

    def test_beyond_the_data(self, cache):
        traffic = sh.load_traffic("commercial", View(0, -10, 10, -90, -89), 360 / 128, cache)
        assert traffic.values.max() == 0


class TestDrawing:
    def test_ranks(self):
        values = np.repeat(np.arange(1, 201, dtype=np.uint8), 10)
        ranks = sh.colour_ranks(values)
        assert ranks[0] == -1 and ranks[50] == -1 and ranks[200] == 1
        assert 0 < ranks[150] < ranks[180] < 1

    def test_too_little_to_rank(self):
        ranks = sh.colour_ranks(np.array([200, 0], np.uint8))
        assert ranks[150] == -1 and ranks[240] == 1

    def test_colours_clear_then_opaque(self):
        lookup = sh._lookup(0.8, np.array([-1.0, 0.0, 1.0]))
        assert lookup[0, 3] == 0 and lookup[1, 3] == round(0.8 * 0.15 * 255) and lookup[2, 3] == round(0.8 * 255)

    def test_overlay_and_key(self, cache):
        ax = _globe(30, 0)
        traffic = sh.add_shipping_overlay(ax, "commercial", regrid_shape=100, cache_dir=cache)
        assert traffic.values.max() > 0 and len(ax.get_images()) == 1
        key = sh.add_shipping_legend(ax, "commercial")
        assert [t.get_text() for t in key.get_texts()] == list(sh.KEY_LABELS)
        assert "commercial ships" in key.get_title().get_text()
