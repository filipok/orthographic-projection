"""Tests for ports.py (no network; a small PortWatch-style table written here)."""

import os
import time
import urllib.error

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import ports as pw
from rotation import globe_projection

HEADER = ("X,Y,portid,portname,country,ISO3,continent,fullname,lat,lon,vessel_count_total,vessel_count_container,"
          "vessel_count_dry_bulk,vessel_count_general_cargo,vessel_count_RoRo,vessel_count_tanker,industry_top1,"
          "industry_top2,industry_top3,share_country_maritime_import,share_country_maritime_export,LOCODE,pageid,"
          "countrynoaccents,ObjectId")


def _row(name, country, lat, lon, total, container=0, tanker=0, roro=0):
    return (f"0,0,port{name[:3]},{name},{country},XXX,Europe,\"{name}, {country}\",{lat},{lon},{total},{container},"
            f"0,0,{roro},{tanker},Mineral Products,,,0.1,0.1,XX XXX,abc,{country},1")


TABLE = "﻿" + "\n".join([
    HEADER,
    _row("Constantza", "Romania", 44.13, 28.65, 3440, container=900, tanker=546),
    _row("Midia", "Romania", 44.34, 28.66, 546, tanker=192),
    _row("Galati", "Romania", 45.44, 28.07, 73),
    _row("Rotterdam", "The Netherlands", 51.95, 4.14, 32584, container=7045, tanker=16990),
    _row("Qingdao Port", "China", 36.07, 120.31, 11381, container=7270),
    _row("Nowhere", "Romania", 45.0, 28.0, 0),
]) + "\n"


def _globe(lat: float = 45, lon: float = 28, inches: float = 15):
    ax = Figure(figsize=(inches, inches)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


@pytest.fixture
def portwatch(monkeypatch):
    """Serve :data:`TABLE` as PortWatch's download; the list holds one entry per download."""
    asked = []

    def fake(timeout=120):
        asked.append(1)
        return TABLE.lstrip("﻿")

    monkeypatch.setattr(pw, "_download", fake)
    return asked


@pytest.mark.parametrize("text, kind", [
    (None, "all"), ("", "all"), ("ports", "all"), ("Containers", "container"), ("tankers", "tanker"),
    ("oil", "tanker"), ("dry bulk", "dry-bulk"), ("bulk", "dry-bulk"), ("ro-ro", "roro"), ("ferries", "roro"),
    ("general_cargo", "general-cargo"),
])
def test_kinds(text, kind):
    assert pw.resolve_port_kind(text) == kind


def test_bad_kind():
    with pytest.raises(ValueError, match="Unknown kind of port traffic 'yachts'; choose from all, container"):
        pw.resolve_port_kind("yachts")


@pytest.mark.parametrize("name, short", [
    ("Qingdao Port", "Qingdao"), ("Rotterdam", "Rotterdam"), ("Yingkou Pt", "Yingkou"),
    ("Democratic Republic of the Congo - Offshore Oil Terminal 1", "Democratic Republic of the…"),
    ("Port", "Port"),
])
def test_short_names(name, short):
    assert pw.short_name(name) == short


class TestData:
    def test_read_once_busiest_first(self, portwatch, tmp_path):
        found = pw.load_ports("all", str(tmp_path))
        assert found.names == ["Rotterdam", "Qingdao Port", "Constantza", "Midia", "Galati"]   # no ships: left out
        assert found.ships.tolist() == [32584, 11381, 3440, 546, 73]
        assert found.lon[2] == pytest.approx(28.65) and found.lat[2] == pytest.approx(44.13)
        assert found.countries[0] == "The Netherlands"
        pw.load_ports("tanker", str(tmp_path))
        assert portwatch == [1]                                                 # cached

    def test_one_kind_of_ship(self, portwatch, tmp_path):
        found = pw.load_ports("tanker", str(tmp_path))
        assert found.names == ["Rotterdam", "Constantza", "Midia"] and found.kind == "tanker"

    def test_unreachable(self, tmp_path, monkeypatch):
        def offline(request, timeout=120, context=None):
            raise urllib.error.URLError("no network")

        monkeypatch.setattr(pw.urllib.request, "urlopen", offline)
        with pytest.raises(pw.PortsDataError, match="Could not reach IMF PortWatch"):
            pw.load_ports("all", str(tmp_path))

    def test_strange_answer(self, tmp_path, monkeypatch):
        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"<html>Service unavailable</html>"

        monkeypatch.setattr(pw.urllib.request, "urlopen", lambda request, timeout=120, context=None: Answer())
        with pytest.raises(pw.PortsDataError, match="something other than its ports table"):
            pw.load_ports("all", str(tmp_path))

    def test_stale_read_again_and_kept_when_offline(self, portwatch, tmp_path, monkeypatch, caplog):
        pw.load_ports("all", str(tmp_path))
        old = time.time() - (pw.REFRESH_DAYS + 1) * 86400
        os.utime(tmp_path / pw.FILE, (old, old))
        pw.load_ports("all", str(tmp_path))
        assert portwatch == [1, 1]                                              # a year on, read again

        def offline(timeout=120):
            raise pw.PortsDataError("Could not reach IMF PortWatch: no network")

        os.utime(tmp_path / pw.FILE, (old, old))
        monkeypatch.setattr(pw, "_download", offline)
        assert pw.load_ports("all", str(tmp_path)).names[0] == "Rotterdam"
        assert "Using the ports downloaded on" in caplog.text

    def test_empty_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pw, "_download", lambda timeout=120: HEADER + "\n")
        with pytest.raises(pw.PortsDataError, match="no ports in it"):
            pw.load_ports("all", str(tmp_path))


def _ports(kind="all"):
    return pw.Ports(kind, ["Rotterdam", "Constantza", "Midia", "Qingdao Port"], ["NL", "RO", "RO", "CN"],
                    np.array([4.14, 28.65, 28.66, 120.31]), np.array([51.95, 44.13, 44.34, 36.07]),
                    np.array([32584.0, 3440.0, 546.0, 11381.0]))


class TestDrawing:
    def test_sizes(self):
        assert pw.dot_size(np.array([20_000, 10])).tolist() == [200, pw.MIN_POINTS2]
        assert pw.dot_size(np.array([20_000]), 2.0)[0] == 800

    def test_far_side_left_out(self):
        ax = _globe(45, 28)
        assert pw.in_view(ax, np.array([28.65, 120.31, -150.0]), np.array([44.13, 36.07, -40.0])).tolist() == \
            [True, True, False]
        assert pw.in_view(ax, np.array([]), np.array([])).tolist() == []

    def test_drawn_named_and_keyed(self):
        ax = _globe(45, 28)
        shown = pw.draw_ports(ax, _ports())
        assert shown == 4
        names = [t.get_text() for t in ax.texts]
        assert names[:2] == ["Rotterdam", "Qingdao"]                          # busiest first, "Port" dropped
        legend = pw.add_ports_legend(ax, _ports(), shown)
        assert legend.get_title().get_text() == ("Ports in view (4), sized by the ships calling a year, "
                                                 "2019-2025 (IMF PortWatch)")
        assert [t.get_text() for t in legend.get_texts()] == ["1,000 ships a year", "5,000 ships a year",
                                                              "20,000 ships a year"]

    def test_kind_in_key(self):
        legend = pw.add_ports_legend(_globe(), _ports("roro"), 2)
        assert legend.get_title().get_text().startswith("Ports of ro-ro ships in view (2)")

    def test_names_leave_room_for_each_other(self):
        ax = _globe(44.2, 28.6)
        ax.figure.set_size_inches(2, 2)
        pw.draw_ports(ax, _ports())
        assert "Midia" not in [t.get_text() for t in ax.texts]                 # on top of Constantza's name

    def test_nothing_in_view(self):
        ax = _globe(-45, -150)
        far = _ports()._replace(lon=np.array([28.0]), lat=np.array([44.0]), names=["Midia"], countries=["RO"],
                                ships=np.array([500.0]))
        assert pw.draw_ports(ax, far) == 0 and not ax.texts and not ax.collections
