"""Tests for ro_admin.py (no network; a small shapefile and SIRUTA table made here)."""

import io
import os
import zipfile
from typing import Any

import pytest
import shapefile
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import ro_admin as ra
from cities import label_space
from rotation import globe_projection


@pytest.mark.parametrize("text, level", [
    (None, "counties"), ("", "counties"), ("Counties", "counties"), ("județe", "counties"), ("judete", "counties"),
    ("uats", "uats"), ("UAT", "uats"), ("comune", "uats"),
])
def test_levels(text, level):
    assert ra.resolve_admin_level(text) == level


def test_bad_level():
    with pytest.raises(ValueError, match="Unknown level of Romania's administrative units 'villages'"):
        ra.resolve_admin_level("villages")


@pytest.mark.parametrize("raw, name", [
    ("ORAŞ CÂMPULUNG LA TISA", "Câmpulung la Tisa"),
    ("CÂMPULUNG LA TISA", "Câmpulung la Tisa"),
    ("MUNICIPIUL PIATRA-NEAMŢ", "Piatra-Neamț"),
    ("JUDEŢUL BISTRIŢA-NĂSĂUD", "Bistrița-Năsăud"),
    ("MUNICIPIUL BUCUREŞTI", "București"),
    ("VALEA LUI MIHAI", "Valea lui Mihai"),
    ("ŞTEFAN CEL MARE", "Ștefan cel Mare"),
    ("POIENILE DE SUB MUNTE", "Poienile de sub Munte"),
    ("C.A.ROSETTI", "C.A. Rosetti"),
    ("C.A. ROSETTI", "C.A. Rosetti"),
    ("I. L. CARAGIALE", "I. L. Caragiale"),
    ("ALEXANDRU I.CUZA", "Alexandru I. Cuza"),
    ("CICEU - MIHĂIEŞTI", "Ciceu-Mihăiești"),
    ("Oraş Dragomiresti", "Dragomiresti"),          # as GISCO names it
    ("Municipiul Sfântul  Gheorghe", "Sfântul Gheorghe"),
])
def test_written_name(raw, name):
    assert ra.written_name(raw) == name


@pytest.mark.parametrize("name, kind", [
    ("Municipiul Turda", 4), ("Oraş Dragomiresti", 2), ("Oras Bragadiru", 2), ("Ciugud", 3), ("Oraşu Nou", 3),
])
def test_gisco_kind(name, kind):
    assert ra.gisco_kind(name) == kind


SIRUTA = """﻿SIRUTA;DENLOC;CODP;JUD;SIRSUP;TIP;NIV;MED;REGIUNE;FSJ;NUTS;LAU
10;JUDEŢUL ALBA;0;1;1;40;1;0;7;1;RO121;
1017;MUNICIPIUL ALBA IULIA;0;1;10;1;2;1;7;1;RO121;RO_1017
1026;ALBA IULIA;510005;1;1017;9;3;1;7;1;RO121;
2000;CIUGUD;0;1;10;3;2;3;7;1;RO121;RO_2000
234;JUDEŢUL ILFOV;0;23;1;40;1;0;8;1;RO322;
3000;ORAŞ BUFTEA;0;23;234;2;2;1;8;1;RO322;RO_3000
403;MUNICIPIUL BUCUREŞTI;0;40;1;40;1;0;8;1;RO321;
179132;MUNICIPIUL BUCUREŞTI;0;40;403;9;2;1;8;1;RO321;RO_179132
"""

BUCHAREST = shapely.box(26.0, 44.35, 26.2, 44.55)
# Alba Iulia and Ciugud make up Alba; Buftea is Ilfov, around Bucharest; 4000 is not in SIRUTA, beside Ciugud
LAUS = [
    ("RO_1017", "RO", "Municipiul Alba Iulia", 63000, shapely.box(23.5, 46.0, 23.7, 46.2)),
    ("HU_1", "HU", "Budapest", 1700000, shapely.box(19.0, 47.4, 19.2, 47.6)),
    ("RO_2000", "RO", "Ciugud", 4000, shapely.box(23.7, 46.0, 24.0, 46.2)),
    ("RO_3000", "RO", "Oraş Buftea", 23000, shapely.box(25.8, 44.2, 26.4, 44.7).difference(BUCHAREST)),
    ("RO_179132", "RO", "Municipiul Bucureşti", 2100000, BUCHAREST),
    ("RO_4000", "RO", "Oraş Sânmiclăuş", 900, shapely.box(24.0, 46.0, 24.2, 46.2)),
]


def _shapefile_zip(laus=LAUS):
    files: dict[str, Any] = {ext: io.BytesIO() for ext in ("shp", "shx", "dbf")}
    writer = shapefile.Writer(shp=files["shp"], shx=files["shx"], dbf=files["dbf"], shapeType=shapefile.POLYGON,
                              encoding="utf-8")
    writer.field("GISCO_ID", "C", size=50)
    writer.field("CNTR_CODE", "C", size=2)
    writer.field("LAU_NAME", "C", size=250)
    writer.field("POP_2024", "N", size=24, decimal=15)
    for code, country, name, people, outline in laus:
        rings = [list(outline.exterior.coords)[::-1]] + [list(h.coords)[::-1] for h in outline.interiors]
        writer.poly(rings)
        writer.record(code, country, name, people)
    writer.close()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for ext, data in files.items():
            archive.writestr(f"LAU_RG_01M_2024_4326.{ext}", data.getvalue())
    return out.getvalue()


class TestData:
    def test_siruta(self):
        counties, uats = ra.read_siruta(SIRUTA)
        assert counties == {1: "Alba", 23: "Ilfov", 40: "București"}
        assert uats[1017] == ra.Entry("Alba Iulia", 1, 1) and uats[3000] == ra.Entry("Buftea", 23, 2)
        assert 1026 not in uats                                                 # a village, not a UAT

    def test_siruta_not_as_expected(self):
        with pytest.raises(ra.AdminError, match="not as expected"):
            ra.read_siruta("SIRUTA;DENLOC\n1017;ALBA IULIA\n")
        with pytest.raises(ra.AdminError, match="names no counties"):
            ra.read_siruta(SIRUTA.splitlines()[0] + "\n")

    def test_read(self, caplog):
        table = ra.read_admin(_shapefile_zip(), SIRUTA)
        assert table["codes"].tolist() == [1017, 2000, 3000, 179132, 4000]             # Romania's only
        assert table["names"].tolist() == ["Alba Iulia", "Ciugud", "Buftea", "București", "Sânmiclăuș"]
        assert table["kinds"].tolist() == [1, 3, 2, 9, 2]
        assert table["counties"].tolist() == [1, 1, 23, 40, 1]          # the newcomer in its neighbour's county
        assert "1 UATs are not in SIRUTA (4000)" in caplog.text
        assert table["county_names"].tolist() == ["Alba", "Ilfov", "București"]
        counties = dict(zip(table["county_codes"].tolist(),
                            ra._unpack({"wkb": table["county_wkb"], "wkb_ends": table["county_wkb_ends"]})))
        assert counties[1].bounds == pytest.approx((23.5, 46.0, 24.2, 46.2))         # its three UATs
        assert counties[23].area == pytest.approx(0.6 * 0.5 - 0.2 * 0.2)              # around Bucharest
        colours = dict(zip(table["county_codes"].tolist(), table["county_colours"].tolist()))
        assert colours[23] != colours[40]                                              # neighbours
        lon, lat = table["label_lon"][0], table["label_lat"][0]
        assert (lon, lat) == pytest.approx((23.6, 46.1), abs=0.01)                    # the middle of Alba Iulia

    def test_archive_without_shapefile(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        with pytest.raises(ra.AdminError, match="holds no shapefile"):
            ra.read_admin(out.getvalue(), SIRUTA)

    def test_no_romanian_units(self):
        with pytest.raises(ra.AdminError, match="no Romanian UATs"):
            ra.read_admin(_shapefile_zip([LAUS[1]]), SIRUTA)

    def test_slivers_left_out(self):
        gap = shapely.box(0.5, 0.5, 0.5001, 0.5001)
        outline = shapely.union_all([shapely.box(0, 0, 1, 1).difference(gap), shapely.box(2, 2, 2.001, 2.001)])
        assert ra._without_slivers(outline).equals(shapely.box(0, 0, 1, 1))

    def test_label_deep_inside(self):
        # A long bar and a block at its end: the label goes in the block, not the middle of all
        bent = shapely.union_all([shapely.box(0, 0, 10, 1), shapely.box(10, 0, 14, 4)])
        lon, lat = ra.label_point(bent)
        assert 10 < lon < 14 and 1 < lat < 3

    def test_neighbours_coloured_apart(self):
        # Four counties meeting at a corner, a fifth apart
        outlines = {1: shapely.box(0, 0, 1, 1), 2: shapely.box(1, 0, 2, 1), 3: shapely.box(0, 1, 1, 2),
                    4: shapely.box(1, 1, 2, 2), 5: shapely.box(5, 5, 6, 6)}
        colours = ra.county_colours(outlines)
        assert len({colours[c] for c in (1, 2, 3, 4)}) == 4 and colours[5] == 0

    @pytest.fixture
    def downloads(self, monkeypatch):
        """Serve the shapefile and SIRUTA as their sites would; the list holds the addresses asked."""
        asked = []

        def fake(url, dest, timeout=120, label=""):
            asked.append(url)
            with open(dest, "wb") as fh:
                fh.write(_shapefile_zip() if url == ra.LAU_URL else SIRUTA.encode("utf-8"))

        monkeypatch.setattr(ra, "_download", fake)
        return asked

    def test_downloaded_once(self, downloads, tmp_path):
        counties = ra.load_admin("counties", str(tmp_path))
        assert [c.name for c in counties.counties] == ["Ilfov", "Alba", "București"]   # largest first
        assert counties.uats == [] and counties.total_uats == 5
        uats = ra.load_admin("uats", str(tmp_path))
        # Municipalities, county seats first, then towns, then communes, the most populous first
        assert [(u.name, u.kind, u.seat) for u in uats.uats] == [
            ("București", "municipality", True), ("Alba Iulia", "municipality", True),
            ("Buftea", "town", False), ("Sânmiclăuș", "town", False), ("Ciugud", "commune", False)]
        assert uats.country.bounds == pytest.approx((23.5, 44.2, 26.4, 46.2))
        # The seats at either level: the capital first; Buftea, a town, is no seat in SIRUTA
        assert [(u.name, u.capital) for u in counties.seats] == [("București", True), ("Alba Iulia", False)]
        assert uats.seats == counties.seats
        assert downloads == [ra.SIRUTA_URL, ra.LAU_URL]                                 # cached
        assert sorted(os.listdir(tmp_path)) == [f"ro_admin_v{ra.DATA_VERSION}.npz"]     # downloads removed

    def test_unreachable(self, tmp_path, monkeypatch):
        def offline(url, dest, timeout=120, label=""):
            raise OSError("no network")

        monkeypatch.setattr(ra, "_download", offline)
        with pytest.raises(ra.AdminError, match="Could not read Romania's counties and UATs: no network"):
            ra.load_admin("counties", str(tmp_path))
        assert not os.listdir(tmp_path)


def _globe(lat=46.0, lon=25.0, inches=15):
    ax = Figure(figsize=(inches, inches)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _admin(level="uats", uats=None):
    counties = [ra.Unit("Alba", 1, "county", shapely.box(16, 40, 26, 52), (21, 46)),
                ra.Unit("Ilfov", 23, "county", shapely.box(26, 40, 36, 52), (31, 46))]
    if uats is None:
        uats = [ra.Unit("Alba Iulia", 1, "municipality", shapely.box(16, 40, 21, 52), (18.5, 49), True, 63000),
                ra.Unit("Buftea", 23, "town", shapely.box(26, 40, 36, 44), (31, 42), False, 23000),
                ra.Unit("Ciugud", 1, "commune", shapely.box(21, 40, 26, 52), (23.5, 43), False, 4000),
                ra.Unit("Mic", 23, "commune", shapely.box(30, 48, 30.2, 48.2), (30.1, 48.1), False, 100)]
    country = shapely.box(16, 40, 36, 52)
    return ra.Admin(level, counties, uats if level == "uats" else [], country, {1: 0, 23: 1}, 4)


class TestDrawing:
    def test_counties_named_in_capitals_and_keyed(self):
        ax = _globe()
        shown = ra.draw_admin(ax, _admin("counties"))
        assert shown.counties == {1, 23} and not shown.uats and shown.border
        assert sorted(t.get_text() for t in ax.texts) == ["ALBA", "ILFOV"]
        legend = ra.add_admin_legend(ax, _admin("counties"), [shown])
        assert legend is not None
        assert legend.get_title().get_text() == ("Counties of Romania in view: 2 counties\n"
                                                 "GISCO LAU 2024, names from SIRUTA 2026 (INS)")
        assert [t.get_text() for t in legend.get_texts()] == ["Border of Romania", "County border (județ)"]

    def test_uats_named_by_kind(self):
        ax = _globe()
        shown = ra.draw_admin(ax, _admin())
        assert shown.uats == {"municipality": 1, "town": 1, "commune": 2}
        texts = {t.get_text(): t for t in ax.texts}
        assert set(texts) == {"ALBA", "ILFOV", "Alba Iulia", "Buftea", "Ciugud"}     # Mic is too small for its name
        assert texts["Alba Iulia"].get_fontweight() == "bold"                         # a county seat
        assert texts["Buftea"].get_fontweight() == "normal" and texts["Buftea"].get_fontstyle() == "normal"
        assert texts["Ciugud"].get_fontstyle() == "italic"
        legend = ra.add_admin_legend(ax, _admin(), [shown])
        assert legend is not None
        assert legend.get_title().get_text().splitlines()[1] == (
            "4 of its 4 UATs: 1 municipality, 1 town, 2 communes")
        assert [t.get_text() for t in legend.get_texts()][-1] == "Border of a municipality, town or commune (UAT)"

    def test_seat_named_like_its_county(self):
        ax = _globe()
        uats = [ra.Unit("Alba", 1, "municipality", shapely.box(16, 46, 26, 52), (21, 50), True, 63000),
                ra.Unit("Ciugud", 1, "commune", shapely.box(16, 40, 26, 46), (21, 41), False, 4000)]
        ra.draw_admin(ax, _admin(uats=uats))
        assert sorted(t.get_text() for t in ax.texts) == ["ALBA", "Alba", "Ciugud", "ILFOV"]

    def test_a_county_of_one_uat_named_once(self):
        ax = _globe()
        bucharest = ra.Unit("București", 40, "county", shapely.box(16, 40, 26, 52), (21, 46))
        admin = _admin(uats=[ra.Unit("București", 40, "municipality", bucharest.outline, (21, 46), True)])
        ra.draw_admin(ax, admin._replace(counties=[bucharest]))
        assert [t.get_text() for t in ax.texts] == ["BUCUREȘTI"]

    def test_not_named_twice_beside_a_city(self):
        ax = _globe()
        ax.annotate("Alba Iulia", (0, 0))                     # as the cities layer would
        ra.draw_admin(ax, _admin())
        assert [t.get_text() for t in ax.texts].count("Alba Iulia") == 1

    def test_names_share_the_room_of_other_layers(self):
        ax = _globe()
        space = label_space(ax)
        ra.draw_admin(ax, _admin())
        assert space.placed and getattr(ax, "_ortho_label_space") is space

    def test_filled_neighbours_apart(self):
        ax = _globe()
        ra.draw_admin(ax, _admin("counties"), alpha=0.5)
        fills = {tuple(c.get_facecolor()[0]) for c in ax.collections
                 if len(c.get_facecolor()) and c.get_facecolor()[0][3] > 0}
        assert len(fills) == 2 and all(f[3] == 0.5 for f in fills)
        legend = ra.add_admin_legend(ax, _admin("counties"), [ra.draw_admin(_globe(), _admin("counties"))], alpha=0.5)
        assert legend is not None and legend.get_texts()[-1].get_text() == "Counties told apart by colour"

    def test_inside_romania_no_border_in_the_key(self):
        ax = _globe(46, 25, inches=4)
        admin = _admin("counties")._replace(counties=[ra.Unit("Alba", 1, "county", shapely.box(-180, -90, 180, 90),
                                                               (25, 46))])
        shown = ra.draw_admin(ax, admin)
        assert not shown.border
        legend = ra.add_admin_legend(ax, admin, [shown])
        assert legend is not None and [t.get_text() for t in legend.get_texts()] == ["County border (județ)"]

    def test_bucharest_in_the_key(self):
        ax = _globe()
        bucharest = ra.Unit("București", 40, "county", shapely.box(26, 40, 36, 52), (31, 46))
        admin = _admin("counties")._replace(counties=[_admin().counties[0], bucharest])
        legend = ra.add_admin_legend(ax, admin, [ra.draw_admin(ax, admin)])
        assert legend is not None and legend.get_title().get_text().startswith(
            "Counties of Romania in view: 1 county and the municipality of Bucharest\n")

    def test_seats_marked_by_counties(self):
        ax = _globe()
        bucharest = ra.Unit("București", 40, "county", shapely.box(29, 40, 31, 42), (30, 41))
        seats = [ra.Unit("București", 40, "municipality", bucharest.outline, (30, 41), True, 2100000, True),
                 ra.Unit("Alba Iulia", 1, "municipality", shapely.box(18, 48, 19, 49), (18.5, 48.5), True, 63000)]
        admin = _admin("counties")._replace(counties=[*_admin().counties, bucharest], seats=seats)
        shown = ra.draw_admin(ax, admin)
        assert (shown.seats, shown.capital) == (2, True)
        texts = {t.get_text(): t for t in ax.texts}
        assert set(texts) == {"ALBA", "ILFOV", "București", "Alba Iulia"}       # Bucharest named once, as the capital
        assert texts["București"].get_fontweight() == "bold" and texts["Alba Iulia"].get_fontweight() == "normal"
        assert len(ax.lines) == 3                                                # two dots, and the capital's ring
        legend = ra.add_admin_legend(ax, admin, [shown])
        assert legend is not None and [t.get_text() for t in legend.get_texts()][-2:] == ["Capital", "County seat"]

    def test_seats_not_marked_by_uats(self):
        ax = _globe()
        seats = [ra.Unit("Alba Iulia", 1, "municipality", shapely.box(16, 40, 21, 52), (18.5, 49), True, 63000)]
        shown = ra.draw_admin(ax, _admin()._replace(seats=seats))
        assert shown.seats == 0 and not ax.lines
        assert [t.get_text() for t in ax.texts].count("Alba Iulia") == 1      # named as a UAT, in bold

    def test_nothing_in_view(self):
        ax = _globe(-46, -155)
        shown = ra.draw_admin(ax, _admin())
        assert not shown.counties and not shown.uats and not ax.texts
        assert ra.add_admin_legend(ax, _admin(), [shown]) is None
