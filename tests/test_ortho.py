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

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
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
    """Key files are loaded like newsgrab's: first file to set a variable wins."""

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

    def test_both_hemispheres_flag(self):
        assert self._parse(["--city", "nyc"]).both_hemispheres is False
        assert self._parse(["--city", "nyc", "--both-hemispheres"]).both_hemispheres is True


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
            koppen=False, koppen_alpha=0.45, route=None, route_legend=False, both_hemispheres=False, no_cache=False,
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
                mock.patch.object(ortho, "add_route_legend") as legend:
            ortho.generate_orthographic_map(
                lat=0, lon=0, output_filename="m.png", zoom=1, dpi=20,
                output_dir=str(tmp_path), routes=[route], route_legend=True, koppen=koppen,
            )
        legend.assert_called_once()
        assert legend.call_args.kwargs["y"] == expected_y

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
