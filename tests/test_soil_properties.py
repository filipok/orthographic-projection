"""Tests for soil_properties.py — names, the int16 GeoTIFF reader, resampling and caching (no network)."""

import struct
import urllib.error
import zlib

import numpy as np
import pytest
from matplotlib.figure import Figure

import cartopy.crs as ccrs

import soil_properties as sp


def _geotiff(grid, x0, y0, cell, tile=16, predictor=2):
    """A little-endian, tiled, deflate-compressed int16 GeoTIFF of *grid*, like SoilGrids'."""
    rows, cols = grid.shape
    padded = np.full((-(-rows // tile) * tile, -(-cols // tile) * tile), sp.NODATA, np.int16)
    padded[:rows, :cols] = grid
    tiles = []
    for r in range(0, padded.shape[0], tile):
        for c in range(0, padded.shape[1], tile):
            block = padded[r:r + tile, c:c + tile].copy()
            if predictor == 2:
                block[:, 1:] = np.diff(block, axis=1)        # wraps, as TIFF predictor 2 does
            tiles.append(zlib.compress(block.astype("<i2").tobytes()))
    n = len(tiles)
    entries = [(256, 4, [cols]), (257, 4, [rows]), (258, 3, [16]), (259, 3, [8]), (277, 3, [1]),
               (317, 3, [predictor]), (322, 4, [tile]), (323, 4, [tile]), (324, 4, None),
               (325, 4, [len(t) for t in tiles]), (339, 3, [2]),
               (33550, 12, [cell, cell, 0.0]), (33922, 12, [0, 0, 0, x0, y0, 0])]
    sizes = {3: 2, 4: 4, 12: 8}
    ifd_size = 2 + 12 * len(entries) + 4
    extra = sum(sizes[t] * (n if v is None else len(v)) for _, t, v in entries
                if sizes[t] * (n if v is None else len(v)) > 4)
    data_start = 8 + ifd_size + extra
    offsets = [data_start + sum(len(t) for t in tiles[:i]) for i in range(n)]
    body, arrays = bytearray(struct.pack("<H", len(entries))), bytearray()
    arrays_at = 8 + ifd_size
    for tag, typ, values in entries:
        values = offsets if values is None else values
        fmt = {3: "H", 4: "I", 12: "d"}[typ]
        raw = struct.pack("<" + fmt * len(values), *values)
        if len(raw) <= 4:
            body += struct.pack("<HHI", tag, typ, len(values)) + raw.ljust(4, b"\x00")
        else:
            body += struct.pack("<HHII", tag, typ, len(values), arrays_at + len(arrays))
            arrays += raw
    body += struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", 8) + bytes(body) + bytes(arrays) + b"".join(tiles)


class TestNames:
    def test_default_depth_is_the_topsoil(self):
        prop, depth = sp.resolve_soil_property("pH")
        assert prop.code == "phh2o" and depth == "0-5cm"

    def test_carbon_stock_has_one_depth(self):
        assert sp.resolve_soil_property("carbon-stock")[1] == "0-30cm"
        with pytest.raises(ValueError, match="0-30cm"):
            sp.resolve_soil_property("carbon-stock", "0-5cm")

    def test_chosen_depth(self):
        assert sp.resolve_soil_property("clay", "30-60 cm")[1] == "30-60cm"

    @pytest.mark.parametrize("name, depth, match", [("acidity", None, "Unknown soil property"),
                                                    ("ph", "0-30cm", "mapped at")])
    def test_bad(self, name, depth, match):
        with pytest.raises(ValueError, match=match):
            sp.resolve_soil_property(name, depth)

    def test_every_property_has_a_label_and_colour_per_band(self):
        for prop in sp.SOIL_PROPERTIES.values():
            assert len(prop.labels) == len(prop.bounds) + 1 == len(sp.band_colours(prop))

    def test_generated_band_labels(self):
        assert sp.SOIL_PROPERTIES["sand"].labels == ("< 20", "20–40", "40–60", "60–80", "≥ 80 %")


class TestReader:
    @pytest.mark.parametrize("predictor", [1, 2])
    def test_decodes_tiles_and_geography(self, predictor):
        grid = (np.arange(20 * 37, dtype=np.int32).reshape(20, 37) * 37 - 9000).astype(np.int16)
        grid[3, 4] = sp.NODATA
        decoded, geo = sp.decode_int16_tiles(_geotiff(grid, -1000.0, 500.0, 5.0, predictor=predictor))
        np.testing.assert_array_equal(decoded, grid)
        assert geo == (-1000.0, 500.0, 5.0)


class TestResampling:
    @pytest.fixture(autouse=True)
    def coarse_grid(self, monkeypatch):
        monkeypatch.setattr(sp, "GRID_CELL", 10.0)          # 14 x 36 output cells

    def test_samples_the_homolosine_cell_under_each_centre(self):
        cell = 1_000_000.0
        x0, y0 = -20_000_000.0, 9_000_000.0
        rows, cols = 18, 40
        grid = np.arange(rows * cols, dtype=np.int16).reshape(rows, cols)
        out = sp.to_lat_lon(grid, x0, y0, cell)
        assert out.shape == (14, 36)
        igh = ccrs.InterruptedGoodeHomolosine()
        for r, c in [(3, 18), (11, 31), (7, 11)]:            # Europe, Australia, South America
            lat, lon = sp.GRID_NORTH - (r + 0.5) * 10, -180 + (c + 0.5) * 10
            x, y = igh.transform_point(lon, lat, ccrs.PlateCarree())
            assert out[r, c] == grid[int((y0 - y) // cell), int((x - x0) // cell)]

    def test_outside_the_source_is_no_data(self):
        grid = np.ones((2, 2), np.int16)                    # covers only a corner of the world
        out = sp.to_lat_lon(grid, 0.0, 2_000_000.0, 1_000_000.0)
        assert (out == sp.NODATA).sum() > 0.9 * out.size


class TestCache:
    def _serve(self, monkeypatch, fail=False):
        calls = []

        def get(url, timeout=120):
            calls.append(url)
            if fail:
                raise urllib.error.URLError("offline")
            return _geotiff(np.full((40, 80), 65, np.int16), -20_037_508.0, 9_000_000.0, 500_000.0)

        monkeypatch.setattr(sp, "_get", get)
        monkeypatch.setattr(sp, "GRID_CELL", 10.0)
        return calls

    def test_downloads_once_and_converts_units(self, tmp_path, monkeypatch):
        calls = self._serve(monkeypatch)
        prop, depth = sp.resolve_soil_property("ph")
        values = sp.load_soil_property(prop, depth, cache_dir=str(tmp_path))
        assert calls == ["https://files.isric.org/soilgrids/latest/data_aggregated/5000m/phh2o/"
                         "phh2o_0-5cm_mean_5000.tif"]
        assert np.nanmax(values) == pytest.approx(6.5)                              # stored 65, divided by 10
        sp.load_soil_property(prop, depth, cache_dir=str(tmp_path))
        assert len(calls) == 1

    def test_download_failure(self, tmp_path, monkeypatch):
        self._serve(monkeypatch, fail=True)
        with pytest.raises(sp.SoilPropertyError, match="offline"):
            sp.ensure_soil_property(*sp.resolve_soil_property("clay"), cache_dir=str(tmp_path))


class TestDrawing:
    def test_rgba_bands_and_clear_gaps(self):
        prop = sp.SOIL_PROPERTIES["ph"]
        rgba = sp.property_rgba(np.array([[4.0, 7.0, 9.0, np.nan]], np.float32), prop, 0.5)
        colours = [tuple(round(c * 255) for c in rgb) for rgb in sp.band_colours(prop)]
        assert tuple(rgba[0, 0, :3]) == colours[0] and tuple(rgba[0, 1, :3]) == colours[3]
        assert tuple(rgba[0, 2, :3]) == colours[5]
        assert rgba[0, 0, 3] == 128 and rgba[0, 3, 3] == 0

    def test_key(self):
        fig = Figure()
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        prop = sp.SOIL_PROPERTIES["organic-carbon"]
        key = sp.add_soil_property_legend(ax, prop, "15-30cm")
        assert key.get_title().get_text() == "Soil organic carbon, g/kg, 15–30 cm (SoilGrids 2.0)"
        assert [t.get_text() for t in key.get_texts()] == list(prop.labels)
        assert key in ax.artists
