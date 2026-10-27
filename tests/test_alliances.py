"""Tests for alliances.py: the EU's and NATO's lines, from made-up Natural Earth records."""

import pytest
import shapely
from shapely.geometry import MultiPolygon, Point, Polygon, box

import alliances
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure
from ortho import globe_projection


def _record(adm0, sovereign, *boxes):
    return {"adm0_a3": adm0, "sovereignt": sovereign}, MultiPolygon([box(*b) for b in boxes])


RECORDS = [
    _record("ROU", "Romania", (20, 44, 29, 48)),
    _record("AUT", "Austria", (10, 46, 17, 49)),
    # France with French Guiana: in the EU, outside NATO's area
    _record("FRA", "France", (-5, 42, 8, 51), (-54, 2, -51, 6)),
    # The Netherlands with Bonaire: outside the EU, and south of the Tropic of Cancer
    _record("NLD", "Netherlands", (3, 51, 7, 53), (-68.5, 12, -68, 12.5)),
    _record("GRL", "Denmark", (-60, 60, -20, 80)),                       # Greenland: NATO only
    _record("USA", "United States of America", (-125, 25, -67, 49), (-160, 19, -155, 22.5), (-178, 28, -177, 28.5)),
    _record("SRB", "Republic of Serbia", (19, 42, 23, 46)),
]


def test_names_are_checked_and_ordered_nato_first():
    assert alliances.resolve_alliances(["EU", "nato"]) == ["nato", "eu"]
    assert alliances.resolve_alliances(["eu", "eu"]) == ["eu"]
    with pytest.raises(ValueError, match="Unknown alliance 'warsaw'"):
        alliances.resolve_alliances(["warsaw"])


def test_the_eu_takes_the_outermost_regions_but_not_the_overseas_territories():
    eu = alliances._territory("eu", RECORDS)
    for inside in [(25, 46), (15, 47), (0, 47), (-52.5, 4), (5, 52)]:
        assert shapely.contains(eu, Point(inside)), inside
    for outside in [(-68.2, 12.2), (-40, 70), (-100, 40), (21, 44)]:
        assert not shapely.contains(eu, Point(outside)), outside


def test_nato_keeps_to_the_treaty_area():
    nato = alliances._territory("nato", RECORDS)
    for inside in [(25, 46), (0, 47), (5, 52), (-40, 70), (-100, 40)]:
        assert shapely.contains(nato, Point(inside)), inside
    # Austria and Serbia are not members; French Guiana, Bonaire, Hawaii and Midway are outside Article 6's area
    for outside in [(15, 47), (21, 44), (-52.5, 4), (-68.2, 12.2), (-157, 20), (-177.5, 28.2)]:
        assert not shapely.contains(nato, Point(outside)), outside


def test_neighbours_join_into_one_outline():
    eu = alliances._territory("eu", RECORDS)
    # Romania and Austria, a hair apart here, close up; France and the Netherlands stay apart
    assert len(shapely.get_parts(eu)) == len(shapely.get_parts(alliances._territory("eu", RECORDS[:4])))
    assert isinstance(alliances._territory("eu", [_record("ROU", "Romania", (20, 44, 29, 48)),
                                                  _record("AUT", "Austria", (10, 44, 19.99, 48))]), Polygon)


def test_a_shade_keeps_the_straits_open():
    apart = [_record("DNK", "Denmark", (8, 54, 12.4, 58)), _record("SWE", "Sweden", (12.6, 55, 20, 69))]
    assert isinstance(alliances._territory("nato", apart), Polygon)                       # a line closes the strait
    assert isinstance(alliances._territory("nato", apart, alliances.HAIRLINE), MultiPolygon)


def test_no_land_is_an_error():
    with pytest.raises(alliances.AllianceError):
        alliances._territory("nato", [_record("AUT", "Austria", (10, 46, 17, 49))])


def test_lines_and_key():
    ax = Figure(figsize=(6, 6)).add_subplot(projection=globe_projection(46, 25))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    found = [alliances.Alliance(key, *alliances.STYLES[key], alliances._territory(key, RECORDS))
             for key in ("nato", "eu")]
    alliances.draw_alliances(ax, found)
    nato_shade, eu_line = ax.collections
    assert nato_shade.get_zorder() < 6 < eu_line.get_zorder()          # under the borders' lines, and over them
    key = alliances.add_alliances_legend(ax, found, "ro")
    assert [t.get_text() for t in key.get_texts()] == ["NATO (32 de state)", "Uniunea Europeană (27 de state)"]
    assert [type(h).__name__ for h in key.legend_handles] == ["Rectangle", "Line2D"]
    assert [t.get_text() for t in alliances.add_alliances_legend(ax, found[1:]).get_texts()] == [
        "European Union (27 states)"]


def test_member_lists():
    assert len(alliances.EU_MEMBERS) == 27 and len(alliances.NATO_MEMBERS) == 32
    assert "United Kingdom" not in alliances.EU_MEMBERS and {"Finland", "Sweden"} <= set(alliances.NATO_MEMBERS)
