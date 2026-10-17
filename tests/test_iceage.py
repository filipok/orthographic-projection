"""Tests for iceage.py — reading the PaleoMIST margins, closing Antarctica round the pole, and drawing (no network)."""

import io
import zipfile

import numpy as np
import pytest
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

import iceage
from rotation import globe_projection

ONE = """# @VGMT1.0 @GPOLYGON
# @NID1|AV_Time
>
# @D2|20.0
0 50
10 50
10 60
0 60
0 50
"""
TWO = """# @VGMT1.0 @GPOLYGON
>
# @D1
# @P
0 0
20 0
20 20
0 20
0 0
# @H
5 5
10 5
10 10
5 10
5 5
>
# @D2
# @P
30 0
31 0
31 1
30 0
"""


def _antarctic_ring():
    """A margin round the pole, in longitude order from 150°E, as PaleoMIST's runs."""
    lon = np.concatenate([np.arange(150, 180, 10.0), np.arange(-180, 150, 10.0)])
    return np.column_stack([np.append(lon, lon[0]), np.full(len(lon) + 1, -65.0)])


def _archive(texts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for region, text in texts.items():
            zf.writestr(iceage.MEMBER.format(region=region, years=iceage.YEARS_AGO), text)
    return buf.getvalue()


class TestReading:
    def test_one_polygon_without_its_mark(self):
        (polygon,) = iceage.parse_gmt(ONE)
        assert polygon.bounds == (0, 50, 10, 60)

    def test_polygons_and_holes(self):
        big, small = iceage.parse_gmt(TWO)
        assert len(big.interiors) == 1 and big.area == pytest.approx(400 - 25)
        assert small.bounds == (30, 0, 31, 1)

    def test_antarctica_closed_through_the_pole(self):
        polygon = iceage.around_the_pole(_antarctic_ring())
        assert polygon.is_valid
        assert polygon.contains(shapely.Point(0, -89)) and not polygon.contains(shapely.Point(0, -60))

    def test_margins(self):
        ring = "\n".join(f"{x} {y}" for x, y in _antarctic_ring())
        sheets = iceage.read_margins({"Eurasia": ONE, "Antarctica": "# @P\n" + ring})
        assert len(sheets) == 2 and sheets[1].contains(shapely.Point(45, -88))


class TestDrawing:
    def test_grid_covers_the_sheets(self):
        sheets = iceage.read_margins({"Eurasia": ONE})
        grid = iceage.ice_sheet_grid(sheets, cells_per_degree=1)
        assert grid.sum() == 100 and grid[90 - 55, 180 + 5]

    def test_outline_leaves_out_the_edges_added_round_the_pole(self):
        polygon = iceage.around_the_pole(_antarctic_ring())
        lines = iceage.margin_lines(polygon)
        coords = np.concatenate([np.asarray(part.coords) for part in shapely.get_parts(lines)])
        assert (coords[:, 1] > -89).all()                          # nothing along the pole's edge
        assert shapely.length(lines) == pytest.approx(360)          # once round the pole at -65°
        assert (coords[np.abs(coords[:, 0]) == 180, 1] == -65).all()  # no cut down the date line

    def test_drawn_filled_and_outlined(self):
        ax = Figure(figsize=(4, 4)).add_subplot(projection=globe_projection(55, 5))
        assert isinstance(ax, GeoAxes)
        ax.set_global()
        iceage.draw_ice_sheets(ax, iceage.read_margins({"Eurasia": ONE}), regrid_shape=100)
        assert len(ax.images) == 1 and any(isinstance(c, LineCollection) for c in ax.collections)


class TestCache:
    def test_read_once_from_the_archive(self, tmp_path, monkeypatch):
        opened = []

        def fake_remote(url):
            opened.append(url)
            return io.BytesIO(_archive({region: ONE for region in iceage.REGIONS}))

        monkeypatch.setattr(iceage, "HttpRangeFile", fake_remote)
        sheets = iceage.load_ice_sheets(str(tmp_path))
        assert len(sheets) == 4 and opened == [iceage.PALEOMIST_URL]
        iceage.load_ice_sheets(str(tmp_path))
        assert len(opened) == 1

    def test_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(iceage, "HttpRangeFile", lambda url: io.BytesIO(b"not a zip"))
        with pytest.raises(iceage.IceAgeDataError, match="PaleoMIST"):
            iceage.load_ice_sheets(str(tmp_path))
