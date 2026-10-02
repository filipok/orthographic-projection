"""Tests for google_tiles.py — Map Tiles API client (network mocked)."""

import io
import json
import os
import sys
import urllib.error
import urllib.parse
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import google_tiles

KEY = "test-key-123"


def _json_response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def _http_error(code, payload, url="https://tile.googleapis.com/x?key=" + KEY):
    return urllib.error.HTTPError(url, code, "error", {}, _json_response(payload))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def no_ambient_keys(monkeypatch):
    """Ignore any Google keys set in the developer's real environment."""
    for name in google_tiles.API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def urlopen():
    with mock.patch.object(google_tiles.urllib.request, "urlopen") as m:
        m.return_value = _json_response({"session": "SESSION-1", "expiry": "1"})
        yield m


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


# ===================================================================
# API key
# ===================================================================


class TestResolveApiKey:
    def test_explicit_key_wins(self, monkeypatch):
        monkeypatch.setenv(google_tiles.API_KEY_ENV, "from-env")
        assert google_tiles.resolve_api_key("explicit") == "explicit"

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv(google_tiles.API_KEY_ENV, "  from-env  ")
        assert google_tiles.resolve_api_key() == "from-env"

    def test_falls_back_to_google_api_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "generic")
        assert google_tiles.resolve_api_key() == "generic"

    def test_maps_key_preferred_over_generic(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "generic")
        monkeypatch.setenv(google_tiles.API_KEY_ENV, "maps")
        assert google_tiles.resolve_api_key() == "maps"

    def test_missing_key_raises(self):
        with pytest.raises(google_tiles.GoogleTilesError) as excinfo:
            google_tiles.resolve_api_key()
        for name in google_tiles.API_KEY_ENVS:
            assert name in str(excinfo.value)


# ===================================================================
# Sessions
# ===================================================================


class TestCreateSession:
    def test_posts_documented_request(self, urlopen):
        token = google_tiles.create_session(KEY, "satellite", language="en-GB", region="GB")
        assert token == "SESSION-1"

        req = urlopen.call_args.args[0]
        assert req.get_method() == "POST"
        assert req.full_url.startswith("https://tile.googleapis.com/v1/createSession?")
        assert _query(req.full_url) == {"key": KEY}
        assert req.get_header("Content-type") == "application/json"
        assert json.loads(req.data) == {"mapType": "satellite", "language": "en-GB", "region": "GB"}

    def test_rejects_unknown_map_type(self, urlopen):
        with pytest.raises(ValueError, match="map_type"):
            google_tiles.create_session(KEY, "streetview")
        urlopen.assert_not_called()

    def test_http_error_uses_google_message_and_hides_key(self, urlopen):
        urlopen.side_effect = _http_error(
            400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}
        )
        with pytest.raises(google_tiles.GoogleTilesError) as excinfo:
            google_tiles.create_session(KEY, "roadmap")
        message = str(excinfo.value)
        assert "HTTP 400" in message
        assert "API key not valid" in message
        assert KEY not in message

    def test_forbidden_error_explains_what_to_check(self, urlopen):
        urlopen.side_effect = _http_error(
            403, {"error": {"code": 403, "message": "Requests to this API tile method are blocked."}}
        )
        with pytest.raises(google_tiles.GoogleTilesError, match="API restrictions allow the Map Tiles API"):
            google_tiles.create_session(KEY, "roadmap")

    def test_network_error(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("no route to host")
        with pytest.raises(google_tiles.GoogleTilesError, match="no route to host"):
            google_tiles.create_session(KEY, "roadmap")


# ===================================================================
# GoogleMapTiles
# ===================================================================


class TestGoogleMapTiles:
    def test_init_creates_session(self, urlopen):
        tiles = google_tiles.GoogleMapTiles("roadmap", api_key=KEY)
        assert tiles.session == "SESSION-1"
        assert json.loads(urlopen.call_args.args[0].data)["mapType"] == "roadmap"

    def test_init_without_key_fails_before_any_request(self, urlopen):
        with pytest.raises(google_tiles.GoogleTilesError):
            google_tiles.GoogleMapTiles("roadmap")
        urlopen.assert_not_called()

    def test_tile_url(self, urlopen):
        tiles = google_tiles.GoogleMapTiles("satellite", api_key=KEY)
        url = tiles._image_url((3, 5, 4))
        assert url.startswith("https://tile.googleapis.com/v1/2dtiles/4/3/5?")
        assert _query(url) == {"session": "SESSION-1", "key": KEY}

    def test_repr_hides_key(self, urlopen):
        assert KEY not in repr(google_tiles.GoogleMapTiles("roadmap", api_key=KEY))

    def test_copyright_queries_viewport(self, urlopen):
        tiles = google_tiles.GoogleMapTiles("roadmap", api_key=KEY)
        urlopen.return_value = _json_response({"copyright": "Map data ©2026 Google", "maxZoomRects": []})

        assert tiles.copyright(3) == "Map data ©2026 Google"

        url = urlopen.call_args.args[0].full_url
        assert url.startswith("https://tile.googleapis.com/tile/v1/viewport?")
        query = _query(url)
        assert query["session"] == "SESSION-1"
        assert query["key"] == KEY
        assert query["zoom"] == "3"
        assert {"north", "south", "east", "west"} <= query.keys()
        assert -90 < float(query["south"]) < float(query["north"]) < 90
        assert -180 < float(query["west"]) < float(query["east"]) < 180

    def test_attribution_lines(self, urlopen):
        tiles = google_tiles.GoogleMapTiles("satellite", api_key=KEY)
        urlopen.return_value = _json_response({"copyright": "Imagery ©2026 TerraMetrics"})
        assert tiles.attribution_lines(3) == ["Google Maps", "Imagery ©2026 TerraMetrics"]

    def test_attribution_lines_without_copyright(self, urlopen):
        tiles = google_tiles.GoogleMapTiles("roadmap", api_key=KEY)
        urlopen.return_value = _json_response({"maxZoomRects": []})
        assert tiles.attribution_lines(3) == ["Google Maps"]
