"""Tests for trewartha.py — class rules, the COG reader, land mask and caching (no network)."""

import io
import os
import struct

import cartopy.crs as ccrs
import matplotlib
import numpy as np
import pytest
import shapefile
from PIL import Image

import trewartha

matplotlib.use("Agg")


# ===================================================================
# Class names
# ===================================================================


class TestClassNames:
    @pytest.mark.parametrize("specs, symbols", [
        (["Do"], ["Do"]),
        (["do"], ["Do"]),
        (["C"], ["Cs", "Cw", "Cf"]),
        (["B"], ["BW", "BS"]),
        (["F", "Ar"], ["Ar", "Ft", "Fi"]),
    ])
    def test_resolve(self, specs, symbols):
        codes = trewartha.resolve_trewartha_classes(specs)
        assert [trewartha.TREWARTHA_CLASSES[c][0] for c in codes] == symbols

    @pytest.mark.parametrize("spec", ["Xx", "", "Cfb"])
    def test_unknown(self, spec):
        with pytest.raises(ValueError, match="Unknown Trewartha class"):
            trewartha.resolve_trewartha_classes([spec])


# ===================================================================
# The rules
# ===================================================================

NH_WINTER = [m in (10, 11, 12, 1, 2, 3) for m in range(1, 13)]

# Monthly mean temperature (°C) and precipitation (mm), January first, all northern hemisphere
CLIMATES = {
    "Ar": ([27] * 12, [250] * 12),
    "Aw": ([25, 26, 28, 30, 31, 30, 28, 28, 28, 28, 27, 25], [5, 5, 5, 20, 100, 300, 600, 500, 300, 80, 10, 5]),
    "As": ([26, 27, 28, 28, 27, 26, 25, 25, 26, 26, 26, 26], [300, 250, 200, 150, 40, 20, 10, 10, 40, 250, 300, 300]),
    "BW": ([14, 16, 20, 25, 29, 32, 33, 33, 30, 26, 20, 15], [5, 4, 3, 1, 0, 0, 0, 0, 0, 1, 3, 5]),
    "BS": ([0, 2, 6, 10, 15, 20, 23, 22, 17, 11, 4, 0], [10, 12, 30, 45, 60, 45, 50, 40, 25, 20, 15, 10]),
    "Cs": ([8, 9, 11, 14, 18, 22, 25, 25, 22, 17, 12, 9], [80, 70, 60, 50, 30, 15, 10, 15, 50, 90, 100, 90]),
    "Cw": ([10, 12, 16, 20, 24, 26, 26, 25, 23, 19, 14, 10], [5, 5, 10, 25, 70, 180, 220, 200, 100, 30, 8, 4]),
    "Cf": ([4, 6, 10, 15, 20, 24, 27, 27, 23, 17, 11, 6], [90, 90, 100, 110, 110, 150, 140, 130, 100, 80, 80, 90]),
    "Do": ([5, 5, 7, 9, 12, 15, 17, 17, 14, 11, 7, 5], [55, 40, 40, 45, 50, 45, 45, 50, 50, 70, 60, 55]),
    "Dc": ([-10, -8, -2, 6, 13, 17, 19, 17, 11, 5, -2, -7], [50, 40, 35, 35, 50, 80, 85, 80, 65, 60, 55, 50]),
    "Eo": ([-3, -3, -1, 3, 7, 11, 13, 12, 9, 5, 1, -2], [80, 60, 60, 50, 50, 60, 70, 80, 90, 100, 90, 80]),
    "Ec": ([-25, -22, -14, -3, 7, 14, 16, 12, 5, -4, -16, -23], [15, 12, 12, 15, 25, 45, 60, 55, 35, 25, 20, 15]),
    "Ft": ([-28, -28, -26, -18, -7, 1, 4, 3, -1, -9, -19, -25], [5, 5, 5, 5, 5, 10, 20, 25, 15, 15, 7, 5]),
    "Fi": ([-30, -32, -30, -25, -15, -8, -3, -6, -12, -20, -26, -29], [10, 10, 10, 10, 10, 10, 15, 20, 20, 15, 10, 10]),
}


def _classify(climates, winter=NH_WINTER):
    names = list(climates)
    acc = trewartha.TrewarthaAccumulator((1, len(names)))
    for month in range(12):
        temp = np.array([[climates[n][0][month] for n in names]], dtype=np.float32)
        precip = np.array([[climates[n][1][month] for n in names]], dtype=np.float32)
        acc.add_month(temp, precip, np.full((1, len(names)), winter[month]))
    return dict(zip(names, (trewartha.TREWARTHA_CLASSES[int(c)][0] for c in acc.classify()[0])))


class TestRules:
    def test_every_class(self):
        assert _classify(CLIMATES) == {name: name for name in CLIMATES}

    def test_southern_hemisphere_seasons_flip(self):
        # Shift Cs by six months: the dry season then falls in the southern summer
        temps, precips = CLIMATES["Cs"]
        shifted = {"Cs": (temps[6:] + temps[:6], precips[6:] + precips[:6])}
        assert _classify(shifted, winter=[not w for w in NH_WINTER]) == {"Cs": "Cs"}

    def test_dry_test_comes_first(self):
        # Hot all year (A by temperature) but almost rainless: desert, not tropical
        assert _classify({"x": ([28] * 12, [2] * 12)}) == {"x": "BW"}

    def test_cold_places_are_never_dry(self):
        # Patton's threshold is negative when it is cold enough, so ice caps stay F
        assert _classify({"x": ([-30] * 12, [1] * 12)}) == {"x": "Fi"}

    def test_needs_twelve_months(self):
        acc = trewartha.TrewarthaAccumulator((1, 1))
        with pytest.raises(ValueError, match="12 months"):
            acc.classify()

    def test_winter_months_by_hemisphere(self):
        winter = trewartha.winter_months(np.array([45.0, -30.0]))
        assert list(winter[1]) == [True, False] and list(winter[7]) == [False, True]


# ===================================================================
# Reading a reduced level of a Cloud-Optimised GeoTIFF
# ===================================================================


def _lzw_tile(array):
    """LZW-compress *array* the way a TIFF tile is stored (no predictor)."""
    buf = io.BytesIO()
    Image.fromarray(array.astype(np.uint16)).save(buf, "TIFF", compression="tiff_lzw")
    tiff = buf.getvalue()
    ifd = trewartha._read_ifds(tiff)[0]
    return tiff[ifd[273][0]: ifd[273][0] + ifd[279][0]]       # its single strip


def _cog(levels, west=-180.0, north=90.0, cell=1.0, tile=16):
    """A little-endian tiled TIFF with one directory per level, like a COG."""
    tiles = []
    for array in levels:
        rows, cols = array.shape
        pieces = [_lzw_tile(array[r:r + tile, c:c + tile])
                  for r in range(0, rows, tile) for c in range(0, cols, tile)]
        tiles.append(pieces)
    head = bytearray(b"II*\x00\x00\x00\x00\x00")
    data = bytearray()
    ifd_blobs = []
    for level, (array, pieces) in enumerate(zip(levels, tiles)):
        entries = [(256, 4, [array.shape[1]]), (257, 4, [array.shape[0]]), (258, 3, [16]), (259, 3, [5]),
                   (277, 3, [1]), (317, 3, [1]), (322, 4, [tile]), (323, 4, [tile]),
                   (324, 4, None), (325, 4, [len(p) for p in pieces]), (339, 3, [1])]
        if level == 0:
            entries += [(33550, 12, [cell, cell, 0.0]), (33922, 12, [0, 0, 0, west, north, 0])]
        ifd_blobs.append((entries, pieces))
    # Lay out: header, directories with their arrays, then tile data
    layout = []
    offset = 8
    for entries, pieces in ifd_blobs:
        size = 2 + 12 * len(entries) + 4
        extra = sum(len(v) * {3: 2, 4: 4, 12: 8}[t] for _, t, v in entries if v is not None and
                    len(v) * {3: 2, 4: 4, 12: 8}[t] > 4) + 4 * len(pieces)
        layout.append(offset)
        offset += size + extra
    data_start = offset
    tile_offsets, cursor = [], data_start
    for _, pieces in ifd_blobs:
        tile_offsets.append([cursor + sum(len(p) for p in pieces[:i]) for i in range(len(pieces))])
        cursor += sum(len(p) for p in pieces)
    for i, ((entries, pieces), start) in enumerate(zip(ifd_blobs, layout)):
        entries = [(tag, typ, tile_offsets[i] if value is None else value) for tag, typ, value in entries]
        arrays_at = start + 2 + 12 * len(entries) + 4
        body, arrays = bytearray(struct.pack("<H", len(entries))), bytearray()
        for tag, typ, values in entries:
            fmt, size = {3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}[typ]
            raw = struct.pack("<" + fmt * len(values), *values)
            if len(raw) <= 4:
                body += struct.pack("<HHI", tag, typ, len(values)) + raw.ljust(4, b"\x00")
            else:
                body += struct.pack("<HHII", tag, typ, len(values), arrays_at + len(arrays))
                arrays += raw
        next_ifd = layout[i + 1] if i + 1 < len(layout) else 0
        body += struct.pack("<I", next_ifd) + arrays
        head += body.ljust(layout[i + 1] - start if i + 1 < len(layout) else data_start - start, b"\x00")
    struct.pack_into("<I", head, 4, layout[0])
    for pieces in tiles:
        for p in pieces:
            data += p
    return bytes(head + data)


class TestCogReader:
    def _serve(self, monkeypatch, blob):
        def fetch(url, start, end, timeout=120):
            return blob[start:end + 1]
        monkeypatch.setattr(trewartha, "_fetch_range", fetch)

    def test_reads_full_and_reduced_levels_with_their_geography(self, monkeypatch):
        full = np.arange(32 * 64, dtype=np.uint16).reshape(32, 64)
        half = (full[::2, ::2] // 2).astype(np.uint16)
        self._serve(monkeypatch, _cog([full, half], west=-180.0, north=84.0, cell=0.5))
        grid, geo = trewartha.read_cog_level("https://example.test/x.tif", 0)
        np.testing.assert_array_equal(grid, full)
        assert geo == (-180.0, 84.0, 0.5)
        grid, geo = trewartha.read_cog_level("https://example.test/x.tif", 1)
        np.testing.assert_array_equal(grid, half)
        assert geo == (-180.0, 84.0, 1.0)            # cells twice as large

    def test_missing_level(self, monkeypatch):
        self._serve(monkeypatch, _cog([np.zeros((16, 16), np.uint16)]))
        with pytest.raises(trewartha.TrewarthaDataError, match="no reduced level 3"):
            trewartha.read_cog_level("https://example.test/x.tif", 3)

    def test_not_a_tiff(self, monkeypatch):
        self._serve(monkeypatch, b"<html>no</html>" * 10)
        with pytest.raises(trewartha.TrewarthaDataError, match="not a little-endian TIFF"):
            trewartha.read_cog_level("https://example.test/x.tif", 0)


# ===================================================================
# Land mask, caching and drawing
# ===================================================================


def _natural_earth(tmp_path):
    base = str(tmp_path / "ne_50m_land")
    with shapefile.Writer(base + ".shp", shapeType=shapefile.POLYGON) as w:
        w.field("FID", "N")
        w.poly([[(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]])   # a 10° square, clockwise
        w.record(0)
    return lambda resolution, category, name: base + ".shp"


class TestLandMask:
    def test_cells_whose_centres_are_on_land(self, tmp_path):
        mask = trewartha.land_mask((4, 4), west=-10, north=20, cell=5, natural_earth=_natural_earth(tmp_path))
        # cell centres at lon -7.5, -2.5, 2.5, 7.5 and lat 17.5, 12.5, 7.5, 2.5
        expected = np.zeros((4, 4), bool)
        expected[2:, 2:] = True
        np.testing.assert_array_equal(mask, expected)


class TestEnsureData:
    def _mock_sources(self, monkeypatch, reads, geo, heights):
        def read_month(var, month):
            reads.append((var, month))
            temp_k = 273.15 + 5                             # 5 °C all year: tundra (Ft)
            grid = np.full((2, 4), round(temp_k * 10) if var == "tas" else 2500, np.uint16)
            return var, month, grid, geo

        monkeypatch.setattr(trewartha, "_read_month", read_month)
        monkeypatch.setattr(trewartha, "land_mask",
                            lambda shape, *a, **k: np.array([[True, False, True, False]] * 2))
        monkeypatch.setattr(trewartha, "elevation_grid", heights)

    def test_computes_once_then_uses_the_cache(self, tmp_path, monkeypatch):
        reads = []
        geo = (-180.0, 90.0, 90.0)                          # a 2 x 4 grid
        # 3,000 m: lowered to 1,500 m it would be 9.75 °C warmer, subtropical, so H
        heights = lambda shape, *a, **k: np.array([[3000.0, 0, 1000, 0]] * 2, np.float32)
        self._mock_sources(monkeypatch, reads, geo, heights)
        path = trewartha.ensure_trewartha_data(cache_dir=str(tmp_path))
        assert len(reads) == 24
        codes, loaded_geo = trewartha.load_trewartha(cache_dir=str(tmp_path))
        assert loaded_geo == geo
        np.testing.assert_array_equal(codes, [[15, 0, 13, 0]] * 2)  # H, ocean, Ft (only 1,000 m), ocean
        trewartha.ensure_trewartha_data(cache_dir=str(tmp_path))
        assert len(reads) == 24                                       # served from the cache
        assert os.path.basename(path).startswith("trewartha_chelsa21_1981-2010_level4")

    def test_missing_heights_are_a_data_error(self, tmp_path, monkeypatch):
        def heights(*a, **k):
            raise trewartha.ElevationDataError("offline")
        self._mock_sources(monkeypatch, [], (-180.0, 90.0, 90.0), heights)
        with pytest.raises(trewartha.TrewarthaDataError, match="offline"):
            trewartha.ensure_trewartha_data(cache_dir=str(tmp_path))
        assert not os.listdir(tmp_path)


class TestHighlands:
    def test_offset_counts_only_the_height_above_1500_m(self):
        offset = trewartha.highland_offset(np.array([np.nan, 0, 1500, 2500], np.float32))
        np.testing.assert_allclose(offset, [0, 0, 0, 6.5])

    def test_accumulator_offset_warms_every_month(self):
        temps, precips = CLIMATES["Do"]
        acc = trewartha.TrewarthaAccumulator((1, 1), offset=np.full((1, 1), 10, np.float32))
        for month in range(12):
            acc.add_month(np.full((1, 1), temps[month], np.float32),
                          np.full((1, 1), precips[month], np.float32), np.full((1, 1), NH_WINTER[month]))
        assert trewartha.TREWARTHA_CLASSES[int(acc.classify()[0, 0])][0] == "Cf"

    def test_only_high_ground_whose_group_changes(self):
        c = trewartha._CODES
        codes = np.array([c["Ft"], c["Dc"], c["Dc"], c["BS"], 0], np.uint8)
        lowered = np.array([c["Cf"], c["Do"], c["Cf"], c["BS"], 0], np.uint8)
        height = np.array([4000, 3000, 1400, 3000, 3000], np.float32)
        # a new group, the same group (D), too low, the same class, ocean
        np.testing.assert_array_equal(trewartha.highlands(codes, lowered, height),
                                      [True, False, False, False, False])

    def test_h_is_a_class_name(self):
        assert trewartha.resolve_trewartha_classes(["h"]) == (15,)


class TestDrawing:
    def test_rgba_clears_ocean_and_unselected_classes(self):
        codes = np.array([[0, 1, 8]], np.uint8)
        rgba = trewartha.trewartha_rgba(codes, 0.5, classes=(8,))
        assert rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 0
        assert tuple(rgba[0, 2]) == (*trewartha.TREWARTHA_CLASSES[8][2], 128)

    @pytest.mark.parametrize("classes, first_label", [(None, "Ar"), ((6, 7, 8), "Cs: Subtropical, dry summer")])
    def test_legend(self, classes, first_label):
        from matplotlib.figure import Figure

        fig = Figure()
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        trewartha.add_trewartha_legend(ax, classes=classes)
        legend = ax.get_legend()
        assert legend is not None
        assert legend.get_title().get_text() == "Trewartha Climate Classification"
        labels = [t.get_text() for t in legend.get_texts()]
        assert labels[0] == first_label
        assert len(labels) == (len(classes) if classes else 15)
