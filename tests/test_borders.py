"""Tests for borders.py — dates, reading Cliopatria and CShapes, caching, colours, names and the key (no network)."""

import io
import json
import lzma
import tarfile
import zipfile

import numpy as np
import pytest
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure
from shapely.geometry import Polygon, box

import borders
from rotation import globe_projection


def _square(west, south, east, north):
    """An anticlockwise ring round a box, as GeoJSON wants."""
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def _feature(name, start, end, ring, kind="POLITY", member="", components=""):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"Name": name, "FromYear": start, "ToYear": end, "Area": 1.0, "Type": kind,
                           "MemberOf": member, "Components": components}}


CLIOPATRIA = {"type": "FeatureCollection", "features": [
    _feature("Rome", -27, 395, _square(0, 30, 20, 50)),
    _feature("Gaul", -100, -51, _square(-5, 43, 5, 51)),
    _feature("(Roman Empire)", -27, 395, _square(0, 30, 40, 50), components="Rome;Egypt"),
    _feature("Egypt", -30, 395, _square(25, 22, 35, 31), member="(Roman Empire)"),
    _feature("(Allegiance of Armenia to Rome)", -20, 395, _square(38, 38, 46, 42), kind="RELATION",
             components="Armenia;Rome"),
    _feature("Armenia", -20, 395, _square(38, 38, 46, 42), member="(Allegiance of Armenia to Rome)"),
]}


def _topojson():
    """Two territories sharing an arc: France (independent) and Algeria (its colony), one ring each."""
    arcs = [
        [[0, 40], [10, 40]],                       # shared edge, west to east
        [[10, 40], [10, 50], [0, 50], [0, 40]],    # France's top, back to the start
        [[0, 40], [0, 30], [10, 30], [10, 40]],    # Algeria's bottom, after the shared edge reversed
    ]
    geometries = [
        {"type": "Polygon", "arcs": [[0, 1]],
         "properties": {"gwcode": 220, "country_name": "France", "start": "1886-01-01", "end": "2019-12-31",
                        "status": "independent", "owner": "220"}},
        {"type": "Polygon", "arcs": [[~0, 2]],
         "properties": {"gwcode": 615, "country_name": "Algeria", "start": "1886-01-01", "end": "1962-07-04",
                        "status": "colony", "owner": "220"}},
        {"type": "Polygon", "arcs": [[~0, 2]],
         "properties": {"gwcode": 615, "country_name": "Algeria", "start": "1962-07-05", "end": "2019-12-31",
                        "status": "independent", "owner": "615"}},
    ]
    return {"type": "Topology", "arcs": arcs, "objects": {"cshapes_2_gw": {"type": "GeometryCollection",
                                                                            "geometries": geometries}}}


def _cliopatria_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cliopatria_polities_only_v021.geojson", json.dumps(CLIOPATRIA))
    return buf.getvalue()


def _cshapes_tar():
    data = lzma.compress(json.dumps(_topojson()).encode())
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(borders.CSHAPES_MEMBER)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A cache directory the two datasets download into from memory."""
    downloads = []

    def fake_download(url, dest, label):
        downloads.append(url)
        with open(dest, "wb") as fh:
            fh.write(_cliopatria_zip() if "cliopatria" in url else _cshapes_tar())

    monkeypatch.setattr(borders, "_download", fake_download)
    monkeypatch.setattr(borders, "_default_cache_dir", lambda: str(tmp_path))
    return tmp_path, downloads


def _globe(lat, lon):
    ax = Figure(figsize=(6, 6)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


class TestWhen:
    @pytest.mark.parametrize("spec, year, date, label", [
        ("1500", 1500, "1500-07-01", "1500"),
        (1914, 1914, "1914-07-01", "1914"),
        ("-500", -500, "", "500 BCE"),
        ("500 BC", -500, "", "500 BCE"),
        ("500bce", -500, "", "500 BCE"),
        ("AD 800", 800, "0800-07-01", "800"),
        ("800 ce", 800, "0800-07-01", "800"),
        ("1914-08-01", 1914, "1914-08-01", "1 August 1914"),
    ])
    def test_parse(self, spec, year, date, label):
        assert borders.parse_when(spec) == (year, date, label)

    @pytest.mark.parametrize("spec", ["0", "0 BC", "soon", "1914-13-01", "AD -5", "", "1500 AD BC"])
    def test_refused(self, spec):
        with pytest.raises(ValueError):
            borders.parse_when(spec)

    @pytest.mark.parametrize("spec, source, chosen", [
        ("1914", "auto", "cshapes"), ("1885", "auto", "cliopatria"), ("2024", "auto", "cliopatria"),
        ("1914", "cliopatria", "cliopatria"), ("500 BC", "auto", "cliopatria"),
    ])
    def test_source(self, spec, source, chosen):
        assert borders.choose_source(borders.parse_when(spec), source) == chosen

    @pytest.mark.parametrize("spec, source, match", [
        ("1500", "cshapes", "CShapes 2.0 covers 1886 to 2019, not 1500"),
        ("4000 BC", "auto", "Cliopatria covers 3400 BCE to 2024"),
        ("1914", "wikipedia", "Unknown borders source"),
    ])
    def test_source_refused(self, spec, source, match):
        with pytest.raises(ValueError, match=match):
            borders.choose_source(borders.parse_when(spec), source)


class TestReading:
    def test_cliopatria_without_groupings_and_with_empires(self):
        table = borders.read_cliopatria(json.dumps(CLIOPATRIA).encode())
        assert list(table["names"]) == ["Rome", "Gaul", "Egypt", "Armenia"]
        # Egypt is part of the Roman Empire; an allegiance is not an empire
        assert list(table["groups"]) == ["", "", "(Roman Empire)", ""]
        assert list(table["starts"]) == [-27, -100, -30, -20]

    def test_cshapes_topojson_arcs_and_status(self):
        table = borders.read_cshapes(json.dumps(_topojson()).encode())
        assert list(table["names"]) == ["France", "Algeria", "Algeria"]
        assert list(table["dependent"]) == [False, True, False]
        assert list(table["owners"]) == ["220", "220", "615"]
        france, algeria, _ = borders._unpack(table)
        assert france.equals(box(0, 40, 10, 50)) and algeria.equals(box(0, 30, 10, 40))

    def test_outlines_are_cleaned_for_cartopy(self):
        clockwise = Polygon([(0, 0), (0, 1), (1e-12, 1), (1, 1), (1, 0)])
        (outline,) = borders._unpack(borders._pack([clockwise]))
        assert outline.exterior.is_ccw
        assert len(outline.exterior.coords) == 5                       # the hair's-breadth point is gone


class TestCache:
    def test_each_source_built_once(self, cache):
        tmp_path, downloads = cache
        rome = borders.load_polities(borders.parse_when("100"), "cliopatria")
        assert [p.name for p in rome] == ["Rome", "Egypt", "Armenia"]           # largest first
        egypt = rome[1]
        assert egypt.group == "(Roman Empire)" and egypt.group_name == "Roman Empire"
        assert rome[2].group == "Armenia"
        assert [p.name for p in borders.load_polities(borders.parse_when("60 BC"), "cliopatria")] == ["Gaul"]
        borders.load_polities(borders.parse_when("200"), "cliopatria")
        assert downloads == [borders.CLIOPATRIA_URL]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["cliopatria_v0.2.1_v1.npz"]

    def test_colonies_take_their_rulers_name(self, cache):
        before = borders.load_polities(borders.parse_when("1914-08-01"), "cshapes")
        algeria = next(p for p in before if p.name == "Algeria")
        assert algeria.dependent and algeria.group == "220" and algeria.group_name == "France"
        after = borders.load_polities(borders.parse_when("1962-07-05"), "cshapes")
        algeria = next(p for p in after if p.name == "Algeria")
        assert not algeria.dependent and algeria.group == "615"

    def test_cran_archive_is_tried_next(self, cache, monkeypatch):
        tmp_path, downloads = cache
        real = borders._download

        def first_fails(url, dest, label):
            if url == borders.CSHAPES_URLS[0]:
                raise OSError("404")
            real(url, dest, label)

        monkeypatch.setattr(borders, "_download", first_fails)
        assert borders.load_polities(borders.parse_when("1914"), "cshapes")
        assert downloads == [borders.CSHAPES_URLS[1]]

    def test_failures_are_borders_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr(borders, "_download", lambda url, dest, label: open(dest, "wb").write(b"junk"))
        with pytest.raises(borders.BordersError, match="Could not read the cliopatria borders"):
            borders.ensure_borders_data("cliopatria", str(tmp_path))
        assert list(tmp_path.iterdir()) == []                          # the download is cleared away


def _polity(name, group, outline, dependent=False):
    return borders.Polity(name, group, group, dependent, outline)


class TestColours:
    def test_neighbours_differ_and_the_palette_spreads(self):
        row = [_polity(f"S{i}", f"g{i}", box(i * 10, 0, i * 10 + 10, 10)) for i in range(4)]
        island = _polity("Island", "island", box(0, 50, 5, 55))
        colours = borders.colour_groups(row + [island])
        assert all(colours[f"g{i}"] != colours[f"g{i + 1}"] for i in range(3))
        assert len(set(colours.values())) == 5                          # nobody shares while colours are free

    def test_a_dependency_is_lighter(self):
        colours = {"fr": (200, 100, 0)}
        france = _polity("France", "fr", box(0, 40, 10, 50))
        algeria = _polity("Algeria", "fr", box(0, 30, 10, 40), dependent=True)
        assert borders.fill_colour(france, colours) == pytest.approx((200 / 255, 100 / 255, 0))
        assert all(a > b for a, b in zip(borders.fill_colour(algeria, colours)[1:], (100 / 255, 0)))


class TestDrawing:
    POLITIES = [_polity("Big Empire", "big", box(-20, -20, 20, 20)),
                _polity("Speck", "speck", box(30, 0, 33, 3)),
                _polity("Far Away", "far", box(150, -10, 170, 10))]

    def test_fills_outlines_and_names_what_shows(self):
        ax = _globe(0, 0)
        colours = borders.colour_groups(self.POLITIES)
        counts = borders.draw_borders(ax, self.POLITIES, colours, samples=100)
        assert set(counts) == {0, 1} and counts[0] > counts[1]          # the far one is not on the globe
        assert [t.get_text() for t in ax.texts] == ["Big Empire"]       # too small to name: the speck
        assert len(ax.collections) == len({borders.fill_colour(p, colours) for p in self.POLITIES}) + 1

    def test_without_names(self):
        ax = _globe(0, 0)
        borders.draw_borders(ax, self.POLITIES, borders.colour_groups(self.POLITIES), labels=False, samples=60)
        assert not ax.texts

    def test_key(self):
        ax = _globe(0, 0)
        colours = borders.colour_groups(self.POLITIES)
        when = borders.parse_when("1914")
        key = borders.add_borders_legend(ax, self.POLITIES, [{0: 50, 1: 2}, {2: 9}], colours, when, "cliopatria",
                                         limit=2)
        assert key is not None
        assert [t.get_text() for t in key.get_texts()] == ["big", "far"]
        assert key.get_title().get_text() == "Borders in 1914 (Cliopatria): the 2 largest of 3 on the map"
        colony = [_polity("Colony", "big", box(0, 0, 1, 1), dependent=True)]
        key = borders.add_borders_legend(ax, colony, [{0: 5}], colours, when, "cshapes")
        assert key is not None and "lighter shades: their colonies" in key.get_title().get_text()
        assert borders.add_borders_legend(ax, self.POLITIES, [{}], colours, when, "cshapes") is None

    def test_credits(self):
        assert "CC BY 4.0" in borders.attribution("cliopatria") and "Cliopatria" in borders.attribution("cliopatria")
        assert "CShapes 2.0" in borders.attribution("cshapes")


def test_crimea_stays_in_ukraine():
    crimea, ukraine, russia = box(33, 44, 36, 46), box(30, 46, 40, 52), box(36, 44, 60, 60)
    table = {"codes": np.array(["369", "369", "365", "365", "220"]),
             "starts": np.array(["1991-12-26", "2014-03-18", "1991-12-21", "2014-03-18", "1886-01-01"]),
             "ends": np.array(["2014-03-17", "2019-12-31", "2014-03-17", "2019-12-31", "2019-12-31"])}
    outlines = [ukraine.union(crimea), ukraine, russia, russia.union(crimea), box(0, 40, 10, 50)]
    fixed = borders._de_jure(table, outlines)
    assert fixed[1].equals(ukraine.union(crimea)) and fixed[3].equals(russia)
    assert fixed[0] is outlines[0] and fixed[4] is outlines[4]


def test_label_sizes_grow_with_the_share():
    assert borders._label_size(0.0) == 8 and borders._label_size(1.0) == 16
    assert borders._label_size(0.01) < borders._label_size(0.05)


def test_shapely_polygons_from_the_cache_are_valid():
    table = borders.read_cshapes(json.dumps(_topojson()).encode())
    assert all(shapely.is_valid(g) for g in borders._unpack(table))
    assert np.all(table["starts"] <= table["ends"])


ROMANIAN = {"RO": "România", "TR": "Turcia", "RU": "Rusia", "DE": "Germania", "CZ": "Cehia", "MD": "Republica Moldova",
            "IR": "Iran", "CD": "Congo - Kinshasa"}
ENGLISH = {"RO": "Romania", "TR": "Türkiye", "RU": "Russia", "DE": "Germany", "CZ": "Czechia", "MD": "Moldova",
           "IR": "Iran", "CD": "Congo - Kinshasa"}


class TestRomanian:
    @pytest.mark.parametrize("name, date, romanian", [
        ("Rumania", "2019-06-01", "România"),                             # CShapes' spelling, by its ISO code
        ("Moldova", "2019-06-01", "Republica Moldova"),                   # CLDR's English name
        ("Iran (Persia)", "2019-06-01", "Iran"),                          # without what the brackets say
        ("Iran (Persia)", "1914-07-01", "Persia"),                        # its name then
        ("Turkey (Ottoman Empire)", "1914-07-01", "Imperiul Otoman"),
        ("Turkey (Ottoman Empire)", "1938-01-01", "Turcia"),
        ("Russia (Soviet Union)", "1914-07-01", "Imperiul Rus"),
        ("Russia (Soviet Union)", "1920-01-01", "Rusia Sovietică"),
        ("Russia (Soviet Union)", "1938-01-01", "Uniunea Sovietică"),
        ("Russia (Soviet Union)", "2019-06-01", "Rusia"),
        ("Germany (Prussia)", "1914-07-01", "Imperiul German"),
        ("Germany (Prussia)", "1938-01-01", "Germania"),
        ("German Federal Republic", "1960-01-01", "Republica Federală Germania"),
        ("German Federal Republic", "2019-06-01", "Germania"),
        ("Congo, Democratic Republic of (Zaire)", "1950-01-01", "Congo Belgian"),
        ("Congo, Democratic Republic of (Zaire)", "1965-01-01", "Congo - Kinshasa"),    # CLDR's, between
        ("Congo, Democratic Republic of (Zaire)", "1980-01-01", "Zair"),
        ("Austria-Hungary", "1914-07-01", "Austro-Ungaria"),
        ("Tibet", "1938-01-01", "Tibet"),
        ("Spanish West Africa", "1938-01-01", ""),                         # no Romanian name known
    ])
    def test_names(self, name, date, romanian):
        assert borders.romanian_name(name, date, ROMANIAN, ENGLISH) == romanian

    def test_polities_in_romanian(self, monkeypatch):
        monkeypatch.setattr(borders, "cldr_territories", lambda language, cache_dir=None:
                            ROMANIAN if language == "ro" else ENGLISH)
        when = borders.parse_when("1914-07-01")
        polities = [borders.Polity("Rumania", "1", "Rumania", False, box(0, 0, 1, 1)),
                    borders.Polity("Spanish Morocco", "2", "Spain", True, box(0, 0, 1, 1))]
        ro = borders.in_language(polities, when, "ro", "cshapes")
        assert [(p.name, p.group_name) for p in ro] == [("România", "România"), ("", "Spain")]
        assert borders.in_language(polities, when, "en", "cshapes") == polities
        assert borders.in_language(polities, when, "ro", "cliopatria") == polities     # as Cliopatria names them

    def test_an_unnamed_state_is_not_labelled(self):
        ax = _globe(0, 0)
        polities = [_polity("", "big", box(-20, -20, 20, 20))]
        borders.draw_borders(ax, polities, borders.colour_groups(polities), samples=60)
        assert not ax.texts

    def test_cldr_downloaded_once(self, tmp_path, monkeypatch):
        asked = []

        def fetch(urls, dest, label):
            asked.append(urls[0])
            with open(dest, "w", encoding="utf-8") as fh:
                json.dump({"main": {"ro": {"localeDisplayNames": {"territories": {
                    "RO": "România", "TR-alt-variant": "Türkiye", "001": "Lume"}}}}}, fh)
        monkeypatch.setattr(borders, "_fetch", fetch)
        assert borders.cldr_territories("ro", str(tmp_path)) == {"RO": "România"}     # countries only
        assert borders.cldr_territories("ro", str(tmp_path)) == {"RO": "România"}
        assert asked == [borders.CLDR_URL.format(language="ro")]
