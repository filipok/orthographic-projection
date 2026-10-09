"""Tests for rotation.py — orthographic globes with any bearing at the top (no network)."""

import math

import cartopy.crs as ccrs
import matplotlib
import numpy as np
import pytest
from pyproj.geod import Geod

import rotation

matplotlib.use("Agg")

_SPHERE = Geod(a=rotation.EARTH_RADIUS_M, f=0)


def _screen_angle(proj, lat, lon, bearing):
    """Clockwise angle from screen-up of a short step from (lat, lon) along *bearing*."""
    cx, cy = proj.transform_point(lon, lat, ccrs.PlateCarree())
    step_lon, step_lat, _ = _SPHERE.fwd(lon, lat, bearing, 300_000)
    x, y = proj.transform_point(step_lon, step_lat, ccrs.PlateCarree())
    return math.degrees(math.atan2(x - cx, y - cy)) % 360


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


CASES = [
    (51.5, -0.13, 90),      # London, east up
    (-33.9, 151.2, 180),    # Sydney, south up
    (31.2, 121.5, 45.5),    # Shanghai, a fractional bearing
    (89.5, 10.0, 300),      # half a degree from the North Pole
    (-89.5, 10.0, 30),      # and from the South Pole
    (0.0, 179.9, 270),      # on the date line
]


class TestGlobeProjection:
    def test_north_up_is_cartopys_own_orthographic(self):
        proj = rotation.globe_projection(51.5, -0.13, 0)
        assert type(proj) is ccrs.Orthographic
        assert type(rotation.globe_projection(51.5, -0.13, 360)) is ccrs.Orthographic

    @pytest.mark.parametrize("lat, lon, up", CASES)
    def test_centre_is_in_the_middle_and_the_bearing_points_up(self, lat, lon, up):
        proj = rotation.globe_projection(lat, lon, up)
        assert isinstance(proj, rotation.RotatedOrthographic)
        cx, cy = proj.transform_point(lon, lat, ccrs.PlateCarree())
        assert abs(cx) < 1 and abs(cy) < 1                          # metres
        assert _angle_diff(_screen_angle(proj, lat, lon, up), 0) < 0.01
        # north turns the other way: at 360° - up on the screen
        assert _angle_diff(_screen_angle(proj, lat, lon, 0), 360 - up) < 0.01

    def test_far_side_is_hidden_and_the_disc_has_the_earths_radius(self):
        proj = rotation.globe_projection(51.5, -0.13, 90)
        hidden = proj.transform_point(179.87, -51.5, ccrs.PlateCarree())   # the antipode
        assert not np.isfinite(hidden).all()
        assert proj.x_limits == proj.y_limits == (-rotation.EARTH_RADIUS_M, rotation.EARTH_RADIUS_M)
        assert proj.boundary.bounds == pytest.approx(
            (-rotation.EARTH_RADIUS_M, -rotation.EARTH_RADIUS_M,
             rotation.EARTH_RADIUS_M, rotation.EARTH_RADIUS_M), rel=1e-6)

    def test_lines_and_areas_draw_on_a_rotated_globe(self, tmp_path):
        import matplotlib.pyplot as plt
        from shapely.geometry import box

        fig, ax = plt.subplots(subplot_kw={"projection": rotation.globe_projection(51.5, -0.13, 135)})
        try:
            ax.set_global()
            ax.plot([-10, 30, 100], [40, 50, 10], transform=ccrs.Geodetic())
            ax.add_geometries([box(-20, 30, 40, 70)], crs=ccrs.PlateCarree(), facecolor="gold")
            ax.gridlines()
            fig.savefig(tmp_path / "rotated.png", dpi=30)
        finally:
            plt.close(fig)
        assert (tmp_path / "rotated.png").stat().st_size > 0


class TestBearings:
    @pytest.mark.parametrize("bearing, expected", [
        (0, 0), (360, 0), (-90, 270), (450, 90), (-1e-17, 0), (180.5, 180.5),
    ])
    def test_normalise(self, bearing, expected):
        assert rotation.normalise_bearing(bearing) == pytest.approx(expected)

    def test_initial_bearing(self):
        # London to Mecca: the qibla from London is about 119°
        assert rotation.initial_bearing(51.5, -0.13, 21.42, 39.83) == pytest.approx(119, abs=1)
        assert rotation.initial_bearing(0, 0, 10, 0) == pytest.approx(0, abs=1e-6)
        assert rotation.initial_bearing(0, 0, 0, -10) == pytest.approx(270, abs=1e-6)

    @pytest.mark.parametrize("target", [(51.5, -0.13), (-51.5, 179.87)])
    def test_bearing_to_the_centre_or_antipode_is_undefined(self, target):
        with pytest.raises(ValueError, match="antipode"):
            rotation.initial_bearing(51.5, -0.13, *target)

    def test_far_globe_keeps_the_same_point_at_the_top(self):
        lat, lon, up = 51.5, -0.13, 90
        anti_lat, anti_lon = -lat, lon + 180
        far_up = rotation.far_side_up(lat, lon, up, anti_lat, anti_lon)
        top_lat, top_lon = rotation._rotated_pole(lat, lon, up)
        x, y = rotation.globe_projection(anti_lat, anti_lon, far_up).transform_point(
            top_lon, top_lat, ccrs.PlateCarree())
        assert abs(x) < 1 and y == pytest.approx(rotation.EARTH_RADIUS_M, abs=1)

    def test_far_globe_stays_north_up_without_rotation(self):
        assert rotation.far_side_up(51.5, -0.13, 0, -51.5, 179.87) == 0
