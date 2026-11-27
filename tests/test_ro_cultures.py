"""Tests for ro_cultures.py (no network; a small register, SIRUTA table and communes made here)."""

import os

import numpy as np
import pytest
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import ro_cultures as rc
from ro_admin import AdminError, Unit
from rotation import globe_projection

# Two communes of 0.2 x 0.2 degrees, and the localities in them
UATS = {100: Unit("Cucuteni", 22, "commune", shapely.box(26.9, 47.2, 27.1, 47.4), (27.0, 47.3)),
        200: Unit("Oituz", 4, "commune", shapely.box(26.4, 46.1, 26.6, 46.3), (26.5, 46.2))}
UAT_CODES = {100: 100, 101: 100, 102: 100, 200: 200, 201: 200, 900: 900}      # 900: a commune not on the map

SIRUTA = ("SIRUTA;DENLOC;CODP;JUD;SIRSUP;TIP;NIV\n10;JUDEŢUL IAŞI;0;22;1;40;1\n100;CUCUTENI;0;22;10;3;2\n"
          "101;CUCUTENI;1;22;100;22;3\n200;OITUZ;0;4;20;3;2\n201;OITUZ;1;4;200;22;3\n")

COLUMNS = ["identificatorul sitului", rc.SITE, "numele sitului", rc.SIRUTA, rc.LON, rc.LAT, *rc.CULTURE_FIELDS]


def _ran(*rows):
    """RAN's table of (site, locality, lon, lat, culture, culture of a complex, of an object)."""
    lines = ["|".join(COLUMNS)]
    for site, siruta, lon, lat, *cultures in rows:
        cultures += [""] * (3 - len(cultures))
        lines.append("|".join(["1", site, '"La Bordei', siruta, lon, lat, *cultures]))
    return "\r\n".join(lines) + "\r\n"


def _globe(lat=46.7, lon=26.8, radius=150e3):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_extent((-radius, radius, -radius, radius), crs=ax.projection)
    return ax


def _read(*rows):
    return rc.read_sites(_ran(*rows), UAT_CODES, UATS)


def _of(table, name):
    mine = table["culture"] == table["names"].tolist().index(name)
    return list(zip(table["lon"][mine].tolist(), table["lat"][mine].tolist(), table["located"][mine].tolist()))


class TestNames:
    @pytest.mark.parametrize("text, names", [
        ("Cucuteni", ["Cucuteni"]),
        ("Boian, Gumelnița", ["Boian", "Gumelnița"]),
        ("Cheile Turzii; Petrești-Iclod", ["Cheile Turzii", "Petrești-Iclod"]),
        ("Glina și Tei", ["Glina", "Tei"]),
        ("Glina/Tei", ["Glina/Tei"]),                          # a passage, or a doubt: not two cultures
        ("Costișa-Komariv sau Noua", ["Costișa-Komariv sau Noua"]),
        ("Cucuteni ?", ["Cucuteni (?)"]), ("Iamnaia (?)", ["Iamnaia (?)"]), ("Jogodin?", ["Jogodin (?)"]),
        ("  Suciu  de Sus ", ["Suciu de Sus"]),
        ("neprecizată", []), ("Neprecizată", []), ("neprecizată.", []), ("necunoscută", []), ("necunoscut", []), ("Fără încadrare culturală certa", []),
        ("474", []), ("sec. XIII -XIV", []), ("Vinča A, B1/B2", ["Vinča A"]), ("", []), (None, []), ("?", []),
    ])
    def test_split(self, text, names):
        assert rc.split_cultures(text) == names

    def test_keys(self):
        assert rc.culture_key("Starčevo - Criș") == rc.culture_key("starcevo-cris")
        assert rc.culture_key("Cernavodă I") != rc.culture_key("Cernavodă II")
        assert rc.culture_key("Cucuteni (?)") != rc.culture_key("Cucuteni")

    @pytest.mark.parametrize("names, chosen", [
        ("Cucuteni", ["Cucuteni"]), ("Cucuteni, Noua ,, ", ["Cucuteni", "Noua"]), ("cucuteni+suciu  de sus", ["cucuteni", "suciu de sus"]),
        (["Cucuteni", "Noua, Tei"], ["Cucuteni", "Noua", "Tei"]), ("Cucuteni, CUCUTENI", ["CUCUTENI"]),
    ])
    def test_parse(self, names, chosen):
        assert rc.parse_cultures(names) == chosen

    @pytest.mark.parametrize("names, problem", [(" , ", "Name the cultures"), ([], "Name the cultures"),
                                                ("a,b,c,d,e,f,g,h,i", "At most 8 cultures")])
    def test_bad_lists(self, names, problem):
        with pytest.raises(ValueError, match=problem):
            rc.parse_cultures(names)


class TestReadSites:
    def test_located_sites_use_their_decimal_comma(self):
        table = _read(("1.01", "101", "26,95", "47,25", "Cucuteni"))
        assert _of(table, "Cucuteni") == [(26.95, 47.25, True)] and table["place"].tolist() == [1]

    @pytest.mark.parametrize("lon, lat", [("", ""), ("26,95", ""), ("27°30'00\"", "47°15'00\""), ("nan", "nan")])
    def test_a_site_without_coordinates_is_placed_at_its_commune(self, lon, lat):
        table = _read(("1.01", "101", lon, lat, "Cucuteni"))
        assert _of(table, "Cucuteni") == [(27.0, 47.3, False)] and table["place"].tolist() == [-100]

    @pytest.mark.parametrize("lon, located", [("27,10", True), ("27,20", True), ("27,24", False), ("22,00", False)])
    def test_coordinates_far_from_the_commune_are_not_used(self, lon, located):
        # The commune ends at 27.1 E; at 47.3 N 10 km is 0.133 degrees
        ((x, _, at_site),) = _of(_read(("1.01", "102", lon, "47,30", "Cucuteni")), "Cucuteni")
        assert at_site is located and x == (float(lon.replace(",", ".")) if located else 27.0)

    def test_a_site_counts_once_for_each_of_its_cultures(self):
        table = _read(("1.01", "101", "26,95", "47,25", "Cucuteni"), ("1.01", "101", "26,95", "47,25", "Noua", "", "Cucuteni"),
                      ("1.01", "101", "26,95", "47,25", "neprecizată"))
        assert len(_of(table, "Cucuteni")) == 1 and len(_of(table, "Noua")) == 1
        assert set(table["place"].tolist()) == {1}

    def test_spellings_are_joined_and_named_as_most_often_written(self):
        table = _read(("1.01", "101", "", "", "Starcevo-Criș"), ("1.02", "101", "", "", "Starčevo - Criș"),
                      ("1.03", "101", "", "", "Starčevo - Criș"), ("1.04", "101", "", "", "Cucuteni - Ariușd"),
                      ("1.05", "101", "", "", "Cucuteni"))
        assert sorted(table["names"].tolist()) == ["Cucuteni", "Cucuteni - Ariușd", "Starčevo - Criș"]
        assert len(_of(table, "Starčevo - Criș")) == 3 and len(_of(table, "Cucuteni")) == 1

    def test_joined_spellings_are_one_culture_and_one_site(self):
        table = _read(("1.01", "101", "", "", "Jamnaia"), ("1.01", "101", "", "", "Yamnaia"), ("1.02", "101", "", "", "Iamnaja"),
                      ("1.03", "101", "", "", "Jamnaja"), ("1.04", "101", "", "", "Yamnaja"), ("1.05", "101", "", "", "Jamnaia"),
                      ("1.06", "101", "", "", "Iamnaia (?)"), ("1.07", "101", "", "", "Iamnaia (?)"), ("1.08", "101", "", "", "Yamnaia (?)"))
        assert sorted(table["names"].tolist()) == ["Yamnaia", "Yamnaia (?)"]
        assert len(_of(table, "Yamnaia")) == 5 and len(_of(table, "Yamnaia (?)")) == 3
        assert rc.culture_key("jamnaja") == rc.culture_key("Yamnaia") != rc.culture_key("Iamnaia ?")

    def test_names_that_may_be_a_phase_or_a_group_stay_apart(self):
        apart = ["Cucuteni", "Cucuteni B", "Cucuteni - Ariușd", "Ariușd", "Cernavodă", "Cernavodă I", "Criș", "Starčevo - Criș",
                 "Sântana de Mureș", "Sântana de Mureș - Cerneahov", "Wietenberg", "Sighișoara - Wietenberg", "Costișa",
                 "Costișa-Komariv", "getică", "geto-dacică", "dacică", "dacilor liberi", "Gáva", "Grănicești - Gáva"]
        assert len({rc.culture_key(name) for name in apart}) == len(apart)

    def test_quotation_marks_are_text(self):
        rows = [("1.01", "101", "", "", "Cucuteni"), ("1.02", "101", "", "", 'scitică ("grupul Ciumbrud")'),
                ("1.03", "101", "", "", "Noua")]
        table = _read(*rows)                                  # each site's name opens a quotation and never closes it
        assert len(table["culture"]) == 3 and "scitică (grupul Ciumbrud)" in table["names"]

    def test_sites_in_no_known_commune_are_left_out(self, caplog):
        table = _read(("1.01", "101", "", "", "Cucuteni"), ("1.02", "555", "", "", "Cucuteni"), ("1.03", "", "", "", "Cucuteni"),
                      ("1.04", "900", "26,95", "47,25", "Cucuteni"))
        assert len(_of(table, "Cucuteni")) == 1 and "3 archaeological sites are in a commune" in caplog.text

    @pytest.mark.parametrize("text, problem", [
        (_ran(), "names no cultures"), (_ran(("1.01", "101", "", "", "neprecizată")), "names no cultures"),
        (_ran(("1.01", "555", "", "", "Cucuteni")), "none of RAN's sites"), ("a|b\n1|2\n", "no column"), ("", "no column"),
    ])
    def test_unexpected_tables(self, text, problem):
        with pytest.raises(rc.CulturesError, match=problem):
            rc.read_sites(text, UAT_CODES, UATS)


RAN = _ran(("1.01", "101", "26,95", "47,25", "Cucuteni, Noua"), ("1.02", "101", "", "", "Cucuteni"), ("1.03", "102", "", "", "Cucuteni"),
           ("1.04", "101", "", "", "Noua"), ("1.05", "201", "26,50", "46,15", "Cucuteni B"), ("1.06", "201", "", "", "Cucuteni (?)"))


@pytest.fixture
def served(monkeypatch):
    """Serve the register and SIRUTA; returns the URLs downloaded."""
    fetched = []

    def download(url, dest, timeout=120, label=""):
        fetched.append(url)
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(RAN if url == rc.RAN_URL else SIRUTA + "102;BAICENI;1;22;100;22;3\n")

    monkeypatch.setattr(rc, "_download", download)
    monkeypatch.setattr(rc, "load_uats", lambda: UATS)
    return fetched


class TestLoad:
    def test_downloaded_once_and_only_the_table_kept(self, served, tmp_path):
        for _ in range(2):
            (sites,) = rc.load_cultures("cucuteni", str(tmp_path))
        assert len(served) == 2
        assert os.listdir(tmp_path) == [f"ro_cultures_v{rc.DATA_VERSION}.npz"]
        assert sites.name == "Cucuteni" and sites.located.tolist() == [True, False, False]

    def test_in_the_order_asked_and_never_joined(self, served, tmp_path):
        found = rc.load_cultures(["noua", "Cucuteni B", "Cucuteni (?)", "Cucuteni"], str(tmp_path))
        assert [(s.name, len(s.lon)) for s in found] == [("Noua", 2), ("Cucuteni B", 1), ("Cucuteni (?)", 1), ("Cucuteni", 3)]

    @pytest.mark.parametrize("name, hint", [("Cucutenni", "did you mean Cucuteni, Cucuteni B"), ("Cucu", "did you mean Cucuteni"),
                                            ("zzz", "RAN has no culture 'zzz'\\.$")])
    def test_an_unknown_name_lists_those_like_it(self, served, tmp_path, name, hint):
        with pytest.raises(rc.UnknownCultureError, match=hint):
            rc.load_cultures(name, str(tmp_path))

    def test_a_failure_keeps_nothing(self, served, monkeypatch, tmp_path):
        def fail(*args, **kwargs):
            raise OSError("offline")
        monkeypatch.setattr(rc, "_download", fail)
        with pytest.raises(rc.CulturesError, match="Could not read Romania's archaeological sites: offline"):
            rc.load_cultures("Cucuteni", str(tmp_path))
        assert os.listdir(tmp_path) == []

    def test_no_communes(self, served, monkeypatch, tmp_path):
        def fail():
            raise AdminError("no shapefile")
        monkeypatch.setattr(rc, "load_uats", fail)
        with pytest.raises(rc.CulturesError, match="no shapefile"):
            rc.load_cultures("Cucuteni", str(tmp_path))


def _sites(name, *rows):
    lon, lat, located, place = zip(*rows)
    return rc.Sites(name, np.array(lon, float), np.array(lat, float), np.array(located, bool), np.array(place))


CUCUTENI = _sites("Cucuteni", (26.95, 47.25, True, 1), *[(27.0, 47.3, False, -100)] * 3, (26.5, 46.2, False, -200))
NOUA = _sites("Noua", (26.95, 47.25, True, 1), (27.0, 47.3, False, -100))


class TestMarks:
    @pytest.mark.parametrize("count, width", [(1, 4.0), (2, 5.8), (4, 5.8), (5, 8.0), (58, 8.0)])
    def test_sizes(self, count, width):
        assert rc.mark_width(count) == width and rc.mark_width(count, 2) == 2 * width

    def test_one_mark_for_a_communes_sites(self):
        marks = rc.place_marks([CUCUTENI])
        assert sorted((m.located, m.sites, m.width, m.shift) for m in marks) == [
            (False, 1, 4.0, 0.0), (False, 3, 5.8, 0.0), (True, 1, rc.SITE_POINTS, 0.0)]

    def test_cultures_that_share_a_point_lie_side_by_side(self):
        marks = rc.place_marks([CUCUTENI, NOUA], scale=2)
        for located, widths in ((True, (8.0, 8.0)), (False, (11.6, 8.0))):
            first, second = sorted((m for m in marks if m.located is located and m.lat > 47), key=lambda m: m.culture)
            assert (first.culture, second.culture) == (0, 1) and (first.width, second.width) == widths
            assert second.shift - first.shift == pytest.approx((first.width + second.width) / 2)      # they touch
            assert first.shift - first.width / 2 == pytest.approx(-(second.shift + second.width / 2))  # centred
        assert [m.shift for m in marks if m.lat < 47] == [0.0]

    def test_drawn_and_counted(self):
        ax = _globe()
        assert rc.draw_cultures(ax, [CUCUTENI, NOUA]) == [rc.Shown(1, 4), rc.Shown(1, 1)]
        assert sum(len(c.get_offsets()) for c in ax.collections) == 5          # 3 marks and 2
        hollow = [c for c in ax.collections if len(c.get_facecolors()) == 0]
        assert sum(len(c.get_offsets()) for c in hollow) == 3

    def test_out_of_sight(self):
        assert rc.draw_cultures(_globe(0, -100), [CUCUTENI]) == [rc.Shown(0, 0)]
        only_oituz = _globe(46.2, 26.5, 40e3)
        assert rc.draw_cultures(only_oituz, [CUCUTENI]) == [rc.Shown(0, 1)]


class TestKey:
    @pytest.mark.parametrize("language, labels, title", [
        ("en", ["Cucuteni (1,381 known, 154 by commune)", "1 site", "2-4", "5 or more"], "Archaeological sites by culture"),
        ("ro", ["Cucuteni (1381 localizate, 154 pe comună)", "1 sit", "2-4", "5 sau mai multe"], "Situri arheologice după cultură"),
        ("fr", ["Cucuteni (1,381 known, 154 by commune)", "1 site", "2-4", "5 or more"], "Archaeological sites by culture"),
    ])
    def test_counts_and_sizes(self, language, labels, title):
        ax = _globe()
        key = rc.add_cultures_legend(ax, [CUCUTENI], [rc.Shown(1381, 154)], language)
        assert key is not None and [t.get_text() for t in key.get_texts()] == labels
        assert key.get_title().get_text().startswith(title) and key in ax.artists

    def test_no_sizes_without_hollow_marks(self):
        key = rc.add_cultures_legend(_globe(), [CUCUTENI, NOUA], [rc.Shown(1, 0), rc.Shown(0, 0)])
        assert key is not None and len(key.get_texts()) == 2

    def test_nothing_shown(self):
        assert rc.add_cultures_legend(_globe(), [CUCUTENI], [rc.Shown(0, 0)]) is None


def test_joined_spellings_are_not_names_themselves():
    keys = [rc._fold(name) for names in rc.JOINED.items() for name in (names[0], *names[1])]
    assert len(set(keys)) == len(keys)


def test_every_culture_has_a_colour_of_its_own():
    assert len(set(rc.COLOURS)) == len(rc.COLOURS) == 8
