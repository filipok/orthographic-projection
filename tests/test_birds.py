"""Tests for birds.py — the catalogue, reading and cleaning Movebank CSVs, caching, choosing and drawing (no network)."""

import datetime
import io
import json
import os
import zipfile

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

import birds as bd
from rotation import globe_projection

HEADER = ("event-id,visible,timestamp,location-long,location-lat,argos:lc,manually-marked-outlier,sensor-type,"
          "individual-taxon-canonical-name,tag-local-identifier,individual-local-identifier,study-name\n")


def _row(t, lon, lat, bird="b1", taxon="Ciconia ciconia", sensor="gps", visible="true", lc="", outlier=""):
    stamp = t.strftime("%Y-%m-%d %H:%M:%S.000")
    return f"1,{visible},{stamp},{lon},{lat},{lc},{outlier},{sensor},{taxon},tag-{bird},{bird},Storks\n"


def _flight(bird="b1", taxon="Ciconia ciconia", start=datetime.datetime(2020, 9, 1), days=4, sensor="gps",
            lon0=8.0, lat0=48.0):
    """A bird flying south-west, a fix every hour, 0.25 degrees a fix (well under the speed limit)."""
    return "".join(_row(start + datetime.timedelta(hours=h), lon0 - 0.02 * h, lat0 - 0.03 * h, bird, taxon, sensor)
                   for h in range(days * 24))


def _study(uuid="u1", names=("Ciconia ciconia",), licence="CC0 1.0", files=(("Storks.csv", 1000, "https://x/1"),)):
    return bd.Study(uuid, f"Data from: {uuid}", f"Someone A. 2024. Data from: {uuid}. Movebank Data Repository.",
                    f"10.5441/{uuid}", licence, tuple(names), tuple(files))


STORK = {"bird": True, "keys": [2481912, 2481911, 9318, 1108, 212], "name": "Ciconia ciconia", "common": "white stork"}
KITE = {"bird": True, "keys": [2480537, 2480534, 2877, 7191147, 212], "name": "Milvus migrans", "common": "Black Kite"}
FOX = {"bird": False, "keys": [5219243, 5219234, 9701, 732, 359], "name": "Vulpes vulpes", "common": "red fox"}


# --- options ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, names", [(None, ()), ("", ()), ("all", ()), (" ALL ", ()),
                                         ("white stork", ("white stork",)),
                                         ("white  stork, Milvus migrans,, white stork", ("white stork", "Milvus migrans"))])
def test_parse_birds(text, names):
    assert bd.parse_birds(text) == names


def test_all_cannot_be_mixed_with_names():
    with pytest.raises(ValueError, match="birds must be 'all'"):
        bd.parse_birds("all, white stork")


@pytest.mark.parametrize("value", [0.5, 6000, float("nan"), float("inf")])
def test_bad_max_mb(value):
    with pytest.raises(ValueError, match="bird_max_mb must be between"):
        bd.check_max_mb(value)


# --- the catalogue -------------------------------------------------------------------------------------------

def _item(uuid="u1", rights="CC0 1.0 Universal", names=("Ciconia ciconia",), cse=True):
    metadata = {"dc.title": [{"value": "Data from: Storks"}], "dc.rights": [{"value": rights}],
                "dc.identifier.doi": [{"value": "doi:10.5441/001/1.abc"}],
                "dc.contributor.author": [{"value": "Fiedler, Wolfgang"}, {"value": "Wikelski, Martin"}],
                "dc.date.issued": [{"value": "2024-01-17"}],
                "dwc.ScientificName": [{"value": n} for n in names]}
    if cse:
        metadata["mdr.citation.CSE"] = [{"value": 'Fiedler W. 2024. Data from: Storks. Movebank Data Repository. '
                                                  '<a href="https://doi.org/10.5441/001/1.abc">https://doi.org/10.5441/001/1.abc</a>'}]
    return {"uuid": uuid, "metadata": metadata}


def test_read_package_keeps_open_licences_and_cleans_the_citation():
    study = bd.read_package(_item(names=("Ciconia ciconia", "Ciconia ciconia", "Milvus migrans")))
    assert study is not None
    assert (study.uuid, study.doi, study.licence) == ("u1", "10.5441/001/1.abc", "CC0 1.0")
    assert study.names == ("Ciconia ciconia", "Milvus migrans")
    assert study.citation == ("Fiedler W. 2024. Data from: Storks. Movebank Data Repository. "
                              "https://doi.org/10.5441/001/1.abc")
    assert bd.read_package(_item(rights="Attribution 4.0 International")).licence == "CC BY 4.0"
    assert bd.read_package(_item(rights="Attribution-NonCommercial 4.0 International")) is None


def test_read_package_builds_a_citation_without_one():
    study = bd.read_package(_item(cse=False))
    assert study.citation == ("Fiedler, Wolfgang, Wikelski, Martin. 2024. Data from: Storks. Movebank Data "
                              "Repository. https://doi.org/10.5441/001/1.abc")


def test_location_files_leave_out_the_other_tables():
    def bit(name, size=10):
        return {"name": name, "sizeBytes": size, "_links": {"content": {"href": f"https://x/{name}"}}}
    files = bd.location_files([bit("Storks-gps.csv", 5), bit("Storks.csv.zip", 7), bit("Storks-reference-data.csv"),
                               bit("Storks-acceleration.csv"), bit("Storks-light-level.csv"), bit("README.txt"),
                               bit("code.R"), bit("Storks-accessory-measurements.csv")])
    assert files == (("Storks-gps.csv", 5, "https://x/Storks-gps.csv"),
                     ("Storks.csv.zip", 7, "https://x/Storks.csv.zip"))


def test_classify(monkeypatch):
    answers = {
        "species/match": {"matchType": "EXACT", "usageKey": 2481912, "speciesKey": 2481912, "genusKey": 2481911,
                          "familyKey": 9318, "orderKey": 1108, "classKey": 212, "class": "Aves",
                          "species": "Ciconia ciconia"},
        "species/2481912/vernacularNames": {"results": [
            {"vernacularName": "European White Stork", "language": "eng", "source": "Catalogue of Life"},
            {"vernacularName": "Weißstorch", "language": "deu", "source": "IOC World Bird List, v14"},
            {"vernacularName": "White Stork", "language": "eng", "source": "IOC World Bird List, v14"}]},
    }
    monkeypatch.setattr(bd, "_get_json", lambda url, **kw: next(v for k, v in answers.items() if k in url))
    assert bd.classify("Ciconia ciconia") == {"bird": True, "keys": [212, 1108, 9318, 2481911, 2481912],
                                              "name": "Ciconia ciconia", "common": "White Stork"}
    monkeypatch.setattr(bd, "_get_json", lambda url, **kw: {"matchType": "NONE"})
    assert bd.classify("Nonsense") == {"bird": False, "keys": [], "name": "Nonsense", "common": None}

    def offline(url, **kw):
        raise bd.BirdDataError("offline")
    monkeypatch.setattr(bd, "_get_json", offline)
    assert bd.classify("Ciconia ciconia") is None


@pytest.mark.parametrize("names, english", [
    ([], None),
    ([{"vernacularName": "Storch", "language": "deu"}], None),
    ([{"vernacularName": "King/Common Eider", "language": "eng"}, {"vernacularName": "king eider", "language": "eng"},
      {"vernacularName": "King Eider", "language": "eng"}, {"vernacularName": "King Eider", "language": "eng"}],
     "King Eider"),
])
def test_english_name_without_the_ioc_list(names, english):
    assert bd.english_name(names) == english


def test_fetch_catalogue_keeps_the_open_bird_studies_with_files(monkeypatch):
    items = [_item("stork"), _item("fox", names=("Vulpes vulpes",)), _item("nc", rights="Attribution-NonCommercial 4.0 "
                                                                                       "International"),
             _item("nofiles")]
    page = {"_embedded": {"searchResult": {"page": {"totalPages": 1},
                                           "_embedded": {"objects": [{"_embedded": {"indexableObject": i}}
                                                                     for i in items]}}}}

    def bundles(uuid):
        listed = [] if uuid == "nofiles" else [
            {"name": "Storks.csv", "sizeBytes": 123, "_links": {"content": {"href": f"https://x/{uuid}.csv"}}},
            {"name": "Storks-reference-data.csv", "sizeBytes": 1, "_links": {"content": {"href": "https://x/r"}}}]
        return {"_embedded": {"bundles": [
            {"name": "ORIGINAL", "_embedded": {"bitstreams": {"page": {"totalElements": len(listed)},
                                                              "_embedded": {"bitstreams": listed}}}},
            {"name": "LICENSE", "_embedded": {"bitstreams": {"_embedded": {"bitstreams": listed}}}}]}}

    def get(url, **kw):
        if "discover/search" in url:
            assert "f.entityType=Datapackage%2Cequals" in url
            return page
        return bundles(url.split("/items/")[1].split("/")[0])
    monkeypatch.setattr(bd, "_get_json", get)
    monkeypatch.setattr(bd, "classify", lambda name: {"Ciconia ciconia": STORK, "Vulpes vulpes": FOX}[name])
    catalogue = bd.fetch_catalogue()
    assert [s.uuid for s in catalogue.studies] == ["stork"]
    assert catalogue.studies[0].files == (("Storks.csv", 123, "https://x/stork.csv"),)
    assert set(catalogue.taxa) == {"Ciconia ciconia", "Vulpes vulpes"}
    assert catalogue.built == datetime.date.today().isoformat()


def test_catalogue_is_cached_refreshed_and_kept_when_offline(tmp_path):
    fresh = bd.Catalogue((_study(),), {"Ciconia ciconia": STORK}, datetime.date.today().isoformat())
    calls = []

    def fetch(taxa):
        calls.append(taxa)
        return fresh
    assert bd.load_catalogue(str(tmp_path), fetch=fetch) == fresh
    assert bd.load_catalogue(str(tmp_path), fetch=fetch) == fresh and len(calls) == 1      # cached
    old = fresh._replace(built="2000-01-01")
    bd._write_catalogue(old, bd._catalogue_path(str(tmp_path)))
    assert bd.load_catalogue(str(tmp_path), fetch=fetch) == fresh and calls[-1] == {"Ciconia ciconia": STORK}

    def offline(taxa):
        raise bd.BirdDataError("offline")
    bd._write_catalogue(old, bd._catalogue_path(str(tmp_path)))
    assert bd.load_catalogue(str(tmp_path), fetch=offline) == old                         # stale beats nothing
    with pytest.raises(bd.BirdDataError, match="Could not list the bird studies"):
        bd.load_catalogue(str(tmp_path / "empty"), fetch=offline)


# --- reading and cleaning ------------------------------------------------------------------------------------------

def test_clean_track_thins_sorts_and_drops_spikes_and_jumps():
    t = np.arange(0, 48 * 3600, 3600, dtype=np.int64)[::-1]           # hourly, newest first
    lon = np.linspace(10, 9, len(t))
    lat = np.linspace(50, 49, len(t))
    keep = bd.clean_track(t, lon, lat)
    assert len(keep) == 8 and np.all(np.diff(t[keep]) == 6 * 3600)      # one fix every six hours, oldest first
    lon[keep[3]], lat[keep[3]] = 30.0, 60.0                             # a spike far from both neighbours
    assert keep[3] not in bd.clean_track(t, lon, lat)
    t5 = np.arange(5, dtype=np.int64) * 48 * 3600                       # two fixes a mirror's width away
    pair = bd.clean_track(t5, np.array([7.0, -60.0, -55.0, 7.5, 8.0]), np.array([53.0, 66.0, 65.0, 53.0, 52.0]))
    assert list(pair) == [0, 3, 4]
    t2 = np.array([0, 6, 12, 18], np.int64) * 3600
    jump = bd.clean_track(t2, np.array([0.0, 0.1, 40.0, 40.1]), np.array([10.0, 10.1, 10.0, 10.1]))
    assert list(jump) == [0, 1]                                         # 4,400 km in six hours: dropped


def test_read_locations_by_bird_taxon_and_sensor():
    start = datetime.datetime(2020, 9, 1)
    text = (HEADER + _flight("b1") + _flight("b2", taxon="Milvus migrans")
            + _flight("b1", sensor="solar-geolocator", start=datetime.datetime(2021, 1, 1))
            + _row(start, 5, 45, visible="false") + _row(start, 5, 45, outlier="true") + _row(start, 0, 0)
            + _row(start, 5, 45, lc="Z") + _row(start, 5, 45, sensor="bird-ring") + _row(start, "", 45)
            + _row(start, 500, 45) + "1,true,,5,45,,,gps,Ciconia ciconia,t,b9,S\n")
    tracks = bd.read_locations(io.StringIO(text, newline=""))
    assert set(tracks) == {("Ciconia ciconia", "b1", False), ("Milvus migrans", "b2", False),
                           ("Ciconia ciconia", "b1", True)}
    one = tracks[("Ciconia ciconia", "b1", False)]
    assert one.shape == (16, 3) and one[0, 0] == datetime.datetime(2020, 9, 1, tzinfo=datetime.UTC).timestamp()
    assert one[0, 1] == 8.0 and one[0, 2] == 48.0


def test_read_locations_takes_the_study_taxon_and_ignores_other_tables():
    text = "timestamp,location-long,location-lat,individual-local-identifier\n" + "".join(
        f"2020-01-0{d} 00:00:00,{d},{d},x\n" for d in range(1, 4))
    assert set(bd.read_locations(io.StringIO(text), "Ciconia nigra")) == {("Ciconia nigra", "x", False)}
    assert bd.read_locations(io.StringIO("timestamp,acceleration-raw-x\n2020-01-01,3\n")) == {}
    assert bd.read_locations(io.StringIO("")) == {}


def _downloader(files):
    calls = []

    def download(url, dest, label):
        calls.append(url)
        data = files[url]
        if dest.endswith(".zip"):
            with zipfile.ZipFile(dest, "w") as archive:
                archive.writestr("inner/Storks.csv", data)
        else:
            with open(dest, "w", encoding="utf-8", newline="") as f:
                f.write(data)
    return download, calls


def test_ensure_file_caches_csv_and_zip(tmp_path):
    study = _study(files=(("Storks.csv", 10, "https://x/a"), ("Kites.csv.zip", 10, "https://x/b")))
    download, calls = _downloader({"https://x/a": HEADER + _flight("b1"),
                                   "https://x/b": HEADER + _flight("k", taxon="Milvus migrans")})
    path = bd.ensure_file(study, study.files[0], str(tmp_path), download)
    assert bd.ensure_file(study, study.files[0], str(tmp_path), download) == path and calls == ["https://x/a"]
    with np.load(path) as data:
        assert list(data["taxon"]) == ["Ciconia ciconia"] and list(data["lengths"]) == [16]
        assert data["lon"].dtype == np.float32 and not data["geolocator"].any()
    zipped = bd.ensure_file(study, study.files[1], str(tmp_path), download)
    with np.load(zipped) as data:
        assert list(data["taxon"]) == ["Milvus migrans"]
    assert sorted(os.listdir(tmp_path / "tracks")) == sorted([os.path.basename(path), os.path.basename(zipped)])


def test_ensure_file_wraps_unreadable_files(tmp_path):
    study = _study(files=(("Storks.csv.zip", 10, "https://x/a"),))

    def broken(url, dest, label):
        with open(dest, "w") as f:
            f.write("not a zip")
    with pytest.raises(bd.BirdDataError, match="Could not read 'Storks.csv.zip'"):
        bd.ensure_file(study, study.files[0], str(tmp_path), broken)
    assert not os.listdir(tmp_path / "tracks")


# --- choosing and loading ------------------------------------------------------------------------------------------

def _catalogue():
    studies = (_study("storks", files=(("Storks.csv", 2_000_000, "https://x/storks"),
                                       ("Storks-big.csv", 90_000_000, "https://x/big"))),
               _study("kites", names=("Milvus migrans",), licence="CC BY 4.0",
                      files=(("Kites.csv", 1_000, "https://x/kites"),)),
               _study("mixed", names=("Ciconia ciconia", "Vulpes vulpes"), files=(("Mixed.csv", 1, "https://x/mix"),)))
    return bd.Catalogue(studies, {"Ciconia ciconia": STORK, "Milvus migrans": KITE, "Vulpes vulpes": FOX},
                        "2026-11-01")


def test_select_files_by_taxon_and_size():
    files, skipped, skipped_bytes = bd.select_files(_catalogue())
    assert [f[2] for _, f in files] == ["https://x/storks", "https://x/kites", "https://x/mix"]
    assert (skipped, skipped_bytes) == (1, 90_000_000)
    assert [f[2] for _, f in bd.select_files(_catalogue(), [2480537])[0]] == ["https://x/kites"]      # Milvus migrans
    assert len(bd.select_files(_catalogue(), [9318], max_mb=100)[0]) == 3                              # Ciconiidae
    assert bd.select_files(_catalogue(), [5219243])[0] == []                                           # red fox


def _loaded(tmp_path, keys=(), geolocators=False, fail=()):
    download, _ = _downloader({
        "https://x/storks": HEADER + _flight("s1") + _flight("s2", lon0=20, lat0=40)
                            + _flight("s1", sensor="solar-geolocator", start=datetime.datetime(2021, 3, 1)),
        "https://x/kites": HEADER + _flight("k1", taxon="Milvus migrans", lon0=-5, lat0=40),
        "https://x/mix": HEADER + _flight("m1") + _flight("f1", taxon="Vulpes vulpes"),
    })

    def fetch_file(study, file, cache_dir, download=download):
        if file[2] in fail:
            raise bd.BirdDataError("server down")
        return original(study, file, cache_dir, download)
    original = bd.ensure_file
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bd, "ensure_file", fetch_file)
        mp.setattr(bd, "_classify_all", lambda names, taxa: None)
        return bd.load_birds(keys, geolocators=geolocators, cache_dir=str(tmp_path), catalogue=_catalogue())


def test_load_birds_assembles_the_birds_of_the_chosen_taxa(tmp_path):
    flock = _loaded(tmp_path)
    assert flock.names == ("Ciconia ciconia", "Milvus migrans") and flock.common == ("white stork", "Black Kite")
    assert [s.uuid for s in flock.studies] == ["storks", "kites", "mixed"]          # the fox left out
    assert len(flock.species) == 4 and not flock.geolocator.any()
    assert list(np.diff(flock.starts)) == [16, 16, 16, 16] and len(flock.lon) == 64
    assert len(_loaded(tmp_path, geolocators=True).species) == 5
    kites = _loaded(tmp_path, keys=[2480537])
    assert kites.names == ("Milvus migrans",) and len(kites.species) == 1


def test_load_birds_leaves_out_failed_files_or_fails_when_all_do(tmp_path, caplog):
    flock = _loaded(tmp_path, fail=("https://x/kites",))
    assert "Milvus migrans" not in flock.names and "1 of 3 files could not be had" in caplog.text
    with pytest.raises(bd.BirdDataError, match="No bird track could be downloaded"):
        _loaded(tmp_path / "other", fail=("https://x/storks", "https://x/kites", "https://x/mix"))


# --- drawing -----------------------------------------------------------------------------------------------------

def _axes(lat=45, lon=10):
    ax = Figure(figsize=(6, 6), dpi=100).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def test_draw_birds_colours_species_by_birds_in_sight(tmp_path):
    flock = _loaded(tmp_path)
    ax = _axes()
    drawn = bd.draw_birds([ax], flock)
    assert drawn.seen.all() and drawn.colours == {0: bd.PALETTE[0], 1: bd.PALETTE[1]}     # 3 storks, 1 kite
    lines = [c for c in ax.collections if isinstance(c, LineCollection)]
    assert sum(len(c.get_segments()) for c in lines) == 4 * 15
    assert bd.summary(flock, drawn) == (4, 2, 3)
    far = bd.draw_birds([_axes(-45, -170)], flock)                                         # the other side
    assert not far.seen.any() and far.colours == {}


def test_draw_birds_greys_the_species_past_the_key(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "MAX_KEYED_SPECIES", 1)
    flock = _loaded(tmp_path)
    ax = _axes()
    drawn = bd.draw_birds([ax], flock)
    assert drawn.colours == {0: bd.PALETTE[0]}
    colours = {tuple(np.round(c.get_colors()[0][:3], 3)) for c in ax.collections}
    assert len(colours) == 2


def test_track_breaks_at_long_gaps(tmp_path, monkeypatch):
    text = HEADER + _flight("s1", days=1) + _flight("s1", days=1, start=datetime.datetime(2020, 10, 1))
    download, _ = _downloader({"https://x/a": text})
    original = bd.ensure_file
    monkeypatch.setattr(bd, "ensure_file", lambda study, file, cache_dir: original(study, file, cache_dir, download))
    catalogue = bd.Catalogue((_study(files=(("S.csv", 1, "https://x/a"),)),), {"Ciconia ciconia": STORK},
                             "2026-11-01")
    flock = bd.load_birds(cache_dir=str(tmp_path), catalogue=catalogue)
    assert list(np.diff(flock.starts)) == [8]
    ax = _axes()
    bd.draw_birds([ax], flock)
    assert sum(len(c.get_segments()) for c in ax.collections) == 3 + 3         # four fixes a day, no step between


def test_legend_credit_and_sources(tmp_path):
    flock = _loaded(tmp_path)
    ax = _axes()
    drawn = bd.draw_birds([ax], flock)
    legend = bd.add_birds_legend(ax, flock, drawn)
    assert legend is not None
    assert [t.get_text() for t in legend.get_texts()] == ["White stork (3)", "Black Kite (1)"]
    assert legend.get_title().get_text().startswith("Tracks of 4 tagged birds of 2 species from 3 studies")
    sources = bd.sources_path(str(tmp_path / "map.png"))
    assert sources == str(tmp_path / "map_bird_studies.txt") == bd.sources_path(str(tmp_path / "map.tmp.png"))
    assert bd.birds_attribution(flock, drawn, sources) == ("Bird tracks: 3 studies in the Movebank Data Repository "
                                                           "(CC0 1.0 / CC BY 4.0), cited in map_bird_studies.txt")
    bd.write_sources(sources, flock, drawn)
    text = open(sources, encoding="utf-8").read()
    assert text.count("\n- ") == 3 and "Data from: storks. Movebank Data Repository. [CC0 1.0; 2 birds drawn]" in text
    assert "[CC BY 4.0; 1 bird drawn]" in text
    empty = bd.Drawn(np.zeros(len(flock.species), bool), {})
    assert bd.add_birds_legend(ax, flock, empty) is None


def test_legend_names_others_and_geolocators(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "MAX_KEYED_SPECIES", 1)
    flock = _loaded(tmp_path, geolocators=True)
    ax = _axes()
    legend = bd.add_birds_legend(ax, flock, bd.draw_birds([ax], flock))
    assert [t.get_text() for t in legend.get_texts()] == ["White stork (4)", "1 other species (1)"]
    assert "geolocator tracks, accurate to ~200 km, paler" in legend.get_title().get_text()


def test_catalogue_round_trips_through_json(tmp_path):
    catalogue = _catalogue()
    path = bd._catalogue_path(str(tmp_path))
    bd._write_catalogue(catalogue, path)
    assert bd._read_catalogue(path) == catalogue
    assert json.load(open(path, encoding="utf-8"))["version"] == bd.CATALOGUE_VERSION
    with open(path, "w") as f:
        f.write("{")
    assert bd._read_catalogue(path) is None


def test_download_tries_again_when_the_connection_drops(tmp_path, monkeypatch):
    calls = []

    def flaky(url, dest, label, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise ConnectionResetError(104, "Connection reset by peer")
        open(dest, "w").close()
    monkeypatch.setattr(bd, "_download_once", flaky)
    monkeypatch.setattr(bd.time, "sleep", lambda s: None)
    bd._download("https://x/a", str(tmp_path / "a.csv"), "a")
    assert len(calls) == 3 and (tmp_path / "a.csv").exists()
    calls.clear()
    monkeypatch.setattr(bd, "_download_once", lambda *a: (_ for _ in ()).throw(ConnectionResetError("reset")))
    with pytest.raises(bd.BirdDataError, match="Could not download 'a'"):
        bd._download("https://x/a", str(tmp_path / "b.csv"), "a")


def test_track_breaks_where_the_tag_fell_silent(tmp_path, monkeypatch):
    start = datetime.datetime(2020, 9, 1)
    rows = [_row(start + datetime.timedelta(hours=6 * i), 10, 50 - 0.5 * i) for i in range(4)]
    rows.append(_row(start + datetime.timedelta(days=8), 10, 30))          # 1,500 km in eight days: a silent tag
    rows.append(_row(start + datetime.timedelta(days=8, hours=6), 10, 29.5))
    rows.append(_row(start + datetime.timedelta(days=8, hours=18), 10, 23))  # 720 km in 12 hours: a real flight
    download, _ = _downloader({"https://x/a": HEADER + "".join(rows)})
    original = bd.ensure_file
    monkeypatch.setattr(bd, "ensure_file", lambda study, file, cache_dir: original(study, file, cache_dir, download))
    catalogue = bd.Catalogue((_study(files=(("S.csv", 1, "https://x/a"),)),), {"Ciconia ciconia": STORK},
                             "2026-11-01")
    flock = bd.load_birds(cache_dir=str(tmp_path), catalogue=catalogue)
    assert list(np.diff(flock.starts)) == [7]
    ax = _axes()
    bd.draw_birds([ax], flock)
    assert sum(len(c.get_segments()) for c in ax.collections) == 3 + 2
