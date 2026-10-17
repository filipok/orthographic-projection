"""Tests for permafrost.py, population.py, livestock.py, timezones.py and the ocean layer of elevation.py (no network)."""

import io
import json

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure
from PIL import Image
from shapely.geometry import box

import elevation
import livestock as lv
import permafrost as pf
import population as pop
import timezones as tz
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _square(x0, y0, x1, y1):
    return np.array([(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)], float)


class TestPermafrost:
    def test_polygons_in_metres_fill_the_grid(self):
        shapes = [(_square(0, 0, 20_000, 10_000), [0], 4), (_square(20_000, 0, 30_000, 10_000), [0], 1)]
        grid = pf.rasterise(shapes, (0, 0, 30_000, 10_000), cell=5_000)
        assert grid.zones.tolist() == [[4, 4, 4, 4, 1, 1], [4, 4, 4, 4, 1, 1]]
        assert grid.extent == (0, 30_000, 0, 10_000)

    def test_colours_and_key(self):
        rgba = pf.permafrost_rgba(np.array([[0, 4]], np.uint8), alpha=1.0)
        assert rgba[0, 0, 3] == 0 and rgba[0, 1].tolist() == [*pf.ZONES[4][2], 255]
        legend = pf.add_permafrost_legend(_globe(80, 0))
        assert [t.get_text() for t in legend.get_texts()][0] == "Continuous: 90-100% of the ground"


COLORMAP = """<ColorMaps><ColorMap title="Population Density"><Entries>
<ColorMapEntry rgb="255,242,209" transparent="false" sourceValue="[0.0,1.0)" value="[0.0,1.0)" ref="2"/>
<ColorMapEntry rgb="250,171,77" transparent="false" sourceValue="[25.0,50.0)" value="[25.0,50.0)" ref="19"/>
<ColorMapEntry rgb="189,0,38" transparent="false" sourceValue="[1000.0,1000000.0)" value="[1000.0,1000000.0)" ref="38"/>
</Entries></ColorMap></ColorMaps>"""


class TestPopulation:
    @pytest.mark.parametrize("year, expected", [(None, 2020), ("2000", 2000), (2015, 2015)])
    def test_years(self, year, expected):
        assert pop.resolve_population_year(year) == expected

    def test_bad_year(self):
        with pytest.raises(ValueError, match="2000, 2005"):
            pop.resolve_population_year(2012)

    def test_colours_back_to_densities_then_bands(self):
        colormap = pop.parse_colormap(COLORMAP)
        mosaic = np.array([[[255, 242, 209, 255], [250, 171, 77, 255], [189, 0, 38, 255], [1, 2, 3, 255],
                            [250, 171, 77, 0]]], np.uint8)
        assert np.nan_to_num(pop.densities(mosaic, colormap), nan=-1).tolist() == [[0, 25, 1000, -1, -1]]
        rgba = pop.population_rgba(mosaic, alpha=1.0, colormap=colormap)
        assert rgba[0, 0, :3].tolist() == list(pop.DENSITY_BANDS[0][2])       # under 1
        assert rgba[0, 1, :3].tolist() == list(pop.DENSITY_BANDS[3][2])       # 25-50
        assert rgba[0, 2, :3].tolist() == list(pop.DENSITY_BANDS[-1][2])      # 1,000 and more
        assert rgba[0, 3, 3] == rgba[0, 4, 3] == 0                            # unknown colour, gap

    def test_colour_map_cached(self, tmp_path, monkeypatch):
        calls = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc) -> None:
                return None

        def fake(request, timeout=60):
            calls.append(request.full_url)
            return Response(COLORMAP.encode())

        monkeypatch.setattr(pop.urllib.request, "urlopen", fake)
        assert len(pop.load_colormap(2020, str(tmp_path))[0]) == 3
        pop.load_colormap(2020, str(tmp_path))
        assert len(calls) == 1

    def test_tiles_and_key(self):
        tiles = pop.population_tiles(2010)
        assert tiles.layer == "GPW_Population_Density_2010" and tiles.max_zoom == 7
        assert "/default/default/" in tiles._image_url((1, 2, 3))
        legend = pop.add_population_legend(_globe(), 2010)
        assert len(legend.get_texts()) == len(pop.DENSITY_BANDS) and "2010" in legend.get_title().get_text()
        assert "CC BY 4.0" in pop.population_attribution(2010)


class TestLivestock:
    @pytest.mark.parametrize("name, key", [("Cattle", "cattle"), ("cow", "cattle"), ("chicken", "chickens"),
                                           ("poultry", "chickens"), ("sheep", "sheep")])
    def test_names(self, name, key):
        assert lv.resolve_species(name) == key

    def test_unknown(self):
        with pytest.raises(ValueError, match="Unknown livestock"):
            lv.resolve_species("llamas")

    def test_cells_add_up_to_the_earth(self):
        areas = lv.cell_area_km2(180, 360)
        assert areas.sum() * 360 == pytest.approx(4 * np.pi * 6371.0072 ** 2, rel=1e-6)
        assert areas[90] > areas[0]

    def test_classified_by_density(self):
        heads = np.full((180, 360), np.nan, np.float32)
        area = lv.cell_area_km2(180, 360)[90]
        heads[90, :4] = [0.5 * area, 3 * area, 300 * area, 0]
        bands = lv.classify(heads, "cattle")
        assert bands[90, :4].tolist() == [0, 1, 7, 255] and bands[0, 0] == 255
        assert lv.classify(heads, "chickens")[90, 2].tolist() == 4             # poultry run on a larger scale

    def test_downloaded_through_dataverse_once(self, tmp_path, monkeypatch):
        image = io.BytesIO()
        Image.fromarray(np.full((180, 360), 5.0, np.float32)).save(image, "TIFF")
        listing = {"data": {"latestVersion": {"files": [
            {"dataFile": {"id": 1, "filename": "5_Ct_2015_Aw.tif"}},
            {"dataFile": {"id": 2, "filename": "6_Ct_2015_Da.tif"}}]}}}
        urls = []

        def fake(url, timeout=300):
            urls.append(url)
            return json.dumps(listing).encode() if "datasets" in url else image.getvalue()

        monkeypatch.setattr(lv, "_get", fake)
        grid = lv.load_livestock("cows", str(tmp_path))
        assert grid.species == "cattle" and grid.bands.shape == (180, 360)
        assert urls[1].endswith("/api/access/datafile/2")
        lv.load_livestock("cattle", str(tmp_path))
        assert len(urls) == 2

    def test_colours_and_key(self):
        rgba = lv.livestock_rgba(np.array([[0, 1, 255]], np.uint8), alpha=1.0)
        assert rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 255 and rgba[0, 2, 3] == 0
        labels = [t.get_text() for t in lv.add_livestock_legend(_globe(), "chickens").get_texts()]
        assert labels[0] == "10-50" and labels[-1] == "2,500 and more"


class TestTimeZones:
    def test_shades_alternate(self):
        assert tz.shade(1) != tz.shade(2) and tz.shade(1) == tz.shade(3) and tz.shade(5.5) == tz.SHADES[2]

    def test_grid_and_drawing(self):
        zones = [(0.0, "UTC", box(-7.5, -60, 7.5, 60)), (1.0, "UTC+01:00", box(7.5, -60, 22.5, 60)),
                 (5.5, "UTC+05:30", box(70, 5, 90, 30))]
        grid = tz.zone_grid(zones, cells_per_degree=1)
        assert grid[90, 180] == tz.SHADES.index(tz.shade(0)) + 1 and grid[90, 200] == tz.SHADES.index(tz.shade(1)) + 1
        assert grid[0, 0] == 0
        ax = _globe(10, 40)
        tz.draw_time_zones(ax, zones)
        assert sorted(t.get_text() for t in ax.texts) == ["UTC", "UTC +01", "UTC +05:30"]


class TestOcean:
    EXTENT = (0.0, 10.0, 0.0, 10.0)

    def _mosaic(self, heights):
        h = np.asarray(heights, np.float64) + 32768
        r = np.floor(h / 256)
        g = np.floor(h - r * 256)
        b = np.round((h - r * 256 - g) * 256)
        return np.stack([r, g, b, np.full_like(r, 255)], axis=-1).astype(np.uint8)

    def test_depth_bands_and_ice_age_land(self):
        heights = [[-50, -150, -3000, -6500, 10, -50]]
        sea = np.array([[True, True, True, True, False, False]])
        out = elevation.ocean_rgba(self._mosaic(heights), self.EXTENT, alpha=1.0, depth=True, ice_age=True, sea=sea)
        assert out[0, 4, 3] == 0 and out[0, 5, 3] == 0                 # land, and a lake below sea level, stay clear
        assert out[0, 0, :3].tolist() in (list(elevation.ICE_AGE_LAND_RGB), list(elevation.ICE_AGE_COAST_RGB))
        assert out[0, 1, 3] == 255 and out[0, 3, 3] == 255

    def test_depth_only(self):
        heights = np.full((5, 5), -3500.0)
        out = elevation.ocean_rgba(self._mosaic(heights), self.EXTENT, alpha=0.5, depth=True,
                                   sea=np.ones((5, 5), bool))
        assert (out[..., 3] == 128).all()
        assert abs(int(out[2, 2, 0]) - elevation.DEPTH_BANDS[4][2][0]) < 40   # 3,000-4,000 m, shaded

    def test_ice_age_coast_between_dry_and_wet(self):
        heights = np.full((1, 8), -500.0)
        heights[0, :4] = -60
        out = elevation.ocean_rgba(self._mosaic(heights), self.EXTENT, alpha=1.0, depth=False, ice_age=True,
                                   sea=np.ones((1, 8), bool))
        assert out[0, 0, :3].tolist() == list(elevation.ICE_AGE_LAND_RGB)
        assert out[0, 3, :3].tolist() == list(elevation.ICE_AGE_COAST_RGB)
        assert out[0, 6, 3] == 0                                         # still sea, and no depth asked for

    def test_key(self):
        both = elevation.add_ocean_legend(_globe(), depth=True, ice_age=True)
        assert len(both.get_texts()) == len(elevation.DEPTH_BANDS) + 1
        only = elevation.add_ocean_legend(_globe(), depth=False, ice_age=True)
        assert len(only.get_texts()) == 1 and "20,000 years" in only.get_title().get_text()
