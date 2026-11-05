"""Tests for rail_tracks.py (no network: small Overpass replies made up here)."""

import logging
import os
import time

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

import high_speed_rail as hsr
import incomplete
import rail_tracks as rt
from rotation import globe_projection

REGIONS = {"test": (-2.0, -1.0, 2.0, 2.0), "east": (-2.0, 1.4, 2.0, 4.0)}
STATIONS = {"A": (0.0, 0.0), "B": (1.0, 0.0), "C": (3.0, 0.0)}


def _way(way_id, points, first_node, tags=None):
    """An Overpass way through *points* (lon, lat), its nodes numbered from *first_node*."""
    return {"type": "way", "id": way_id, "nodes": list(range(first_node, first_node + len(points))),
            "geometry": [{"lon": lon, "lat": lat} for lon, lat in points],
            "tags": tags or {"railway": "rail", "highspeed": "yes"}}


def _arc(lon0, lon1, bulge, n=60):
    """Points from (*lon0*, 0.02) to (*lon1*, 0.02), bowed north by *bulge* degrees in the middle."""
    t = np.linspace(0, 1, n)
    return list(zip(lon0 + (lon1 - lon0) * t, 0.02 + bulge * np.sin(np.pi * t)))


def _fetcher(elements, asked=None):
    def fetch(text):
        if asked is not None:
            asked.append(text)
        return elements
    return fetch


def _router(elements, tmp_path, asked=None, stations=STATIONS):
    return rt.TrackRouter(stations, str(tmp_path), _fetcher(elements, asked), REGIONS)


def _straight(a, b):
    (lon1, lat1), (lon2, lat2) = STATIONS[a], STATIONS[b]
    return np.linspace(lon1, lon2, 20), np.linspace(lat1, lat2, 20)


class TestReading:
    def test_query_names_the_box_and_the_tags(self):
        text = rt.query((36.0, -9.5, 44.0, 3.5))
        assert "(36,-9.5,44,3.5)" in text and '"highspeed"="yes"' in text and '"railway"="construction"' in text
        assert "{box}" not in text and text.rstrip().endswith("out geom;")

    def test_parse_keeps_ways_and_drops_what_cannot_be_drawn(self):
        elements = [_way(1, [(0, 0), (1, 0), (2, 0)], 10),
                    _way(2, [(0, 1), (1, 1)], 20, {"railway": "construction", "construction:highspeed": "yes"}),
                    {"type": "node", "id": 5, "lat": 0, "lon": 0},
                    {"type": "way", "id": 3, "nodes": [1, 2, 3], "geometry": [None, {"lon": 0, "lat": 0}, None]}]
        ways = rt.parse(elements)
        assert ways.ids.tolist() == [1, 2] and ways.starts.tolist() == [0, 3, 5]
        assert ways.nodes.tolist() == [10, 11, 12, 20, 21] and ways.construction.tolist() == [False, True]

    def test_merge_and_select(self):
        one = rt.parse([_way(1, [(0, 0), (1, 0)], 10), _way(2, [(0, 1), (1, 1)], 20)])
        two = rt.parse([_way(2, [(0, 1), (1, 1)], 20), _way(3, [(5, 5), (6, 6), (7, 7)], 30,
                                                           {"railway": "construction", "highspeed": "yes"})])
        both = rt.merge([one, two])
        assert both.ids.tolist() == [1, 2, 3] and both.starts.tolist() == [0, 2, 4, 7]
        open_only = rt.select(both, ~both.construction)
        assert open_only.ids.tolist() == [1, 2] and open_only.nodes.tolist() == [10, 11, 20, 21]
        assert rt.merge([]).ids.size == 0

    def test_regions_for(self):
        lon, lat = np.array([0.0, 1.0]), np.array([0.0, 0.0])
        assert rt.regions_for(lon, lat, regions=REGIONS) == ["test", "east"]     # within half a degree
        assert rt.regions_for(lon, lat, margin=0, regions=REGIONS) == ["test"]
        assert rt.regions_for(lon + 50, lat, regions=REGIONS) == []

    def test_every_link_of_high_speed_rail_lies_in_a_region(self):
        rail = hsr.load_high_speed_rail("planned", tracks=False)
        for k in range(len(rail.a)):
            lon, lat = hsr._path(rail, k)
            for x, y in zip(lon, lat):
                assert any(s <= y <= n and w <= x <= e for s, w, n, e in rt.REGIONS.values()), \
                    (rail.names[rail.a[k]], rail.names[rail.b[k]], x, y)


class TestCache:
    def test_downloaded_once(self, tmp_path):
        asked = []
        fetch = _fetcher([_way(1, _arc(0.05, 0.95, 0.1), 1)], asked)
        first = rt.load_region("test", str(tmp_path), fetch, REGIONS)
        again = rt.load_region("test", str(tmp_path), fetch, REGIONS)
        assert len(asked) == 1 and first.ids.tolist() == again.ids.tolist() == [1]
        assert os.path.isfile(tmp_path / f"test_v{rt.DATA_VERSION}.npz")

    def test_downloaded_again_after_a_year_or_kept_if_that_fails(self, tmp_path):
        rt.load_region("test", str(tmp_path), _fetcher([_way(1, _arc(0.05, 0.95, 0.1), 1)]), REGIONS)
        path = tmp_path / f"test_v{rt.DATA_VERSION}.npz"
        old = time.time() - (rt.MAX_AGE_DAYS + 1) * 86400
        os.utime(path, (old, old))

        def offline(text):
            raise rt.TrackDataError("offline")

        assert rt.load_region("test", str(tmp_path), offline, REGIONS).ids.tolist() == [1]
        newer = rt.load_region("test", str(tmp_path), _fetcher([_way(7, _arc(0.05, 0.95, 0.1), 1)]), REGIONS)
        assert newer.ids.tolist() == [7]

    def test_nothing_to_be_had(self, tmp_path):
        def offline(text):
            raise rt.TrackDataError("offline")

        with pytest.raises(rt.TrackDataError, match="offline"):
            rt.load_region("test", str(tmp_path), offline, REGIONS)

    def test_overpass_servers_tried_in_turn(self, monkeypatch):
        tried = []

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return b'{"elements": [{"type": "way", "id": 1}]}'

        def urlopen(request, timeout, context):
            tried.append((request.full_url, timeout))
            if request.full_url.startswith("https://one/"):
                raise OSError("reset")
            return Reply()

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        assert rt.fetch_overpass("[out:json];", urls=("https://one/api/interpreter", "https://two/api/interpreter")
                                 ) == [{"type": "way", "id": 1}]
        # Each asked for its status first, briefly
        assert tried == [("https://one/api/status", rt.STATUS_TIMEOUT), ("https://two/api/status", rt.STATUS_TIMEOUT),
                         ("https://two/api/interpreter", rt.TIMEOUT)]
        monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
        dead = set()
        with pytest.raises(rt.TrackDataError, match="one: down; two: down"):
            rt.fetch_overpass("[out:json];", urls=("https://one/api", "https://two/api"), dead=dead)
        assert dead == {"https://one/api", "https://two/api"}
        # Not asked again in the same run
        with pytest.raises(rt.TrackDataError, match="earlier in this run"):
            rt.fetch_overpass("[out:json];", urls=("https://one/api", "https://two/api"), dead=dead)

    def test_a_router_asks_no_dead_server_twice(self, monkeypatch, tmp_path):
        tried = []

        def urlopen(request, timeout, context):
            tried.append(request.full_url)
            raise TimeoutError("timed out")

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        router = rt.TrackRouter(STATIONS, str(tmp_path), regions=REGIONS)
        with incomplete.noting() as noted:
            router.prepare([_straight("A", "C")])                 # both regions
        assert tried == [url.rsplit("/", 1)[0] + "/status" for url in rt.OVERPASS_URLS]
        assert set(router.failed) == {"test", "east"} and len(noted) == 2


class TestRouting:
    def test_a_link_follows_the_track(self, tmp_path):
        router = _router([_way(1, _arc(0.05, 0.95, 0.15), 1)], tmp_path)
        lon, lat = router.route("A", "B", False, *_straight("A", "B"))
        assert (lon[0], lat[0]) == STATIONS["A"] and (lon[-1], lat[-1]) == STATIONS["B"]
        assert lat.max() == pytest.approx(0.17, abs=0.01)             # along the bow, not straight
        assert router.used and list(router.loaded) == ["test", "east"]

    def test_both_ways_round(self, tmp_path):
        router = _router([_way(1, _arc(0.05, 0.95, 0.15), 1)], tmp_path)
        there = router.route("A", "B", False, *_straight("A", "B"))
        back = router.route("B", "A", False, *_straight("B", "A"))
        assert np.allclose(there[0][::-1], back[0])

    def test_gaps_in_the_track_are_bridged(self, tmp_path):
        points = _arc(0.05, 0.95, 0.15, n=200)
        router = _router([_way(1, points[:100], 1), _way(2, points[101:], 500)], tmp_path)   # ~1 km apart
        assert router.route("A", "B", False, *_straight("A", "B")) is not None

    def test_a_track_far_from_the_stations_is_not_used(self, tmp_path):
        far = [(lon, lat + 0.6) for lon, lat in _arc(0.05, 0.95, 0.1)]         # ~65 km north of both
        router = _router([_way(1, far, 1)], tmp_path)
        assert router.route("A", "B", False, *_straight("A", "B")) is None
        assert not router.used

    def test_a_long_detour_is_refused(self, tmp_path, caplog):
        router = _router([_way(1, _arc(0.05, 0.95, 0.8), 1)], tmp_path)        # bowed ~90 km north
        with caplog.at_level(logging.INFO, logger="rail_tracks"):
            assert router.route("A", "B", False, *_straight("A", "B")) is None
        assert "drawn city to city" in caplog.text

    def test_a_link_mostly_off_track_is_drawn_city_to_city(self, tmp_path):
        stations = {"A": (0.0, 0.0), "B": (0.5, 0.0)}
        short = _way(1, [(0.2, 0.01), (0.25, 0.01), (0.3, 0.01)], 1)            # 11 km of 55 km on track
        router = _router([short], tmp_path, stations=stations)
        lon, lat = np.linspace(0, 0.5, 10), np.zeros(10)
        assert router.route("A", "B", False, lon, lat) is None

    def test_open_links_keep_off_the_tracks_being_built(self, tmp_path):
        building = _way(1, _arc(0.05, 0.95, 0.15), 1, {"railway": "construction", "highspeed": "yes"})
        router = _router([building], tmp_path)
        assert router.route("A", "B", False, *_straight("A", "B")) is None
        assert router.route("A", "B", True, *_straight("A", "B")) is not None

    def test_tracks_not_to_be_had_are_noted_once(self, tmp_path):
        asked = []

        def offline(text):
            asked.append(text)
            raise rt.TrackDataError("offline")

        router = rt.TrackRouter(STATIONS, str(tmp_path), offline, REGIONS)
        with incomplete.noting() as noted:
            assert router.route("A", "B", False, *_straight("A", "B")) is None
            assert router.route("B", "C", False, *_straight("B", "C")) is None
        assert len(asked) == 1 and set(router.failed) == {"test"}
        assert len(noted) == 1 and "test from city to city" in noted[0] and "offline" in noted[0]

    def test_routes_are_remembered(self, tmp_path):
        router = _router([_way(1, _arc(0.05, 0.95, 0.15), 1)], tmp_path)
        first = router.route("A", "B", False, *_straight("A", "B"))
        assert router.route("A", "B", False, *_straight("A", "B")) is first

    def test_an_empty_region(self, tmp_path):
        router = _router([], tmp_path)
        assert router.route("A", "B", False, *_straight("A", "B")) is None

    def test_stations_join_the_tracks_in_each_direction(self):
        # Two lines leave the station, one west and one east, each a piece of its own
        ways = rt.parse([_way(1, [(-0.1, 0.0), (-0.5, 0.0)], 1), _way(2, [(0.1, 0.0), (0.5, 0.0)], 10)])
        graph = rt.Graph(ways, {"S": (0.0, 0.0)})
        joined = graph.matrix[graph.station["S"]].indices
        assert sorted(np.round(graph.lon[joined], 2).tolist()) == [-0.1, 0.1]


def _paris_lyon():
    """A made-up track from the edge of Paris to the edge of Lyon, bowed east through Burgundy."""
    t = np.linspace(0, 1, 200)
    lat = 48.78 + (45.79 - 48.78) * t
    lon = 2.45 + (4.90 - 2.45) * t + 0.8 * np.sin(np.pi * t)
    return _way(99, list(zip(lon, lat)), 1)


class TestHighSpeedRail:
    def test_links_in_view_drawn_along_osm_with_its_credit(self, tmp_path):
        asked = []
        rail = hsr.load_high_speed_rail("open", cache_dir=str(tmp_path), fetch=_fetcher([_paris_lyon()], asked))
        ax = Figure(figsize=(8, 8)).add_subplot(projection=globe_projection(47, 3))
        assert isinstance(ax, GeoAxes)
        half = np.radians(4) * 6_371_000
        ax.set_xlim(-half, half)
        ax.set_ylim(-half, half)
        hsr.draw_high_speed_rail(ax, rail)
        # Only the regions of the links near the map were asked for
        assert 0 < len(asked) <= 4 and not any("(18,100" in q for q in asked)
        k = next(k for k in range(len(rail.a)) if {rail.names[rail.a[k]], rail.names[rail.b[k]]} == {"Paris", "Lyon"})
        track = rail.router.routes[("Paris", "Lyon", False)] or rail.router.routes[("Lyon", "Paris", False)]
        assert track is not None and track[0].max() > 4.0               # east of the line drawn city to city
        assert rail.lines[k]
        drawn = np.concatenate([np.concatenate(c.get_segments()) for c in ax.collections
                                if isinstance(c, LineCollection)])
        x, y = ax.projection.transform_point(track[0][100], track[1][100], hsr.ccrs.PlateCarree())
        assert np.min(np.hypot(drawn[:, 0] - x, drawn[:, 1] - y)) < 1.0
        assert hsr.high_speed_rail_attribution(rail).endswith(rt.TRACKS_ATTRIBUTION)

    def test_city_to_city_credits_no_tracks(self):
        rail = hsr.load_high_speed_rail("open", tracks=False)
        assert rail.router is None and hsr.high_speed_rail_attribution(rail) == hsr.HIGH_SPEED_RAIL_ATTRIBUTION
