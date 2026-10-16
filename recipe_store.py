"""Recipes and route files for the web app: list, read, write and delete them on disk.

A recipe is a TOML file of ``ortho.py`` options (see ``--config``). Here it is
read into a map request, in the options' argparse names, which the web form
fills itself from, and written back from one: the request's options, the
recipe's description as its leading comment, and whatever the form does not
cover (``output``, ``dpi``, routes outside the routes folder) kept from the
file as it was. Route paths are written relative to the recipe, as the
bundled recipes have them.

Route files are the GeoJSON files in the routes folder; a new one is checked
with :func:`routes.load_routes` before it is saved.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import textwrap
import tomllib
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

import ortho
from routes import load_routes
from wind import resolve_wind_period

RECIPE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ROUTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}\.geojson$")
MAX_ROUTE_BYTES = 5_000_000
# Options whose value is a period, stored in the form's spelling ("july", "djf", "year")
PERIOD_OPTIONS = ("temperature", "precipitation", "humidity", "wind", "sea_temperature")
# Map options the form shows but a recipe keeps as it is: dpi is the output's, not the map's
KEPT_OPTIONS = {"dpi"}
_LINE = 88


class RecipeError(ValueError):
    """A recipe or route file that cannot be read, or a name or request that cannot be saved."""


class RecipeNotFound(LookupError):
    """No recipe or route file by that name."""


class RecipeExists(FileExistsError):
    """A recipe or route file by that name is there already."""


def _actions() -> dict[str, argparse.Action]:
    return ortho._option_actions(ortho.build_cli_parser())


def _recipe_path(recipes_dir: str, name: str) -> Path:
    if not RECIPE_NAME.match(name):
        raise RecipeError(f"{name!r} is not a recipe name: use lower-case letters, digits, '_' and '-'.")
    return Path(recipes_dir) / f"{name}.toml"


def _write_atomically(path: Path, text: str) -> None:
    fd, part = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(part, path)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


def description(text: str) -> str:
    """The comment at the top of a recipe, before its first option, as plain text.

    Comment lines join into paragraphs; an empty comment line ends one.
    """
    paragraphs: list[list[str]] = [[]]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        words = stripped.lstrip("#").strip()
        if words:
            paragraphs[-1].append(words)
        elif paragraphs[-1]:
            paragraphs.append([])
    return "\n\n".join(" ".join(p) for p in paragraphs if p)


def list_recipes(recipes_dir: str) -> list[dict[str, str]]:
    """Every recipe's name and description, by name."""
    found = []
    for path in sorted(Path(recipes_dir).glob("*.toml")):
        if RECIPE_NAME.match(path.stem):
            found.append({"name": path.stem, "description": description(path.read_text(encoding="utf-8"))})
    return found


def _load(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise RecipeError(f"{path.name} is not valid TOML: {e}") from None


def _route_name(entry: str, recipe_dir: Path, routes_dir: str) -> str | None:
    """The route file's name when *entry*, relative to the recipe, is in the routes folder."""
    path = (recipe_dir / entry).resolve()
    if path.parent == Path(routes_dir).resolve() and ROUTE_NAME.match(path.name):
        return path.name
    return None


def read_recipe(recipes_dir: str, routes_dir: str, name: str, fields: Collection[str]) -> dict[str, Any]:
    """Recipe *name* as ``{"name", "description", "request", "kept"}``.

    *request* holds the options among *fields* (the web form's), by argparse
    name, periods spelled as the form does, the city as it is listed and
    route files by name. *kept* names what the form leaves alone, which a save
    keeps as it is.
    """
    path = _recipe_path(recipes_dir, name)
    if not path.is_file():
        raise RecipeNotFound(f"There is no recipe {name!r}.")
    text, data = _load(path)
    actions = _actions()
    request: dict[str, Any] = {}
    kept: list[str] = []
    for key, value in data.items():
        action = actions.get(key.replace("_", "-"))
        if action is None:
            raise RecipeError(f"{path.name}: unknown option {key!r}.")
        dest = action.dest
        if dest == "route":
            names = []
            for entry in value if isinstance(value, list) else [value]:
                route = _route_name(str(entry), path.parent, routes_dir)
                if route is None:
                    kept.append(f"route = {json.dumps(entry)}")
                else:
                    names.append(route)
            if names:
                request["route"] = names
            continue
        if dest not in fields or dest in KEPT_OPTIONS:
            kept.append(f"{key} = {_toml(value)}")
            if dest in fields:
                request[dest] = value                       # shown in the form, written as it was
            continue
        if isinstance(action, argparse._AppendAction) and not isinstance(value, list):
            value = [value]
        if dest in PERIOD_OPTIONS and isinstance(value, str):
            try:
                value = resolve_wind_period(value, kind=dest.replace("_", " ")).key
            except ValueError as e:
                raise RecipeError(f"{path.name}: {e}") from None
        if dest == "borders" and isinstance(value, int):
            value = str(value)                              # a year, written as a number
        if dest == "city" and isinstance(value, str):
            value = next((city for city in ortho.MAJOR_METROPOLISES if city.lower() == value.lower()), value)
        request[dest] = value
    return {"name": name, "description": description(text), "request": request, "kept": kept}


def _kept_entries(path: Path, routes_dir: str, fields: Collection[str]) -> list[tuple[str, Any]]:
    """The options of the recipe at *path* that the form leaves alone, as ``(key, value)``."""
    if not path.is_file():
        return []
    _, data = _load(path)
    actions = _actions()
    kept: list[tuple[str, Any]] = []
    for key, value in data.items():
        action = actions.get(key.replace("_", "-"))
        if action is None:
            continue
        if action.dest == "route":
            outside = [entry for entry in (value if isinstance(value, list) else [value])
                       if _route_name(str(entry), path.parent, routes_dir) is None]
            if outside:
                kept.append(("route", outside))
        elif action.dest not in fields or action.dest in KEPT_OPTIONS:
            kept.append((key, value))
    return kept


def _toml(value: Any, key_width: int = 0) -> str:
    """*value* as TOML: a string, number, boolean or list of them, a long list over several lines."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)        # JSON's escapes are TOML's
    if isinstance(value, list):
        items = [_toml(item) for item in value]
        one_line = f"[{', '.join(items)}]"
        if key_width + len(one_line) <= _LINE:
            return one_line
        return "[\n" + "".join(f"    {item},\n" for item in items) + "]"
    raise RecipeError(f"cannot write {value!r} to a recipe")


def _comment(text: str) -> list[str]:
    lines: list[str] = []
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", text.strip())):
        if not paragraph:
            continue
        if lines:
            lines.append("#")
        # Only at spaces, so the lines join back into the same text
        lines += [f"# {line}" for line in textwrap.wrap(" ".join(paragraph.split()), _LINE - 2,
                                                         break_on_hyphens=False, break_long_words=False)]
    return lines


def recipe_text(description_text: str, entries: Iterable[tuple[str, Any]]) -> str:
    """A recipe file: the description as comment lines, then one option per line."""
    lines = _comment(description_text)
    for key, value in entries:
        lines.append(f"{key} = {_toml(value, len(key) + 3)}")
    return "\n".join(lines) + "\n"


def write_recipe(
    recipes_dir: str, routes_dir: str, name: str, description_text: str, request: dict[str, Any],
    fields: Collection[str], overwrite: bool = False,
) -> Path:
    """Save *request* (options by argparse name, routes by file name) as recipe *name*.

    Raises :class:`RecipeExists` if it is there and *overwrite* is false, and
    :class:`RecipeError` for a bad name or an unknown route file.
    """
    path = _recipe_path(recipes_dir, name)
    if path.exists() and not overwrite:
        raise RecipeExists(f"There is already a recipe {name!r}.")
    long_names = {action.dest: name for name, action in reversed(_actions().items())}  # the first spelling
    entries: list[tuple[str, Any]] = []
    for dest in fields:
        value = request.get(dest)
        if dest in KEPT_OPTIONS or value is None or value is False or value == "" or value == []:
            continue
        if dest == "route":
            value = [os.path.relpath(route_path(routes_dir, route), recipes_dir).replace(os.sep, "/")
                     for route in value]
        entries.append((long_names[dest], value))
    for key, value in _kept_entries(path, routes_dir, fields):
        if key == "route":
            routes = next((v for k, v in entries if k == "route"), [])
            entries = [(k, v) for k, v in entries if k != "route"] + [("route", routes + value)]
        else:
            entries.append((key, value))
    os.makedirs(recipes_dir, exist_ok=True)
    _write_atomically(path, recipe_text(description_text, entries))
    return path


def delete_recipe(recipes_dir: str, name: str) -> None:
    path = _recipe_path(recipes_dir, name)
    if not path.is_file():
        raise RecipeNotFound(f"There is no recipe {name!r}.")
    path.unlink()


# ---------------------------------------------------------------------------
# Route files
# ---------------------------------------------------------------------------


def route_path(routes_dir: str, name: str) -> str:
    """The path of route file *name*; :class:`RecipeError` unless it is one of the routes folder's."""
    path = Path(routes_dir) / name
    if not ROUTE_NAME.match(name) or not path.is_file():
        raise RecipeError(f"There is no route file {name!r}.")
    return str(path)


def _summary(name: str, path: str) -> dict[str, Any]:
    try:
        routes = load_routes(path)
    except (OSError, ValueError) as e:
        return {"name": name, "routes": [], "error": str(e).replace(path, name)}
    return {"name": name, "routes": [route.name for route in routes]}


def list_routes(routes_dir: str) -> list[dict[str, Any]]:
    """Every route file's name and the routes in it (or why it cannot be read), by name."""
    return [_summary(path.name, str(path)) for path in sorted(Path(routes_dir).glob("*.geojson"))
            if ROUTE_NAME.match(path.name)]


def route_file_name(filename: str) -> str:
    """A safe name in the routes folder for an uploaded *filename*: ``"Silk Road.json"`` -> ``"silk_road.geojson"``."""
    stem = re.sub(r"[^a-z0-9_-]+", "_", Path(filename).stem.lower()).strip("_-")[:70]
    return f"{stem or 'routes'}.geojson"


def add_route_file(routes_dir: str, filename: str, content: str) -> dict[str, Any]:
    """Save GeoJSON *content* in the routes folder, under a name made from *filename*.

    Raises :class:`RecipeExists` if that name is taken and :class:`RecipeError`
    if the content is too large or is not routes :func:`routes.load_routes` can read.
    """
    name = route_file_name(filename)
    path = Path(routes_dir) / name
    if path.exists():
        raise RecipeExists(f"There is already a route file {name!r}; rename yours and upload it again.")
    if len(content.encode("utf-8")) > MAX_ROUTE_BYTES:
        raise RecipeError(f"{filename} is larger than {MAX_ROUTE_BYTES // 1_000_000} MB.")
    os.makedirs(routes_dir, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=routes_dir, prefix=f".{name}.", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        try:
            load_routes(part)
        except ValueError as e:
            raise RecipeError(str(e).replace(part, filename)) from None
        os.replace(part, path)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return _summary(name, str(path))           # unnamed routes take the file's name, so read it there
