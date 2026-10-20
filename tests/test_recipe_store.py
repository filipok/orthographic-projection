"""Tests for recipe_store.py — reading recipes into map requests, writing them back, and route files."""

import json
import os
import shutil
from pathlib import Path

import pytest

import ortho
import recipe_store

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "recipes"
ROUTES = ROOT / "routes"

# The web form's options, in the order it lists them (as webapp.MapRequest has them)
FIELDS = [
    "city", "lat", "lon", "provider", "zoom", "dpi", "radius", "both_hemispheres", "beside", "up", "up_toward",
    "circles_from", "koppen", "koppen_class", "trewartha", "trewartha_class", "temperature", "precipitation",
    "humidity", "temperature_over_sea", "sea_temperature", "koppen_alpha", "elevation", "elevation_alpha",
    "landcover", "landcover_class", "landcover_year", "ndvi", "vegetation_alpha", "biomes", "biome_class",
    "ecoregions", "realms", "realm_regions", "biome_alpha", "soil", "soil_class",
    "soil_alpha", "soil_property", "soil_depth", "wind", "ice", "ice_year", "crop", "borders", "borders_source",
    "borders_alpha", "ocean_depth", "ice_age", "currents", "plates", "earthquakes", "volcanoes", "rivers", "basins",
    "basins_alpha", "cyclones", "time_zones", "permafrost", "permafrost_alpha", "population", "population_alpha",
    "livestock", "livestock_alpha", "malaria", "malaria_year", "malaria_alpha", "aedes", "species", "species_years", "iucn", "iucn_alpha", "cities", "cities_min", "route", "route_legend",
]

LINE = {"type": "Feature", "properties": {"name": "A line"},
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 10]]}}


@pytest.fixture
def dirs(tmp_path):
    """Empty recipes and routes folders, the routes folder holding one route file."""
    recipes, routes = tmp_path / "recipes", tmp_path / "routes"
    recipes.mkdir()
    routes.mkdir()
    (routes / "line.geojson").write_text(json.dumps(LINE), encoding="utf-8")
    return str(recipes), str(routes)


def _recipe(dirs, name, text):
    path = Path(dirs[0]) / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestDescription:
    def test_the_leading_comment_in_paragraphs(self):
        text = "# One line\n# and the next.\n#\n# A second paragraph.\ncity = \"paris\"  # not this\n# nor this\n"
        assert recipe_store.description(text) == "One line and the next.\n\nA second paragraph."

    def test_none(self):
        assert recipe_store.description('city = "paris"\n') == ""


class TestReadRecipe:
    def test_options_routes_and_what_is_kept(self, dirs):
        _recipe(dirs, "trip", '# A trip.\ncity = "lisbon"\nroute = ["../routes/line.geojson", "/elsewhere/x.geojson"]\n'
                              'route-legend = true\nkoppen-class = "Cs"\nclimate-alpha = 0.6\ndpi = 200\n'
                              'output = "trip.png"\n')
        recipe = recipe_store.read_recipe(*dirs, "trip", FIELDS)
        assert recipe["description"] == "A trip."
        assert recipe["request"] == {"city": "Lisbon", "route": ["line.geojson"], "route_legend": True,
                                     "koppen_class": ["Cs"], "koppen_alpha": 0.6, "dpi": 200}
        assert recipe["kept"] == ['route = "/elsewhere/x.geojson"', "dpi = 200", 'output = "trip.png"']

    @pytest.mark.parametrize("spec, key", [("jan", "january"), ("7", "july"), ("DJF", "djf"), ("annual", "year")])
    def test_periods_in_the_forms_spelling(self, dirs, spec, key):
        _recipe(dirs, "p", f'lat = 1\nlon = 2\nwind = "{spec}"\nsea-temperature = "{spec}"\n')
        request = recipe_store.read_recipe(*dirs, "p", FIELDS)["request"]
        assert request["wind"] == request["sea_temperature"] == key

    def test_a_year_written_as_a_number(self, dirs):
        _recipe(dirs, "y", 'lat = 1\nlon = 2\nborders = -500\n')
        assert recipe_store.read_recipe(*dirs, "y", FIELDS)["request"]["borders"] == "-500"

    @pytest.mark.parametrize("text, match", [
        ('cty = "paris"\n', "unknown option 'cty'"),
        ('city = "paris\n', "not valid TOML"),
        ('city = "paris"\nwind = "monsoon"\n', "Unknown wind period"),
    ])
    def test_bad_recipes(self, dirs, text, match):
        _recipe(dirs, "bad", text)
        with pytest.raises(recipe_store.RecipeError, match=match):
            recipe_store.read_recipe(*dirs, "bad", FIELDS)

    def test_missing_and_badly_named(self, dirs):
        with pytest.raises(recipe_store.RecipeNotFound):
            recipe_store.read_recipe(*dirs, "nothing", FIELDS)
        with pytest.raises(recipe_store.RecipeError, match="not a recipe name"):
            recipe_store.read_recipe(*dirs, "../ortho", FIELDS)

    def test_listed_by_name_with_their_descriptions(self, dirs):
        _recipe(dirs, "b", '# Second.\ncity = "paris"\n')
        _recipe(dirs, "a", 'city = "paris"\n')
        (Path(dirs[0]) / "Not A Recipe.toml").write_text("", encoding="utf-8")
        assert recipe_store.list_recipes(dirs[0]) == [{"name": "a", "description": ""},
                                                     {"name": "b", "description": "Second."}]


class TestWriteRecipe:
    @pytest.mark.parametrize("name", sorted(p.stem for p in RECIPES.glob("*.toml")))
    def test_every_bundled_recipe_survives_a_round_trip(self, tmp_path, name):
        """Read into a request and written again, a recipe draws the same map."""
        recipe = recipe_store.read_recipe(str(RECIPES), str(ROUTES), name, FIELDS)
        # The same layout elsewhere: the recipe (so its kept options carry over) beside the routes
        shutil.copytree(ROUTES, tmp_path / "routes")
        target = tmp_path / "recipes"
        target.mkdir()
        shutil.copy(RECIPES / f"{name}.toml", target / f"{name}.toml")
        path = recipe_store.write_recipe(str(target), str(tmp_path / "routes"), name, recipe["description"],
                                         recipe["request"], FIELDS, overwrite=True)
        before = vars(ortho.parse_cli_args(["--config", str(RECIPES / f"{name}.toml")]))
        after = vars(ortho.parse_cli_args(["--config", str(path)]))
        for args in (before, after):
            del args["config"]
            args["route"] = [os.path.basename(route) for route in args["route"] or []]
        assert after == before
        assert recipe_store.description(path.read_text(encoding="utf-8")) == recipe["description"]

    def test_written_with_its_description_and_relative_routes(self, dirs):
        request = {"city": "Lisbon", "beside": "Tokyo", "koppen_alpha": 0.6, "crop": ["wheat", "rice"],
                   "route": ["line.geojson"], "route_legend": True, "ice": False, "dpi": 150}
        path = recipe_store.write_recipe(*dirs, "pair", "Lisbon and Tokyo.\n\nWith crops.", request, FIELDS)
        assert path.read_text(encoding="utf-8") == (
            "# Lisbon and Tokyo.\n#\n# With crops.\n"
            'city = "Lisbon"\nbeside = "Tokyo"\nclimate-alpha = 0.6\ncrop = ["wheat", "rice"]\n'
            'route = ["../routes/line.geojson"]\nroute-legend = true\n')

    def test_long_lists_go_over_several_lines(self, dirs):
        crops = ["wheat", "rice", "maize", "barley", "sorghum", "millet", "soybean", "potato", "cassava"]
        path = recipe_store.write_recipe(*dirs, "crops", "", {"lat": 1.0, "lon": 2.0, "crop": crops}, FIELDS)
        assert 'crop = [\n    "wheat",\n' in path.read_text(encoding="utf-8")
        assert ortho.parse_cli_args(["--config", str(path)]).crop == crops

    def test_overwriting_keeps_what_the_form_leaves_alone(self, dirs):
        _recipe(dirs, "trip", 'city = "lisbon"\nroute = ["../routes/line.geojson", "/elsewhere/x.geojson"]\n'
                              'koppen = true\ndpi = 200\noutput = "trip.png"\n')
        path = recipe_store.write_recipe(*dirs, "trip", "", {"city": "Lisbon", "wind": "july", "dpi": 100},
                                         FIELDS, overwrite=True)
        assert path.read_text(encoding="utf-8") == (
            'city = "Lisbon"\nwind = "july"\nroute = ["/elsewhere/x.geojson"]\ndpi = 200\noutput = "trip.png"\n')

    def test_an_existing_recipe_needs_overwrite(self, dirs):
        _recipe(dirs, "trip", 'city = "lisbon"\n')
        with pytest.raises(recipe_store.RecipeExists):
            recipe_store.write_recipe(*dirs, "trip", "", {"city": "Paris"}, FIELDS)
        assert (Path(dirs[0]) / "trip.toml").read_text(encoding="utf-8") == 'city = "lisbon"\n'

    @pytest.mark.parametrize("name, request_, match", [
        ("Bad Name", {"city": "Paris"}, "not a recipe name"),
        ("ok", {"city": "Paris", "route": ["nothing.geojson"]}, "no route file 'nothing.geojson'"),
        ("ok", {"city": "Paris", "route": ["../ortho.py"]}, "no route file"),
    ])
    def test_refused(self, dirs, name, request_, match):
        with pytest.raises(recipe_store.RecipeError, match=match):
            recipe_store.write_recipe(*dirs, name, "", request_, FIELDS)
        assert not list(Path(dirs[0]).iterdir())

    def test_delete(self, dirs):
        _recipe(dirs, "trip", 'city = "lisbon"\n')
        recipe_store.delete_recipe(dirs[0], "trip")
        assert not list(Path(dirs[0]).iterdir())
        with pytest.raises(recipe_store.RecipeNotFound):
            recipe_store.delete_recipe(dirs[0], "trip")


class TestRouteFiles:
    def test_listed_with_their_routes_or_why_not(self, dirs):
        (Path(dirs[1]) / "broken.geojson").write_text("{", encoding="utf-8")
        found = recipe_store.list_routes(dirs[1])
        assert found[1] == {"name": "line.geojson", "routes": ["A line"]}
        assert found[0]["name"] == "broken.geojson" and found[0]["routes"] == []
        assert "invalid JSON" in found[0]["error"] and dirs[1] not in found[0]["error"]

    @pytest.mark.parametrize("filename, name", [
        ("Silk Road.json", "silk_road.geojson"), ("routes.geojson", "routes.geojson"), ("???.geojson", "routes.geojson"),
    ])
    def test_upload_names(self, filename, name):
        assert recipe_store.route_file_name(filename) == name

    def test_upload_is_checked_then_saved(self, dirs):
        unnamed = {"type": "Feature", "properties": {}, "geometry": LINE["geometry"]}
        added = recipe_store.add_route_file(dirs[1], "My Trip.geojson", json.dumps(unnamed))
        assert added == {"name": "my_trip.geojson", "routes": ["my_trip"]}
        assert sorted(os.listdir(dirs[1])) == ["line.geojson", "my_trip.geojson"]

    @pytest.mark.parametrize("content, error, match", [
        ("{", recipe_store.RecipeError, r"Bad\.geojson: invalid JSON"),
        (json.dumps({"type": "FeatureCollection", "features": []}), recipe_store.RecipeError, "no routes found"),
        (json.dumps(LINE), recipe_store.RecipeExists, "already a route file 'line.geojson'"),
    ])
    def test_upload_refused(self, dirs, content, error, match):
        name = "line.geojson" if error is recipe_store.RecipeExists else "Bad.geojson"
        with pytest.raises(error, match=match):
            recipe_store.add_route_file(dirs[1], name, content)
        assert os.listdir(dirs[1]) == ["line.geojson"]                  # nothing left behind

    def test_upload_too_large(self, dirs, monkeypatch):
        monkeypatch.setattr(recipe_store, "MAX_ROUTE_BYTES", 10)
        with pytest.raises(recipe_store.RecipeError, match="larger than"):
            recipe_store.add_route_file(dirs[1], "big.geojson", json.dumps(LINE))

    def test_route_paths_only_for_files_in_the_folder(self, dirs):
        assert recipe_store.route_path(dirs[1], "line.geojson") == os.path.join(dirs[1], "line.geojson")
        for name in ("nothing.geojson", "../line.geojson", "line.txt"):
            with pytest.raises(recipe_store.RecipeError):
                recipe_store.route_path(dirs[1], name)
