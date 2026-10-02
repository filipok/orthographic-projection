"""Google Maps tiles through the official Map Tiles API.

Requires an API key for a Google Cloud project with the **Map Tiles API**
enabled and billing set up.  Unless passed explicitly, the key is read from
``GOOGLE_MAPS_API_KEY`` or, failing that, ``GOOGLE_API_KEY`` (the CLI loads
both from ``~/myapikeys.env``; see ``ortho.load_env_files``).

Flow (https://developers.google.com/maps/documentation/tile/2d-tiles-overview):

1. ``POST /v1/createSession`` with the map type returns a session token
   (valid for about two weeks).
2. Tiles are fetched from ``/v1/2dtiles/{z}/{x}/{y}?session=…&key=…``.
3. ``GET /tile/v1/viewport`` returns the copyright string that must be shown
   on the map, together with "Google Maps" attribution.

Google's policies forbid caching tiles beyond what their ``Cache-Control``
headers allow, so this source never enables Cartopy's tile cache.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import cartopy.io.img_tiles as cimgt

logger = logging.getLogger(__name__)

API_KEY_ENV = "GOOGLE_MAPS_API_KEY"
# Checked in order. GOOGLE_API_KEY is the generic name other tools use (e.g. in
# ~/myapikeys.env); it works if its project has the Map Tiles API enabled.
API_KEY_ENVS = (API_KEY_ENV, "GOOGLE_API_KEY")
BASE_URL = "https://tile.googleapis.com"
MAP_TYPES = ("roadmap", "satellite")

# Viewport bounds for the attribution lookup. The API wants values strictly
# inside (-90, 90) / (-180, 180); Web Mercator tiles stop at ±85.05°.
_WORLD_BOUNDS = {"north": 85, "south": -85, "east": 179.999, "west": -179.999}


class GoogleTilesError(RuntimeError):
    """A Map Tiles API request failed (bad key, API disabled, quota, …)."""


def resolve_api_key(api_key: str | None = None) -> str:
    """Return *api_key*, or the first key set in :data:`API_KEY_ENVS`."""
    if api_key and api_key.strip():
        return api_key.strip()
    for name in API_KEY_ENVS:
        key = os.environ.get(name, "").strip()
        if key:
            logger.debug("Using Google API key from $%s", name)
            return key
    raise GoogleTilesError(
        "Google tiles need a Google Maps Platform API key with the Map Tiles API "
        f"enabled. Set {' or '.join(API_KEY_ENVS)} in the environment or in ~/myapikeys.env."
    )


def _request_json(req: urllib.request.Request, what: str, timeout: float) -> dict[str, Any]:
    """Send *req* and decode the JSON body, turning API errors into GoogleTilesError.

    Error messages never include the request URL, because it contains the key.
    """
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc)["error"]["message"]
        except Exception:
            message = exc.reason
        hint = ""
        if exc.code == 403:
            hint = (
                " Check that the key's Google Cloud project has the Map Tiles API enabled"
                " (with billing) and that the key's API restrictions allow the Map Tiles API."
            )
        raise GoogleTilesError(f"{what} failed (HTTP {exc.code}): {message}{hint}") from None
    except urllib.error.URLError as exc:
        raise GoogleTilesError(f"{what} failed: {exc.reason}") from None


def create_session(
    api_key: str,
    map_type: str,
    language: str = "en-US",
    region: str = "US",
    timeout: float = 30,
) -> str:
    """Create a Map Tiles API session for *map_type* and return its token."""
    if map_type not in MAP_TYPES:
        raise ValueError(f"map_type must be one of {MAP_TYPES}, got {map_type!r}")
    body = json.dumps({"mapType": map_type, "language": language, "region": region}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/v1/createSession?key={urllib.parse.quote(api_key)}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    data = _request_json(req, "Creating a Google Map Tiles session", timeout)
    logger.debug("Created Google %s tile session (expires %s)", map_type, data.get("expiry"))
    return data["session"]


class GoogleMapTiles(cimgt.GoogleWTS):
    """Cartopy tile source backed by the Google Map Tiles API.

    Creating an instance creates a session, so an invalid key or a disabled
    API fails here rather than mid-render (Cartopy replaces tiles that fail
    to download with blank grey ones).
    """

    def __init__(
        self,
        map_type: str = "roadmap",
        api_key: str | None = None,
        language: str = "en-US",
        region: str = "US",
        desired_tile_form: str = "RGB",
    ) -> None:
        self.map_type = map_type
        self._api_key = resolve_api_key(api_key)
        self.session = create_session(self._api_key, map_type, language, region)
        super().__init__(desired_tile_form=desired_tile_form)

    def __repr__(self) -> str:  # keep the key out of reprs and logs
        return f"{type(self).__name__}(map_type={self.map_type!r})"

    def _image_url(self, tile: tuple[int, int, int]) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        x, y, z = tile
        query = urllib.parse.urlencode({"session": self.session, "key": self._api_key})
        return f"{BASE_URL}/v1/2dtiles/{z}/{x}/{y}?{query}"

    def copyright(self, zoom: int, timeout: float = 30) -> str:
        """Return the data copyright string Google requires for *zoom*."""
        query = urllib.parse.urlencode(
            {"session": self.session, "key": self._api_key, "zoom": zoom, **_WORLD_BOUNDS}
        )
        req = urllib.request.Request(f"{BASE_URL}/tile/v1/viewport?{query}")
        data = _request_json(req, "Fetching Google tile attribution", timeout)
        return str(data.get("copyright", "")).strip()

    def attribution_lines(self, zoom: int) -> list[str]:
        """Credit lines for a map rendered at *zoom*: "Google Maps" plus the data copyright."""
        copyright_text = self.copyright(zoom)
        return ["Google Maps", copyright_text] if copyright_text else ["Google Maps"]
