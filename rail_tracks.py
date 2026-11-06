"""High-speed rail tracks from OpenStreetMap, to draw the links of :mod:`high_speed_rail` along their line.

Data source
-----------
OpenStreetMap (https://www.openstreetmap.org/copyright), © OpenStreetMap
contributors, Open Database License (ODbL 1.0), through the Overpass API. The
tracks are the ways tagged ``railway=rail`` with ``highspeed=yes`` or a
``maxspeed`` of 200 km/h (125 mph) or more, the Channel Tunnel, slower but the
only way from Britain to the continent, and, for the planned lines, those
tagged ``railway=construction`` and high-speed. They are downloaded once per
region, a square of a grid :data:`REGION_DEG` degrees a side, for the regions a
map's links pass through; each is cached as it comes, in
``~/.cache/ortho_tiles/high_speed_rail/``, so a run cut short carries on where
it stopped, and downloaded again after a year.

The Overpass servers are busy and fail often, in several ways: a 502 or 504 in
under a second, no answer at all, or a 200 with a ``remark`` that the query ran
out of time and as many ways as it had by then; and one, as of November 2026,
answers a query along a line with nothing and no remark. So a reply with a
remark, or with nothing along a line, counts as a failure, the servers are asked in turn and again after :data:`RETRY_WAIT`,
and only :data:`PATIENCE` seconds of failures in a row end the downloads of a
run (:class:`Overpass`); a link is then drawn city to city if a region it passes
through is missing, whatever becomes of the others.

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
:data:`SLACK_KM`) and at least :data:`MIN_ON_TRACK` of it is on track.

Many open links run partly on slower track, so an open link with no such way
tries again with every railway but sidings and yards within :data:`CORRIDOR_KM`
of its path, downloaded and cached for that link alone. A planned link does not:
the old line beside it is not the one being built. If that fails too,
:mod:`high_speed_rail` draws the link city to city as before.
"""

from __future__ import annotations

import hashlib
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
from scipy.sparse import coo_matrix, csr_matrix
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

REGION_DEG = 4            # the side of a region, a square of the grid: larger ones keep timing out
QUERY_TIMEOUT = 180       # seconds a server may spend on a region
TIMEOUT = 240             # seconds to wait for its answer: the above, and the queue before it
RETRY_WAIT = 30           # seconds before asking every server again
PATIENCE = 600            # seconds of failures in a row before no more is asked in this run

CORRIDOR_KM = 15          # how far from a link's path the ordinary tracks are taken, for a link with no fast way

# Each statement with a box scans the railways in it, so they are scanned once, then filtered
QUERY = f"""[out:json][timeout:{QUERY_TIMEOUT}];
way["railway"="rail"]({{box}})->.open;
way["railway"="construction"]({{box}})->.building;
(
  way.open["highspeed"="yes"];
  way.open["maxspeed"~"^[2-3][0-9][0-9]$"];
  way.open["maxspeed"~"^1[2-9][0-9] mph$"];
  way.open["name"~"Channel Tunnel"];
  way.building["highspeed"="yes"];
  way.building["construction:highspeed"="yes"];
);
out geom;"""

CORRIDOR_QUERY = f"""[out:json][timeout:{QUERY_TIMEOUT}];
way["railway"="rail"][!"service"](around:{CORRIDOR_KM * 1000},{{line}});
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


def query(region: tuple[int, int]) -> str:
    """The Overpass query for the high-speed tracks in *region*, the square with this (south, west) corner."""
    south, west = region
    return QUERY.replace("{box}", f"{south},{west},{south + REGION_DEG},{west + REGION_DEG}")


def corridor_query(lon: np.ndarray, lat: np.ndarray) -> str:
    """The Overpass query for the railways, sidings and yards apart, within :data:`CORRIDOR_KM` of a link's path."""
    return CORRIDOR_QUERY.replace("{line}", ",".join(f"{y:.4f},{x:.4f}" for x, y in zip(lon, lat)))


class Overpass:
    """The Overpass servers, for one run: called with a query, it gives the elements of the first full answer.

    The server that last answered is asked first. One that fails goes to the back of the line, and when all have
    failed they are asked again after :data:`RETRY_WAIT`. After *patience* seconds of failures with no answer it
    raises :class:`TrackDataError`, and at once from then on, so that a map does not wait on each region in turn.
    """

    def __init__(self, urls: Sequence[str] = OVERPASS_URLS, timeout: float = TIMEOUT, patience: float = PATIENCE):
        self.urls = list(urls)
        self.timeout = timeout
        self.patience = patience
        self.failing = 0.0                   # seconds of failures since the last answer
        self.errors: dict[str, str] = {}     # each server's last

    def __call__(self, text: str) -> list[dict[str, Any]]:
        data = urllib.parse.urlencode({"data": text}).encode()
        while self.failing < self.patience:
            for url in list(self.urls):
                started = time.monotonic()
                try:
                    elements = self._ask(url, data)
                    if not elements and "around:" in text:   # a link's stations have railways by them, so a server
                        raise ValueError("no railways along the line")   # with none could not read the query
                except (OSError, http.client.HTTPException, ValueError, KeyError) as exc:  # a reply cut short too
                    logger.info("Overpass at %s did not answer: %s", url, exc)
                    self.errors[urllib.parse.urlparse(url).netloc] = str(exc)
                    self.failing += time.monotonic() - started
                    self.urls.remove(url)
                    self.urls.append(url)
                else:
                    self.failing = 0.0
                    return elements
            time.sleep(RETRY_WAIT)
            self.failing += RETRY_WAIT
        raise TrackDataError("no Overpass server answered ("
                             + "; ".join(f"{server}: {error}" for server, error in self.errors.items()) + ")")

    def _ask(self, url: str, data: bytes) -> list[dict[str, Any]]:
        from shipping import _tls

        request = urllib.request.Request(url, data=data, headers={"User-Agent": "ortho/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout, context=_tls()) as resp:
            reply = json.load(resp)
        if reply.get("remark"):              # a timeout comes as 200 OK, with the ways found so far
            raise ValueError(reply["remark"])
        return list(reply["elements"])


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


def _cached(path: str, what: str, text: str, fetch: Callable[[str], list[dict[str, Any]]]) -> Ways:
    """The ways the Overpass query *text* finds: cached at *path*, or downloaded (again after
    :data:`MAX_AGE_DAYS`, the old copy kept if that fails). Raises :class:`TrackDataError`."""
    stale = None
    if os.path.isfile(path):
        try:
            ways = _read(path)
        except (OSError, ValueError, KeyError) as exc:
            logger.info("Reading the cached %s again: %s", what, exc)
        else:
            if time.time() - os.path.getmtime(path) < MAX_AGE_DAYS * 86400:
                return ways
            stale = ways
    logger.info("Downloading %s from OSM (one-time) …", what)
    try:
        ways = parse(fetch(text))
    except TrackDataError:
        if stale is not None:
            logger.info("Keeping last year's %s: the Overpass API did not answer.", what)
            return stale
        raise
    _save(path, ways)
    logger.info("%s from OSM: %d ways.", what.capitalize(), len(ways.ids))
    return ways


def load_region(region: tuple[int, int], cache_dir: str | None,
                fetch: Callable[[str], list[dict[str, Any]]]) -> Ways:
    """The high-speed tracks of *region* (see :func:`query`), through :func:`_cached`."""
    south, west = region
    path = os.path.join(cache_dir or default_cache_dir(), f"{south}_{west}_v{DATA_VERSION}.npz")
    return _cached(path, f"the high-speed tracks of the {REGION_DEG}-degree square at {region}", query(region), fetch)


def load_corridor(a: str, b: str, lon: np.ndarray, lat: np.ndarray, cache_dir: str | None,
                  fetch: Callable[[str], list[dict[str, Any]]]) -> Ways:
    """The railways around the path (lon, lat) of the link from *a* to *b* (see :func:`corridor_query`), through
    :func:`_cached`, under a name from the query, so that a path changed in the data is downloaded anew."""
    text = corridor_query(lon, lat)
    digest = hashlib.sha1(text.encode()).hexdigest()[:16]
    path = os.path.join(cache_dir or default_cache_dir(), f"corridor_{digest}_v{DATA_VERSION}.npz")
    return _cached(path, f"the railways along {a}-{b}", text, fetch)


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
            track = coo_matrix((w, (u, v)), shape=(n, n)).tocsr()
            track = track + track.T
            tree = cKDTree(_xyz(self.lon, self.lat))
            degree = np.bincount(np.concatenate([u, v]), minlength=n)
            # A track's loose end, joined to the nearest track of each other piece of the tracks nearby
            ends = np.flatnonzero(degree == 1)
            gaps = self._joins(tree, track, self.lon[ends], self.lat[ends], GAP_KM, ends, sectors=1)
            rows.append(gaps[0])
            cols.append(gaps[1])
            weights.append(gaps[2])
        else:
            tree, track = None, None
        # The stations, vertices after the tracks', each joined to the tracks around it
        names = list(stations)
        self.station = {name: n + i for i, name in enumerate(names)}
        self.lon = np.concatenate([self.lon, [stations[s][0] for s in names]])
        self.lat = np.concatenate([self.lat, [stations[s][1] for s in names]])
        if tree is not None and names:
            joins = self._joins(tree, track, self.lon[n:], self.lat[n:], SNAP_KM, n + np.arange(len(names)),
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

    def _joins(self, tree: cKDTree, track: csr_matrix, lon: np.ndarray, lat: np.ndarray, reach_km: float,
               sources: np.ndarray, sectors: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Edges from each of *sources* (at *lon*, *lat*) to the nearest track vertex within *reach_km* in each
        of *sectors* directions on each piece of the tracks within that reach, but the source's own piece.

        The pieces are those within the reach, not of the whole network: with a country's tracks in one graph
        nearly all are one piece, joined somewhere far off, and two lines side by side would count as one."""
        rows, cols, weights = [], [], []
        chord = 2 * np.sin(reach_km / EARTH_KM / 2)
        place = np.full(track.shape[0], -1)      # where each vertex is among those near the source
        for k, found in enumerate(tree.query_ball_point(_xyz(lon, lat), chord)):
            near = np.array(found, int)
            if not len(near):
                continue
            place[near] = np.arange(len(near))
            edges = track[near].tocoo()
            within = place[edges.col] >= 0
            local = coo_matrix((np.ones(int(within.sum())), (edges.row[within], place[edges.col[within]])),
                               shape=(len(near), len(near)))
            _, piece = connected_components(local, directed=False)
            if sources[k] < track.shape[0]:
                others = piece != piece[place[sources[k]]]
                place[near] = -1
                near, piece = near[others], piece[others]
                if not len(near):
                    continue
            else:
                place[near] = -1
            d = distance_km(lon[k], lat[k], self.lon[near], self.lat[near])
            bearing = np.degrees(np.arctan2(self.lon[near] - lon[k],
                                            (self.lat[near] - lat[k]) / max(np.cos(np.radians(lat[k])), 0.1)))
            sector = np.floor((bearing + 180) / 360 * sectors).astype(int) % sectors
            best: dict[tuple[int, int], int] = {}
            for j in np.argsort(d):
                best.setdefault((int(piece[j]), int(sector[j])), int(j))
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


def regions_for(lon: np.ndarray, lat: np.ndarray, margin: float = 0.5) -> list[tuple[int, int]]:
    """The regions (south, west) the path (lon, lat) passes through or within *margin* degrees of."""
    regions: dict[tuple[int, int], None] = {}
    for dlat, dlon in ((-margin, -margin), (-margin, margin), (margin, -margin), (margin, margin)):
        south = np.floor((lat + dlat) / REGION_DEG).astype(int) * REGION_DEG
        west = np.floor((lon + dlon) / REGION_DEG).astype(int) * REGION_DEG
        regions.update(dict.fromkeys(zip(south.tolist(), west.tolist())))
    return list(regions)


class TrackRouter:
    """Routes the links between *stations* (name -> (lon, lat)) along OSM's tracks, the regions downloaded as
    the links in view need them."""

    def __init__(self, stations: Mapping[str, tuple[float, float]], cache_dir: str | None = None,
                 fetch: Callable[[str], list[dict[str, Any]]] | None = None):
        self.stations = dict(stations)
        self.cache_dir = cache_dir
        self.fetch = fetch or Overpass()
        self.loaded: dict[tuple[int, int], Ways] = {}
        self.failed: dict[tuple[int, int], str] = {}
        self.noted: set[tuple[int, int]] = set()   # the failures already warned of
        self.corridor_noted = False                # whether a failed download of a link's railways was
        self.graphs: dict[bool, Graph] = {}        # of the regions loaded, with the tracks being built or without
        self.routes: dict[tuple[str, str, bool], tuple[np.ndarray, np.ndarray] | None] = {}
        self.used = False                          # whether a link was drawn along OSM's tracks

    def prepare(self, paths: Iterable[tuple[np.ndarray, np.ndarray]]) -> None:
        """Download the regions the *paths* (lon, lat) run through, at once."""
        regions = list(dict.fromkeys(r for lon, lat in paths for r in regions_for(lon, lat)))
        for region in regions:
            self._region(region)
        self._note_failures(len(regions))

    def _region(self, region: tuple[int, int]) -> bool:
        if region in self.loaded:
            return True
        if region in self.failed:
            return False
        try:
            self.loaded[region] = load_region(region, self.cache_dir, self.fetch)
            self.graphs.clear()
            return True
        except (TrackDataError, OSError) as exc:
            self.failed[region] = str(exc)
            return False

    def _note_failures(self, wanted: int) -> None:
        """One warning for the regions that failed since the last, however many they are."""
        new = [region for region in self.failed if region not in self.noted]
        if new:
            self.noted.update(new)
            incomplete.note(logger, "Drawing the high-speed lines through %d of %d regions from city to city: OSM's "
                                    "tracks could not be downloaded (%s)", len(new), wanted, self.failed[new[-1]])

    def _graph(self, planned: bool) -> Graph:
        if planned not in self.graphs:
            ways = merge([self.loaded[region] for region in sorted(self.loaded)])
            if not planned:
                ways = select(ways, ~ways.construction)
            self.graphs[planned] = Graph(ways, self.stations)
        return self.graphs[planned]

    def route(self, a: str, b: str, planned: bool, lon: np.ndarray, lat: np.ndarray
              ) -> tuple[np.ndarray, np.ndarray] | None:
        """The way along the tracks (lon, lat) from station *a* to *b*, a link drawn city to city along *lon*,
        *lat*; None to keep that (no tracks there, or a way too long or too little on track)."""
        key = (a, b, planned)
        if key in self.routes:
            return self.routes[key]
        found = None
        regions = regions_for(lon, lat)
        if all(self._region(region) for region in regions):
            found = self._follow(self._graph(planned), a, b, lon, lat)
            if found is None and not planned:
                found = self._along_the_railways(a, b, lon, lat, regions)
            if found is None:
                logger.info("High-speed rail %s-%s drawn city to city.", a, b)
        self._note_failures(len(regions))
        self.routes[key] = found
        self.used |= found is not None
        return found

    def _follow(self, graph: Graph, a: str, b: str, lon: np.ndarray, lat: np.ndarray
                ) -> tuple[np.ndarray, np.ndarray] | None:
        """The way from *a* to *b* on *graph* (lon, lat); None if there is none near enough the path (lon, lat)."""
        longest = MAX_STRETCH * path_km(lon, lat) + SLACK_KM
        way = graph.route(a, b, JOIN_PENALTY * longest)
        if way is None:
            return None
        track_lon, track_lat, on_track = way
        if on_track >= MIN_ON_TRACK and path_km(track_lon, track_lat) <= longest:
            return track_lon, track_lat
        logger.info("High-speed rail %s-%s: OSM's way is %.0f km, %.0f%% on track.",
                    a, b, path_km(track_lon, track_lat), 100 * on_track)
        return None

    def _along_the_railways(self, a: str, b: str, lon: np.ndarray, lat: np.ndarray,
                            regions: Sequence[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray] | None:
        """The way of an open link with no fast one, on the fast tracks of its *regions* and the railways around it."""
        try:
            corridor = load_corridor(a, b, lon, lat, self.cache_dir, self.fetch)
        except (TrackDataError, OSError) as exc:
            if not self.corridor_noted:
                self.corridor_noted = True
                incomplete.note(logger, "Drawing some high-speed links from city to city: OSM's railways around "
                                        "them could not be downloaded (%s)", exc)
            return None
        ways = merge([self.loaded[region] for region in regions] + [corridor])
        stations = {name: self.stations[name] for name in (a, b) if name in self.stations}
        return self._follow(Graph(select(ways, ~ways.construction), stations), a, b, lon, lat)
