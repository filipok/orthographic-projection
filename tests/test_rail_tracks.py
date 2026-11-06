"""Tests for rail_tracks.py (no network: small Overpass replies made up here)."""

import json
import logging
import os
import re
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

STATIONS = {"A": (1.0, 1.0), "B": (2.0, 1.0), "C": (3.0, 1.0)}
HOME = (0, 0)      # the square of the grid they are all in


def _way(way_id, points, first_node, tags=None):
    """An Overpass way through *points* (lon, lat), its nodes numbered from *first_node*."""
    return {"type": "way", "id": way_id, "nodes": list(range(first_node, first_node + len(points))),
            "geometry": [{"lon": lon, "lat": lat} for lon, lat in points],
            "tags": tags or {"railway": "rail", "highspeed": "yes"}}


def _arc(lon0, lon1, bulge, n=60):
    """Points from (1 + *lon0*, 1.02) to (1 + *lon1*, 1.02), bowed north by *bulge* degrees in the middle."""
    t = np.linspace(0, 1, n)
    return list(zip(1 + lon0 + (lon1 - lon0) * t, 1.02 + bulge * np.sin(np.pi * t)))


def _fetcher(elements, asked=None):
    def fetch(text):
        if asked is not None:
            asked.append(text)
        return elements
    return fetch


def _router(elements, tmp_path, asked=None, stations=STATIONS):
    return rt.TrackRouter(stations, str(tmp_path), _fetcher(elements, asked))


def _straight(a, b):
    (lon1, lat1), (lon2, lat2) = STATIONS[a], STATIONS[b]
    return np.linspace(lon1, lon2, 20), np.linspace(lat1, lat2, 20)


class TestReading:
    def test_query_names_the_square_and_the_tags(self):
        text = rt.query((36, -12))
        assert "(36,-12,40,-8)" in text and '"highspeed"="yes"' in text and '"railway"="construction"' in text
        assert "{box}" not in text and text.rstrip().endswith("out geom;")

    def test_query_takes_the_channel_tunnel_though_it_is_not_tagged_high_speed(self):
        assert 'way.open["name"~"Channel Tunnel"];' in rt.query((48, 0))

    def test_query_scans_the_railways_of_the_square_once(self):
        # Each statement with a box scans the railways in it, which is what made the query slow
        assert rt.query((36, -12)).count("(36,-12,40,-8)") == 2     # the open railways, those being built

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

    def test_regions_for_are_the_squares_near_the_path(self):
        lon, lat = np.array([1.0, 2.0]), np.array([1.0, 1.0])
        assert rt.regions_for(lon, lat) == [HOME]
        assert set(rt.regions_for(lon + 1.8, lat)) == {(0, 0), (0, 4)}      # within half a degree of the next
        assert rt.regions_for(lon + 1.8, lat, margin=0) == [(0, 0)]
        assert rt.regions_for(np.array([-1.0]), np.array([-1.0]), margin=0) == [(-4, -4)]

    def test_regions_follow_the_path_not_its_bounding_box(self):
        lon, lat = np.linspace(1, 15, 57), np.linspace(1, 15, 57)           # a diagonal, four squares each way
        names = rt.regions_for(lon, lat, margin=0)
        assert (0, 0) in names and (12, 12) in names and (12, 0) not in names and (0, 12) not in names

    def test_taiwans_links_do_not_need_the_rest_of_china(self):
        rail = hsr.load_high_speed_rail("open", tracks=False)
        for k in range(len(rail.a)):
            if {rail.names[rail.a[k]], rail.names[rail.b[k]]} == {"Taipei", "Hsinchu"}:
                assert rt.regions_for(*hsr._path(rail, k)) == [(24, 120)]
                return
        pytest.fail("no Taipei-Hsinchu link")


class Reply:
    """What urlopen gives: the bytes of an HTTP reply."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self.body


OK = b'{"elements": [{"type": "way", "id": 1}]}'
SERVERS = ("https://one/api/interpreter", "https://two/api/interpreter")


class TestCache:
    def test_downloaded_once(self, tmp_path):
        asked = []
        fetch = _fetcher([_way(1, _arc(0.05, 0.95, 0.1), 1)], asked)
        first = rt.load_region(HOME, str(tmp_path), fetch)
        again = rt.load_region(HOME, str(tmp_path), fetch)
        assert len(asked) == 1 and first.ids.tolist() == again.ids.tolist() == [1]
        assert os.path.isfile(tmp_path / f"0_0_v{rt.DATA_VERSION}.npz")

    def test_downloaded_again_after_a_year_or_kept_if_that_fails(self, tmp_path):
        rt.load_region(HOME, str(tmp_path), _fetcher([_way(1, _arc(0.05, 0.95, 0.1), 1)]))
        path = tmp_path / f"0_0_v{rt.DATA_VERSION}.npz"
        old = time.time() - (rt.MAX_AGE_DAYS + 1) * 86400
        os.utime(path, (old, old))

        def offline(text):
            raise rt.TrackDataError("offline")

        assert rt.load_region(HOME, str(tmp_path), offline).ids.tolist() == [1]
        newer = rt.load_region(HOME, str(tmp_path), _fetcher([_way(7, _arc(0.05, 0.95, 0.1), 1)]))
        assert newer.ids.tolist() == [7]

    def test_nothing_to_be_had(self, tmp_path):
        def offline(text):
            raise rt.TrackDataError("offline")

        with pytest.raises(rt.TrackDataError, match="offline"):
            rt.load_region(HOME, str(tmp_path), offline)

    def test_a_square_without_tracks_is_kept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *a, **k: Reply(b'{"elements": []}'))
        assert rt.load_region(HOME, str(tmp_path), rt.Overpass(SERVERS)).ids.size == 0
        assert os.path.isfile(tmp_path / f"0_0_v{rt.DATA_VERSION}.npz")


class TestOverpass:
    @pytest.fixture(autouse=True)
    def no_waiting(self, monkeypatch):
        self.waits = []
        monkeypatch.setattr(rt.time, "sleep", self.waits.append)

    @pytest.mark.parametrize("body", [
        b'{"elements": [], "remark": "runtime error: Query timed out at line 3 after 91 seconds."}',
        b'{"elements": [{"type": "way", "id": 1}], "remark": "runtime error: out of memory"}',
    ])
    def test_a_reply_with_a_remark_is_a_failure_and_not_cached(self, tmp_path, monkeypatch, body):
        # Overpass gives its timeouts as 200 OK, with what it had so far
        monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *a, **k: Reply(body))
        with pytest.raises(rt.TrackDataError, match="runtime error"):
            rt.load_region(HOME, str(tmp_path), rt.Overpass(SERVERS))
        assert os.listdir(tmp_path) == []

    def test_nothing_along_a_line_passes_on_to_the_next_server(self, tmp_path, monkeypatch):
        # overpass-api.de answered every query along a line so: no ways, no remark, 200 OK
        railway = _way(1, _arc(0.05, 0.95, 0.15), 1, {"railway": "rail"})
        replies = {SERVERS[0]: b'{"elements": []}', SERVERS[1]: json.dumps({"elements": [railway]}).encode()}
        monkeypatch.setattr(rt.urllib.request, "urlopen",
                            lambda request, timeout, context: Reply(replies[request.full_url]))
        ways = rt.load_corridor("A", "B", *_straight("A", "B"), str(tmp_path), rt.Overpass(SERVERS))
        assert ways.ids.tolist() == [1]

    def test_nothing_along_a_line_from_every_server_is_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *a, **k: Reply(b'{"elements": []}'))
        with pytest.raises(rt.TrackDataError, match="no railways along the line"):
            rt.load_corridor("A", "B", *_straight("A", "B"), str(tmp_path), rt.Overpass(SERVERS, patience=1))
        assert os.listdir(tmp_path) == []

    def test_a_remark_passes_on_to_the_next_server(self, monkeypatch):
        replies = {SERVERS[0]: b'{"elements": [], "remark": "runtime error: timed out"}', SERVERS[1]: OK}
        monkeypatch.setattr(rt.urllib.request, "urlopen",
                            lambda request, timeout, context: Reply(replies[request.full_url]))
        assert rt.Overpass(SERVERS)("[out:json];") == [{"type": "way", "id": 1}]

    def test_no_status_check_before_the_query(self, monkeypatch):
        tried = []

        def urlopen(request, timeout, context):
            tried.append((request.full_url, timeout))
            return Reply(OK)

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        rt.Overpass(SERVERS)("[out:json];")
        assert tried == [(SERVERS[0], rt.TIMEOUT)]

    def test_a_server_that_failed_is_asked_again(self, monkeypatch):
        tried = []

        def urlopen(request, timeout, context):
            tried.append(request.full_url)
            if len(tried) <= 2:                      # both fail at first
                raise OSError("502 Bad Gateway")
            return Reply(OK)

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        assert rt.Overpass(SERVERS)("[out:json];") == [{"type": "way", "id": 1}]
        assert tried == [SERVERS[0], SERVERS[1], SERVERS[0]] and self.waits == [rt.RETRY_WAIT]

    def test_the_server_that_answered_is_asked_first_next_time(self, monkeypatch):
        tried = []

        def urlopen(request, timeout, context):
            tried.append(request.full_url)
            if request.full_url == SERVERS[0]:
                raise OSError("down")
            return Reply(OK)

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        servers = rt.Overpass(SERVERS)
        servers("[out:json];")
        tried.clear()
        servers("[out:json];")
        assert tried == [SERVERS[1]]

    def test_it_gives_up_after_its_patience_and_stays_given_up(self, monkeypatch):
        tried = []

        def urlopen(request, timeout, context):
            tried.append(request.full_url)
            raise OSError("down")

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        servers = rt.Overpass(SERVERS, patience=3 * rt.RETRY_WAIT)
        with pytest.raises(rt.TrackDataError, match="one: down.*two: down|two: down.*one: down"):
            servers("[out:json];")
        assert sum(self.waits) == 3 * rt.RETRY_WAIT and len(tried) == 2 * 3
        tried.clear()
        with pytest.raises(rt.TrackDataError, match="down"):
            servers("[out:json];")               # the next square asks nobody
        assert tried == []

    def test_an_answer_renews_its_patience(self, monkeypatch):
        answers = iter([OSError("down"), OSError("down"), OK, OSError("down"), OSError("down"), OK])

        def urlopen(request, timeout, context):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return Reply(answer)

        monkeypatch.setattr(rt.urllib.request, "urlopen", urlopen)
        servers = rt.Overpass(SERVERS, patience=2 * rt.RETRY_WAIT)
        assert servers("[out:json];") and servers("[out:json];")

    @pytest.mark.parametrize("body", [b"<html>busy</html>", b'{"elements": [', b'{"version": 0.6}'])
    def test_a_reply_it_cannot_read_is_a_failure(self, monkeypatch, body):
        monkeypatch.setattr(rt.urllib.request, "urlopen", lambda *a, **k: Reply(body))
        with pytest.raises(rt.TrackDataError):
            rt.Overpass(SERVERS, patience=rt.RETRY_WAIT)("[out:json];")


class TestRouting:
    def test_a_link_follows_the_track(self, tmp_path):
        router = _router([_way(1, _arc(0.05, 0.95, 0.15), 1)], tmp_path)
        lon, lat = router.route("A", "B", False, *_straight("A", "B"))
        assert (lon[0], lat[0]) == STATIONS["A"] and (lon[-1], lat[-1]) == STATIONS["B"]
        assert lat.max() == pytest.approx(1.17, abs=0.01)             # along the bow, not straight
        assert router.used and list(router.loaded) == [HOME]

    def test_both_ways_round(self, tmp_path):
        router = _router([_way(1, _arc(0.05, 0.95, 0.15), 1)], tmp_path)
        there = router.route("A", "B", False, *_straight("A", "B"))
        back = router.route("B", "A", False, *_straight("B", "A"))
        assert np.allclose(there[0][::-1], back[0])

    def test_gaps_in_the_track_are_bridged(self, tmp_path):
        points = _arc(0.05, 0.95, 0.15, n=200)
        router = _router([_way(1, points[:100], 1), _way(2, points[101:], 500)], tmp_path)   # ~1 km apart
        assert router.route("A", "B", False, *_straight("A", "B")) is not None

    def test_a_gap_is_bridged_though_the_tracks_meet_far_off(self, tmp_path):
        # With a whole country's tracks loaded, the two sides of a gap are nearly always joined somewhere else
        west = [(1.0 + 0.5 * t, 1.01) for t in np.linspace(0, 1, 40)]
        east = [(1.51 + 0.49 * t, 1.01) for t in np.linspace(0, 1, 40)]
        round_by = {"type": "way", "id": 3, "nodes": [1, 900, 901, 539],        # the far ends, ~90 km to the south
                    "geometry": [{"lon": lon, "lat": lat} for lon, lat in
                                 [west[0], (1.0, 0.2), (2.0, 0.2), east[-1]]],
                    "tags": {"railway": "rail", "highspeed": "yes"}}
        router = _router([_way(1, west, 1), _way(2, east, 500), round_by], tmp_path)
        lon, lat = router.route("A", "B", False, *_straight("A", "B"))
        assert lat.min() > 0.99                                                 # straight on, not round by the south

    def test_a_station_joins_a_line_behind_a_nearer_one(self, tmp_path):
        # A's nearest track curves round its north-east, so the main line behind it lies in the same directions,
        # and the two meet only ~100 km off: that they are one network must not hide the main line from A
        radius = 0.005
        curve = [(1 + radius * np.sin(np.radians(b)), 1 + radius * np.cos(np.radians(b))) for b in (5, 25, 50, 70, 88)]
        main = [(1.0 + t, 1.03) for t in np.linspace(0.002, 1, 60)]
        branch = curve + [(1.3, 0.3), (2.0, 0.3), main[-1]]
        side = {"type": "way", "id": 2, "nodes": [*range(700, 700 + len(branch) - 1), 59],
                "geometry": [{"lon": lon, "lat": lat} for lon, lat in branch],
                "tags": {"railway": "rail", "highspeed": "yes"}}
        router = _router([_way(1, main, 0), side], tmp_path)
        lon, lat = router.route("A", "B", False, *_straight("A", "B"))
        assert lat.min() > 0.99 and rt.path_km(lon, lat) < 125

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
        stations = {"A": (1.0, 1.0), "B": (1.5, 1.0)}
        short = _way(1, [(1.2, 1.01), (1.25, 1.01), (1.3, 1.01)], 1)            # 11 km of 55 km on track
        router = _router([short], tmp_path, stations=stations)
        lon, lat = np.linspace(1, 1.5, 10), np.ones(10)
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

        router = rt.TrackRouter(STATIONS, str(tmp_path), offline)
        with incomplete.noting() as noted:
            assert router.route("A", "B", False, *_straight("A", "B")) is None
            assert router.route("B", "C", False, *_straight("B", "C")) is None
        assert len(asked) == 1 and set(router.failed) == {HOME}
        assert len(noted) == 1 and "city to city" in noted[0] and "offline" in noted[0]

    def test_a_failed_square_stops_only_the_links_that_need_it(self, tmp_path):
        stations = {**STATIONS, "D": (21.0, 1.0), "E": (22.0, 1.0)}
        far_track = [(lon + 20, lat) for lon, lat in _arc(0.05, 0.95, 0.15)]
        far_link = np.linspace(21, 22, 20), np.ones(20)

        def fetch(text):
            if "(0,0,4,4)" in text:
                raise rt.TrackDataError("504")
            return [_way(2, far_track, 500)]

        router = rt.TrackRouter(stations, str(tmp_path), fetch)
        with incomplete.noting() as noted:
            router.prepare([_straight("A", "B"), far_link])
            assert router.route("A", "B", False, *_straight("A", "B")) is None
            assert router.route("D", "E", False, *far_link) is not None
        assert set(router.failed) == {HOME} and router.used
        assert len(noted) == 1 and "1 of 2" in noted[0]

    def test_a_map_is_noted_once_however_many_squares_fail(self, tmp_path):
        def offline(text):
            raise rt.TrackDataError("offline")

        router = rt.TrackRouter(STATIONS, str(tmp_path), offline)
        with incomplete.noting() as noted:
            router.prepare([(np.linspace(1, 30, 120), np.ones(120))])
        assert len(router.failed) >= 7 and len(noted) == 1

    def test_squares_downloaded_before_a_failure_are_kept_for_the_next_run(self, tmp_path):
        path = np.linspace(1, 6, 20), np.ones(20)                # two squares, (0, 0) and (0, 4)

        def only_the_first(text):
            if "(0,4,4,8)" in text:
                raise rt.TrackDataError("504")
            return [_way(1, _arc(0.05, 0.95, 0.15), 1)]

        with incomplete.noting():
            rt.TrackRouter(STATIONS, str(tmp_path), only_the_first).prepare([path])
        asked = []
        rt.TrackRouter(STATIONS, str(tmp_path), _fetcher([], asked)).prepare([path])
        assert len(asked) == 1 and "(0,4,4,8)" in asked[0]

    def test_tracks_downloaded_later_are_routed_on(self, tmp_path):
        answers = iter([[], [_way(1, _arc(0.05, 0.95, 0.15), 1)]])
        router = rt.TrackRouter(STATIONS, str(tmp_path), lambda text: [] if "around:" in text else next(answers))
        assert router.route("A", "B", False, *_straight("A", "B")) is None
        router.prepare([(np.linspace(5, 6, 5), np.ones(5))])     # another square: a new graph
        router.routes.clear()
        assert router.route("A", "B", False, *_straight("A", "B")) is not None

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


def _by_kind(fast, railways, asked=None):
    """An Overpass that gives *fast* to a region's query and *railways* to a link's."""
    def fetch(text):
        if asked is not None:
            asked.append(text)
        return railways if "around:" in text else fast
    return fetch


class TestTheRailwaysAroundALink:
    BOW = _arc(0.05, 0.95, 0.15)

    def test_query_takes_the_railways_near_the_path_but_not_sidings(self):
        text = rt.corridor_query(np.array([1.5, 2.25]), np.array([50.0, 51.0]))
        assert '[!"service"]' in text and '"railway"="rail"' in text
        assert "(around:15000,50.0000,1.5000,51.0000,2.2500)" in text

    def test_an_open_link_with_no_fast_way_follows_them(self, tmp_path):
        router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([], [_way(1, self.BOW, 1, {"railway": "rail"})]))
        lon, lat = router.route("A", "B", False, *_straight("A", "B"))
        assert lat.max() == pytest.approx(1.17, abs=0.01) and router.used

    def test_they_join_the_fast_tracks_they_meet(self, tmp_path):
        # Half the link is tagged fast, half is not: neither alone gets from A to B
        fast = _way(1, self.BOW[:30], 1)
        slow = _way(2, self.BOW[29:], 30, {"railway": "rail"})
        router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([fast], [slow]))
        lon, lat = router.route("A", "B", False, *_straight("A", "B"))
        assert lat.max() == pytest.approx(1.17, abs=0.01)

    def test_not_asked_for_a_link_with_a_fast_way(self, tmp_path):
        asked = []
        router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([_way(1, self.BOW, 1)], [], asked))
        assert router.route("A", "B", False, *_straight("A", "B")) is not None
        assert not any("around:" in text for text in asked)

    def test_not_asked_for_a_planned_link(self, tmp_path):
        # The old line beside a planned one is not the line being built
        asked = []
        router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([], [_way(1, self.BOW, 1)], asked))
        assert router.route("A", "B", True, *_straight("A", "B")) is None
        assert not any("around:" in text for text in asked)

    def test_tracks_being_built_do_not_serve_an_open_link(self, tmp_path):
        building = _way(1, self.BOW, 1, {"railway": "construction", "highspeed": "yes"})
        router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([building], []))
        assert router.route("A", "B", False, *_straight("A", "B")) is None

    def test_a_long_way_round_is_refused_there_too(self, tmp_path, caplog):
        far = _way(1, _arc(0.05, 0.95, 0.8), 1, {"railway": "rail"})            # bowed ~90 km north
        router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([], [far]))
        with caplog.at_level(logging.INFO, logger="rail_tracks"):
            assert router.route("A", "B", False, *_straight("A", "B")) is None
        assert "drawn city to city" in caplog.text

    def test_cached_for_the_next_run(self, tmp_path):
        asked = []
        for _ in range(2):
            router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([], [_way(1, self.BOW, 1)], asked))
            assert router.route("A", "B", False, *_straight("A", "B")) is not None
        assert sum("around:" in text for text in asked) == 1

    def test_a_path_changed_in_the_data_is_downloaded_anew(self, tmp_path):
        asked = []
        for lat in (1.0, 1.01):
            router = rt.TrackRouter(STATIONS, str(tmp_path), _by_kind([], [], asked))
            router.route("A", "B", False, np.array([1.0, 2.0]), np.array([1.0, lat]))
        assert sum("around:" in text for text in asked) == 2

    def test_a_failed_download_is_noted_once_and_not_cached(self, tmp_path):
        calls = []

        def fetch(text):
            if "around:" in text:
                calls.append(text)
                raise rt.TrackDataError("busy")
            return []

        router = rt.TrackRouter(STATIONS, str(tmp_path), fetch)
        with incomplete.noting() as noted:
            assert router.route("A", "B", False, *_straight("A", "B")) is None
            assert router.route("B", "C", False, *_straight("B", "C")) is None
        assert len(noted) == 1 and "could not be downloaded" in noted[0] and len(calls) == 2
        assert not any(name.startswith("corridor_") for name in os.listdir(tmp_path))


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
        # Only the squares of the links near the map were asked for: nothing beyond Europe
        squares = [q for q in asked if "around:" not in q]
        assert 0 < len(squares) <= 40 and not any(re.search(r"\(\d+,1\d\d,", q) for q in squares)
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
