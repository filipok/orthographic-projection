"""Tests for climate.py — periods, bands, reading and caching the means, resampling, masks and key (no network)."""

import io

import numpy as np
import pytest
import shapely
import matplotlib.text as mtext
from matplotlib.figure import Figure

import cartopy.crs as ccrs

import climate
import elevation
from trewartha import TrewarthaDataError
from wind import resolve_wind_period

TEMPERATURE = climate.CLIMATE_VARIABLES["temperature"]
PRECIPITATION = climate.CLIMATE_VARIABLES["precipitation"]
HUMIDITY = climate.CLIMATE_VARIABLES["humidity"]

# A 3 × 6 grid of 60° cells, from 180°W and 90°N
GEO = (-180.0, 90.0, 60.0)


class TestPeriodsAndBands:
    def test_periods_name_the_layer(self):
        assert climate.resolve_climate_period("humidity", "jja").months == (6, 7, 8)
        with pytest.raises(ValueError, match="Unknown precipitation period 'monsoon'"):
            climate.resolve_climate_period("precipitation", "monsoon")

    @pytest.mark.parametrize("variable", list(climate.CLIMATE_VARIABLES.values()))
    @pytest.mark.parametrize("period", ["year", "son", "march"])
    def test_one_colour_per_band(self, variable, period):
        edges = climate.band_edges(variable, resolve_wind_period(period))
        assert len(variable.colours) == len(edges) + 1 and list(edges) == sorted(edges)

    def test_precipitation_bands_scale_with_the_period(self):
        month, season, year = (climate.band_edges(PRECIPITATION, resolve_wind_period(p))[-1]
                               for p in ("july", "jja", "year"))
        assert (month, season, year) == (400, 1200, 4000)

    def test_freezing_is_a_band_edge(self):
        edges = climate.band_edges(TEMPERATURE, resolve_wind_period("year"))
        below = edges.index(0)                               # the band from -5 to 0 °C …
        assert climate._TEMPERATURE_COLOURS[below] == "#b0e0ec"     # … is the palest blue
        assert climate._TEMPERATURE_COLOURS[below + 1] == "#d3eba4"  # and 0 to 5 °C the palest green

    @pytest.mark.parametrize("variable, period, title", [
        (TEMPERATURE, "july", "Air temperature, July mean, 1981–2010 (°C)"),
        (PRECIPITATION, "year", "Precipitation, annual total, 1981–2010 (mm)"),
        (HUMIDITY, "djf", "Relative humidity, December–February mean, 1981–2010 (%)"),
    ])
    def test_titles(self, variable, period, title):
        assert climate.legend_title(variable, resolve_wind_period(period)) == title


@pytest.fixture
def chelsa(monkeypatch):
    """Serve month m of every variable as a 3 × 6 grid of the integer 1000 + m; returns the URLs read."""
    read = []

    def read_cog_level(url, level):
        read.append(url)
        month = int(url.rsplit("_", 3)[1])
        grid = np.full((3, 6), 1000 + month, np.uint16)
        grid[0, 0] = climate._NODATA
        return grid, GEO

    monkeypatch.setattr(climate, "read_cog_level", read_cog_level)
    return read


class TestLoadClimate:
    def test_downloaded_once_per_month_then_cached(self, tmp_path, chelsa):
        climate.load_climate(HUMIDITY, resolve_wind_period("djf"), str(tmp_path))
        climate.load_climate(HUMIDITY, resolve_wind_period("january"), str(tmp_path))
        assert len(chelsa) == 3 and all("/hurs/" in url and "_V.2.1.tif" in url for url in chelsa)
        assert len(list(tmp_path.glob("chelsa21_hurs_*.npz"))) == 3

    def test_temperature_is_the_mean_weighted_by_month_length(self, tmp_path, chelsa):
        values, geo = climate.load_climate(TEMPERATURE, resolve_wind_period("djf"), str(tmp_path))
        days = np.array([31, 31, 28.25])
        kelvin = (days * (1000 + np.array([12, 1, 2]))).sum() / days.sum() / 10
        assert values[1, 1] == pytest.approx(kelvin - 273.15, abs=1e-4)
        assert np.isnan(values[0, 0]) and geo == GEO

    def test_precipitation_is_the_total(self, tmp_path, chelsa):
        values, _ = climate.load_climate(PRECIPITATION, resolve_wind_period("year"), str(tmp_path))
        assert values[2, 3] == pytest.approx(sum(100 + m / 10 for m in range(1, 13)))

    def test_humidity_in_percent(self, tmp_path, chelsa):
        values, _ = climate.load_climate(HUMIDITY, resolve_wind_period("july"), str(tmp_path))
        assert values[1, 2] == pytest.approx(10.07)

    def test_download_failure(self, tmp_path, monkeypatch):
        def fail(url, level):
            raise TrewarthaDataError("offline")
        monkeypatch.setattr(climate, "read_cog_level", fail)
        with pytest.raises(climate.ClimateDataError, match="offline"):
            climate.load_climate(TEMPERATURE, resolve_wind_period("july"), str(tmp_path))
        assert not list(tmp_path.glob("*.npz"))


class TestResample:
    VALUES = np.arange(18, dtype=np.float32).reshape(3, 6)      # 6 × row + column

    def test_bilinear_between_cell_centres(self):
        view = climate.View(0.0, -150.0, -90.0, 0.0, 60.0)
        grid, extent = climate.resample(self.VALUES, GEO, view, 30.0)
        assert extent == (-150.0, -90.0, 0.0, 60.0)
        # Cells centred a quarter and three quarters of the way between the grid's centres
        assert grid == pytest.approx(np.array([[1.75, 2.25], [4.75, 5.25]]))

    def test_matches_the_grid_at_its_cell_centres(self):
        grid, _ = climate.resample(self.VALUES, GEO, climate.View(0.0, -180.0, 180.0, -90.0, 90.0), 20.0)
        assert grid[1, 1] == pytest.approx(0.0)                 # 60°N, 150°W
        assert grid[7, 16] == pytest.approx(17.0)               # 60°S, 150°E

    def test_wraps_across_the_date_line(self):
        values = np.zeros((3, 6), np.float32)
        values[:, 0], values[:, 5] = 10.0, 20.0                  # 150°W and 150°E
        grid, extent = climate.resample(values, GEO, climate.View(180.0, -10.0, 10.0, -5.0, 5.0), 1.0)
        assert extent == (-10.0, 10.0, -5.0, 5.0)
        assert grid[5, 10] == pytest.approx(15.0, abs=0.3)       # half way between them, at the date line
        assert grid[5, 0] > grid[5, 19]                         # nearer 150°E in the west

    def test_beyond_the_data_is_nan(self):
        geo = (-180.0, 84.0, 60.0)                              # data stops at 84°N, as CHELSA's does
        grid, _ = climate.resample(self.VALUES, geo, climate.View(0, -10, 10, 80, 90), 1.0)
        assert np.isnan(grid[:6]).all() and not np.isnan(grid[6:]).any()


@pytest.fixture
def coast(monkeypatch):
    """Land: a square at 170-179°E and one at 175-170°W, with a lake in the first."""
    land = [shapely.box(170, -10, 179, 10), shapely.box(-175, -10, -170, 10)]
    lakes = [shapely.box(172, -2, 174, 2)]
    monkeypatch.setattr(elevation, "_land_polygons", {"50m": land})
    monkeypatch.setattr(elevation, "_lake_polygons", {"50m": lakes})


class TestLandMask:
    def test_land_on_both_sides_of_the_date_line(self, coast):
        # 1° cells from 160°E to 160°W, rows from 10°N down to 10°S
        mask = climate.land_mask((20, 40), (-20.0, 20.0, -10.0, 10.0), 180.0)
        assert mask[15, 15] and mask[15, 13]                    # 175.5°E and 173.5°E, south of the lake
        assert not mask[10, 13]                                 # the lake at 173°E, 0°
        assert not mask[10, 21] and not mask[10, 23]            # sea at 178.5°W and 176.5°W
        assert mask[10, 27]                                     # 172.5°W
        assert not mask[10, 35]

    def test_far_side_polygons_are_skipped(self, coast):
        mask = climate.land_mask((10, 10), (-5.0, 5.0, -5.0, 5.0), 0.0)
        assert not mask.any()


class TestVisibleBox:
    def _ax(self, lon, lat, half=None):
        fig = Figure(figsize=(4, 4))
        ax = fig.add_subplot(projection=ccrs.Orthographic(lon, lat))
        if half is None:
            ax.set_global()
        else:
            ax.set_extent((-half, half, -half, half), crs=ax.projection)
        return ax

    def test_a_globe_on_the_equator_shows_a_hemisphere(self):
        view = climate.visible_box(self._ax(30, 0))
        assert view.centre_lon == pytest.approx(30)
        # Cartopy draws the globe's edge at 0.99999 of its radius, a quarter degree short of 90°
        assert (view.west, view.east) == pytest.approx((-90.25, 90.25), abs=0.05)
        assert (view.south, view.north) == pytest.approx((-90, 90), abs=0.6)

    def test_a_pole_in_view_takes_every_longitude(self):
        view = climate.visible_box(self._ax(100, 60))
        assert (view.west, view.east, view.north) == (-180, 180, 90)
        assert view.south == pytest.approx(-30.25, abs=0.05)

    def test_a_zoomed_map_across_the_date_line(self):
        view = climate.visible_box(self._ax(178, -20, half=1_000_000))
        assert view.centre_lon == pytest.approx(178)
        assert -12 < view.west < -9 and 9 < view.east < 12      # about ±9.5° of longitude, plus the margin
        assert view.south == pytest.approx(-29.5, abs=0.5) and view.north == pytest.approx(-10.5, abs=0.5)


class TestDrawing:
    def test_rgba_bands_and_clear_cells(self):
        values = np.array([[-50.0, -2.0, 3.0, 40.0, np.nan]])
        mask = np.array([[True, True, False, True, True]])
        rgba = climate.climate_rgba(values, climate._TEMPERATURE_EDGES, TEMPERATURE.colours, 0.5, mask)
        assert rgba[0, 0].tolist() == [0x3f, 0x0d, 0x5c, 128]
        assert rgba[0, 1].tolist() == [0xb0, 0xe0, 0xec, 128]
        assert rgba[0, 3].tolist() == [0x7f, 0x16, 0x20, 128]
        assert rgba[0, 2, 3] == 0 and rgba[0, 4, 3] == 0

    def _overlay(self, monkeypatch, coast_scale, **axes):
        values = np.full((3, 6), 12.0, np.float32)
        monkeypatch.setattr(climate, "load_climate", lambda variable, period, cache_dir=None: (values, GEO))
        scales = []
        monkeypatch.setattr(climate, "land_mask", lambda shape, extent, centre, scale: (
            scales.append(scale), np.ones(shape, bool))[1])
        fig = Figure(figsize=(4, 4))
        ax = fig.add_subplot(projection=ccrs.Orthographic(10, 46))
        half = axes.get("half")
        if half:
            ax.set_extent((-half, half, -half, half), crs=ax.projection)
        else:
            ax.set_global()
        climate.add_climate_overlay(ax, TEMPERATURE, resolve_wind_period("july"), alpha=0.7,
                                    regrid_shape=400, zoom=axes.get("zoom", 3))
        (image,) = ax.get_images()
        return image, scales

    def test_globe_overlay_at_the_grid_resolution(self, monkeypatch):
        image, scales = self._overlay(monkeypatch, "50m")
        assert image.get_zorder() == 5 and scales == ["50m"]

    def test_zoomed_overlay_is_finer_with_the_fine_coast(self, monkeypatch):
        image, scales = self._overlay(monkeypatch, "10m", half=500_000, zoom=8)
        assert scales == ["10m"]
        assert image.get_array().shape[1] > 100                  # resampled well below the 60° cells

    def test_key(self):
        fig = Figure(figsize=(8, 8))
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        ax.set_global()
        key = climate.add_climate_legend(ax, PRECIPITATION, resolve_wind_period("jja"))
        assert key.get_title().get_text() == "Precipitation, June–August total, 1981–2010 (mm)"
        assert key in ax.artists
        fig.savefig(io.BytesIO(), format="png", dpi=20)           # the colour bar draws
        labels = [t.get_text() for t in key.findobj(mtext.Text)]
        assert "25" in labels and "1,200" in labels

    @pytest.mark.parametrize("value, text", [(-5, "−5"), (1000, "1,000"), (0, "0")])
    def test_numbers(self, value, text):
        assert climate._number(value) == text
