"""Tests for ortho.py — unit-level tests that do NOT hit the network."""

import argparse
import io
import os
import time
import urllib.error
from unittest import mock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Make sure ortho is importable from repo root
# ---------------------------------------------------------------------------
import ortho
from ice import IceLayers


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

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unsupported.*nasa"):
            ortho.create_tile_source("bing")


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

    def test_both_hemispheres_suffix(self):
        result = ortho.build_output_filename("lisbon", "osm", 3, both_hemispheres=True)
        assert result == "orthographic_map_lisbon_osm_z3_hemispheres.png"

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


class TestValidateRenderOptions:
    def test_both_hemispheres_halves_the_dpi_cap(self):
        ortho.validate_render_options(dpi=ortho.MAX_DPI // 2, koppen_alpha=0.5, both_hemispheres=True)
        with pytest.raises(ValueError, match="with both hemispheres"):
            ortho.validate_render_options(dpi=ortho.MAX_DPI // 2 + 1, koppen_alpha=0.5, both_hemispheres=True)
        ortho.validate_render_options(dpi=ortho.MAX_DPI, koppen_alpha=0.5)

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

    def test_lat_lon_flags(self):
        args = self._parse(["--lat", "35.0", "--lon", "139.0"])
        assert args.lat == 35.0
        assert args.lon == 139.0

    def test_defaults(self):
        args = self._parse(["--city", "paris"])
        assert args.provider == "osm"
        assert args.zoom == 3
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
        for title in ("Location:", "Imagery and output:", "Globe layout:", "Climate (Köppen-Geiger or Trewartha):",
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
        ('zoom = 9\n', "invalid choice"),
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
            koppen=False, koppen_alpha=0.45, route=None, route_legend=False,
            both_hemispheres=False, ice=False, ice_year=None, crop=None, no_cache=False,
            koppen_class=None, up=0.0, up_toward=None, trewartha=False, trewartha_class=None,
            elevation=False, elevation_alpha=0.8, soil=False, soil_class=None, soil_alpha=0.6,
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

    def test_soil_failure_still_saves_map_without_key_or_credit(self, tmp_path):
        with mock.patch.object(ortho.GeoAxes, "add_image", return_value=None),                 mock.patch.object(ortho, "add_soil_overlay", side_effect=ortho.SoilDataError("offline")),                 mock.patch.object(ortho, "add_soil_legend") as key,                 mock.patch.object(ortho, "_add_attribution") as attribution:
            result = ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), soil=True,
            )
        assert os.path.exists(result)
        key.assert_not_called()
        assert attribution.call_args.args[1] == [ortho.TILE_ATTRIBUTIONS["osm"]]

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
        # Google credits come from the Map Tiles API; everything else is fixed text
        assert set(ortho.TILE_ATTRIBUTIONS) | set(ortho.GOOGLE_MAP_TYPES) == set(ortho.TILE_PROVIDERS)

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
