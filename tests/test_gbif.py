"""Tests for gbif.py (no network)."""

import json
import urllib.parse

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure

import gbif
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _varint(n):
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def _field(number, value):
    """A protocol-buffer field: an int as a varint, bytes as length-delimited."""
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _zigzag(n):
    return (n << 1) ^ (n >> 63)


def _tile(cells, extent=512, signed=True):
    """A GBIF-style vector tile: one point per (column, row, records), tagged total and a year."""
    keys = [b"total", b"2010"]
    values, features = [], b""
    for col, row, count in cells:
        values += [_field(6, _zigzag(count)) if signed else _field(5, count), _field(5, 1)]
        tags = _varint(0) + _varint(len(values) - 2) + _varint(1) + _varint(len(values) - 1)
        geometry = _varint(9) + _varint(_zigzag(col)) + _varint(_zigzag(row))
        features += _field(2, _field(3, 1) + _field(2, tags) + _field(4, geometry))
    layer = _field(15, 2) + _field(1, b"occurrence") + features + b"".join(_field(3, k) for k in keys) \
        + b"".join(_field(4, v) for v in values) + _field(5, extent)
    return _field(3, layer)


class TestTiles:
    @pytest.mark.parametrize("signed", [True, False])
    def test_cells_and_counts(self, signed):
        cells = gbif.read_tile(_tile([(0, 0, 3), (511, 200, 1500), (-20, 5, 9), (530, 5, 9)], signed=signed))
        assert cells == [(0, 0, 3), (511, 200, 1500)]           # the buffer beyond the tile is left out

    def test_other_extent_scaled_to_cells(self):
        assert gbif.read_tile(_tile([(4095, 2048, 2)], extent=4096)) == [(511, 256, 2)]

    def test_empty_tile(self):
        assert gbif.read_tile(b"") == []

    def test_url_keeps_wild_records_of_the_years(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(gbif.tile_url(5219404, 3, 1, (1990, 2020))).query)
        assert query["taxonKey"] == ["5219404"] and query["srs"] == ["EPSG:4326"] and query["year"] == ["1990,2020"]
        assert "FOSSIL_SPECIMEN" not in query["basisOfRecord"] and "LIVING_SPECIMEN" not in query["basisOfRecord"]
        assert gbif.tile_url(1, 0, 0).split("?")[0].endswith("/density/1/0/0.mvt")


@pytest.mark.parametrize("text, years", [(None, None), ("", None), ("2015", (2015, 2015)),
                                         (" 1990 - 2020 ", (1990, 2020))])
def test_years(text, years):
    assert gbif.parse_years(text) == years


@pytest.mark.parametrize("text", ["2020-1990", "1500-1600", "3000", "last year", "1-2-3"])
def test_bad_years(text):
    with pytest.raises(ValueError, match="Species years"):
        gbif.parse_years(text)


def _answers(monkeypatch, replies):
    """Serve GBIF API answers by path; records the URLs asked for."""
    asked = []

    def fake(url, timeout=60):
        asked.append(url)
        path = urllib.parse.urlparse(url).path
        for start, reply in replies.items():
            if path.startswith(start):
                return json.dumps(reply(url) if callable(reply) else reply).encode()
        raise AssertionError(url)

    monkeypatch.setattr(gbif, "_get", fake)
    return asked


LION = {"usageKey": 5219404, "canonicalName": "Panthera leo", "rank": "SPECIES", "matchType": "EXACT",
        "confidence": 99}


class TestNames:
    def test_scientific_name(self, tmp_path, monkeypatch):
        asked = _answers(monkeypatch, {"/v1/species/match": LION,
                                       "/v1/species/5219404": {"vernacularName": "lion"}})
        assert gbif.resolve_taxon("Panthera  leo", str(tmp_path)) == gbif.Taxon(5219404, "Panthera leo", "SPECIES",
                                                                                "lion")
        assert gbif.resolve_taxon("panthera leo", str(tmp_path)).key == 5219404
        assert len(asked) == 2                                     # the second time from the cache

    def test_synonym_named_as_accepted(self, tmp_path, monkeypatch):
        _answers(monkeypatch, {"/v1/species/match": {**LION, "canonicalName": "Felis leo", "acceptedUsageKey": 5219404,
                                                     "usageKey": 1},
                               "/v1/species/5219404": {"canonicalName": "Panthera leo", "rank": "SPECIES"}})
        assert gbif.resolve_taxon("Felis leo", str(tmp_path))[:3] == (5219404, "Panthera leo", "SPECIES")

    def test_common_name_most_often_given(self, tmp_path, monkeypatch):
        found = [
            {"key": 2435099, "canonicalName": "Puma concolor", "rank": "SPECIES",
             "vernacularNames": [{"vernacularName": "Lion"}, {"vernacularName": "Puma"}]},
            {"key": 5219404, "canonicalName": "Panthera leo", "rank": "SPECIES",
             "vernacularNames": [{"vernacularName": "lion"}, {"vernacularName": "Lion"}]},
            {"key": 7, "canonicalName": "Lionella", "rank": "GENUS", "vernacularNames": [{"vernacularName": "lions"}]},
        ]
        _answers(monkeypatch, {"/v1/species/match": {"matchType": "NONE"},
                               "/v1/species/search": {"results": found}})
        assert gbif.resolve_taxon("Lion", str(tmp_path)) == gbif.Taxon(5219404, "Panthera leo", "SPECIES", "lion")

    def test_tie_broken_by_records(self, tmp_path, monkeypatch):
        found = [{"key": k, "canonicalName": n, "rank": "SPECIES", "vernacularNames": [{"vernacularName": "tiger"}]}
                 for k, n in ((1, "Esox masquinongy"), (2, "Panthera tigris"))]
        _answers(monkeypatch, {"/v1/species/match": {"matchType": "NONE"},
                               "/v1/species/search": {"results": found},
                               "/v1/occurrence/search": lambda url: {"count": 9 if "taxonKey=2" in url else 1}})
        assert gbif.resolve_taxon("tiger", str(tmp_path)).name == "Panthera tigris"

    def test_unknown_name(self, tmp_path, monkeypatch):
        _answers(monkeypatch, {"/v1/species/match": {"matchType": "NONE"}, "/v1/species/search": {"results": []}})
        with pytest.raises(ValueError, match="no species called 'zzqx'"):
            gbif.resolve_taxon("zzqx", str(tmp_path))

    def test_offline(self, tmp_path, monkeypatch):
        def offline(url, timeout=60):
            raise OSError("no network")

        monkeypatch.setattr(gbif, "_get", offline)
        with pytest.raises(gbif.GbifDataError, match="Could not reach GBIF"):
            gbif.resolve_taxon("lion", str(tmp_path))

    @pytest.mark.parametrize("taxon, label", [
        (gbif.Taxon(1, "Panthera leo", "SPECIES", "lion"), "Panthera leo (lion)"),
        (gbif.Taxon(1, "Quercus", "GENUS", "oak"), "Quercus (oak, genus)"),
        (gbif.Taxon(1, "Felidae", "FAMILY", None), "Felidae (family)"),
        (gbif.Taxon(1, "Danaus plexippus", "SPECIES", None), "Danaus plexippus"),
    ])
    def test_labels(self, taxon, label):
        assert gbif.taxon_label(taxon) == label


TAXON = gbif.Taxon(5219404, "Panthera leo", "SPECIES", "lion")


class TestRecords:
    def test_read_once_with_cell_centres(self, tmp_path, monkeypatch):
        urls = []

        def fake(url, timeout=60):
            urls.append(url)
            return _tile([(0, 0, 4)]) if "/1/2/1.mvt" in url else b""   # one cell, just south-east of 0, 0

        monkeypatch.setattr(gbif, "_get", fake)
        found = gbif.load_occurrences(TAXON, (2000, 2020), str(tmp_path))
        assert len(urls) == 8 and all("year=2000%2C2020" in u for u in urls)
        half = gbif.CELL_DEGREES / 2
        assert found.lon.tolist() == pytest.approx([half]) and found.lat.tolist() == pytest.approx([-half])
        assert found.records.tolist() == [4] and found.years == (2000, 2020) and len(found.retrieved) == 10
        gbif.load_occurrences(TAXON, (2000, 2020), str(tmp_path))
        assert len(urls) == 8

    def test_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gbif, "_get", lambda url, timeout=60: b"\x0f")
        with pytest.raises(gbif.GbifDataError, match="Panthera leo"):
            gbif.load_occurrences(TAXON, cache_dir=str(tmp_path))

    def test_classes(self):
        assert gbif.count_classes(np.array([1, 2, 10, 11, 100, 101, 1000, 1001, 50000])).tolist() == \
            [0, 1, 1, 2, 2, 3, 3, 4, 4]

    def test_drawn_and_keyed(self):
        ax = _globe(0, 20)
        found = gbif.Occurrences(TAXON, np.array([20.0, 30.0, -120.0]), np.array([0.0, -5.0, 0.0]),
                                 np.array([5, 2000, 1]), None, "2026-10-18")
        gbif.draw_occurrences(ax, found)
        cells = [c for c in ax.collections if isinstance(c, PolyCollection)]
        assert len(cells) == 1 and len(cells[0].get_paths()) == 2          # the cell beyond the horizon is left out
        assert cells[0].get_linewidth()[0] > 0                            # widened to stay visible on a globe
        legend = gbif.add_occurrences_legend(ax, found)
        title = legend.get_title().get_text()
        assert "Panthera leo (lion)" in title and "2,006 worldwide in 3 cells" in title
        assert [t.get_text() for t in legend.get_texts()][-1] == "over 1,000"
        assert gbif.gbif_attribution(found).endswith("retrieved 2026-10-18")

    def test_nothing_in_sight(self):
        ax = _globe(0, 20)
        gbif.draw_occurrences(ax, gbif.Occurrences(TAXON, np.array([-160.0]), np.array([0.0]), np.array([3]), None,
                                                   "2026-10-18"))
        assert not ax.collections
