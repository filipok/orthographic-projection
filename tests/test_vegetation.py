"""Tests for vegetation.py — names, NDVI months, the GIBS tile source, colouring and keys (no network)."""

import datetime
import io
import urllib.error

import numpy as np
import pytest
from matplotlib.figure import Figure
from PIL import Image

import cartopy.crs as ccrs

import vegetation as veg

TODAY = datetime.date(2026, 10, 10)          # NDVI published up to August 2026


class TestLandCoverNames:
    @pytest.mark.parametrize("specs, codes", [
        (["forest"], (1, 2, 3, 4, 5)),
        (["Forests"], (1, 2, 3, 4, 5)),
        (["cropland", "urban"], (12, 13, 14)),
        (["evergreen"], (1, 2)),
        (["Barren"], (16,)),
    ])
    def test_resolve(self, specs, codes):
        assert veg.resolve_land_cover_classes(specs) == codes

    @pytest.mark.parametrize("spec", ["jungle", "ev", ""])
    def test_unknown(self, spec):
        with pytest.raises(ValueError, match="Unknown land cover class"):
            veg.resolve_land_cover_classes([spec])


class TestNdviMonths:
    def test_latest_month_allows_for_the_publishing_lag(self):
        assert veg.latest_ndvi_month(TODAY) == (2026, 8)
        assert veg.latest_ndvi_month(datetime.date(2026, 1, 5)) == (2025, 11)

    @pytest.mark.parametrize("spec, expected", [
        ("2026-07", (2026, 7)),
        ("2001-1", (2001, 1)),
        ("july", (2026, 7)),
        ("Jan", (2026, 1)),
        ("8", (2026, 8)),
        ("september", (2025, 9)),          # September 2026 is not out yet
        ("December", (2025, 12)),
    ])
    def test_resolve(self, spec, expected):
        assert veg.resolve_ndvi_month(spec, TODAY) == expected

    @pytest.mark.parametrize("spec, match", [
        ("summer", "Unknown NDVI month"), ("ju", "Unknown NDVI month"), ("13", "Unknown NDVI month"),
        ("2026-13", "Unknown NDVI month"), ("2000-01", "NDVI runs from"), ("2026-09", "NDVI runs from"),
    ])
    def test_bad(self, spec, match):
        with pytest.raises(ValueError, match=match):
            veg.resolve_ndvi_month(spec, TODAY)


def _png(mode):
    buf = io.BytesIO()
    if mode == "P":
        img = Image.new("P", (256, 256), 1)
        img.putpalette([0, 0, 0, *veg.LAND_COVER_CLASSES[12][1]] + [0] * 762)
    else:
        img = Image.new("RGBA", (256, 256), (90, 90, 90, 255))   # a true-colour stand-in
    img.save(buf, "PNG")
    return buf.getvalue()


class TestGibsTiles:
    def _source(self, tmp_path, monkeypatch, answers):
        source = veg.land_cover_tiles(2024, cache_dir=str(tmp_path))
        calls = []

        def download(tile):
            calls.append(tile)
            answer = answers[min(len(calls), len(answers)) - 1]
            if isinstance(answer, int):
                raise urllib.error.HTTPError("u", answer, "error", {}, None)  # type: ignore[arg-type]
            return answer

        monkeypatch.setattr(source, "_download", download)
        return source, calls

    def test_url(self):
        source = veg.ndvi_tiles(2026, 7)
        assert source._image_url((3, 2, 5)) == (
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_L3_NDVI_Monthly/default/"
            "2026-07-01/GoogleMapsCompatible_Level7/5/2/3.png")

    def test_palette_tile_is_cached(self, tmp_path, monkeypatch):
        source, calls = self._source(tmp_path, monkeypatch, [_png("P")])
        for _ in range(2):
            img, extent, origin = source.get_image((1, 2, 5))
            assert tuple(img[0, 0]) == (*veg.LAND_COVER_CLASSES[12][1], 255) and origin == "lower"
        assert len(calls) == 1
        assert (tmp_path / "gibs" / veg.LAND_COVER_LAYER[0] / "2024-01-01" / "5_1_2.png").is_file()

    def test_true_colour_substitute_is_retried_then_left_clear(self, tmp_path, monkeypatch):
        source, calls = self._source(tmp_path, monkeypatch, [_png("RGBA")])
        img, _, _ = source.get_image((1, 2, 5))
        assert len(calls) == veg.GibsTiles.ATTEMPTS and not img[..., 3].any()
        assert not (tmp_path / "gibs").exists()                       # tried again next time

    def test_a_retry_can_succeed(self, tmp_path, monkeypatch):
        source, calls = self._source(tmp_path, monkeypatch, [500, _png("RGBA"), _png("P")])
        img, _, _ = source.get_image((1, 2, 5))
        assert len(calls) == 3 and img[..., 3].all()

    def test_missing_tile_is_clear(self, tmp_path, monkeypatch):
        source, calls = self._source(tmp_path, monkeypatch, [404])
        img, _, _ = source.get_image((31, 2, 5))
        assert len(calls) == 1 and not img[..., 3].any()

    @pytest.mark.parametrize("answers", [[500], [403]])
    def test_server_errors_raise(self, tmp_path, monkeypatch, answers):
        source, _ = self._source(tmp_path, monkeypatch, answers)
        with pytest.raises(urllib.error.HTTPError):
            source.get_image((1, 2, 5))


class TestColouring:
    def _mosaic(self):
        colours = [veg.LAND_COVER_CLASSES[1][1], veg.LAND_COVER_CLASSES[12][1], (134, 202, 227), (1, 2, 3)]
        mosaic = np.array([[(*c, 255) for c in colours] + [(33, 138, 33, 0)]], np.uint8)
        return mosaic

    def test_codes_from_palette_colours(self):
        # forest, cropland, water, an unknown colour, a gap
        assert veg.land_cover_codes(self._mosaic()).tolist() == [[1, 12, 0, 0, 0]]

    def test_rgba_shows_only_the_chosen_classes(self):
        rgba = veg.land_cover_rgba(self._mosaic(), alpha=0.5, classes=(12,))
        assert rgba[0, :, 3].tolist() == [0, 128, 0, 0, 0]
        assert tuple(rgba[0, 1, :3]) == veg.LAND_COVER_CLASSES[12][1]

    def test_ndvi_keeps_colours_and_gaps(self):
        mosaic = np.array([[(78, 148, 1, 255), (0, 0, 0, 0)]], np.uint8)
        rgba = veg.ndvi_rgba(mosaic, alpha=0.5)
        assert tuple(rgba[0, 0]) == (78, 148, 1, 128) and rgba[0, 1, 3] == 0


class TestKeys:
    def _ax(self):
        return Figure().add_subplot(projection=ccrs.Orthographic(0, 0))

    def test_land_cover_key(self):
        ax = self._ax()
        key = veg.add_land_cover_legend(ax, 2024, classes=(12, 14))
        assert key.get_title().get_text() == "Land cover 2024 (MODIS, IGBP classes)"
        assert [t.get_text() for t in key.get_texts()] == ["Croplands", "Cropland/natural vegetation mosaics"]
        assert key in ax.artists

    def test_ndvi_key(self):
        key = veg.add_ndvi_legend(self._ax(), 2026, 1)
        labels = [t.get_text() for t in key.get_texts()]
        assert labels[0] == "0.0" and labels[-1] == "0.9+"
        assert "January 2026" in key.get_title().get_text()

    def test_credits(self):
        assert "2024" in veg.land_cover_attribution(2024)
        assert "July 2026" in veg.ndvi_attribution(2026, 7)
