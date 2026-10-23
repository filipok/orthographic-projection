"""Tests for ortho.py — unit-level tests that do NOT hit the network."""

import argparse
import io
import os
import time
import urllib.error
from collections import Counter
from unittest import mock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Make sure ortho is importable from repo root
# ---------------------------------------------------------------------------
import ortho
from ice import IceLayers
from vegetation import GibsTiles


# ===================================================================
# Data constants
# ===================================================================


class TestConstants:
    """Smoke-test the built-in city and provider data."""

    def test_metropolises_not_empty(self):
        assert len(ortho.MAJOR_METROPOLISES) > 0

    def test_each_city_has_required_keys(self):
        for name, info in ortho.MAJOR_METROPOLISES.items():
            assert "lat" in info, f"{name} missing 'lat'"
            assert "lon" in info, f"{name} missing 'lon'"
            assert "slug" in info, f"{name} missing 'slug'"

    def test_lat_lon_ranges(self):
        for name, info in ortho.MAJOR_METROPOLISES.items():
            assert -90 <= info["lat"] <= 90, f"{name} lat out of range"
            assert -180 <= info["lon"] <= 180, f"{name} lon out of range"

    def test_tile_providers_list(self):
        assert "osm" in ortho.TILE_PROVIDERS
        assert "osm_plain" in ortho.TILE_PROVIDERS
        assert "osm_local" in ortho.TILE_PROVIDERS and "natural_earth" in ortho.TILE_PROVIDERS
        assert "google" in ortho.TILE_PROVIDERS
        assert "google_satellite" in ortho.TILE_PROVIDERS
        assert "nasa" in ortho.TILE_PROVIDERS


# ===================================================================
# BufferedTileSource
# ===================================================================


class TestBufferedTileSource:
    """Test the tile-domain buffering proxy."""

    def _make_fake_source(self):
        """Return a minimal mock that quacks like a Cartopy tile source."""
        source = mock.MagicMock()
        source.crs.x_limits = (0, 256)
        return source

    def test_crs_forwarded(self):
        inner = self._make_fake_source()
        buffered = ortho.BufferedTileSource(inner, tile_buffer_factor=0.5)
        assert buffered.crs is inner.crs

    def test_getattr_delegates(self):
        inner = self._make_fake_source()
        inner.some_attr = 42
        buffered = ortho.BufferedTileSource(inner, tile_buffer_factor=0.5)
        assert buffered.some_attr == 42

    def test_getattr_before_init_does_not_recurse(self):
        bare = ortho.BufferedTileSource.__new__(ortho.BufferedTileSource)
        with pytest.raises(AttributeError):
            bare.anything

    # --- tile fetching (review #22) ---------------------------------

    @staticmethod
    def _world(source):
        from shapely.geometry import box

        (x0, x1), (y0, y1) = source.crs.x_limits, source.crs.y_limits
        return box(x0, y0, x1, y1)

    def _render_domain(self, fail=lambda tile: False, rgb=False):
        """Fetch zoom-1 (2x2 tiles) with get_image stubbed; *fail* picks tiles that raise."""
        source = ortho.CachedOSM()
        channels = 3 if rgb else 4

        def get_image(tile):
            if fail(tile):
                raise urllib.error.URLError("network down")
            tile_img = np.full((256, 256, channels), 255, np.uint8)
            tile_img[..., :3] = 200
            return tile_img, source.tileextent(tile), "lower"

        buffered = ortho.BufferedTileSource(source, tile_buffer_factor=0.5)
        with mock.patch.object(source, "get_image", side_effect=get_image):
            img, _extent, _origin = buffered.image_for_domain(self._world(source), 1)
        return buffered, img

    def test_all_tiles_succeed(self):
        buffered, img = self._render_domain()
        assert img.shape[2] == 4 and img.shape[0] > 500   # 2x2 tiles of 256 px (edges shared)
        assert (img[..., 3] == 255).all()
        assert buffered.total_tiles == 4 and buffered.failed_tiles == []

    def test_failed_tile_becomes_transparent(self):
        buffered, img = self._render_domain(fail=lambda tile: tile[:2] == (0, 0))
        assert len(buffered.failed_tiles) == 1
        assert isinstance(buffered.failed_tiles[0][1], urllib.error.URLError)
        transparent = (img[..., 3] == 0).mean()
        assert 0.2 < transparent < 0.3                        # one tile of four is see-through
        assert ((img[..., 3] == 0) | (img[..., 3] == 255)).all()

    def test_all_tiles_failing_does_not_raise(self):
        # Cartopy's own merge raises ValueError here, crashing savefig
        buffered, img = self._render_domain(fail=lambda tile: True)
        assert len(buffered.failed_tiles) == buffered.total_tiles == 4
        assert (img[..., 3] == 0).all()

    def test_rgb_tiles_are_promoted_to_rgba(self):
        _buffered, img = self._render_domain(rgb=True)
        assert img.shape[2] == 4 and (img[..., 3] == 255).all()

    def test_postprocess_receives_the_merged_mosaic(self):
        source = ortho.CachedOSM()
        seen = []

        def postprocess(mosaic, extent):
            seen.append((mosaic.shape, extent))
            return np.zeros(mosaic.shape[:2] + (4,), np.uint8)

        buffered = ortho.BufferedTileSource(source, tile_buffer_factor=0.5, postprocess=postprocess)
        tile = (np.full((256, 256, 4), 200, np.uint8), None, "lower")
        with mock.patch.object(source, "get_image",
                               side_effect=lambda t: (tile[0], source.tileextent(t), "lower")):
            img, extent, _ = buffered.image_for_domain(self._world(source), 1)
        assert len(seen) == 1 and seen[0][1] == extent      # once, for the whole mosaic
        assert (img == 0).all()

    # --- views across the date line ------------------------------------

    @staticmethod
    def _date_line_domain():
        """From 165°E to 165°W: half of the last column of zoom-3 tiles and half of the first."""
        from shapely.geometry import box

        half = ortho.CachedOSM().crs.x_limits[1]
        tile = 2 * half / 8
        domain = box(half - 0.5 * tile, -1e6, half, 1e6).union(box(-half, -1e6, -half + 0.5 * tile, 1e6))
        return domain, half, tile

    def _date_line_render(self, domain, z=3, postprocess=None):
        """Fetch tiles for *domain*, each filled with its own column number."""
        source = ortho.CachedOSM()

        def get_image(tile):
            tile_img = np.full((256, 256, 4), 255, np.uint8)
            tile_img[..., 0] = tile[0]
            return tile_img, source.tileextent(tile), "lower"

        buffered = ortho.BufferedTileSource(source, tile_buffer_factor=0, postprocess=postprocess)
        with mock.patch.object(source, "get_image", side_effect=get_image):
            img, extent, _origin = buffered.image_for_domain(domain, z)
        return buffered, img, extent

    def test_tiles_across_the_date_line_are_one_mosaic(self):
        from shapely.geometry import box

        domain, half, tile = self._date_line_domain()
        buffered, img, extent = self._date_line_render(domain)
        assert buffered.crs is not buffered.tile_crs
        assert buffered.crs.proj4_params["lon_0"] == 180
        # Centred on the date line: column 7 (west of it) on the left, column 0 on the right
        assert extent[0] == pytest.approx(-tile) and extent[1] == pytest.approx(tile)
        assert img[0, 10, 0] == 7 and img[0, -10, 0] == 0

        # Cartopy gives the next domain in that frame; it is put back in the tiles' own first
        source = buffered.tile_source
        with mock.patch.object(source, "get_image", side_effect=lambda t: (
                np.zeros((256, 256, 4), np.uint8), source.tileextent(t), "lower")):
            # Just east of 0° in the tiles' frame: the first tile of zoom 3's fifth column
            _, extent, _ = buffered.image_for_domain(box(-half + 0.1 * tile, -1e6, -half + 0.4 * tile, 1e6), 3)
        assert buffered.crs is buffered.tile_crs
        assert extent[0] == pytest.approx(0) and extent[1] == pytest.approx(tile)

    def test_from_date_line_frame(self):
        from shapely.geometry import box

        _, half, tile = self._date_line_domain()
        back = ortho._from_date_line_frame(box(-0.5 * tile, -1e6, 0.25 * tile, 1e6), 2 * half)
        assert back.geom_type == "MultiPolygon"
        assert sorted(round(g.bounds[0] / tile, 3) for g in back.geoms) == [-4.0, 3.5]
        assert sorted(round(g.bounds[2] / tile, 3) for g in back.geoms) == [-3.75, 4.0]

    def test_postprocess_sees_the_unshifted_extent(self):
        domain, half, tile = self._date_line_domain()
        seen = []
        self._date_line_render(domain, postprocess=lambda img, extent: seen.append(extent) or img)
        assert seen[0][0] == pytest.approx(half - tile) and seen[0][1] == pytest.approx(half + tile)

    def test_a_whole_row_of_tiles_is_not_moved(self):
        buffered, _img, extent = self._date_line_render(self._world(ortho.CachedOSM()), z=1)
        assert buffered.crs is buffered.tile_crs
        assert extent[0] == pytest.approx(ortho.CachedOSM().crs.x_limits[0])

    def test_tiles_past_date_line(self):
        source = ortho.CachedOSM()
        x0, x1 = source.crs.x_limits
        wrapped = [(c, 3, 5) for c in (0, 1, 25, 26, 31)]
        assert ortho._tiles_past_date_line(wrapped, source, x0, x1) == {(0, 3, 5), (1, 3, 5)}
        assert ortho._tiles_past_date_line([(c, 3, 5) for c in range(10, 20)], source, x0, x1) == set()
        # A gap that does not reach both edges of the grid is left alone
        assert ortho._tiles_past_date_line([(c, 3, 5) for c in (2, 3, 7, 8)], source, x0, x1) == set()
        assert ortho._tiles_past_date_line([], source, x0, x1) == set()


# ===================================================================
# create_tile_source
# ===================================================================


class TestCreateTileSource:
    def test_osm(self):
        src = ortho.create_tile_source("osm")
        assert isinstance(src, ortho.BufferedTileSource)
        assert isinstance(src.tile_source, ortho.CachedOSM)
        assert src.tile_source.cache_path is None

    def test_osm_uses_cache_dir(self, tmp_path):
        src = ortho.create_tile_source("osm", cache_dir=str(tmp_path))
        assert str(src.tile_source.cache_path) == str(tmp_path)

    def test_osm_plain_shares_the_osm_cache(self, tmp_path):
        src = ortho.create_tile_source("osm_plain", cache_dir=str(tmp_path)).tile_source
        assert isinstance(src, ortho.CachedOSMPlain)
        assert src._cache_dir == tmp_path / "osm"

    def test_osm_plain_caches_the_tile_as_downloaded(self, tmp_path):
        src = ortho.create_tile_source("osm_plain", cache_dir=str(tmp_path)).tile_source
        tile = np.zeros((256, 256, 4), np.uint8)
        tile[...] = (*ortho.OSM_FOREST_RGB, 255)
        with mock.patch.object(ortho, "download_tile", return_value=tile) as download:
            first, _, _ = src.get_image((1, 1, 2))
            second, _, _ = src.get_image((1, 1, 2))
        assert download.call_count == 1
        assert tuple(first[0, 0]) == tuple(second[0, 0]) == (*ortho.OSM_LAND_RGB, 255)
        cached = np.load(tmp_path / "osm" / "1_1_2.npy")
        assert tuple(cached[0, 0]) == (*ortho.OSM_FOREST_RGB, 255)

    def test_google_is_never_cached(self, tmp_path):
        with mock.patch("google_tiles.create_session", return_value="S"):
            src = ortho.create_tile_source("google", cache_dir=str(tmp_path), api_key="k")
        assert src.tile_source.cache_path is None

    @pytest.mark.parametrize(
        "provider, map_type", [("google", "roadmap"), ("google_satellite", "satellite")]
    )
    def test_google_uses_map_tiles_api(self, provider, map_type):
        with mock.patch("google_tiles.create_session", return_value="S") as create:
            src = ortho.create_tile_source(provider, api_key="k")
        assert isinstance(src, ortho.BufferedTileSource)
        assert isinstance(src.tile_source, ortho.GoogleMapTiles)
        assert create.call_args.args[:2] == ("k", map_type)

    def test_google_without_key_raises(self, monkeypatch):
        for name in ortho.API_KEY_ENVS:
            monkeypatch.delenv(name, raising=False)
        with mock.patch("google_tiles.create_session") as create:
            with pytest.raises(ortho.GoogleTilesError, match="API key"):
                ortho.create_tile_source("google")
        create.assert_not_called()

    def test_nasa_is_cached_in_its_own_folder(self, tmp_path):
        src = ortho.create_tile_source("nasa", cache_dir=str(tmp_path))
        assert isinstance(src.tile_source, ortho.CachedNASA)
        assert str(src.tile_source.cache_path) == str(tmp_path)
        assert src.tile_source._cache_dir == tmp_path / "nasa"
        assert src.tile_source.max_age_days == 365        # the imagery never changes

    def test_nasa_url_is_zoom_row_column(self):
        url = ortho.create_tile_source("nasa").tile_source._image_url((4, 2, 3))   # (x, y, z)
        assert url == ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/"
                       "default/GoogleMapsCompatible_Level8/3/2/4.jpeg")

    def test_nasa_tiles_are_downloaded_and_cached(self, tmp_path):
        src = ortho.create_tile_source("nasa", cache_dir=str(tmp_path)).tile_source
        tile = np.full((256, 256, 4), 90, np.uint8)
        with mock.patch.object(ortho, "download_tile", return_value=tile) as download:
            first, _, _ = src.get_image((1, 1, 2))
            second, _, _ = src.get_image((1, 1, 2))
        assert download.call_count == 1                   # the second read is from the cache
        np.testing.assert_array_equal(first, second)
        assert (tmp_path / "nasa" / "1_1_2.npy").exists()

    def test_night_lights_are_black_marble_in_their_own_folder(self, tmp_path):
        src = ortho.create_tile_source("nasa_night", cache_dir=str(tmp_path)).tile_source
        assert isinstance(src, ortho.CachedNASANight)
        assert src._cache_dir == tmp_path / "nasa_night" and src.max_age_days == 365
        assert src._image_url((4, 2, 3)) == ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/"
                                             "default/2016-01-01/GoogleMapsCompatible_Level8/3/2/4.png")
        assert ortho.max_zoom(100, 0, "nasa_night") == 8 and ortho.zoom_for_radius(100, 0, "nasa_night") == 8

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unsupported.*nasa_night"):
            ortho.create_tile_source("bing")


class TestPlainOsmLand:
    @staticmethod
    def _strip(hex_colour, alpha=255):
        rgb = [int(hex_colour[i:i + 2], 16) for i in (0, 2, 4)]
        return tuple(int(v) for v in ortho.plain_osm_land(np.array([[[*rgb, alpha]]], np.uint8))[0, 0])

    @pytest.mark.parametrize("colour", ["add19e", "cdebb0", "d6d99f", "eef0d5", "f5e9c6"])   # forest, meadow, heath, farmland, sand
    def test_fill_becomes_land(self, colour):
        assert self._strip(colour) == (*ortho.OSM_LAND_RGB, 255)

    @pytest.mark.parametrize("colour", ["cfe0c3", "e0edcc", "e4e4c4", "f0efdf", "f4ecd8"])   # half a fill, half land
    def test_fill_edge_blended_with_land_becomes_land(self, colour):
        assert max(abs(a - b) for a, b in zip(self._strip(colour), (*ortho.OSM_LAND_RGB, 255))) <= 1

    @pytest.mark.parametrize("colour", [
        "def6c0",   # golf course
        "c8facc",   # park
        "c8d7ab",   # scrub
        "aedfa3",   # orchard
        "fff1ba",   # beach
        "aad3df",   # water
        "f2efe9",   # land
        "000000",   # text
    ])
    def test_other_colours_are_kept(self, colour):
        rgb = tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
        assert self._strip(colour) == (*rgb, 255)

    def test_alpha_is_kept(self):
        assert self._strip("add19e", alpha=0)[3] == 0


# ===================================================================
# build_output_filename
# ===================================================================


class TestBuildOutputFilename:
    def test_basic(self):
        result = ortho.build_output_filename("nyc", "osm", 3)
        assert result == "orthographic_map_nyc_osm_z3.png"

    def test_spaces_in_provider(self):
        result = ortho.build_output_filename("paris", "google satellite", 3)
        assert result == "orthographic_map_paris_google_satellite_z3.png"

    def test_a_globe_beside_joins_the_places(self):
        result = ortho.build_output_filename("lisbon", "osm", 3, beside_slug="tokyo")
        assert result == "orthographic_map_lisbon_and_tokyo_osm_z3.png"

    def test_both_hemispheres_suffix(self):
        result = ortho.build_output_filename("lisbon", "osm", 3, both_hemispheres=True)
        assert result == "orthographic_map_lisbon_osm_z3_hemispheres.png"

    def test_radius_suffix(self):
        assert ortho.build_output_filename("rome", "osm", 7, radius_km=2500.0, up=90) == \
            "orthographic_map_rome_osm_z7_r2500km_up90.png"

    def test_rectangle_suffix(self):
        assert ortho.build_output_filename("cluj", "osm", 7, radius_km=450.0, rectangle=1.5) == \
            "orthographic_map_cluj_osm_z7_r450km_rect1p5.png"
        tall = ortho.build_output_filename("cluj", "osm", 7, radius_km=450.0, rectangle=2 / 3)
        assert tall.endswith("_rect0p667.png")

    @pytest.mark.parametrize("up, suffix", [(0, ""), (360, ""), (180, "_up180"), (119.6, "_up120"), (-90, "_up270")])
    def test_rotation_suffix(self, up, suffix):
        assert ortho.build_output_filename("sydney", "osm", 3, up=up) == f"orthographic_map_sydney_osm_z3{suffix}.png"
        assert ortho.build_output_filename("sydney", "osm", 3, True, up).endswith(f"_hemispheres{suffix}.png")


class TestResolvePlace:
    def test_city_names_in_any_case(self):
        assert ortho.resolve_place("new delhi") == (
            ortho.MAJOR_METROPOLISES["New Delhi"]["lat"], ortho.MAJOR_METROPOLISES["New Delhi"]["lon"])

    def test_lat_lon(self):
        assert ortho.resolve_place("21.42, 39.83") == (21.42, 39.83)

    @pytest.mark.parametrize("place, match", [("Atlantis", "not a known city"), ("95,0", "out of range"),
                                              ("1,2,3", "not a known city")])
    def test_invalid(self, place, match):
        with pytest.raises(ValueError, match=match):
            ortho.resolve_place(place)


# ===================================================================
# antipode and render options
# ===================================================================


class TestAntipode:
    @pytest.mark.parametrize("lat, lon, expected", [
        (38.7223, -9.1393, (-38.7223, 170.8607)),   # Lisbon -> Tasman Sea, west of New Zealand
        (31.2304, 121.4737, (-31.2304, -58.5263)),  # Shanghai -> Entre Ríos, Argentina
        (0.0, 0.0, (-0.0, 180.0)),
        (10.0, 180.0, (-10.0, 0.0)),
        (-5.0, -180.0, (5.0, 0.0)),
        (90.0, 45.0, (-90.0, -135.0)),
    ])
    def test_opposite_point(self, lat, lon, expected):
        anti_lat, anti_lon = ortho.antipode(lat, lon)
        assert anti_lat == pytest.approx(expected[0])
        assert anti_lon == pytest.approx(expected[1])
        assert -180 <= anti_lon <= 180

    def test_far_side_rings_mirror_the_near_ones(self):
        # ~20 000 km to the antipode, so 17 500 / 15 000 km sit 2 500 / 5 000 km from it
        assert ortho.FAR_SIDE_RADII_KM == (17_500, 15_000)


class TestRadiusAndZoom:
    @pytest.mark.parametrize("radius, lat, provider, zoom", [
        (2_500, 0, "osm", 7), (2_500, 46, "osm", 6), (2_500, -60, "osm", 6), (2_500, 89, "osm", 5),
        (1_000, 0, "osm", 8), (100, 0, "osm", 11), (100, 0, "nasa", 8),
        (5_000, 0, "osm", 6), (10_000, 0, "osm", 5), (10_000, 89, "osm", 3),
    ])
    def test_default_zoom_for_a_radius(self, radius, lat, provider, zoom):
        assert ortho.zoom_for_radius(radius, lat, provider) == zoom

    def test_max_zoom(self):
        assert ortho.max_zoom() == 4
        assert ortho.max_zoom(2_500) == 8 and ortho.max_zoom(2_500, 46) == 7
        assert ortho.max_zoom(100) == 12 and ortho.max_zoom(100, 0, "nasa") == 8
        assert ortho.max_zoom(10_000, 60) == 5 and ortho.max_zoom(10_000, 89) == 4

    @pytest.mark.parametrize("radius, rings", [
        (2_500, (500, 1_000, 1_500, 2_000)),
        (1_000, (250, 500, 750)),
        (800, (250, 500, 750)),
        (10_000, (2_500, 5_000, 7_500)),
        (100, (25, 50, 75)),
        (3_000, (1_000, 2_000)),
    ])
    def test_ring_radii_are_round_and_inside_the_edge(self, radius, rings):
        assert ortho.ring_radii(radius) == rings

    def test_prompt_for_radius(self, monkeypatch, capsys):
        answers = iter(["", "y", "50", "abc", "1,200"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        assert ortho.prompt_for_radius() is None
        assert ortho.prompt_for_radius() == 2_500
        assert ortho.prompt_for_radius() == 1_200                  # after two bad answers
        assert capsys.readouterr().out.count("between 100 and 10,000 km") == 2


class TestValidateRenderOptions:
    @pytest.mark.parametrize("kwargs, match", [
        (dict(radius_km=99), "radius_km must be between 100 and 10,000"),
        (dict(radius_km=10_001), "radius_km must be between"),
        (dict(radius_km=float("nan")), "radius_km must be between"),
        (dict(radius_km=2_500, both_hemispheres=True), "cannot have both hemispheres"),
        (dict(zoom=5), "between 1 and 4 for the whole globe"),
        (dict(zoom=0), "between 1 and 4"),
        (dict(zoom=9, radius_km=2_500), "between 1 and 8 for a 2,500 km radius"),
        (dict(zoom=9, radius_km=100, tile_provider="nasa"), "between 1 and 8"),
        (dict(zoom=8, radius_km=2_500, lat=46), "between 1 and 7"),
        (dict(rectangle=1.5), "cut from a radius map"),
        (dict(rectangle=6.0, radius_km=2_500), "rectangle must be between"),
        (dict(rectangle=1.0, radius_km=5_100), "reach past the edge of the globe; use a radius of at most 5,003 km"),
    ])
    def test_zoom_and_radius(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            ortho.validate_render_options(dpi=100, koppen_alpha=0.5, **kwargs)

    def test_zoom_within_limits(self):
        ortho.validate_render_options(dpi=100, koppen_alpha=0.5, zoom=4)
        ortho.validate_render_options(dpi=100, koppen_alpha=0.5, zoom=8, radius_km=2_500)
        ortho.validate_render_options(dpi=100, zoom=8, radius_km=2_500, rectangle=1.5)
        ortho.validate_render_options(dpi=100, radius_km=5_000, rectangle=1.0)

    def test_both_hemispheres_halves_the_dpi_cap(self):
        ortho.validate_render_options(dpi=ortho.MAX_DPI // 2, koppen_alpha=0.5, both_hemispheres=True)
        with pytest.raises(ValueError, match="with both hemispheres"):
            ortho.validate_render_options(dpi=ortho.MAX_DPI // 2 + 1, koppen_alpha=0.5, both_hemispheres=True)
        ortho.validate_render_options(dpi=ortho.MAX_DPI, koppen_alpha=0.5)

    def test_a_globe_beside_halves_the_dpi_cap(self):
        ortho.validate_render_options(dpi=ortho.MAX_DPI // 2, beside=(35.7, 139.7))
        with pytest.raises(ValueError, match="with two globes"):
            ortho.validate_render_options(dpi=ortho.MAX_DPI // 2 + 1, beside=(35.7, 139.7))

    @pytest.mark.parametrize("kwargs, match", [
        (dict(beside=(35.7, 139.7), both_hemispheres=True), "antipode or another place, not both"),
        (dict(beside=(91.0, 0.0)), "beside must be a latitude"),
    ])
    def test_bad_globe_beside(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            ortho.validate_render_options(dpi=100, **kwargs)

    def test_radius_zoom_with_a_globe_beside_follows_the_centre_nearer_the_equator(self):
        # Zoom 8 is too fine for 2,500 km at 46°N alone (see above), not with a globe on the equator
        ortho.validate_render_options(dpi=100, zoom=8, radius_km=2_500, lat=46, beside=(0.0, 0.0))
        assert ortho.zoom_latitude(46, (-10.0, 0.0)) == -10 and ortho.zoom_latitude(46) == 46

    @pytest.mark.parametrize("alpha", [-0.1, 1.5])
    def test_elevation_alpha_range(self, alpha):
        with pytest.raises(ValueError, match="elevation_alpha"):
            ortho.validate_render_options(dpi=100, koppen_alpha=0.5, elevation_alpha=alpha)


# ===================================================================
# configure_tile_cache
# ===================================================================


class TestConfigureTileCache:
    def test_creates_directory(self, tmp_path):
        cache = tmp_path / "tile_cache"
        result = ortho.configure_tile_cache(str(cache))
        assert cache.is_dir()
        assert result == str(cache)

    def test_default_when_none(self):
        with mock.patch("os.makedirs") as makedirs:
            assert ortho.configure_tile_cache(None) == ortho.DEFAULT_CACHE_DIR
        makedirs.assert_called_once_with(ortho.DEFAULT_CACHE_DIR, exist_ok=True)

    def test_does_not_move_cartopy_data_dir(self, tmp_path):
        """Natural Earth data must stay where Cartopy keeps it (review #4)."""
        import cartopy

        before = cartopy.config["data_dir"]
        ortho.configure_tile_cache(str(tmp_path / "tiles"))
        assert cartopy.config["data_dir"] == before


# ===================================================================
# CachedOSM
# ===================================================================


def _png_bytes(colour=(10, 20, 30)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (256, 256), colour).save(buf, format="PNG")
    return buf.getvalue()


class TestCachedOSM:
    TILE = (1, 2, 3)

    @pytest.fixture
    def urlopen(self):
        with mock.patch("tile_fetch.urllib.request.urlopen") as m:
            m.side_effect = lambda *a, **k: io.BytesIO(_png_bytes())
            yield m

    def test_no_cache_by_default(self, urlopen):
        src = ortho.CachedOSM()
        assert src.cache_path is None
        img, _extent, origin = src.get_image(self.TILE)
        assert img.shape == (256, 256, 4)
        assert origin == "lower"

    def test_successful_tile_is_cached_and_reused(self, tmp_path, urlopen):
        first = ortho.CachedOSM(cache=str(tmp_path))
        img1, _, _ = first.get_image(self.TILE)
        cached = tmp_path / "osm" / "1_2_3.npy"
        assert cached.is_file()
        assert not list((tmp_path / "osm").glob("*.part"))

        # A new instance (next run) reads the tile from disk without the network
        urlopen.reset_mock()
        second = ortho.CachedOSM(cache=str(tmp_path))
        img2, _, _ = second.get_image(self.TILE)
        urlopen.assert_not_called()
        assert (img1 == img2).all()

    def test_failed_download_raises_and_is_not_cached(self, tmp_path, urlopen):
        urlopen.side_effect = urllib.error.URLError("network down")
        src = ortho.CachedOSM(cache=str(tmp_path))
        with pytest.raises(urllib.error.URLError):
            src.get_image(self.TILE)
        assert not (tmp_path / "osm" / "1_2_3.npy").exists()

        # Once the network is back the real tile is fetched and cached
        urlopen.side_effect = lambda *a, **k: io.BytesIO(_png_bytes())
        img, _, _ = src.get_image(self.TILE)
        assert tuple(img[0, 0, :3]) == (10, 20, 30)
        assert (tmp_path / "osm" / "1_2_3.npy").exists()

    def test_stale_tile_is_refetched(self, tmp_path, urlopen):
        ortho.CachedOSM(cache=str(tmp_path)).get_image(self.TILE)
        cached = tmp_path / "osm" / "1_2_3.npy"
        old = time.time() - (ortho.TILE_CACHE_MAX_AGE_DAYS + 1) * 86400
        os.utime(cached, (old, old))

        urlopen.reset_mock()
        ortho.CachedOSM(cache=str(tmp_path)).get_image(self.TILE)
        urlopen.assert_called_once()
        assert time.time() - cached.stat().st_mtime < 60   # refreshed on disk

    def test_sends_user_agent_and_timeout(self, urlopen):
        ortho.CachedOSM(timeout=7).get_image(self.TILE)
        request = urlopen.call_args.args[0]
        assert request.get_header("User-agent")
        assert urlopen.call_args.kwargs["timeout"] == 7


# ===================================================================
# load_env_files
# ===================================================================


class TestLoadEnvFiles:
    """Key files are loaded in priority order: the first file to set a variable wins."""

    @pytest.fixture
    def home(self, tmp_path, monkeypatch):
        """Point ~ at an empty temp dir so the real ~/myapikeys.env is never read."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(ortho.os.path, "expanduser",
                            lambda p: p.replace("~", str(home), 1))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ORTHO_ENV_FILE", raising=False)
        for name in ("ORTHO_TEST_A", "ORTHO_TEST_B"):
            monkeypatch.delenv(name, raising=False)
        return home

    def test_loads_central_key_file(self, home):
        (home / "myapikeys.env").write_text("ORTHO_TEST_A=central\n", encoding="utf-8")
        loaded = ortho.load_env_files()
        assert [os.path.normpath(p) for p in loaded] == [str(home / "myapikeys.env")]
        assert os.environ["ORTHO_TEST_A"] == "central"

    def test_priority_and_no_override(self, home, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit.env"
        explicit.write_text("ORTHO_TEST_A=explicit\n", encoding="utf-8")
        (home / "myapikeys.env").write_text("ORTHO_TEST_A=central\nORTHO_TEST_B=central\n",
                                            encoding="utf-8")
        (tmp_path / ".env").write_text("ORTHO_TEST_B=local\n", encoding="utf-8")
        monkeypatch.setenv("ORTHO_ENV_FILE", str(explicit))

        loaded = ortho.load_env_files()

        assert len(loaded) == 3
        assert os.environ["ORTHO_TEST_A"] == "explicit"   # $ORTHO_ENV_FILE beats ~/myapikeys.env
        assert os.environ["ORTHO_TEST_B"] == "central"    # ~/myapikeys.env beats ./.env

    def test_existing_environment_wins(self, home, monkeypatch):
        (home / "myapikeys.env").write_text("ORTHO_TEST_A=file\n", encoding="utf-8")
        monkeypatch.setenv("ORTHO_TEST_A", "shell")
        ortho.load_env_files()
        assert os.environ["ORTHO_TEST_A"] == "shell"

    def test_no_files_is_fine(self, home):
        assert ortho.load_env_files() == []


# ===================================================================
# CLI parser
# ===================================================================


class TestCLIParser:
    def _parse(self, argv):
        parser = ortho.build_cli_parser()
        return parser.parse_args(argv)

    def test_city_flag(self):
        args = self._parse(["--city", "nyc"])
        assert args.city == "nyc"

    def test_sea_flags(self):
        args = self._parse(["--city", "lisbon"])
        assert args.temperature_over_sea is False and args.sea_temperature is None
        args = self._parse(["--city", "lisbon", "--temperature", "--temperature-over-sea", "--sea-temperature"])
        assert args.temperature_over_sea is True and args.sea_temperature == "year"
        assert self._parse(["--city", "lisbon", "--sea-temperature", "jan"]).sea_temperature == "jan"

    def test_beside_flag(self):
        assert self._parse(["--city", "lisbon"]).beside is None
        assert self._parse(["--city", "lisbon", "--beside", "Tokyo"]).beside == "Tokyo"

    def test_no_circles_flag(self):
        assert self._parse(["--city", "nyc"]).no_circles is False
        assert self._parse(["--city", "nyc", "--no-circles"]).no_circles is True

    def test_circles_from_flag(self):
        assert self._parse(["--city", "nyc"]).circles_from is None
        assert self._parse(["--city", "nyc", "--circles-from", "London"]).circles_from == "London"
        assert self._parse(["--city", "nyc", "--circles-from=-33.9,151.2"]).circles_from == "-33.9,151.2"

    def test_lat_lon_flags(self):
        args = self._parse(["--lat", "35.0", "--lon", "139.0"])
        assert args.lat == 35.0
        assert args.lon == 139.0

    def test_defaults(self):
        args = self._parse(["--city", "paris"])
        assert args.provider == "osm"
        assert args.zoom is None            # 3 for the globe, or chosen from --radius
        assert args.radius is None and args.wind is None
        assert args.dpi == ortho.DEFAULT_DPI == 300
        assert args.output is None
        assert args.output_dir is None
        assert args.cache_dir is None

    @pytest.mark.parametrize("raw", ["NYC", "Nyc", "nyc"])
    def test_city_is_case_insensitive(self, raw):
        assert self._parse(["--city", raw]).city == "nyc"

    def test_multi_word_city_is_case_insensitive(self):
        assert self._parse(["--city", "New Delhi"]).city == "new delhi"

    def test_unknown_city_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["--city", "Atlantis"])

    def test_city_and_lat_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--lat", "40.0"])

    def test_route_defaults_to_none(self):
        assert self._parse(["--city", "nyc"]).route is None

    def test_route_is_repeatable(self):
        args = self._parse(["--city", "nyc", "--route", "a.geojson", "--route", "b.geojson"])
        assert args.route == ["a.geojson", "b.geojson"]

    def test_route_legend_flag(self):
        assert self._parse(["--city", "nyc"]).route_legend is False
        assert self._parse(["--city", "nyc", "--route-legend"]).route_legend is True

    def test_ice_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.ice is False and args.ice_year is None
        args = self._parse(["--city", "nyc", "--ice", "--ice-year", "2012"])
        assert args.ice is True and args.ice_year == 2012

    def test_nasa_provider_accepted(self):
        assert self._parse(["--city", "nyc", "--provider", "nasa"]).provider == "nasa"

    def test_orientation_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.up == 0 and args.up_toward is None
        assert self._parse(["--city", "nyc", "--up", "180"]).up == 180
        assert self._parse(["--city", "nyc", "--up-toward", "Tokyo"]).up_toward == "Tokyo"
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--up", "90", "--up-toward", "Tokyo"])

    def test_koppen_class_flag(self):
        assert self._parse(["--city", "nyc"]).koppen_class is None
        assert self._parse(["--city", "nyc", "--koppen-class", "Cfb", "--koppen-class", "Cs"]).koppen_class == [
            "Cfb", "Cs"]

    def test_trewartha_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.trewartha is False and args.trewartha_class is None
        args = self._parse(["--city", "nyc", "--trewartha", "--trewartha-class", "Do", "--trewartha-class", "C"])
        assert args.trewartha is True and args.trewartha_class == ["Do", "C"]

    def test_soil_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.soil is False and args.soil_class is None and args.soil_alpha == 0.6
        args = self._parse(["--city", "nyc", "--soil", "--soil-class", "CH", "--soil-class", "Podzols",
                            "--soil-alpha", "0.7"])
        assert args.soil and args.soil_class == ["CH", "Podzols"] and args.soil_alpha == 0.7

    def test_biome_flags(self):
        args = self._parse(["--city", "nyc"])
        assert not args.biomes and not args.ecoregions and args.biome_class is None and args.biome_alpha == 0.6
        args = self._parse(["--city", "nyc", "--ecoregions", "--biome-class", "temperate", "--biome-class", "14",
                            "--biome-alpha", "0.5"])
        assert args.ecoregions and args.biome_class == ["temperate", "14"] and args.biome_alpha == 0.5
        assert args.realms is None and not args.realm_regions
        assert self._parse(["--city", "nyc", "--realms"]).realms == "wwf"
        args = self._parse(["--city", "nyc", "--realms", "holt", "--realm-regions"])
        assert args.realms == "holt" and args.realm_regions
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--realms", "sclater"])

    def test_borders_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.borders is None and args.borders_source == "auto" and args.borders_alpha == 0.55
        args = self._parse(["--city", "nyc", "--borders", "-500", "--borders-source", "cliopatria",
                            "--borders-alpha", "0.4"])
        assert args.borders == "-500" and args.borders_source == "cliopatria" and args.borders_alpha == 0.4
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--borders", "1500", "--borders-source", "wikipedia"])

    def test_atlas_layer_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.earthquakes is None and args.cyclones is None and args.population is None and not args.plates
        args = self._parse(["--city", "nyc", "--ocean-depth", "--ice-age", "--plates", "--earthquakes", "--volcanoes",
                            "--currents", "--rivers", "--basins", "--cyclones", "--time-zones", "--permafrost",
                            "--population", "--livestock", "goats"])
        assert args.ocean_depth and args.ice_age and args.plates and args.volcanoes and args.currents
        assert args.rivers and args.basins and args.time_zones and args.permafrost
        assert args.earthquakes == 6.0 and args.cyclones == 1980 and args.population == 2020 and args.livestock == "goats"
        args = self._parse(["--city", "nyc", "--earthquakes", "7.5", "--cyclones", "2000", "--population", "2005"])
        assert args.earthquakes == 7.5 and args.cyclones == 2000 and args.population == 2005

    def test_malaria_and_aedes_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.malaria is None and args.malaria_year == 2025 and args.malaria_alpha == 0.75 and args.aedes is None
        args = self._parse(["--city", "nyc", "--malaria", "pv", "--malaria-year", "2010", "--malaria-alpha", "0.5",
                            "--aedes"])
        assert (args.malaria, args.malaria_year, args.malaria_alpha, args.aedes) == ("pv", 2010, 0.5, "both")
        assert self._parse(["--city", "nyc", "--aedes", "albopictus"]).aedes == "albopictus"

    def test_soil_property_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.soil_property is None and args.soil_depth is None
        args = self._parse(["--city", "nyc", "--soil-property", "ph", "--soil-depth", "30-60cm"])
        assert args.soil_property == "ph" and args.soil_depth == "30-60cm"
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--soil-property", "acidity"])

    def test_vegetation_flags(self):
        args = self._parse(["--city", "nyc"])
        assert not args.landcover and args.landcover_class is None and args.ndvi is None
        assert args.vegetation_alpha == 0.7
        args = self._parse(["--city", "nyc", "--landcover-class", "forest", "--landcover-class", "cropland",
                            "--landcover-year", "2010", "--vegetation-alpha", "0.5"])
        assert args.landcover_class == ["forest", "cropland"] and args.landcover_year == 2010
        assert self._parse(["--city", "nyc", "--ndvi", "july"]).ndvi == "july"

    def test_radius_flag(self):
        assert self._parse(["--city", "paris", "--radius"]).radius == 2_500
        assert self._parse(["--city", "paris", "--radius", "800"]).radius == 800
        assert self._parse(["--city", "paris", "--zoom", "7"]).zoom == 7

    def test_climate_mean_flags(self):
        args = self._parse(["--city", "paris", "--temperature"])
        assert args.temperature == "year" and args.precipitation is None and args.humidity is None
        assert self._parse(["--city", "paris", "--precipitation", "jja"]).precipitation == "jja"
        assert self._parse(["--city", "paris", "--humidity", "7"]).humidity == "7"
        assert self._parse(["--city", "paris"]).koppen_alpha is None

    @pytest.mark.parametrize("flags", [["--temperature", "--humidity"], ["--koppen", "--precipitation"]])
    def test_one_climate_layer(self, flags, capsys):
        with pytest.raises(SystemExit):
            self._parse(["--city", "paris", *flags])
        assert "not allowed with argument" in capsys.readouterr().err

    def test_wind_flag(self):
        assert self._parse(["--city", "paris", "--wind"]).wind == "year"
        assert self._parse(["--city", "paris", "--wind", "jja"]).wind == "jja"

    def test_elevation_flags(self):
        args = self._parse(["--city", "nyc"])
        assert args.elevation is False and args.elevation_alpha == 0.8
        args = self._parse(["--city", "nyc", "--elevation", "--elevation-alpha", "0.6"])
        assert args.elevation is True and args.elevation_alpha == 0.6

    def test_one_climate_classification_at_a_time(self):
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--koppen", "--trewartha"])

    def test_climate_alpha_and_its_old_name(self):
        assert self._parse(["--city", "nyc", "--climate-alpha", "0.7"]).koppen_alpha == 0.7
        assert self._parse(["--city", "nyc", "--koppen-alpha", "0.6"]).koppen_alpha == 0.6

    def test_crop_flags(self):
        assert self._parse(["--city", "nyc"]).crop is None
        args = self._parse(["--city", "nyc", "--crop", "wheat", "--crop", "rice:#00ffff"])
        assert args.crop == ["wheat", "rice:#00ffff"]
        assert self._parse(["--list-crops"]).list_crops is True

    def test_list_crops_prints_names_and_exits(self, capsys, monkeypatch):
        monkeypatch.setattr(ortho.sys, "argv", ["ortho.py", "--list-crops"])
        with mock.patch.object(ortho, "run_cli") as run:
            ortho.main()
        run.assert_not_called()
        out = capsys.readouterr().out
        assert out.startswith("173 crops") and "wheat" in out and "sugarcane" in out

    def test_both_hemispheres_flag(self):
        assert self._parse(["--city", "nyc"]).both_hemispheres is False
        assert self._parse(["--city", "nyc", "--both-hemispheres"]).both_hemispheres is True


# ===================================================================
# Grouped --help and recipe files (--config)
# ===================================================================


RECIPE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recipes")


class TestGroupedHelp:
    def test_options_are_grouped_by_topic(self):
        help_text = ortho.build_cli_parser().format_help()
        for title in ("Location:", "Imagery and output:", "Globe layout:", "Climate (Köppen-Geiger, Trewartha or CHELSA means):",
                      "Polar ice:", "Crops (CROPGRIDS):", "Routes and areas:", "Recipes:"):
            assert title in help_text
        city_entry = help_text.index("\n  --city ")             # its entry, not the usage line
        assert help_text.index("Location:") < city_entry < help_text.index("Imagery and output:")


class TestRecipes:
    def _recipe(self, tmp_path, text, name="recipe.toml"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_settings_come_from_the_recipe(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "lisbon"\nprovider = "nasa"\nzoom = 2\n'
                                      'route_legend = true\nboth-hemispheres = true\ncrop = ["wheat", "rice"]\n')
        args = ortho.parse_cli_args(["--config", path])
        assert (args.city, args.provider, args.zoom) == ("lisbon", "nasa", 2)
        assert args.route_legend and args.both_hemispheres
        assert args.crop == ["wheat", "rice"]
        assert args.config == path

    def test_command_line_overrides_and_lists_add_up(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "lisbon"\nprovider = "nasa"\ncrop = ["wheat"]\n')
        args = ortho.parse_cli_args(["--config", path, "--provider", "osm", "--crop", "rice"])
        assert args.provider == "osm"
        assert args.crop == ["wheat", "rice"]

    def test_command_line_location_replaces_the_recipes(self, tmp_path):
        path = self._recipe(tmp_path, 'lat = 62\nlon = 15\n')
        args = ortho.parse_cli_args(["--config", path, "--city", "paris"])
        assert (args.city, args.lat, args.lon) == ("paris", None, None)
        path = self._recipe(tmp_path, 'city = "paris"\n', name="city.toml")
        args = ortho.parse_cli_args(["--config", path, "--lat", "-33.9", "--lon", "151.2"])
        assert (args.city, args.lat, args.lon) == (None, -33.9, 151.2)

    def test_command_line_orientation_replaces_the_recipes(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "nyc"\nup-toward = "london"\n')
        args = ortho.parse_cli_args(["--config", path, "--up", "180"])
        assert (args.up, args.up_toward) == (180, None)

    def test_abbreviated_options_still_override(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "nyc"\nup = 90\n')
        args = ortho.parse_cli_args(["--config", path, "--up-t", "london"])
        assert (args.up, args.up_toward) == (0.0, "london")

    def test_negative_numbers(self, tmp_path):
        path = self._recipe(tmp_path, 'lat = -33.9\nlon = 151.2\nup-toward = "-15.4,28.3"\n')
        args = ortho.parse_cli_args(["--config", path])
        assert (args.lat, args.lon, args.up_toward) == (-33.9, 151.2, "-15.4,28.3")

    def test_route_paths_are_relative_to_the_recipe(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "lisbon"\nroute = ["../routes/a.geojson"]\n',
                            name="recipes/r.toml")
        args = ortho.parse_cli_args(["--config", path])
        assert args.route == [str(tmp_path / "routes" / "a.geojson")]

    def test_false_flag_is_simply_off(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "nyc"\nice = false\n')
        assert ortho.parse_cli_args(["--config", path]).ice is False

    @pytest.mark.parametrize("text, message", [
        ('citty = "paris"\n', "unknown option 'citty'. Did you mean 'city'?"),
        ('config = "other.toml"\n', "unknown option 'config'"),
        ('list-crops = true\n', "unknown option 'list-crops'"),
        ('ice = "yes"\n', "ice must be true or false"),
        ('zoom = [3, 4]\n', "zoom takes a single value"),
        ('city = {name = "paris"}\n', "city must be text or a number"),
        ('provider = "bing"\n', "invalid choice"),
        ('city = \n', "is not valid TOML"),
    ])
    def test_errors_exit_like_a_bad_command_line(self, tmp_path, capsys, text, message):
        path = self._recipe(tmp_path, text)
        with pytest.raises(SystemExit) as excinfo:
            ortho.parse_cli_args(["--config", path])
        assert excinfo.value.code == 2
        assert message in capsys.readouterr().err

    def test_missing_recipe(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            ortho.parse_cli_args(["--config", str(tmp_path / "nope.toml")])
        assert "cannot read recipe" in capsys.readouterr().err

    def test_without_config_parses_as_before(self):
        args = ortho.parse_cli_args(["--city", "nyc", "--ice"])
        assert args.city == "nyc" and args.ice and args.config is None

    def test_main_runs_the_recipe(self, tmp_path, monkeypatch):
        path = self._recipe(tmp_path, 'city = "lusaka"\nkoppen-class = ["Cwa"]\n')
        monkeypatch.setattr(ortho.sys, "argv", ["ortho.py", "--config", path])
        with mock.patch.object(ortho, "run_cli") as run, \
                mock.patch.object(ortho, "load_env_files"):
            ortho.main()
        args = run.call_args.args[0]
        assert args.city == "lusaka" and args.koppen_class == ["Cwa"]

    def test_recipe_accepts_option_aliases(self, tmp_path):
        path = self._recipe(tmp_path, 'city = "lusaka"\ntrewartha = true\nclimate-alpha = 0.7\n')
        args = ortho.parse_cli_args(["--config", path])
        assert args.trewartha is True and args.koppen_alpha == 0.7


class TestBundledRecipes:
    RECIPES = sorted(f for f in os.listdir(RECIPE_DIR) if f.endswith(".toml"))

    def test_there_is_a_recipe_per_readme_sample(self):
        assert len(self.RECIPES) >= 9

    @pytest.mark.parametrize("name", RECIPES)
    def test_recipe_parses_and_its_routes_exist(self, name):
        args = ortho.parse_cli_args(["--config", os.path.join(RECIPE_DIR, name)])
        assert args.city or args.lat is not None
        assert args.output and args.output.startswith("orthographic_map_")   # ignored by git
        assert all(os.path.isfile(route) for route in args.route or [])

    def test_viking_recipe_matches_the_command_line(self):
        root = os.path.dirname(RECIPE_DIR)
        routes = [os.path.join(root, "routes", f"viking_{n}.geojson")
                  for n in ("homelands", "settlements", "trade", "raids", "exploration")]
        typed = ["--lat", "62", "--lon", "15", "--route-legend", "--ice",
                 "-o", "orthographic_map_scandinavia_osm_z3_vikings_ice.png"]
        for route in routes:
            typed += ["--route", route]
        from_recipe = vars(ortho.parse_cli_args(["--config", os.path.join(RECIPE_DIR, "viking_routes.toml")]))
        from_flags = vars(ortho.parse_cli_args(typed))
        from_recipe.pop("config")
        from_flags.pop("config")
        assert from_recipe == from_flags


# ===================================================================
# run_cli validation
# ===================================================================


class TestRunCLIValidation:
    """Test that run_cli exits cleanly on bad input (no rendering)."""

    def _make_args(self, **overrides):
        defaults = dict(
            city=None, lat=None, lon=None,
            provider="osm", zoom=3, dpi=300,
            output=None, output_dir=None, cache_dir=None,
            koppen=False, koppen_alpha=None, route=None, route_legend=False,
            both_hemispheres=False, ice=False, ice_year=None, crop=None, no_cache=False,
            koppen_class=None, up=0.0, up_toward=None, circles_from=None, no_circles=False, beside=None, temperature_over_sea=False, sea_temperature=None,
            trewartha=False, trewartha_class=None,
            elevation=False, elevation_alpha=0.8, soil=False, soil_class=None, soil_alpha=0.6,
            soil_property=None, soil_depth=None,
            landcover=False, landcover_class=None, landcover_year=None, ndvi=None, vegetation_alpha=0.7,
            biomes=False, biome_class=None, ecoregions=False, realms=None, realm_regions=False, biome_alpha=0.6,
            borders=None, borders_source="auto", borders_alpha=0.55,
            ocean_depth=False, ice_age=False, plates=False, earthquakes=None, volcanoes=False, currents=False,
            rivers=False, basins=False, basins_alpha=0.5, cyclones=None, time_zones=False, permafrost=False,
            permafrost_alpha=0.7, population=None, population_alpha=0.7, livestock=None, livestock_alpha=0.7,
            malaria=None, malaria_year=2025, malaria_alpha=0.75, aedes=None, species=None, species_years=None,
            iucn=None, iucn_data=None, iucn_alpha=0.45, cities=None, cities_min=None,
            indicator=None, indicator_year=None, indicator_alpha=0.75, industry=None,
            shipping=None, shipping_alpha=0.85, ports=None, air_routes=None, relief_units=None,
            relief_alpha=0.55, ro_admin=None, ro_admin_alpha=0.0, text_scale=1.0, language="en",
            wind=None, radius=None, rectangle=None, temperature=None, precipitation=None, humidity=None,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_lon_without_lat_exits(self):
        args = self._make_args(lon=50.0)
        with pytest.raises(SystemExit):
            ortho.run_cli(args)

    def test_lat_without_lon_exits(self):
        args = self._make_args(lat=50.0)
        with pytest.raises(SystemExit):
            ortho.run_cli(args)

    def test_no_location_exits(self):
        args = self._make_args()
        with pytest.raises(SystemExit):
            ortho.run_cli(args)

    def test_lat_out_of_range_exits(self):
        args = self._make_args(lat=100.0, lon=50.0)
        with pytest.raises(SystemExit):
            ortho.run_cli(args)

    def test_lon_out_of_range_exits(self):
        args = self._make_args(lat=50.0, lon=200.0)
        with pytest.raises(SystemExit):
            ortho.run_cli(args)

    def test_mixed_case_city_resolves_to_display_name(self):
        args = ortho.build_cli_parser().parse_args(["--city", "SAN FRANCISCO"])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["city_name"] == "San Francisco"
        assert kwargs["output_filename"] == "orthographic_map_san_francisco_osm_z3.png"

    @pytest.mark.parametrize("provider", ["google", "google_satellite"])
    def test_google_without_key_exits_before_render(self, provider, monkeypatch):
        for name in ortho.API_KEY_ENVS:
            monkeypatch.delenv(name, raising=False)
        args = self._make_args(city="paris", provider=provider)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_google_api_error_exits_cleanly(self, monkeypatch):
        monkeypatch.setenv(ortho.API_KEY_ENV, "k")
        args = self._make_args(city="paris", provider="google")
        with mock.patch.object(ortho, "generate_orthographic_map",
                               side_effect=ortho.GoogleTilesError("API key not valid")), \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit) as excinfo:
                ortho.run_cli(args)
        assert excinfo.value.code == 1

    @pytest.mark.parametrize("overrides", [
        {"koppen_alpha": 5.0},
        {"koppen_alpha": -0.1},
        {"dpi": 0},
        {"dpi": 5000},
    ])
    def test_invalid_render_options_exit_before_render(self, overrides):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit) as excinfo:
                ortho.run_cli(args)
        assert excinfo.value.code == 1
        render.assert_not_called()

    def test_output_dir_ignored_with_explicit_output_warns(self, tmp_path, caplog):
        args = self._make_args(city="paris", output=str(tmp_path / "x.png"),
                               output_dir=str(tmp_path / "ignored"))
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--output-dir is ignored" in caplog.text
        assert render.call_args.kwargs["output_dir"] is None

    def test_cache_dir_passed_to_renderer(self, tmp_path):
        args = self._make_args(city="paris", cache_dir=str(tmp_path / "tiles"))
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            ortho.run_cli(args)
        assert render.call_args.kwargs["tile_cache_dir"] == str(tmp_path / "tiles")

    def test_no_cache_flag_disables_cache(self):
        args = self._make_args(city="paris", no_cache=True)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache") as configure:
            ortho.run_cli(args)
        configure.assert_not_called()
        assert render.call_args.kwargs["tile_cache_dir"] is None

    def test_missing_route_file_exits_before_render(self, tmp_path):
        args = self._make_args(city="paris", route=[str(tmp_path / "nope.geojson")])
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_routes_passed_to_renderer(self, tmp_path):
        route_file = tmp_path / "r.geojson"
        route_file.write_text(
            '{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}', encoding="utf-8"
        )
        args = self._make_args(city="paris", route=[str(route_file)])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        (route,) = render.call_args.kwargs["routes"]
        assert route.name == "r"
        assert render.call_args.kwargs["route_legend"] is False

    def test_route_legend_passed_to_renderer(self, tmp_path):
        route_file = tmp_path / "r.geojson"
        route_file.write_text(
            '{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}', encoding="utf-8"
        )
        args = self._make_args(city="paris", route=[str(route_file)], route_legend=True)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["route_legend"] is True

    def test_both_hemispheres_passed_to_renderer_and_named(self):
        args = self._make_args(city="lisbon", both_hemispheres=True)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["both_hemispheres"] is True
        assert kwargs["output_filename"] == "orthographic_map_lisbon_osm_z3_hemispheres.png"

    @pytest.mark.parametrize("ice, ice_year, expected", [
        (False, None, False),
        (True, None, True),
        (False, 2012, True),   # choosing a year implies --ice
    ])
    def test_ice_passed_to_renderer(self, ice, ice_year, expected):
        args = self._make_args(city="paris", ice=ice, ice_year=ice_year)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["ice"] is expected
        assert render.call_args.kwargs["ice_year"] == ice_year

    @pytest.mark.parametrize("ice_year", [1978, 3000])
    def test_ice_year_out_of_range_exits_before_render(self, ice_year):
        args = self._make_args(city="paris", ice_year=ice_year)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_radius_picks_its_zoom_and_is_named(self):
        args = self._make_args(city="paris", radius=2_500.0, zoom=None)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["radius_km"] == 2_500 and kwargs["zoom"] == 6       # Paris is at 49°N
        assert kwargs["output_filename"] == "orthographic_map_paris_osm_z6_r2500km.png"

    def test_rectangle_passed_to_renderer(self):
        args = ortho.parse_cli_args(["--city", "paris", "--radius", "450", "--rectangle", "3:2"])
        assert args.rectangle == 1.5
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["rectangle"] == 1.5 and render.call_args.kwargs["radius_km"] == 450
        assert render.call_args.kwargs["output_filename"].endswith("_r450km_rect1p5.png")

    def test_bad_rectangle_is_a_usage_error(self, capsys):
        with pytest.raises(SystemExit):
            ortho.parse_cli_args(["--city", "paris", "--radius", "450", "--rectangle", "wide"])
        assert "written width:height" in capsys.readouterr().err

    def test_globe_zoom_defaults_to_3(self):
        args = self._make_args(city="paris", zoom=None)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["zoom"] == 3 and render.call_args.kwargs["radius_km"] is None

    @pytest.mark.parametrize("overrides, message", [
        (dict(zoom=6), "between 1 and 4 for the whole globe"),
        (dict(radius=2_500.0, zoom=8), "between 1 and 7"),
        (dict(radius=50.0, zoom=None), "radius_km must be between"),
        (dict(radius=2_500.0, zoom=None, both_hemispheres=True), "both hemispheres"),
        (dict(rectangle=1.5), "cut from a radius map"),
        (dict(wind="monsoon"), "Unknown wind period"),
    ])
    def test_bad_radius_zoom_or_wind_exits_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                pytest.raises(SystemExit):
            ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_climate_means_passed_to_renderer(self):
        args = self._make_args(city="paris", precipitation="djf")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["temperature"], kwargs["precipitation"], kwargs["humidity"]) == (None, "djf", None)
        assert kwargs["koppen_alpha"] is None

    @pytest.mark.parametrize("overrides, message", [
        (dict(humidity="monsoon"), "Unknown humidity period"),
        (dict(temperature="year", koppen_class=["Cfb"]), "Choose one climate layer"),
    ])
    def test_bad_climate_means_exit_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                pytest.raises(SystemExit):
            ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_wind_passed_to_renderer(self):
        args = self._make_args(city="paris", wind="jja")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["wind"] == "jja"

    def test_up_passed_to_renderer_and_named(self):
        args = self._make_args(city="sydney", up=180.0)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["up"] == 180
        assert render.call_args.kwargs["output_filename"] == "orthographic_map_sydney_osm_z3_up180.png"

    def test_up_toward_becomes_a_bearing(self):
        args = self._make_args(city="london", up_toward="21.42,39.83")    # Mecca
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["up"] == pytest.approx(119, abs=1)

    @pytest.mark.parametrize("place, expected", [("London", (51.5074, -0.1278)), ("32.06,118.78", (32.06, 118.78))])
    def test_circles_from_a_place(self, place, expected):
        args = self._make_args(city="paris", circles_from=place)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["circles_from"] == expected

    def test_a_globe_beside_passed_to_renderer_and_named(self):
        args = self._make_args(city="lisbon", beside="Tokyo")
        with mock.patch.object(ortho, "configure_tile_cache"):
            plan = ortho.prepare_render(args)
        kwargs = plan.kwargs
        assert plan.label == "Lisbon and Tokyo"
        assert kwargs["city_name"] == "Lisbon" and kwargs["beside_name"] == "Tokyo"
        assert kwargs["beside"] == (35.6762, 139.6503) and kwargs["beside_up"] is None
        assert kwargs["output_filename"] == "orthographic_map_lisbon_and_tokyo_osm_z3.png"

    def test_a_globe_beside_at_coordinates_is_unnamed(self):
        with mock.patch.object(ortho, "configure_tile_cache"):
            plan = ortho.prepare_render(self._make_args(city="lisbon", beside="-33.9,18.4"))
        assert plan.kwargs["beside"] == (-33.9, 18.4) and plan.kwargs["beside_name"] is None
        assert plan.label == "Lisbon and (-33.9, 18.4)"
        assert plan.kwargs["output_filename"] == "orthographic_map_lisbon_and_custom_osm_z3.png"

    def test_up_toward_turns_each_globe_from_its_own_centre(self):
        args = self._make_args(city="lisbon", beside="tokyo", up_toward="new delhi")
        with mock.patch.object(ortho, "configure_tile_cache"):
            kwargs = ortho.prepare_render(args).kwargs
        assert kwargs["up"] == pytest.approx(ortho.initial_bearing(38.7223, -9.1393, 28.6139, 77.2090))
        assert kwargs["beside_up"] == pytest.approx(ortho.initial_bearing(35.6762, 139.6503, 28.6139, 77.2090))

    def test_radius_beside_takes_the_zoom_of_the_centre_nearer_the_equator(self):
        args = self._make_args(city="paris", beside="jakarta", radius=2_500.0, zoom=None)
        with mock.patch.object(ortho, "configure_tile_cache"):
            kwargs = ortho.prepare_render(args).kwargs
        assert kwargs["zoom"] == ortho.zoom_for_radius(2_500, -6.2088) == 7     # Paris alone: 6
        assert kwargs["output_filename"] == "orthographic_map_paris_and_jakarta_osm_z7_r2500km.png"

    @pytest.mark.parametrize("overrides, message", [
        (dict(beside="tokyo", both_hemispheres=True), "Choose --both-hemispheres or --beside, not both."),
        (dict(beside="Atlantis"), "--beside: 'Atlantis' is not a known city"),
        (dict(beside="tokyo", dpi=ortho.MAX_DPI), "with two globes"),
    ])
    def test_bad_globe_beside_exits_before_render(self, caplog, overrides, message):
        args = self._make_args(city="lisbon", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"), \
                pytest.raises(SystemExit):
            ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_sea_options_passed_to_renderer(self):
        with mock.patch.object(ortho, "configure_tile_cache"):
            kwargs = ortho.prepare_render(self._make_args(city="paris", temperature="july",
                                                          temperature_over_sea=True)).kwargs
            assert kwargs["temperature_over_sea"] is True and kwargs["sea_temperature"] is None
            kwargs = ortho.prepare_render(self._make_args(city="paris", sea_temperature="jja")).kwargs
            assert kwargs["temperature_over_sea"] is False and kwargs["sea_temperature"] == "jja"

    @pytest.mark.parametrize("overrides, message", [
        (dict(temperature_over_sea=True), "--temperature-over-sea needs --temperature."),
        (dict(temperature="year", temperature_over_sea=True, sea_temperature="year"),
         "Choose --temperature-over-sea or --sea-temperature, not both."),
        (dict(sea_temperature="monsoon"), "Unknown sea temperature period"),
    ])
    def test_bad_sea_options_exit_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"), \
                pytest.raises(SystemExit):
            ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_no_circles_reaches_the_renderer(self):
        with mock.patch.object(ortho, "configure_tile_cache"):
            assert ortho.prepare_render(self._make_args(city="paris")).kwargs["distance_circles"] is True
            plan = ortho.prepare_render(self._make_args(city="paris", no_circles=True))
        assert plan.kwargs["distance_circles"] is False

    def test_circles_from_the_centre_by_default(self):
        with mock.patch.object(ortho, "configure_tile_cache"):
            assert ortho.prepare_render(self._make_args(city="paris")).kwargs["circles_from"] is None

    def test_bad_circles_from_exits_before_render(self, caplog):
        args = self._make_args(city="paris", circles_from="Atlantis")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                pytest.raises(SystemExit):
            ortho.run_cli(args)
        render.assert_not_called()
        assert "--circles-from: 'Atlantis' is not a known city" in caplog.text

    @pytest.mark.parametrize("target", ["Atlantis", "london"])   # unknown, and the centre itself
    def test_bad_up_toward_exits_before_render(self, target):
        args = self._make_args(city="london", up_toward=target)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_koppen_classes_passed_to_renderer(self):
        args = self._make_args(city="paris", koppen_class=["Cfb", "Cs"])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["koppen_classes"] == ["Cfb", "Cs"]

    def test_unknown_koppen_class_exits_before_render(self, caplog):
        args = self._make_args(city="paris", koppen_class=["Cxx"])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert "Unknown Köppen-Geiger class" in caplog.text

    def test_trewartha_passed_to_renderer(self):
        args = self._make_args(city="paris", trewartha=True, trewartha_class=["Do"])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["trewartha"] is True
        assert render.call_args.kwargs["trewartha_classes"] == ["Do"]

    def test_soil_passed_to_renderer(self):
        args = self._make_args(city="paris", soil=True, soil_class=["CH"], soil_alpha=0.7)
        with mock.patch.object(ortho, "generate_orthographic_map") as render,                 mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["soil"] is True and kwargs["soil_classes"] == ["CH"] and kwargs["soil_alpha"] == 0.7

    @pytest.mark.parametrize("overrides", [{"soil_class": ["Xx"]}, {"soil": True, "soil_alpha": 2.0}])
    def test_bad_soil_options_exit_before_render(self, overrides):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_biomes_passed_to_renderer(self):
        args = self._make_args(city="paris", ecoregions=True, biome_class=["mangroves"], biome_alpha=0.5)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["ecoregions"] is True and kwargs["biomes"] is False
        assert kwargs["biome_classes"] == ["mangroves"] and kwargs["biome_alpha"] == 0.5

    @pytest.mark.parametrize("overrides", [{"biome_class": ["jungle"]}, {"biomes": True, "biome_alpha": 1.5},
                                           {"ecoregions": True, "realms": "wwf"},
                                           {"realms": "holt", "biome_class": ["forest"]},
                                           {"realms": "holt", "biomes": True}])
    def test_bad_biome_options_exit_before_render(self, overrides):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_borders_passed_to_renderer(self):
        args = self._make_args(city="paris", borders="1914-08-01", borders_source="cshapes", borders_alpha=0.4)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["borders"] == "1914-08-01" and kwargs["borders_source"] == "cshapes"
        assert kwargs["borders_alpha"] == 0.4

    @pytest.mark.parametrize("overrides, message", [
        ({"borders": "soon"}, "Not a year"),
        ({"borders": "1500", "borders_source": "cshapes"}, "CShapes 2.0 covers 1886 to 2019"),
        ({"borders": "1500", "borders_alpha": 1.5}, "borders_alpha"),
    ])
    def test_bad_borders_exit_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_atlas_layers_passed_to_renderer(self):
        args = self._make_args(city="paris", ocean_depth=True, plates=True, earthquakes=7.0, cyclones=1990,
                               population=2010, livestock="cow", basins=True, basins_alpha=0.3)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["ocean_depth"] and kwargs["plates"] and kwargs["basins"] and kwargs["basins_alpha"] == 0.3
        assert (kwargs["earthquakes"], kwargs["cyclones"], kwargs["population"], kwargs["livestock"]) == \
            (7.0, 1990, 2010, "cow")

    def test_malaria_and_aedes_passed_to_renderer(self):
        args = self._make_args(city="paris", malaria="vivax", malaria_year=2005, malaria_alpha=0.5, aedes="both")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["malaria"], kwargs["malaria_year"], kwargs["malaria_alpha"], kwargs["aedes"]) == \
            ("vivax", 2005, 0.5, "both")

    def test_species_passed_to_renderer(self, caplog):
        args = self._make_args(city="paris", species="red fox", species_years="1990-2020")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert (render.call_args.kwargs["species"], render.call_args.kwargs["species_years"]) == \
            ("red fox", "1990-2020")
        args = self._make_args(city="paris", species_years="1990-2020")
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--species-years is ignored" in caplog.text

    def test_indicator_passed_to_renderer(self, caplog):
        args = self._make_args(city="paris", indicator="gdp-per-person", indicator_year=2000, indicator_alpha=0.5)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["indicator"], kwargs["indicator_year"], kwargs["indicator_alpha"]) == ("gdp-per-person", 2000, 0.5)
        args = self._make_args(city="paris", indicator_year=2000)
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--indicator-year is ignored" in caplog.text

    def test_industry_passed_to_renderer(self):
        args = self._make_args(city="paris", industry="petrochemical")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["industry"] == "petrochemical"

    def test_shipping_and_air_routes_passed_to_renderer(self, caplog):
        args = self._make_args(city="paris", shipping="fishing", shipping_alpha=0.6, air_routes="LHR, airline:BA")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["shipping"], kwargs["shipping_alpha"], kwargs["air_routes"]) == ("fishing", 0.6,
                                                                                         "LHR, airline:BA")
        args = self._make_args(city="paris", shipping_alpha=0.6)
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--shipping-alpha is ignored" in caplog.text

    def test_ports_passed_to_renderer(self):
        args = self._make_args(city="paris", ports="tankers")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["ports"] == "tankers"

    def test_relief_units_passed_to_renderer(self):
        args = self._make_args(lat=44.4, lon=26.1, relief_units="regions", relief_alpha=0.4)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["relief_units"], kwargs["relief_alpha"]) == ("regions", 0.4)

    def test_relief_alpha_alone_is_ignored(self, caplog):
        args = self._make_args(lat=44.4, lon=26.1, relief_alpha=0.4)
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--relief-alpha is ignored" in caplog.text

    def test_ro_admin_passed_to_renderer(self):
        args = self._make_args(lat=46.75, lon=23.6, ro_admin="UATs", ro_admin_alpha=0.4)
        with mock.patch.object(ortho, "generate_orthographic_map") as render,                 mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["ro_admin"], kwargs["ro_admin_alpha"]) == ("UATs", 0.4)

    def test_language_passed_to_renderer(self):
        args = self._make_args(city="paris", language="ro")
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["language"] == "ro"
        assert ortho.build_cli_parser().parse_args(["--language", "ro"]).language == "ro"

    def test_text_scale_passed_to_renderer(self):
        args = self._make_args(city="paris", text_scale=2.0)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["text_scale"] == 2.0
        assert ortho.build_cli_parser().parse_args(["--text-scale", "2.5"]).text_scale == 2.5

    def test_ro_admin_alpha_alone_is_ignored(self, caplog):
        args = self._make_args(lat=46.75, lon=23.6, ro_admin_alpha=0.4)
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--ro-admin-alpha is ignored" in caplog.text

    def test_cli_parses_ro_admin(self):
        parse = ortho.build_cli_parser().parse_args
        assert (parse(["--ro-admin"]).ro_admin, parse(["--ro-admin"]).ro_admin_alpha) == ("counties", 0.0)
        assert parse(["--ro-admin", "uats", "--ro-admin-alpha", "0.3"]).ro_admin_alpha == 0.3
        assert parse(["--city", "paris"]).ro_admin is None

    def test_cli_parses_relief_units(self):
        parse = ortho.build_cli_parser().parse_args
        assert (parse(["--relief-units"]).relief_units, parse(["--relief-units"]).relief_alpha) == ("units", 0.55)
        assert parse(["--relief-units", "regions", "--relief-alpha", "0.3"]).relief_alpha == 0.3
        assert parse(["--city", "paris"]).relief_units is None

    def test_cli_parses_ports(self):
        assert ortho.build_cli_parser().parse_args(["--city", "paris", "--ports"]).ports == "all"
        assert ortho.build_cli_parser().parse_args(["--ports", "container"]).ports == "container"
        assert ortho.build_cli_parser().parse_args(["--city", "paris"]).ports is None

    def test_cli_parses_night_lights(self):
        assert ortho.build_cli_parser().parse_args(["--provider", "nasa_night"]).provider == "nasa_night"

    def test_cli_parses_shipping_and_air_routes(self):
        args = ortho.build_cli_parser().parse_args(["--city", "paris", "--shipping", "--air-routes", "all"])
        assert (args.shipping, args.shipping_alpha, args.air_routes) == ("commercial", 0.85, "all")
        assert ortho.build_cli_parser().parse_args(["--shipping", "passenger"]).shipping == "passenger"

    def test_cities_passed_to_renderer(self, caplog):
        args = self._make_args(city="paris", cities=1990, cities_min=200000)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert (render.call_args.kwargs["cities"], render.call_args.kwargs["cities_min"]) == (1990, 200000)
        args = self._make_args(city="paris", cities_min=200000)
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--cities-min is ignored" in caplog.text

    def test_iucn_passed_to_renderer(self, caplog):
        args = self._make_args(city="paris", iucn="lion", iucn_data="~/ranges", iucn_alpha=0.3)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert (kwargs["iucn"], kwargs["iucn_data"], kwargs["iucn_alpha"]) == ("lion", "~/ranges", 0.3)
        args = self._make_args(city="paris", iucn_data="~/ranges")
        with mock.patch.object(ortho, "generate_orthographic_map"), mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--iucn-data is ignored" in caplog.text

    @pytest.mark.parametrize("overrides, message", [
        ({"population": 2012}, "Population density is mapped for"),
        ({"livestock": "llamas"}, "Unknown livestock"),
        ({"malaria": "knowlesi"}, "Unknown malaria parasite"),
        ({"malaria": "pf", "malaria_year": 1999}, "Malaria is mapped for"),
        ({"malaria": "pf", "malaria_alpha": 1.5}, "malaria_alpha"),
        ({"aedes": "vexans"}, "Unknown Aedes"),
        ({"species": "lion", "species_years": "2020-1990"}, "Species years"),
        ({"iucn": "lion", "iucn_alpha": -0.1}, "iucn_alpha"),
        ({"cities": 1977}, "Cities are mapped for"),
        ({"indicator": "happiness"}, "Unknown indicator"),
        ({"industry": "smelting"}, "Unknown industry"),
        ({"shipping": "submarines"}, "Unknown kind of ships"),
        ({"ports": "yachts"}, "Unknown kind of port traffic"),
        ({"relief_units": "counties"}, "Unknown level of relief units"),
        ({"relief_units": "units", "relief_alpha": 1.5}, "relief_alpha"),
        ({"ro_admin": "villages"}, "Unknown level of Romania's administrative units"),
        ({"ro_admin": "counties", "ro_admin_alpha": -0.1}, "ro_admin_alpha"),
        ({"text_scale": 5.0}, "text_scale must be between 0.5 and 4"),
        ({"language": "fr"}, "language must be one of en, ro"),
        ({"shipping": "all", "shipping_alpha": 1.5}, "shipping_alpha"),
        ({"air_routes": "airline:"}, "Name an airline"),
        ({"indicator": "fertility", "indicator_year": 1900}, "Indicator years run from 1960"),
        ({"indicator": "fertility", "indicator_alpha": 2.0}, "indicator_alpha"),
        ({"earthquakes": 4.0}, "earthquakes must be a magnitude"),
        ({"cyclones": 1970}, "cyclones must start"),
        ({"basins": True, "basins_alpha": 2.0}, "basins_alpha"),
    ])
    def test_bad_atlas_options_exit_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_soil_property_passed_to_renderer(self):
        args = self._make_args(city="paris", soil_property="clay", soil_depth="15-30cm")
        with mock.patch.object(ortho, "generate_orthographic_map") as render,                 mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["soil_property"] == "clay" and kwargs["soil_depth"] == "15-30cm"

    @pytest.mark.parametrize("overrides, message", [
        ({"soil": True, "soil_property": "ph"}, "Choose one soil layer"),
        ({"soil_depth": "5-15cm"}, "--soil-depth needs --soil-property"),
        ({"soil_property": "carbon-stock", "soil_depth": "0-5cm"}, "mapped at 0-30cm"),
    ])
    def test_bad_soil_property_options_exit_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_vegetation_passed_to_renderer(self):
        args = self._make_args(city="paris", landcover_class=["forest"], landcover_year=2010,
                               vegetation_alpha=0.5)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        kwargs = render.call_args.kwargs
        assert kwargs["land_cover_classes"] == ["forest"] and kwargs["land_cover_year"] == 2010
        assert kwargs["vegetation_alpha"] == 0.5 and kwargs["ndvi"] is None

    @pytest.mark.parametrize("overrides, message", [
        ({"landcover": True, "ndvi": "2020-07"}, "Choose one vegetation layer"),
        ({"landcover_class": ["jungle"]}, "Unknown land cover class"),
        ({"ndvi": "summer"}, "Unknown NDVI month"),
        ({"landcover_year": 1999}, "land_cover_year must be between"),
        ({"ndvi": "2020-07", "vegetation_alpha": 1.5}, "vegetation_alpha"),
    ])
    def test_bad_vegetation_options_exit_before_render(self, caplog, overrides, message):
        args = self._make_args(city="paris", **overrides)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_elevation_passed_to_renderer(self):
        args = self._make_args(city="paris", elevation=True, elevation_alpha=0.6)
        with mock.patch.object(ortho, "generate_orthographic_map") as render,                 mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["elevation"] is True
        assert render.call_args.kwargs["elevation_alpha"] == 0.6

    def test_bad_elevation_alpha_exits_before_render(self):
        args = self._make_args(city="paris", elevation=True, elevation_alpha=3.0)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    @pytest.mark.parametrize("kwargs, message", [
        ({"trewartha_class": ["Xx"]}, "Unknown Trewartha class"),
        ({"koppen_class": ["Cfb"], "trewartha": True}, "Choose one climate classification"),
    ])
    def test_bad_trewartha_options_exit_before_render(self, caplog, kwargs, message):
        args = self._make_args(city="paris", **kwargs)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert message in caplog.text

    def test_crops_passed_to_renderer(self):
        args = self._make_args(city="paris", crop=["wheat", "rice:#00ffff"])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert render.call_args.kwargs["crops"] == ["wheat", "rice:#00ffff"]

    def test_unknown_crop_exits_before_render(self, caplog):
        args = self._make_args(city="paris", crop=["whaet"])
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()
        assert "Did you mean wheat" in caplog.text

    def test_both_hemispheres_dpi_over_cap_exits_before_render(self):
        args = self._make_args(city="lisbon", both_hemispheres=True, dpi=ortho.MAX_DPI)
        with mock.patch.object(ortho, "generate_orthographic_map") as render, \
                mock.patch.object(ortho, "configure_tile_cache"):
            with pytest.raises(SystemExit):
                ortho.run_cli(args)
        render.assert_not_called()

    def test_route_legend_without_routes_warns(self, caplog):
        args = self._make_args(city="paris", route_legend=True)
        with mock.patch.object(ortho, "generate_orthographic_map"), \
                mock.patch.object(ortho, "configure_tile_cache"):
            ortho.run_cli(args)
        assert "--route-legend is ignored" in caplog.text


# ===================================================================
# Integration: full render pipeline (offline, mocked tiles)
# ===================================================================


class TestGenerateOrthographicMapIntegration:
    """End-to-end render with tile fetching stubbed out.

    This confirms Matplotlib + Cartopy produce a valid PNG without
    hitting the network.  Uses the lowest practical settings to keep
    the test fast (~2-3 s).
    """

    def test_renders_valid_png(self, tmp_path):
        """generate_orthographic_map should produce a non-empty PNG file."""
        output_file = "test_map.png"

        # Stub add_image so no network access occurs
        with mock.patch.object(
            ortho.GeoAxes, "add_image", return_value=None,
        ):
            result = ortho.generate_orthographic_map(
                lat=48.8566,
                lon=2.3522,
                output_filename=output_file,
                tile_provider="osm",
                zoom=1,
                dpi=50,
                output_dir=str(tmp_path),
            )

        out_path = tmp_path / output_file
        assert out_path.exists(), "Output PNG was not created"
        assert out_path.stat().st_size > 0, "Output PNG is empty"

        # Verify the return value is the absolute path
        assert os.path.isabs(result)
        assert result == str(out_path.resolve())

        # Verify it's a valid PNG (starts with the PNG magic bytes)
        with open(out_path, "rb") as f:
            header = f.read(8)
        assert header[:4] == b"\x89PNG", "File does not have a valid PNG header"

    def _render_credits(self, tmp_path, **kwargs):
        """Render offline and return the credits passed to _add_attribution."""
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay"), \
                mock.patch.object(ortho, "add_koppen_legend"), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), **kwargs,
            )
        attribution.assert_called_once()
        return attribution.call_args.args[1]

    def test_osm_is_credited(self, tmp_path):
        credits = self._render_credits(tmp_path, tile_provider="osm")
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"]]

    def test_night_lights_credited(self, tmp_path):
        credits = self._render_credits(tmp_path, tile_provider="nasa_night")
        assert credits == ["Imagery: NASA Black Marble 2016 (VIIRS night lights), via NASA GIBS (ESDIS)"]

    def test_nasa_is_credited(self, tmp_path):
        credits = self._render_credits(tmp_path, tile_provider="nasa")
        assert credits == ["Imagery: NASA Blue Marble, via NASA GIBS (ESDIS)"]

    @pytest.mark.parametrize("provider", ["google", "google_satellite"])
    def test_google_credit_comes_from_viewport(self, tmp_path, provider):
        with mock.patch("google_tiles.create_session", return_value="S"), \
                mock.patch.object(ortho.GoogleMapTiles, "copyright",
                                  return_value="Map data ©2026 Google") as copyright_:
            credits = self._render_credits(
                tmp_path, tile_provider=provider, tile_kwargs={"api_key": "k"},
            )
        assert credits == ["Google Maps", "Map data ©2026 Google"]
        copyright_.assert_called_once_with(1)  # the render's zoom level

    def test_koppen_is_credited_when_enabled(self, tmp_path):
        credits = self._render_credits(tmp_path, koppen=True)
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.KOPPEN_ATTRIBUTION]
        assert "Beck et al. (2018)" in ortho.KOPPEN_ATTRIBUTION

    def test_koppen_failure_still_saves_map_without_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay",
                                  side_effect=ortho.KoppenDataError("no data")), \
                mock.patch.object(ortho, "add_koppen_legend") as legend, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), koppen=True,
            )
        assert os.path.exists(result)
        legend.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    def test_trewartha_is_credited_when_enabled(self, tmp_path):
        with mock.patch.object(ortho, "add_trewartha_overlay"), \
                mock.patch.object(ortho, "add_trewartha_legend"):
            credits = self._render_credits(tmp_path, trewartha=True)
        # The highland group uses the terrain tiles, so their sources are credited too
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.TREWARTHA_ATTRIBUTION,
                           ortho.ELEVATION_ATTRIBUTION]
        assert "CHELSA" in ortho.TREWARTHA_ATTRIBUTION

    def test_soil_classes_imply_the_overlay_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "add_soil_overlay") as overlay,                 mock.patch.object(ortho, "add_soil_legend") as key,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), soil_classes=["Chernozems", "KS"], soil_alpha=0.7,
            )
        codes = ortho.resolve_soil_classes(["Chernozems", "KS"])
        assert overlay.call_args.kwargs["classes"] == codes and overlay.call_args.kwargs["alpha"] == 0.7
        assert key.call_args.kwargs["classes"] == codes
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.SOIL_ATTRIBUTION]

    @pytest.mark.parametrize("by", ["biome", "ecoregion", "realm"])
    def test_biome_classes_imply_the_overlay_key_and_credit(self, tmp_path, by):
        layer = object()
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ecoregions", return_value=layer), \
                mock.patch.object(ortho, "add_ecoregion_overlay") as overlay, \
                mock.patch.object(ortho, "visible_ecoregions", return_value="visible") as visible, \
                mock.patch.object(ortho, "add_biome_legend") as biome_key, \
                mock.patch.object(ortho, "add_ecoregion_legend") as ecoregion_key, \
                mock.patch.object(ortho, "add_realm_legend") as realm_key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
                biome_classes=["temperate"], ecoregions=by == "ecoregion", realms=by == "realm",
                biome_alpha=0.5, beside=(35.7, 139.7),
            )
        all_keys = {"biome": biome_key, "ecoregion": ecoregion_key, "realm": realm_key}
        codes = ortho.resolve_biome_classes(["temperate"])
        assert overlay.call_count == 2                                   # one per globe
        assert overlay.call_args.args[1] is layer
        assert overlay.call_args.kwargs == dict(alpha=0.5, regrid_shape=mock.ANY, classes=codes, by=by)
        assert len(visible.call_args.args[0]) == 2                      # the key counts both globes
        key = all_keys.pop(by)
        assert key.call_args.args[1:] == ("visible", layer) and key.call_args.kwargs["classes"] == codes
        for unused in all_keys.values():
            unused.assert_not_called()
        credit = {"biome": ortho.BIOME_ATTRIBUTION, "ecoregion": ortho.ECOREGION_ATTRIBUTION,
                  "realm": ortho.REALM_ATTRIBUTION}[by]
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], credit]

    @pytest.mark.parametrize("realms, regions", [("holt", False), ("holt", True), (None, True)])
    def test_holt_realms_and_regions_key_and_credit(self, tmp_path, realms, regions):
        holt = mock.Mock(realms="grid")
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_holt", return_value=holt), \
                mock.patch.object(ortho, "load_ecoregions") as resolve, \
                mock.patch.object(ortho, "add_holt_realm_overlay") as overlay, \
                mock.patch.object(ortho, "draw_holt_regions") as outlines, \
                mock.patch.object(ortho, "visible_values", return_value="visible") as visible, \
                mock.patch.object(ortho, "add_holt_realm_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=31, lon=121, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
                realms=realms, realm_regions=regions, biome_alpha=0.5,
            )
        resolve.assert_not_called()                                   # RESOLVE is not needed
        assert overlay.call_count == (realms == "holt")
        if realms:
            assert overlay.call_args.kwargs["alpha"] == 0.5
            assert visible.call_args.args[1] == "grid"
            assert key.call_args.args[1] == "visible"
        else:
            key.assert_not_called()
        assert outlines.call_count == regions
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.HOLT_ATTRIBUTION]

    def test_holt_failure_still_saves_map_without_key_or_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_holt", side_effect=ortho.HoltDataError("offline")), \
                mock.patch.object(ortho, "add_holt_realm_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
                realms="holt", realm_regions=True,
            )
        assert os.path.exists(result)
        key.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    @pytest.mark.parametrize("when, source, chosen", [("1500", "auto", "cliopatria"),
                                                      ("1914-08-01", "auto", "cshapes")])
    def test_borders_drawn_with_key_and_credit(self, tmp_path, when, source, chosen):
        polities = [mock.Mock()]
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_polities", return_value=polities) as load, \
                mock.patch.object(ortho, "colour_groups", return_value={"g": (1, 2, 3)}), \
                mock.patch.object(ortho, "draw_borders", return_value={0: 5}) as draw, \
                mock.patch.object(ortho, "add_borders_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
                borders=when, borders_source=source, borders_alpha=0.4, beside=(35.7, 139.7),
            )
        assert load.call_args.args == (ortho.parse_when(when), chosen)
        assert draw.call_count == 2 and draw.call_args.kwargs["alpha"] == 0.4      # one per globe
        assert key.call_args.args[1] == polities and key.call_args.args[2] == [{0: 5}, {0: 5}]
        assert key.call_args.args[4:] == (ortho.parse_when(when), chosen)
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.borders_attribution(chosen)]

    def test_borders_failure_still_saves_map_without_key_or_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_polities", side_effect=ortho.BordersError("offline")), \
                mock.patch.object(ortho, "add_borders_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path), borders=1500,
            )
        assert os.path.exists(result)
        key.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    @pytest.mark.parametrize("option, loader, drawer, key, credit", [
        ({"currents": True}, "load_currents", "add_currents_overlay", "add_currents_legend", "CURRENTS_ATTRIBUTION"),
        ({"cyclones": 1980}, "load_tracks", "draw_tracks", "add_cyclones_legend", "CYCLONES_ATTRIBUTION"),
        ({"permafrost": True}, "load_permafrost", "add_permafrost_overlay", "add_permafrost_legend",
         "PERMAFROST_ATTRIBUTION"),
        ({"livestock": "sheep"}, "load_livestock", "add_livestock_overlay", "add_livestock_legend",
         "LIVESTOCK_ATTRIBUTION"),
        ({"malaria": "pv", "malaria_year": 2010}, "load_malaria", "add_malaria_overlay", "add_malaria_legend",
         "MALARIA_ATTRIBUTION"),
        ({"aedes": "aegypti"}, "load_aedes", "draw_aedes", "add_aedes_legend", "AEDES_ATTRIBUTION"),
        ({"time_zones": True}, "load_time_zones", "draw_time_zones", None, "TIMEZONES_ATTRIBUTION"),
        ({"rivers": True}, "load_rivers", "draw_rivers", None, "RIVERS_ATTRIBUTION"),
    ])
    def test_atlas_layer_drawn_with_key_and_credit(self, tmp_path, option, loader, drawer, key, credit):
        patches = [mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),
                   mock.patch.object(ortho, loader, return_value=mock.Mock()),
                   mock.patch.object(ortho, drawer),
                   mock.patch.object(ortho, "_add_attribution")]
        if key:
            patches.append(mock.patch.object(ortho, key))
        with patches[0], patches[1] as load, patches[2] as draw, patches[3] as attribution, \
                (patches[4] if key else mock.patch.object(ortho, "logger")) as legend:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), **option)
        load.assert_called_once()
        assert draw.call_count == 2                                  # one per globe
        if key:
            legend.assert_called_once()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], getattr(ortho, credit)]

    def test_species_records_drawn_with_key_and_credit(self, tmp_path):
        taxon = mock.Mock()
        found = mock.Mock(records=[3, 4], retrieved="2026-10-18")
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "resolve_taxon", return_value=taxon) as resolve, \
                mock.patch.object(ortho, "load_occurrences", return_value=found) as load, \
                mock.patch.object(ortho, "draw_occurrences") as draw, \
                mock.patch.object(ortho, "add_occurrences_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), species="lion",
                                            species_years="2000-2010")
        assert resolve.call_args.args == ("lion",) and load.call_args.args == (taxon, (2000, 2010))
        assert draw.call_count == 2                                  # one per globe
        key.assert_called_once()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"],
                                                 "Species records: GBIF.org occurrence data, retrieved 2026-10-18"]

    @pytest.mark.parametrize("resolved, loaded, warning", [
        ({"side_effect": ortho.GbifDataError("offline")}, {}, "Skipping the species records"),
        ({"return_value": mock.Mock()}, {"side_effect": ortho.GbifDataError("busy")}, "Skipping the records"),
        ({"return_value": mock.Mock()}, {"return_value": mock.Mock(records=[])}, "no wild records"),
    ])
    def test_species_records_missing_still_saves_map(self, tmp_path, caplog, resolved, loaded, warning):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "resolve_taxon", **resolved), \
                mock.patch.object(ortho, "load_occurrences", **loaded), \
                mock.patch.object(ortho, "add_occurrences_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), species="lion")
        assert os.path.exists(result) and warning in caplog.text
        key.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    def test_iucn_range_drawn_with_key_and_credit(self, tmp_path):
        found = mock.Mock(credit="Range: IUCN (2026), The IUCN Red List of Threatened Species, spatial data of 2026-06-26")
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_range", return_value=found) as load, \
                mock.patch.object(ortho, "draw_range") as draw, \
                mock.patch.object(ortho, "add_range_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), iucn="lion",
                                            iucn_data="ranges", iucn_alpha=0.3)
        assert load.call_args.args == ("lion", "ranges")
        assert draw.call_count == 2 and draw.call_args.kwargs["alpha"] == 0.3        # one per globe
        key.assert_called_once()
        assert attribution.call_args.args[1][1].startswith("Range: IUCN (2026), The IUCN Red List")

    def test_iucn_range_unreadable_still_saves_map(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_range", side_effect=ortho.IucnDataError("bad zip")), \
                mock.patch.object(ortho, "add_range_legend") as key:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), iucn="lion")
        assert os.path.exists(result) and "Skipping the IUCN range: bad zip" in caplog.text
        key.assert_not_called()

    @pytest.mark.parametrize("radius, floor", [(None, 1_000_000), (800, 50_000), (2500, 250_000)])
    def test_cities_drawn_with_key_and_credit(self, tmp_path, radius, floor):
        towns = mock.Mock()
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_cities", return_value=towns) as load, \
                mock.patch.object(ortho, "draw_cities", return_value=[1, 2, 3]) as draw, \
                mock.patch.object(ortho, "add_cities_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=50, lon=5, output_filename="m.png", zoom=1 if radius is None else 6,
                                            dpi=20, output_dir=str(tmp_path), cities=1975, radius_km=radius)
        assert load.call_args.args == (1975,) and draw.call_args.args[1:] == (towns, floor)
        assert key.call_args.args[1:] == (towns, floor, 3)
        assert attribution.call_args.args[1][-1] == ortho.CITIES_ATTRIBUTION

    def test_indicator_drawn_with_key_and_credit(self, tmp_path):
        values = mock.Mock(values={"FRA": 1.0, "DEU": 2.0}, indicator=mock.Mock(log=False))
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_values", return_value=values) as load, \
                mock.patch.object(ortho, "load_areas", return_value=["countries"]), \
                mock.patch.object(ortho, "draw_indicator", return_value=Counter({0: 2})) as draw, \
                mock.patch.object(ortho, "add_indicator_legend") as key, \
                mock.patch.object(ortho, "indicator_attribution", return_value="Statistics: World Bank") as credit, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), indicator="fertility",
                                            indicator_year=2010, indicator_alpha=0.4)
        assert load.call_args.args[0].code == "SP.DYN.TFRT.IN" and load.call_args.args[1] == 2010
        assert draw.call_count == 2 and draw.call_args.args[1:3] == (values, ["countries"])
        assert draw.call_args.kwargs["alpha"] == 0.4
        key.assert_called_once()
        credit.assert_called_once_with(values)
        assert "Statistics: World Bank" in attribution.call_args.args[1]

    def test_industry_drawn_with_key_and_credit(self, tmp_path, caplog):
        refineries = mock.Mock(retrieved="2026-10-20")

        def load(sector):
            if sector == "chemicals":
                raise ortho.IndustryDataError("busy")
            return refineries

        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_plants", side_effect=load) as loaded, \
                mock.patch.object(ortho, "draw_industry", return_value={"refineries": 3}) as draw, \
                mock.patch.object(ortho, "add_industry_legend") as key, \
                mock.patch.object(ortho, "industry_attribution", return_value="Industry: Climate TRACE") as credit, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), industry="petrochemical")
        assert [c.args[0] for c in loaded.call_args_list] == ["refineries", "petrochemicals", "chemicals"]
        assert "Skipping the chemical plants (ammonia, methanol, soda ash): busy" in caplog.text
        assert draw.call_count == 2 and draw.call_args.args[1] == [refineries, refineries]
        assert key.call_args.args[1:] == ([refineries, refineries], {"refineries": 3})
        credit.assert_called_once()
        assert "Industry: Climate TRACE" in attribution.call_args.args[1]

    def test_shipping_drawn_with_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_shipping_overlay") as overlay, \
                mock.patch.object(ortho, "add_shipping_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), shipping="oil and gas",
                                            shipping_alpha=0.5)
        assert overlay.call_count == 2 and overlay.call_args.args[1] == "oil-gas"
        assert overlay.call_args.kwargs["alpha"] == 0.5
        assert key.call_args.args[1] == "oil-gas" and key.call_args.kwargs["alpha"] == 0.5
        assert ortho.SHIPPING_ATTRIBUTION in attribution.call_args.args[1]

    def test_shipping_unavailable_draws_nothing(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_shipping_overlay", side_effect=ortho.ShippingDataError("offline")), \
                mock.patch.object(ortho, "add_shipping_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), shipping="commercial")
        assert os.path.exists(result) and "Skipping the ship traffic: offline" in caplog.text
        key.assert_not_called()
        assert ortho.SHIPPING_ATTRIBUTION not in attribution.call_args.args[1]

    def test_ports_drawn_with_key_and_credit(self, tmp_path):
        harbours = mock.Mock()
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ports", return_value=harbours) as loaded, \
                mock.patch.object(ortho, "draw_ports", side_effect=[4, 6]) as draw, \
                mock.patch.object(ortho, "add_ports_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), ports="Containers")
        assert loaded.call_args.args[0] == "container"
        assert draw.call_count == 2
        assert key.call_args.args[1:] == (harbours, 6)
        assert ortho.PORTS_ATTRIBUTION in attribution.call_args.args[1]

    def test_ports_unavailable_draws_nothing(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ports", side_effect=ortho.PortsDataError("offline")), \
                mock.patch.object(ortho, "draw_ports") as draw, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), ports="all")
        assert os.path.exists(result) and "Skipping the ports: offline" in caplog.text
        draw.assert_not_called()
        assert ortho.PORTS_ATTRIBUTION not in attribution.call_args.args[1]

    def test_relief_units_drawn_with_key_and_credit(self, tmp_path):
        landforms = mock.Mock()
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_relief", return_value=landforms) as loaded, \
                mock.patch.object(ortho, "draw_relief", side_effect=[{0: 4}, {1: 6}]) as draw, \
                mock.patch.object(ortho, "add_relief_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(45, 26), relief_units="Regions",
                                            relief_alpha=0.3)
        assert loaded.call_args.args[0] == "regions"
        assert draw.call_count == 2 and draw.call_args.kwargs["alpha"] == 0.3
        assert key.call_args.args[1:] == (landforms, [{0: 4}, {1: 6}])
        assert ortho.RELIEF_ATTRIBUTION in attribution.call_args.args[1]

    def test_relief_units_unavailable_draws_nothing(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_relief", side_effect=ortho.ReliefError("offline")), \
                mock.patch.object(ortho, "draw_relief") as draw, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), relief_units="units")
        assert os.path.exists(result) and "Skipping the relief units: offline" in caplog.text
        draw.assert_not_called()
        assert ortho.RELIEF_ATTRIBUTION not in attribution.call_args.args[1]

    def test_ro_admin_drawn_with_key_and_credit(self, tmp_path):
        divisions = mock.Mock()
        shown = [mock.Mock(), mock.Mock()]
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "load_admin", return_value=divisions) as loaded,                 mock.patch.object(ortho, "draw_admin", side_effect=shown) as draw,                 mock.patch.object(ortho, "add_admin_legend") as key,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(45, 26), ro_admin="Județe",
                                            ro_admin_alpha=0.3)
        assert loaded.call_args.args[0] == "counties"
        assert draw.call_count == 2 and draw.call_args.kwargs["alpha"] == 0.3
        assert key.call_args.args[1:] == (divisions, shown) and key.call_args.kwargs["alpha"] == 0.3
        assert ortho.ADMIN_ATTRIBUTION in attribution.call_args.args[1]

    def test_osm_local_drawn_from_data_and_named_last(self, tmp_path):
        base = mock.Mock(date="22 Oct 2026")
        order = []
        with mock.patch.object(ortho.GeoAxes, "add_image") as tiles, \
                mock.patch.object(ortho.osm_local, "load_base", return_value=base), \
                mock.patch.object(ortho.osm_local, "draw_base", side_effect=lambda *a, **k: order.append("base")), \
                mock.patch.object(ortho.osm_local, "name_base", side_effect=lambda *a, **k: order.append("names")), \
                mock.patch.object(ortho, "load_relief", return_value=mock.Mock()), \
                mock.patch.object(ortho, "draw_relief", side_effect=lambda *a, **k: order.append("relief") or {}), \
                mock.patch.object(ortho, "add_relief_legend"), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), tile_provider="osm_local",
                                            relief_units="units")
        tiles.assert_not_called()
        assert order == ["base", "relief", "names"]
        assert attribution.call_args.args[1][0] == ortho.osm_local.attribution(base)       # in place of the tiles

    @pytest.mark.parametrize("borders, places", [(None, True), ("1914-07-01", False), ("2019-06-01", True)])
    def test_natural_earth_in_the_maps_language(self, tmp_path, borders, places):
        base = mock.Mock(credit="Map data: Natural Earth (public domain)")
        with mock.patch.object(ortho.GeoAxes, "add_image") as tiles, \
                mock.patch.object(ortho.ne_local, "load_base", return_value=base) as load, \
                mock.patch.object(ortho.osm_local, "load_base") as osm, \
                mock.patch.object(ortho.osm_local, "draw_base") as draw, \
                mock.patch.object(ortho.osm_local, "name_base") as name, \
                mock.patch.object(ortho, "load_polities", return_value=[]), \
                mock.patch.object(ortho, "in_language", return_value=[]), \
                mock.patch.object(ortho, "colour_groups", return_value={}), \
                mock.patch.object(ortho, "draw_borders", return_value={}), \
                mock.patch.object(ortho, "add_borders_legend"), \
                mock.patch.object(ortho, "_add_attribution"):
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), tile_provider="natural_earth", language="ro",
                                            borders=borders)
        tiles.assert_not_called()
        osm.assert_not_called()
        assert load.call_args.args == ("ro",)
        # A date's borders are drawn and its states named by the borders layer, not today's by the base
        assert draw.call_args.kwargs == {"borders": borders is None, "reservoirs": places}
        # and the cities of today only beside the borders of today
        assert name.call_args.kwargs == {"countries": borders is None, "places": places}

    @pytest.mark.parametrize("scale, beside", [(1.0, None), (2.0, None), (2.0, (45, 26))])
    def test_text_scale_draws_a_smaller_figure_at_a_higher_dpi(self, tmp_path, scale, beside):
        saved = []

        def savefig(fig, *args, **kwargs):
            saved.append((tuple(fig.get_size_inches()), kwargs["dpi"],
                          [ax.get_position().bounds for ax in fig.axes]))
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho.Figure, "savefig", savefig):
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), text_scale=scale, beside=beside)
        (size, dpi, boxes), = saved
        inches = 20 / scale
        assert size == pytest.approx((inches * (2 if beside else 1), inches)) and dpi == 20 * scale
        if beside:                       # the globes laid out alike, whatever the figure's size
            assert boxes[0] == pytest.approx((0.1025, 0.11, 0.385, 0.77))     # as at 20 in

    def test_borders_named_in_romanian_with_credit(self, tmp_path):
        polities = [mock.Mock()]
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_polities", return_value=polities), \
                mock.patch.object(ortho, "in_language", return_value=["ro"]) as translate, \
                mock.patch.object(ortho, "colour_groups", return_value={}), \
                mock.patch.object(ortho, "draw_borders", return_value={}) as draw, \
                mock.patch.object(ortho, "add_borders_legend"), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), borders="2019-06-01", language="ro")
        assert translate.call_args.args[2:] == ("ro", "cshapes") and draw.call_args.args[1] == ["ro"]
        assert any(ortho.CLDR_ATTRIBUTION in line for line in attribution.call_args.args[1])

    def test_borders_keep_english_names_without_cldr(self, tmp_path, caplog):
        polities = [mock.Mock()]
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_polities", return_value=polities), \
                mock.patch.object(ortho, "in_language", side_effect=ortho.BordersError("offline")), \
                mock.patch.object(ortho, "colour_groups", return_value={}), \
                mock.patch.object(ortho, "draw_borders", return_value={}) as draw, \
                mock.patch.object(ortho, "add_borders_legend"), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), borders="2019-06-01", language="ro")
        assert draw.call_args.args[1] is polities and "keep their English names: offline" in caplog.text
        assert not any(ortho.CLDR_ATTRIBUTION in line for line in attribution.call_args.args[1])

    def test_osm_local_unavailable_stops(self, tmp_path):
        with mock.patch.object(ortho.osm_local, "load_base", side_effect=ortho.OSMDataError("offline")):
            with pytest.raises(ortho.OSMDataError, match="offline"):
                ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                                output_dir=str(tmp_path), tile_provider="osm_local")
        assert not os.listdir(tmp_path)

    def test_osm_local_has_no_tiles(self):
        with pytest.raises(ValueError, match="drawn from data, not tiles"):
            ortho.create_tile_source("osm_local")
        assert ortho.tile_attribution_lines(None, "osm_local", 9) == []

    def test_ro_admin_unavailable_draws_nothing(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "load_admin", side_effect=ortho.AdminError("offline")),                 mock.patch.object(ortho, "draw_admin") as draw,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), ro_admin="uats")
        assert os.path.exists(result) and "Skipping Romania's counties and UATs: offline" in caplog.text
        draw.assert_not_called()
        assert ortho.ADMIN_ATTRIBUTION not in attribution.call_args.args[1]

    def test_cities_named_before_counties_and_counties_before_relief(self, tmp_path):
        order = []
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "load_relief", return_value=mock.Mock()),                 mock.patch.object(ortho, "load_admin", return_value=mock.Mock()),                 mock.patch.object(ortho, "load_cities", return_value=mock.Mock()),                 mock.patch.object(ortho, "draw_relief", side_effect=lambda *a, **k: order.append("relief") or {}),                 mock.patch.object(ortho, "draw_admin", side_effect=lambda *a, **k: order.append("admin") or None),                 mock.patch.object(ortho, "draw_cities", side_effect=lambda *a: order.append("cities") or []),                 mock.patch.object(ortho, "add_relief_legend"), mock.patch.object(ortho, "add_admin_legend"),                 mock.patch.object(ortho, "add_cities_legend"):
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), relief_units="units", ro_admin="uats",
                                            cities=2025)
        assert order == ["cities", "admin", "relief"]

    def test_cities_named_before_relief_units(self, tmp_path):
        order = []
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_relief", return_value=mock.Mock()), \
                mock.patch.object(ortho, "load_cities", return_value=mock.Mock()), \
                mock.patch.object(ortho, "draw_relief", side_effect=lambda *a, **k: order.append("relief") or {}), \
                mock.patch.object(ortho, "draw_cities", side_effect=lambda *a: order.append("cities") or []), \
                mock.patch.object(ortho, "add_relief_legend"), mock.patch.object(ortho, "add_cities_legend"):
            ortho.generate_orthographic_map(lat=46, lon=25, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), relief_units="units", cities=2025)
        assert order == ["cities", "relief"]

    def test_cities_named_before_ports(self, tmp_path):
        order = []
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ports", return_value=mock.Mock()), \
                mock.patch.object(ortho, "load_cities", return_value=mock.Mock()), \
                mock.patch.object(ortho, "draw_ports", side_effect=lambda *a: order.append("ports") or 0), \
                mock.patch.object(ortho, "draw_cities", side_effect=lambda *a: order.append("cities") or []), \
                mock.patch.object(ortho, "add_ports_legend"), mock.patch.object(ortho, "add_cities_legend"):
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), ports="all", cities=2025)
        assert order == ["cities", "ports"]

    def test_night_lights_fall_back_on_a_dark_globe(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho.GeoAxes, "add_feature") as features:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), tile_provider="nasa_night")
        colours = [c.kwargs.get("facecolor") for c in features.call_args_list[:2]]
        assert colours == list(ortho.FALLBACK_COLOURS["nasa_night"])

    def test_air_routes_drawn_with_key_and_credit(self, tmp_path):
        flights = mock.Mock()
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_air_routes", return_value=flights) as loaded, \
                mock.patch.object(ortho, "draw_air_routes", side_effect=[7, 9]) as draw, \
                mock.patch.object(ortho, "add_air_routes_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), beside=(10, 10), air_routes="LHR")
        assert loaded.call_args.args[0] == ortho.resolve_air_routes("LHR")
        assert draw.call_count == 2
        assert key.call_args.args[1:] == (flights, 9)
        assert ortho.AIR_ROUTES_ATTRIBUTION in attribution.call_args.args[1]

    def test_unknown_airport_stops_before_tiles(self, tmp_path):
        with mock.patch.object(ortho, "load_air_routes", side_effect=ValueError("No airport 'ZZZ'")), \
                mock.patch.object(ortho.GeoAxes, "add_image") as tiles:
            with pytest.raises(ValueError, match="No airport"):
                ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                output_dir=str(tmp_path), air_routes="ZZZ")
        tiles.assert_not_called()

    def test_air_routes_unavailable_draws_nothing(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_air_routes", side_effect=ortho.AirRoutesDataError("offline")), \
                mock.patch.object(ortho, "draw_air_routes") as draw:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), air_routes="all")
        assert os.path.exists(result) and "Skipping the air routes: offline" in caplog.text
        draw.assert_not_called()

    def test_industry_unavailable_draws_nothing(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_plants", side_effect=ortho.IndustryDataError("offline")), \
                mock.patch.object(ortho, "draw_industry") as draw, \
                mock.patch.object(ortho, "add_industry_legend") as key:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), industry="steel")
        assert os.path.exists(result)
        draw.assert_not_called()
        key.assert_not_called()

    @pytest.mark.parametrize("failure", ["values", "countries"])
    def test_indicator_unavailable_still_saves_map(self, tmp_path, caplog, failure):
        values = mock.Mock(values={"FRA": 1.0}, indicator=mock.Mock(log=False))
        loaded = {"side_effect": ortho.IndicatorDataError("offline")} if failure == "values" else {"return_value": values}
        countries = {"side_effect": OSError("no Natural Earth")} if failure == "countries" else {"return_value": []}
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_values", **loaded), \
                mock.patch.object(ortho, "load_areas", **countries), \
                mock.patch.object(ortho, "add_indicator_legend") as key:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), indicator="fertility")
        assert os.path.exists(result) and "Skipping the statistics" in caplog.text
        key.assert_not_called()

    def test_cities_unavailable_still_saves_map(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_cities", side_effect=ortho.CitiesDataError("offline")), \
                mock.patch.object(ortho, "add_cities_legend") as key:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), cities=2025, cities_min=10)
        assert os.path.exists(result) and "Skipping the cities: offline" in caplog.text
        key.assert_not_called()

    def test_iucn_unknown_or_missing_stops_before_tiles(self, tmp_path):
        with mock.patch.object(ortho, "load_range", side_effect=ValueError("No IUCN range files in x")), \
                mock.patch.object(ortho.GeoAxes, "add_image") as tiles:
            with pytest.raises(ValueError, match="No IUCN range files"):
                ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                output_dir=str(tmp_path), iucn="lion")
        tiles.assert_not_called()

    def test_unknown_species_stops_before_tiles(self, tmp_path):
        with mock.patch.object(ortho, "resolve_taxon", side_effect=ValueError("GBIF knows no species called 'x'")), \
                mock.patch.object(ortho.GeoAxes, "add_image") as tiles:
            with pytest.raises(ValueError, match="no species"):
                ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                output_dir=str(tmp_path), species="x")
        tiles.assert_not_called()

    def test_tectonics_share_one_key(self, tmp_path):
        quakes = mock.Mock()
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_plates"), mock.patch.object(ortho, "draw_plates"), \
                mock.patch.object(ortho, "load_quakes", return_value=quakes) as load_quakes, \
                mock.patch.object(ortho, "draw_quakes"), \
                mock.patch.object(ortho, "load_volcanoes", side_effect=ortho.TectonicsDataError("offline")), \
                mock.patch.object(ortho, "add_tectonics_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), plates=True, earthquakes=7.0,
                                                     volcanoes=True)
        assert os.path.exists(result)
        assert load_quakes.call_args.args == (7.0,)
        assert key.call_args.args[1:] == (True, quakes, False)       # the volcanoes failed: not in the key
        assert attribution.call_args.args[1][1:] == ortho.tectonics_credits(True, True, False)

    def test_basins_and_population(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image, \
                mock.patch.object(ortho, "load_basins", return_value=[mock.Mock()]), \
                mock.patch.object(ortho, "basin_colours", return_value={}), \
                mock.patch.object(ortho, "draw_basins", return_value={0: 1}) as draw, \
                mock.patch.object(ortho, "add_basins_legend") as basins_key, \
                mock.patch.object(ortho, "load_colormap", return_value=(None, None)), \
                mock.patch.object(ortho, "add_population_legend") as population_key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), basins=True, basins_alpha=0.3,
                                            population=2015)
        assert draw.call_args.kwargs["alpha"] == 0.3
        assert basins_key.call_args.args[2] == [{0: 1}]
        assert population_key.call_args.args[1] == 2015
        assert len(add_image.call_args_list) == 2                    # the map tiles and the population tiles
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.population_attribution(2015),
                                                 ortho.BASINS_ATTRIBUTION]

    def test_ocean_layer_and_its_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image, \
                mock.patch.object(ortho, "load_ice_sheets", return_value=["sheet"]), \
                mock.patch.object(ortho, "draw_ice_sheets") as sheets, \
                mock.patch.object(ortho, "add_ocean_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                            output_dir=str(tmp_path), ice_age=True)
        ocean = add_image.call_args_list[1].args[0]
        assert ocean.postprocess.keywords == {"alpha": 0.8, "depth": False, "ice_age": True, "zoom": ortho.RELIEF_ZOOM}
        assert key.call_args.kwargs["ice_age"] is True and key.call_args.kwargs["depth"] is False
        assert sheets.call_args.args[1] == ["sheet"] and key.call_args.kwargs["ice_sheets"] is True
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.ICE_SHEETS_ATTRIBUTION,
                                                 ortho.ELEVATION_ATTRIBUTION]

    def test_ice_age_without_its_ice_sheets_still_draws(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ice_sheets", side_effect=ortho.IceAgeDataError("offline")), \
                mock.patch.object(ortho, "add_ocean_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                                                     output_dir=str(tmp_path), ice_age=True)
        assert os.path.exists(result) and key.call_args.kwargs["ice_sheets"] is False
        assert ortho.ICE_SHEETS_ATTRIBUTION not in attribution.call_args.args[1]

    @pytest.mark.parametrize("text, expected", [
        ("", {}),
        ("plates, earthquakes ,population", {"plates": True, "earthquakes": 6.0, "population": 2020}),
        ("livestock:cow, ice-age", {"livestock": "cattle", "ice_age": True}),
        ("malaria, aedes", {"malaria": "pf", "aedes": "both"}),
        ("malaria:vivax, aedes:tiger", {"malaria": "pv", "aedes": "tiger"}),
        ("species: Red Fox, plates", {"species": "red fox", "plates": True}),
        ("iucn:Panthera leo, species:lion", {"iucn": "panthera leo", "species": "lion"}),
        ("cities, plates", {"cities": 2025, "plates": True}),
        ("cities:1975", {"cities": 1975}),
        ("indicator:Life-Expectancy, cities", {"indicator": "life-expectancy", "cities": 2025}),
        ("industry:petrochemical", {"industry": "petrochemical"}),
        ("shipping, air-routes", {"shipping": "commercial", "air_routes": "all"}),
        ("shipping:Fishing, air-routes:lhr+jfk", {"shipping": "fishing", "air_routes": "lhr+jfk"}),
        ("ports, shipping", {"ports": "all", "shipping": "commercial"}),
        ("ports:Tankers", {"ports": "tanker"}),
        ("relief-units", {"relief_units": "units"}),
        ("relief-units:Regions", {"relief_units": "regions"}),
        ("ro-admin", {"ro_admin": "counties"}),
        ("ro-admin:UAT", {"ro_admin": "uats"}),
    ])
    def test_extra_layers_for_the_prompt(self, text, expected):
        assert ortho.parse_extra_layers(text) == expected

    def test_extra_layer_species_needs_a_name(self):
        with pytest.raises(ValueError, match="Name the species"):
            ortho.parse_extra_layers("species:")
        with pytest.raises(ValueError, match="after 'iucn:'"):
            ortho.parse_extra_layers("iucn: ")

    def test_unknown_extra_layer(self):
        with pytest.raises(ValueError, match="Unknown layer"):
            ortho.parse_extra_layers("dragons")

    def test_biome_failure_still_saves_map_without_key_or_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ecoregions", side_effect=ortho.BiomeDataError("offline")), \
                mock.patch.object(ortho, "add_biome_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path), biomes=True,
            )
        assert os.path.exists(result)
        key.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    def test_soil_failure_still_saves_map_without_key_or_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "add_soil_overlay", side_effect=ortho.SoilDataError("offline")),                 mock.patch.object(ortho, "add_soil_legend") as key,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), soil=True,
            )
        assert os.path.exists(result)
        key.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    def test_soil_property_overlay_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "add_soil_property_overlay") as overlay,                 mock.patch.object(ortho, "add_soil_property_legend") as key,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), soil_property="ph", soil_alpha=0.7,
            )
        prop = ortho.SOIL_PROPERTIES["ph"]
        assert overlay.call_args.args[1:] == (prop, "0-5cm") and overlay.call_args.kwargs["alpha"] == 0.7
        assert key.call_args.args[1:] == (prop, "0-5cm")
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.SOIL_PROPERTY_ATTRIBUTION]

    def test_soil_property_failure_still_saves_map(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "add_soil_property_overlay",
                                  side_effect=ortho.SoilPropertyError("offline")),                 mock.patch.object(ortho, "add_soil_property_legend") as key:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), soil_property="ph",
            )
        assert os.path.exists(result)
        key.assert_not_called()

    @pytest.mark.parametrize("kwargs, message", [
        ({"soil": True, "soil_property": "ph"}, "choose one soil layer"),
        ({"soil_depth": "5-15cm"}, "soil_depth needs a soil_property"),
        ({"soil_property": "acidity"}, "Unknown soil property"),
    ])
    def test_bad_soil_property_fails_before_any_work(self, tmp_path, kwargs, message):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match=message):
                ortho.generate_orthographic_map(lat=0, lon=0, output_filename=str(tmp_path / "m.png"), **kwargs)
        create.assert_not_called()

    def _vegetation_render(self, tmp_path, **kwargs):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path), **kwargs,
            )
        layers = [c for c in add_image.call_args_list if isinstance(c.args[0].tile_source, GibsTiles)]
        return layers, attribution.call_args.args[1]

    def test_land_cover_layer_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho, "add_land_cover_legend") as key:
            layers, credits = self._vegetation_render(
                tmp_path, land_cover_classes=["cropland"], land_cover_year=2010, vegetation_alpha=0.5)
        assert len(layers) == 1
        layer = layers[0]
        assert layer.args[1] == ortho.VEGETATION_ZOOM and layer.kwargs["zorder"] == 2
        assert layer.args[0].tile_source.time == "2010-01-01"
        assert layer.args[0].postprocess.keywords == {"alpha": 0.5, "classes": (12, 14)}
        assert key.call_args.args[1] == 2010 and key.call_args.kwargs["classes"] == (12, 14)
        assert credits[-1] == ortho.land_cover_attribution(2010)

    def test_no_cache_reaches_the_vegetation_and_elevation_tiles(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image, \
                mock.patch.object(ortho, "add_elevation_legend"), \
                mock.patch.object(ortho, "add_land_cover_legend"):
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
                elevation=True, land_cover=True, tile_cache_dir=None,
            )
        sources = [c.args[0] for c in add_image.call_args_list]
        layers = [s for s in sources if isinstance(s.tile_source, (GibsTiles, ortho.TerrariumTiles))]
        assert len(layers) == 2 and not any(s.tile_source.use_cache for s in layers)
        relief = next(s for s in layers if isinstance(s.tile_source, ortho.TerrariumTiles))
        assert relief.postprocess.keywords["use_cache"] is False

    def test_ndvi_layer_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho, "add_ndvi_legend") as key:
            layers, credits = self._vegetation_render(tmp_path, ndvi="2020-07")
        assert layers[0].args[0].tile_source.time == "2020-07-01"
        assert key.call_args.args[1:] == (2020, 7)
        assert credits[-1] == ortho.ndvi_attribution(2020, 7)

    @pytest.mark.parametrize("kwargs, message", [
        ({"land_cover": True, "ndvi": "2020-07"}, "choose one vegetation layer"),
        ({"ndvi": "1999-07"}, "NDVI runs from"),
        ({"land_cover_year": 2000}, "land_cover_year"),
    ])
    def test_bad_vegetation_fails_before_any_work(self, tmp_path, kwargs, message):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match=message):
                ortho.generate_orthographic_map(lat=0, lon=0, output_filename=str(tmp_path / "m.png"), **kwargs)
        create.assert_not_called()

    @pytest.mark.parametrize("kwargs, message", [
        ({"biome_classes": ["jungle"]}, "Unknown biome"),
        ({"biomes": True, "biome_alpha": -0.1}, "biome_alpha"),
        ({"ecoregions": True, "realms": "wwf"}, "ecoregions or realms"),
        ({"realms": "holt", "biome_classes": ["forest"]}, "not split by biome"),
        ({"realms": "sclater"}, "realms must be"),
        ({"borders": "4000 BC"}, "Cliopatria covers"),
        ({"borders": "1500", "borders_alpha": 2.0}, "borders_alpha"),
    ])
    def test_bad_biome_options_fail_before_any_work(self, tmp_path, kwargs, message):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match=message):
                ortho.generate_orthographic_map(lat=0, lon=0, output_filename=str(tmp_path / "m.png"), **kwargs)
        create.assert_not_called()

    def test_unknown_soil_group_fails_before_any_work(self, tmp_path):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match="soil group"):
                ortho.generate_orthographic_map(
                    lat=0, lon=0, output_filename=str(tmp_path / "m.png"), soil_classes=["Xx"],
                )
        create.assert_not_called()

    def test_elevation_layer_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image,                 mock.patch.object(ortho, "add_elevation_legend") as key,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), elevation=True, elevation_alpha=0.6,
            )
        relief = [c for c in add_image.call_args_list if isinstance(c.args[0].tile_source, ortho.TerrariumTiles)]
        assert len(relief) == 1
        assert relief[0].args[1] == ortho.RELIEF_ZOOM and relief[0].kwargs["zorder"] == 1
        assert relief[0].args[0].postprocess.keywords["alpha"] == 0.6
        key.assert_called_once()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.ELEVATION_ATTRIBUTION]

    def test_elevation_with_trewartha_credits_the_heights_once(self, tmp_path):
        with mock.patch.object(ortho, "add_trewartha_overlay"),                 mock.patch.object(ortho, "add_trewartha_legend"),                 mock.patch.object(ortho, "add_elevation_legend"):
            credits = self._render_credits(tmp_path, trewartha=True, elevation=True)
        assert credits.count(ortho.ELEVATION_ATTRIBUTION) == 1

    def test_trewartha_failure_still_saves_map_without_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_trewartha_overlay",
                                  side_effect=ortho.TrewarthaDataError("no data")), \
                mock.patch.object(ortho, "add_trewartha_legend") as legend, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), trewartha=True,
            )
        assert os.path.exists(result)
        legend.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    @pytest.mark.parametrize("kwargs", [{"dpi": 0}, {"koppen_alpha": 2.0}])
    def test_invalid_options_fail_before_any_work(self, tmp_path, kwargs):
        with mock.patch.object(ortho, "create_tile_source") as create, \
                mock.patch.object(ortho, "Figure") as figure:
            with pytest.raises(ValueError):
                ortho.generate_orthographic_map(
                    lat=0, lon=0, output_filename=str(tmp_path / "m.png"), **kwargs,
                )
        create.assert_not_called()
        figure.assert_not_called()

    def test_explicit_output_path_folders_are_created(self, tmp_path):
        target = tmp_path / "renders" / "nested" / "m.png"
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None):
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename=str(target), zoom=1, dpi=20,
            )
        assert target.is_file()
        assert result == str(target.resolve())

    @pytest.mark.parametrize("fail_all, expected", [
        (True, "No map tiles could be downloaded"),
        (False, "map tiles could not be downloaded"),
    ])
    def test_tile_failures_still_save_map(self, tmp_path, caplog, fail_all, expected):
        def get_image(self, tile):
            if fail_all or tile[0] == 0:
                raise urllib.error.URLError("network down")
            return np.full((256, 256, 4), 200, np.uint8), self.tileextent(tile), "lower"

        with mock.patch.object(ortho.CachedOSM, "get_image", get_image):
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path),
            )
        assert os.path.exists(result)
        assert expected in caplog.text

    def test_no_routes_drawn_by_default(self, tmp_path):
        """Without routes, nothing but the two distance circles is plotted."""
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "draw_routes") as draw:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path),
            )
        draw.assert_not_called()

    @pytest.mark.parametrize("koppen, expected_y", [(False, -0.01), (True, -0.07)])
    def test_route_legend_drawn_when_requested(self, tmp_path, koppen, expected_y):
        route = ortho.Route(name="r", lines=(((0.0, 0.0), (10.0, 10.0)),))
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay"), \
                mock.patch.object(ortho, "add_koppen_legend"), \
                mock.patch.object(ortho, "add_route_legend") as legend, \
                mock.patch.object(ortho, "_stack_keys") as stack:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), routes=[route], route_legend=True, koppen=koppen,
            )
        legend.assert_called_once()
        assert stack.call_args.args[1] == [legend.return_value]
        assert stack.call_args.kwargs["top"] == expected_y

    def test_both_hemispheres_draws_two_globes(self, tmp_path):
        """The antipode globe sits right of the city's, keys centre under the pair."""
        route = ortho.Route(name="r", lines=(((0.0, 0.0), (10.0, 10.0)),))
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay") as overlay, \
                mock.patch.object(ortho, "add_koppen_legend") as koppen_legend, \
                mock.patch.object(ortho, "add_route_legend") as route_legend, \
                mock.patch.object(ortho, "draw_routes") as draw, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=38.7223, lon=-9.1393, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), city_name="Lisbon", koppen=True,
                routes=[route], route_legend=True, both_hemispheres=True,
            )
        assert os.path.exists(result)

        right = attribution.call_args.args[0]           # credits go under the right globe
        left, other = right.figure.axes
        assert other is right
        assert left.projection.proj4_params["lat_0"] == pytest.approx(38.7223)
        assert left.projection.proj4_params["lon_0"] == pytest.approx(-9.1393)
        assert right.projection.proj4_params["lat_0"] == pytest.approx(-38.7223)
        assert right.projection.proj4_params["lon_0"] == pytest.approx(170.8607)
        assert left.get_position().x1 < right.get_position().x0   # side by side, no overlap

        # Both globes get the overlay and routes; each key is drawn once, centred on the pair
        assert overlay.call_count == 2 and draw.call_count == 2
        mid = (left.get_position().x0 + right.get_position().x1) / 2
        for key in (koppen_legend, route_legend):
            key.assert_called_once()
            assert key.call_args.args[0] is left
            x = key.call_args.kwargs["x"]
            assert left.get_position().x0 + x * left.get_position().width == pytest.approx(mid)

        # The far globe marks the antipode and labels its rings by distance from Lisbon
        assert any(t.get_text().strip() == "Antipode of Lisbon" for t in right.texts)
        assert {t.get_text().strip() for t in right.texts} >= {"17,500 km", "15,000 km"}

    def test_a_globe_beside_draws_both_places(self, tmp_path):
        """The second globe is centred on its own place, marked, and ringed from it."""
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay") as overlay, \
                mock.patch.object(ortho, "add_koppen_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=38.7223, lon=-9.1393, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), city_name="Lisbon", koppen=True,
                beside=(35.6762, 139.6503), beside_name="Tokyo",
            )
        assert os.path.exists(result)
        right = attribution.call_args.args[0]
        left, other = right.figure.axes
        assert other is right and left.get_position().x1 < right.get_position().x0
        assert right.projection.proj4_params["lat_0"] == pytest.approx(35.6762)
        assert right.projection.proj4_params["lon_0"] == pytest.approx(139.6503)
        assert overlay.call_count == 2
        key.assert_called_once()
        mid = (left.get_position().x0 + right.get_position().x1) / 2
        x = key.call_args.kwargs["x"]
        assert left.get_position().x0 + x * left.get_position().width == pytest.approx(mid)
        for ax, name in ((left, "Lisbon"), (right, "Tokyo")):
            texts = {t.get_text().strip() for t in ax.texts}
            assert name in texts and {"2,500 km", "5,000 km"} <= texts
        assert not any("Antipode" in t.get_text() for t in right.texts)

    @pytest.mark.parametrize("beside_up, expected", [(None, 30), (90, 90)])
    def test_a_globe_beside_turns_with_the_first_unless_told(self, tmp_path, beside_up, expected):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=38.7, lon=-9.1, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
                up=30, beside=(35.7, 139.7), beside_up=beside_up,
            )
        left, right = attribution.call_args.args[0].figure.axes
        assert left.projection.up == 30 and right.projection.up == expected

    def test_a_radius_map_beside_shows_the_radius_on_both(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=38.7, lon=-9.1, output_filename="m.png", zoom=6, dpi=20, output_dir=str(tmp_path),
                radius_km=2_500, beside=(35.7, 139.7),
            )
        left, right = attribution.call_args.args[0].figure.axes
        for ax in (left, right):
            half = ax.projection.x_limits[1] * np.sin(2_500_000 / ortho.EARTH_RADIUS_M)
            assert ax.get_xlim() == pytest.approx((-half, half))

    def test_ice_drawn_on_every_globe_and_credited(self, tmp_path):
        layers = IceLayers(sea_ice=(), land_ice=(), years={"N": 2026, "S": 2026})
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ice_layers", return_value=layers) as load, \
                mock.patch.object(ortho, "draw_ice") as draw, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), ice=True, ice_year=2026, both_hemispheres=True,
            )
        load.assert_called_once_with(year=2026)
        assert draw.call_count == 2                      # once per globe, data loaded once
        assert attribution.call_args.args[1][-1] == layers.attribution

    def test_ice_failure_still_saves_map_without_credit(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ice_layers",
                                  side_effect=ortho.IceDataError("no sea ice data")), \
                mock.patch.object(ortho, "draw_ice") as draw, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), ice=True,
            )
        assert os.path.exists(result)
        assert "Skipping polar ice: no sea ice data" in caplog.text
        draw.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

    def test_crops_drawn_on_every_globe_keyed_and_credited(self, tmp_path):
        layer = mock.Mock(name="crop_layer")
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_crop_layer", return_value=layer) as load, \
                mock.patch.object(ortho, "draw_crops") as draw, \
                mock.patch.object(ortho, "add_crop_legend") as key, \
                mock.patch.object(ortho, "_stack_keys") as stack, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), crops=["wheat", "rice"], both_hemispheres=True,
            )
        load.assert_called_once_with(["wheat", "rice"])
        assert draw.call_count == 2                       # once per globe, data loaded once
        key.assert_called_once()
        assert stack.call_args.args[1] == [key.return_value]
        assert attribution.call_args.args[1][-1] == ortho.CROP_ATTRIBUTION

    def test_crop_failure_still_saves_map(self, tmp_path, caplog):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_crop_layer",
                                  side_effect=ortho.CropDataError("no crop data")), \
                mock.patch.object(ortho, "add_crop_legend") as key, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), crops=["wheat"],
            )
        assert os.path.exists(result)
        assert "Skipping crop areas: no crop data" in caplog.text
        key.assert_not_called()
        assert ortho.CROP_ATTRIBUTION not in attribution.call_args.args[1]

    def test_rotated_globes(self, tmp_path):
        """Both globes turn; the far one keeps the near globe's top point at its top."""
        from rotation import RotatedOrthographic, far_side_up

        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=51.5, lon=-0.13, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), up=90, both_hemispheres=True, city_name="London",
            )
        near, far = attribution.call_args.args[0].figure.axes
        assert isinstance(near.projection, RotatedOrthographic) and near.projection.up == 90
        assert isinstance(far.projection, RotatedOrthographic)
        assert far.projection.up == pytest.approx(far_side_up(51.5, -0.13, 90, -51.5, 179.87))
        assert any(t.get_text().strip() == "London" for t in near.texts)

    def test_north_up_keeps_cartopys_orthographic(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20, output_dir=str(tmp_path),
            )
        assert type(attribution.call_args.args[0].projection) is ortho.ccrs.Orthographic

    def test_non_finite_up_fails_before_any_work(self, tmp_path):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match="bearing"):
                ortho.generate_orthographic_map(
                    lat=0, lon=0, output_filename=str(tmp_path / "m.png"), up=float("nan"),
                )
        create.assert_not_called()

    def test_koppen_classes_imply_the_overlay_and_reach_it(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay") as overlay, \
                mock.patch.object(ortho, "add_koppen_legend") as key:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), koppen_classes=["Cs"],
            )
        codes = ortho.resolve_koppen_classes(["Cs"])
        assert overlay.call_args.kwargs["classes"] == codes
        assert key.call_args.kwargs["classes"] == codes

    def test_unknown_koppen_class_fails_before_any_work(self, tmp_path):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match="Unknown Köppen-Geiger class"):
                ortho.generate_orthographic_map(
                    lat=0, lon=0, output_filename=str(tmp_path / "m.png"), koppen_classes=["Cxx"],
                )
        create.assert_not_called()

    def test_trewartha_classes_imply_the_overlay_and_reach_it(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_koppen_overlay") as koppen, \
                mock.patch.object(ortho, "add_trewartha_overlay") as overlay, \
                mock.patch.object(ortho, "add_trewartha_legend") as key:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), trewartha_classes=["C"],
            )
        codes = ortho.resolve_trewartha_classes(["C"])
        assert overlay.call_args.kwargs["classes"] == codes
        assert key.call_args.kwargs["classes"] == codes
        koppen.assert_not_called()

    @pytest.mark.parametrize("kwargs, message", [
        ({"trewartha_classes": ["Xx"]}, "Unknown Trewartha class"),
        ({"koppen": True, "trewartha": True}, "choose one climate classification"),
        ({"koppen_classes": ["Cs"], "trewartha_classes": ["Cs"]}, "choose one climate classification"),
    ])
    def test_bad_trewartha_options_fail_before_any_work(self, tmp_path, kwargs, message):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match=message):
                ortho.generate_orthographic_map(
                    lat=0, lon=0, output_filename=str(tmp_path / "m.png"), **kwargs,
                )
        create.assert_not_called()

    def test_unknown_crop_fails_before_any_work(self, tmp_path):
        with mock.patch.object(ortho, "create_tile_source") as create:
            with pytest.raises(ValueError, match="Unknown crop"):
                ortho.generate_orthographic_map(
                    lat=0, lon=0, output_filename=str(tmp_path / "m.png"), crops=["whaet"],
                )
        create.assert_not_called()

    def test_stacked_keys_do_not_overlap(self):
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.legend import Legend

        fig, ax = plt.subplots(subplot_kw={"projection": ortho.ccrs.Orthographic(0, 0)})
        try:
            keys = []
            for rows in (3, 5):
                key = Legend(ax, [mpatches.Patch()] * rows, [f"entry {i}" for i in range(rows)],
                             loc="upper center")
                ax.add_artist(key)
                keys.append(key)
            ortho._stack_keys(ax, keys, top=-0.01)
            first, second = (k.get_window_extent() for k in keys)
            assert second.y1 < first.y0                     # the second sits wholly below the first
            assert first.y1 <= ax.get_window_extent().y0    # and both sit below the globe
        finally:
            plt.close(fig)

    def test_no_ice_by_default(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "load_ice_layers") as load:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path),
            )
        load.assert_not_called()

    def test_route_legend_off_by_default(self, tmp_path):
        route = ortho.Route(name="r", lines=(((0.0, 0.0), (10.0, 10.0)),))
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "add_route_legend") as legend:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), routes=[route],
            )
        legend.assert_not_called()


# ===================================================================
# Attribution
# ===================================================================


class TestAttribution:
    def test_every_provider_has_a_credit_source(self):
        # Google credits come from the Map Tiles API, a base drawn from data credits its data; the rest is fixed
        assert (set(ortho.TILE_ATTRIBUTIONS) | set(ortho.GOOGLE_MAP_TYPES) | ortho.LOCAL_PROVIDERS
                == set(ortho.TILE_PROVIDERS))

    def test_credits_drawn_unclipped_in_bottom_right(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import cartopy.crs as ccrs

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            ortho._add_attribution(ax, ["Line one", "Line two"])
            (text,) = ax.texts
            assert text.get_text() == "Line one\nLine two"
            assert text.get_position() == (1.0, 0.0)
            assert text.get_transform() is ax.transAxes
            assert not text.get_clip_on()  # GeoAxes would clip it to the globe
        finally:
            plt.close(fig)

    @pytest.mark.parametrize("rectangle", [True, False])
    def test_names_under_a_rectangles_credits_left_out(self, rectangle):
        fig = ortho.Figure(figsize=(8, 8))
        ax = ortho._add_globe_axes(fig, 46, 10, 0, 1, radius_km=800, rectangle=1.5 if rectangle else None)
        (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
        # Placed as the layers place names: annotations above a point, which have no extent until drawn
        corner = ax.annotate("Munții Buzăului", (x1 - 0.05 * (x1 - x0), y0 + 0.02 * (y1 - y0)), xycoords="data",
                             xytext=(0, 2), textcoords="offset points", ha="right", annotation_clip=True)
        middle = ax.annotate("Depresiunea Brașovului", ((x0 + x1) / 2, (y0 + y1) / 2), xycoords="data",
                             xytext=(0, 2), textcoords="offset points", annotation_clip=True)
        ortho._add_attribution(ax, ["Map tiles © OpenStreetMap contributors", "Relief units: geo-spatial.org"])
        assert corner.get_visible() is not rectangle             # a disc's credits sit outside the map
        assert middle.get_visible() and ax.texts[-1].get_visible()


# ===================================================================
# Distance circles
# ===================================================================


class TestDrawDistanceCircles:
    """Circles must stay continuous even when they enclose a pole."""

    @pytest.mark.parametrize(
        "lon, lat",
        [
            (-0.1276, 51.5072),   # London: 5 000 km ring encloses the North Pole
            (-21.9, 64.1),        # Reykjavik: both rings near / around the pole
            (166.7, -77.8),       # McMurdo: rings enclose the South Pole
            (-46.6, -23.5),       # São Paulo: no pole inside, control case
        ],
    )
    def test_rings_are_closed_and_continuous(self, lon, lat):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import cartopy.crs as ccrs

        fig, ax = plt.subplots(
            subplot_kw={"projection": ccrs.Orthographic(lon, lat)},
        )
        try:
            ortho._draw_distance_circles(ax, lon, lat, radii_km=(2_500, 5_000))
            assert len(ax.lines) == 2

            for line in ax.lines:
                xy = np.column_stack(line.get_data())
                assert np.isfinite(xy).all()
                # Closed ring
                assert np.allclose(xy[0], xy[-1])
                # No spurious long edges: 180 vertices on a <=5 000 km circle
                # are ~175 km apart, so anything over 500 km is a seam jump.
                steps = np.hypot(*np.diff(xy, axis=0).T)
                assert steps.max() < 500_000
        finally:
            plt.close(fig)

    def test_rings_outside_a_radius_disc(self):
        """On a radius map, a ring wholly outside the disc is left out and labels stay inside it."""
        import cartopy.crs as ccrs
        import matplotlib.pyplot as plt

        proj = ccrs.Orthographic(0, 0)
        disc = proj.x_limits[1] * np.sin(1_000_000 / ortho.EARTH_RADIUS_M)   # a 1,000 km map
        fig, ax = plt.subplots(subplot_kw={"projection": proj})
        ax.set_extent((-disc, disc, -disc, disc), crs=proj)
        try:
            # From 1,500 km east of the centre: 1,000 km crosses the disc, 3,000 km misses it
            ortho._draw_distance_circles(ax, 13.5, 0, radii_km=(1_000, 3_000), within=True)
            assert len(ax.texts) == 1 and ax.texts[0].get_text().strip() == "1,000 km"
            assert np.hypot(*ax.texts[0].get_position()) <= disc
        finally:
            plt.close(fig)

    def test_circle_crossing_the_horizon_has_no_chord(self):
        """A ring that straddles the horizon must not get a straight line across its hidden part.

        Rings centred on the view centre are either fully visible or fully
        hidden, so this uses a ring centred off-view (80°E on a 0°E map),
        which is cut by the horizon at 90°E.
        """
        import cartopy.crs as ccrs
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            ortho._draw_distance_circles(ax, 80, 0, radii_km=(2_500,))
            assert len(ax.lines) == 1          # one visible arc, not a closed ring
            xy = np.column_stack(ax.lines[0].get_data())
            assert np.isfinite(xy).all()
            assert not np.allclose(xy[0], xy[-1])   # open arc: ends sit on the horizon
            steps = np.hypot(*np.diff(xy, axis=0).T)
            assert steps.max() < 500_000            # a chord would span thousands of km
            assert len(ax.texts) == 1
        finally:
            plt.close(fig)


class TestVisibleRuns:
    @staticmethod
    def _ring(visible):
        """A closed ring of len(visible) distinct points, NaN where not visible."""
        n = len(visible)
        pts = np.column_stack([np.arange(n, dtype=float), np.zeros(n)])
        pts[~np.asarray(visible)] = np.nan
        return np.vstack([pts, pts[:1]])   # closed: last == first

    def test_fully_visible_ring_is_returned_closed(self):
        ring = self._ring([True] * 5)
        (run,) = ortho._visible_runs(ring)
        assert np.array_equal(run, ring)

    def test_gap_in_the_middle_gives_one_run_across_the_seam(self):
        # Points 0,1 and 4,5 are visible; 2,3 hidden. 4,5,0,1 are contiguous around the ring.
        (run,) = ortho._visible_runs(self._ring([True, True, False, False, True, True]))
        assert run[:, 0].tolist() == [4, 5, 0, 1]

    def test_two_gaps_give_two_runs(self):
        runs = ortho._visible_runs(self._ring([True, True, False, True, True, False]))
        assert sorted(run[:, 0].tolist() for run in runs) == [[0, 1], [3, 4]]

    def test_single_visible_points_are_dropped(self):
        assert ortho._visible_runs(self._ring([True, False, False, False])) == []


class TestInteractiveClimate:
    """The climate prompt of interactive mode, answered by prompt text."""

    def _run(self, monkeypatch, climate, periods=(), opacity=""):
        choices = iter(["2", "1"])                           # the first city, OSM
        period_answers = iter(periods)
        calls = []

        def answer(prompt):
            calls.append(prompt)
            assert len(calls) < 60, "interactive mode kept asking"
            if "number of your choice" in prompt:
                return next(choices)
            if prompt.startswith("Climate overlay:"):
                return climate
            if " period:" in prompt:
                return next(period_answers)
            if "opacity" in prompt:
                return opacity
            return ""

        monkeypatch.setattr("builtins.input", answer)
        with mock.patch.object(ortho, "generate_orthographic_map") as render:
            ortho.run_interactive()
        return render.call_args.kwargs, calls

    def test_a_climate_mean_and_its_period(self, monkeypatch, capsys):
        kwargs, calls = self._run(monkeypatch, "p", periods=["monsoon", "jja"])
        assert kwargs["precipitation"] == "jja" and "temperature" not in kwargs
        assert kwargs["koppen_alpha"] == 0.7 and not kwargs["koppen"] and not kwargs["trewartha"]
        assert "Unknown precipitation period 'monsoon'" in capsys.readouterr().out
        assert any("[default: 0.7]" in c for c in calls)

    def test_a_blank_period_is_the_year_and_opacity_can_be_set(self, monkeypatch):
        kwargs, _ = self._run(monkeypatch, "m", periods=[""], opacity="0.5")
        assert kwargs["temperature"] == "year" and kwargs["koppen_alpha"] == 0.5

    def test_no_climate_layer(self, monkeypatch):
        kwargs, calls = self._run(monkeypatch, "")
        assert kwargs["koppen_alpha"] == 0.45 and not any("opacity" in c for c in calls)
        assert not {"temperature", "precipitation", "humidity"} & kwargs.keys()


class TestRadiusAndWindRendering:
    """Offline renders of a zoomed-in map and of the wind layer."""

    def _render(self, tmp_path, **kwargs):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None), \
                mock.patch.object(ortho, "_draw_distance_circles") as circles, \
                mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=46, lon=10, output_filename="m.png", zoom=kwargs.pop("zoom", 1), dpi=20,
                output_dir=str(tmp_path), **kwargs,
            )
        assert os.path.exists(result)
        return attribution.call_args.args[0], circles, attribution.call_args.args[1]

    def test_radius_zooms_to_a_disc_of_that_size(self, tmp_path):
        ax, circles, _ = self._render(tmp_path, radius_km=2_500, zoom=6)
        half = ax.projection.x_limits[1] * np.sin(2_500_000 / ortho.EARTH_RADIUS_M)
        assert ax.get_extent(ax.projection) == pytest.approx((-half, half, -half, half))
        # Clipped to the circle within that square
        boundary = ax.patch.get_path().vertices
        assert np.hypot(boundary[:, 0] - 0.5, boundary[:, 1] - 0.5) == pytest.approx(0.5)
        assert circles.call_args.kwargs["radii_km"] == (500, 1_000, 1_500, 2_000)

    def test_rectangle_cuts_the_radius_square(self, tmp_path):
        ax, circles, _ = self._render(tmp_path, radius_km=2_500, rectangle=1.5, zoom=6)
        half = ax.projection.x_limits[1] * np.sin(2_500_000 / ortho.EARTH_RADIUS_M)
        assert ax.get_extent(ax.projection) == pytest.approx((-half, half, -half / 1.5, half / 1.5))
        # The whole frame, not a circle within it
        boundary = ax.patch.get_path().vertices
        assert {tuple(v) for v in np.round(boundary, 6)} == {(0, 0), (1, 0), (1, 1), (0, 1)}
        assert ortho.is_rectangle(ax)
        assert circles.call_args.kwargs["within"] is True
        assert circles.call_args.kwargs["radii_km"] == (500, 1_000, 1_500, 2_000)

    def test_rectangle_shaped_before_the_layers_measure_it(self):
        fig = ortho.Figure(figsize=(10, 10))
        ax = ortho._add_globe_axes(fig, 46, 10, 0, 1, radius_km=800, rectangle=1.5)
        box = ax.get_window_extent()                               # as names and keys measure it, before drawing
        assert box.width / box.height == pytest.approx(1.5, rel=0.01)

    def test_tall_rectangle(self, tmp_path):
        ax, _, _ = self._render(tmp_path, radius_km=1_000, rectangle=0.5, zoom=7)
        half = ax.projection.x_limits[1] * np.sin(1_000_000 / ortho.EARTH_RADIUS_M)
        assert ax.get_extent(ax.projection) == pytest.approx((-half / 2, half / 2, -half, half))

    def test_two_globes_cut_alike(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None):
            fig_axes = []
            real = ortho._add_globe_axes

            def spy(*args, **kwargs):
                fig_axes.append(real(*args, **kwargs))
                return fig_axes[-1]

            with mock.patch.object(ortho, "_add_globe_axes", spy):
                ortho.generate_orthographic_map(lat=46, lon=10, output_filename="m.png", zoom=6, dpi=20,
                                                output_dir=str(tmp_path), radius_km=800, rectangle=4 / 3,
                                                beside=(40.4, -3.7))
        assert len(fig_axes) == 2 and all(ortho.is_rectangle(ax) for ax in fig_axes)

    def test_circles_from_another_place(self, tmp_path):
        ax, circles, _ = self._render(tmp_path, radius_km=2_500, zoom=6, circles_from=(52.52, 13.405))   # Berlin
        args, kwargs = circles.call_args
        assert args[1:] == (13.405, 52.52)
        # The map reaches ~3,250 km from Berlin, so the rings step by 1,000 km
        assert kwargs["radii_km"] == (1_000, 2_000, 3_000)
        assert kwargs["within"] is True
        rings = [line for line in ax.lines if line.get_marker() == "o" and line.get_markerfacecolor() == "none"]
        assert len(rings) == 1 and tuple(np.ravel(rings[0].get_data())) == (13.405, 52.52)

    def test_rotated_radius_map(self, tmp_path):
        ax, _, _ = self._render(tmp_path, radius_km=1_000, zoom=8, up=45)
        half = ax.projection.x_limits[1] * np.sin(1_000_000 / ortho.EARTH_RADIUS_M)
        assert ax.get_extent(ax.projection) == pytest.approx((-half, half, -half, half))

    def test_no_distance_circles(self, tmp_path):
        ax, circles, _ = self._render(tmp_path, radius_km=2_500, zoom=6, circles_from=(52.52, 13.405),
                                      distance_circles=False)
        assert not circles.called
        assert not [line for line in ax.lines if line.get_marker() == "o" and line.get_markerfacecolor() == "none"]

    def test_whole_globe_by_default(self, tmp_path):
        ax, circles, _ = self._render(tmp_path)
        assert "radii_km" not in circles.call_args.kwargs
        assert ax.get_extent(ax.projection)[1] == pytest.approx(ax.projection.x_limits[1])

    def test_radius_maps_fetch_a_thinner_border_of_tiles(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image:
            ortho.generate_orthographic_map(lat=46, lon=10, output_filename="m.png", zoom=6, dpi=20,
                                            output_dir=str(tmp_path), radius_km=2_500)
        assert add_image.call_args.args[0].tile_buffer_factor == ortho.RADIUS_TILE_BUFFER

    def test_radius_maps_use_finer_terrain_and_vegetation_tiles(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None) as add_image, \
                mock.patch.object(ortho, "add_elevation_legend"), \
                mock.patch.object(ortho, "add_land_cover_legend"):
            ortho.generate_orthographic_map(
                lat=46, lon=10, output_filename="m.png", zoom=11, dpi=20, output_dir=str(tmp_path),
                radius_km=200, elevation=True, land_cover=True,
            )
        zooms = {type(c.args[0].tile_source).__name__: c.args[1] for c in add_image.call_args_list}
        assert zooms == {"CachedOSM": 11, "TerrariumTiles": 10, "GibsTiles": 8}
        relief = next(c.args[0] for c in add_image.call_args_list
                      if isinstance(c.args[0].tile_source, ortho.TerrariumTiles))
        assert relief.postprocess.keywords["zoom"] == 10

    def test_wind_layer_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho, "add_wind_overlay") as overlay, \
                mock.patch.object(ortho, "add_wind_legend") as key:
            _, _, credits = self._render(tmp_path, wind="djf")
        period = overlay.call_args.args[1]
        assert period.months == (12, 1, 2) and key.call_args.args[1] == period
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.WIND_ATTRIBUTION]

    def test_wind_failure_still_saves_map(self, tmp_path, caplog):
        with mock.patch.object(ortho, "add_wind_overlay", side_effect=ortho.WindDataError("offline")), \
                mock.patch.object(ortho, "add_wind_legend") as key:
            _, _, credits = self._render(tmp_path, wind="year")
        key.assert_not_called()
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"]] and "Skipping prevailing winds" in caplog.text

    def test_climate_mean_layer_key_and_credit(self, tmp_path):
        with mock.patch.object(ortho, "add_climate_overlay") as overlay, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, humidity="jja", radius_km=1_000, zoom=8)
        variable, period = overlay.call_args.args[1:3]
        assert variable.key == "humidity" and period.months == (6, 7, 8)
        assert overlay.call_args.kwargs["alpha"] == ortho.CLIMATE_MEAN_ALPHA == 0.7
        assert overlay.call_args.kwargs["zoom"] == 8
        assert key.call_args.args[1:] == (variable, period)
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.CLIMATE_ATTRIBUTION]

    def test_climate_mean_opacity_can_be_set(self, tmp_path):
        with mock.patch.object(ortho, "add_climate_overlay") as overlay, \
                mock.patch.object(ortho, "add_climate_legend"):
            self._render(tmp_path, temperature="year", koppen_alpha=0.3)
        assert overlay.call_args.kwargs["alpha"] == 0.3

    def test_climate_mean_failure_still_saves_map(self, tmp_path, caplog):
        with mock.patch.object(ortho, "add_climate_overlay", side_effect=ortho.ClimateDataError("offline")), \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, precipitation="year")
        key.assert_not_called()
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"]] and "Skipping precipitation" in caplog.text

    @pytest.mark.parametrize("kwargs, match", [
        (dict(temperature="year", humidity="year"), "choose one climate mean"),
        (dict(precipitation="year", trewartha=True), "choose one climate layer"),
        (dict(temperature="monsoon"), "Unknown temperature period"),
    ])
    def test_bad_climate_means_fail_before_any_work(self, tmp_path, kwargs, match):
        with pytest.raises(ValueError, match=match):
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", output_dir=str(tmp_path),
                                            **kwargs)

    def test_temperature_over_sea_joins_the_land(self, tmp_path):
        grid = object()
        with mock.patch.object(ortho, "load_sea_air", return_value=grid) as load, \
                mock.patch.object(ortho, "add_climate_overlay") as overlay, \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, temperature="july", temperature_over_sea=True)
        assert load.call_args.args[0].months == (7,)
        assert overlay.call_args.kwargs["sea"] is grid
        sea_only.assert_not_called()
        key.assert_called_once()
        assert key.call_args.kwargs["title"] is None                # the land's own title covers both
        assert credits[-2:] == [ortho.CLIMATE_ATTRIBUTION, ortho.SEA_AIR_ATTRIBUTION]

    def test_sea_temperature_with_the_land_over_the_same_period_shares_image_and_key(self, tmp_path):
        grid = object()
        with mock.patch.object(ortho, "load_sea_temperature", return_value=grid), \
                mock.patch.object(ortho, "add_climate_overlay") as overlay, \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, temperature="jja", sea_temperature="jja")
        assert overlay.call_args.kwargs["sea"] is grid
        sea_only.assert_not_called()
        key.assert_called_once()
        assert key.call_args.kwargs["title"].startswith("Air temperature on land (1981–2010) and sea surface")
        assert credits[-2:] == [ortho.CLIMATE_ATTRIBUTION, ortho.SEA_TEMPERATURE_ATTRIBUTION]

    def test_sea_temperature_over_another_period_gets_its_own_image_and_key(self, tmp_path):
        grid = object()
        with mock.patch.object(ortho, "load_sea_temperature", return_value=grid), \
                mock.patch.object(ortho, "add_climate_overlay") as overlay, \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            self._render(tmp_path, temperature="year", sea_temperature="july")
        assert overlay.call_args.kwargs["sea"] is None
        assert sea_only.call_args.args[1:3] == (grid, ortho.resolve_sea_period("july"))
        assert key.call_count == 2
        assert key.call_args_list[1].kwargs["title"] == "Sea surface temperature, July mean, 1991–2020 (°C)"

    def test_sea_temperature_beside_a_classification_keeps_its_own_opacity(self, tmp_path):
        with mock.patch.object(ortho, "load_sea_temperature", return_value=object()), \
                mock.patch.object(ortho, "add_koppen_overlay") as koppen, \
                mock.patch.object(ortho, "add_koppen_legend"), \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend"):
            _, _, credits = self._render(tmp_path, koppen=True, sea_temperature="year")
        assert koppen.call_args.kwargs["alpha"] == ortho.CLASSIFICATION_ALPHA
        assert sea_only.call_args.kwargs["alpha"] == ortho.CLIMATE_MEAN_ALPHA
        assert credits[-1] == ortho.SEA_TEMPERATURE_ATTRIBUTION

    def test_sea_temperature_alone(self, tmp_path):
        with mock.patch.object(ortho, "load_sea_temperature", return_value=object()), \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, sea_temperature="jan", koppen_alpha=0.4)
        assert sea_only.call_args.kwargs["alpha"] == 0.4
        assert key.call_args.args[1].key == "temperature"
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.SEA_TEMPERATURE_ATTRIBUTION]

    def test_the_sea_is_drawn_on_its_own_when_the_land_fails(self, tmp_path, caplog):
        with mock.patch.object(ortho, "load_sea_air", return_value=object()), \
                mock.patch.object(ortho, "add_climate_overlay", side_effect=ortho.ClimateDataError("offline")), \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, temperature="july", temperature_over_sea=True)
        sea_only.assert_called_once()
        key.assert_called_once()
        assert credits == [ortho.TILE_ATTRIBUTIONS["osm"], ortho.SEA_AIR_ATTRIBUTION]
        assert "Skipping air temperature" in caplog.text

    def test_sea_failure_still_saves_the_land(self, tmp_path, caplog):
        with mock.patch.object(ortho, "load_sea_temperature", side_effect=ortho.SeaDataError("offline")), \
                mock.patch.object(ortho, "add_climate_overlay") as overlay, \
                mock.patch.object(ortho, "add_sea_overlay") as sea_only, \
                mock.patch.object(ortho, "add_climate_legend") as key:
            _, _, credits = self._render(tmp_path, temperature="july", sea_temperature="july")
        assert overlay.call_args.kwargs["sea"] is None
        sea_only.assert_not_called()
        key.assert_called_once()
        assert credits[-1] == ortho.CLIMATE_ATTRIBUTION and "Skipping the sea surface temperature" in caplog.text

    @pytest.mark.parametrize("kwargs, match", [
        (dict(temperature_over_sea=True), "temperature_over_sea needs temperature"),
        (dict(humidity="year", temperature_over_sea=True), "temperature_over_sea needs temperature"),
        (dict(temperature="year", temperature_over_sea=True, sea_temperature="year"), "choose one layer for the sea"),
        (dict(sea_temperature="monsoon"), "Unknown sea temperature period"),
    ])
    def test_bad_sea_options_fail_before_any_work(self, tmp_path, kwargs, match):
        with pytest.raises(ValueError, match=match):
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", output_dir=str(tmp_path),
                                            **kwargs)

    def test_bad_wind_period_fails_before_any_work(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown wind period"):
            ortho.generate_orthographic_map(lat=0, lon=0, output_filename="m.png", wind="monsoon",
                                            output_dir=str(tmp_path))
