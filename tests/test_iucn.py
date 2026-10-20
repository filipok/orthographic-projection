"""Tests for iucn.py (no network; small range files written here)."""

import io
import os
import re
import sqlite3
import zipfile

import matplotlib.patches as mpatches
import numpy as np
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
        assert iucn.range_label(found) == "Panthera leo, Vulnerable" and found.publisher == iucn.IUCN
        assert re.fullmatch(r"Range: IUCN \((\d{4})\), The IUCN Red List of Threatened Species, "
                            r"spatial data of \1-\d\d-\d\d", found.credit)

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

    def test_other_groups_keep_it(self, data, tmp_path, monkeypatch):
        import gbif

        cache = str(tmp_path / "cache")
        iucn.load_range("Panthera leo", data, cache)
        frogs = [("Rana temporaria", "Rana", "RANIDAE", 1, 1, 1, "LC", [_square(0, 50)])]
        _write_ranges(os.path.join(data, "AMPHIBIANS.zip"), frogs, member="AMPHIBIANS_PART1")
        monkeypatch.setattr(iucn, "_build", lambda *a: pytest.fail("read again"))
        monkeypatch.setattr(gbif, "resolve_taxon",
                            lambda name, cache_dir=None: gbif.Taxon(5219404, "Panthera leo", "SPECIES", "lion"))
        assert iucn.load_range("Panthera leo", data, cache).common is None
        assert iucn.load_range("lion", data, cache).common == "lion"     # the same range, by its common name
        assert len(os.listdir(cache)) == 1

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
        with pytest.raises(iucn.IucnDataError, match="Could not read the range of Panthera leo"):
            iucn.load_range("Panthera leo", data, str(tmp_path / "cache"))


class TestDrawing:
    def test_drawn_keyed_and_credited(self, data, tmp_path, no_gbif):
        found = iucn.load_range("Panthera leo", data, str(tmp_path / "cache"))
        ax = _globe(0, 20)
        iucn.draw_range(ax, found)
        assert len(ax.collections) == 4                            # each kind: its fill, then its outline
        legend = iucn.add_range_legend(ax, found)
        assert [t.get_text() for t in legend.get_texts()] == ["Extant (resident)", "Extinct"]
        assert legend.get_title().get_text() == "Range of Panthera leo, Vulnerable (IUCN Red List)"
        hatched = [h for h in legend.legend_handles if isinstance(h, mpatches.Patch) and h.get_hatch()]
        assert len(hatched) == 1                                   # the extinct part
        assert iucn.iucn_attribution(found) == found.credit

    def test_tiles_cover_the_range(self):
        ocean = shapely.box(-100, -40, 100, 60)
        pieces = iucn.tiles(ocean)
        assert len(pieces) == 8 * 4                            # tiles start at multiples of 30°
        assert sum(p.area for p in pieces) == pytest.approx(ocean.area)
        assert all(np.ptp(shapely.get_coordinates(p), axis=0).max() <= iucn.TILE_DEGREES for p in pieces)

    def test_hairline_gap_closed(self):
        west, east = shapely.box(-30, -20, 0, 20), shapely.box(0.00002, -20, 30, 20)   # 2 m apart
        merged = iucn.merge([west, east])
        assert merged.geom_type == "Polygon" and merged.area == pytest.approx(2400, rel=1e-6)
        apart = iucn.merge([west, shapely.box(0.01, -20, 30, 20)])                       # 1 km apart: kept
        assert apart.geom_type == "MultiPolygon"

    def test_specks_dropped(self):
        land = shapely.MultiPolygon([shapely.box(0, 0, 10, 10), shapely.box(20, 0, 20.001, 0.001)])
        assert iucn.drop_specks(land).equals(shapely.box(0, 0, 10, 10))
        assert iucn.drop_specks(shapely.GeometryCollection([shapely.box(0, 0, 1, 1), shapely.LineString([(0, 0), (1, 1)])])
                                ).equals(shapely.box(0, 0, 1, 1))
        assert iucn.drop_specks(shapely.box(0, 0, 0.001, 0.001)).is_empty

    def test_inside_out_piece_split_and_retried(self, monkeypatch):
        ax = _globe(0, 0)
        piece = shapely.box(0, 0, 20, 20)                           # spans four 15° tiles
        real = ax.projection.project_geometry
        calls = []

        def project_geometry(geometry, crs):          # the whole disc for the first, as cartopy does at times
            calls.append(geometry)
            return shapely.box(-6e6, -6e6, 6e6, 6e6) if len(calls) == 1 else real(geometry, crs)

        monkeypatch.setattr(ax.projection, "project_geometry", project_geometry)
        projected = iucn.project(ax, piece)
        assert len(calls) == 5 and len(projected) == 4                     # cut in four, each drawn
        assert sum(p.area for p in projected) < iucn._largest_area(piece)

    def test_geometry_kept_as_degrees(self, data, tmp_path, no_gbif):
        found = iucn.load_range("Lynx lynx", data, str(tmp_path / "cache"))
        assert found.kinds == [iucn.POSSIBLY_EXTANT]
        assert shapely.bounds(found.geometries[0]).tolist() == pytest.approx([0, 0, 10, 10])


# ---------------------------------------------------------------------------
# BirdLife's GeoPackage and checklist
# ---------------------------------------------------------------------------


def _gpkg_blob(geometry):
    """A GeoPackage geometry: the header (no envelope, little-endian, EPSG:4326), then WKB."""
    return b"GP\x00\x01" + (4326).to_bytes(4, "little") + shapely.to_wkb(geometry)


# (sci_name, presence, origin, seasonal, version, geometry)
BIRDS = [
    ("Haliaeetus albicilla", 1, 1, 1, "2024.1", shapely.box(0, 50, 30, 70)),
    ("Haliaeetus albicilla", 1, 1, 3, "2025.2", shapely.box(0, 40, 20, 50)),
    ("Haliaeetus albicilla", 1, 1, 4, "unpublished", shapely.box(20, 40, 25, 50)),
    ("Haliaeetus leucocephalus", 1, 1, 1, "2023.1", shapely.box(-120, 30, -70, 60)),
    ("Pandion haliaetus", 1, 1, 2, "2025.1", shapely.MultiPolygon([shapely.box(-10, 50, 40, 70)])),
]


def _write_geopackage(path, birds=BIRDS):
    db = sqlite3.connect(path)
    with db:
        db.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
        db.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)")
        db.execute("INSERT INTO gpkg_contents VALUES ('all_species', 'features')")
        db.execute("INSERT INTO gpkg_geometry_columns VALUES ('all_species', 'geom')")
        db.execute("CREATE TABLE all_species (fid INTEGER PRIMARY KEY, geom BLOB, sci_name TEXT, presence INT, "
                   "origin INT, seasonal INT, version TEXT)")
        for i, (name, presence, origin, seasonal, version, geometry) in enumerate(birds):
            db.execute("INSERT INTO all_species VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (i + 10, _gpkg_blob(geometry), name, presence, origin, seasonal, version))
    db.close()
    return path


CHECKLIST = [
    ["Seq.", "Order", "Family name", "Common name", "Scientific name", "2025 IUCN Red List category",
     "Alternative common names"],
    ["1", "ACCIPITRIFORMES", "Accipitridae", "White-tailed Sea-eagle", "Haliaeetus albicilla", "LC",
     "Grey Sea Eagle, Pigargo Europeo"],
    ["2", "ACCIPITRIFORMES", "Accipitridae", "Bald Eagle", "Haliaeetus leucocephalus", "LC", ""],
    ["3", "ACCIPITRIFORMES", "Pandionidae", "Western Osprey", "Pandion haliaetus", "LC", "Osprey"],
    ["4", "STRUTHIONIFORMES", "Struthionidae", "Common Ostrich", "Struthio camelus", "LC", ""],
    ["5", "ACCIPITRIFORMES", "Pandionidae", "", "Pandion haliaetus", "NR", ""],      # listed again, not recognised
]


def _write_checklist(path, rows=CHECKLIST):
    """A minimal .xlsx: shared strings and one sheet, with a title row above the headings as BirdLife's has."""
    strings = sorted({cell for row in rows for cell in row if cell} | {"Citation: HBW and BirdLife International"})

    def row_xml(n, cells):
        refs = "".join(f'<c r="{chr(65 + i)}{n}" t="s"><v>{strings.index(v)}</v></c>'
                       for i, v in enumerate(cells) if v)
        return f'<row r="{n}">{refs}</row>'

    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = (f"<worksheet {ns}><sheetData>" + row_xml(1, ["", "Citation: HBW and BirdLife International"])
             + "".join(row_xml(n + 2, r) for n, r in enumerate(rows)) + "</sheetData></worksheet>")
    shared = f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


@pytest.fixture
def birds(tmp_path):
    folder = tmp_path / "birds"
    folder.mkdir()
    _write_geopackage(str(folder / "BOTW_2025.gpkg"))
    _write_checklist(str(folder / "HBW_BirdLife_Checklist_v10.xlsx"))
    return str(folder)


class TestBirds:
    def test_geometry_blob(self):
        square = shapely.box(0, 0, 1, 1)
        assert iucn.parse_geopackage_geometry(_gpkg_blob(square)).equals(square)
        with_envelope = b"GP\x00\x03" + (4326).to_bytes(4, "little") + bytes(32) + shapely.to_wkb(square)
        assert iucn.parse_geopackage_geometry(with_envelope).equals(square)
        assert iucn.parse_geopackage_geometry(b"GP\x00\x11" + bytes(4)) is None      # flagged empty
        assert iucn.parse_geopackage_geometry(None) is None

    def test_checklist(self, tmp_path):
        rows = iucn.read_checklist(_write_checklist(str(tmp_path / "c.xlsx")))
        assert rows[0] == {"order_": "ACCIPITRIFORMES", "family": "Accipitridae", "common": "White-tailed Sea-eagle",
                           "sci_name": "Haliaeetus albicilla", "category": "LC",
                           "aliases": "Grey Sea Eagle, Pigargo Europeo"}
        assert len(rows) == 5

    def test_checklist_unpacked_from_its_zip(self, tmp_path):
        folder = tmp_path / "f"
        folder.mkdir()
        sheet = _write_checklist(str(tmp_path / "Checklist_v10.xlsx"))
        with zipfile.ZipFile(folder / "species.zip", "w") as z:
            z.write(sheet, "Checklist_v10.xlsx")
        found = iucn.find_checklist(str(folder))
        assert found == str(folder / "Checklist_v10.xlsx") and os.path.isfile(found)

    def test_species_with_its_category_and_credit(self, birds, tmp_path, no_gbif):
        found = iucn.load_range("Haliaeetus albicilla", birds, str(tmp_path / "cache"))
        assert found.kinds == [iucn.PASSAGE, iucn.NONBREEDING, iucn.RESIDENT] and found.publisher == iucn.BIRDLIFE
        assert iucn.range_label(found) == "Haliaeetus albicilla, Least Concern"
        assert found.credit == ("Range: BirdLife International and Handbook of the Birds of the World (2025), "
                                "Bird species distribution maps of the world, version 2025.2")

    @pytest.mark.parametrize("name, label", [
        ("white-tailed  sea-eagle", "Haliaeetus albicilla (white-tailed sea-eagle), Least Concern"),
        ("Osprey", "Pandion haliaetus (osprey), Least Concern"),
        ("Haliaeetus", "Haliaeetus (genus, 2 species)"),
        ("accipitridae", "Accipitridae (family, 2 species)"),
        ("Accipitriformes", "Accipitriformes (order, 3 species)"),
    ])
    def test_names_from_the_checklist(self, birds, tmp_path, no_gbif, name, label):
        assert iucn.range_label(iucn.load_range(name, birds, str(tmp_path / "cache"))) == label

    @pytest.mark.parametrize("versions, newest", [
        (["2017.2", "2025.2", "unpublished", "2025.10", None], "2025.10"),
        (["unpublished"], ""),
    ])
    def test_dataset_version(self, versions, newest):
        assert iucn.dataset_version(versions) == newest

    def test_names_indexed_once(self, birds, tmp_path, monkeypatch, no_gbif):
        cache = str(tmp_path / "cache")
        iucn.load_range("Pandion haliaetus", birds, cache)
        monkeypatch.setattr(iucn, "_geopackage_records", lambda *a: pytest.fail("indexed again"))
        assert iucn.load_range("Bald Eagle", birds, cache).name == "Haliaeetus leucocephalus"

    def test_without_checklist(self, birds, tmp_path, no_gbif):
        os.remove(os.path.join(birds, "HBW_BirdLife_Checklist_v10.xlsx"))
        found = iucn.load_range("Haliaeetus", birds, str(tmp_path / "cache"))
        assert found.species == 2 and found.category is None
        with pytest.raises(ValueError, match="no species, genus or family called 'osprey'"):
            iucn.load_range("osprey", birds, str(tmp_path / "cache"))

    def test_beside_iucn_files(self, birds, tmp_path, no_gbif):
        _write_ranges(os.path.join(birds, "MAMMALS.zip"))
        assert iucn.load_range("Panthera leo", birds, str(tmp_path / "cache")).publisher == iucn.IUCN
        assert iucn.load_range("Pandion haliaetus", birds, str(tmp_path / "cache")).publisher == iucn.BIRDLIFE

    def test_zipped_geopackage_named(self, tmp_path, caplog):
        folder = tmp_path / "z"
        folder.mkdir()
        gpkg = _write_geopackage(str(tmp_path / "BOTW_2025.gpkg"))
        with zipfile.ZipFile(folder / "species.zip", "w") as z:
            z.write(gpkg, "BOTW_2025.gpkg")
        with pytest.raises(ValueError, match="No IUCN range files"):
            iucn.find_sources(str(folder))
        assert "Unzip BOTW_2025.gpkg from species.zip" in caplog.text
