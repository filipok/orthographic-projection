"""Tests for air_routes.py (no network; small OpenFlights files are written here)."""

import io

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

import air_routes as ar
from rotation import globe_projection

AIRPORTS = """\
507,"London Heathrow Airport","London","United Kingdom","LHR","EGLL",51.4706,-0.461941,83,0,"E","Europe/London","airport","OurAirports"
502,"London Gatwick Airport","London","United Kingdom","LGW","EGKK",51.148102,-0.190278,202,0,"E","Europe/London","airport","OurAirports"
3797,"John F Kennedy International Airport","New York","United States","JFK","KJFK",40.63980103,-73.77890015,13,-5,"A","America/New_York","airport","OurAirports"
1382,"Charles de Gaulle International Airport","Paris","France","CDG","LFPG",49.012798,2.55,392,1,"E","Europe/Paris","airport","OurAirports"
3361,"Sydney Kingsford Smith International Airport","Sydney","Australia","SYD","YSSY",-33.94609832763672,151.177001953125,21,10,"O","Australia/Sydney","airport","OurAirports"
9999,"Nowhere Strip","Nowhere","Nowhere",\\N,\\N,10,10,0,0,"U","\\N","airport","OurAirports"
"""
AIRLINES = """\
1355,"British Airways",\\N,"BA","BAW","SPEEDBIRD","United Kingdom","Y"
24,"American Airlines",\\N,"AA","AAL","AMERICAN","United States","Y"
137,"Air France",\\N,"AF","AFR","AIRFRANS","France","Y"
4296,"Qantas",\\N,"QF","QFA","QANTAS","Australia","Y"
"""
ROUTES = """\
BA,1355,LHR,507,JFK,3797,,0,744
BA,1355,JFK,3797,LHR,507,,0,744
AA,24,LHR,507,JFK,3797,,0,777
AA,24,LHR,507,JFK,3797,Y,0,777
AF,137,LHR,507,CDG,1382,,0,320
BA,1355,LGW,502,CDG,1382,,0,320
QF,4296,LHR,507,SYD,3361,,1,380
AF,137,CDG,1382,JFK,3797,,0,777
AA,24,JFK,3797,XXX,\\N,,0,777
"""


@pytest.fixture
def data(tmp_path):
    for name, text in (("airports", AIRPORTS), ("airlines", AIRLINES), ("routes", ROUTES)):
        (tmp_path / f"{name}.dat").write_text(text, encoding="utf-8")
    return str(tmp_path)


def _globe(lat=45, lon=-30):
    ax = Figure(figsize=(6, 6)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _pairs(routes):
    codes = routes.airports.iata
    return {(codes[a], codes[b]): int(n) for a, b, n in zip(routes.a, routes.b, routes.airlines)}


@pytest.mark.parametrize("text, request_", [
    ("all", ar.Request((), ())),
    ("", ar.Request((), ())),
    ("LHR, london ,EGLL", ar.Request(("LHR", "london", "EGLL"), ())),
    ("LHR+JFK", ar.Request(("LHR", "JFK"), ())),
    ("airline:BA, airline: Air France, CDG", ar.Request(("CDG",), ("BA", "Air France"))),
])
def test_requests(text, request_):
    assert ar.resolve_air_routes(text) == request_


def test_airline_needs_a_name():
    with pytest.raises(ValueError, match="Name an airline"):
        ar.resolve_air_routes("airline:")


class TestLoad:
    def test_whole_network(self, data):
        routes = ar.load_air_routes("all", data)
        # Both directions are one arc; the codeshare and the route to an unknown airport are left out
        assert {frozenset(p): n for p, n in _pairs(routes).items()} == {
            frozenset(p): n for p, n in {("LHR", "JFK"): 2, ("LHR", "CDG"): 1, ("LGW", "CDG"): 1, ("LHR", "SYD"): 1,
                                         ("CDG", "JFK"): 1}.items()}
        assert list(routes.airlines) == sorted(routes.airlines)         # the busiest drawn last, on top
        assert routes.title == "Air routes" and routes.picked == [] and routes.carriers == []

    @pytest.mark.parametrize("wanted", ["LHR", "egll"])
    def test_from_an_airport(self, data, wanted):
        routes = ar.load_air_routes(wanted, data)
        assert set(_pairs(routes)) == {("LHR", "JFK"), ("LHR", "CDG"), ("LHR", "SYD")}
        assert routes.title == "Air routes from London Heathrow Airport (LHR)"

    def test_from_a_city(self, data):
        routes = ar.load_air_routes("London", data)
        assert len(routes.picked) == 2 and ("LGW", "CDG") in _pairs(routes)

    def test_routes_start_at_the_chosen_airport(self, data):
        routes = ar.load_air_routes("CDG", data)
        assert set(_pairs(routes)) == {("CDG", "LHR"), ("CDG", "LGW"), ("CDG", "JFK")}

    def test_of_an_airline(self, data):
        routes = ar.load_air_routes("airline:BAW", data)
        assert {frozenset(p): n for p, n in _pairs(routes).items()} == {frozenset(("LHR", "JFK")): 1,
                                                                       frozenset(("LGW", "CDG")): 1}
        assert routes.title == "Air routes of British Airways"
        assert _pairs(ar.load_air_routes("airline:british airways", data)) == _pairs(routes)

    def test_airline_from_an_airport(self, data):
        assert set(_pairs(ar.load_air_routes("LHR, airline:AF", data))) == {("LHR", "CDG")}

    @pytest.mark.parametrize("wanted, message", [("ZZZ", "No airport 'ZZZ'"), ("Atlantis", "No airport 'Atlantis'"),
                                                 ("airline:ZZ", "No airline 'ZZ'")])
    def test_unknown(self, data, wanted, message):
        with pytest.raises(ValueError, match=message):
            ar.load_air_routes(wanted, data)

    def test_downloaded_once(self, tmp_path, monkeypatch):
        asked = []

        def fake(request, timeout=60, context=None):
            asked.append(request.full_url)
            name = request.full_url.rsplit("/", 1)[1][:-4]
            return io.BytesIO({"airports": AIRPORTS, "airlines": AIRLINES, "routes": ROUTES}[name].encode())

        monkeypatch.setattr(ar.urllib.request, "urlopen", fake)
        ar.load_air_routes("all", str(tmp_path))
        ar.load_air_routes("all", str(tmp_path))
        assert asked == [ar.DATA_URL.format(name=n) for n in ar.FILES]

    def test_download_failure(self, tmp_path, monkeypatch):
        def fail(request, timeout=60, context=None):
            raise OSError("offline")

        monkeypatch.setattr(ar.urllib.request, "urlopen", fail)
        with pytest.raises(ar.AirRoutesDataError, match="Could not download the OpenFlights airports: offline"):
            ar.load_air_routes("all", str(tmp_path))


class TestGeometry:
    def test_great_circle(self):
        lon, lat = ar.great_circle(0, 0, 90, 0)
        assert len(lon) == 91 and lon[45] == pytest.approx(45) and np.allclose(lat, 0, atol=1e-9)
        lon, lat = ar.great_circle(-74, 40.6, -0.5, 51.5)
        assert lat.max() > 52                 # the arc bows north of both ends
        assert (lon[0], lat[0]) == pytest.approx((-74, 40.6)) and (lon[-1], lat[-1]) == pytest.approx((-0.5, 51.5))

    def test_same_place(self):
        lon, lat = ar.great_circle(10, 10, 10, 10)
        assert len(lon) == 2 and np.allclose(lon, 10)

    def test_near_side(self):
        ax = _globe(0, 0)
        assert ar.near_side(ax, np.array([0.0, 80.0, 100.0, 180.0]), np.zeros(4)).tolist() == [True, True, False, False]


class TestDrawing:
    def test_small_airports_left_undotted_on_the_whole_network(self, data):
        ax = _globe(45, -30)
        ar.draw_air_routes(ax, ar.load_air_routes("all", data))
        assert not ax.texts and len(ax.collections) == 1

    def test_whole_network(self, data, monkeypatch):
        monkeypatch.setattr(ar, "CROWDED_MIN_LINKS", 1)
        ax = _globe(45, -30)
        routes = ar.load_air_routes("all", data)
        shown = ar.draw_air_routes(ax, routes)
        assert shown == 5                       # London-Sydney too, as far as the rim
        assert "Sydney" not in {t.get_text() for t in ax.texts}
        lines = [c for c in ax.collections if isinstance(c, LineCollection)]
        assert len(lines) == 1
        widths = lines[0].get_linewidths()
        assert max(widths) > min(widths)
        names = [t.get_text() for t in ax.texts]
        assert {"London", "New York"} <= set(names) and names.count("London") == 1   # Paris and Gatwick: no room
        texts = [t.get_text() for t in ar.add_air_routes_legend(ax, routes, shown).get_texts()]
        assert texts == ["1 airline", "3 airlines", "10 airlines or more"]

    def test_chosen_airports_coloured_and_named(self, data):
        ax = _globe(45, -30)
        routes = ar.load_air_routes("LHR, CDG", data)
        shown = ar.draw_air_routes(ax, routes)
        colours = {tuple(np.round(c[:3], 3)) for c in ax.collections[0].get_colors()}
        assert len(colours) == 2
        assert "London" in {t.get_text() for t in ax.texts}
        key = ar.add_air_routes_legend(ax, routes, shown)
        assert [t.get_text() for t in key.get_texts()][:2] == ["London Heathrow Airport (LHR)",
                                                              "Charles de Gaulle International Airport (CDG)"]
        assert key.get_title().get_text().startswith("Air routes from London Heathrow Airport (LHR), Charles")

    def test_nothing_in_view(self, data):
        ax = _globe(-40, 170)
        routes = ar.load_air_routes("LGW", data)
        assert ar.draw_air_routes(ax, routes) == 0
        assert not [c for c in ax.collections if isinstance(c, LineCollection)]
