"""Tests for ne_local.py (no network; small Natural Earth-like shapefiles made here, Wikidata answered here)."""

import io
import json
import os
import urllib.error
from typing import Any

import numpy as np
import pytest
import shapefile
import shapely

import ne_local as nl
import osm_local as ol


@pytest.mark.parametrize("title, label, river, name", [
    ("Marea Chinei de Est", "Marea Chinei", False, "Marea Chinei de Est"),      # the article's title first
    ("", "lacul Champlain", False, "Lacul Champlain"),                          # else the label, with a capital
    ("Ohře (râu)", "Ohre", True, "Ohře"),                                       # without the note in brackets
    ("", "Râul Latorița, Ucraina", True, "Latorița"),                           # or after a comma
    ("Râul Mureș", "Mureș", True, "Mureș"),                                     # a river without the word
    ("Fluviul Zambezi", "", True, "Zambezi"),
    ("Fluviu Sfântul Laurențiu", "", True, "Sfântul Laurențiu"),
    ("Fluviul Galben", "fluviul Galben", True, "Fluviul Galben"),               # unless an adjective follows
    ("Râul Negru", "", True, "Râul Negru"),
    ("Lacul Ladoga", "", False, "Lacul Ladoga"),                                # a lake keeps it
    ("Râmnicu Vâlcea", "", False, "Râmnicu Vâlcea"),
    ("Piatra Neamţ", "", False, "Piatra Neamț"),                                # the comma below
    ("", "", True, ""),
])
def test_romanian(title, label, river, name):
    assert nl.romanian(title, label, river) == name


# ---------------------------------------------------------------------------
# Wikidata
# ---------------------------------------------------------------------------


def _entity(title: str = "", label: str = "") -> dict[str, Any]:
    return {"labels": {"ro": {"value": label}} if label else {},
            "sitelinks": {"rowiki": {"title": title}} if title else {}}


class TestWikidata:
    def test_titles_and_labels_in_batches(self, monkeypatch):
        asked = []

        def urlopen(request, timeout=0):
            ids = request.full_url.split("ids=")[1].split("&")[0].split("%7C")
            asked.append(ids)
            return io.BytesIO(json.dumps({"entities": {q: _entity(f"T{q}", f"L{q}") for q in ids}}).encode())
        monkeypatch.setattr(nl.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(nl.time, "sleep", lambda s: None)
        items = [f"Q{i}" for i in range(1, 121)] + ["Q1"]                         # asked once each
        found = nl.ask_wikidata(items)
        assert [len(a) for a in asked] == [50, 50, 20] and len(found) == 120
        assert found["Q7"] == ("TQ7", "LQ7")

    def test_waits_when_busy(self, monkeypatch):
        calls, waits = [], []

        def urlopen(request, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "3"}, None)  # type: ignore[arg-type]
            return io.BytesIO(json.dumps({"entities": {"Q1": _entity(label="Dunărea"), "Q2": {}}}).encode())
        monkeypatch.setattr(nl.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(nl.time, "sleep", waits.append)
        assert nl.ask_wikidata(["Q1", "Q2"]) == {"Q1": ("", "Dunărea"), "Q2": ("", "")}
        assert waits[0] == 3

    def test_no_answer(self, monkeypatch):
        def offline(request, timeout=0):
            raise urllib.error.URLError("offline")
        monkeypatch.setattr(nl.urllib.request, "urlopen", offline)
        monkeypatch.setattr(nl.time, "sleep", lambda s: None)
        with pytest.raises(nl.NEDataError, match="Wikidata did not answer"):
            nl.ask_wikidata(["Q1"])


# ---------------------------------------------------------------------------
# Natural Earth
# ---------------------------------------------------------------------------


def _write(folder, name: str, kind: int, fields: list[tuple[str, Any]], rows: list[tuple[Any, list]]) -> str:
    path = os.path.join(folder, f"ne_10m_{name}.shp")
    with shapefile.Writer(path, shapeType=kind, encoding="utf-8") as w:
        for field, type_ in fields:
            w.field(field, type_, size=80) if type_ == "C" else w.field(field, type_, size=18, decimal=4)
        for geometry, values in rows:
            if kind == shapefile.POINT:
                w.point(*geometry)
            elif kind == shapefile.POLYLINE:
                w.line([geometry])
            else:
                w.poly([geometry])
            w.record(*values)
    return path


@pytest.fixture(scope="module")
def natural_earth(tmp_path_factory):
    """A natural_earth() over small files of each layer, as Cartopy's would read them."""
    folder = str(tmp_path_factory.mktemp("ne"))
    square = lambda x, y, d: [(x, y), (x, y + d), (x + d, y + d), (x + d, y), (x, y)]       # noqa: E731
    paths = {
        "land": _write(folder, "land", shapefile.POLYGON, [("featurecla", "C")], [(square(10, 40, 20), ["Land"])]),
        "admin_0_boundary_lines_land": _write(folder, "admin_0_boundary_lines_land", shapefile.POLYLINE,
                                              [("FEATURECLA", "C")], [([(20, 41), (20, 59)], ["International"])]),
        "populated_places": _write(folder, "populated_places", shapefile.POINT,
                                   [("FEATURECLA", "C"), ("NAME", "C"), ("NAME_EN", "C"), ("POP_MAX", "N"),
                                    ("WIKIDATAID", "C")],
                                   [((26.1, 44.43), ["Admin-0 capital", "Bucharest", "Bucharest", 1942000, "Q19660"]),
                                    ((23.6, 46.77), ["Admin-1 capital", "Cluj-Napoca", "Cluj-Napoca", 316748, "Q100188"]),
                                    ((24.0, 45.0), ["Populated place", "Tiny", "Tiny", 2000, "Q5"]),          # too small
                                    ((25.0, 47.0), ["Populated place", "Bâlea", "Balea", 60000, ""])]),
        "rivers_lake_centerlines": _write(folder, "rivers_lake_centerlines", shapefile.POLYLINE,
                                          [("featurecla", "C"), ("name", "C"), ("name_en", "C"), ("wikidataid", "C")],
                                          [([(12, 48), (18, 47)], ["River", "Donau", "Danube", "Q1653"]),
                                           ([(18, 47), (25, 44)], ["River", "Dunav", "Danube", "Q1653"]),
                                           ([(14, 50), (15, 51)], ["Lake Centerline", "Lake", "Lake", "Q9"])]),
        "rivers_europe": _write(folder, "rivers_europe", shapefile.POLYLINE,
                                [("featurecla", "C"), ("name", "C"), ("name_en", "C"), ("wikidataid", "C")],
                                [([(12, 48), (18, 47)], ["River", "Donau", "Danube", "Q1653"]),      # already had
                                 ([(21, 46.4), (22.5, 46.5)], ["River", "Crisul Alb", "Crisul Alb", "Q833573"])]),
        "lakes": _write(folder, "lakes", shapefile.POLYGON,
                        [("featurecla", "C"), ("name", "C"), ("name_en", "C"), ("wikidataid", "C")],
                        [(square(17, 46, 1), ["Lake", "Balaton", "Lake Balaton", "Q35"])]),
        "lakes_europe": _write(folder, "lakes_europe", shapefile.POLYGON,
                               [("featurecla", "C"), ("name", "C"), ("name_en", "C"), ("wikidataid", "C")],
                               [(square(17, 46, 1), ["Lake", "Balaton", "Lake Balaton", "Q35"]),
                                (square(29, 45, 0.2), ["Reservoir", "Razim", "Razim", "Q36"])]),
        "admin_0_countries": _write(folder, "admin_0_countries", shapefile.POLYGON,
                                    [("NAME_EN", "C"), ("ISO_A2_EH", "C"), ("WIKIDATAID", "C"), ("LABEL_X", "N"),
                                     ("LABEL_Y", "N")],
                                    [(square(20, 43, 8), ["Romania", "RO", "Q218", 24.9, 45.8]),
                                     (square(10, 40, 2), ["Somaliland", "-99", "Q34754", 15.0, 45.0])]),
        "geography_marine_polys": _write(folder, "geography_marine_polys", shapefile.POLYGON,
                                         [("featurecla", "C"), ("name", "C"), ("name_en", "C"), ("wikidataid", "C"),
                                          ("scalerank", "N")],
                                         [(square(28, 41, 5), ["sea", "Black Sea", "Black Sea", "Q166", 1]),
                                          (square(0, 30, 10), ["ocean", "North Atlantic Ocean", "North Atlantic Ocean",
                                                               "Q97", 0]),
                                          (square(29, 46, 0.1), ["lagoon", "Lagoon", "Lagoon", "Q8", 8])]),
    }
    return lambda scale, category, name: paths[name]


_READ = nl.read_natural_earth         # as written, for the tests that put another in its place
WIKIDATA = {"Q19660": ("București", "București"), "Q100188": ("Cluj-Napoca", ""), "Q1653": ("Dunărea", ""),
            "Q833573": ("Râul Criș", ""), "Q35": ("Lacul Balaton", ""), "Q218": ("România", ""),
            "Q34754": ("Somaliland", ""), "Q166": ("Marea Neagră", ""), "Q97": ("", "Oceanul Atlantic")}
CLDR = {"RO": "România"}


class TestRead:
    def test_features(self, natural_earth):
        features = nl.read_natural_earth(natural_earth)
        assert [p["en"] for p in features["places"]] == ["Bucharest", "Cluj-Napoca", "Balea"]
        assert features["places"][0]["kind"] == "national_capital" and features["places"][1]["kind"] == "city"
        assert [r["local"] for r in features["rivers"]] == ["Donau", "Dunav", "Crisul Alb"]   # no centreline twice
        assert [w["en"] for w in features["lakes"]] == ["Lake Balaton", "Razim"]
        assert [s["en"] for s in features["seas"]] == ["Black Sea", "North Atlantic Ocean"]  # not the lagoon
        assert features["seas"][1]["kind"] == "ocean"
        x, y = features["seas"][0]["points"][0]
        assert 28 < x < 33 and 41 < y < 46

    def test_table_in_both_languages(self, natural_earth):
        table = nl.build_table(nl.read_natural_earth(natural_earth), WIKIDATA, CLDR)
        assert table["place_ro"].tolist() == ["București", "Cluj-Napoca", "Bâlea"]     # Bâlea: no item, its own
        assert table["place_en"].tolist() == ["Bucharest", "Cluj-Napoca", "Balea"]
        assert table["river_ro"].tolist() == ["Dunărea", "Crișul Alb"]                 # the pieces joined
        assert table["river_en"].tolist() == ["Danube", "Crișul Alb"]          # not the Körös it is tagged as
        assert table["river_km"][0] > 1000
        assert table["lake_ro"].tolist() == ["Lacul Balaton", ""]                     # Razim: no Romanian name
        # Somaliland, with no ISO code, is not named
        assert table["label_ro"].tolist() == ["România", "Oceanul Atlantic", "Marea Neagră"]
        assert table["label_kinds"].tolist() == ["country", "ocean", "sea"]
        assert table["label_points"][0].tolist() == [24.9, 45.8]


class TestCache:
    @pytest.fixture
    def online(self, monkeypatch, natural_earth):
        asked = []
        monkeypatch.setattr(nl, "read_natural_earth", lambda: _READ(natural_earth))
        monkeypatch.setattr(nl, "ask_wikidata", lambda items: asked.append(sorted(items)) or
                            {q: WIKIDATA.get(q, ("", "")) for q in items})
        monkeypatch.setattr(nl, "cldr_territories", lambda language: CLDR)
        return asked

    def test_built_once(self, online, tmp_path):
        base = nl.load_base("ro", str(tmp_path))
        assert [p.name for p in base.places] == ["București", "Cluj-Napoca", "Bâlea"]
        assert [r.name for r in base.rivers] == ["Dunărea", "Crișul Alb"]
        assert [w.name for w in base.lakes] == ["Lacul Balaton"]
        assert [w.name for w in base.reservoirs] == [""]                                 # Razim's, as written here
        assert [a.name for a in base.labels] == ["România", "Oceanul Atlantic", "Marea Neagră"]
        assert "Wikidata" in ol.attribution(base) and "CLDR" in ol.attribution(base)
        english = nl.load_base("en", str(tmp_path))
        assert len(online) == 1 and "Q1653" in online[0]
        assert [p.name for p in english.places][:2] == ["Bucharest", "Cluj-Napoca"]
        assert [a.name for a in english.labels][:2] == ["Romania", "North Atlantic Ocean"]
        assert ol.attribution(english) == nl.ATTRIBUTION
        assert len(english.land) == 1 and len(english.borders) == 1

    def test_wikidata_asked_once(self, online, tmp_path):
        nl.load_base("ro", str(tmp_path))
        os.remove(tmp_path / f"ne_local_v{nl.DATA_VERSION}.npz")                         # as for a new version
        assert [p.name for p in nl.load_base("ro", str(tmp_path)).places][0] == "București"
        assert len(online) == 1 and sorted(os.listdir(tmp_path)) == [f"ne_local_v{nl.DATA_VERSION}.npz",
                                                                    "wikidata_ro.json"]

    def test_unreachable(self, monkeypatch, natural_earth, tmp_path):
        monkeypatch.setattr(nl, "read_natural_earth", lambda: _READ(natural_earth))

        def offline(items):
            raise nl.NEDataError("Wikidata did not answer: offline")
        monkeypatch.setattr(nl, "ask_wikidata", offline)
        with pytest.raises(ol.OSMDataError, match="Wikidata did not answer"):
            nl.load_base("ro", str(tmp_path))
        assert not os.listdir(tmp_path)

    def test_cities_wikidata_titles_otherwise(self, natural_earth):
        features = nl.read_natural_earth(natural_earth)
        features["places"][1]["en"] = "Brussels"
        table = nl.build_table(features, WIKIDATA | {"Q100188": ("Orașul Bruxelles", "")}, CLDR)
        assert table["place_ro"][1] == "Bruxelles"

    def test_files_missing(self, monkeypatch, tmp_path):
        def missing():
            raise OSError("no such file")
        monkeypatch.setattr(nl, "read_natural_earth", missing)
        with pytest.raises(nl.NEDataError, match="Could not read Natural Earth's data: no such file"):
            nl.load_base("en", str(tmp_path))

    def test_language(self):
        with pytest.raises(ValueError, match="language must be one of en, ro"):
            nl.load_base("fr")


def test_label_points_largest_part_first():
    two = shapely.MultiPolygon([shapely.box(0, 0, 1, 1), shapely.box(10, 0, 14, 4)])
    points = nl._label_points(two, 3)
    assert np.allclose(points[0], (12, 2), atol=0.1)                  # the middle of the largest first
    assert len(points) == 4 and all(10 < x < 14 and 0 < y < 4 for x, y in points[:3])
    assert all(np.hypot(x - 12, y - 2) >= 1 for x, y in points[1:3])          # spread over it
    assert 0 < points[3][0] < 1                                               # then the other part
    assert nl._label_points(two, 1) == points[:1]
