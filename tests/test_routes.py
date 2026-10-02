"""Tests for routes.py — GeoJSON route loading and drawing (no network)."""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO_ROOT)

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
            assert loaded and all(len(r.lines[0]) >= 2 for r in loaded)

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
