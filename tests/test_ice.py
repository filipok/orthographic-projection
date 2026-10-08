"""Tests for ice.py — sea ice download/caching, shape loading and drawing (no network)."""

import datetime
import os
import urllib.error
import zipfile

import cartopy.crs as ccrs
import matplotlib
import pytest
import shapefile

import ice

matplotlib.use("Agg")

TODAY = datetime.date(2026, 10, 8)

# Antarctica as Natural Earth stores it: up from the pole at -180°, along
# 70°S (densely, as in the real data) and back down to the pole at 180°
ANTARCTICA = (
    [[-180, -90], [-180, -70]]
    + [[lon, -70] for lon in range(-170, 181, 10)]
    + [[180, -90], [-180, -90]]
)


def _write_shapefile(path_base, polygons):
    """Write *polygons* (lists of clockwise [x, y] rings) to a shapefile at *path_base*.

    The ".shp" is spelled out: pyshp would read the ".0" of a name ending in
    "v4.0" as the extension and write the files under a different name.
    """
    with shapefile.Writer(path_base + ".shp", shapeType=shapefile.POLYGON) as w:
        w.field("FID", "N")
        for i, rings in enumerate(polygons):
            w.poly(rings)
            w.record(i)


def _write_extent_zip(cache_dir, hemisphere, year, polygons=None):
    """Write a cached extent zip like NSIDC's for *hemisphere* / *year*."""
    name = ice._extent_name(hemisphere, year)
    base = os.path.join(cache_dir, "src", name)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    # A 500 km square of sea ice in polar stereographic metres (clockwise ring)
    square = [[0, 0], [0, 500_000], [500_000, 500_000], [500_000, 0], [0, 0]]
    _write_shapefile(base, polygons or [[square]])
    zip_path = os.path.join(cache_dir, name + ".zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for ext in (".shp", ".shx", ".dbf"):
            zf.write(base + ext, name + ext)
    return zip_path


def _http_error(code):
    return urllib.error.HTTPError("https://example.test", code, "error", {}, None)


# ===================================================================
# ensure_sea_ice_extent
# ===================================================================


class TestEnsureSeaIceExtent:
    def test_urls_follow_the_nsidc_layout(self):
        assert ice._extent_url("N", 2026) == (
            "https://noaadata.apps.nsidc.org/NOAA/G02135/north/monthly/shapefiles/"
            "shp_extent/03_Mar/extent_N_202603_polygon_v4.0.zip"
        )
        assert ice._extent_url("S", 2025).endswith(
            "/south/monthly/shapefiles/shp_extent/09_Sep/extent_S_202509_polygon_v4.0.zip"
        )

    def test_cached_file_is_used_without_downloading(self, tmp_path, monkeypatch):
        cached = _write_extent_zip(str(tmp_path), "N", 2026)
        monkeypatch.setattr(ice, "_download", lambda *a, **k: pytest.fail("downloaded"))
        assert ice.ensure_sea_ice_extent("N", cache_dir=str(tmp_path), today=TODAY) == (cached, 2026)

    def test_unpublished_year_falls_back_to_the_previous_one(self, tmp_path, monkeypatch):
        tried = []

        def fake_download(url, dest, timeout=60):
            tried.append(url)
            if "202609" in url:
                raise _http_error(404)
            _write_extent_zip(str(tmp_path), "S", 2025)

        monkeypatch.setattr(ice, "_download", fake_download)
        path, year = ice.ensure_sea_ice_extent("S", cache_dir=str(tmp_path), today=TODAY)
        assert year == 2025
        assert path.endswith("extent_S_202509_polygon_v4.0.zip")
        assert len(tried) == 2

    def test_offline_uses_an_older_cached_year(self, tmp_path, monkeypatch):
        cached = _write_extent_zip(str(tmp_path), "N", 2025)

        def offline(url, dest, timeout=60):
            raise urllib.error.URLError("no network")

        monkeypatch.setattr(ice, "_download", offline)
        assert ice.ensure_sea_ice_extent("N", cache_dir=str(tmp_path), today=TODAY) == (cached, 2025)

    def test_server_error_is_reported(self, tmp_path, monkeypatch):
        def server_error(url, dest, timeout=60):
            raise _http_error(500)

        monkeypatch.setattr(ice, "_download", server_error)
        with pytest.raises(ice.IceDataError, match="500"):
            ice.ensure_sea_ice_extent("N", cache_dir=str(tmp_path), today=TODAY)

    def test_nothing_available_names_the_years_tried(self, tmp_path, monkeypatch):
        def not_found(url, dest, timeout=60):
            raise _http_error(404)

        monkeypatch.setattr(ice, "_download", not_found)
        with pytest.raises(ice.IceDataError, match="2026, 2025, 2024.*--ice-year"):
            ice.ensure_sea_ice_extent("N", cache_dir=str(tmp_path), today=TODAY)

    def test_explicit_year_tries_only_that_year(self, tmp_path, monkeypatch):
        tried = []

        def not_found(url, dest, timeout=60):
            tried.append(url)
            raise _http_error(404)

        monkeypatch.setattr(ice, "_download", not_found)
        with pytest.raises(ice.IceDataError, match="2001"):
            ice.ensure_sea_ice_extent("S", year=2001, cache_dir=str(tmp_path), today=TODAY)
        assert len(tried) == 1 and "200109" in tried[0]

    def test_non_zip_download_is_rejected(self, tmp_path, monkeypatch):
        def html_page(url, dest, timeout=60):
            with open(dest, "w") as fh:
                fh.write("<html>not a zip</html>")

        monkeypatch.setattr(ice, "_download", html_page)
        with pytest.raises(ice.IceDataError, match="valid zip"):
            ice.ensure_sea_ice_extent("N", year=2026, cache_dir=str(tmp_path), today=TODAY)
        assert not os.listdir(tmp_path) or not any(f.endswith(".zip") for f in os.listdir(tmp_path))

    def test_bad_hemisphere(self, tmp_path):
        with pytest.raises(ValueError):
            ice.ensure_sea_ice_extent("E", cache_dir=str(tmp_path))


# ===================================================================
# Shape loading
# ===================================================================


class TestReadZipShapefile:
    def test_reads_polygons_and_drops_zero_area_slivers(self, tmp_path):
        square = [[0, 0], [0, 500_000], [500_000, 500_000], [500_000, 0], [0, 0]]
        sliver = [[0, 0], [0, 0], [100, 0], [0, 0]]
        zip_path = _write_extent_zip(str(tmp_path), "N", 2026, polygons=[[square], [sliver]])
        (geom,) = ice._read_zip_shapefile(zip_path)
        assert geom.area == pytest.approx(500_000 ** 2)

    def test_zip_without_shapefile_is_rejected(self, tmp_path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("readme.txt", "no shapes")
        with pytest.raises(ice.IceDataError, match="no shapefile"):
            ice._read_zip_shapefile(str(zip_path))


class TestPolarLandIce:
    def _natural_earth(self, tmp_path):
        layers = {
            "glaciated_areas": [
                # Antarctica: runs along the South Pole from 180° to -180°
                [ANTARCTICA],
                # A Greenland-like ice cap
                [[[-50, 70], [-40, 75], [-30, 70], [-50, 70]]],
                # An Alpine glacier, too far from the poles
                [[[7, 46], [7.5, 46.5], [8, 46], [7, 46]]],
                # The zero-area sliver that projects as the whole globe
                [[[-45.6, 82.78], [-45.3, 82.78], [-45.5, 82.78], [-45.6, 82.78]]],
            ],
            "antarctic_ice_shelves_polys": [
                [[[160, -78], [160, -77], [175, -77], [175, -78], [160, -78]]],
            ],
        }
        paths = {}
        for name, polygons in layers.items():
            base = str(tmp_path / f"ne_10m_{name}")
            _write_shapefile(base, polygons)
            paths[name] = base + ".shp"
        return lambda resolution, category, name: paths[name]

    def test_keeps_polar_ice_and_reprojects_shapes_on_the_pole(self, tmp_path):
        layers = ice.polar_land_ice(natural_earth=self._natural_earth(tmp_path))
        by_crs = {type(crs).__name__: geoms for geoms, crs in layers}
        # Greenland and the ice shelf stay in lon/lat; Alps and the sliver are dropped
        assert len(by_crs["PlateCarree"]) == 2
        # Antarctica is drawn from polar stereographic, centred on the pole
        (antarctica,) = by_crs["SouthPolarStereo"]
        assert antarctica.is_valid
        assert antarctica.contains(antarctica.centroid)
        minx, miny, maxx, maxy = antarctica.bounds
        assert minx < 0 < maxx and miny < 0 < maxy


# ===================================================================
# Drawing and credits
# ===================================================================


class TestDrawIce:
    def _layers(self):
        from shapely.geometry import box
        return ice.IceLayers(
            sea_ice=((tuple([box(0, 0, 1_000_000, 1_000_000)]), ice.SEA_ICE_CRS["N"]),),
            land_ice=((tuple([box(-50, 70, -30, 80)]), ccrs.PlateCarree()),),
            years={"N": 2026, "S": 2025},
        )

    def test_sea_ice_then_land_ice_both_opaque(self):
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 90)})
        try:
            ice.draw_ice(ax, self._layers(), zorder=3)
            sea, land = ax.collections
            assert mcolors.same_color(sea.get_facecolor()[0], ice.SEA_ICE_COLOR)
            assert mcolors.same_color(land.get_facecolor()[0], ice.LAND_ICE_COLOR)
            assert sea.get_facecolor()[0][3] == 1.0 and land.get_facecolor()[0][3] == 1.0
            assert sea.get_zorder() == land.get_zorder() == 3
        finally:
            plt.close(fig)

    def test_attribution_names_the_maxima_shown(self):
        assert self._layers().attribution == (
            "Sea ice: NSIDC Sea Ice Index v4, extent March 2026 (Arctic) "
            "and September 2025 (Antarctic)"
        )
