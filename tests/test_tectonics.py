"""Tests for tectonics.py — plate boundaries, earthquakes and volcanoes: reading, caching and drawing (no network)."""

import gzip
import io
import os
import time

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import tectonics as t
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _step(cls, lon=0.0):
    return {"properties": {"STEPCLASS": cls, "STARTLONG": lon, "STARTLAT": 0.0, "FINALLONG": lon + 1, "FINALLAT": 1.0}}


STEPS = {"features": [_step("OSR"), _step("SUB", 10), _step("OTF", 20), _step("CTF", 30), _step("???", 40)]}
PLATES = {"features": [
    {"properties": {"PlateName": "Big"}, "geometry": {"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [30, 0], [30, 30], [0, 30], [0, 0]]], [[[170, 0], [171, 0], [171, 1], [170, 1], [170, 0]]]]}},
]}


def _quake(lon, lat, depth, mag):
    return {"geometry": {"coordinates": [lon, lat, depth]}, "properties": {"mag": mag}}


class TestReading:
    def test_plates(self):
        table = t.read_plates(STEPS, PLATES)
        assert list(table["kinds"]) == ["divergent", "convergent", "transform", "transform"]   # unknown classes left out
        assert table["starts"][1].tolist() == [10, 0] and table["ends"][1].tolist() == [11, 1]
        assert list(table["names"]) == ["Big"]
        lon, lat = table["label_points"][0]
        assert 0 < lon < 30 and 0 < lat < 30                       # on the larger part

    def test_quakes(self):
        table = t.read_quakes({"features": [_quake(1, 2, 10, 6.5), _quake(3, 4, None, 7.0),
                                            {"geometry": None, "properties": {"mag": 6}},
                                            _quake(5, 6, 5, None)]})
        assert table["lon"].tolist() == [1, 3] and table["depth"].tolist() == [10, 0]
        assert table["magnitude"].tolist() == [6.5, 7.0]

    def test_volcanoes(self):
        table = t.read_volcanoes({"features": [
            {"geometry": {"coordinates": [10, 20]}, "properties": {"Volcano_Name": "A", "Last_Eruption_Year": 1991}},
            {"geometry": {"coordinates": [30, 40]}, "properties": {"Volcano_Name": "B", "Last_Eruption_Year": None}}]})
        assert table["names"].tolist() == ["A", "B"]
        assert table["last_eruption"][0] == 1991 and np.isnan(table["last_eruption"][1])

    def test_quake_windows_stay_under_the_service_limit(self):
        assert t._quake_windows(6.0, 2026) == [(1900, 2026)]
        windows = t._quake_windows(5.0, 2026)
        assert windows[0] == (1900, 1969) and windows[-1] == (2020, 2026)
        assert all(last - first <= 9 for first, last in windows[1:])


class TestCaching:
    def test_quakes_fetched_then_cached_then_kept_when_offline(self, tmp_path, monkeypatch):
        calls = []

        def fake(url, timeout=180):
            calls.append(url)
            return {"features": [_quake(1, 2, 30, 6.1)]}

        monkeypatch.setattr(t, "_get_json", fake)
        quakes = t.load_quakes(6.0, str(tmp_path))
        assert quakes.lon.tolist() == [1] and quakes.min_magnitude == 6.0 and len(calls) == 1
        t.load_quakes(6.0, str(tmp_path))
        assert len(calls) == 1                                      # fresh: not fetched again

        path = next(tmp_path.iterdir())
        old = time.time() - (t.REFRESH_DAYS + 1) * 86400
        os.utime(path, (old, old))
        monkeypatch.setattr(t, "_get_json", lambda url, timeout=180: (_ for _ in ()).throw(OSError("offline")))
        assert t.load_quakes(6.0, str(tmp_path)).lon.tolist() == [1]      # stale beats nothing

    @pytest.mark.parametrize("encoding", ["gzip", None])
    def test_answers_asked_for_compressed(self, monkeypatch, encoding):
        body = b'{"features": []}'
        sent = {}

        class Response(io.BytesIO):
            headers = {"Content-Encoding": encoding} if encoding else {}

        def urlopen(request, timeout):
            sent.update(request.headers)
            return Response(gzip.compress(body) if encoding else body)

        monkeypatch.setattr(t.urllib.request, "urlopen", urlopen)
        assert t._get_json("https://example.org") == {"features": []}
        assert sent["Accept-encoding"] == "gzip"                    # whether or not the server compresses

    def test_failures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(t, "_get_json", lambda url, timeout=180: (_ for _ in ()).throw(OSError("offline")))
        for load in (lambda: t.load_plates(str(tmp_path)), lambda: t.load_quakes(6.0, str(tmp_path)),
                     lambda: t.load_volcanoes(str(tmp_path))):
            with pytest.raises(t.TectonicsDataError):
                load()
        with pytest.raises(ValueError, match="min_magnitude"):
            t.load_quakes(4.0, str(tmp_path))


class TestDrawing:
    def test_plates_by_kind_and_named(self):
        ax = _globe(15, 15)
        table = t.read_plates(STEPS, PLATES)
        t.draw_plates(ax, t.Plates(**table))
        assert len(ax.collections) == 3
        assert [x.get_text() for x in ax.texts] == ["Big Plate"]

    def test_quakes_and_volcanoes_only_in_sight(self):
        ax = _globe(0, 0)
        quakes = t.Quakes(np.array([0.0, 180.0]), np.array([0.0, 0.0]), np.array([10.0, 500.0]),
                          np.array([6.0, 8.0]), 6.0)
        t.draw_quakes(ax, quakes)
        assert np.asarray(ax.collections[0].get_offsets()).shape[0] == 1            # the far side is left out
        volcanoes = t.Volcanoes(np.array([0.0, 10.0]), np.array([0.0, 0.0]), np.array(["A", "B"]),
                                np.array([2000.0, np.nan]))
        t.draw_volcanoes(ax, volcanoes)
        assert [np.asarray(c.get_offsets()).shape[0] for c in ax.collections[1:]] == [1, 1]   # one old, one recent

    def test_depth_colours_and_sizes(self):
        assert t.depth_colours(np.array([10, 100, 600])) == [c for _, _, c in t.DEPTH_CLASSES]
        assert t.quake_size(7.0) == pytest.approx(3 * t.quake_size(6.0))

    def test_key_and_credits(self):
        ax = _globe()
        quakes = t.Quakes(np.zeros(3), np.zeros(3), np.zeros(3), np.full(3, 6.0), 6.0)
        legend = t.add_tectonics_legend(ax, plates=True, quakes=quakes, volcanoes=True)
        assert legend is not None
        labels = [x.get_text() for x in legend.get_texts()]
        assert len(labels) == 3 + 3 + 4 + 2 and "Magnitude 9" in labels
        assert "3)" in legend.get_title().get_text()
        assert t.add_tectonics_legend(ax) is None
        assert t.credits(True, False, True) == [t.PLATES_ATTRIBUTION, t.VOLCANOES_ATTRIBUTION]
