"""High-speed rail tracks from OpenStreetMap, to draw the links of :mod:`high_speed_rail` along their line.

Data source
-----------
OpenStreetMap (https://www.openstreetmap.org/copyright), © OpenStreetMap
contributors, Open Database License (ODbL 1.0), through the Overpass API. The
tracks are the ways tagged ``railway=rail`` with ``highspeed=yes`` or a
``maxspeed`` of 200 km/h (125 mph) or more, and, for the planned lines, those
tagged ``railway=construction`` and high-speed. They are downloaded once per
region of :data:`REGIONS` (the ones a map shows links in), cached in
``~/.cache/ortho_tiles/high_speed_rail/`` and downloaded again after a year.

Routing
-------
The tracks are joined into a graph at the OSM nodes they share, thinned to a
node every ~200 m. As the tracks into a city's terminus are rarely tagged
high-speed, each station is joined to the nearest track in each direction
within :data:`SNAP_KM`, and the loose end of a track to the nearest other
track within :data:`GAP_KM`; those joins count :data:`JOIN_PENALTY` times their
length. A link follows the shortest way between its two stations, open track
only for an open link. It is drawn so only if that way is no more than
:data:`MAX_STRETCH` times as long as the line drawn city to city (plus
:data:`SLACK_KM`) and at least :data:`MIN_ON_TRACK` of it is on track; else
:mod:`high_speed_rail` draws it city to city as before.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

import incomplete

logger = logging.getLogger(__name__)

OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                 "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
DATA_VERSION = 1
MAX_AGE_DAYS = 365
EARTH_KM = 6371.0
THIN_KM = 0.2             # a node kept about this often along a track
SNAP_KM = 30.0            # how far from a station a track may start
GAP_KM = 3.0              # how far a track's loose end may be joined to another
JOIN_PENALTY = 1.5        # a join counts this many times its length
SECTORS = 8               # the directions a station is joined to the tracks in
MAX_STRETCH = 1.5
SLACK_KM = 20.0
MIN_ON_TRACK = 0.5

TRACKS_ATTRIBUTION = "tracks © OpenStreetMap contributors, ODbL"

# The regions downloaded apart (south, west, north, east): together they hold every link of high_speed_rail
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "western-europe": (42.0, -5.5, 54.0, 8.5),
    "iberia": (36.0, -9.5, 44.0, 3.5),
    "central-europe": (45.5, 8.5, 55.0, 24.0),
    "alps-italy": (40.0, 6.5, 48.0, 16.5),
    "baltic-balkans": (44.0, 18.0, 60.0, 26.5),
    "russia": (55.0, 29.0, 60.5, 38.5),
    "turkey": (36.5, 28.0, 41.5, 38.0),
    "morocco": (33.0, -8.5, 36.0, -5.0),
    "arabia": (21.0, 38.5, 25.0, 40.5),
    "uzbekistan": (39.0, 64.0, 42.0, 70.0),
    "india": (18.5, 72.0, 23.5, 73.5),
    "china-north": (34.0, 100.0, 47.0, 127.0),
    "china-south": (18.0, 100.0, 34.5, 122.5),
    "korea": (34.0, 126.0, 38.5, 130.0),
    "japan": (31.0, 129.5, 42.5, 142.0),
    "taiwan": (22.0, 119.5, 25.5, 122.0),
    "java": (-7.5, 106.0, -5.5, 108.5),
    "us-northeast": (38.5, -78.0, 43.0, -70.5),
}

TIMEOUT = 300             # seconds a server may take to answer a region
STATUS_TIMEOUT = 20       # seconds a server may take to say it is there
QUERY = """[out:json][timeout:300][maxsize:1073741824];
(
  way["railway"="rail"]["highspeed"="yes"]({box});
  way["railway"="rail"]["maxspeed"~"^[2-3][0-9][0-9]$"]({box});
  way["railway"="rail"]["maxspeed"~"^1[2-9][0-9] mph$"]({box});
  way["railway"="construction"]["highspeed"="yes"]({box});
  way["railway"="construction"]["construction:highspeed"="yes"]({box});
);
out geom;"""


class TrackDataError(RuntimeError):
    """OSM's tracks could not be downloaded or read."""


class Ways(NamedTuple):
    """OSM ways: way *k* is nodes ``starts[k]:starts[k + 1]`` of *nodes*, *lon* and *lat*."""
    ids: np.ndarray            # way ids
    starts: np.ndarray
    nodes: np.ndarray          # node ids
    lon: np.ndarray
    lat: np.ndarray
    construction: np.ndarray   # True for a way being built


def empty_ways() -> Ways:
    return Ways(np.zeros(0, np.int64), np.zeros(1, np.int64), np.zeros(0, np.int64), np.zeros(0), np.zeros(0),
                np.zeros(0, bool))


def query(box: tuple[float, float, float, float]) -> str:
    """The Overpass query for the high-speed tracks in *box* (south, west, north, east)."""
    return QUERY.replace("{box}", ",".join(f"{v:g}" for v in box))


def fetch_overpass(text: str, urls: Sequence[str] = OVERPASS_URLS, timeout: float = TIMEOUT,
                   dead: set[str] | None = None) -> list[dict[str, Any]]:
    """The elements Overpass returns for *text*, from the first server that answers; a server that does not is
    added to *dead* and not asked again. Each server is first asked for its status, so one that is down or
    does not answer is passed over in seconds. Raises :class:`TrackDataError`."""
    from shipping import _tls

    data = urllib.parse.urlencode({"data": text}).encode()
    errors = []
    dead = set() if dead is None else dead
    if all(url in dead for url in urls):
        raise TrackDataError("no Overpass server answered earlier in this run")
    for url in urls:
        if url in dead:
            continue
        try:
            status = urllib.request.Request(url.rsplit("/", 1)[0] + "/status", headers={"User-Agent": "ortho/1.0"})
            with urllib.request.urlopen(status, timeout=STATUS_TIMEOUT, context=_tls()) as resp:
                resp.read()
            request = urllib.request.Request(url, data=data, headers={"User-Agent": "ortho/1.0"})
            with urllib.request.urlopen(request, timeout=timeout, context=_tls()) as resp:
                return list(json.load(resp)["elements"])
        except (OSError, http.client.HTTPException, ValueError, KeyError) as exc:  # a reply cut short too
            logger.info("Overpass at %s did not answer: %s", url, exc)
            errors.append(f"{urllib.parse.urlparse(url).netloc}: {exc}")
            dead.add(url)
    raise TrackDataError("no Overpass server answered (" + "; ".join(errors) + ")")


def parse(elements: Iterable[Mapping[str, Any]]) -> Ways:
    """The ways in Overpass's *elements* (``out geom``), each with its nodes' ids and places."""
    ids, starts, nodes, lon, lat, building = [], [0], [], [], [], []
    for e in elements:
        if e.get("type") != "way":
            continue
        refs, points = e.get("nodes") or [], e.get("geometry") or []
        pairs = [(int(r), p) for r, p in zip(refs, points) if p]   # a node outside the box comes as null
        if len(pairs) < 2 or len(refs) != len(points):
            continue
        ids.append(int(e["id"]))
        nodes += [r for r, _ in pairs]
        lon += [float(p["lon"]) for _, p in pairs]
        lat += [float(p["lat"]) for _, p in pairs]
        starts.append(len(nodes))
        building.append((e.get("tags") or {}).get("railway") == "construction")
    return Ways(np.array(ids, np.int64), np.array(starts, np.int64), np.array(nodes, np.int64), np.array(lon),
                np.array(lat), np.array(building, bool))


def merge(parts: Sequence[Ways]) -> Ways:
    """The ways of *parts*, each once (a way across two regions is in both)."""
    seen: set[int] = set()
    ids, starts, nodes, lon, lat, building = [], [0], [], [], [], []
    for ways in parts:
        for k, way in enumerate(ways.ids.tolist()):
            if way in seen:
                continue
            seen.add(way)
            s, e = ways.starts[k], ways.starts[k + 1]
            ids.append(way)
            nodes.append(ways.nodes[s:e])
            lon.append(ways.lon[s:e])
            lat.append(ways.lat[s:e])
            starts.append(starts[-1] + e - s)
            building.append(bool(ways.construction[k]))
    if not ids:
        return empty_ways()
    return Ways(np.array(ids, np.int64), np.array(starts, np.int64), np.concatenate(nodes), np.concatenate(lon),
                np.concatenate(lat), np.array(building, bool))


def select(ways: Ways, keep: np.ndarray) -> Ways:
    """The ways of *ways* where *keep* holds."""
    lengths = np.diff(ways.starts)
    nodes = np.repeat(keep, lengths)
    return Ways(ways.ids[keep], np.concatenate([[0], np.cumsum(lengths[keep])]).astype(np.int64),
                ways.nodes[nodes], ways.lon[nodes], ways.lat[nodes], ways.construction[keep])


def default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "high_speed_rail")


def _save(path: str, ways: Ways) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part.npz"
    np.savez_compressed(part, **ways._asdict())
    os.replace(part, path)


def _read(path: str) -> Ways:
    with np.load(path) as saved:
        return Ways(*(saved[f] for f in Ways._fields))


def load_region(name: str, cache_dir: str | None = None,
                fetch: Callable[[str], list[dict[str, Any]]] = fetch_overpass,
                regions: Mapping[str, tuple[float, float, float, float]] = REGIONS) -> Ways:
    """The tracks of region *name*: cached, or downloaded (again after :data:`MAX_AGE_DAYS`, the old copy kept if
    that fails). Raises :class:`TrackDataError`."""
    path = os.path.join(cache_dir or default_cache_dir(), f"{name}_v{DATA_VERSION}.npz")
    stale = None
    if os.path.isfile(path):
        try:
            ways = _read(path)
        except (OSError, ValueError, KeyError) as exc:
            logger.info("Reading the cached tracks of %s again: %s", name, exc)
        else:
            if time.time() - os.path.getmtime(path) < MAX_AGE_DAYS * 86400:
                return ways
            stale = ways
    logger.info("Downloading OSM's high-speed tracks of %s (one-time) …", name)
    try:
        ways = parse(fetch(query(regions[name])))
    except TrackDataError:
        if stale is not None:
            logger.info("Keeping last year's tracks of %s: the Overpass API did not answer.", name)
            return stale
        raise
    _save(path, ways)
    logger.info("OSM's high-speed tracks of %s: %d ways.", name, len(ways.ids))
    return ways


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


def _xyz(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    lo, la = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def distance_km(lon1: Any, lat1: Any, lon2: Any, lat2: Any) -> np.ndarray:
    """Great-circle distances (km)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlon, dlat = np.radians(np.asarray(lon2) - lon1), p2 - p1
    h = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def path_km(lon: np.ndarray, lat: np.ndarray) -> float:
    """The length (km) of a path through the points (lon, lat)."""
    return float(distance_km(lon[:-1], lat[:-1], lon[1:], lat[1:]).sum()) if len(lon) > 1 else 0.0


class Graph:
    """The tracks of *ways* as a graph, the *stations* (name -> (lon, lat)) joined to them."""

    def __init__(self, ways: Ways, stations: Mapping[str, tuple[float, float]]):
        lengths = np.diff(ways.starts)
        way_of = np.repeat(np.arange(len(lengths)), lengths)
        first = np.zeros(len(ways.nodes), bool)
        first[ways.starts[:-1]] = True
        last = np.zeros(len(ways.nodes), bool)
        last[ways.starts[1:] - 1] = True
        step = np.where(first, 0.0, distance_km(np.roll(ways.lon, 1), np.roll(ways.lat, 1), ways.lon, ways.lat))
        along = np.cumsum(step)
        along -= np.repeat(along[ways.starts[:-1]], lengths) if len(ways.nodes) else 0
        _, inverse, counts = np.unique(ways.nodes, return_inverse=True, return_counts=True)
        bucket = np.floor(along / THIN_KM)
        keep = first | last | (counts[inverse] > 1) | (bucket != np.roll(bucket, 1))
        # The vertices: the nodes kept, one per OSM node
        kept_ids, vertex_of_kept = np.unique(ways.nodes[keep], return_inverse=True)
        n = len(kept_ids)
        index_kept = np.flatnonzero(keep)
        self.lon = np.zeros(n)
        self.lat = np.zeros(n)
        self.lon[vertex_of_kept] = ways.lon[index_kept]
        self.lat[vertex_of_kept] = ways.lat[index_kept]
        # The track edges: each two nodes kept one after the other on a way
        same_way = way_of[index_kept[1:]] == way_of[index_kept[:-1]]
        u, v = vertex_of_kept[:-1][same_way], vertex_of_kept[1:][same_way]
        w = (along[index_kept[1:]] - along[index_kept[:-1]])[same_way]
        ok = u != v
        u, v, w = u[ok], v[ok], np.maximum(w[ok], 1e-6)
        rows, cols, weights = [u], [v], [w]
        if n:
            _, component = connected_components(coo_matrix((w, (u, v)), shape=(n, n)), directed=False)
            tree = cKDTree(_xyz(self.lon, self.lat))
            degree = np.bincount(np.concatenate([u, v]), minlength=n)
            # A track's loose end, joined to the nearest track of each other piece of the network nearby
            ends = np.flatnonzero(degree == 1)
            gaps = self._joins(tree, component, self.lon[ends], self.lat[ends], GAP_KM, ends, sectors=1)
            rows.append(gaps[0])
            cols.append(gaps[1])
            weights.append(gaps[2])
        else:
            tree, component = None, np.zeros(0, int)
        # The stations, vertices after the tracks', each joined to the tracks around it
        names = list(stations)
        self.station = {name: n + i for i, name in enumerate(names)}
        self.lon = np.concatenate([self.lon, [stations[s][0] for s in names]])
        self.lat = np.concatenate([self.lat, [stations[s][1] for s in names]])
        if tree is not None and names:
            joins = self._joins(tree, component, self.lon[n:], self.lat[n:], SNAP_KM, n + np.arange(len(names)),
                                sectors=SECTORS)
            rows.append(joins[0])
            cols.append(joins[1])
            weights.append(joins[2])
        u, v, w = np.concatenate(rows), np.concatenate(cols), np.concatenate(weights)
        # Both ways, the shortest edge between two vertices
        u, v, w = np.concatenate([u, v]), np.concatenate([v, u]), np.concatenate([w, w])
        order = np.lexsort((w, v, u))
        u, v, w = u[order], v[order], w[order]
        first_of = np.ones(len(u), bool)
        first_of[1:] = (u[1:] != u[:-1]) | (v[1:] != v[:-1])
        size = len(self.lon)
        self.matrix = coo_matrix((w[first_of], (u[first_of], v[first_of])), shape=(size, size)).tocsr()
        self.tracks = n

    def _joins(self, tree: cKDTree, component: np.ndarray, lon: np.ndarray, lat: np.ndarray, reach_km: float,
               sources: np.ndarray, sectors: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Edges from each of *sources* (at *lon*, *lat*) to the nearest track vertex within *reach_km* in each
        of *sectors* directions on each piece of the network, but the source's own piece."""
        rows, cols, weights = [], [], []
        chord = 2 * np.sin(reach_km / EARTH_KM / 2)
        own = np.where(sources < len(component), component[np.minimum(sources, len(component) - 1)], -1)
        for k, near in enumerate(tree.query_ball_point(_xyz(lon, lat), chord)):
            near = np.array([j for j in near if component[j] != own[k]], int)
            if not len(near):
                continue
            d = distance_km(lon[k], lat[k], self.lon[near], self.lat[near])
            bearing = np.degrees(np.arctan2(self.lon[near] - lon[k],
                                            (self.lat[near] - lat[k]) / max(np.cos(np.radians(lat[k])), 0.1)))
            sector = np.floor((bearing + 180) / 360 * sectors).astype(int) % sectors
            best: dict[tuple[int, int], int] = {}
            for j in np.argsort(d):
                best.setdefault((int(component[near[j]]), int(sector[j])), int(j))
            for j in best.values():
                rows.append(int(sources[k]))
                cols.append(int(near[j]))
                weights.append(JOIN_PENALTY * max(float(d[j]), 1e-3))
        return np.array(rows, int), np.array(cols, int), np.array(weights, float)

    def route(self, a: str, b: str, limit_km: float) -> tuple[np.ndarray, np.ndarray, float] | None:
        """The shortest way from station *a* to *b* (lon, lat) and the share of it on track; None if there is
        none within *limit_km* (counting joins at their penalty)."""
        if a not in self.station or b not in self.station:
            return None
        src, dst = self.station[a], self.station[b]
        dist, previous = dijkstra(self.matrix, indices=src, return_predecessors=True, limit=limit_km)
        if not np.isfinite(dist[dst]):
            return None
        path = [dst]
        while path[-1] != src:
            path.append(int(previous[path[-1]]))
        path.reverse()
        p = np.array(path)
        lon, lat = self.lon[p], self.lat[p]
        steps = distance_km(lon[:-1], lat[:-1], lon[1:], lat[1:])
        weights = np.asarray(self.matrix[p[:-1], p[1:]]).ravel()
        on_track = (p[:-1] < self.tracks) & (p[1:] < self.tracks) & (weights < 1.25 * steps + 1e-3)
        total = float(steps.sum())
        return lon, lat, (float(steps[on_track].sum()) / total if total > 0 else 0.0)


def regions_for(lon: np.ndarray, lat: np.ndarray, margin: float = 0.5,
                regions: Mapping[str, tuple[float, float, float, float]] = REGIONS) -> list[str]:
    """The regions the path (lon, lat) runs through, within *margin* degrees."""
    s, w, n, e = float(np.min(lat)) - margin, float(np.min(lon)) - margin, float(np.max(lat)) + margin, \
        float(np.max(lon)) + margin
    return [name for name, (rs, rw, rn, re) in regions.items() if rs < n and s < rn and rw < e and w < re]


class TrackRouter:
    """Routes the links between *stations* (name -> (lon, lat)) along OSM's tracks, the regions downloaded as
    the links in view need them."""

    def __init__(self, stations: Mapping[str, tuple[float, float]], cache_dir: str | None = None,
                 fetch: Callable[[str], list[dict[str, Any]]] | None = None,
                 regions: Mapping[str, tuple[float, float, float, float]] = REGIONS):
        self.stations = dict(stations)
        self.cache_dir = cache_dir
        self.dead: set[str] = set()          # the Overpass servers that did not answer, not asked again
        self.fetch = fetch or (lambda text: fetch_overpass(text, dead=self.dead))
        self.regions = regions
        self.loaded: dict[str, Ways] = {}
        self.failed: dict[str, str] = {}
        self.graphs: dict[tuple[frozenset[str], bool], Graph] = {}
        self.routes: dict[tuple[str, str, bool], tuple[np.ndarray, np.ndarray] | None] = {}
        self.used = False                    # whether a link was drawn along OSM's tracks

    def prepare(self, paths: Iterable[tuple[np.ndarray, np.ndarray]]) -> None:
        """Download the regions the *paths* (lon, lat) run through, at once."""
        for name in dict.fromkeys(r for lon, lat in paths for r in regions_for(lon, lat, regions=self.regions)):
            self._region(name)

    def _region(self, name: str) -> bool:
        if name in self.loaded:
            return True
        if name in self.failed:
            return False
        try:
            self.loaded[name] = load_region(name, self.cache_dir, self.fetch, self.regions)
            return True
        except (TrackDataError, OSError) as exc:
            self.failed[name] = str(exc)
            incomplete.note(logger, "Drawing the high-speed lines of %s from city to city: OSM's tracks could not "
                                    "be downloaded (%s)", name, exc)
            return False

    def _graph(self, names: list[str], planned: bool) -> Graph:
        key = (frozenset(names), planned)
        if key not in self.graphs:
            ways = merge([self.loaded[n] for n in sorted(names)])
            if not planned:
                ways = select(ways, ~ways.construction)
            boxes = [self.regions[n] for n in names]
            stations = {s: p for s, p in self.stations.items()
                        if any(b[0] - 1 <= p[1] <= b[2] + 1 and b[1] - 1 <= p[0] <= b[3] + 1 for b in boxes)}
            self.graphs[key] = Graph(ways, stations)
        return self.graphs[key]

    def route(self, a: str, b: str, planned: bool, lon: np.ndarray, lat: np.ndarray
              ) -> tuple[np.ndarray, np.ndarray] | None:
        """The way along the tracks (lon, lat) from station *a* to *b*, a link drawn city to city along *lon*,
        *lat*; None to keep that (no tracks there, or a way too long or too little on track)."""
        key = (a, b, planned)
        if key in self.routes:
            return self.routes[key]
        found = None
        names = regions_for(lon, lat, regions=self.regions)
        if names and all(self._region(n) for n in names):
            schematic = path_km(lon, lat)
            limit = JOIN_PENALTY * (MAX_STRETCH * schematic + SLACK_KM)
            way = self._graph(names, planned).route(a, b, limit)
            if way is not None:
                track_lon, track_lat, on_track = way
                if on_track >= MIN_ON_TRACK and path_km(track_lon, track_lat) <= MAX_STRETCH * schematic + SLACK_KM:
                    found = (track_lon, track_lat)
                else:
                    logger.info("High-speed rail %s-%s drawn city to city: OSM's way is %.0f km, %.0f%% on track.",
                                a, b, path_km(track_lon, track_lat), 100 * on_track)
        self.routes[key] = found
        self.used |= found is not None
        return found
