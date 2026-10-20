"""Tests for industry.py (no network; Climate TRACE answers written here)."""

import json
import os
import urllib.parse

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import PathCollection
from matplotlib.figure import Figure

import industry as ind
from rotation import globe_projection


def _globe(lat=0, lon=0, inches=15):
    ax = Figure(figsize=(inches, inches)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _asset(name, lon, lat, tonnes, kind="Deep Conversion"):
    return {"Id": 1, "Name": name, "AssetType": kind, "Centroid": {"Geometry": [lon, lat], "SRID": 4326},
            "EmissionsSummary": [{"Gas": "co2e_100yr", "EmissionsQuantity": tonnes},
                                 {"Gas": "co2", "EmissionsQuantity": 999.0}]}


@pytest.fixture
def climate_trace(monkeypatch):
    """Serve Climate TRACE pages of assets by subsector (``answers[subsector] = [asset, ...]``)."""
    asked = []
    answers = {}

    def fake(url, timeout=120):
        asked.append(url)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        everything = answers.get(query["subsectors"], [])
        start = int(query["offset"])
        return {"bbox": [], "assets": everything[start:start + int(query["limit"])]}

    monkeypatch.setattr(ind, "_get_json", fake)
    monkeypatch.setattr(ind, "PAUSE", 0)
    return asked, answers


@pytest.mark.parametrize("text, sectors", [
    ("petrochemical", ("refineries", "petrochemicals", "chemicals")),
    ("Steel, refinery , Aluminum", ("refineries", "steel", "aluminium")),
    ("coal mines,power", ("coal-mines", "power")),
    ("mining", ("coal-mines", "copper-mines", "iron-mines", "bauxite-mines")),
])
def test_sectors(text, sectors):
    assert ind.resolve_sectors(text) == sectors


def test_all_leaves_out_basins():
    assert "oil-gas" not in ind.resolve_sectors("all") and len(ind.resolve_sectors("all")) == len(ind.SECTORS) - 1
    assert ind.resolve_sectors("all, oil-gas")[-1] == "power" and "oil-gas" in ind.resolve_sectors("all,oil-gas")


@pytest.mark.parametrize("text, message", [("smelting", "Unknown industry 'smelting'"), (" , ", "Name the industries")])
def test_bad_sectors(text, message):
    with pytest.raises(ValueError, match=message):
        ind.resolve_sectors(text)


@pytest.mark.parametrize("name, short", [
    ("Zhejiang Petroleum & Chemical, Zhejiang, Zhoushan", "Zhejiang Petroleum & Chemical"),
    ("RPL (Reliance Petroleum Limited) Jamnagar Refinery", "RPL Jamnagar Refinery"),
    ("CHN-Yinchuan_methanol", "Yinchuan methanol"),
    ("Russia_Central Sub-basin - West Siberia_Conventional onshore", "Central Sub-basin - West Siberia"),
    ("Russia_OtherBasins_OtherResources", "Russia, other basins"),
    ("ThyssenKrupp Steel Duisburg steel plant and more", "ThyssenKrupp Steel Duisburg…"),
    ("", ""),
])
def test_short_names(name, short):
    assert ind._short_name(name) == short


class TestData:
    def test_every_page_read_once(self, climate_trace, tmp_path, monkeypatch):
        asked, answers = climate_trace
        monkeypatch.setattr(ind, "PAGE", 2)
        answers["oil-and-gas-refining"] = [_asset(f"R{i}", i, 10, 1e6 * i) for i in range(5)] + \
            [{"Name": "No place", "Centroid": None}]
        found = ind.load_plants("refineries", str(tmp_path))
        assert found.names == ["R0", "R1", "R2", "R3", "R4"] and found.kinds[0] == "Deep Conversion"
        assert found.emissions.tolist() == [0, 1e6, 2e6, 3e6, 4e6] and found.lon.tolist() == [0, 1, 2, 3, 4]
        assert len(asked) == 4 and all("year=" in u for u in asked)          # the last page comes back empty
        ind.load_plants("refineries", str(tmp_path))
        assert len(asked) == 4                                              # from the cache

    def test_other_basins_marked(self, climate_trace, tmp_path):
        _, answers = climate_trace
        answers["oil-and-gas-production"] = [_asset("Russia_Central Sub-basin_Conventional onshore", 70, 60, 2e8),
                                             _asset("Russia_OtherBasins_OtherResources", 82, 57, 1.5e8)]
        found = ind.load_plants("oil-gas", str(tmp_path))
        assert len(found.names) == 2 and found.remainder.tolist() == [False, True]

    def test_unreachable(self, tmp_path, monkeypatch):
        def offline(url, timeout=120):
            raise ind.IndustryDataError("Could not reach Climate TRACE: no network")

        monkeypatch.setattr(ind, "_get_json", offline)
        with pytest.raises(ind.IndustryDataError, match="no network"):
            ind.load_plants("steel", str(tmp_path))

    def test_strange_answer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ind, "_get_json", lambda url, timeout=120: {"detail": "busy"})
        with pytest.raises(ind.IndustryDataError, match="sent no plants for iron-and-steel"):
            ind.load_plants("steel", str(tmp_path))

    def test_stale_kept_when_offline(self, climate_trace, tmp_path, monkeypatch, caplog):
        _, answers = climate_trace
        answers["cement"] = [_asset("C1", 1, 1, 1e6)]
        ind.load_plants("cement", str(tmp_path))
        path = os.path.join(tmp_path, os.listdir(tmp_path)[0])
        os.utime(path, (0, 0))

        def offline(url, timeout=120):
            raise ind.IndustryDataError("no network")

        monkeypatch.setattr(ind, "_get_json", offline)
        assert ind.load_plants("cement", str(tmp_path)).names == ["C1"]
        assert "Using the Climate TRACE plants of" in caplog.text
        with open(path, encoding="utf-8") as f:
            assert json.load(f)["rows"] == [["C1", "Deep Conversion", 1.0, 1.0, 1e6]]


def _plants(sector, rows, year=2025):
    return ind.Plants(sector, [r[0] for r in rows], ["x"] * len(rows), np.array([r[1] for r in rows], float),
                      np.array([r[2] for r in rows], float), np.array([r[3] for r in rows], float), year, "2026-10-20")


class TestDrawing:
    def test_sizes(self):
        sizes = ind.circle_size(np.array([0, 1e6, 10e6]))
        assert sizes[0] == ind.MIN_POINTS2 and sizes[2] == pytest.approx(10 * sizes[1])

    def test_scale_follows_the_globe(self):
        assert ind._scale(_globe(inches=15)) == pytest.approx(15 * 0.775 / 15, rel=0.01)

    def test_drawn_named_and_keyed(self):
        ax = _globe(30, 60)
        refineries = _plants("refineries", [("RPL (Reliance) Jamnagar Refinery", 70, 22, 19e6),
                                            ("Small refinery", 50, 26, 0.2e6), ("Far away", -120, 0, 5e6)])
        steel = _plants("steel", [("POSCO Gwangyang steel plant", 95, 35, 36e6)])
        counts = ind.draw_industry(ax, [refineries, steel])
        assert counts == {"refineries": 2, "steel": 1}
        (points,) = [c for c in ax.collections if isinstance(c, PathCollection)]
        assert len(points.get_offsets()) == 3
        assert points.get_sizes()[0] == max(points.get_sizes())               # biggest first, underneath
        assert {t.get_text() for t in ax.texts} >= {"RPL Jamnagar Refinery", "POSCO Gwangyang steel plant"}
        legend = ind.add_industry_legend(ax, [refineries, steel], counts)
        texts = [t.get_text() for t in legend.get_texts()]
        assert texts[:2] == ["Oil refineries (2)", "Steelworks (1)"] and texts[-3:] == [
            "1 Mt CO₂e a year", "5 Mt CO₂e a year", "20 Mt CO₂e a year"]
        assert legend.get_title().get_text() == ("Industry in view, circles sized by greenhouse-gas emissions in "
                                                 "2025 (Climate TRACE)")
        assert ind.industry_attribution([refineries, steel]) == (
            "Industry: Climate TRACE (climatetrace.org), CC BY 4.0, retrieved 2026-10-20")

    def test_key_keeps_sizes_in_their_own_column(self):
        ax = _globe()
        plants = [_plants(s, []) for s in ind.resolve_sectors("all")]
        legend = ind.add_industry_legend(ax, plants, {})
        texts = [t.get_text() for t in legend.get_texts()]
        rows = len(texts) // legend._ncols
        assert legend._ncols == 4 and texts[-rows:][:3] == ["1 Mt CO₂e a year", "5 Mt CO₂e a year",
                                                             "20 Mt CO₂e a year"]

    def test_other_basins_as_rings(self):
        ax = _globe(55, 70)
        basins = _plants("oil-gas", [("Russia_Central Sub-basin_Conventional onshore", 70, 60, 2e8),
                                     ("Russia_OtherBasins_OtherResources", 100, 45, 1.5e8)])._replace(
            remainder=np.array([False, True]))
        counts = ind.draw_industry(ax, [basins])
        assert counts == {"oil-gas": 2, "oil-gas:other": 1}
        filled, rings = [c for c in ax.collections if isinstance(c, PathCollection)]
        assert len(filled.get_offsets()) == 1 and len(rings.get_offsets()) == 1
        assert rings.get_facecolors().size == 0 or rings.get_facecolors()[0][3] == 0
        assert "Russia, other basins" in {t.get_text() for t in ax.texts}
        texts = [t.get_text() for t in ind.add_industry_legend(ax, [basins], counts).get_texts()]
        assert texts[:2] == ["Oil and gas basins (each a whole basin) (2)", "Rest of a country's basins, at one point (1)"]

    def test_nothing_in_view(self):
        ax = _globe(0, 0)
        assert ind.draw_industry(ax, [_plants("cement", [("Far", 180, 0, 1e6)])]) == {"cement": 0}
        assert not ax.collections and not ax.texts
