"""Tests for routes.py — GeoJSON route loading and drawing (no network)."""

import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import routes


def _write(tmp_path, data, name="route.geojson"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _feature(coords, gtype="LineString", props=None):
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": {"type": gtype, "coordinates": coords},
    }


# ===================================================================
# load_routes
# ===================================================================


class TestLoadRoutes:
    def test_bundled_route_files_load(self):
        route_dir = os.path.join(REPO_ROOT, "routes")
        files = [f for f in os.listdir(route_dir) if f.endswith(".geojson")]
        assert files, "expected at least one bundled route file"
        for fname in files:
            loaded = routes.load_routes(os.path.join(route_dir, fname))
            assert loaded and all(r.lines or r.areas for r in loaded)

    def test_feature_collection_with_style(self, tmp_path):
        path = _write(tmp_path, {
            "type": "FeatureCollection",
            "features": [_feature([[0, 0], [10, 10]], props={"name": "A", "stroke": "blue", "stroke-width": 3})],
        })
        (route,) = routes.load_routes(path)
        assert route.name == "A"
        assert route.color == "blue"
        assert route.linewidth == 3.0
        assert route.lines == (((0.0, 0.0), (10.0, 10.0)),)

    def test_single_feature_defaults(self, tmp_path):
        path = _write(tmp_path, _feature([[0, 0], [1, 1]]), name="my_trip.geojson")
        (route,) = routes.load_routes(path)
        assert route.name == "my_trip"
        assert route.color == routes.DEFAULT_ROUTE_COLOR
        assert route.linewidth == routes.DEFAULT_ROUTE_WIDTH

    def test_bare_geometry(self, tmp_path):
        path = _write(tmp_path, {"type": "LineString", "coordinates": [[0, 0], [1, 1]]})
        assert len(routes.load_routes(path)) == 1

    def test_multilinestring(self, tmp_path):
        path = _write(tmp_path, _feature([[[0, 0], [1, 1]], [[5, 5], [6, 6]]], gtype="MultiLineString"))
        (route,) = routes.load_routes(path)
        assert len(route.lines) == 2

    def test_polygon_with_hole_and_fill_style(self, tmp_path):
        outer = [[0, 0], [10, 0], [10, 10], [0, 0]]
        hole = [[2, 1], [8, 1], [8, 7], [2, 1]]
        path = _write(tmp_path, _feature(
            [outer, hole], gtype="Polygon",
            props={"stroke": "green", "fill": "yellow", "fill-opacity": 0.5},
        ))
        (route,) = routes.load_routes(path)
        assert route.lines == ()
        assert len(route.areas) == 1 and len(route.areas[0]) == 2
        assert route.fill == "yellow"
        assert route.fill_opacity == 0.5

    def test_multipolygon_fill_defaults_to_stroke(self, tmp_path):
        square = [[0, 0], [1, 0], [1, 1], [0, 0]]
        path = _write(tmp_path, _feature([[square], [square]], gtype="MultiPolygon", props={"stroke": "blue"}))
        (route,) = routes.load_routes(path)
        assert len(route.areas) == 2
        assert route.fill == "blue"
        assert route.fill_opacity == routes.DEFAULT_FILL_OPACITY

    def test_legend_label(self, tmp_path):
        path = _write(tmp_path, _feature([[0, 0], [1, 1]], props={"name": "Long name", "legend": " Short "}))
        (route,) = routes.load_routes(path)
        assert route.legend == "Short"

    def test_legend_defaults_to_none(self, tmp_path):
        (route,) = routes.load_routes(_write(tmp_path, _feature([[0, 0], [1, 1]])))
        assert route.legend is None

    def test_altitude_is_ignored(self, tmp_path):
        path = _write(tmp_path, _feature([[0, 0, 100], [1, 1, 200]]))
        (route,) = routes.load_routes(path)
        assert route.lines[0] == ((0.0, 0.0), (1.0, 1.0))

    @pytest.mark.parametrize(
        "data, match",
        [
            (_feature([[0, 0]]), "at least 2"),
            (_feature([[0, 95], [0, 0]]), "out of range"),
            (_feature([[200, 0], [0, 0]]), "out of range"),
            (_feature([["a", 0], [0, 0]]), "non-numeric"),
            (_feature([[True, 0], [0, 0]]), "non-numeric"),
            (_feature([0, 0], gtype="Point"), "unsupported geometry"),
            (_feature([[0, 0], [1, 1]], props={"stroke": "notacolour"}), "stroke colour"),
            (_feature([[0, 0], [1, 1]], props={"stroke-width": 0}), "stroke-width"),
            (_feature([[[0, 0], [1, 0], [0, 0]]], gtype="Polygon"), "at least 4"),
            (_feature([[[0, 0], [1, 0], [1, 1], [0, 1]]], gtype="Polygon"), "not closed"),
            (_feature([], gtype="Polygon"), "no rings"),
            (_feature([], gtype="MultiPolygon"), "no polygons"),
            (_feature([[0, 0], [1, 1]], props={"fill": "notacolour"}), "fill colour"),
            (_feature([[0, 0], [1, 1]], props={"fill-opacity": 1.5}), "fill-opacity"),
            (_feature([[0, 0], [1, 1]], props={"legend": 3}), "legend"),
            (_feature([[0, 0], [1, 1]], props={"legend": "  "}), "legend"),
            ({"type": "FeatureCollection", "features": []}, "no routes"),
            ([1, 2, 3], "top level"),
        ],
    )
    def test_invalid_input_raises(self, tmp_path, data, match):
        path = _write(tmp_path, data)
        with pytest.raises(ValueError, match=match):
            routes.load_routes(path)

    def test_invalid_json_raises_value_error(self, tmp_path):
        path = tmp_path / "bad.geojson"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            routes.load_routes(str(path))

    def test_missing_file_raises_os_error(self, tmp_path):
        with pytest.raises(OSError):
            routes.load_routes(str(tmp_path / "nope.geojson"))


# ===================================================================
# draw_routes
# ===================================================================


class TestDrawRoutes:
    def test_one_line_per_part(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import cartopy.crs as ccrs

        route = routes.Route(
            name="r",
            lines=(((0.0, 0.0), (10.0, 10.0)), ((20.0, 0.0), (30.0, 5.0))),
            color="#00ff00",
            linewidth=1.5,
        )
        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            routes.draw_routes(ax, [route])
            assert len(ax.lines) == 2
            assert all(line.get_color() == "#00ff00" for line in ax.lines)
            assert all(line.get_linewidth() == 1.5 for line in ax.lines)
        finally:
            plt.close(fig)

    def test_area_drawn_below_lines(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import cartopy.crs as ccrs

        square = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0))
        route = routes.Route(name="a", lines=(), color="#0000ff", areas=((square,),), fill_opacity=0.5)
        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            routes.draw_routes(ax, [route], zorder=9)
            (artist,) = ax.collections
            assert artist.get_zorder() == 8
            assert len(ax.lines) == 0
        finally:
            plt.close(fig)


# ===================================================================
# add_route_legend
# ===================================================================


class TestRouteLegend:
    SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0))

    def _legends(self, route_list, **kwargs):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import cartopy.crs as ccrs
        from matplotlib.legend import Legend

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            routes.add_route_legend(ax, route_list, **kwargs)
            fig.canvas.draw()  # the key must render outside the globe disc
            return ax, [a for a in ax.artists if isinstance(a, Legend)]
        finally:
            plt.close(fig)

    def test_entries_top_layer_first_and_merged_by_label(self):
        line = (((0.0, 0.0), (1.0, 1.0)),)
        drawn = [
            routes.Route(name="first", lines=line, color="red", legend="Trade"),
            routes.Route(name="second", lines=line, color="blue"),
            routes.Route(name="third", lines=line, color="green", legend="Trade"),
        ]
        _, (legend,) = self._legends(drawn)
        assert [t.get_text() for t in legend.get_texts()] == ["Trade", "second"]
        # The merged entry takes the style of its top layer
        assert legend.legend_handles[0].get_color() == "green"
        assert not legend.get_clip_on()

    def test_area_routes_get_a_filled_swatch(self):
        import matplotlib.patches as mpatches

        area = routes.Route(name="a", lines=(), color="#00aa00", areas=((self.SQUARE,),), fill_opacity=0.5)
        _, (legend,) = self._legends([area])
        (patch,) = legend.legend_handles
        assert isinstance(patch, mpatches.Patch)
        assert patch.get_facecolor()[3] == 0.5

    def test_keeps_an_existing_legend(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import cartopy.crs as ccrs

        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.Orthographic(0, 0)})
        try:
            existing = ax.legend([mpatches.Patch()], ["Köppen"])
            routes.add_route_legend(ax, [routes.Route(name="r", lines=(((0.0, 0.0), (1.0, 1.0)),))])
            assert ax.get_legend() is existing
            assert len(ax.artists) == 1
        finally:
            plt.close(fig)

    def test_no_routes_adds_nothing(self):
        _, legends = self._legends([])
        assert legends == []
