"""Tests for the web app: requests are checked like a command line and rendered on a worker."""

import logging
import os
import threading

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import ortho  # noqa: E402
import webapp  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def renders(monkeypatch):
    """Replace the renderer with one that writes a tiny file; records each call's arguments."""
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        logging.getLogger("ortho").warning("Skipping something: offline")
        os.makedirs(kwargs["output_dir"], exist_ok=True)
        path = os.path.join(kwargs["output_dir"], kwargs["output_filename"])
        with open(path, "wb") as fh:
            fh.write(PNG)
        return path

    monkeypatch.setattr(ortho, "generate_orthographic_map", render)
    return calls


@pytest.fixture
def settings(tmp_path):
    return webapp.Settings(output_dir=str(tmp_path / "maps"), cache_dir=str(tmp_path / "tiles"),
                           max_dpi=300, max_queue=3, keep=2)


@pytest.fixture
def client(settings, monkeypatch):
    for name in ortho.API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    with TestClient(webapp.create_app(settings)) as test_client:
        yield test_client


def _render(client, **request):
    response = client.post("/api/maps", json=request)
    assert response.status_code == 202, response.text
    job = client.app.state.maps.wait(response.json()["id"], timeout=10)
    return job, client.get(f"/api/maps/{job.id}").json()


class TestPage:
    def test_index_and_assets(self, client):
        page = client.get("/")
        assert page.status_code == 200 and 'id="map-form"' in page.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200

    def test_options(self, client):
        options = client.get("/api/options").json()
        assert "Paris" in options["cities"]
        assert options["providers"] == ["osm", "nasa"]          # no Google key set
        assert options["dpi"] == {"default": 150, "min": ortho.MIN_DPI, "max": 300}
        assert options["periods"][0] == ["year", "Year"] and len(options["periods"]) == 17
        assert "ph" in options["soil_properties"] and "wheat" in options["crops"]

    def test_google_offered_with_a_key(self, client, monkeypatch):
        monkeypatch.setenv(ortho.API_KEY_ENV, "k")
        assert "google_satellite" in client.get("/api/options").json()["providers"]


class TestMaps:
    def test_render_and_fetch(self, client, renders, settings):
        job, status = _render(client, city="Paris", temperature="july", koppen_alpha=0.5)
        assert status["status"] == "done" and status["label"] == "Paris"
        assert status["warnings"] == ["Skipping something: offline"]
        kwargs = renders[0]
        assert kwargs["temperature"] == "july" and kwargs["koppen_alpha"] == 0.5
        assert kwargs["dpi"] == 150                                  # the web default, not the CLI's 300
        assert kwargs["output_dir"] == os.path.join(settings.output_dir, job.id)
        assert kwargs["tile_cache_dir"] == settings.cache_dir
        assert kwargs["city_name"] == "Paris" and kwargs["routes"] == []

        image = client.get(status["image"])
        assert image.status_code == 200 and image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert image.headers["content-disposition"].startswith("inline")
        download = client.get(status["image"], params={"download": 1})
        assert download.headers["content-disposition"] == \
            'attachment; filename="orthographic_map_paris_osm_z3.png"'

    def test_the_cli_command_for_the_same_map(self, client, renders):
        _, status = _render(client, city="New Delhi", radius=1500, wind="jja", koppen_class=["Cw", "Am"],
                            elevation=True, ice=False)
        assert status["command"] == ("python ortho.py --city 'New Delhi' --radius 1500.0 "
                                     "--koppen-class Cw --koppen-class Am --elevation --wind jja --dpi 150")
        args = ortho.parse_cli_args(["--city", "New Delhi", "--radius", "1500.0", "--koppen-class", "Cw",
                                     "--koppen-class", "Am", "--elevation", "--wind", "jja", "--dpi", "150"])
        cli = ortho.prepare_render(args).kwargs
        for key in ("lat", "lon", "radius_km", "zoom", "koppen_classes", "elevation", "wind", "dpi"):
            assert renders[0][key] == cli[key], key

    def test_coordinates(self, client, renders):
        _, status = _render(client, lat=-33.9, lon=18.4, up_toward="London")
        assert status["label"] == "(-33.9, 18.4)" and renders[0]["city_name"] is None
        assert 0 < renders[0]["up"] < 360

    @pytest.mark.parametrize("request_, message", [
        ({"city": "Paris", "humidity": "monsoon"}, "Unknown humidity period"),
        ({"city": "Paris", "koppen": True, "temperature": "year"}, "Choose one climate layer"),
        ({"city": "Paris", "lat": 1, "lon": 2}, "not both"),
        ({"city": "Paris", "up": 90, "up_toward": "London"}, "not both"),
        ({"city": "Paris", "dpi": 600}, "up to 300 dpi"),
        ({"city": "Paris", "dpi": 5}, "dpi must be between"),
        ({"city": "Atlantis"}, "Unknown city"),
        ({"lat": 95, "lon": 0}, "--lat must be between"),
        ({}, "Provide --city or --lat/--lon"),
        ({"city": "Paris", "provider": "google"}, "API key"),
        ({"city": "Paris", "provider": "bing"}, "Unknown tile provider"),
        ({"city": "Paris", "crop": ["kale-ish"]}, "kale"),
    ])
    def test_refused_before_any_work(self, client, renders, request_, message):
        response = client.post("/api/maps", json=request_)
        assert response.status_code == 400
        assert message in response.json()["detail"]
        assert renders == []

    def test_unknown_options_are_refused(self, client, renders):
        response = client.post("/api/maps", json={"city": "Paris", "output": "C:/Windows/x.png"})
        assert response.status_code == 422
        assert renders == []

    def test_a_failed_render_is_reported(self, client, monkeypatch):
        def fail(**_kwargs):
            raise ortho.GoogleTilesError("API key not valid")

        monkeypatch.setattr(ortho, "generate_orthographic_map", fail)
        job, status = _render(client, city="Paris")
        assert status["status"] == "failed" and status["error"] == "API key not valid"
        assert status["image"] is None
        assert client.get(f"/api/maps/{job.id}/image").status_code == 404

    def test_keys_are_redacted(self, client, monkeypatch):
        monkeypatch.setenv(ortho.API_KEY_ENV, "AIzaSECRET123")

        def leak(**_kwargs):
            logging.getLogger("urllib").warning("GET https://tile.googleapis.com/v1/2dtiles/1/0/0?session=abc&key=xyz")
            raise OSError("bad key AIzaSECRET123")

        monkeypatch.setattr(ortho, "generate_orthographic_map", leak)
        _, status = _render(client, city="Paris")
        assert status["error"] == "bad key REDACTED"
        assert status["warnings"] == ["GET https://tile.googleapis.com/v1/2dtiles/1/0/0?session=REDACTED&key=REDACTED"]

    def test_unknown_map(self, client):
        assert client.get("/api/maps/nope").status_code == 404

    def test_the_queue_has_a_limit(self, client, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(ortho, "generate_orthographic_map", lambda **_kw: release.wait(10))
        ids = [client.post("/api/maps", json={"city": "Paris"}).json()["id"] for _ in range(3)]
        try:
            refused = client.post("/api/maps", json={"city": "Paris"})
            assert refused.status_code == 503 and "already waiting" in refused.json()["detail"]
            assert client.get(f"/api/maps/{ids[2]}").json()["position"] in (1, 2)
        finally:
            release.set()
        for job_id in ids:
            client.app.state.maps.wait(job_id, timeout=10)

    def test_old_maps_are_cleared(self, client, renders, settings):
        first, _ = _render(client, city="Paris")
        for _ in range(2):
            _render(client, city="London")
        _render(client, city="Tokyo")           # pruning happens as a map is queued
        assert client.get(f"/api/maps/{first.id}").status_code == 404
        assert not os.path.exists(os.path.join(settings.output_dir, first.id))


class TestRequestArgs:
    def test_request_fields_are_cli_options(self):
        dests = vars(ortho.build_cli_parser().parse_args([]))
        assert set(webapp.MapRequest.model_fields) <= set(dests)

    def test_defaults_come_from_the_cli(self, settings):
        args = webapp.request_args(webapp.MapRequest(city="paris"), settings, "out")
        cli = ortho.build_cli_parser().parse_args(["--city", "paris"])
        for name in ("provider", "zoom", "up", "elevation_alpha", "soil_alpha", "vegetation_alpha", "koppen"):
            assert getattr(args, name) == getattr(cli, name)
        assert (args.dpi, args.output, args.output_dir, args.cache_dir) == (150, None, "out", settings.cache_dir)

    def test_blank_values_are_left_out(self, settings):
        request = webapp.MapRequest(city="paris", up_toward="", crop=[], wind=None)
        assert request.given() == {"city": "paris"}
