"""Tests for ortho.py — unit-level tests that do NOT hit the network."""

import argparse
import os
import sys
import types
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Make sure ortho is importable from repo root
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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

    def test_image_for_domain_calls_inner(self):
        inner = self._make_fake_source()
        buffered = ortho.BufferedTileSource(inner, tile_buffer_factor=0.5)
        domain = mock.MagicMock()
        buffered.image_for_domain(domain, target_z=3)
        inner.image_for_domain.assert_called_once()


# ===================================================================
# create_tile_source
# ===================================================================


class TestCreateTileSource:
    def test_osm(self):
        src = ortho.create_tile_source("osm")
        assert isinstance(src, ortho.BufferedTileSource)

    def test_google(self):
        src = ortho.create_tile_source("google")
        assert isinstance(src, ortho.BufferedTileSource)

    def test_google_satellite(self):
        src = ortho.create_tile_source("google_satellite")
        assert isinstance(src, ortho.BufferedTileSource)

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
        result = ortho.build_output_filename("paris", "google satellite", 5)
        assert result == "orthographic_map_paris_google_satellite_z5.png"


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
        import cartopy

        with mock.patch("os.makedirs"):
            ortho.configure_tile_cache(None)
            assert cartopy.config["data_dir"] == ortho.DEFAULT_CACHE_DIR


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
        assert args.dpi == 600
        assert args.output is None
        assert args.output_dir is None
        assert args.cache_dir is None

    def test_city_and_lat_mutually_exclusive(self):
        with pytest.raises(SystemExit):
            self._parse(["--city", "nyc", "--lat", "40.0"])


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
