"""Tests for osm_local.py (no network; small Geofabrik-like and GISCO-like archives made here)."""

import io
import json
import os
import zipfile
from typing import Any
from unittest import mock

import numpy as np
import pytest
import shapefile
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

import osm_local as ol
from cities import label_space
from rotation import globe_projection


@pytest.mark.parametrize("text, expected", [
    ("Mureș", True), ("Cluj-Napoca", True), ("Ceadîr-Lunga", True), ("Тирасполь", False), ("Dunav/Дунав", False),
    ("", False), ("123", False),
])
def test_latin(text, expected):
    assert ol.latin(text) is expected


@pytest.mark.parametrize("raw, name", [
    ("Mureș", "Mureș"),
    ("Râul  Mureş", "Râul Mureș"),                     # spaces and the cedilla
    ("Dunărea - Дунав", "Dunărea"),
    ("Dunav/Dunărea", "Dunărea"),                      # both Latin: the Romanian one
    ("Дунав/Dunărea", "Dunărea"),
    ("Dunărea (Brațul Sulina)", "Brațul Sulina"),
    ("Brațul Borcea (Dunărea)", "Brațul Borcea"),
    ("Râul Mare - Cugir", "Râul Mare"),
    ("Drobeta-Turnu", "Drobeta-Turnu"),                # a hyphen without spaces is part of the name
    ("Вит", ""),
    ("", ""),
])
def test_river_name(raw, name):
    assert ol.river_name(raw) == name


def test_shorter_names_only_where_osm_uses_them():
    names = ["Mureș", "Râul Mureș", "Râul Doamnei", "Pârâul Câinelui", "Câinelui", "Râul Negru"]
    assert ol.shorter_names(names) == {"Râul Mureș": "Mureș", "Pârâul Câinelui": "Câinelui"}


SIRUTA = """﻿SIRUTA;DENLOC;CODP;JUD;SIRSUP;TIP;NIV;MED;REGIUNE;FSJ;NUTS;LAU
10;JUDEŢUL ALBA;0;1;1;40;1;0;7;1;RO121;
1017;MUNICIPIUL ALBA IULIA;0;1;10;1;2;1;7;1;RO121;RO_1017
2001;POIENILE DE SUB MUNTE;0;24;1;3;3;1;7;1;RO114;
2002;STRÂMBA-JIU;0;18;1;3;3;1;7;1;RO411;
2003;VĂLCANI;0;35;1;3;3;1;7;1;RO424;
2004;VÂLCANI;0;35;1;3;3;1;7;1;RO424;
2005;ILVA MICA;0;6;1;3;3;1;7;1;RO112;
"""


def test_siruta_spellings():
    spellings = ol.siruta_spellings(SIRUTA)
    assert spellings["poieniledesubmunte"] == "Poienile de sub Munte" and "alba" not in spellings   # no counties
    assert spellings["albaiulia"] == "Alba Iulia"


@pytest.mark.parametrize("osm, name", [
    ("Poienile de Sub Munte", "Poienile de sub Munte"),       # SIRUTA's case
    ("Strâmba Jiu", "Strâmba-Jiu"),                           # and hyphen
    ("Ilva Mică", "Ilva Mică"),                               # SIRUTA leaves out the breve: OSM's kept
    ("Vălcani", "Vălcani"),                                   # two spellings in SIRUTA: OSM's kept
    ("Roșiorii de Vede", "Roșiori de Vede"),                  # SPELLINGS
    ("Chișinău", "Chișinău"),
])
def test_official(osm, name):
    assert ol.official(osm, ol.siruta_spellings(SIRUTA)) == name


@pytest.mark.parametrize("url, date", [
    ("https://download.geofabrik.de/europe/romania-261022-free.shp.zip", "22 Oct 2026"),
    ("https://download.geofabrik.de/europe/romania-latest-free.shp.zip", ""),
    ("https://download.geofabrik.de/europe/romania-261322-free.shp.zip", ""),
])
def test_extract_date(url, date):
    assert ol._extract_date(url) == date


class TestRivers:
    def test_pieces_of_a_name_that_join_are_one_river(self):
        a = shapely.LineString([(0, 0), (1, 0)])
        b = shapely.LineString([(1, 0), (2, 0)])
        c = shapely.LineString([(2.001, 0), (3, 0)])          # a hair apart: the same river
        far = shapely.LineString([(10, 0), (11, 0)])           # another river of the same name
        rivers = ol.merge_rivers([("Olt", "Olt", a), ("Olt", "Olt", b), ("Olt", "Olt", c), ("Olt", "Olt", far)])
        assert [r.name for r in rivers] == ["Olt", "Olt"]
        assert rivers[0].km == pytest.approx(3 * 111.32, rel=0.01) and rivers[1].km == pytest.approx(111.32, rel=0.01)

    def test_a_name_in_another_script_joins_up_but_is_not_written(self):
        rivers = ol.merge_rivers([("Вит", "", shapely.LineString([(0, 0), (1, 0)])),
                                  ("Вит", "", shapely.LineString([(1, 0), (2, 0)]))])
        assert [(r.name, round(r.km)) for r in rivers] == [("", 223)]

    def test_unnamed_pieces_join_only_between_branches(self):
        # A stream and its two branches: three runs, not one network
        trunk = shapely.LineString([(0, 0), (1, 0)])
        left, right = shapely.LineString([(1, 0), (2, 1)]), shapely.LineString([(1, 0), (2, -1)])
        assert len(ol.merge_rivers([("", "", trunk), ("", "", left), ("", "", right)])) == 3
        # Two pieces end to end are one run
        rivers = ol.merge_rivers([("", "", trunk), ("", "", shapely.LineString([(1, 0), (2, 0)]))])
        assert len(rivers) == 1 and rivers[0].km == pytest.approx(2 * 111.32, rel=0.01)

    def test_length_shrinks_with_latitude(self):
        at_60 = ol._km(shapely.LineString([(0, 60), (1, 60)]))
        assert at_60 == pytest.approx(111.32 / 2, rel=0.01)

    def test_area(self):
        assert ol._km2(shapely.box(0, 0, 0.01, 0.01)) == pytest.approx(1.23, rel=0.01)


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def _shapefile(kind: int, fields: list[tuple[str, Any, int]], rows: list[tuple[Any, list]]) -> dict[str, bytes]:
    files: dict[str, Any] = {ext: io.BytesIO() for ext in ("shp", "shx", "dbf")}
    writer = shapefile.Writer(shp=files["shp"], shx=files["shx"], dbf=files["dbf"], shapeType=kind, encoding="utf-8")
    for name, kind_of, size in fields:
        writer.field(name, kind_of, size=size)
    for geometry, record in rows:
        if kind == shapefile.POINT:
            writer.point(geometry.x, geometry.y)
        elif kind == shapefile.POLYLINE:
            writer.line([list(geometry.coords)])
        else:
            writer.poly([list(geometry.exterior.coords)[::-1]])
        writer.record(*record)
    writer.close()
    return {ext: data.getvalue() for ext, data in files.items()}


def _archive(layers: dict[str, dict[str, bytes]]) -> zipfile.ZipFile:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("README", "Geofabrik")
        for stem, files in layers.items():
            for ext, data in files.items():
                archive.writestr(f"{stem}.{ext}", data)
    return zipfile.ZipFile(io.BytesIO(out.getvalue()))


PLACE_FIELDS = [("osm_id", "C", 12), ("code", "N", 4), ("fclass", "C", 28), ("population", "N", 10),
                ("name", "C", 100)]
WAY_FIELDS = [("osm_id", "C", 12), ("code", "N", 4), ("fclass", "C", 28), ("width", "N", 5), ("name", "C", 100)]
WATER_FIELDS = [("osm_id", "C", 12), ("code", "N", 4), ("fclass", "C", 28), ("name", "C", 100)]


def _geofabrik(places=(), ways=(), water=()) -> zipfile.ZipFile:
    return _archive({
        "gis_osm_places_free_1": _shapefile(shapefile.POINT, PLACE_FIELDS,
                                            [(shapely.Point(x, y), [i, 1001, k, p, n]) for i, k, p, n, x, y in places]),
        "gis_osm_waterways_free_1": _shapefile(shapefile.POLYLINE, WAY_FIELDS,
                                               [(line, [i, 8101, k, 0, n]) for i, k, n, line in ways]),
        "gis_osm_water_a_free_1": _shapefile(shapefile.POLYGON, WATER_FIELDS,
                                             [(poly, [i, 8200, k, n]) for i, k, n, poly in water]),
    })


ROMANIA = _geofabrik(
    places=[("1", "national_capital", 1883425, "București", 26.1, 44.43),
            ("2", "city", 324576, "Cluj-Napoca", 23.6, 46.77),
            ("3", "town", 20000, "Sânmiclăuş", 24.0, 46.1),        # the cedilla, written with the comma
            ("4", "hamlet", 50, "Cătun", 24.5, 46.0),              # hamlets are not kept
            ("5", "village", 900, "Ciugud", 23.7, 46.05),
            ("9", "village", 400, "Poienile de Sub Munte", 24.43, 47.82)],
    ways=[("10", "river", "Mureș", shapely.LineString([(22, 46), (23, 46)])),
          ("11", "river", "Râul Mureș", shapely.LineString([(23, 46), (24, 46)])),
          ("12", "river", "Dunărea - Дунав", shapely.LineString([(22, 44), (24, 44)])),
          ("13", "canal", "", shapely.LineString([(27, 45), (27.5, 45)])),               # an irrigation canal
          ("14", "canal", "Canalul Dunăre-Marea Neagră", shapely.LineString([(28, 44.2), (28.6, 44.2)])),
          ("15", "stream", "Pârâu", shapely.LineString([(25, 46), (25.1, 46)])),          # streams are not kept
          ("16", "river", "Olt", shapely.LineString([(24.3, 44.5), (24.4, 45.5)])),
          ("30", "river", "Prut", shapely.LineString([(28, 46), (28, 47)]))],
    water=[("20", "water", "Lacul Bicaz", shapely.box(26.0, 46.9, 26.1, 47.0)),
           ("21", "riverbank", "Dunărea", shapely.box(25.0, 43.9, 25.2, 44.0)),          # the Danube's water
           ("22", "water", "Olt", shapely.box(24.3, 45.0, 24.35, 45.1)),                 # a river's water
           ("23", "water", "Baltă", shapely.box(25.0, 45.0, 25.001, 45.001)),            # too small
           ("24", "wetland", "Mlaștina", shapely.box(25.0, 45.5, 25.2, 45.6))],          # not open water
)
MOLDOVA = _geofabrik(
    places=[("6", "national_capital", 756900, "Chișinău", 28.86, 47.02),
            ("7", "city", 128979, "Тирасполь", 29.6, 46.84)],                            # Cyrillic only
    ways=[("30", "river", "Prut", shapely.LineString([(28, 46), (28, 47)]))],            # Romania's too
)
SERBIA = _geofabrik(
    places=[("8", "national_capital", 1200000, "Beograd", 20.46, 44.82)],
    ways=[("40", "river", "Тиса", shapely.LineString([(20.1, 45.2), (20.2, 46.0)])),
          ("41", "river", "Морава", shapely.LineString([(10.0, 45.0), (11.0, 45.0)]))],  # far off: left out
    water=[("50", "water", "Сребрно језеро", shapely.box(21.3, 44.7, 21.5, 44.8))],
)


def test_transnistria_in_romanian():
    table = ol.read_osm([(MOLDOVA, True)], romanian={"7": "Tiraspol, oraș"})
    assert table["place_names"].tolist() == ["Chișinău", "Tiraspol"]          # without the note after the comma


class TestOverpass:
    def test_romanian_names_by_id(self, monkeypatch):
        body = json.dumps({"elements": [{"id": 7, "tags": {"name": "Тирасполь", "name:ro": "Tiraspol"}},
                                        {"id": 8, "tags": {"name": "Бендеры"}}]}).encode()
        asked = []

        def urlopen(request, timeout=0):
            asked.append(request.full_url)
            if request.full_url == ol.OVERPASS_URLS[0]:
                raise ol.urllib.error.URLError("busy")                        # the next server answers
            return io.BytesIO(body)
        monkeypatch.setattr(ol.urllib.request, "urlopen", urlopen)
        assert ol.romanian_place_names() == {"7": "Tiraspol"}
        assert asked == list(ol.OVERPASS_URLS)

    def test_no_server_answers(self, monkeypatch, caplog):
        def offline(request, timeout=0):
            raise ol.urllib.error.URLError("offline")
        monkeypatch.setattr(ol.urllib.request, "urlopen", offline)
        assert ol.romanian_place_names() == {} and "left unnamed" in caplog.text


@pytest.fixture(scope="module")
def table():
    return ol.read_osm([(ROMANIA, True), (MOLDOVA, True), (SERBIA, False)], ol.siruta_spellings(SIRUTA))


class TestRead:
    def test_places_most_important_first(self, table):
        assert table["place_names"].tolist() == ["București", "Chișinău", "Cluj-Napoca", "Sânmiclăuș", "Ciugud",
                                                 "Poienile de sub Munte"]                  # as SIRUTA spells it
        assert table["place_kinds"].tolist() == ["national_capital", "national_capital", "city", "town", "village",
                                                 "village"]

    def test_rivers_merged_and_named(self, table):
        rivers = dict(zip(table["river_names"].tolist(), table["river_km"].tolist()))
        # Mureș joins "Râul Mureș"; the Prut is read once; Serbia's Tisa is drawn unnamed, its Morava left out
        assert set(rivers) == {"Mureș", "Dunărea", "Olt", "Prut", "Canalul Dunăre-Marea Neagră", ""}
        assert rivers["Mureș"] == pytest.approx(2 * 77.3, rel=0.02)
        assert rivers["Prut"] == pytest.approx(111.2, rel=0.01)
        assert len(table["river_names"]) == 6

    def test_lakes(self, table):
        lakes = list(zip(table["lake_names"].tolist(), np.round(table["lake_km2"], 1).tolist()))
        names = [n for n, _ in lakes]
        assert names[0] == "" and "Lacul Bicaz" in names                       # the Danube's water is unnamed
        assert "Olt" not in names and "Baltă" not in names and "Mlaștina" not in names
        assert "Сребрно језеро" not in names and len(lakes) == 4               # Serbia's lake, unnamed

    def test_countries(self):
        countries = _archive({"CNTR_RG_01M_2024_4326": _shapefile(
            shapefile.POLYGON, [("CNTR_ID", "C", 2)],
            [(shapely.box(20, 43, 30, 48), ["RO"]), (shapely.box(30, 45, 35, 48), ["MD"])])})
        borders = _archive({"CNTR_BN_01M_2024_4326": _shapefile(
            shapefile.POLYLINE, [("COAS_FLAG", "C", 1)],
            [(shapely.LineString([(30, 45), (30, 48)]), ["F"]), (shapely.LineString([(30, 43), (30, 45)]), ["T"])])})
        table = ol.read_countries(countries, borders)
        land = ol._unpacked(table, "land")
        assert len(land) == 2 and land[0].bounds == pytest.approx((20, 43, 30, 48))
        assert [b.bounds for b in ol._unpacked(table, "border")] == [pytest.approx((30, 45, 30, 48))]   # no coast

    def test_archive_without_layer(self):
        with pytest.raises(ol.OSMDataError, match="holds no gis_osm_places_free_1"):
            ol.read_osm([(_archive({}), True)])


def _zip_bytes(archive: zipfile.ZipFile) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as copy:
        for name in archive.namelist():
            copy.writestr(name, archive.read(name))
    return out.getvalue()


class _Response:
    def __init__(self, url, status, body=b"", headers=None):
        self.url, self.status, self.headers, self._body = url, status, headers or {}, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRemoteFile:
    def _server(self, data: bytes, ranges: bool = True):
        """urlopen serving *data* at a dated address, in parts if *ranges*; the list holds the requests."""
        asked = []

        def urlopen(request, timeout=0):
            asked.append(request.headers.get("Range"))
            dated = "https://download.geofabrik.de/europe/romania-261022-free.shp.zip"
            if request.get_method() == "HEAD":
                return _Response(dated, 200, headers={"Content-Length": str(len(data))})
            assert request.full_url == dated
            span = request.headers.get("Range")
            if not ranges or span is None:
                return _Response(dated, 200, data)
            start, end = (int(v) for v in span.removeprefix("bytes=").split("-"))
            return _Response(dated, 206, data[start:end + 1])
        return urlopen, asked

    def test_reads_a_member_in_parts(self):
        data = _zip_bytes(ROMANIA)
        urlopen, asked = self._server(data)
        with mock.patch.object(ol.urllib.request, "urlopen", urlopen):
            remote = ol.RemoteFile("https://download.geofabrik.de/europe/romania-latest-free.shp.zip")
            archive = zipfile.ZipFile(io.BufferedReader(remote, 4096))
            assert archive.read("README") == b"Geofabrik"
        assert remote.url.endswith("romania-261022-free.shp.zip") and remote.size == len(data)
        assert all(r is None or r.startswith("bytes=") for r in asked) and len(asked) < 10

    def test_a_server_without_ranges(self):
        urlopen, _ = self._server(_zip_bytes(ROMANIA), ranges=False)
        with mock.patch.object(ol.urllib.request, "urlopen", urlopen):
            remote = ol.RemoteFile("https://download.geofabrik.de/europe/romania-latest-free.shp.zip")
            with pytest.raises(ol.OSMDataError, match="cannot be read in parts"):
                remote.read(10)


class TestCache:
    @pytest.fixture
    def online(self, monkeypatch):
        """Geofabrik's archives as remote files, GISCO's as downloads; the list holds the addresses asked."""
        asked = []
        archives = {"romania": ROMANIA, "moldova": MOLDOVA, "serbia": SERBIA}

        class Remote(io.BytesIO):
            def __init__(self, url):
                asked.append(url)
                region = url.rsplit("/", 1)[-1].split("-")[0]
                super().__init__(_zip_bytes(archives.get(region, _geofabrik())))
                self.url = url.replace("latest", "261022")

        def download(url, dest, timeout=120, label=""):
            asked.append(url)
            if url == ol.SIRUTA_URL:
                with open(dest, "wb") as fh:
                    fh.write(SIRUTA.encode("utf-8"))
                return
            stem, rows, kind, fields = (
                ("CNTR_RG_01M_2024_4326", [(shapely.box(20, 43, 30, 48), ["RO"])], shapefile.POLYGON,
                 [("CNTR_ID", "C", 2)]) if url == ol.COUNTRIES_URL else
                ("CNTR_BN_01M_2024_4326", [(shapely.LineString([(30, 45), (30, 48)]), ["F"])], shapefile.POLYLINE,
                 [("COAS_FLAG", "C", 1)]))
            with open(dest, "wb") as fh:
                fh.write(_zip_bytes(_archive({stem: _shapefile(kind, fields, rows)})))

        monkeypatch.setattr(ol, "RemoteFile", Remote)
        monkeypatch.setattr(ol, "_download", download)
        monkeypatch.setattr(ol, "romanian_place_names", lambda: {"7": "Tiraspol"})
        return asked

    def test_built_once(self, online, tmp_path):
        base = ol.load_base(str(tmp_path))
        assert [p.name for p in base.places][:3] == ["București", "Chișinău", "Cluj-Napoca"]
        assert "Tiraspol" in [p.name for p in base.places]
        assert len(base.land) == 1 and len(base.borders) == 1 and base.date == "22 Oct 2026"
        assert "Geofabrik extracts of 22 Oct 2026" in ol.attribution(base)
        again = ol.load_base(str(tmp_path))
        assert [r.name for r in again.rivers] == [r.name for r in base.rivers]
        assert len(online) == len(ol.REGIONS) + len(ol.NEIGHBOURS) + 3                     # cached
        assert sorted(os.listdir(tmp_path)) == [f"osm_local_v{ol.DATA_VERSION}.npz"]       # downloads removed

    def test_unreachable(self, tmp_path, monkeypatch):
        def offline(url, *args, **kwargs):
            raise OSError("no network")

        monkeypatch.setattr(ol, "_download", offline)
        with pytest.raises(ol.OSMDataError, match="Could not read the OpenStreetMap data: no network"):
            ol.load_base(str(tmp_path))
        assert not os.listdir(tmp_path)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _map(km=900, lat=46.0, lon=25.0, inches=10):
    """A disc *km* across, as a radius map is drawn."""
    ax = Figure(figsize=(inches, inches), dpi=72).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    half = km * 500
    ax.set_xlim(-half, half)
    ax.set_ylim(-half, half)
    return ax


def _base(**changes):
    base = ol.Base(
        land=[shapely.box(15, 40, 35, 52)], borders=[shapely.LineString([(28, 45), (28, 48)])],
        places=[ol.Place("București", "national_capital", 1883425, 26.1, 44.43),
                ol.Place("Cluj-Napoca", "city", 324576, 23.6, 46.77),
                ol.Place("Sibiu", "city", 147245, 24.15, 45.8),
                ol.Place("Sânmiclăuș", "town", 20000, 24.0, 46.1),
                ol.Place("Ciugud", "village", 900, 23.75, 46.3)],
        rivers=[ol.Water("Mureș", 700, shapely.LineString([(21.5, 46.6), (25.5, 46.5)])),   # level: named
                ol.Water("Siret", 500, shapely.LineString([(27.0, 48.0), (27.1, 44.0)])),   # north to south
                ol.Water("", 300, shapely.LineString([(22, 44.5), (24, 44.6)])),
                ol.Water("Pârâu", 10, shapely.LineString([(25, 45), (25.05, 45)]))],
        lakes=[ol.Water("Lacul Mare", 400, shapely.box(23.0, 44.8, 23.6, 45.2)),
               ol.Water("Lacul Mic", 1, shapely.box(26.0, 45.0, 26.01, 45.01))],
        date="22 Oct 2026")
    return base._replace(**changes)


class TestDrawing:
    def test_scale(self):
        assert ol.min_river_km(900) == 30 and ol.min_lake_km2(900) == 9
        assert ol.place_kinds(200) == set(ol.PLACE_KINDS)
        assert ol.place_kinds(900) == {"national_capital", "city", "town"}
        assert ol.min_population(900) == pytest.approx(24_300) and ol.min_population(200) == pytest.approx(1_200)
        assert ol.place_kinds(12_000) == {"national_capital", "city"}
        assert ol.river_width(25) < ol.river_width(300) < ol.river_width(3000) == 1.6

    def test_base_for_the_scale(self):
        ax = _map(900)
        ol.draw_base(ax, _base())
        rivers = [c for c in ax.collections if isinstance(c, LineCollection)]
        assert len(rivers) == 1 and len(rivers[0].get_segments()) == 3          # not the 10 km stream
        assert ol.map_km(ax) == pytest.approx(900)

    def test_places_rivers_and_lakes_named(self):
        ax = _map(900)
        named = ol.name_base(ax, _base())
        texts = {t.get_text(): t for t in ax.texts}
        assert {"București", "Cluj-Napoca", "Sibiu", "Mureș", "Lacul Mare"} <= set(texts)
        # Too small for this scale: a town of 20,000 (24,300 needed), a village, a pond
        assert not {"Sânmiclăuș", "Ciugud", "Lacul Mic"} & set(texts)
        assert texts["București"].get_fontweight() == "bold"
        tilt = texts["Mureș"].get_rotation()                                      # matplotlib's, 0 to 360
        assert texts["Mureș"].get_fontstyle() == "italic" and min(tilt, 360 - tilt) < 5
        assert named.lakes == 1 and named.places == 3

    def test_a_river_named_along_its_course(self):
        slanted = ol.Water("Olt", 600, shapely.LineString([(23.0, 44.5), (25.8, 46.5)]))
        ax = _map(900)
        ol.name_base(ax, _base(places=[], lakes=[], rivers=[slanted, *_base().rivers]))
        angles = {t.get_text(): t.get_rotation() for t in ax.texts}
        assert 25 < angles["Olt"] < 60                       # rising to the north-east, as drawn
        assert "Siret" not in angles                         # straight north to south: too steep to read

    def test_villages_on_a_small_map(self):
        ax = _map(150, lat=46.2, lon=23.8)
        ol.name_base(ax, _base())
        assert "Ciugud" in {t.get_text() for t in ax.texts}

    def test_a_name_already_written_is_not_repeated(self):
        ax = _map(900)
        ax.annotate("Sibiu", (0, 0))
        ol.name_base(ax, _base())
        assert [t.get_text() for t in ax.texts].count("Sibiu") == 1

    def test_a_place_another_layer_marked_is_not_named_again(self):
        ax = _map(900)
        # GHSL's dot for "Bucharest", a few km from OSM's node for București
        x, y = ax.projection.transform_point(26.08, 44.44, ol.ccrs.PlateCarree())
        ax.plot(x, y, "o", transform=ax.projection)
        ax.annotate("Bucharest", (x, y))
        ol.name_base(ax, _base())
        texts = [t.get_text() for t in ax.texts]
        assert "București" not in texts and "Sibiu" in texts

    def test_names_share_the_room_with_other_layers(self):
        ax = _map(900)
        # A layer's name above Bucharest: its own goes below its dot
        x, y = ax.projection.transform_point(26.1, 44.43, ol.ccrs.PlateCarree())
        assert label_space(ax).claim(x, y, "Ilfov", 9)
        ol.name_base(ax, _base())
        bucharest = next(t for t in ax.texts if t.get_text() == "București")
        assert bucharest.get_verticalalignment() == "top"

    def test_a_place_hemmed_in_is_not_named(self):
        ax = _map(900)
        x, y = ax.projection.transform_point(26.1, 44.43, ol.ccrs.PlateCarree())
        px, py = ax.transData.transform((x, y))
        label_space(ax).placed.append((px - 200, py - 200, px + 200, py + 200))     # names all around it
        ol.name_base(ax, _base())
        assert "București" not in {t.get_text() for t in ax.texts}
