"""Tests for the web app: requests are checked like a command line and rendered on a worker."""

import logging
import os
import threading

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import atlas  # noqa: E402
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
        assert options["providers"] == ["osm", "osm_plain", "nasa", "nasa_night"]          # no Google key set
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

    def test_atlas_layers(self, client, renders):
        _, status = _render(client, city="Lisbon", ocean_depth=True, plates=True, earthquakes=7.0,
                            population=2010, livestock="sheep")
        assert renders[0]["earthquakes"] == 7.0 and renders[0]["population"] == 2010
        assert renders[0]["livestock"] == "sheep" and renders[0]["ocean_depth"] is True
        assert "--ocean-depth --plates --earthquakes 7.0" in status["command"]
        assert "--population 2010 --livestock sheep" in status["command"]

    def test_malaria_and_aedes(self, client, renders):
        _, status = _render(client, city="Lisbon", malaria="pv", malaria_year=2010, aedes="both")
        assert (renders[0]["malaria"], renders[0]["malaria_year"], renders[0]["aedes"]) == ("pv", 2010, "both")
        assert "--malaria pv --malaria-year 2010 --aedes both" in status["command"]

    def test_species(self, client, renders):
        _, status = _render(client, city="Lisbon", species="red fox", species_years="1990-2020")
        assert (renders[0]["species"], renders[0]["species_years"]) == ("red fox", "1990-2020")
        assert "--species 'red fox' --species-years 1990-2020" in status["command"]

    def test_indicator(self, client, renders):
        _, status = _render(client, city="Lisbon", indicator="life-expectancy", indicator_year=2000,
                            indicator_alpha=0.5)
        assert (renders[0]["indicator"], renders[0]["indicator_year"], renders[0]["indicator_alpha"]) == \
            ("life-expectancy", 2000, 0.5)
        assert "--indicator life-expectancy --indicator-year 2000 --indicator-alpha 0.5" in status["command"]

    def test_ports(self, client, renders):
        options = client.get("/api/options").json()
        assert options["ports"]["container"] == "Container ships" and list(options["ports"])[0] == "all"
        _, status = _render(client, city="Lisbon", ports="container", provider="nasa_night")
        assert (renders[0]["ports"], renders[0]["tile_provider"]) == ("container", "nasa_night")
        assert "--ports container" in status["command"] and "--provider nasa_night" in status["command"]

    def test_shipping_and_air_routes(self, client, renders):
        _, status = _render(client, city="Lisbon", shipping="fishing", shipping_alpha=0.6, air_routes="LIS")
        assert (renders[0]["shipping"], renders[0]["shipping_alpha"], renders[0]["air_routes"]) == ("fishing", 0.6,
                                                                                                   "LIS")
        assert "--shipping fishing" in status["command"] and "--air-routes LIS" in status["command"]

    def test_industry(self, client, renders):
        _, status = _render(client, city="Lisbon", industry="petrochemical,steel")
        assert renders[0]["industry"] == "petrochemical,steel"
        assert "--industry petrochemical,steel" in status["command"]

    def test_cities(self, client, renders):
        _, status = _render(client, city="Lisbon", cities=1975, cities_min=250000)
        assert (renders[0]["cities"], renders[0]["cities_min"]) == (1975, 250000)
        assert "--cities 1975 --cities-min 250000" in status["command"]

    def test_iucn_range(self, client, renders):
        _, status = _render(client, city="Lisbon", iucn="Lynx pardinus", iucn_alpha=0.6)
        assert (renders[0]["iucn"], renders[0]["iucn_alpha"]) == ("Lynx pardinus", 0.6)
        assert "--iucn 'Lynx pardinus' --iucn-alpha 0.6" in status["command"]

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


ATLAS = ('# A test atlas.\ntitle = "Test"\n\n[[pages]]\ntype = "section"\ntitle = "Part"\n\n'
         '[[pages]]\ntitle = "Lisbon"\ntext = "Words."\nquestions = ["Why?"]\n[pages.map]\ncity = "Lisbon"\n'
         'radius = 800\n')


@pytest.fixture
def atlas_store(tmp_path, monkeypatch):
    """A client with an atlases folder of its own, holding one atlas, and a renderer that draws real PNGs."""
    from PIL import Image

    atlases_dir = tmp_path / "atlases"
    atlases_dir.mkdir()
    (atlases_dir / "test.toml").write_text(ATLAS, encoding="utf-8")
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        Image.new("RGBA", (60, 70), (90, 150, 200, 255)).save(kwargs["output_filename"])
        return kwargs["output_filename"]

    monkeypatch.setattr(ortho, "generate_orthographic_map", render)

    def make(read_only=False, max_dpi=300):
        settings = webapp.Settings(output_dir=str(tmp_path / "maps"), cache_dir=str(tmp_path / "tiles"),
                                   atlases_dir=str(atlases_dir), read_only=read_only, max_dpi=max_dpi)
        return TestClient(webapp.create_app(settings))
    return make, atlases_dir, calls


class TestAtlases:
    def test_page_and_options(self, atlas_store):
        with atlas_store[0]() as client:
            page = client.get("/atlas")
            assert page.status_code == 200 and 'id="pages"' in page.text
            assert client.get("/static/atlas.js").status_code == 200
            assert client.get("/api/options").json()["atlas"]["papers"] == ["a4", "a3", "letter"]

    def test_listed_and_read(self, atlas_store):
        with atlas_store[0]() as client:
            assert client.get("/api/atlases").json() == [{"name": "test", "title": "Test", "pages": 2}]
            data = client.get("/api/atlases/test").json()
        assert data["atlas"]["title"] == "Test"
        assert data["atlas"]["pages"][1] == {"type": "map", "title": "Lisbon", "text": "Words.", "questions": ["Why?"],
                                             "orientation": "auto", "recipe": None,
                                             "map": {"city": "Lisbon", "radius": 800}, "kept": []}

    @pytest.mark.parametrize("url, status", [
        ("/api/atlases/nothing", 404), ("/api/atlases/Bad.Name", 400),
        ("/api/atlases/test/maps/1", 400), ("/api/atlases/test/maps/9", 400), ("/api/atlases/nothing/maps/1", 404),
    ])
    def test_unknown_names_and_pages(self, atlas_store, url, status):
        with atlas_store[0]() as client:
            assert client.get(url).status_code == status

    def test_saved_checked_and_overwritten(self, atlas_store):
        make, atlases_dir, _ = atlas_store
        with make() as client:
            data = client.get("/api/atlases/test").json()["atlas"]
            data["pages"][1]["title"] = "Lisboa"
            assert client.put("/api/atlases/copy", json={"atlas": data}).status_code == 200
            assert client.put("/api/atlases/copy", json={"atlas": data}).status_code == 409
            data["pages"][1]["map"]["koppen_class"] = ["Zzz"]
            bad = client.put("/api/atlases/copy", json={"atlas": data, "overwrite": True})
            assert bad.status_code == 400 and "Unknown Köppen-Geiger class 'Zzz'" in bad.json()["detail"]
        assert atlas.load_atlas(str(atlases_dir / "copy.toml")).pages[1].title == "Lisboa"

    def test_a_pages_map_in_the_form(self, atlas_store):
        make, atlases_dir, _ = atlas_store
        with make() as client:
            found = client.get("/api/atlases/test/maps/2").json()
            assert found == {"name": "test", "page": "2", "title": "Lisbon", "request": {"city": "Lisbon", "radius": 800}}
            saved = client.put("/api/atlases/test/maps/2", json={"city": "Tokyo", "koppen": True, "dpi": 150})
            assert saved.status_code == 200
            assert client.put("/api/atlases/test/maps/2", json={"city": "Tokyo", "zoom": 99}).status_code == 400
        text = (atlases_dir / "test.toml").read_text(encoding="utf-8")
        assert text.startswith("# A test atlas.\n")
        assert atlas.load_atlas(str(atlases_dir / "test.toml")).pages[1].map == {"city": "Tokyo", "koppen": True}

    def test_built_into_a_pdf_drawing_only_new_maps(self, atlas_store):
        make, _, calls = atlas_store
        with make() as client:
            job = client.post("/api/atlases/test/pdf", json={})
            assert job.status_code == 202 and job.json()["command"].startswith("python atlas.py ")
            client.app.state.maps.wait(job.json()["id"], timeout=30)
            status = client.get(f"/api/maps/{job.json()['id']}").json()
            assert status["status"] == "done" and status["progress"] == [1, 1] and status["image"] is None
            pdf = client.get(status["pdf"])
            assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
            assert pdf.content.startswith(b"%PDF")
            assert client.get(f"/api/maps/{status['id']}/image").status_code == 404
            assert len(calls) == 1 and calls[0]["dpi"] == atlas.DEFAULT_ATLAS_DPI

            again = client.post("/api/atlases/test/pdf", json={"proof": True}).json()
            client.app.state.maps.wait(again["id"], timeout=30)
            assert client.get(f"/api/maps/{again['id']}").json()["filename"] == "atlas_test_proof.pdf"
            assert len(calls) == 1

    def test_builds_are_refused_before_they_start(self, atlas_store):
        make, atlases_dir, _ = atlas_store
        with make(max_dpi=100) as client:
            response = client.post("/api/atlases/test/pdf")
            assert response.status_code == 400 and "up to 100 dpi" in response.json()["detail"]
            assert client.post("/api/atlases/nothing/pdf").status_code == 404
        (atlases_dir / "broken.toml").write_text('title = "B"\n[[pages]]\ntitle = "M"\n[pages.map]\nradius = 5\n',
                                                 encoding="utf-8")
        with make() as client:
            response = client.post("/api/atlases/broken/pdf")
            assert response.status_code == 400 and "page 1 (M)" in response.json()["detail"]

    def test_deleted(self, atlas_store):
        make, atlases_dir, _ = atlas_store
        with make() as client:
            assert client.delete("/api/atlases/test").status_code == 204
            assert client.delete("/api/atlases/test").status_code == 404
        assert not list(atlases_dir.iterdir())

    def test_a_read_only_server_builds_but_changes_nothing(self, atlas_store):
        make, atlases_dir, _ = atlas_store
        with make(read_only=True) as client:
            data = client.get("/api/atlases/test").json()["atlas"]
            assert client.put("/api/atlases/test", json={"atlas": data, "overwrite": True}).status_code == 403
            assert client.put("/api/atlases/test/maps/2", json={"city": "Tokyo"}).status_code == 403
            assert client.delete("/api/atlases/test").status_code == 403
            assert client.post("/api/atlases/test/pdf", json={"proof": True}).status_code == 202
        assert (atlases_dir / "test.toml").read_text(encoding="utf-8") == ATLAS
