"""Air routes: the airline network as great-circle arcs, from OpenFlights.

Data source
-----------
OpenFlights airport, airline and route databases (https://openflights.org/data),
Open Database License (ODbL 1.0). The route database lists 67,663 routes
between 3,321 airports on 548 airlines, as of June 2014: the last update
OpenFlights made. Routes that only an airline's codeshare partner sells are
left out, and the two directions of a route are one arc, so each arc is an
airport pair, weighted by how many airlines fly it.

The three files (~3.8 MB) are downloaded once into
``~/.cache/ortho_tiles/air_routes/``.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import urllib.request
from collections import defaultdict
from typing import NamedTuple

import numpy as np

import cartopy.crs as ccrs
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from biomes import on_globe
from cities import label_space

logger = logging.getLogger(__name__)

DATA_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/{name}.dat"
FILES = ("airports", "airlines", "routes")
ROUTE_COLOUR = "#c51b7d"
PICKED_COLOURS = ("#c51b7d", "#2166ac", "#1b7837", "#e08214", "#762a83", "#b2182b")
AIRPORT_COLOUR = "#4d004b"
LABEL_MAX = 30
CROWDED_MIN_LINKS = 12          # on the whole network, only airports linked to this many places are dotted
KEY_AIRLINES = (1, 3, 10)

AIR_ROUTES_ATTRIBUTION = "Air routes: OpenFlights (openflights.org), June 2014, ODbL"


class AirRoutesDataError(RuntimeError):
    """The OpenFlights databases could not be downloaded or read."""


class Airports(NamedTuple):
    iata: list[str]
    icao: list[str]
    names: list[str]
    cities: list[str]
    countries: list[str]
    lon: np.ndarray
    lat: np.ndarray


class AirRoutes(NamedTuple):
    """Airport pairs: indices into *airports*, and how many airlines fly each."""
    a: np.ndarray
    b: np.ndarray
    airlines: np.ndarray
    airports: Airports
    picked: list[int]          # the airports asked for (index into *airports*); empty for the whole network
    carriers: list[str]        # the airlines asked for, by name; empty for all
    title: str                 # what the arcs are: "Air routes", "Air routes from London Heathrow (LHR)" …


class Request(NamedTuple):
    airports: tuple[str, ...]  # IATA or ICAO codes, or city names
    airlines: tuple[str, ...]  # IATA or ICAO codes, or names


def resolve_air_routes(text: str | None) -> Request:
    """What to draw: ``"all"`` (or blank) for the whole network; airports by code (``"LHR"``, ``"EGLL"``) or
    city (``"London"``), and airlines as ``"airline:BA"`` or ``"airline:British Airways"``, separated by commas
    (or ``+``, which a list of layers can hold: ``"air-routes:LHR+JFK"``).

    Raises ``ValueError`` for an airline left unnamed.
    """
    airports, airlines = [], []
    for part in re.split(r"[,+]", text or ""):
        item = part.strip()
        if not item or item.lower() in ("all", "air-routes", "flights"):
            continue
        if item.lower().startswith("airline:"):
            name = item.split(":", 1)[1].strip()
            if not name:
                raise ValueError("Name an airline after 'airline:', e.g. 'airline:BA'.")
            airlines.append(name)
        else:
            airports.append(item)
    return Request(tuple(airports), tuple(airlines))


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "air_routes")


def _ensure_files(cache_dir: str) -> dict[str, str]:
    from shipping import _tls

    paths = {name: os.path.join(cache_dir, f"{name}.dat") for name in FILES}
    missing = [name for name, path in paths.items() if not os.path.isfile(path)]
    if missing:
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Downloading the OpenFlights databases (one-time, ~4 MB) …")
    for name in missing:
        try:
            request = urllib.request.Request(DATA_URL.format(name=name), headers={"User-Agent": "ortho/1.0"})
            with urllib.request.urlopen(request, timeout=60, context=_tls()) as resp:
                data = resp.read()
        except Exception as exc:              # network and HTTP errors alike
            raise AirRoutesDataError(f"Could not download the OpenFlights {name}: {exc}") from exc
        with open(paths[name] + ".part", "wb") as out:
            out.write(data)
        os.replace(paths[name] + ".part", paths[name])
    return paths


def _rows(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return [[("" if v == "\\N" else v.strip()) for v in row] for row in csv.reader(io.StringIO(fh.read()))]


def _read(cache_dir: str) -> tuple[Airports, dict[str, int], dict[str, tuple[str, str, str, bool]], list[list[str]]]:
    """The airports (and each one's position by OpenFlights id), the airlines by id, and the route rows."""
    paths = _ensure_files(cache_dir)
    try:
        ports = [r for r in _rows(paths["airports"]) if len(r) >= 8]
        index = {r[0]: i for i, r in enumerate(ports)}
        airports = Airports([r[4] for r in ports], [r[5] for r in ports], [r[1] for r in ports],
                            [r[2] for r in ports], [r[3] for r in ports],
                            np.array([float(r[7]) for r in ports]), np.array([float(r[6]) for r in ports]))
        # name, IATA, ICAO, and whether it still flew
        lines = {r[0]: (r[1], r[3], r[4], len(r) < 8 or r[7].upper() != "N")
                 for r in _rows(paths["airlines"]) if len(r) >= 5}
        routes = [r for r in _rows(paths["routes"]) if len(r) >= 8]
    except (OSError, ValueError) as exc:
        raise AirRoutesDataError(f"Could not read the OpenFlights databases: {exc}") from exc
    return airports, index, lines, routes


def _find_airports(airports: Airports, wanted: str, served: dict[int, int]) -> list[int]:
    """The airports *wanted* names: by IATA or ICAO code, or all those of a city with routes, in the country
    where that city has the most (London is London, England, not Ontario). *served* counts each airport's
    routes."""
    code = wanted.strip().upper()
    if len(code) == 3 and code.isalnum():
        return [i for i, c in enumerate(airports.iata) if c == code]
    if len(code) == 4 and code.isalnum():
        found = [i for i, c in enumerate(airports.icao) if c == code]
        if found:
            return found
    name = wanted.strip().lower()
    found = [i for i, c in enumerate(airports.cities) if c.lower() == name and served.get(i)]
    by_country: dict[str, int] = defaultdict(int)
    for i in found:
        by_country[airports.countries[i]] += served[i]
    if not by_country:
        return []
    country = max(by_country, key=lambda k: by_country[k])
    return sorted((i for i in found if airports.countries[i] == country), key=lambda i: -served[i])


def _find_airlines(lines: dict[str, tuple[str, str, str, bool]], wanted: str) -> set[str]:
    """The airlines *wanted* names, by IATA or ICAO code (those still flying, if any are) or by name."""
    text = wanted.strip()
    upper = text.upper()
    by_code = {k for k, (_, iata, icao, _) in lines.items() if (len(upper) == 2 and iata == upper)
               or (len(upper) == 3 and icao == upper)}
    flying = {k for k in by_code if lines[k][3]}
    return flying or by_code or {k for k, (name, _, _, _) in lines.items() if name.lower() == text.lower()}


def _airport_label(airports: Airports, i: int) -> str:
    code = airports.iata[i] or airports.icao[i]
    return f"{airports.names[i]} ({code})" if code else airports.names[i]


def load_air_routes(request: Request | str | None, cache_dir: str | None = None) -> AirRoutes:
    """The airport pairs of *request*. Raises ``ValueError`` for an unknown airport or airline, and
    :class:`AirRoutesDataError` when the data cannot be had."""
    if not isinstance(request, Request):
        request = resolve_air_routes(request)
    airports, index, lines, routes = _read(cache_dir or _default_cache_dir())
    served: dict[int, int] = defaultdict(int)
    for r in routes:
        for end in (r[3], r[5]):
            if end in index:
                served[index[end]] += 1
    picked: list[int] = []
    for wanted in request.airports:
        found = _find_airports(airports, wanted, served)
        if not found:
            raise ValueError(f"No airport {wanted!r} in OpenFlights: give its IATA code (LHR), ICAO code (EGLL) "
                             "or city (London).")
        picked += [i for i in found if i not in picked]
    carriers: set[str] = set()
    flying = {r[1] for r in routes}
    for wanted in request.airlines:
        found_lines = _find_airlines(lines, wanted)
        found_lines = (found_lines & flying) or found_lines        # those with routes, if any have
        if not found_lines:
            raise ValueError(f"No airline {wanted!r} in OpenFlights: give its IATA code (BA), ICAO code (BAW) "
                             "or full name.")
        carriers |= found_lines
    chosen = set(picked)
    flown: dict[tuple[int, int], set[str]] = defaultdict(set)
    for r in routes:
        if r[6].upper() == "Y" or r[3] not in index or r[5] not in index:   # sold by a codeshare partner only
            continue
        if carriers and r[1] not in carriers:
            continue
        a, b = index[r[3]], index[r[5]]
        if a == b or (chosen and a not in chosen and b not in chosen):
            continue
        flown[(min(a, b), max(a, b))].add(r[1] or r[0])
    # Routes from the chosen airports start there
    pairs = [(a, b) if not chosen or a in chosen else (b, a) for a, b in flown]
    order = np.argsort([len(flown[(min(p), max(p))]) for p in pairs], kind="stable")
    pairs = [pairs[i] for i in order]
    names = sorted({lines[c][0] for c in carriers if c in lines})
    title = "Air routes"
    if picked:
        title += " from " + ", ".join(_airport_label(airports, i) for i in picked[:4]) + (" …" if len(picked) > 4 else "")
    if names:
        title += " of " + ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
    logger.info("%s: %d airport pairs.", title, len(pairs))
    return AirRoutes(np.array([a for a, _ in pairs], int), np.array([b for _, b in pairs], int),
                     np.array([len(flown[(min(p), max(p))]) for p in pairs], int), airports, picked, names, title)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def great_circle(lon1: float, lat1: float, lon2: float, lat2: float, step: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Points every *step* degrees or so along the great circle between two places (lon, lat in degrees)."""
    p = np.radians([lon1, lat1, lon2, lat2])
    a = np.array([np.cos(p[1]) * np.cos(p[0]), np.cos(p[1]) * np.sin(p[0]), np.sin(p[1])])
    b = np.array([np.cos(p[3]) * np.cos(p[2]), np.cos(p[3]) * np.sin(p[2]), np.sin(p[3])])
    angle = float(np.arccos(np.clip(a @ b, -1, 1)))
    n = max(2, int(np.ceil(np.degrees(angle) / step)) + 1)
    t = np.linspace(0, 1, n)[:, None]
    if angle < 1e-9:
        xyz = a + t * (b - a)
    else:
        xyz = (np.sin((1 - t) * angle) * a + np.sin(t * angle) * b) / np.sin(angle)
    return np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0])), np.degrees(np.arcsin(np.clip(xyz[:, 2], -1, 1)))


def near_side(ax: GeoAxes, lon: np.ndarray, lat: np.ndarray, xy: np.ndarray | None = None) -> np.ndarray:
    """True for the points (lon, lat in degrees) on the side of the globe *ax* shows.

    The orthographic projection folds the far side onto the near one rather
    than failing, so a point counts only if projecting it there and back
    returns it (within ~1 km).
    """
    geodetic = ccrs.PlateCarree()
    if xy is None:
        xy = ax.projection.transform_points(geodetic, lon, lat)[:, :2]
    ok = np.all(np.isfinite(xy), axis=1)
    back = geodetic.transform_points(ax.projection, np.where(ok, xy[:, 0], 0), np.where(ok, xy[:, 1], 0))
    dlon = np.radians((back[:, 0] - lon + 180) % 360 - 180)
    p1, p2 = np.radians(lat), np.radians(back[:, 1])
    cos_d = np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(dlon)
    return ok & np.isfinite(cos_d) & (cos_d > np.cos(np.radians(0.01)))


def _width_scale(ax: GeoAxes) -> float:
    """Line widths grow with the map, as on a 15-inch globe."""
    return float(np.clip(ax.get_window_extent().width / ax.figure.dpi / 15, 0.4, 3))


def line_width(airlines: np.ndarray, scale: float = 1.0, crowded: bool = False) -> np.ndarray:
    """Line widths (points) of routes flown by *airlines* airlines: thinner where the whole network shows."""
    base, slope = (0.15, 0.3) if crowded else (0.3, 0.45)
    return (base + slope * np.sqrt(np.asarray(airlines, float))) * scale


def _line_alpha(airlines: np.ndarray, crowded: bool) -> np.ndarray:
    base, slope, top = (0.05, 0.04, 0.6) if crowded else (0.45, 0.05, 0.85)
    return np.clip(base + slope * np.asarray(airlines, float), base, top)


def draw_air_routes(ax: GeoAxes, routes: AirRoutes, labels: bool = True, limit: int = LABEL_MAX) -> int:
    """Draw *routes* on *ax* as great-circle arcs, wider and stronger the more airlines fly them; dot the airports
    and name the busiest (or the chosen ones and their destinations). Returns how many arcs show."""
    airports = routes.airports
    scale = _width_scale(ax)
    crowded = not routes.picked and not routes.carriers
    geodetic = ccrs.PlateCarree()
    colour_of = {i: PICKED_COLOURS[k % len(PICKED_COLOURS)] for k, i in enumerate(routes.picked)}
    segments, widths, colours = [], [], []
    shown = 0
    weights = line_width(routes.airlines, scale, crowded)
    alphas = _line_alpha(routes.airlines, crowded)
    arcs = [great_circle(float(airports.lon[a]), float(airports.lat[a]), float(airports.lon[b]),
                         float(airports.lat[b])) for a, b in zip(routes.a, routes.b)]
    lon = np.concatenate([arc[0] for arc in arcs] + [np.zeros(0)])
    lat = np.concatenate([arc[1] for arc in arcs] + [np.zeros(0)])
    xy = ax.projection.transform_points(geodetic, lon, lat)[:, :2]
    visible = near_side(ax, lon, lat, xy)
    starts = np.cumsum([0] + [len(arc[0]) for arc in arcs])
    for k in range(len(arcs)):
        part, ok = xy[starts[k]:starts[k + 1]], visible[starts[k]:starts[k + 1]]
        if not ok.any():
            continue
        # Pieces of the arc on the near side of the globe
        edges = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
        pieces = [part[s:e] for s, e in zip(edges[::2], edges[1::2]) if e - s >= 2]
        if not pieces:
            continue
        shown += 1
        rgba = list(_rgb(colour_of.get(int(routes.a[k]), ROUTE_COLOUR))) + [float(alphas[k])]
        for piece in pieces:
            segments.append(piece)
            widths.append(float(weights[k]))
            colours.append(rgba)
    if segments:
        ax.add_collection(LineCollection(segments, linewidths=widths, colors=colours, capstyle="round",
                                         joinstyle="round", zorder=7.6))
    _draw_airports(ax, routes, scale, labels, limit)
    logger.info("%s drawn: %d of %d arcs in view.", routes.title, shown, len(routes.a))
    return shown


def _rgb(colour: str) -> tuple[float, float, float]:
    from matplotlib.colors import to_rgb
    return to_rgb(colour)


def _draw_airports(ax: GeoAxes, routes: AirRoutes, scale: float, labels: bool, limit: int) -> None:
    """Dot each airport by how many places it is linked to, and name the chosen and then the busiest."""
    airports = routes.airports
    links: dict[int, int] = defaultdict(int)
    for a, b in zip(routes.a, routes.b):
        links[int(a)] += 1
        links[int(b)] += 1
    floor = CROWDED_MIN_LINKS if not routes.picked and not routes.carriers else 1
    candidates = [i for i in links if links[i] >= floor or i in routes.picked]
    near = near_side(ax, airports.lon[candidates], airports.lat[candidates]) if candidates else []
    seen = [i for i, ok in zip(candidates, near)
            if ok and on_globe(ax, float(airports.lon[i]), float(airports.lat[i]), margin=1.0)]
    if not seen:
        return
    seen.sort(key=lambda i: (i not in routes.picked, -links[i]))
    sizes = np.array([(36 if i in routes.picked else 1.5 + 3 * np.sqrt(links[i])) for i in seen]) * scale
    if not routes.picked:
        sizes = np.minimum(sizes, 60 * scale)
    lon = np.array([airports.lon[i] for i in seen])
    lat = np.array([airports.lat[i] for i in seen])
    ax.scatter(lon, lat, s=sizes, c=AIRPORT_COLOUR, edgecolors="white", linewidths=0.4 * scale, alpha=0.85,
               transform=ccrs.PlateCarree(), zorder=7.7)
    if not labels:
        return
    space = label_space(ax)
    named = 0
    for i, size in zip(seen, sizes):
        if named >= limit:
            break
        picked = i in routes.picked
        name = airports.cities[i] or airports.names[i]
        if picked and len([p for p in routes.picked if airports.cities[p] == airports.cities[i]]) > 1:
            name = airports.iata[i] or airports.icao[i] or name
        x, y = ax.projection.transform_point(float(airports.lon[i]), float(airports.lat[i]), ccrs.PlateCarree())
        font = (11 if picked else float(np.clip(6 + np.log2(links[i]) * 0.6, 6, 9.5))) * scale ** 0.5
        if not name or not space.claim(x, y, name, font, point=np.sqrt(size) * ax.figure.dpi / 72):
            continue
        named += 1
        ax.annotate(name, (x, y), xycoords="data", xytext=(0, 0.5 * np.sqrt(size) + 1), textcoords="offset points",
                    fontsize=font, fontweight="bold" if picked else "normal", color="#222222", ha="center",
                    va="bottom", zorder=8, annotation_clip=True,
                    path_effects=[pe.withStroke(linewidth=2.2, foreground="white")])


def add_air_routes_legend(ax: GeoAxes, routes: AirRoutes, shown: int, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the line widths (how many airlines fly a route) and, for chosen airports, their colours."""
    scale = _width_scale(ax)
    crowded = not routes.picked and not routes.carriers
    handles, labels = [], []
    if len(routes.picked) > 1:
        for k, i in enumerate(routes.picked):
            handles.append(Line2D([], [], color=PICKED_COLOURS[k % len(PICKED_COLOURS)], linewidth=2.5 * scale))
            labels.append(_airport_label(routes.airports, i))
    colour = PICKED_COLOURS[0] if routes.picked else ROUTE_COLOUR
    for n in KEY_AIRLINES:
        alpha = float(min(1.0, _line_alpha(np.array([n]), crowded)[0] + 0.25))
        handles.append(Line2D([], [], color=colour, alpha=alpha, linewidth=float(line_width(np.array([n]), scale, crowded)[0])))
        labels.append(f"{n} airline{'s' if n > 1 else ''}" + (" or more" if n == KEY_AIRLINES[-1] else ""))
    title = f"{routes.title}: {shown:,} in view, by how many airlines fly them (OpenFlights, 2014)"
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=min(len(handles), 6),
                    fontsize=10, title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=2.5, columnspacing=1.6, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
