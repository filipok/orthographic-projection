"""Tests for high_speed_rail.py (no network: the lines and populations are bundled)."""

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.text import Text

import frame
import high_speed_rail as hsr
from rotation import globe_projection


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
    ("planned", "planned"), ("Future", "planned"), ("all-planned", "planned"),
])
def test_choices(text, choice):
    assert hsr.resolve_high_speed_rail(text) == choice


def test_unknown_choice():
    with pytest.raises(ValueError, match="Unknown high-speed rail choice 'maglev'"):
        hsr.resolve_high_speed_rail("maglev")


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
        for table in (hsr.OPEN_LINES, hsr.PLANNED_LINES):
            for name, items in table.items():
                assert hsr._sequence(name, items)

    def test_bad_lines_are_refused(self):
        with pytest.raises(ValueError, match="alternate"):
            hsr._sequence("x", ["Paris", 60])
        with pytest.raises(ValueError, match="alternate"):
            hsr._sequence("x", ["Paris", 0, "Lyon"])
        with pytest.raises(ValueError, match="without a station: Atlantis"):
            hsr._sequence("x", ["Paris", 60, "Atlantis"])

    def test_every_station_is_used_and_peopled(self):
        named = {c for t in (hsr.OPEN_LINES, hsr.PLANNED_LINES) for items in t.values() for c in items[::2]}
        assert named == set(hsr.STATIONS)
        for name, (lat, lon, people, _) in hsr.STATIONS.items():
            assert -90 <= lat <= 90 and -180 <= lon <= 180, name
            assert people >= 40_000, name

    def test_points_along_the_way_join_linked_cities(self):
        pairs = {frozenset(l[:2]) for t in (hsr.OPEN_LINES, hsr.PLANNED_LINES) for n, items in t.items()
                 for l in hsr._sequence(n, items)}
        for a, b in hsr.VIA:
            assert frozenset((a, b)) in pairs, (a, b)

    def test_open_network(self):
        rail = _load("open")
        assert not rail.planned.any() and not rail.with_planned
        assert len(rail.a) == len({frozenset((int(a), int(b))) for a, b in zip(rail.a, rail.b)})
        assert np.all(np.diff(rail.score) >= 0)                           # the strongest drawn last
        k = _link(rail, "Paris", "Lyon")
        assert rail.minutes[k] == 116
        people = hsr.STATIONS["Paris"][2] * hsr.STATIONS["Lyon"][2]
        assert rail.score[k] == pytest.approx(people / 100_000 / 232)
        assert "Riga" not in {rail.names[i] for i in (*rail.a, *rail.b)}

    def test_brescia_verona_padua_is_not_open_yet(self):
        rail = _load()
        assert "Verona" not in {rail.names[i] for i in (*rail.a, *rail.b)}
        assert not rail.planned[_link(rail, "Milan", "Brescia")] and not rail.planned[_link(rail, "Padua", "Venice")]
        rail = _load("planned")
        assert rail.planned[_link(rail, "Brescia", "Verona")] and rail.planned[_link(rail, "Verona", "Padua")]

    def test_planned_adds_dashed_links(self):
        rail = _load("planned")
        assert rail.with_planned
        assert rail.planned[_link(rail, "Tallinn", "Riga")]
        assert not rail.planned[_link(rail, "Paris", "Lyon")]
        assert rail.planned.sum() == sum(len(items) // 2 for items in hsr.PLANNED_LINES.values())

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
        rail = _load("planned")
        shown = hsr.draw_high_speed_rail(ax, rail)
        legend = hsr.add_high_speed_rail_legend(ax, rail, shown)
        labels = [t.get_text() for t in legend.get_texts()]
        assert labels[:6] == ["under 1k", "1k-10k", "10k-100k", "100k-1M", "1M-10M", "10M or more"]
        assert "planned" in labels                                        # HS2 and Rail Baltica
        assert "within one urban area" not in labels                      # none in Europe
        assert f"{len(shown):,} links in view" in legend.get_title().get_text()

    def test_within_one_urban_area_keyed_where_it_shows(self):
        ax = _globe(25, 115)
        rail = _load("open")
        labels = [t.get_text() for t in hsr.add_high_speed_rail_legend(ax, rail, hsr.draw_high_speed_rail(ax, rail))
                  .get_texts()]
        assert "within one urban area" in labels and "planned" not in labels
