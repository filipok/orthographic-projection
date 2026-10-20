"""Tests for iucn.py (no network; small range files written here)."""

import io
import os
import zipfile

import matplotlib.patches as mpatches
import pytest
import shapefile
import shapely
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import iucn
from rotation import globe_projection

SQUARE = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]          # clockwise: an outer ring
HOLE = [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]                 # anticlockwise: a hole


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _square(x, y, size=5):
    return [(x, y), (x, y + size), (x + size, y + size), (x + size, y), (x, y)]


# (sci_name, genus, family, presence, origin, seasonal, category, rings)
PARTS = [
    ("Panthera leo", "Panthera", "FELIDAE", 1, 1, 1, "VU", [_square(20, -10)]),
    ("Panthera leo", "Panthera", "FELIDAE", 1, 1, 1, "VU", [_square(23, -10)]),       # overlaps: merged
    ("Panthera leo", "Panthera", "FELIDAE", 5, 1, 1, "VU", [_square(0, 30)]),
    ("Panthera pardus", "Panthera", "FELIDAE", 1, 1, 1, "VU", [_square(80, 10)]),
    ("Lynx lynx", "Lynx", "FELIDAE", 3, 1, 1, "LC", [SQUARE, HOLE]),
]


def _write_ranges(path, parts=PARTS, member="MAMMALS_PART1"):
    """A zip of one IUCN-style shapefile at *path*."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON) as w:
        w.field("id_no", "N", 10)
        w.field("sci_name", "C", 254)
        for name in ("presence", "origin", "seasonal"):
            w.field(name, "N", 5)
        for name in ("genus", "family", "category"):
            w.field(name, "C", 50)
        for i, (sci, genus, family, presence, origin, seasonal, category, rings) in enumerate(parts):
            w.poly(rings)
            w.record(i, sci, presence, origin, seasonal, genus, family, category)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for ext, data in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"{member}.{ext}", data.getvalue())
        z.writestr(f"{member}.cpg", "UTF-8")
    return path


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "iucn"
    folder.mkdir()
    _write_ranges(folder / "MAMMALS.zip")
    return str(folder)


@pytest.fixture
def no_gbif(monkeypatch):
    import gbif

    def unknown(name, cache_dir=None):
        raise ValueError("unknown")

    monkeypatch.setattr(gbif, "resolve_taxon", unknown)


class TestSources:
    def test_every_shapefile_in_the_zips(self, data):
        _write_ranges(os.path.join(data, "redlist_species_data.zip"), PARTS[:1], member="data_0")
        open(os.path.join(data, "notes.txt"), "w").close()
        sources = iucn.find_sources(data)
        assert [s.member for s in sources] == ["MAMMALS_PART1", "data_0"]
        assert iucn.find_sources(os.path.join(data, "MAMMALS.zip"))[0].member == "MAMMALS_PART1"

    def test_none(self, tmp_path):
        with pytest.raises(ValueError, match="No IUCN range files .*iucnredlist.org"):
            iucn.find_sources(str(tmp_path))
        with pytest.raises(ValueError, match="No IUCN range files"):
            iucn.find_sources(str(tmp_path / "missing"))

    def test_damaged_zip_skipped(self, data, caplog):
        with open(os.path.join(data, "broken.zip"), "wb") as f:
            f.write(b"not a zip")
        assert len(iucn.find_sources(data)) == 1 and "broken.zip" in caplog.text


@pytest.mark.parametrize("codes, kind", [
    ((1, 1, 1), iucn.RESIDENT), ((2, 1, 1), iucn.RESIDENT), ((1, 2, 1), iucn.RESIDENT),
    ((1, 1, 2), iucn.BREEDING), ((1, 1, 3), iucn.NONBREEDING), ((1, 1, 4), iucn.PASSAGE), ((1, 1, 5), iucn.RESIDENT),
    ((3, 1, 1), iucn.POSSIBLY_EXTANT), ((6, 1, 1), iucn.POSSIBLY_EXTANT),
    ((1, 3, 1), iucn.INTRODUCED), ((3, 3, 1), iucn.INTRODUCED), ((1, 4, 5), iucn.VAGRANT),
    ((4, 1, 1), iucn.POSSIBLY_EXTINCT), ((5, 3, 1), iucn.EXTINCT),
])
def test_kinds(codes, kind):
    assert iucn.range_kind(*codes) == kind


def _record(rings):
    shp = io.BytesIO()
    with shapefile.Writer(shp=shp, shx=io.BytesIO(), dbf=io.BytesIO(), shapeType=shapefile.POLYGON) as w:
        w.field("x", "N", 1)
        w.poly(rings)
        w.record(1)
    return shp.getvalue()[108:]                                 # past the file header and the record header


class TestPolygons:
    def test_hole_inside_its_ring(self):
        assert iucn.parse_polygon(_record([SQUARE, HOLE])).area == pytest.approx(96)

    def test_islands(self):
        geometry = iucn.parse_polygon(_record([_square(0, 0), _square(20, 0)]))
        assert geometry.geom_type == "MultiPolygon" and geometry.area == pytest.approx(50)

    def test_rings_the_wrong_way_round(self):
        assert iucn.parse_polygon(_record([SQUARE[::-1]])).area == pytest.approx(100)

    def test_not_a_polygon(self):
        assert iucn.parse_polygon(b"\x00\x00\x00\x00") is None


class TestRanges:
    def test_species_merged_by_kind(self, data, tmp_path, no_gbif):
        found = iucn.load_range("panthera  LEO", data, str(tmp_path / "cache"))
        assert (found.name, found.rank, found.species, found.category) == ("Panthera leo", "SPECIES", 1, "VU")
        assert found.kinds == [iucn.EXTINCT, iucn.RESIDENT]
        assert [round(g.area) for g in found.geometries] == [25, 40]   # the two living squares as one
        assert found.dated[:4].isdigit() and iucn.range_label(found) == "Panthera leo, Vulnerable"

    def test_read_once(self, data, tmp_path, monkeypatch, no_gbif):
        cache = str(tmp_path / "cache")
        iucn.load_range("Panthera leo", data, cache)
        monkeypatch.setattr(iucn, "_build", lambda *a: pytest.fail("read again"))
        assert iucn.load_range("Panthera leo", data, cache).name == "Panthera leo"

    def test_new_files_read_afresh(self, data, tmp_path, no_gbif):
        cache = str(tmp_path / "cache")
        iucn.load_range("Panthera leo", data, cache)
        _write_ranges(os.path.join(data, "MAMMALS.zip"), PARTS[:1])
        os.utime(os.path.join(data, "MAMMALS.zip"), (0, 0))
        assert iucn.load_range("Panthera leo", data, cache).kinds == [iucn.RESIDENT]

    @pytest.mark.parametrize("name, label", [
        ("Panthera", "Panthera (genus, 2 species)"),
        ("felidae", "Felidae (family, 3 species)"),
    ])
    def test_genus_and_family(self, data, tmp_path, no_gbif, name, label):
        assert iucn.range_label(iucn.load_range(name, data, str(tmp_path / "cache"))) == label

    def test_common_name_through_gbif(self, data, tmp_path, monkeypatch):
        import gbif

        monkeypatch.setattr(gbif, "resolve_taxon",
                            lambda name, cache_dir=None: gbif.Taxon(5219404, "Panthera leo", "SPECIES", "lion"))
        found = iucn.load_range("lion", data, str(tmp_path / "cache"))
        assert iucn.range_label(found) == "Panthera leo (lion), Vulnerable"

    def test_unknown_name_suggests(self, data, tmp_path, no_gbif):
        with pytest.raises(ValueError, match="no species, genus or family called 'Panthera leon'.*'Panthera leo'"):
            iucn.load_range("Panthera leon", data, str(tmp_path / "cache"))

    def test_blank_name(self, data):
        with pytest.raises(ValueError, match="Give a species name"):
            iucn.load_range("  ", data)

    def test_damaged_shapes(self, data, tmp_path, no_gbif):
        path = os.path.join(data, "MAMMALS.zip")
        with zipfile.ZipFile(path) as z:
            members = {n: z.read(n) for n in z.namelist()}
        members["MAMMALS_PART1.shp"] = members["MAMMALS_PART1.shp"][:120]
        with zipfile.ZipFile(path, "w") as z:
            for n, b in members.items():
                z.writestr(n, b)
        with pytest.raises(iucn.IucnDataError, match="Could not read the IUCN range of Panthera leo"):
            iucn.load_range("Panthera leo", data, str(tmp_path / "cache"))


class TestDrawing:
    def test_drawn_keyed_and_credited(self, data, tmp_path, no_gbif):
        found = iucn.load_range("Panthera leo", data, str(tmp_path / "cache"))
        ax = _globe(0, 20)
        iucn.draw_range(ax, found)
        assert len(ax.collections) == 2
        legend = iucn.add_range_legend(ax, found)
        assert [t.get_text() for t in legend.get_texts()] == ["Extant (resident)", "Extinct"]
        assert legend.get_title().get_text() == "Range of Panthera leo, Vulnerable (IUCN Red List)"
        hatched = [h for h in legend.legend_handles if isinstance(h, mpatches.Patch) and h.get_hatch()]
        assert len(hatched) == 1                                   # the extinct part
        assert iucn.iucn_attribution(found).startswith(f"Range: IUCN ({found.dated[:4]}), The IUCN Red List")

    def test_geometry_kept_as_degrees(self, data, tmp_path, no_gbif):
        found = iucn.load_range("Lynx lynx", data, str(tmp_path / "cache"))
        assert found.kinds == [iucn.POSSIBLY_EXTANT]
        assert shapely.bounds(found.geometries[0]).tolist() == [0, 0, 10, 10]
