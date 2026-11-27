"""Tests for geology.py (no network; small tiles and a table of intervals made here)."""

import json
import logging
import os
import struct
from collections import Counter

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import geology as g
import incomplete
from climate import MAX_CELLS, View
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


# --- A table of intervals like Macrostrat's ---------------------------------

TABLE = [  # number, name, oldest, youngest, rank
    (1, "Cenozoic", 66, 0, "international eras"),
    (2, "Neogene", 23.04, 2.58, "international periods"),
    (3, "Quaternary", 2.58, 0, "international periods"),
    (4, "Pliocene", 5.333, 2.58, "international epochs"),
    (5, "Cretaceous", 143.1, 66, "international periods"),
    (6, "Jurassic", 201.4, 143.1, "international periods"),
    (7, "Late Cretaceous", 100.5, 66, "international epochs"),
    (8, "Phanerozoic", 538.8, 0, "international eons"),
    (9, "Cambrian", 538.8, 486.85, "international periods"),
    (10, "Neoproterozoic", 1000, 538.8, "international eras"),
    (11, "Ediacaran", 635, 538.8, "international periods"),
    (12, "Precambrian", 4031, 538.8, ""),
    (13, "Berriasian", 143.1, 137.05, "international ages"),
    (14, "Proterozoic", 2500, 538.8, "international eons"),
    (15, "Paleozoic", 538.8, 251.902, "international eras"),
]
INTERVALS = json.dumps({"success": {"data": [
    {"int_id": n, "name": name, "b_age": b, "t_age": t, "color": "#%02X8040" % (n * 10),
     "timescales": [{"name": rank}] if rank else []} for n, name, b, t, rank in TABLE]}}).encode()
NUMBER = {name: n for n, name, *_ in TABLE}


def _tags(oldest, youngest=None, **more):
    return {"b_int_id": NUMBER.get(oldest, -1), "t_int_id": NUMBER.get(youngest or oldest, -1), **more}


# --- Vector tiles ------------------------------------------------------------


def _varint(n):
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def _field(number, value):
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _zigzag(n):
    return _varint((n << 1) ^ (n >> 63))


def _geometry(rings):
    out, x, y = b"", 0, 0
    for ring in rings:
        for i, (px, py) in enumerate(ring):
            if i == 0:
                out += _varint(1 << 3 | 1)
            elif i == 1:
                out += _varint((len(ring) - 1) << 3 | 2)
            out += _zigzag(px - x) + _zigzag(py - y)
            x, y = px, py
        out += _varint(1 << 3 | 7)
    return out


def _value(value):
    if isinstance(value, str):
        return _field(1, value.encode())
    if isinstance(value, float):
        return _varint(3 << 3 | 1) + struct.pack("<d", value)
    return _field(4, value)


def _tile(units, name=b"units", extent=100):
    """A tile of (tags, rings) or (tags, rings, geometry type)."""
    keys, values, features = [], [], b""
    for tags, rings, *kind in units:
        pairs = b""
        for key, value in tags.items():
            keys.append(_field(3, key.encode()))
            values.append(_field(4, _value(value)))
            pairs += _varint(len(keys) - 1) + _varint(len(values) - 1)
        features += _field(2, _field(2, pairs) + _field(3, kind[0] if kind else 3) + _field(4, _geometry(rings)))
    return _field(3, _field(1, name) + features + b"".join(keys) + b"".join(values) + _field(5, extent))


def _box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


REF = {"source_id": 154, "ref_name": "World", "ref_authors": "Chorlton, L.B.", "ref_year": "2007"}


@pytest.fixture
def intervals():
    return g.read_intervals(INTERVALS), g.read_classes(INTERVALS)


@pytest.fixture
def dry_land(monkeypatch):
    """Everywhere is land."""
    monkeypatch.setattr(g, "land_mask", lambda shape, *args, **kwargs: np.ones(shape, bool))


@pytest.fixture
def serve(monkeypatch):
    """Serve the table and tiles: ``serve(tiles, bad)`` with tiles[(z, x, y)]; every other tile is empty."""
    def start(tiles, bad=()):
        fetched = []

        def get(url, timeout=60):
            fetched.append(url)
            if url == g.INTERVALS_URL:
                return INTERVALS
            z, x, y = (int(part) for part in url.removesuffix(".mvt").split("/")[-3:])
            if (z, x, y) in bad:
                raise OSError("offline")
            return tiles.get((z, x, y), b"")

        monkeypatch.setattr(g, "_get", get)
        return fetched

    return start


@pytest.mark.parametrize("text, mode", [(None, "age"), ("", "age"), ("Age", "age"), ("rock", "rock"), ("r", "rock")])
def test_modes(text, mode):
    assert g.resolve_geology(text) == mode


def test_bad_mode():
    with pytest.raises(ValueError, match="Unknown geology 'soil'; choose age or rock"):
        g.resolve_geology("soil")


class TestReadTile:
    def test_numbers_text_and_rings(self):
        hole = _box(40, 40, 60, 60)
        (unit,) = g.read_tile(_tile([({"age": 145.0, "b_int_id": 5, "name": "Chalk"},
                                      [_box(0, 0, 100, 100), hole, _box(0, 0, 10, 10)])]))
        assert unit.tags == {"age": 145.0, "b_int_id": 5, "name": "Chalk"}
        assert [len(ring) for ring in unit.rings] == [4, 4, 4]
        np.testing.assert_allclose(unit.rings[1], np.array(hole) / 100)      # fractions of the tile

    def test_lines_and_other_layers_are_left_out(self):
        line = ({"b_int_id": 5}, [[(0, 0), (50, 50), (90, 10)]], 2)
        assert g.read_tile(_tile([line])) == []
        assert g.read_tile(_tile([({}, [_box(0, 0, 9, 9)])], name=b"lines")) == []
        assert g.read_tile(b"") == []                                        # a tile of open sea


class TestAge:
    @pytest.mark.parametrize("oldest, youngest, name", [
        ("Cretaceous", None, "Cretaceous"),                 # although the tiles' 145.0 Ma is older than the Jurassic's end
        ("Late Cretaceous", None, "Cretaceous"),            # epochs only of the Cenozoic
        ("Berriasian", None, "Cretaceous"),
        ("Pliocene", None, "Pliocene"),
        ("Pliocene", "Quaternary", "Cenozoic"),             # no shorter interval holds both
        ("Jurassic", "Cretaceous", "Phanerozoic"),
        ("Ediacaran", None, "Neoproterozoic"),              # eras before the Cambrian
        ("Precambrian", None, "Precambrian"),
        ("Neoproterozoic", "Cambrian", "Neoproterozoic to Paleozoic"),
        ("Ediacaran", "Paleozoic", "Neoproterozoic to Paleozoic"),
        ("Proterozoic", "Cambrian", "Precambrian to Paleozoic"),
        ("Proterozoic", "Neoproterozoic", "Proterozoic"),   # the spans take no unit that ends in the Precambrian
        ("Cambrian", "Paleozoic", "Paleozoic"),             # nor one that starts after it
        ("Neoproterozoic", "Cretaceous", None),             # nothing holds both
        ("unknown", None, None),
    ])
    def test_shortest_interval_that_holds_the_whole_age(self, intervals, oldest, youngest, name):
        found = g.age_class(_tags(oldest, youngest, best_age_bottom=145.0), *intervals)
        assert (found.name if found else None) == name

    def test_a_unit_without_intervals(self, intervals):
        assert g.age_class({"name": "ice"}, *intervals) is None

    def test_classes_are_shortest_first_without_precambrian_periods(self, intervals):
        names = [i.name for i in intervals[1]]
        assert "Ediacaran" not in names and "Late Cretaceous" not in names and "Berriasian" not in names
        assert "Neoproterozoic to Paleozoic" in names and "Precambrian to Paleozoic" in names
        lengths = [i.oldest - i.youngest for i in intervals[1]]
        assert lengths == sorted(lengths)


@pytest.mark.parametrize("tags, rock", [
    ({"lith_class1": "sedimentary", "lith_type1": "carbonate"}, "Sedimentary"),
    ({"lith_class1": "metamorphic"}, "Metamorphic"),
    ({"lith_class1": "igneous", "lith_type1": "volcanic"}, "Volcanic"),
    ({"lith_class1": "igneous", "lith_type1": "igneous", "lith_type2": "plutonic"}, "Plutonic"),
    ({"lith_class1": "igneous", "lith_type1": "plutonic", "lith_type2": "volcanic"}, "Other igneous"),
    ({"lith_class1": "igneous"}, "Other igneous"),
    ({"lith_class1": "igneous", "lith_class2": "sedimentary", "lith_type1": "volcanic"}, "Mixed"),   # listed by alphabet
    ({"lith_class1": "", "lith_type1": "volcanic"}, None),
    ({"lith_class1": "water"}, None),
    ({}, None),
])
def test_rock_only_where_a_unit_gives_one_class(tags, rock):
    assert g.rock_class(tags) == rock


@pytest.mark.parametrize("tags, name", [
    (REF, "World (Chorlton, L.B. 2007)"),
    ({"ref_name": "GMNA", "ref_authors": "Garrity, C.P., and Soller, D.R.,", "ref_year": "2009"},
     "GMNA (Garrity, C.P., and Soller, D.R. 2009)"),
    ({"ref_name": "Arctic", "ref_authors": "J.C. Harrison, M.R. St-Onge, O.V. Petrov, \nF.H. Wilson and others", "ref_year": "2011"},
     "Arctic (J.C. Harrison et al. 2011)"),
    ({"source_id": 9}, "Macrostrat source 9"),
])
def test_source_names(tags, name):
    assert g.source_name(tags) == name


class TestTilesOver:
    def test_the_whole_globe_is_sixteen_tiles(self):
        assert len(g.tiles_over(View(0.0, -180.0, 180.0, -90.0, 90.0), 2)) == 16
        assert len(set(g.tiles_over(View(170.0, -180.0, 180.0, -90.0, 90.0), 2))) == 16

    def test_a_view_across_the_date_line_wraps(self):
        columns = {x for x, _ in g.tiles_over(View(179.0, -5.0, 5.0, -5.0, 5.0), 5)}
        assert columns == {31, 0}

    @pytest.mark.parametrize("half, south, north, zoom", [(6, 43, 49, 5), (40, 10, 85, 4), (180, -85, 85, 3)])
    def test_the_finest_zoom_within_the_limit(self, half, south, north, zoom):
        view = View(20.0, -half, half, south, north)
        assert g.close_zoom(view) == zoom
        assert len(g.tiles_over(view, zoom)) <= g.MAX_TILES
        assert zoom == 5 or len(g.tiles_over(view, zoom + 1)) > g.MAX_TILES


@pytest.mark.parametrize("width, regrid, cell", [(12, 3000, 0.01), (12, 750, 0.016), (180, 4096, None)])
def test_cells_follow_the_output_within_the_cap(width, regrid, cell):
    view = View(0.0, -width / 2, width / 2, -width / 2, width / 2)
    size = g.cell_size(view, regrid)
    assert (width / size) ** 2 <= MAX_CELLS * 1.0001
    if cell is not None:
        assert size == pytest.approx(cell)


def _names(layer):
    return {entry.name for entry in layer.entries}


def _at(layer, lon, lat):
    west, east, south, north = layer.extent
    rows, cols = layer.cells.shape
    col = int(((lon - layer.centre_lon - west) % 360) / (east - west) * cols)
    value = layer.cells[int((north - lat) / (north - south) * rows), col]
    return layer.entries[value - 1].name if value else None


class TestLayer:
    def test_units_holes_and_ice(self, serve, dry_land, tmp_path):
        # Zoom 1, tile (1, 0): 0 to 180 east, the equator to 85 north
        unit = ({**_tags("Cretaceous"), **REF}, [_box(0, 0, 100, 100), _box(40, 40, 60, 60)])
        ice = ({"name": "ice", **REF}, [_box(0, 0, 10, 100)])                # on top of the Cretaceous
        serve({(1, 1, 0): _tile([unit, ice])})
        layer = g.load_layer(View(0.0, -180.0, 180.0, -90.0, 90.0), 1.0, 1, "age", str(tmp_path))
        assert _at(layer, 30.5, 40.5) == "Cretaceous"
        assert _at(layer, 90.5, 66.5) is None                                # the hole
        assert _at(layer, 5.5, 40.5) is None and _at(layer, -60.5, 40.5) is None and _at(layer, 30.5, -40.5) is None
        assert layer.entries == [g.Entry("Cretaceous", (50, 128, 64), "World (Chorlton, L.B. 2007)")]

    @pytest.mark.parametrize("centre", [179.0, -179.0])
    def test_a_window_across_the_date_line(self, serve, dry_land, tmp_path, centre):
        # Zoom 1: the tile east of Greenwich ends, and the one west of it starts, at 180 degrees
        serve({(1, 1, 0): _tile([({**_tags("Jurassic"), **REF}, [_box(98, 0, 100, 100)])]),
               (1, 0, 0): _tile([({**_tags("Cretaceous"), **REF}, [_box(0, 0, 2, 100)])])})
        layer = g.load_layer(View(centre, -10.0, 10.0, 10.0, 30.0), 0.1, 1, "age", str(tmp_path))
        assert layer.cells.shape == (200, 200)
        assert _at(layer, 178.05, 20) == "Jurassic" and _at(layer, -178.05, 20) == "Cretaceous"
        assert _at(layer, 174.05, 20) is None and _at(layer, -174.05, 20) is None

    def test_rock(self, serve, dry_land, tmp_path):
        serve({(1, 1, 0): _tile([({"lith_class1": "metamorphic", **REF}, [_box(0, 0, 100, 100)])])})
        layer = g.load_layer(View(0.0, -180.0, 180.0, -90.0, 90.0), 1.0, 1, "rock", str(tmp_path))
        assert _names(layer) == {"Metamorphic"} and layer.entries[0].colour == g.ROCKS["Metamorphic"]

    def test_the_sea_is_clear(self, serve, monkeypatch, tmp_path):
        masks = []

        def sea(shape, extent, centre_lon, scale):
            masks.append((extent, centre_lon, scale))
            return np.zeros(shape, bool)

        monkeypatch.setattr(g, "land_mask", sea)
        serve({(1, 1, 0): _tile([({**_tags("Cretaceous"), **REF}, [_box(0, 0, 100, 100)])])})
        layer = g.load_layer(View(25.0, -5.0, 5.0, 40.0, 50.0), 0.02, 1, "age", str(tmp_path))
        assert not layer.cells.any()
        assert masks == [((-5.0, pytest.approx(5.0), pytest.approx(40.0), 50.0), 25.0, "10m")]

    def test_cached_once_read(self, serve, dry_land, tmp_path):
        tiles = {(1, 1, 0): _tile([({**_tags("Cretaceous"), **REF}, [_box(0, 0, 100, 100)])])}
        view = View(0.0, -180.0, 180.0, -90.0, 90.0)
        fetched = serve(tiles)
        first = g.load_layer(view, 1.0, 1, "age", str(tmp_path))
        assert len(fetched) == 5                                             # the table and four tiles
        again = g.load_layer(view, 1.0, 1, "age", str(tmp_path))
        assert len(fetched) == 5
        np.testing.assert_array_equal(first.cells, again.cells)

    @pytest.mark.parametrize("broken", ["missing", "unreadable"])
    def test_a_tile_that_fails_is_noted_and_not_kept(self, serve, dry_land, tmp_path, broken):
        good = _tile([({**_tags("Cretaceous"), **REF}, [_box(0, 0, 100, 100)])])
        view = View(0.0, -180.0, 180.0, -90.0, 90.0)
        tiles = {(1, 1, 0): good, (1, 0, 0): b"<html>busy</html>" if broken == "unreadable" else good}
        serve(tiles, bad={(1, 0, 0)} if broken == "missing" else ())
        with incomplete.noting() as noted:
            layer = g.load_layer(view, 1.0, 1, "age", str(tmp_path))
        assert len(noted) == 1 and noted[0].startswith("1 of 4 geology tiles could not be downloaded")
        assert _at(layer, 30.5, 40.5) == "Cretaceous" and _at(layer, -30.5, 40.5) is None
        assert not os.path.exists(tmp_path / "carto" / "1" / "0_0.mvt")
        serve({**tiles, (1, 0, 0): good})                                    # it comes back the next time
        with incomplete.noting() as noted:
            layer = g.load_layer(view, 1.0, 1, "age", str(tmp_path))
        assert noted == [] and _at(layer, -30.5, 40.5) == "Cretaceous"

    def test_no_tile_at_all(self, serve, dry_land, tmp_path):
        serve({}, bad={(1, x, y) for x in (0, 1) for y in (0, 1)})
        with pytest.raises(g.GeologyDataError, match="Could not read the geology tiles: offline"):
            g.load_layer(View(0.0, -180.0, 180.0, -90.0, 90.0), 1.0, 1, "age", str(tmp_path))

    @pytest.mark.parametrize("table", [b"not json", b'{"success": {"data": []}}'])
    def test_a_bad_table_is_not_kept(self, monkeypatch, tmp_path, table):
        monkeypatch.setattr(g, "_get", lambda url, timeout=60: table)
        with pytest.raises(g.GeologyDataError, match="table of intervals"):
            g.load_intervals(str(tmp_path))
        assert os.listdir(tmp_path) == []


class TestLoadGeology:
    def _record(self, monkeypatch):
        calls = []

        def load(view, cell, z, by, cache_dir=None):
            calls.append((view, cell, z, by))
            return object()

        monkeypatch.setattr(g, "load_layer", load)
        return calls

    def test_globes_share_one_world_grid(self, monkeypatch):
        calls = self._record(monkeypatch)
        first, second = g.load_geology([_globe(), _globe(0, 180)], "age")
        assert first is second
        assert calls == [(View(0.0, -180.0, 180.0, -90.0, 90.0), g.GLOBE_CELL, 2, "age")]

    def test_closer_maps_have_a_window_each(self, monkeypatch):
        calls = self._record(monkeypatch)
        axes = [_globe(46, 25), _globe(0, 100)]
        for ax in axes:
            ax.set_extent((-450e3, 450e3, -450e3, 450e3), crs=ax.projection)
        assert len(g.load_geology(axes, "rock", whole_globe=False, regrid_shape=3000)) == 2
        assert [(round(view.centre_lon), z, by) for view, _, z, by in calls] == [(25, 5, "rock"), (100, 5, "rock")]
        assert all(cell == g.MIN_CELL for _, cell, _, _ in calls)


class TestDrawing:
    OLD, YOUNG = g.Entry("Cretaceous", (1, 2, 3), "World"), g.Entry("Pliocene", (4, 5, 6), "Europe")

    def _layer(self):
        cells = np.zeros((180, 360), np.uint16)
        cells[:, 90:270] = 1                                                 # 90 west to 90 east
        cells[:, 270:] = 2
        return g.Layer(cells, [self.OLD, self.YOUNG], (-180.0, 180.0, -90.0, 90.0), 0.0)

    def test_rgba(self):
        rgba = g.geology_rgba(self._layer(), 0.5)
        assert rgba[0, 0, 3] == 0
        assert tuple(rgba[0, 100]) == (1, 2, 3, 128) and tuple(rgba[0, 300]) == (4, 5, 6, 128)

    def test_only_what_the_map_shows_is_counted(self):
        layer = self._layer()
        assert set(g.visible_entries([_globe(0, 0)], [layer])) == {self.OLD}
        assert set(g.visible_entries([_globe(0, 130)], [layer])) == {self.OLD, self.YOUNG}

    def test_a_window_is_counted_where_it_lies(self):
        ax = _globe(46, 179)
        ax.set_extent((-450e3, 450e3, -450e3, 450e3), crs=ax.projection)
        layer = g.Layer(np.ones((100, 100), np.uint16), [self.OLD], (-10.0, 10.0, 40.0, 52.0), 179.0)
        assert g.visible_entries([ax], [layer])[self.OLD] > 1000

    def test_key_is_youngest_first(self, intervals):
        ax = _globe()
        key = g.add_geology_legend(ax, Counter({self.OLD: 9, self.YOUNG: 1}), "age", intervals[1])
        assert key is not None and [t.get_text() for t in key.get_texts()] == ["Pliocene", "Cretaceous"]
        assert "whole age" in key.get_title().get_text() and key in ax.artists

    def test_key_is_as_pale_as_the_map(self):
        shown = Counter({g.Entry("Volcanic", (200, 100, 0), "a"): 1})
        key = g.add_geology_legend(_globe(), shown, "rock", alpha=0.5)
        assert key is not None
        assert key.get_patches()[0].get_facecolor()[:3] == pytest.approx((228 / 255, 178 / 255, 128 / 255))

    def test_rock_key(self):
        shown = Counter({g.Entry("Mixed", g.ROCKS["Mixed"], "a"): 1, g.Entry("Volcanic", g.ROCKS["Volcanic"], "b"): 5})
        key = g.add_geology_legend(_globe(), shown, "rock")
        assert key is not None and [t.get_text() for t in key.get_texts()] == ["Volcanic", "Mixed"]

    def test_nothing_shown_has_no_key(self):
        assert g.add_geology_legend(_globe(), Counter()) is None

    def test_credit_names_each_map_once_the_largest_first(self):
        shown = Counter({self.YOUNG: 5, self.OLD: 3, g.Entry("Jurassic", (0, 0, 0), "World"): 4})
        assert g.geology_attribution(shown) == "Geology: World; Europe, via Macrostrat (Peters et al. 2018), CC BY 4.0"

    def test_overlay(self, caplog):
        ax = _globe()
        with caplog.at_level(logging.INFO):
            g.add_geology_overlay(ax, self._layer(), alpha=0.5, regrid_shape=100)
        assert len(ax.images) == 1 and "Geology drawn" in caplog.text
