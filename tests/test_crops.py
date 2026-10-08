"""Tests for crops.py — crop names, remote zip extraction, shares and drawing (no network)."""

import os
import zipfile

import cartopy.crs as ccrs
import h5py
import matplotlib
import matplotlib.colors as mcolors
import numpy as np
import pytest

import crops

matplotlib.use("Agg")


def _write_crop_nc(path, croparea, lat):
    """Write a CROPGRIDS-like NetCDF4 file with *croparea* (ha) on *lat* rows."""
    with h5py.File(path, "w") as f:
        f["croparea"] = np.asarray(croparea, dtype=np.float32)
        f["lat"] = np.asarray(lat, dtype=np.float32)
        f["lon"] = np.linspace(-179.975, 179.975, np.asarray(croparea).shape[1], dtype=np.float32)


def _cell_ha(lat, step=0.05):
    return (np.radians(step) * crops._EARTH_RADIUS_KM) ** 2 * np.cos(np.radians(lat)) * 100


# ===================================================================
# Names and colours
# ===================================================================


class TestCropSpecs:
    def test_all_173_crops_are_known(self):
        assert len(crops.CROP_NAMES) == 173 == len(set(crops.CROP_NAMES))
        assert {"wheat", "rice", "maize", "coffee", "sugarcane"} <= set(crops.CROP_NAMES)

    @pytest.mark.parametrize("spec, expected", [
        ("wheat", ("wheat", None)),
        (" Wheat ", ("wheat", None)),
        ("rice:#123456", ("rice", "#123456")),
        ("maize:red", ("maize", "red")),
    ])
    def test_parse(self, spec, expected):
        assert crops.parse_crop_spec(spec) == expected

    def test_unknown_crop_suggests_close_names(self):
        with pytest.raises(ValueError, match="Did you mean wheat"):
            crops.parse_crop_spec("whaet")

    def test_bad_colour(self):
        with pytest.raises(ValueError, match="Invalid colour"):
            crops.parse_crop_spec("wheat:notacolour")

    def test_resolve_fills_default_and_spare_colours(self):
        resolved = crops.resolve_crops(["wheat", "rice:#000000", "yam", "taro"])
        assert resolved[0] == ("wheat", crops.CROP_COLORS["wheat"])
        assert resolved[1] == ("rice", "#000000")
        colours = [c for _, c in resolved]
        assert len(set(colours)) == 4            # crops without a preset get distinct colours

    def test_duplicate_crop_rejected(self):
        with pytest.raises(ValueError, match="twice"):
            crops.resolve_crops(["wheat", "wheat:#fff000"])

    def test_label(self):
        assert crops.crop_label("sugarcane") == "Sugarcane"


# ===================================================================
# Fetching a member of the remote zip
# ===================================================================


class TestEnsureCropFile:
    def _serve_zip(self, monkeypatch, tmp_path, members, status=206):
        """Serve a local zip in place of the figshare archive, through byte ranges."""
        zip_path = tmp_path / "remote.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        blob = zip_path.read_bytes()
        calls = []

        def fetch_range(url, start, end, timeout=120):
            calls.append((start, end))
            if status != 206:
                raise crops.CropDataError(f"{url} ignored the range request (HTTP {status})")
            return blob[start:end + 1]

        monkeypatch.setattr(crops, "_remote_size", lambda url, timeout=60: len(blob))
        monkeypatch.setattr(crops, "_fetch_range", fetch_range)
        return calls

    def _nc_bytes(self, tmp_path):
        path = tmp_path / "src.nc"
        _write_crop_nc(path, [[1.0, 2.0]], [0.025])
        return path.read_bytes()

    def test_extracts_only_the_requested_crop(self, tmp_path, monkeypatch):
        member = crops._MEMBER.format(v=crops.CROPGRIDS_VERSION, name="wheat")
        calls = self._serve_zip(monkeypatch, tmp_path, {
            member: self._nc_bytes(tmp_path),
            member.replace("wheat", "rice"): os.urandom(200_000),
        })
        cache = tmp_path / "cache"
        path = crops.ensure_crop_file("wheat", cache_dir=str(cache))
        assert path == str(cache / "CROPGRIDSv1.08_wheat.nc")
        assert crops._is_hdf5(path)
        assert calls                                   # read through range requests
        assert not any(f.endswith(".part") for f in os.listdir(cache))

    def test_cached_file_is_used_without_network(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        _write_crop_nc(cache / "CROPGRIDSv1.08_rice.nc", [[1.0]], [0.025])
        monkeypatch.setattr(crops, "_remote_size", lambda *a, **k: pytest.fail("network used"))
        assert crops.ensure_crop_file("rice", cache_dir=str(cache)).endswith("CROPGRIDSv1.08_rice.nc")

    def test_server_ignoring_ranges_is_an_error(self, tmp_path, monkeypatch):
        member = crops._MEMBER.format(v=crops.CROPGRIDS_VERSION, name="wheat")
        self._serve_zip(monkeypatch, tmp_path, {member: b"x"}, status=200)
        with pytest.raises(crops.CropDataError, match="ignored the range request"):
            crops.ensure_crop_file("wheat", cache_dir=str(tmp_path / "cache"))

    def test_missing_member(self, tmp_path, monkeypatch):
        self._serve_zip(monkeypatch, tmp_path, {"other.txt": b"x"})
        with pytest.raises(crops.CropDataError, match="missing from the CROPGRIDS archive"):
            crops.ensure_crop_file("wheat", cache_dir=str(tmp_path / "cache"))

    def test_member_that_is_not_netcdf4(self, tmp_path, monkeypatch):
        member = crops._MEMBER.format(v=crops.CROPGRIDS_VERSION, name="wheat")
        self._serve_zip(monkeypatch, tmp_path, {member: b"not hdf5 at all"})
        cache = tmp_path / "cache"
        with pytest.raises(crops.CropDataError, match="not NetCDF4"):
            crops.ensure_crop_file("wheat", cache_dir=str(cache))
        assert not os.listdir(cache)

    def test_unknown_crop(self, tmp_path):
        with pytest.raises(ValueError):
            crops.ensure_crop_file("unobtainium", cache_dir=str(tmp_path))


# ===================================================================
# Shares and the overlay image
# ===================================================================


class TestShares:
    def test_share_of_cell_area_with_ocean_and_flipped_rows(self, tmp_path):
        lat = [0.075, 0.025]                        # stored north to south, 0.05° apart
        cell = _cell_ha(np.array(lat))
        area = [[-1.0, 0.25 * cell[0]], [0.0, 2 * cell[1]]]
        path = tmp_path / "c.nc"
        _write_crop_nc(path, area, lat)
        share = crops.read_crop_share(str(path))
        # rows come back south to north; ocean (-1) is 0; shares are capped at 1
        np.testing.assert_allclose(share, [[0.0, 1.0], [0.0, 0.25]], atol=1e-4)

    def test_alpha_ramp(self):
        shares = np.array([0.0, 0.004, crops.MIN_SHARE, 0.1, crops.SATURATION, 1.0])
        alpha = crops.share_to_alpha(shares)
        assert alpha[0] == alpha[1] == 0                     # clear below MIN_SHARE
        assert crops.ALPHA_RANGE[0] <= alpha[2] < alpha[3] < alpha[4]
        assert alpha[4] == alpha[5] == pytest.approx(crops.ALPHA_RANGE[1])

    def test_each_cell_shows_its_largest_crop(self):
        wheat = np.array([[0.3, 0.0, 0.1]], dtype=np.float32)
        rice = np.array([[0.1, 0.0, 0.5]], dtype=np.float32)
        layer = crops.build_crop_layer([("wheat", "#ff0000"), ("rice", "#0000ff")], [wheat, rice])
        rgba = layer.rgba
        assert tuple(rgba[0, 0, :3]) == (255, 0, 0)        # wheat wins
        assert tuple(rgba[0, 2, :3]) == (0, 0, 255)        # rice wins
        assert rgba[0, 1, 3] == 0                          # neither: clear
        assert rgba[0, 2, 3] > rgba[0, 0, 3]               # deeper where the share is larger

    def test_layer_needs_one_grid_per_crop(self):
        with pytest.raises(ValueError):
            crops.build_crop_layer([("wheat", "red")], [])


class TestDrawing:
    def _layer(self, names=("wheat",)):
        shares = [np.full((4, 8), 0.3, dtype=np.float32) for _ in names]
        return crops.build_crop_layer(crops.resolve_crops(list(names)), shares)

    def test_draws_one_image_between_climate_and_ice(self):
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            crops.draw_crops(ax, self._layer(), regrid_shape=50)
            (image,) = ax.get_images()
            assert 5 < image.get_zorder() < 6
        finally:
            plt.close(fig)

    @pytest.mark.parametrize("names, title_start", [
        (("wheat",), "Share of land under the crop"),
        (("wheat", "rice"), "Crop with the largest share of land"),
    ])
    def test_key(self, names, title_start):
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            legend = crops.add_crop_legend(ax, self._layer(names))
            assert legend in ax.artists and not legend.get_clip_on()
            assert legend.get_title().get_text().startswith(title_start)
            assert [t.get_text() for t in legend.get_texts()] == [n.capitalize() for n in names]
            colour = crops.CROP_COLORS[names[0]]
            assert mcolors.same_color(legend.legend_handles[0].get_facecolor(), colour)
        finally:
            plt.close(fig)
