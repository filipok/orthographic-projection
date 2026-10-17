"""Tests for malaria.py and aedes.py (no network)."""

import io
import struct
import urllib.parse
import zipfile

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import aedes as ae
import malaria as ml
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _tiff(array, order=">", tile=None):
    """An uncompressed float32 TIFF of *array* (rows, cols, samples), in one strip or in tiles."""
    rows, cols, samples = array.shape
    pieces = []
    if tile:
        for r in range(0, rows, tile):
            for c in range(0, cols, tile):
                block = np.zeros((tile, tile, samples), np.float32)
                part = array[r:r + tile, c:c + tile]
                block[:part.shape[0], :part.shape[1]] = part
                pieces.append(block.astype(order + "f4").tobytes())
    else:
        pieces.append(array.astype(order + "f4").tobytes())
    header = 8
    offsets, position = [], header
    for piece in pieces:
        offsets.append(position)
        position += len(piece)
    extra_at = position                               # out-of-line tag values follow the image data
    extra = b"".join(struct.pack(order + "I", o) for o in offsets) + struct.pack(order + "H" * samples, *[32] * samples) \
        + struct.pack(order + "H" * samples, *[3] * samples)
    offsets_at, bits_at, formats_at = extra_at, extra_at + 4 * len(offsets), extra_at + 4 * len(offsets) + 2 * samples
    entries = [(256, 4, 1, cols), (257, 4, 1, rows), (258, 3, samples, bits_at), (259, 3, 1, 1), (262, 3, 1, 1),
               (277, 3, 1, samples), (284, 3, 1, 1), (339, 3, samples, formats_at)]
    if tile:
        entries += [(322, 3, 1, tile), (323, 3, 1, tile), (324, 4, len(offsets), offsets_at)]
    else:
        entries += [(273, 4, 1, offsets[0]), (278, 4, 1, rows)]
    if len(offsets) == 1:
        entries = [(t, k, n, offsets[0] if t in (273, 324) else v) for t, k, n, v in entries]
    if samples == 1:
        entries = [(t, k, n, 32 if t == 258 else 3 if t == 339 else v) for t, k, n, v in entries]
    ifd_at = extra_at + len(extra)
    ifd = struct.pack(order + "H", len(entries))
    for tag, kind, count, value in sorted(entries):
        packed = struct.pack(order + ("HH" if kind == 3 else "I"), value, 0) if kind == 3 and count == 1 \
            else struct.pack(order + "I", value)
        ifd += struct.pack(order + "HHI", tag, kind, count) + packed
    ifd += struct.pack(order + "I", 0)
    magic = b"MM\x00*" if order == ">" else b"II*\x00"
    return magic + struct.pack(order + "I", ifd_at) + b"".join(pieces) + extra + ifd


def _rates(rows=4, cols=6):
    grid = np.full((rows, cols, 4), -9999.0, np.float32)
    grid[0, :4, 0] = [0.0005, 0.02, 0.3, 0.7]          # below the first band, then 1-5%, 20-40%, 60%+
    grid[1, 0, 0], grid[1, 0, 1] = 0.5, 1.0             # masked (sparsely populated)
    return grid


class TestMalariaReading:
    @pytest.mark.parametrize("order, tile", [(">", 16), ("<", None), (">", None)])
    def test_reads_tiles_or_strips_either_byte_order(self, order, tile):
        grid = np.arange(20 * 24 * 4, dtype=np.float32).reshape(20, 24, 4)
        np.testing.assert_array_equal(ml.read_tiff(_tiff(grid, order, tile)), grid)

    def test_not_a_tiff(self):
        with pytest.raises(ml.MalariaDataError, match="not a TIFF"):
            ml.read_tiff(b"<?xml version='1.0'?><ows:ExceptionReport/>")

    def test_bands_masks_and_blanks(self):
        grid = _rates()
        bands = ml.classify(grid[..., 0], grid[..., 1], "pf")
        assert bands[0, :4].tolist() == [255, 1, 4, 6]
        assert bands[1, 0] == 255 and bands[3, 5] == 255
        vivax_mask = np.full_like(grid[..., 1], -9999.0)
        vivax_mask[0, 1] = -1.0                          # P. vivax marks very low transmission with -1
        assert ml.classify(grid[..., 0], vivax_mask, "pv")[0, :3].tolist() == [255, 255, 6]

    @pytest.mark.parametrize("name, key", [("pf", "pf"), ("Falciparum", "pf"), ("P. vivax", "pv"), ("PV", "pv")])
    def test_parasite_names(self, name, key):
        assert ml.resolve_parasite(name) == key

    def test_bad_parasite_and_year(self):
        with pytest.raises(ValueError, match="Unknown malaria parasite"):
            ml.resolve_parasite("knowlesi")
        with pytest.raises(ValueError, match="2000-2025"):
            ml.resolve_year(1999)

    def test_request_asks_for_the_year_at_a_quarter_scale(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(ml.coverage_url("pv", 2010)).query)
        assert query["coverageId"] == ["Malaria__202608_Global_Pv_Parasite_Rate"]
        assert query["subset"] == ['time("2010-01-01T00:00:00.000Z")'] and query["SCALEFACTOR"] == ["0.25"]

    def test_downloaded_once(self, tmp_path, monkeypatch):
        urls = []

        def fake(url, timeout=600):
            urls.append(url)
            return _tiff(_rates(), ">", 16)

        monkeypatch.setattr(ml, "_get", fake)
        grid = ml.load_malaria("falciparum", 2015, str(tmp_path))
        assert (grid.parasite, grid.year, grid.bands[0, 3]) == ("pf", 2015, 6)
        ml.load_malaria("pf", 2015, str(tmp_path))
        assert len(urls) == 1

    def test_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ml, "_get", lambda url, timeout=600: b"<html>busy</html>")
        with pytest.raises(ml.MalariaDataError, match="PF map of 2025"):
            ml.load_malaria("pf", cache_dir=str(tmp_path))


class TestMalariaDrawing:
    def test_colours(self):
        rgba = ml.malaria_rgba(np.array([[0, 6, 255]], np.uint8), alpha=1.0)
        assert rgba[0, 0, 3] == 255 and rgba[0, 1, 3] == 255 and rgba[0, 2, 3] == 0

    def test_drawn_and_keyed(self):
        ax = _globe(5, 20)
        grid = ml.MalariaGrid("pf", 2025, np.zeros((870, 2160), np.uint8))
        ml.add_malaria_overlay(ax, grid, regrid_shape=100)
        assert len(ax.images) == 1
        legend = ml.add_malaria_legend(ax, "pv", 2010)
        labels = [t.get_text() for t in legend.get_texts()]
        assert labels[0] == "0.1%-0.5%" and labels[-1] == "7% and more"
        assert "2010" in legend.get_title().get_text() and "vivax" in legend.get_title().get_text()


OCCURRENCES = ("gbifID\tdecimalLatitude\tdecimalLongitude\tyear\tscientificName\n"
               "1\t22.63\t120.33\t2013\tAedes aegypti\n"
               "2\t-23.5\t-46.6\tNA\tAedes aegypti\n"
               "3\t\t10\t2000\tAedes aegypti\n"
               "4\t95\t10\t2000\tAedes aegypti\n")


def _archive(text=OCCURRENCES):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rescued-occurrence.txt", text)
        zf.writestr("meta.xml", "<archive/>")
    return buf.getvalue()


class TestAedes:
    @pytest.mark.parametrize("name, species", [
        ("both", ("aegypti", "albopictus")), ("Aedes albopictus", ("albopictus",)), ("tiger", ("albopictus",)),
        ("aegypti", ("aegypti",)),
    ])
    def test_species_names(self, name, species):
        assert ae.resolve_aedes(name) == species

    def test_unknown_species(self):
        with pytest.raises(ValueError, match="Unknown Aedes"):
            ae.resolve_aedes("vexans")

    def test_records_with_coordinates_kept(self):
        records = ae.read_occurrences(OCCURRENCES)
        assert records["lon"].tolist() == [120.33, -46.6] and records["lat"].tolist() == [22.63, -23.5]
        assert records["year"][0] == 2013 and np.isnan(records["year"][1])

    def test_downloaded_once(self, tmp_path, monkeypatch):
        urls = []

        def fake(url, timeout=120):
            urls.append(url)
            return _archive()

        monkeypatch.setattr(ae, "_get", fake)
        found = ae.load_aedes("albopictus", str(tmp_path))
        assert found.species == "albopictus" and len(found.lon) == 2
        assert urls == ["https://orphans.gbif.org/GB/33614778-513a-4ec0-814d-125021cca5fe.zip"]
        ae.load_aedes("albopictus", str(tmp_path))
        assert len(urls) == 1

    def test_empty_or_broken_archive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ae, "_get", lambda url, timeout=120: _archive("gbifID\tdecimalLatitude\n"))
        with pytest.raises(ae.AedesDataError, match="no records"):
            ae.load_aedes("aegypti", str(tmp_path))
        monkeypatch.setattr(ae, "_get", lambda url, timeout=120: b"not a zip")
        with pytest.raises(ae.AedesDataError, match="aegypti"):
            ae.load_aedes("aegypti", str(tmp_path))

    def test_drawn_and_keyed(self):
        ax = _globe(20, 120)
        records = [ae.AedesRecords("aegypti", np.array([120.0, -60.0]), np.array([22.0, 0.0]), np.array([1990.0, 2014.0])),
                   ae.AedesRecords("albopictus", np.array([121.0]), np.array([23.0]), np.array([np.nan]))]
        ae.draw_aedes(ax, records)
        assert len(ax.collections) == 2
        assert np.asarray(ax.collections[1].get_offsets()).shape[0] == 1          # the point beyond the horizon is left out
        legend = ae.add_aedes_legend(ax, records)
        labels = [t.get_text() for t in legend.get_texts()]
        assert labels[0].startswith("Aedes aegypti") and labels[0].endswith("2 records")
        assert "1990-2014" in legend.get_title().get_text()
