"""Tests for hydrology.py — rivers, merging HydroBASINS into river basins, naming and drawing them (no network)."""

import cartopy.crs as ccrs
import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from shapely.geometry import LineString, box

import hydrology as h
from borders import Polity
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _sub(hybas, main, area, endo=0, coast=0):
    return {"HYBAS_ID": hybas, "MAIN_BAS": main, "SUB_AREA": area, "ENDO": endo, "COAST": coast}


class TestBasins:
    RECORDS = [
        _sub(1, 1, 80_000), _sub(2, 1, 50_000, endo=1),    # a river with a closed hollow inside: still to the sea
        _sub(3, 3, 120_000, endo=2),                         # an inland sink
        _sub(4, 4, 500_000, coast=1),                        # a coastal strip
        _sub(5, 5, 20_000),                                  # too small
    ]
    OUTLINES = [box(0, 0, 1, 1), box(1, 0, 2, 1), box(5, 5, 6, 6), box(10, 10, 20, 20), box(30, 0, 31, 1)]

    def test_merged_by_outlet(self):
        basins = h.merge_basins(self.RECORDS, self.OUTLINES)
        assert [(b.area, b.inland) for b in basins] == [(130_000, False), (120_000, True)]
        assert basins[0].outline.equals(box(0, 0, 2, 1)) and basins[0].outlet.equals(box(0, 0, 1, 1))

    def test_named_by_the_river_at_the_mouth(self):
        basin = h.Basin(1.0, False, box(0, 0, 10, 10), box(0, 0, 1, 10))
        rivers = [h.River("Tributary", 1, LineString([(5, 0), (5, 10)])),     # longer, but not at the mouth
                  h.River("Main", 2, LineString([(0.5, 5), (3, 5)])),
                  h.River("", 0, LineString([(0.5, 1), (9, 1)]))]
        assert h.name_basins([basin], rivers) == ["Main"]
        far = h.Basin(1.0, False, box(50, 50, 51, 51), box(50, 50, 51, 51))
        assert h.name_basins([far], rivers) == [""]

    def test_names_corrected_by_place_not_by_name(self):
        outlines = [box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 50, 10)]
        names = h.corrected_names(["Volga", "Volga", "Huang"], outlines,
                                  corrections=[(25, 5, "Neva"), (45, 5, "Yellow"), (100, 5, "Nowhere")])
        assert names == ["Volga", "Neva", "Yellow"]

    def test_inland_basins_are_grey(self):
        basins = [Polity("Big", "basin0", "Big", False, box(0, 0, 10, 10)),
                  Polity("", h.INLAND_GROUP, "Inland", False, box(10, 0, 20, 10))]
        colours = h.basin_colours(basins)
        assert colours[h.INLAND_GROUP] == h.INLAND_RGB and colours["basin0"] != h.INLAND_RGB

    def test_key_names_the_largest_and_inland_last(self):
        basins = [Polity("A", "a", "A", False, box(0, 0, 1, 1)), Polity("B", "b", "B", False, box(1, 0, 2, 1)),
                  Polity("", h.INLAND_GROUP, "Inland drainage", False, box(2, 0, 3, 1))]
        colours = {"a": (1, 2, 3), "b": (4, 5, 6), h.INLAND_GROUP: h.INLAND_RGB}
        legend = h.add_basins_legend(_globe(), basins, [{0: 5, 1: 9, 2: 20}], colours, limit=1)
        assert legend is not None
        assert [t.get_text() for t in legend.get_texts()] == ["B", "Inland drainage"]
        assert "the 1 largest of 2" in legend.get_title().get_text()
        assert h.add_basins_legend(_globe(), basins, [{}], colours) is None

    def test_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(h, "_download", lambda url, dest, label: open(dest, "wb").write(b"junk"))
        with pytest.raises(h.HydrologyDataError, match="Could not build the river basins"):
            h.ensure_basins_data(str(tmp_path))


class TestRivers:
    def test_drawn_wider_when_greater_and_named(self):
        ax = _globe(0, 0)
        rivers = [h.River("Great", 0, LineString([(-10, 0), (10, 0)])), h.River("Small", 5, LineString([(0, 5), (1, 6)])),
                  h.River("Far", 0, LineString([(170, 0), (175, 0)]))]
        h.draw_rivers(ax, rivers)
        lines = ax.collections[0]
        assert isinstance(lines, LineCollection)
        widths = list(lines.get_linewidths())
        assert len(widths) == 2 and widths[0] > widths[1]
        assert [t.get_text() for t in ax.texts] == ["Great"]        # small ones and far ones unnamed

    @pytest.mark.parametrize("attributes, name", [
        ({"name": "Donau", "name_en": "Danube"}, "Danube"),
        ({"name": "Paraná", "name_en": "Paraná River"}, "Paraná"),
        ({"name": "Rio Grande", "name_en": "Grande"}, "Rio Grande"),
        ({"name": "Kagera", "name_en": ""}, "Kagera"),
    ])
    def test_names_in_english(self, attributes, name):
        assert h.river_name(attributes) == name

    def test_each_river_named_once_and_branches_not_at_all(self):
        ax = _globe(0, 20)
        rivers = [h.River("White Nile", 1, LineString([(30, -10), (30, 5)])),
                  h.River("White Nile", 1, LineString([(30, 5), (31, 15)])),
                  h.River("Rosetta Branch", 1, LineString([(10, 0), (15, 20)]), labelled=False),
                  h.River("Nile", 1, LineString([(20, 20), (22, 30)]))]
        h.draw_rivers(ax, rivers)
        assert sorted(t.get_text() for t in ax.texts) == ["Nile", "White Nile"]

    def test_the_longest_on_the_map_named_first(self):
        ax = _globe(0, 0)
        # Two rivers whose names would overlap: only the longer is named
        rivers = [h.River("Short", 0, LineString([(-5, 0), (5, 0)])), h.River("Long", 3, LineString([(-20, 0), (20, 0)]))]
        h.draw_rivers(ax, rivers)
        assert [t.get_text() for t in ax.texts] == ["Long"]

    def test_clear_of_names_already_there(self):
        ax = _globe(0, 0)
        ax.text(0, 0, "Basin name", transform=ccrs.PlateCarree(), fontsize=12)
        h.draw_rivers(ax, [h.River("River", 0, LineString([(-20, -1), (20, -1)]))])
        assert [t.get_text() for t in ax.texts] == ["Basin name"]

    def test_lines_split_where_out_of_sight(self):
        runs = h.project_lines(_globe(0, 0), LineString([(0, 0), (60, 0), (120, 0), (130, 0), (80, 0)]))
        assert [len(r) for r in runs] == [2]                         # 0-60°E; past 90°E is out of sight
        assert np.isfinite(runs[0]).all()
