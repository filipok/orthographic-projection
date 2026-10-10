"""Tests for elevation.py — Terrarium decoding, caching, sampling, the land mask and relief (no network)."""

import io
import math

import numpy as np
import pytest
import shapefile
from matplotlib.figure import Figure
from PIL import Image

import cartopy.crs as ccrs

import elevation

WORLD = 2 * math.pi * 6378137.0


def _encode(height):
    """Terrarium RGBA pixels for *height* (m)."""
    value = np.asarray(height, np.float64) + 32768
    r = np.floor(value / 256)
    g = np.floor(value - r * 256)
    b = np.round((value - r * 256 - g) * 256)
    rgba = np.stack([r, g, b, np.full_like(r, 255)], axis=-1)
    return rgba.astype(np.uint8)


class TestDecode:
    @pytest.mark.parametrize("height", [-10994.0, -0.5, 0.0, 1134.0, 8848.25])
    def test_round_trip(self, height):
        assert elevation.decode_terrarium(_encode([[height]]))[0, 0] == pytest.approx(height, abs=1 / 256)


class TestFetchTile:
    def test_downloads_once_then_reads_the_cache(self, tmp_path, monkeypatch):
        buf = io.BytesIO()
        Image.fromarray(_encode(np.full((4, 4), 250.0))[..., :3]).save(buf, "PNG")
        calls = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return Response(buf.getvalue())

        monkeypatch.setattr(elevation.urllib.request, "urlopen", urlopen)
        for _ in range(2):
            tile = elevation.fetch_tile((3, 2, 4), cache_dir=str(tmp_path))
            assert tile.shape == (4, 4, 4)
            assert elevation.decode_terrarium(tile)[0, 0] == 250
        assert calls == ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/4/3/2.png"]
        assert (tmp_path / "terrarium" / "4_3_2.png").is_file()

    def test_without_the_cache_nothing_is_read_or_written(self, tmp_path, monkeypatch):
        buf = io.BytesIO()
        Image.fromarray(_encode(np.full((4, 4), 250.0))[..., :3]).save(buf, "PNG")
        calls = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return Response(buf.getvalue())

        monkeypatch.setattr(elevation.urllib.request, "urlopen", urlopen)
        for _ in range(2):
            tile = elevation.fetch_tile((3, 2, 4), cache_dir=str(tmp_path), use_cache=False)
            assert elevation.decode_terrarium(tile)[0, 0] == 250
        assert len(calls) == 2 and not (tmp_path / "terrarium").exists()

    def test_failures_are_not_cached(self, tmp_path, monkeypatch):
        def urlopen(request, timeout):
            raise elevation.urllib.error.URLError("offline")

        monkeypatch.setattr(elevation.urllib.request, "urlopen", urlopen)
        with pytest.raises(OSError):
            elevation.fetch_tile((0, 0, 1), cache_dir=str(tmp_path))
        assert not (tmp_path / "terrarium").exists()


class TestElevationGrid:
    def test_samples_cell_centres_from_the_mercator_tiles(self, monkeypatch):
        # Zoom 1: four tiles, each one height: NW 100, NE 200, SW 300, SE 400
        heights = {(0, 0): 100, (1, 0): 200, (0, 1): 300, (1, 1): 400}
        monkeypatch.setattr(elevation, "fetch_tile",
                            lambda tile, cache_dir=None, **k: _encode(np.full((256, 256), heights[tile[:2]])))
        grid = elevation.elevation_grid((3, 6), west=-180, north=90, cell=60, zoom=1)
        # Cell centres at 60°N, 0° and 60°S, and from 150°W to 150°E
        assert grid[0].tolist() == [100] * 3 + [200] * 3
        assert grid[2].tolist() == [300] * 3 + [400] * 3

    def test_cells_beyond_the_mercator_limit_are_nan(self, monkeypatch):
        monkeypatch.setattr(elevation, "fetch_tile", lambda tile, cache_dir=None, **k: _encode(np.zeros((256, 256))))
        grid = elevation.elevation_grid((2, 1), west=-180, north=90, cell=4, zoom=1)
        assert np.isnan(grid[0, 0]) and grid[1, 0] == 0     # centres at 88° and 84°

    def test_failed_tile_is_a_data_error(self, monkeypatch):
        def fetch(tile, cache_dir=None, **k):
            raise OSError("offline")

        monkeypatch.setattr(elevation, "fetch_tile", fetch)
        with pytest.raises(elevation.ElevationDataError, match="offline"):
            elevation.elevation_grid((2, 2), -180, 90, 90, zoom=1)


# A 1° Mercator mosaic around the equator: 100 x 100 pixels over (0..1°E, 0..1°N)
EXTENT = (0.0, WORLD / 360, 0.0, WORLD / 360)


class TestHillshade:
    def test_flat_ground_has_the_light_altitude_shade(self):
        shade = elevation.hillshade(np.zeros((10, 10)), EXTENT)
        np.testing.assert_allclose(shade, math.sin(math.radians(45)), atol=1e-6)

    def test_slopes_facing_the_light_are_brighter(self):
        rows = np.arange(100, dtype=np.float32)[:, None] * np.ones((1, 100))
        rising_north = elevation.hillshade(rows * 50, EXTENT)        # rows run south to north
        rising_south = elevation.hillshade(rows[::-1] * 50, EXTENT)
        # Light from the north-west: a slope rising to the north faces south, away from it
        assert rising_south.mean() > math.sin(math.radians(45)) > rising_north.mean()


def _shapes(tmp_path, land, lakes=()):
    """A natural_earth stand-in serving *land* and *lakes* (lists of lon/lat rings)."""
    paths = {}
    for name, rings in (("land", land), ("lakes", lakes)):
        base = str(tmp_path / f"ne_50m_{name}")
        with shapefile.Writer(base + ".shp", shapeType=shapefile.POLYGON) as w:
            w.field("FID", "N")
            for ring in rings:
                w.poly([ring])
                w.record(0)
            if not rings:
                w.null()
                w.record(0)
        paths[name] = base + ".shp"
    return lambda resolution, category, name: paths[name]


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]   # clockwise, as shapefiles want


class TestLandRaster:
    def test_land_minus_lakes_rows_south_to_north(self, tmp_path, monkeypatch):
        monkeypatch.setattr(elevation, "_land_polygons", {})
        monkeypatch.setattr(elevation, "_lake_polygons", {})
        natural_earth = _shapes(tmp_path, land=[_square(0, 0, 0.5, 1)], lakes=[_square(0.1, 0.1, 0.3, 0.3)])
        mask = elevation.land_raster((100, 100), EXTENT, natural_earth=natural_earth)
        assert mask[50, 25] and not mask[50, 75]             # western half is land
        assert not mask[20, 20]                              # the lake, near the south-west corner
        assert mask[80, 20]                                  # land north of the lake

    def test_scale_reaches_natural_earth_and_is_cached_separately(self, tmp_path, monkeypatch):
        monkeypatch.setattr(elevation, "_land_polygons", {})
        monkeypatch.setattr(elevation, "_lake_polygons", {})
        shapes = _shapes(tmp_path, land=[_square(0, 0, 0.5, 1)], lakes=[])
        asked = []

        def natural_earth(resolution, category, name):
            asked.append(resolution)
            return shapes(resolution, category, name)

        for scale in ("10m", "10m", "50m"):
            elevation.land_raster((10, 10), EXTENT, natural_earth=natural_earth, scale=scale)
        assert asked == ["10m", "10m", "50m", "50m"]         # land and lakes, once per scale


class TestRelief:
    def test_colours_land_by_band_and_leaves_sea_and_gaps_clear(self, monkeypatch):
        heights = np.array([[-50, 150, 2500, 6000]], np.float32)
        mosaic = _encode(heights)
        mosaic[0, 3, 3] = 0                                  # a failed tile
        monkeypatch.setattr(elevation, "with_ice_surface", lambda h, extent, *a, **k: h)
        land = np.array([[False, True, True, True]])
        rgba = elevation.relief_rgba(mosaic, EXTENT, alpha=0.5, land=land)
        assert rgba[0, 0, 3] == 0 and rgba[0, 3, 3] == 0
        assert rgba[0, 1, 3] == rgba[0, 2, 3] == 128
        # Flat ground keeps its band colour (shading factor 1)
        assert tuple(rgba[0, 1, :3]) == elevation.ELEVATION_BANDS[0][2]
        assert tuple(rgba[0, 2, :3]) == elevation.ELEVATION_BANDS[4][2]

    @pytest.mark.parametrize("zoom, scale", [(5, "50m"), (6, "50m"), (7, "10m"), (10, "10m")])
    def test_close_views_use_the_fine_coastline(self, monkeypatch, zoom, scale):
        monkeypatch.setattr(elevation, "with_ice_surface", lambda h, extent, *a, **k: h)
        scales = []
        monkeypatch.setattr(elevation, "land_raster",
                            lambda shape, extent, scale: scales.append(scale) or np.ones(shape, bool))
        elevation.relief_rgba(_encode([[100.0]]), EXTENT, zoom=zoom)
        assert scales == [scale]

    def test_finer_tiles_are_shaded_less_steeply(self):
        assert elevation.shade_exaggeration(5) == 8
        assert elevation.shade_exaggeration(4) == 8
        assert elevation.shade_exaggeration(7) == pytest.approx(8 * 0.36)
        assert elevation.shade_exaggeration(10) == 1.0

    def test_land_below_sea_level_is_coloured(self, monkeypatch):
        monkeypatch.setattr(elevation, "with_ice_surface", lambda h, extent, *a, **k: h)
        rgba = elevation.relief_rgba(_encode([[-28.0]]), EXTENT, land=np.array([[True]]))
        assert rgba[0, 0, 3] > 0 and tuple(rgba[0, 0, :3]) == elevation.ELEVATION_BANDS[0][2]


class TestIceSurface:
    # Rows centred near 70°N over 1° of longitude, in Mercator metres
    Y70 = 6378137.0 * math.asinh(math.tan(math.radians(70)))
    POLAR = (0.0, WORLD / 360, Y70, Y70 + 1000)

    def test_raises_the_ice_sheet_but_not_valleys(self, monkeypatch):
        monkeypatch.setattr(elevation, "fetch_tile",
                            lambda tile, cache_dir=None, **k: _encode(np.full((256, 256), 3000.0)))
        bedrock = np.array([[-50.0, 2800.0]], np.float32)   # under the ice; a peak near the surface
        out = elevation.with_ice_surface(bedrock, self.POLAR)
        assert out.tolist() == [[3000.0, 2800.0]]

    def test_away_from_the_poles_nothing_is_fetched(self, monkeypatch):
        def fetch(tile, cache_dir=None, **k):
            raise AssertionError("fetched")

        monkeypatch.setattr(elevation, "fetch_tile", fetch)
        height = np.zeros((4, 4), np.float32)
        assert elevation.with_ice_surface(height, EXTENT) is height

    def test_offline_keeps_the_bedrock(self, monkeypatch):
        def fetch(tile, cache_dir=None, **k):
            raise OSError("offline")

        monkeypatch.setattr(elevation, "fetch_tile", fetch)
        bedrock = np.array([[-50.0]], np.float32)
        assert elevation.with_ice_surface(bedrock, self.POLAR).tolist() == [[-50.0]]


class TestTilesAndKey:
    def test_tile_source_serves_raw_tiles(self, monkeypatch):
        monkeypatch.setattr(elevation, "fetch_tile", lambda tile, cache_dir=None, timeout=30, **k: _encode(np.zeros((2, 2))))
        source = elevation.TerrariumTiles()
        img, extent, origin = source.get_image((0, 0, 1))
        assert img.shape == (2, 2, 4) and origin == "lower"
        assert extent == source.tileextent((0, 0, 1))

    def test_key_lists_the_bands(self):
        fig = Figure()
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        key = elevation.add_elevation_legend(ax)
        assert [t.get_text() for t in key.get_texts()] == [label for _, label, _ in elevation.ELEVATION_BANDS]
        assert key in ax.artists
