"""Tests for high_speed_rail.py (no network: the lines and populations are bundled)."""

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.text import Text

import frame
import high_speed_rail as hsr
from high_speed_rail_china import CHINA_LINES, CHINA_STATIONS, CHINA_VIA
from high_speed_rail_world import WORLD_LINES, WORLD_STATIONS, WORLD_VIA
from rail_tracks import distance_km
from rotation import globe_projection

TRUNK_STATIONS = 221       # high_speed_rail's own tables, without the generated ones
TRUNK_OPEN_LINKS = 207


def _load(choice="open"):
    """The links, drawn city to city (OSM's tracks are tested in test_rail_tracks.py)."""
    return hsr.load_high_speed_rail(choice, tracks=False)


def _globe(lat=48, lon=9, inches=8):
    ax = Figure(figsize=(inches, inches)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _radius(lat, lon, half_degrees, inches=8):
    """A globe zoomed to about *half_degrees* around its centre, as --radius cuts it."""
    ax = _globe(lat, lon, inches)
    half = np.radians(half_degrees) * 6_371_000
    ax.set_xlim(-half, half)
    ax.set_ylim(-half, half)
    return ax


def _link(rail, a, b):
    for k in range(len(rail.a)):
        if {rail.names[rail.a[k]], rail.names[rail.b[k]]} == {a, b}:
            return k
    raise AssertionError(f"no link {a}-{b}")


@pytest.mark.parametrize("text, choice", [
    (None, "open"), ("", "open"), (True, "open"), ("Open", "open"), ("all", "open"), ("high_speed_rail", "open"),
])
def test_choices(text, choice):
    assert hsr.resolve_high_speed_rail(text) == choice


@pytest.mark.parametrize("text", ["maglev", "planned"])
def test_unknown_choice(text):
    with pytest.raises(ValueError, match=f"Unknown high-speed rail choice '{text}'"):
        hsr.resolve_high_speed_rail(text)


def test_gravity_is_the_formula_in_the_map_it_follows():
    # (P1 × P2 / 100,000) / (2 × minutes)
    assert hsr.gravity(2_000_000, 300_000, 100) == pytest.approx(2e6 * 3e5 / 1e5 / 200)
    assert np.allclose(hsr.gravity(np.array([1e6, 2e6]), 1e6, np.array([10, 20])), [5e5, 5e5])


@pytest.mark.parametrize("score, text", [
    (0.4, "0"), (730, "730"), (999, "999"), (1_000, "1k"), (2_345, "2.3k"), (27_400, "27k"), (999_000, "999k"),
    (1_000_000, "1M"), (4_230_000, "4.2M"), (77_000_000, "77M"),
])
def test_format_score(score, text):
    assert hsr.format_score(score) == text


def test_classes_are_powers_of_ten():
    assert hsr.score_class([0, 999, 1_000, 50_000, 999_999, 1e6, 5e7]).tolist() == [0, 0, 1, 2, 3, 4, 5]
    assert len(hsr.COLOURS) == len(hsr.WIDTHS) == len(hsr.BREAKS) + 1


class TestData:
    def test_every_line_alternates_known_cities_and_minutes(self):
        for name, items in hsr.OPEN_LINES.items():
            assert hsr._sequence(name, items)

    def test_bad_lines_are_refused(self):
        with pytest.raises(ValueError, match="alternate"):
            hsr._sequence("x", ["Paris", 60])
        with pytest.raises(ValueError, match="alternate"):
            hsr._sequence("x", ["Paris", 0, "Lyon"])
        with pytest.raises(ValueError, match="without a station: Atlantis"):
            hsr._sequence("x", ["Paris", 60, "Atlantis"])

    def test_every_station_is_used_and_peopled(self):
        named = {c for items in hsr.OPEN_LINES.values() for c in items[::2]}
        assert named == set(hsr.STATIONS)
        for name, (lat, lon, people, _) in hsr.STATIONS.items():
            assert -90 <= lat <= 90 and -180 <= lon <= 180, name
            assert people >= 40_000, name

    def test_points_along_the_way_join_linked_cities(self):
        pairs = {frozenset(l[:2]) for n, items in hsr.OPEN_LINES.items() for l in hsr._sequence(n, items)}
        for a, b in hsr.VIA:
            assert frozenset((a, b)) in pairs, (a, b)

    def test_open_network(self):
        rail = _load("open")
        assert len(rail.a) == len({frozenset((int(a), int(b))) for a, b in zip(rail.a, rail.b)})
        assert np.all(np.diff(rail.score) >= 0)                           # the strongest drawn last
        k = _link(rail, "Paris", "Lyon")
        assert rail.minutes[k] == 116
        people = hsr.STATIONS["Paris"][2] * hsr.STATIONS["Lyon"][2]
        assert rail.score[k] == pytest.approx(people / 100_000 / 232)

    def test_the_generated_tables_only_add_to_the_hand_compiled_lines(self):
        # A name or a line of the same name in two tables would silently replace the hand-compiled line's
        assert len(hsr.STATIONS) == TRUNK_STATIONS + len(CHINA_STATIONS) + len(WORLD_STATIONS)
        added = sum(len(items) // 2 for table in (CHINA_LINES, WORLD_LINES) for items in table.values())
        assert len(_load("open").a) == TRUNK_OPEN_LINKS + added
        assert all(name.startswith("China ") for name in CHINA_LINES)
        assert all(name.startswith("OSM ") for name in WORLD_LINES)
        assert set(CHINA_VIA) | set(WORLD_VIA) <= set(hsr.VIA)
        rail = _load()
        assert rail.minutes[_link(rail, "Shenyang", "Dalian")] == 105      # a trunk link keeps its timetable time
        assert rail.minutes[_link(rail, "Paris", "Lyon")] == 116

    @pytest.mark.parametrize("lines", [CHINA_LINES, WORLD_LINES], ids=["china", "world"])
    def test_the_generated_tables_have_estimated_times(self, lines):
        for name, items in lines.items():
            for a, b, minutes in hsr._sequence(name, items):
                assert minutes % 5 == 0, (a, b)
                lat_a, lon_a = hsr.STATIONS[a][:2]
                lat_b, lon_b = hsr.STATIONS[b][:2]
                km = float(distance_km(lon_a, lat_a, lon_b, lat_b))
                assert km / (minutes / 60) < 400, (a, b, minutes)          # no train is that fast

    def test_the_world_table_has_real_names_and_no_line_no_train_runs(self):
        for name in WORLD_STATIONS:
            assert "#" not in name and not name.endswith("-si"), name
        # GHSL's names for these places are a province, an industrial estate, or spelt without accents
        for wrong, right in (("Kocaeli", "İzmit"), ("ICAD I", "Musaffah"), ("Malmo", "Malmö")):
            assert wrong not in WORLD_STATIONS and WORLD_STATIONS[right][3] == wrong
        pairs = {frozenset(l[:2]) for n, items in WORLD_LINES.items() for l in hsr._sequence(n, items)}
        for a, b in (("Luton", "Peterborough"), ("Luton", "Northampton"), ("Helsingborg", "Västerås")):
            assert frozenset((a, b)) not in pairs                          # main lines joined across a gap

    def test_the_china_table_has_real_names(self):
        for name in CHINA_STATIONS:
            assert "#" not in name and "[" not in name, name
        # GHSL's names for these places are a town, a district, or another city's
        for wrong, right in (("Fushan", "Yantai"), ("Yingkou", "Huaihua"), ("Haizhou District", "Lianyungang")):
            assert wrong not in CHINA_STATIONS and CHINA_STATIONS[right][3] == wrong

    def test_no_planned_lines_are_left(self):
        assert not hasattr(hsr, "PLANNED_LINES") and list(hsr.CHOICES) == ["open"]
        assert "Milan-Brescia" in hsr.OPEN_LINES and "Padua-Venice" in hsr.OPEN_LINES

    def test_the_main_lines_osm_left_on_no_link_have_timetable_times(self):
        # Fast track no link drew in 2026, added by hand with the operators' times (SAR, SJ, National Rail, CP,
        # Hellenic Train); Lisbon and Porto are back, on today's line
        rail = _load()
        for a, b, minutes in (("Riyadh", "Buraydah", 147), ("Sakaka", "Qurayyat", 176), ("Stockholm", "Gothenburg", 205),
                              ("London", "Stevenage", 21), ("Stevenage", "Peterborough", 29), ("Lisbon", "Coimbra", 96),
                              ("Aveiro", "Porto", 33), ("Athens", "Larissa", 218), ("Larissa", "Thessaloniki", 91)):
            assert rail.minutes[_link(rail, a, b)] == minutes, (a, b)
        assert not {"Stockholm", "Gothenburg", "Peterborough"} & set(WORLD_STATIONS)
        assert hsr.STATIONS["Coimbra"][3] == "" and hsr.STATIONS["Aveiro"][3] == ""     # people from GeoNames

    def test_links_within_one_urban_centre_are_not_scored(self):
        rail = _load()
        k = _link(rail, "Guangzhou", "Shenzhen")
        assert rail.within[k] and rail.score[k] == 0
        assert not rail.within[_link(rail, "Shenzhen", "Hong Kong")]
        # A town outside every urban centre ("") is never within one with another
        assert not rail.within[_link(rail, "Madrid", "Toledo")]


class TestDrawing:
    def test_links_in_view_drawn_and_counted(self):
        ax = _globe()
        rail = _load()
        shown = hsr.draw_high_speed_rail(ax, rail)
        names = {frozenset((rail.names[rail.a[k]], rail.names[rail.b[k]])) for k in shown}
        assert frozenset(("Paris", "Lyon")) in names
        assert frozenset(("Jakarta", "Bandung")) not in names           # on the far side
        lines = [c for c in ax.collections if isinstance(c, LineCollection)]
        assert lines and max(max(c.get_linewidths()) for c in lines) > hsr.WIDTHS[0]
        assert "Paris" in {t.get_text() for t in ax.texts}
        # Zoomed in, the scores are written along their links
        ax = _radius(47, 4, 4)
        hsr.draw_high_speed_rail(ax, rail)
        assert hsr.format_score(float(rail.score[_link(rail, "Paris", "Lyon")])) in {t.get_text() for t in ax.texts}

    def test_a_radius_map_counts_only_its_links(self):
        rail = _load()
        everywhere = hsr.draw_high_speed_rail(_globe(40, 116), rail)
        near = hsr.draw_high_speed_rail(_radius(40, 116, 3), rail)
        names = {rail.names[i] for k in near for i in (rail.a[k], rail.b[k])}
        assert near and len(near) < len(everywhere) and "Beijing" in names and "Kunming" not in names

    def test_score_labels_stay_on_the_map(self):
        ax = _radius(48, 9, 6)
        hsr.draw_high_speed_rail(ax, _load())
        ax.figure.canvas.draw()
        written = [t for t in ax.texts if isinstance(t, Text) and t.get_text()[:1].isdigit()]
        assert written
        for text in written:
            assert frame.inside(ax, *text.get_position())

    def test_nothing_in_view(self):
        ax = _globe(-40, -140)                                            # the South Pacific
        assert hsr.draw_high_speed_rail(ax, _load()) == set()
        assert not ax.collections

    def test_no_labels(self):
        ax = _globe()
        assert hsr.draw_high_speed_rail(ax, _load(), labels=False)
        assert not ax.texts


class TestLegend:
    def test_classes_and_only_what_shows(self):
        ax = _radius(50, 5, 12)
        rail = _load("open")
        shown = hsr.draw_high_speed_rail(ax, rail)
        legend = hsr.add_high_speed_rail_legend(ax, rail, shown)
        labels = [t.get_text() for t in legend.get_texts()]
        assert labels[:6] == ["under 1k", "1k-10k", "10k-100k", "100k-1M", "1M-10M", "10M or more"]
        assert "within one urban area" not in labels                      # none in Europe
        assert f"{len(shown):,} links in view" in legend.get_title().get_text()

    def test_within_one_urban_area_keyed_where_it_shows(self):
        ax = _globe(25, 115)
        rail = _load("open")
        labels = [t.get_text() for t in hsr.add_high_speed_rail_legend(ax, rail, hsr.draw_high_speed_rail(ax, rail))
                  .get_texts()]
        assert "within one urban area" in labels
