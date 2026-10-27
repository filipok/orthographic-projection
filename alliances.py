"""The European Union and NATO today: NATO's land shaded, a line round the EU's.

Data source
-----------
Natural Earth 1:10m Admin 0 countries (public domain), read through cartopy as
the ``natural_earth`` base map reads it (see :mod:`ne_local`).

Members as of 2026: the EU's 27 states (the United Kingdom left on 31 January
2020) and NATO's 32 (Finland joined on 4 April 2023, Sweden on 7 March 2024).

What each line goes round
-------------------------
The EU: its member states with Åland and the outermost regions (French
Guiana, Guadeloupe, Martinique, Réunion, Mayotte, Saint-Martin, the Azores,
Madeira and the Canary Islands), and all of Cyprus, the north too, where EU
law is suspended; not the overseas countries and territories (Greenland, the
Faroe Islands, Aruba, Curaçao, Sint Maarten and the Caribbean Netherlands).

NATO: its members' land within the area of Article 6 of the North Atlantic
Treaty, Europe, North America, Turkey and the islands north of the Tropic of
Cancer (Greenland, Svalbard, Bermuda); not Hawaii, French Guiana or the
Caribbean islands. Ceuta and Melilla, on the African coast and outside it,
are too small to show and are left in.

Natural Earth draws Crimea in Russia; neither line goes near it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, NamedTuple

import shapely

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.io import shapereader
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

logger = logging.getLogger(__name__)

ATTRIBUTION = "EU and NATO: Natural Earth"
TROPIC_OF_CANCER = 23.44
SIMPLIFY = 0.02           # degrees (~2 km)
CLOSE = 0.15              # degrees: round a line, straits and gaps narrower than ~30 km are closed
HAIRLINE = 0.01           # degrees: under a shade, only the hairline gaps between neighbours
MIN_AREA = 0.02           # square degrees (~200 km² in Europe): smaller islands are left out, but each country's largest

EU_MEMBERS = ("Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland",
              "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg",
              "Malta", "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden")
NATO_MEMBERS = ("Albania", "Belgium", "Bulgaria", "Canada", "Croatia", "Czechia", "Denmark", "Estonia", "Finland",
                "France", "Germany", "Greece", "Hungary", "Iceland", "Italy", "Latvia", "Lithuania", "Luxembourg",
                "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland", "Portugal", "Romania",
                "Slovakia", "Slovenia", "Spain", "Sweden", "Turkey", "United Kingdom", "United States of America")
# Natural Earth's countries (ADM0_A3) in the EU: the members, Åland, Saint-Martin, northern Cyprus and the buffer zone
EU_TERRITORIES = ("AUT", "BEL", "BGR", "HRV", "CYP", "CYN", "CNM", "CZE", "DNK", "EST", "FIN", "ALD", "FRA", "MAF",
                  "DEU", "GRC", "HUN", "IRL", "ITA", "LVA", "LTU", "LUX", "MLT", "NLD", "POL", "PRT", "ROU", "SVK",
                  "SVN", "ESP", "SWE")


class Alliance(NamedTuple):
    """An alliance's land and how it is drawn: a line round it, or a shade over it."""
    key: str                # "eu" or "nato"
    names: dict[str, str]   # its name by language
    colour: str
    width: float            # points of the line round it; 0 for none
    shade: float            # opacity of the colour over its land; 0 for none
    outline: shapely.Geometry


STYLES = {   # names, colour, line width, shade: NATO's land shaded pale blue, the EU's lined in gold over it
    "eu": ({"en": f"European Union ({len(EU_MEMBERS)} states)", "ro": f"Uniunea Europeană ({len(EU_MEMBERS)} de state)"},
           "#ffcc00", 1.1, 0.0),
    "nato": ({"en": f"NATO ({len(NATO_MEMBERS)} states)", "ro": f"NATO ({len(NATO_MEMBERS)} de state)"},
             "#7dbaff", 0.0, 0.5),
}
NAMES = tuple(STYLES)


class AllianceError(RuntimeError):
    """Natural Earth's countries could not be read."""


def resolve_alliances(names: Sequence[str]) -> list[str]:
    """*names* checked and in drawing order (NATO's shade under the EU's line). Raises ``ValueError``."""
    chosen = {name.strip().lower() for name in names}
    unknown = chosen - set(NAMES)
    if unknown:
        raise ValueError(f"Unknown alliance {sorted(unknown)[0]!r}; use {' or '.join(NAMES)}.")
    return [name for name in ("nato", "eu") if name in chosen]


def _in_treaty_area(part: shapely.Geometry) -> bool:
    """Whether *part* lies where Article 6 of the North Atlantic Treaty reaches: north of the Tropic of Cancer,
    and not out in the Pacific (Hawaii's north-western islands and Midway are north of it)."""
    x, y = shapely.get_coordinates(shapely.point_on_surface(part))[0]
    return y > TROPIC_OF_CANCER and not (x < -150 and y < 35)


def _territory(key: str, records: Sequence[tuple[dict[str, Any], Any]], close: float = CLOSE) -> shapely.Geometry:
    """The land of alliance *key*, one shape, from Natural Earth's ``(attributes, geometry)`` *records*, with the
    gaps narrower than twice *close* (degrees) shut."""
    parts = []
    for attributes, geometry in records:
        if key == "eu":
            code = str(attributes.get("adm0_a3"))
            if code not in EU_TERRITORIES:
                continue
            found = shapely.get_parts(geometry)
            if code == "NLD":   # Bonaire, Sint Eustatius and Saba are overseas territories, outside the EU
                found = [p for p in found if shapely.get_x(shapely.point_on_surface(p)) > -30]
        else:
            if attributes.get("sovereignt") not in NATO_MEMBERS:
                continue
            found = [p for p in shapely.get_parts(geometry) if _in_treaty_area(p)]
        found = sorted(found, key=shapely.area, reverse=True)
        parts += [shapely.simplify(p, SIMPLIFY) for k, p in enumerate(found) if k == 0 or shapely.area(p) >= MIN_AREA]
    if not parts:
        raise AllianceError(f"Natural Earth's countries hold no land of the {key.upper()}")
    # Closing the union shuts the hairline gaps between neighbours and, round a line, the narrow straits inside it
    land = shapely.union_all(parts)
    return shapely.make_valid(shapely.buffer(shapely.buffer(land, close), -close))


def load_alliances(names: Sequence[str]) -> list[Alliance]:
    """The alliances called *names* (``"eu"``, ``"nato"``), NATO first so the EU's line is drawn over its shade."""
    keys = resolve_alliances(names)
    try:
        reader = shapereader.Reader(shapereader.natural_earth("10m", "cultural", "admin_0_countries"))
        records = [({k.lower(): v for k, v in r.attributes.items()}, r.geometry) for r in reader.records()
                   if r.geometry is not None and not r.geometry.is_empty]
    except Exception as e:  # cartopy raises whatever its download or the shapefile reader does
        raise AllianceError(f"could not read Natural Earth's countries: {e}") from e
    return [Alliance(key, *STYLES[key], _territory(key, records, CLOSE if STYLES[key][2] else HAIRLINE))
            for key in keys]


def draw_alliances(ax: GeoAxes, alliances: Sequence[Alliance]) -> None:
    """Shade or line each of *alliances* on *ax*: a shade under the borders' lines, a line over them."""
    for alliance in alliances:
        if alliance.shade:
            ax.add_geometries([alliance.outline], crs=ccrs.PlateCarree(), facecolor=alliance.colour,
                              alpha=alliance.shade, edgecolor="none", zorder=5)
        if alliance.width:
            ax.add_geometries([shapely.boundary(alliance.outline)], crs=ccrs.PlateCarree(), facecolor="none",
                              edgecolor=alliance.colour, linewidth=alliance.width, zorder=7,
                              capstyle="round", joinstyle="round")


def add_alliances_legend(ax: GeoAxes, alliances: Sequence[Alliance], language: str = "en",
                         y: float = -0.01, x: float = 0.5) -> Legend:
    """A key below the globe: each alliance's line or shade and its name, with its member count."""
    handles = [Line2D([], [], color=a.colour, linewidth=a.width, solid_capstyle="round") if a.width else
               mpatches.Patch(facecolor=a.colour, alpha=a.shade, edgecolor="#333333", linewidth=0.6)
               for a in alliances]
    labels = [a.names.get(language, a.names["en"]) for a in alliances]
    legend = Legend(
        ax, handles, labels,
        loc="upper center", bbox_to_anchor=(x, y), ncol=len(alliances), fontsize=10,
        frameon=True, fancybox=True, framealpha=0.85, edgecolor="#444444",
        handlelength=2.4, columnspacing=1.6, borderpad=0.6,
    )
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
