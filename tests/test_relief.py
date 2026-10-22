"""Tests for relief.py (no network; a small shapefile and legend made here)."""

import io
import os
import zipfile
from typing import Any

import numpy as np
import pytest
import shapefile
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import relief as rl
from rotation import globe_projection

ROWS = [
    ["Cod", "Denumire", "Cod", "Denumire", "Cod", "Denumire", "Cod", "Denumire"],
    ["I", "Carpaţii Orientali (Răsăriteni)", "A", "Carpaţii Maramureşului şi Bucovinei", "a.",
     "M-ţii Vulcanici de Nord (Oaş-Ţibles)", "0.2.", "Dep. Oaş (Ţara Oaşului)"],
    ["", "", "", "", "b.", "Dep.-le Gheorgheni-Ciuc", "2.2.", "Dep. Ciucului de Jos (Tuşnad)"],
    ["III", "Carpaţii Meridionali (Carpaţii de Miazăzi)", "", "", "d.", "Dep. Haţegului (Ţara Haţegului)"],
    # As in the legend: Câmpia Careiului is Câmpia Valea lui Mihai and Câmpia Pișcolțului, beside Câmpia Ierului
    ["XIII", "Câmpia Banato-Crişană", "A", "Câmpia Someşului", "", "", "3.", "C. Ierului"],
    ["", "", "", "", "", "", "4.", "C. Careiului", "4.1.", "C. Valea lui Mihai"],
    ["", "", "", "", "", "", "", "", "4.2.", "C. Pişcolţului"],
    ["", "", "", "", "", "", "5.", "Lc. Jiu-Jieţ"],
    ["", "", "B", "Câmpia Crişurilor", "", "", "1.", "Cul. Târnavei Mici"],
]


def _legend():
    return rl.legend_from_rows(ROWS)


@pytest.mark.parametrize("text, level", [
    (None, "units"), ("", "units"), ("all", "units"), ("Units", "units"), ("regions", "regions"),
    ("Region", "regions"), ("major", "regions"),
])
def test_levels(text, level):
    assert rl.resolve_relief_level(text) == level


def test_bad_level():
    with pytest.raises(ValueError, match="Unknown level of relief units 'counties'; choose from units, regions"):
        rl.resolve_relief_level("counties")


@pytest.mark.parametrize("name, parts", [
    ("Dep. Haţegului", ("Dep.", "Haţegului")), ("M-ţii Rodnei", ("M-ţii", "Rodnei")),
    ("Dep.-le Gheorgheni-Ciuc", ("Dep.-le", "Gheorgheni-Ciuc")),
    ("Depresiunea  Sovata (Sovata - Praid)", ("Depresiunea", "Sovata (Sovata - Praid)")),
    ("Cîmpul Colentinei", ("Cîmpul", "Colentinei")), ("Bistrita Bargaului", ("", "Bistrita Bargaului")),
    ("Depresiune", ("Depresiune", "")),
])
def test_split_kind(name, parts):
    assert rl.split_kind(name) == parts


class TestNames:
    def test_legend(self):
        legend = _legend()
        assert legend.regions == {"I": "Carpații Orientali (Răsăriteni)",
                                  "III": "Carpații Meridionali (Carpații de Miazăzi)",
                                  "XIII": "Câmpia Banato-Crișană"}                 # comma below, not cedilla
        assert legend.rests["hateguluitarahategului"] == "Hațegului (Țara Hațegului)"
        assert legend.words["oasului"] == "Oașului"

    def test_legend_hierarchy(self):
        entries = _legend().entries
        assert entries[("XIII", "valealuimihai")] == [
            rl.Entry("A", ("Câmpia Someșului", "C. Careiului", "C. Valea lui Mihai"))]
        assert entries[("XIII", "ierului")] == [rl.Entry("A", ("Câmpia Someșului", "C. Ierului"))]
        assert entries[("XIII", "tarnaveimici")] == [rl.Entry("B", ("Câmpia Crișurilor", "Cul. Târnavei Mici"))]
        assert entries[("III", "hateguluitarahategului")] == [rl.Entry("", ("Dep. Hațegului (Țara Hațegului)",))]

    def test_legend_without_regions(self):
        with pytest.raises(rl.ReliefError, match="names no regions"):
            rl.legend_from_rows([["", "", "1.", "Dep. Haţegului"]])

    def test_not_a_workbook(self):
        with pytest.raises(rl.ReliefError, match="not an Excel sheet"):
            rl.read_legend(b"<html>Not found</html>")

    @pytest.mark.parametrize("raw, name", [
        ("Depresiunea Hategului (Tara Hategului)", "Depresiunea Hațegului (Țara Hațegului)"),   # from the legend
        ("Câmpia Ierului", "Câmpia Ierului"),
        ("Lunca Jiu - Jiet", "Lunca Jiu-Jieț"),
        ("Culoarul  Târnavei Mici", "Culoarul Târnavei Mici"),
        ("Depresiunea Ciucului de Jos (Tusnad)", "Depresiunea Ciucului de Jos (Tușnad)"),
        ("Muntii Oasului", "Munții Oașului"),                                    # word by word
        ("Masivul Rodna (Muntii Rodnei)", "Masivul Rodna (Munții Rodnei)"),
        ("Podisul Tîrnaveni", "Podișul Târnaveni"),                              # î inside a word is â
        ("Depresiunea Întorsurii", "Depresiunea Întorsurii"),                    # but not at its start
        ("Cîmpul Colentinei", "Câmpul Colentinei"),
        ("MAsivul Vladeasa", "Masivul Vladeasa"),
        ("Depr. Poiana", "Depresiunea Poiana"),
        ("Bistrita Bargaului", "Bistrita Bargaului"),                            # nothing to restore it from
        ("Depresiune", ""), ("depresiune", ""),                                  # no name of its own
    ])
    def test_restored(self, raw, name):
        assert rl.restore_name(raw, _legend()) == name

    @pytest.mark.parametrize("name, full", [
        ("C. Careiului", "Câmpia Careiului"), ("Dep.-le Gheorgheni-Ciuc", "Depresiunile Gheorgheni-Ciuc"),
        ("M-ții Harghita", "Munții Harghita"), ("Ob-le Oltului (Brașovului)", "Obcinele Oltului (Brașovului)"),
        ("Dl. Silvaniei", "Dl. Silvaniei"), ("D. lui Bran", "Dl. lui Bran"),        # Dealul or Dealurile: kept short
        ("Câmpia Someșului", "Câmpia Someșului"), ("Carpații Moldo-Transilvani", "Carpații Moldo-Transilvani"),
    ])
    def test_group_names(self, name, full):
        assert rl.group_name(name) == full

    def test_entry_found_by_name_and_subregion(self):
        legend = rl.legend_from_rows(ROWS + [["", "", "C", "Câmpia Banatului", "", "", "2.", "C. Ierului"]])
        assert rl.find_entry("Câmpia Ierului", "XIII", "A", legend) == rl.Entry("A", ("Câmpia Someșului",
                                                                                      "C. Ierului"))
        assert rl.find_entry("Cîmpia Ierului", "XIII", "C", legend) == rl.Entry("C", ("Câmpia Banatului",
                                                                                      "C. Ierului"))
        assert rl.find_entry("Câmpia Ierului", "III", "", legend) is None               # in another region
        assert rl.find_entry("Depresiune", "XIII", "A", legend) is None

    def test_groups(self):
        someș, careiului = ("XIII", ("S",)), ("XIII", ("S", "C"))
        paths = [("XIII", ("S", "Ier")), ("XIII", ("S", "C", "V")), ("XIII", ("S", "C", "P")), None,
                 ("XIII", ("S", "C", "P")), ("XIII", ("S", "B", "X")), ("XIII", ("S", "B"))]
        groups = rl.unit_groups(paths)
        assert groups == {someș: [0, 1, 2, 4, 5, 6], careiului: [1, 2, 4]}  # B is mapped itself: not a group

    def test_short_names(self):
        assert rl.short_names("Depresiunea Hațegului (Țara Hațegului)") == [
            "Depresiunea Hațegului (Țara Hațegului)", "Depresiunea Hațegului", "Dep. Hațegului"]
        assert rl.short_names("Munții Rodnei") == ["Munții Rodnei", "M-ții Rodnei"]
        assert rl.short_names("Bistrița Bârgăului") == ["Bistrița Bârgăului"]


# Three units: one in the Southern Carpathians, one in the Banat-Crișana Plain and one without a name
UNITS = [
    ("Depresiunea Hategului (Tara Hategului)", "III", "", [(22.8, 45.5), (22.8, 45.7), (23.2, 45.7), (23.2, 45.5)]),
    ("Câmpia Ierului", "XIII", "A", [(21.9, 47.3), (21.9, 47.5), (22.5, 47.5), (22.5, 47.3)]),
    ("Depresiune", "III", "", [(23.3, 45.5), (23.3, 45.6), (23.4, 45.6), (23.4, 45.5)]),
    ("Câmpia Valea Lui Mihai", "XIII", "A", [(21.9, 47.5), (21.9, 47.7), (22.2, 47.7), (22.2, 47.5)]),
    ("Câmpia Piscoltului", "XIII", "A", [(22.2, 47.5), (22.2, 47.7), (22.5, 47.7), (22.5, 47.5)]),
]


def _shapefile_zip(units=UNITS):
    files: dict[str, Any] = {ext: io.BytesIO() for ext in ("shp", "shx", "dbf")}
    writer = shapefile.Writer(shp=files["shp"], shx=files["shx"], dbf=files["dbf"], shapeType=shapefile.POLYGON,
                              encoding="iso8859_16")
    writer.field("ID", "N")
    writer.field("DENUMIRE", "C", size=80)
    writer.field("REGIUNE", "C", size=5)
    writer.field("SUBREGIUNE", "C", size=1)
    for i, (name, region, subregion, ring) in enumerate(units):
        writer.poly([ring + [ring[0]]])
        writer.record(i, name, region, subregion)
    writer.close()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for ext, data in files.items():
            archive.writestr(f"limite_unitati_relief.{ext}", data.getvalue())
    return out.getvalue()


class TestData:
    def test_units_read(self):
        table = rl.read_units(_shapefile_zip(), _legend())
        assert table["names"].tolist() == ["Depresiunea Hațegului (Țara Hațegului)", "Câmpia Ierului", "",
                                           "Câmpia Valea lui Mihai", "Câmpia Pișcolțului"]
        assert table["regions"].tolist() == ["III", "XIII", "III", "XIII", "XIII"]
        assert table["group_names"].tolist() == ["Câmpia Someșului", "Câmpia Careiului"]
        assert table["group_regions"].tolist() == ["XIII", "XIII"]
        careiului = rl._unpack({"wkb": table["group_wkb"], "wkb_ends": table["group_wkb_ends"]})[1]
        assert careiului.bounds == pytest.approx((21.9, 47.5, 22.5, 47.7))      # its two halves
        assert dict(zip(table["region_codes"], table["region_names"]))["XIII"] == "Câmpia Banato-Crișană"
        outlines = rl._unpack(table)
        assert outlines[1].bounds == pytest.approx((21.9, 47.3, 22.5, 47.5))

    def test_archive_without_shapefile(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        with pytest.raises(rl.ReliefError, match="holds no shapefile"):
            rl.read_units(out.getvalue(), _legend())

    @pytest.fixture
    def geo_spatial(self, monkeypatch):
        """Serve the shapefile and a legend as geo-spatial.org's downloads; the list holds the addresses asked."""
        asked = []

        def fake(url, dest, timeout=120, label=""):
            asked.append(url)
            with open(dest, "wb") as fh:
                fh.write(_shapefile_zip() if url == rl.SHAPES_URL else b"legend")

        monkeypatch.setattr(rl, "_download", fake)
        monkeypatch.setattr(rl, "read_legend", lambda xls: _legend())
        return asked

    def test_downloaded_once_largest_first(self, geo_spatial, tmp_path):
        units = rl.load_relief("units", str(tmp_path))
        assert [u.name for u in units.units] == ["Câmpia Ierului", "Depresiunea Hațegului (Țara Hațegului)",
                                                 "Câmpia Valea lui Mihai", "Câmpia Pișcolțului", ""]
        assert units.level == "units" and len(units.borders) == 2
        assert [g.name for g in units.groups] == ["Câmpia Someșului", "Câmpia Careiului"]   # largest first
        rl.load_relief("regions", str(tmp_path))
        assert geo_spatial == [rl.SHAPES_URL, rl.LEGEND_URL]                    # cached
        assert sorted(os.listdir(tmp_path)) == [f"relief_units_v{rl.DATA_VERSION}.npz"]   # downloads removed

    def test_regions(self, geo_spatial, tmp_path):
        regions = rl.load_relief("regions", str(tmp_path))
        assert [(u.region, u.name) for u in regions.units] == [
            ("XIII", "Câmpia Banato-Crișană"), ("III", "Carpații Meridionali (Carpații de Miazăzi)")]
        assert regions.units[1].outline.area == pytest.approx(0.4 * 0.2 + 0.1 * 0.1)   # both units of III
        assert not regions.groups

    def test_unreachable(self, tmp_path, monkeypatch):
        def offline(url, dest, timeout=120, label=""):
            raise OSError("no network")

        monkeypatch.setattr(rl, "_download", offline)
        with pytest.raises(rl.ReliefError, match="Could not read the relief units: no network"):
            rl.load_relief("units", str(tmp_path))
        assert not os.listdir(tmp_path)


def _globe(lat=46.0, lon=25.0, inches=15):
    ax = Figure(figsize=(inches, inches)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _relief(level="units"):
    units = [rl.Unit("Carpații Orientali", "I", shapely.box(24, 45, 27, 48)),
             rl.Unit("Câmpia Română", "XIV", shapely.box(23, 43.5, 28, 45)),
             rl.Unit("", "I", shapely.box(27, 46, 28, 47))]
    return rl.Relief(level, units, {"I": "Carpații Orientali (Răsăriteni)", "XIV": "Câmpia Română"},
                     [shapely.box(24, 45, 28, 48), shapely.box(23, 43.5, 28, 45)])


class TestDrawing:
    def test_drawn_named_and_keyed(self):
        ax = _globe()
        counts = rl.draw_relief(ax, _relief())
        assert set(counts) == {0, 1, 2} and counts[0] > counts[2]
        assert sorted(t.get_text() for t in ax.texts) == ["Carpații Orientali", "Câmpia Română"]  # not the unnamed
        assert ax.texts[0].get_fontstyle() == "italic"
        legend = rl.add_relief_legend(ax, _relief(), [counts])
        assert legend is not None
        assert legend.get_title().get_text() == ("Relief units (3) of Romania in view, after Posea & Badea 1984 "
                                                 "(Candrea et al. 2008)")
        assert [t.get_text() for t in legend.get_texts()] == ["I. Carpații Orientali (Răsăriteni)",
                                                              "XIV. Câmpia Română"]

    def test_groups_named_in_bold_before_their_units(self):
        ax = _globe()
        careiului = rl.Unit("Câmpia Careiului", "XIII", shapely.box(17, 42, 33, 50))
        units = [rl.Unit("Câmpia Valea lui Mihai", "XIII", shapely.box(17, 42, 25, 50)),
                 rl.Unit("Câmpia Pișcolțului", "XIII", shapely.box(25, 42, 33, 50))]
        relief = rl.Relief("units", units, {"XIII": "Câmpia Banato-Crișană"}, [careiului.outline], [careiului])
        counts = rl.draw_relief(ax, relief)
        assert set(counts) == {0, 1}                                              # the units, not the group
        texts = {t.get_text(): t for t in ax.texts}
        assert list(texts)[0] == "Câmpia Careiului" and texts["Câmpia Careiului"].get_fontweight() == "bold"
        assert texts["Câmpia Pișcolțului"].get_fontweight() == "normal"
        legend = rl.add_relief_legend(ax, relief, [counts])
        assert legend is not None and legend.get_title().get_text().endswith(
            "\nin bold: the larger units they make up")

    def test_regions_key(self):
        ax = _globe()
        legend = rl.add_relief_legend(ax, _relief("regions"), [rl.draw_relief(ax, _relief("regions"))])
        assert legend is not None and legend.get_title().get_text().startswith("Major relief regions of Romania")

    def test_names_share_the_room_of_other_layers(self):
        ax = _globe()
        space = rl.label_space(ax)
        rl.draw_relief(ax, _relief())
        assert space.placed and getattr(ax, "_ortho_label_space") is space

    def test_name_shortened_to_fit(self):
        ax = _globe(inches=2)
        units = [rl.Unit("Depresiunea Hațegului (Țara Hațegului)", "III", shapely.box(20, 40, 30, 50))]
        rl.draw_relief(ax, rl.Relief("units", units, {"III": "Carpații Meridionali"}, []))
        assert [t.get_text() for t in ax.texts] == ["Depresiunea Hațegului"]       # without the other name

    def test_nothing_in_view(self):
        ax = _globe(-46, -155)
        counts = rl.draw_relief(ax, _relief())
        assert counts == {} and not ax.texts
        assert rl.add_relief_legend(ax, _relief(), [counts]) is None

    def test_named_deep_inside(self):
        # A long bar and a block at its end: the deepest point is in the block, not at the middle of all
        bent = shapely.union_all([shapely.box(0, 0, 10, 1), shapely.box(10, 0, 14, 4)])
        lon, lat = np.array([5.0, 12.0, 13.5]), np.array([0.5, 2.0, 3.5])
        assert rl.anchors(rl.Unit("x", "I", bent), lon, lat)[0] == 1
