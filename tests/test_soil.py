"""Tests for soil.py — group names, the SoilGrids reader, the majority vote and caching (no network)."""

import io
import os
import urllib.error

import numpy as np
import pytest
from matplotlib.figure import Figure
from PIL import Image

import cartopy.crs as ccrs

import soil


def _tiff(grid):
    """*grid* as a deflate-compressed, strip-organised 8-bit TIFF, like the SoilGrids files."""
    buf = io.BytesIO()
    Image.fromarray(np.asarray(grid, np.uint8)).save(buf, "TIFF", compression="tiff_adobe_deflate")
    return buf.getvalue()


class TestGroupNames:
    @pytest.mark.parametrize("specs, names", [
        (["Chernozems"], ["Chernozems"]),
        (["chernozem"], ["Chernozems"]),
        (["CH"], ["Chernozems"]),
        (["pz", "Ferral"], ["Ferralsols", "Podzols"]),
        (["pl"], ["Planosols"]),                     # the code wins over a two-letter start
    ])
    def test_resolve(self, specs, names):
        values = soil.resolve_soil_classes(specs)
        assert [soil.SOIL_GROUPS[v][1] for v in values] == names

    @pytest.mark.parametrize("spec, problem", [("Xx", "Unknown"), ("", "Unknown"), ("solon", "Ambiguous")])
    def test_bad_names(self, spec, problem):
        with pytest.raises(ValueError, match=f"{problem} soil group"):
            soil.resolve_soil_classes([spec])

    def test_codes_and_names_are_unique(self):
        codes = [code for code, _, _, _ in soil.SOIL_GROUPS.values()]
        names = [name for _, name, _, _ in soil.SOIL_GROUPS.values()]
        assert len(set(codes)) == len(set(names)) == 30


VRT = b"""<VRTDataset rasterXSize="128" rasterYSize="64">
  <GeoTransform> -1.8000000000000000e+02,  2.0e+00,  0.0e+00,  8.4e+01,  0.0e+00, -2.0e+00</GeoTransform>
  <VRTRasterBand dataType="Byte" band="1">
    <ComplexSource>
      <SourceFilename relativeToVRT="1">MostProbable/1.tif</SourceFilename>
      <DstRect xOff="0" yOff="0" xSize="64" ySize="64" />
    </ComplexSource>
    <ComplexSource>
      <SourceFilename relativeToVRT="1">MostProbable/2.tif</SourceFilename>
      <DstRect xOff="64" yOff="0" xSize="64" ySize="64" />
    </ComplexSource>
  </VRTRasterBand>
</VRTDataset>"""


class TestReading:
    def test_parse_vrt(self):
        geo, sources = soil.parse_vrt(VRT)
        assert geo == (-180.0, 84.0, 2.0)
        assert sources == [("MostProbable/1.tif", 0, 0, 64, 64), ("MostProbable/2.tif", 64, 0, 64, 64)]

    def test_decode_strips(self):
        grid = np.arange(64 * 40, dtype=np.uint16).reshape(40, 64) % 31
        np.testing.assert_array_equal(soil.decode_strips(_tiff(grid)), grid)


class TestMajority:
    def test_commonest_group_per_block(self):
        grid = np.array([
            [7, 7, 18, 6],
            [7, 6, 18, 18],
            [255, 255, 255, 3],
            [255, 4, 255, 255],
        ], np.uint8)
        # Bottom left has one soil cell in four, under the third needed
        np.testing.assert_array_equal(soil.majority(grid, factor=2), [[7, 18], [255, 255]])

    def test_too_little_soil_is_no_data(self):
        grid = np.full((3, 3), 255, np.uint8)
        grid[0, 0] = 10                                   # 1 of 9 cells
        assert soil.majority(grid, factor=3)[0, 0] == soil.NODATA
        grid[0, :3] = 10                                  # 3 of 9 cells: a third
        assert soil.majority(grid, factor=3)[0, 0] == 10


class TestEnsureData:
    def _serve(self, monkeypatch, fail=False):
        files = {
            "MostProbable.vrt": VRT,
            "MostProbable/1.tif": _tiff(np.full((64, 64), 7)),                   # Chernozems
            "MostProbable/2.tif": _tiff(np.where(np.arange(64)[:, None] < 32, 10, 255) * np.ones((1, 64))),
        }
        fetched = []

        def get(url, timeout=120):
            name = url.removeprefix(soil.SOILGRIDS_URL)
            fetched.append(name)
            if fail and name.endswith("2.tif"):
                raise urllib.error.URLError("offline")
            return files[name]

        monkeypatch.setattr(soil, "_get", get)
        return fetched

    def test_builds_once_then_uses_the_cache(self, tmp_path, monkeypatch):
        fetched = self._serve(monkeypatch)
        groups, geo = soil.load_soil(cache_dir=str(tmp_path))
        assert geo == (-180.0, 84.0, 2.0 * soil.FACTOR)
        # Each 64 px file becomes 2 x 2 cells: the left one all Chernozems, the right
        # one Ferralsols in its top half and no data below
        np.testing.assert_array_equal(groups, [[7, 7, 10, 10], [7, 7, 255, 255]])
        assert sorted(fetched) == ["MostProbable.vrt", "MostProbable/1.tif", "MostProbable/2.tif"]
        assert not (tmp_path / "tiles_x32").exists()      # pieces removed once the grid is saved
        soil.load_soil(cache_dir=str(tmp_path))
        assert len(fetched) == 3

    def test_a_failed_download_resumes(self, tmp_path, monkeypatch):
        self._serve(monkeypatch, fail=True)
        with pytest.raises(soil.SoilDataError, match="offline"):
            soil.ensure_soil_data(cache_dir=str(tmp_path))
        assert os.listdir(tmp_path / "tiles_x32") == ["1.npy"]  # the file that arrived is kept
        fetched = self._serve(monkeypatch)
        soil.ensure_soil_data(cache_dir=str(tmp_path))
        assert "MostProbable/1.tif" not in fetched


class TestDrawing:
    def test_rgba_clears_no_data_and_unselected_groups(self):
        groups = np.array([[255, 7, 10]], np.uint8)
        rgba = soil.soil_rgba(groups, 0.5, classes=(10,))
        assert rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 0
        assert tuple(rgba[0, 2]) == (*soil.SOIL_GROUPS[10][3], 128)

    @pytest.mark.parametrize("classes, first_label", [(None, "Acrisols"), ((7,), "Chernozems: black earths, deep humus")])
    def test_key(self, classes, first_label):
        fig = Figure()
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        key = soil.add_soil_legend(ax, classes=classes)
        labels = [t.get_text() for t in key.get_texts()]
        assert labels[0] == first_label and len(labels) == (len(classes) if classes else 30)
        assert key in ax.artists
