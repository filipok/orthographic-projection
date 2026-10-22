"""Tests for frame.py: the disc or rectangle a map is cut to."""

import math

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import frame
from rotation import globe_projection


def _map(rectangle: bool, half_w: float = 1_000_000.0, half_h: float = 500_000.0):
    proj = globe_projection(45, 25)
    ax = Figure(figsize=(12, 12)).add_subplot(projection=proj)
    assert isinstance(ax, GeoAxes)
    ax.set_extent((-half_w, half_w, -half_h, half_h), crs=proj)
    if rectangle:
        frame.set_rectangle(ax)
    return ax


class TestAspect:
    @pytest.mark.parametrize("text, aspect", [("3:2", 1.5), ("2:3", 2 / 3), ("1.414:1", 1.414), (" 4 x 3 ", 4 / 3),
                                              ("1:1", 1.0), ("1.5", 1.5), (1.5, 1.5)])
    def test_shapes(self, text, aspect):
        assert frame.parse_aspect(text) == pytest.approx(aspect)

    @pytest.mark.parametrize("text", ["", "wide", "3:", ":2", "0:1", "1:0", "-3:2", "5:1", "1:5"])
    def test_mistakes(self, text):
        with pytest.raises(ValueError, match="rectangle"):
            frame.parse_aspect(text)

    def test_the_longer_side_is_the_diameter(self):
        assert frame.half_sides(900.0, 1.5) == (900.0, 600.0)
        assert frame.half_sides(900.0, 0.5) == (450.0, 900.0)

    def test_corners_stay_on_the_globe(self):
        r = 6_371.0
        # A square's corners are √2 further out: sin(d / R) may be at most 1 / √2, so d up to R·π/4
        assert frame.max_rectangle_radius_km(1.0, r) == pytest.approx(r * math.pi / 4)
        assert frame.max_rectangle_radius_km(1.5, r) == frame.max_rectangle_radius_km(2 / 3, r)
        assert frame.max_rectangle_radius_km(4.0, r) > frame.max_rectangle_radius_km(1.5, r)


class TestInside:
    def test_a_disc_leaves_its_corners_out(self):
        ax = _map(rectangle=False, half_w=1e6, half_h=1e6)
        assert not frame.is_rectangle(ax) and frame.coverage(ax) == pytest.approx(math.pi / 4)
        assert frame.inside(ax, np.array([0.0, 0.7e6, 0.95e6]), np.array([0.0, 0.7e6, 0.0])).tolist() == \
            [True, True, True]
        assert not frame.inside(ax, 0.8e6, 0.8e6)
        assert not frame.inside(ax, 0.95e6, 0.0, margin=0.9)

    def test_a_rectangle_keeps_them(self):
        ax = _map(rectangle=True)
        assert frame.is_rectangle(ax) and frame.coverage(ax) == 1.0
        assert frame.inside(ax, np.array([0.99e6, -0.99e6, 0.0]), np.array([0.49e6, -0.49e6, 0.51e6])).tolist() == \
            [True, True, False]
        assert not frame.inside(ax, 0.95e6, 0.0, margin=0.9)

    def test_boxes_in_pixels(self):
        for rectangle in (False, True):
            ax = _map(rectangle, half_w=1e6, half_h=1e6)
            to_px = ax.transData.transform
            (cx, cy), (corner_x, corner_y) = to_px((0, 0)), to_px((0.9e6, 0.9e6))
            middle = (cx - 5, cy - 5, cx + 5, cy + 5)
            corner = (corner_x - 5, corner_y - 5, corner_x + 5, corner_y + 5)
            assert frame.box_inside(ax, middle)
            assert frame.box_inside(ax, corner) is rectangle
            assert not frame.box_inside(ax, corner, margin=0.8)
