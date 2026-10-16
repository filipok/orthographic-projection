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
        assert options["providers"] == ["osm", "osm_plain", "nasa"]          # no Google key set
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

    def test_circles_from_another_place(self, client, renders):
        _, status = _render(client, city="Paris", circles_from="London")
        assert renders[0]["circles_from"] == (51.5074, -0.1278)
        assert "--circles-from London" in status["command"]

    def test_temperatures_over_the_sea(self, client, renders):
        _, status = _render(client, city="Lisbon", temperature="july", temperature_over_sea=True)
        assert renders[0]["temperature_over_sea"] is True
        assert "--temperature july --temperature-over-sea" in status["command"]
        _, status = _render(client, city="Lisbon", sea_temperature="jan")
        assert renders[1]["sea_temperature"] == "jan" and "--sea-temperature jan" in status["command"]

    def test_biomes_and_ecoregions(self, client, renders):
        _, status = _render(client, city="Lisbon", ecoregions=True, biome_class=["temperate", "rock"],
                            biome_alpha=0.5)
        assert renders[0]["ecoregions"] is True and renders[0]["biome_classes"] == ["temperate", "rock"]
        assert renders[0]["biome_alpha"] == 0.5
        assert "--biome-class temperate --biome-class rock --ecoregions --biome-alpha 0.5" in status["command"]
        _, status = _render(client, city="Shanghai", realms="wwf")
        assert renders[1]["realms"] == "wwf" and "--realms wwf" in status["command"]
        _, status = _render(client, city="Shanghai", realms="holt", realm_regions=True)
        assert renders[2]["realms"] == "holt" and renders[2]["realm_regions"] is True
        assert "--realms holt --realm-regions" in status["command"]

    def test_borders(self, client, renders):
        _, status = _render(client, city="Lisbon", borders="1500", borders_alpha=0.4)
        assert renders[0]["borders"] == "1500" and renders[0]["borders_alpha"] == 0.4
        assert "--borders 1500 --borders-alpha 0.4" in status["command"]
        _, status = _render(client, city="Lisbon", borders="-500")
        assert "--borders -500" in status["command"]

    def test_a_globe_beside(self, client, renders):
        _, status = _render(client, city="Lisbon", beside="Tokyo")
        assert renders[0]["beside"] == (35.6762, 139.6503) and renders[0]["beside_name"] == "Tokyo"
        assert status["label"] == "Lisbon and Tokyo"
        assert "--beside Tokyo" in status["command"]

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


LINE = ('{"type": "Feature", "properties": {"name": "A line"}, '
        '"geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 10]]}}')


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A client whose recipes and routes folders are its own: one recipe, one route file."""
    recipes, routes = tmp_path / "recipes", tmp_path / "routes"
    recipes.mkdir()
    routes.mkdir()
    (routes / "line.geojson").write_text(LINE, encoding="utf-8")
    (recipes / "trip.toml").write_text('# A trip.\ncity = "lisbon"\nroute = ["../routes/line.geojson"]\n'
                                       'route-legend = true\nwind = "jul"\noutput = "trip.png"\n', encoding="utf-8")

    def make(read_only=False):
        for name in ortho.API_KEY_ENVS:
            monkeypatch.delenv(name, raising=False)
        settings = webapp.Settings(output_dir=str(tmp_path / "maps"), cache_dir=str(tmp_path / "tiles"),
                                   recipes_dir=str(recipes), routes_dir=str(routes), read_only=read_only)
        return TestClient(webapp.create_app(settings))
    return make, recipes, routes


class TestRecipes:
    def test_listed_and_read_into_a_request(self, store):
        make, _, _ = store
        with make() as client:
            assert client.get("/api/options").json()["editable"] is True
            assert client.get("/api/recipes").json() == [{"name": "trip", "description": "A trip."}]
            recipe = client.get("/api/recipes/trip").json()
        assert recipe["request"] == {"city": "Lisbon", "route": ["line.geojson"], "route_legend": True, "wind": "july"}
        assert recipe["kept"] == ['output = "trip.png"']

    @pytest.mark.parametrize("name, status", [("nothing", 404), ("Bad.Name", 400)])
    def test_unknown_or_bad_names(self, store, name, status):
        with store[0]() as client:
            assert client.get(f"/api/recipes/{name}").status_code == status

    def test_saved_new_then_overwritten(self, store):
        make, recipes, _ = store
        with make() as client:
            body = {"description": "Mine.", "request": {"city": "Tokyo", "beside": "Lisbon", "dpi": 150}}
            saved = client.put("/api/recipes/mine", json=body)
            assert saved.status_code == 200 and saved.json()["name"] == "mine"
            assert client.put("/api/recipes/mine", json=body).status_code == 409
            body["request"]["ice"] = True
            assert client.put("/api/recipes/mine", json={**body, "overwrite": True}).status_code == 200
        assert (recipes / "mine.toml").read_text(encoding="utf-8") == (
            '# Mine.\ncity = "Tokyo"\nbeside = "Lisbon"\nice = true\n')

    def test_saving_keeps_what_the_form_leaves_alone(self, store):
        make, recipes, _ = store
        with make() as client:
            request = client.get("/api/recipes/trip").json()["request"]
            request["route"] = []
            response = client.put("/api/recipes/trip", json={"request": request, "overwrite": True})
        assert response.status_code == 200
        assert (recipes / "trip.toml").read_text(encoding="utf-8") == (
            'city = "Lisbon"\nwind = "july"\nroute-legend = true\noutput = "trip.png"\n')

    @pytest.mark.parametrize("request_, status", [
        ({"city": "Paris", "route": ["nothing.geojson"]}, 400),
        ({"city": "Paris", "colour": "red"}, 422),                       # not an option
    ])
    def test_refused(self, store, request_, status):
        with store[0]() as client:
            assert client.put("/api/recipes/new", json={"request": request_}).status_code == status

    def test_deleted(self, store):
        make, recipes, _ = store
        with make() as client:
            assert client.delete("/api/recipes/trip").status_code == 204
            assert client.delete("/api/recipes/trip").status_code == 404
        assert not list(recipes.iterdir())

    def test_a_read_only_server_changes_nothing(self, store):
        make, recipes, routes = store
        with make(read_only=True) as client:
            assert client.get("/api/options").json()["editable"] is False
            assert client.get("/api/recipes/trip").status_code == 200
            assert client.put("/api/recipes/new", json={"request": {"city": "Paris"}}).status_code == 403
            assert client.delete("/api/recipes/trip").status_code == 403
            assert client.post("/api/routes", json={"filename": "x.geojson", "content": LINE}).status_code == 403
        assert [p.name for p in recipes.iterdir()] == ["trip.toml"]
        assert [p.name for p in routes.iterdir()] == ["line.geojson"]


class TestRoutes:
    def test_listed_and_uploaded(self, store):
        make, _, routes = store
        with make() as client:
            assert client.get("/api/routes").json() == [{"name": "line.geojson", "routes": ["A line"]}]
            added = client.post("/api/routes", json={"filename": "My Line.json", "content": LINE})
            assert added.status_code == 201 and added.json() == {"name": "my_line.geojson", "routes": ["A line"]}
            assert client.post("/api/routes", json={"filename": "my_line.geojson", "content": LINE}).status_code == 409
            bad = client.post("/api/routes", json={"filename": "bad.geojson", "content": "{"})
            assert bad.status_code == 400 and "bad.geojson: invalid JSON" in bad.json()["detail"]
        assert sorted(p.name for p in routes.iterdir()) == ["line.geojson", "my_line.geojson"]

    def test_a_map_with_routes(self, client, renders):
        _, status = _render(client, city="Lisbon", route=["gibraltar_ascension_falklands.geojson"], route_legend=True)
        assert [route.name for route in renders[0]["routes"]] == ["Gibraltar - Ascension - Falklands"]
        assert renders[0]["route_legend"] is True
        assert "--route routes/gibraltar_ascension_falklands.geojson --route-legend" in status["command"]

    @pytest.mark.parametrize("route", ["nothing.geojson", "../recipes/viking_routes.toml"])
    def test_a_map_with_an_unknown_route_is_refused(self, client, route):
        response = client.post("/api/maps", json={"city": "Lisbon", "route": [route]})
        assert response.status_code == 400 and "no route file" in response.json()["detail"]
