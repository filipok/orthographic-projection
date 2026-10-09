"""Orthographic globes with any compass direction at the top.

Cartopy's Orthographic projection always puts north up. A rotated view is the
same orthographic projection of a rotated sphere, which PROJ provides as an
oblique transformation (``ob_tran``). The sphere is rotated so that:

- the view centre lies on the rotated equator at rotated longitude 0, where
  ``o_proj=ortho`` centres its view, and
- the rotated north pole is the point 90° from the centre along the chosen
  bearing, so that bearing points straight up on the map.

PROJ's rotated-pole convention: ``o_lat_p`` is the new pole's latitude,
``lon_0`` its longitude plus 180°, and ``o_lon_p`` spins the frame about it.

``ob_tran`` works on a sphere, so rotated globes use the mean Earth radius
instead of the WGS-84 ellipsoid: a difference far below a pixel at globe scale.
"""

from __future__ import annotations

import math

import numpy as np
import shapely.geometry as sgeom
from pyproj import CRS, Transformer
from pyproj.geod import Geod

import cartopy.crs as ccrs

# IUGG mean Earth radius (m)
EARTH_RADIUS_M = 6_371_008.8

_SPHERE = Geod(a=EARTH_RADIUS_M, f=0)
_QUARTER_TURN_M = math.pi / 2 * EARTH_RADIUS_M


def normalise_bearing(bearing: float) -> float:
    """*bearing* in degrees, folded into [0, 360)."""
    folded = float(bearing) % 360.0
    return 0.0 if folded >= 360.0 else folded  # -1e-17 % 360 rounds to 360.0


def _rotated_pole(lat: float, lon: float, up: float) -> tuple[float, float]:
    """The point 90° from (*lat*, *lon*) along bearing *up*: the top of the rotated view."""
    pole_lon, pole_lat, _ = _SPHERE.fwd(lon, lat, up, _QUARTER_TURN_M)
    return pole_lat, pole_lon


class RotatedOrthographic(ccrs.Projection):
    """An orthographic globe centred on (*lat*, *lon*) with bearing *up* at the top.

    Text drawn on the map stays upright; only the globe turns.
    """

    def __init__(self, lat: float, lon: float, up: float) -> None:
        self.centre = (float(lat), float(lon))
        self.up = normalise_bearing(up)
        pole_lat, pole_lon = _rotated_pole(lat, lon, self.up)
        sphere = f"+R={EARTH_RADIUS_M}"

        # Where the centre falls in the rotated frame before spinning it
        to_rotated = Transformer.from_crs(
            "EPSG:4326",
            CRS.from_proj4(f"+proj=ob_tran +o_proj=longlat +o_lat_p={pole_lat} +o_lon_p=0 "
                           f"+lon_0={pole_lon + 180} {sphere}"),
            always_xy=True,
        )
        centre_rot_lon, _ = to_rotated.transform(lon, lat)

        params = [
            ("proj", "ob_tran"),
            ("o_proj", "ortho"),
            ("o_lat_p", pole_lat),
            ("o_lon_p", -centre_rot_lon),   # spin the centre to rotated longitude 0
            ("lon_0", pole_lon + 180),
        ]
        # ellipse=None: a plain sphere, without Globe's default WGS84 ellipsoid
        globe = ccrs.Globe(semimajor_axis=EARTH_RADIUS_M, semiminor_axis=EARTH_RADIUS_M,
                           ellipse=None)  # pyright: ignore[reportArgumentType]
        super().__init__(params, globe=globe)
        self.threshold = 1e5  # same interpolation step as Cartopy's Orthographic

        theta = np.linspace(0, 2 * np.pi, 361)
        ring = np.column_stack([EARTH_RADIUS_M * np.cos(theta), EARTH_RADIUS_M * np.sin(theta)])
        self._boundary = sgeom.LinearRing(ring[::-1])  # clockwise, as Cartopy expects

    @property
    def boundary(self) -> sgeom.LinearRing:
        return self._boundary

    @property
    def x_limits(self) -> tuple[float, float]:
        return (-EARTH_RADIUS_M, EARTH_RADIUS_M)

    @property
    def y_limits(self) -> tuple[float, float]:
        return (-EARTH_RADIUS_M, EARTH_RADIUS_M)


def globe_projection(lat: float, lon: float, up: float = 0.0) -> ccrs.Projection:
    """An orthographic globe centred on (*lat*, *lon*), bearing *up* at the top.

    North up (``up`` = 0) returns Cartopy's own Orthographic, so north-up
    renders are unchanged.
    """
    up = normalise_bearing(up)
    if up == 0:
        return ccrs.Orthographic(central_longitude=lon, central_latitude=lat)
    return RotatedOrthographic(lat, lon, up)


def initial_bearing(lat: float, lon: float, target_lat: float, target_lon: float) -> float:
    """Compass bearing (0-360°) of the great circle from (*lat*, *lon*) to the target.

    Raises ``ValueError`` when the target is the start point or its antipode,
    where every direction leads there.
    """
    azimuth, _, distance = _SPHERE.inv(lon, lat, target_lon, target_lat)
    if distance < 1_000 or distance > math.pi * EARTH_RADIUS_M - 1_000:
        raise ValueError("the target must not be the centre or its antipode; every bearing leads there")
    return normalise_bearing(azimuth)


def far_side_up(lat: float, lon: float, up: float, anti_lat: float, anti_lon: float) -> float:
    """Bearing at the antipode that keeps both globes' tops on the same point.

    The near globe's top is the rotated pole, 90° from both the centre and the
    antipode; pointing the far globe at it makes the pair two halves of one
    rotated Earth (north up stays north up).
    """
    if normalise_bearing(up) == 0:
        return 0.0
    pole_lat, pole_lon = _rotated_pole(lat, lon, normalise_bearing(up))
    return initial_bearing(anti_lat, anti_lon, pole_lat, pole_lon)
