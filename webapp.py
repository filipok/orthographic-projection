"""A local web app for the globe maps: a form in the browser, the same renderer as the CLI.

Run ``python webapp.py`` (or ``ortho-web`` once installed with the ``web``
extra) and open http://127.0.0.1:8000. A map request takes the CLI's options,
by their argparse names, as JSON; it is checked by :func:`ortho.prepare_render`
like a command line, then rendered by :func:`ortho.generate_orthographic_map`
on a single worker thread, one map at a time.

Settings come from the environment, so the same code runs locally or hosted:

- ``ORTHO_WEB_HOST``, ``ORTHO_WEB_PORT`` (or ``PORT``): where to listen
  (default ``127.0.0.1:8000``).
- ``ORTHO_WEB_OUTPUT_DIR``: where the maps are written (default: a folder in
  the system's temporary directory).
- ``ORTHO_WEB_CACHE_DIR``: the tile cache (default: the CLI's).
- ``ORTHO_WEB_MAX_DPI``: the largest DPI a request may ask for (default 300).
- ``ORTHO_WEB_MAX_QUEUE``: how many maps may wait or render at once (default 10).
- ``ORTHO_WEB_KEEP``: how many finished maps to keep (default 50).
- ``ORTHO_WEB_RECIPES_DIR``, ``ORTHO_WEB_ROUTES_DIR``: the recipes and route
  files the form loads and saves (default: the ``recipes`` and ``routes``
  folders beside this file).
- ``ORTHO_WEB_ATLASES_DIR``: the atlases the atlas page edits and prints
  (default: the ``atlases`` folder beside this file).
- ``ORTHO_WEB_READ_ONLY``: ``1`` to offer recipes, routes and atlases without
  saving, deleting or uploading any (for a server others can reach).

An atlas (see :mod:`atlas`) is built into a PDF on the same worker as the
maps, its maps kept between builds so that only changed maps are drawn again.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

import atlas as atlases
import ortho
import recipe_store
from crops import CROP_NAMES
from ice import FIRST_ICE_YEAR
from soil_properties import DEPTHS as SOIL_DEPTHS, SOIL_PROPERTIES
from vegetation import FIRST_LAND_COVER_YEAR, LATEST_LAND_COVER_YEAR

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
DEFAULT_WEB_DPI = 150

PERIODS = [
    ("year", "Year"),
    ("djf", "Dec–Feb"),
    ("mam", "Mar–May"),
    ("jja", "Jun–Aug"),
    ("son", "Sep–Nov"),
    *((month.lower(), month) for month in (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December")),
]


@dataclass(frozen=True)
class Settings:
    """Where maps go and how much a visitor may ask for."""
    output_dir: str
    cache_dir: str | None = None
    max_dpi: int = 300
    max_queue: int = 10
    keep: int = 50
    recipes_dir: str = str(HERE / "recipes")
    routes_dir: str = str(HERE / "routes")
    atlases_dir: str = str(HERE / "atlases")
    read_only: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            output_dir=env.get("ORTHO_WEB_OUTPUT_DIR") or os.path.join(tempfile.gettempdir(), "ortho_web"),
            cache_dir=env.get("ORTHO_WEB_CACHE_DIR") or None,
            max_dpi=int(env.get("ORTHO_WEB_MAX_DPI", 300)),
            max_queue=int(env.get("ORTHO_WEB_MAX_QUEUE", 10)),
            keep=int(env.get("ORTHO_WEB_KEEP", 50)),
            recipes_dir=env.get("ORTHO_WEB_RECIPES_DIR") or str(HERE / "recipes"),
            routes_dir=env.get("ORTHO_WEB_ROUTES_DIR") or str(HERE / "routes"),
            atlases_dir=env.get("ORTHO_WEB_ATLASES_DIR") or str(HERE / "atlases"),
            read_only=env.get("ORTHO_WEB_READ_ONLY", "").strip().lower() in ("1", "true", "yes", "on"),
        )


class MapRequest(BaseModel):
    """A map, in the CLI's options by their argparse names; anything left out takes the CLI's default.

    Run-level options (output paths, the cache) are the server's. Routes are
    named by their file in the server's routes folder.
    """
    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    provider: str | None = None
    zoom: int | None = None
    dpi: int | None = None
    radius: float | None = None
    rectangle: str | None = None
    both_hemispheres: bool | None = None
    beside: str | None = None
    up: float | None = None
    up_toward: str | None = None
    circles_from: str | None = None
    no_circles: bool | None = None
    koppen: bool | None = None
    koppen_class: list[str] | None = None
    trewartha: bool | None = None
    trewartha_class: list[str] | None = None
    temperature: str | None = None
    precipitation: str | None = None
    humidity: str | None = None
    temperature_over_sea: bool | None = None
    sea_temperature: str | None = None
    koppen_alpha: float | None = None
    elevation: bool | None = None
    elevation_alpha: float | None = None
    landcover: bool | None = None
    landcover_class: list[str] | None = None
    landcover_year: int | None = None
    ndvi: str | None = None
    vegetation_alpha: float | None = None
    biomes: bool | None = None
    biome_class: list[str] | None = None
    ecoregions: bool | None = None
    realms: str | None = None
    realm_regions: bool | None = None
    biome_alpha: float | None = None
    soil: bool | None = None
    soil_class: list[str] | None = None
    soil_alpha: float | None = None
    soil_property: str | None = None
    soil_depth: str | None = None
    wind: str | None = None
    ice: bool | None = None
    ice_year: int | None = None
    crop: list[str] | None = None
    borders: str | None = None
    borders_source: str | None = None
    borders_alpha: float | None = None
    ocean_depth: bool | None = None
    ice_age: bool | None = None
    currents: bool | None = None
    plates: bool | None = None
    earthquakes: float | None = None
    volcanoes: bool | None = None
    rivers: bool | None = None
    basins: bool | None = None
    basins_alpha: float | None = None
    cyclones: int | None = None
    time_zones: bool | None = None
    permafrost: bool | None = None
    permafrost_alpha: float | None = None
    population: int | None = None
    population_alpha: float | None = None
    livestock: str | None = None
    livestock_alpha: float | None = None
    malaria: str | None = None
    malaria_year: int | None = None
    malaria_alpha: float | None = None
    aedes: str | None = None
    species: str | None = None
    species_years: str | None = None
    iucn: str | None = None
    iucn_alpha: float | None = None
    cities: int | None = None
    cities_min: int | None = None
    indicator: str | None = None
    indicator_year: int | None = None
    indicator_alpha: float | None = None
    industry: str | None = None
    shipping: str | None = None
    shipping_alpha: float | None = None
    ports: str | None = None
    air_routes: str | None = None
    relief_units: str | None = None
    relief_alpha: float | None = None
    ro_admin: str | None = None
    ro_admin_alpha: float | None = None
    route: list[str] | None = None
    route_legend: bool | None = None

    def given(self) -> dict[str, Any]:
        """The options set, without blanks: ``None``, ``""`` and empty lists."""
        return {k: v for k, v in self.model_dump().items() if v is not None and v != "" and v != []}


# The CLI flag of each option whose flag is not just its name with dashes
_FLAGS = {"koppen_alpha": "--climate-alpha"}


def _shown_path(path: str) -> str:
    """*path* relative to this folder when it is inside it (as the command is run from here), else as is."""
    relative = os.path.relpath(path, HERE)
    return Path(path).as_posix() if relative.startswith("..") else Path(relative).as_posix()


def cli_command(request: MapRequest, dpi: int, routes_dir: str = str(HERE / "routes")) -> str:
    """The ``ortho.py`` command line that draws the same map as *request*."""
    tokens = ["python", "ortho.py"]
    for name, value in {**request.given(), "dpi": dpi}.items():
        flag = _FLAGS.get(name, "--" + name.replace("_", "-"))
        if name == "route":
            value = [_shown_path(os.path.join(routes_dir, route)) for route in value]
        if value is True:
            tokens.append(flag)
        elif value is False:
            continue
        else:
            for item in value if isinstance(value, list) else [value]:
                tokens += [flag, shlex.quote(str(item))]
    return " ".join(tokens)


def request_args(request: MapRequest, settings: Settings, output_dir: str) -> argparse.Namespace:
    """The CLI's options for *request*: the parser's defaults with the request on top.

    Raises ``ValueError`` for what the command line's parser would refuse, and
    for more than this server allows.
    """
    given = request.given()
    if "city" in given and ("lat" in given or "lon" in given):
        raise ValueError("Choose a city or a latitude and longitude, not both.")
    if "up" in given and "up_toward" in given:
        raise ValueError("Choose a bearing or a place to put at the top, not both.")
    dpi = given.get("dpi", min(DEFAULT_WEB_DPI, settings.max_dpi))
    if dpi > settings.max_dpi:
        raise ValueError(f"This server draws maps of up to {settings.max_dpi} dpi, not {dpi}.")

    args = ortho.build_cli_parser().parse_args([])
    for name, value in given.items():
        setattr(args, name, value)
    if "rectangle" in given:
        try:
            args.rectangle = ortho.parse_aspect(given["rectangle"])   # as the parser's type would
        except ValueError as e:
            raise ValueError(f"{str(e)[0].upper()}{str(e)[1:]}.") from e
    args.route = [recipe_store.route_path(settings.routes_dir, route) for route in given.get("route", [])]
    args.dpi = dpi
    args.output = None
    args.output_dir = output_dir
    args.cache_dir = settings.cache_dir
    return args


@dataclass
class Job:
    """One map, or one atlas's PDF: queued, then running, then done or failed."""
    id: str
    label: str
    filename: str
    command: str
    kwargs: dict[str, Any] = field(repr=False)
    task: Callable[[Job], str] | None = field(default=None, repr=False)   # an atlas: builds it, gives the PDF
    progress: list[int] | None = None                                     # an atlas: [maps done, maps]
    status: str = "queued"
    activity: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    path: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None

    def summary(self, position: int | None) -> dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id,
            "status": self.status,
            "label": self.label,
            "filename": self.filename,
            "command": self.command,
            "position": position,
            "activity": self.activity,
            "warnings": self.warnings,
            "error": self.error,
            "seconds": round(end - self.started, 1) if self.started else None,
            "image": f"/api/maps/{self.id}/image" if self.status == "done" and self.task is None else None,
            "pdf": f"/api/maps/{self.id}/pdf" if self.status == "done" and self.task is not None else None,
            "progress": self.progress,
        }


_SECRET_PARAM = re.compile(r"\b(key|session|token|access_token|api_key)=[^&\s'\"]+", re.IGNORECASE)


def redact(message: str) -> str:
    """*message* without API keys: the configured keys and any ``key=``-style URL parameter.

    The renderer keeps URLs out of its messages, since Google's carry the key; this is a
    second line of defence before a message reaches the page.
    """
    message = _SECRET_PARAM.sub(r"\1=REDACTED", message)
    for name in ortho.API_KEY_ENVS:
        secret = os.environ.get(name)
        if secret and len(secret) >= 8:
            message = message.replace(secret, "REDACTED")
    return message


class _JobLog(logging.Handler):
    """Collects what the renderer logs while a job runs: its latest step and its warnings."""

    def __init__(self, job: Job) -> None:
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__ or record.name.startswith(("uvicorn", "fastapi", "httpx")):
            return
        message = redact(record.getMessage())
        if record.levelno >= logging.WARNING:
            self.job.warnings.append(message)
        else:
            self.job.activity = message


class QueueFull(Exception):
    pass


class MapQueue:
    """Renders maps one at a time on a worker thread and keeps the latest few."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ortho-render")

    def submit(self, request: MapRequest) -> Job:
        """Check *request* like a command line and queue it; ``ValueError`` if it is refused."""
        job_id = uuid.uuid4().hex[:12]
        job_dir = os.path.join(self.settings.output_dir, job_id)
        args = request_args(request, self.settings, job_dir)
        plan = ortho.prepare_render(args)
        return self._queue(Job(job_id, plan.label, plan.kwargs["output_filename"],
                               cli_command(request, args.dpi, self.settings.routes_dir), plan.kwargs))

    def submit_atlas(self, name: str, proof: bool = False) -> Job:
        """Check atlas *name* and every map in it, and queue its PDF; ``ValueError`` if it is refused.

        Its maps are kept in a folder of their own between builds, so a build
        draws only the maps that changed; a *proof* draws none.
        """
        settings = self.settings
        path = atlases.atlas_path(settings.atlases_dir, name)
        if not path.is_file():
            raise LookupError(f"There is no atlas {name!r}.")
        book = atlases.load_atlas(str(path))
        if book.dpi > settings.max_dpi:
            raise ValueError(f"This server draws maps of up to {settings.max_dpi} dpi, not {book.dpi}.")
        maps_dir = os.path.join(settings.output_dir, "atlases", name)
        atlases.plan_maps(book, maps_dir, settings.cache_dir)          # refuse a bad map now, not mid-build
        job_id = uuid.uuid4().hex[:12]
        output = os.path.join(settings.output_dir, job_id, f"atlas_{name}{'_proof' if proof else ''}.pdf")

        def build(job: Job) -> str:
            def progress(done: int, total: int, _title: str) -> None:
                job.progress = [done, total]

            result = atlases.build_atlas(book, output, maps_dir, proof=proof, cache_dir=settings.cache_dir,
                                         progress=progress)
            job.warnings += [f"Could not draw the map of page {key}: {redact(error)}"
                             for key, error in result.failures.items()]
            return result.output

        command = f"python atlas.py {shlex.quote(_shown_path(str(path)))}" + (" --proof" if proof else "")
        return self._queue(Job(job_id, book.title, os.path.basename(output), command, {}, task=build))

    def _queue(self, job: Job) -> Job:
        job_id = job.id
        with self._lock:
            waiting = sum(j.status in ("queued", "running") for j in self._jobs.values())
            if waiting >= self.settings.max_queue:
                raise QueueFull(f"{waiting} maps are already waiting; try again in a minute.")
            self._jobs[job_id] = job
            self._futures[job_id] = self._executor.submit(self._run, job)
            self._prune()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def position(self, job: Job) -> int | None:
        """How many maps are ahead of a queued *job*."""
        if job.status != "queued":
            return None
        with self._lock:
            ahead = [j for j in self._jobs.values() if j.status in ("queued", "running")]
        return next((i for i, j in enumerate(ahead) if j is job), None)  # None if it started meanwhile

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        """Block until *job_id* has finished (for tests and scripts)."""
        self._futures[job_id].result(timeout)
        return self._jobs[job_id]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: Job) -> None:
        job.status, job.started = "running", time.time()
        handler = _JobLog(job)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            job.path = job.task(job) if job.task else ortho.generate_orthographic_map(**job.kwargs)
            job.status = "done"
        except Exception as e:  # anything the renderer raises is shown on the page
            logger.exception("Map %s failed", job.id)
            job.error = redact(str(e)) or type(e).__name__
            job.status = "failed"
        finally:
            root.removeHandler(handler)
            job.activity = None
            job.finished = time.time()

    def _prune(self) -> None:
        """Forget the oldest finished maps beyond ``keep``, and delete their files."""
        finished = [j for j in self._jobs.values() if j.status in ("done", "failed")]
        for job in finished[:max(0, len(finished) - self.settings.keep)]:
            del self._jobs[job.id]
            self._futures.pop(job.id, None)
            shutil.rmtree(os.path.join(self.settings.output_dir, job.id), ignore_errors=True)


def _google_key_set() -> bool:
    try:
        ortho.resolve_api_key()
    except ortho.GoogleTilesError:
        return False
    return True


def map_options(settings: Settings) -> dict[str, Any]:
    """What the form offers: the CLI's choices and limits, and this server's."""
    google = _google_key_set()
    return {
        "cities": list(ortho.MAJOR_METROPOLISES),
        "providers": [p for p in ortho.TILE_PROVIDERS if google or p not in ortho.GOOGLE_MAP_TYPES],
        "dpi": {"default": min(DEFAULT_WEB_DPI, settings.max_dpi), "min": ortho.MIN_DPI, "max": settings.max_dpi},
        "zoom": {"default": ortho.DEFAULT_ZOOM, "globe_max": ortho.MAX_GLOBE_ZOOM,
                 "radius_max": ortho.MAX_RADIUS_ZOOM},
        "radius": {"default": ortho.DEFAULT_RADIUS_KM, "min": ortho.MIN_RADIUS_KM, "max": ortho.MAX_RADIUS_KM},
        "alpha": {"classification": ortho.CLASSIFICATION_ALPHA, "climate_mean": ortho.CLIMATE_MEAN_ALPHA,
                  "elevation": 0.8, "vegetation": 0.7, "biome": 0.6, "soil": 0.6, "borders": 0.55,
                  "basins": 0.5, "permafrost": 0.7, "population": 0.7, "livestock": 0.7, "malaria": 0.75,
                  "iucn": 0.45, "indicator": 0.75, "shipping": 0.85, "relief": 0.55,
                  "ro_admin": 0.0},
        "periods": PERIODS,
        "soil_properties": {name: prop.title for name, prop in SOIL_PROPERTIES.items()},
        "soil_depths": list(SOIL_DEPTHS),
        "land_cover_years": [FIRST_LAND_COVER_YEAR, LATEST_LAND_COVER_YEAR],
        "first_ice_year": FIRST_ICE_YEAR,
        "population_years": list(ortho.POPULATION_YEARS),
        "cities_years": list(ortho.CITIES_YEARS),
        "industry": {**{name: f"group: {', '.join(sectors)}" for name, sectors in ortho.INDUSTRY_GROUPS.items()},
                     **{name: sector.label for name, sector in ortho.INDUSTRY_SECTORS.items()}},
        "indicators": {**{name: preset.title for name, preset in ortho.INDICATOR_PRESETS.items()},
                       **{f"eurostat:{name}": f"{preset.title} (European regions)"
                          for name, preset in ortho.EUROSTAT_PRESETS.items()}},
        "shipping": {name: kind.label for name, kind in ortho.SHIP_KINDS.items()},
        "ports": {name: kind.label for name, kind in ortho.PORT_KINDS.items()},
        "relief_units": {"units": "Every unit, down to single depressions and plains (820)",
                         "regions": "The 16 major regions"},
        "ro_admin": {"counties": "The 41 counties and Bucharest, with their seats",
                     "uats": "Counties, and their 3,181 municipalities, towns and communes"},
        "livestock": list(ortho.LIVESTOCK_SPECIES),
        "malaria_years": [ortho.FIRST_MALARIA_YEAR, ortho.LATEST_MALARIA_YEAR],
        "aedes": list(ortho.AEDES_CHOICES),
        "first_cyclone_year": ortho.FIRST_CYCLONE_YEAR,
        "earthquakes": {"default": ortho.DEFAULT_MIN_MAGNITUDE, "min": ortho.MIN_MAGNITUDE, "max": ortho.MAX_MAGNITUDE},
        "crops": list(CROP_NAMES),
        "editable": not settings.read_only,
        "atlas": {"papers": list(atlases.PAPER_SIZES), "languages": list(atlases.LABELS),
                  "dpi": {"default": atlases.DEFAULT_ATLAS_DPI, "min": atlases.MIN_ATLAS_DPI,
                          "max": min(atlases.MAX_ATLAS_DPI, settings.max_dpi)},
                  "text_scale": {"default": 1.0, "min": ortho.MIN_TEXT_SCALE, "max": ortho.MAX_TEXT_SCALE}},
    }


class RecipeSave(BaseModel):
    """A recipe to save: the form's map request and the description written above it."""
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    request: MapRequest
    overwrite: bool = False


class RouteUpload(BaseModel):
    """A GeoJSON file from the browser: its name and its text."""
    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str


class AtlasSave(BaseModel):
    """An atlas to save, as ``GET /api/atlases/{name}`` gives it: its settings and its pages."""
    model_config = ConfigDict(extra="forbid")

    atlas: dict[str, Any]
    overwrite: bool = False


class AtlasBuild(BaseModel):
    """Build an atlas's PDF; a proof lays out the pages without drawing the maps not drawn yet."""
    model_config = ConfigDict(extra="forbid")

    proof: bool = False


# The options a recipe can give the form, in the order a saved recipe lists them
FORM_FIELDS = list(MapRequest.model_fields)


def create_app(settings: Settings | None = None) -> FastAPI:
    """The web app; settings from the environment unless given."""
    settings = settings or Settings.from_env()
    os.makedirs(settings.output_dir, exist_ok=True)
    queue = MapQueue(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        queue.shutdown()

    app = FastAPI(title="Orthographic globe maps", lifespan=lifespan)
    app.state.settings = settings
    app.state.maps = queue

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/atlas", include_in_schema=False)
    def atlas_page() -> FileResponse:
        return FileResponse(WEB_DIR / "atlas.html")

    @app.get("/api/options")
    def options() -> dict[str, Any]:
        return map_options(settings)

    @app.post("/api/maps", status_code=202)
    def create_map(request: MapRequest) -> dict[str, Any]:
        try:
            job = queue.submit(request)
        except QueueFull as e:
            raise HTTPException(503, str(e)) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return job.summary(queue.position(job))

    def find(job_id: str) -> Job:
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(404, "No such map; it may have been cleared to make room for newer ones.")
        return job

    @app.get("/api/maps/{job_id}")
    def map_status(job_id: str) -> dict[str, Any]:
        job = find(job_id)
        return job.summary(queue.position(job))

    def writable() -> None:
        if settings.read_only:
            raise HTTPException(403, "This server shows recipes and routes but does not change them.")

    @app.get("/api/recipes")
    def recipes() -> list[dict[str, str]]:
        return recipe_store.list_recipes(settings.recipes_dir)

    @app.get("/api/recipes/{name}")
    def recipe(name: str) -> dict[str, Any]:
        try:
            return recipe_store.read_recipe(settings.recipes_dir, settings.routes_dir, name, FORM_FIELDS)
        except recipe_store.RecipeNotFound as e:
            raise HTTPException(404, str(e)) from None
        except recipe_store.RecipeError as e:
            raise HTTPException(400, str(e)) from None

    @app.put("/api/recipes/{name}")
    def save_recipe(name: str, body: RecipeSave) -> dict[str, str]:
        writable()
        try:
            for route in body.request.given().get("route", []):
                recipe_store.route_path(settings.routes_dir, route)
            path = recipe_store.write_recipe(settings.recipes_dir, settings.routes_dir, name, body.description,
                                             body.request.given(), FORM_FIELDS, overwrite=body.overwrite)
        except recipe_store.RecipeExists as e:
            raise HTTPException(409, str(e)) from None
        except recipe_store.RecipeError as e:
            raise HTTPException(400, str(e)) from None
        return {"name": name, "file": _shown_path(str(path))}

    @app.delete("/api/recipes/{name}", status_code=204)
    def delete_recipe(name: str) -> None:
        writable()
        try:
            recipe_store.delete_recipe(settings.recipes_dir, name)
        except recipe_store.RecipeNotFound as e:
            raise HTTPException(404, str(e)) from None
        except recipe_store.RecipeError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/routes")
    def routes() -> list[dict[str, Any]]:
        return recipe_store.list_routes(settings.routes_dir)

    @app.post("/api/routes", status_code=201)
    def upload_route(body: RouteUpload) -> dict[str, Any]:
        writable()
        try:
            return recipe_store.add_route_file(settings.routes_dir, body.filename, body.content)
        except recipe_store.RecipeExists as e:
            raise HTTPException(409, str(e)) from None
        except recipe_store.RecipeError as e:
            raise HTTPException(400, str(e)) from None

    def atlas_errors(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except (FileNotFoundError, LookupError) as e:
            raise HTTPException(404, str(e)) from None
        except FileExistsError as e:
            raise HTTPException(409, str(e)) from None
        except ValueError as e:                     # AtlasError, RecipeError and the renderer's checks
            raise HTTPException(400, str(e)) from None

    @app.get("/api/atlases")
    def atlas_list() -> list[dict[str, Any]]:
        return atlases.list_atlases(settings.atlases_dir)

    @app.get("/api/atlases/{name}")
    def atlas_read(name: str) -> dict[str, Any]:
        return atlas_errors(lambda: atlases.read_atlas_json(settings.atlases_dir, settings.routes_dir, name,
                                                            FORM_FIELDS))

    @app.put("/api/atlases/{name}")
    def atlas_save(name: str, body: AtlasSave) -> dict[str, str]:
        writable()
        path = atlas_errors(lambda: atlases.write_atlas(settings.atlases_dir, settings.routes_dir, name, body.atlas,
                                                        FORM_FIELDS, body.overwrite, settings.cache_dir))
        return {"name": name, "file": _shown_path(str(path))}

    @app.delete("/api/atlases/{name}", status_code=204)
    def atlas_delete(name: str) -> None:
        writable()
        atlas_errors(lambda: atlases.delete_atlas(settings.atlases_dir, name))

    @app.get("/api/atlases/{name}/maps/{page}")
    def atlas_map(name: str, page: str) -> dict[str, Any]:
        """The whole map of a page (``cover`` or its number) as the form's request."""
        request = atlas_errors(lambda: atlases.map_request(settings.atlases_dir, settings.routes_dir, name, page,
                                                           FORM_FIELDS))
        title = atlas_errors(lambda: atlases.page_title(settings.atlases_dir, name, page))
        return {"name": name, "page": page, "title": title, "request": request}

    @app.put("/api/atlases/{name}/maps/{page}")
    def atlas_set_map(name: str, page: str, request: MapRequest) -> dict[str, str]:
        writable()
        path = atlas_errors(lambda: atlases.set_page_map(settings.atlases_dir, settings.routes_dir, name, page,
                                                         request.given(), FORM_FIELDS, settings.cache_dir))
        return {"name": name, "file": _shown_path(str(path))}

    @app.post("/api/atlases/{name}/pdf", status_code=202)
    def atlas_build(name: str, body: AtlasBuild | None = None) -> dict[str, Any]:
        try:
            job = atlas_errors(lambda: queue.submit_atlas(name, proof=bool(body and body.proof)))
        except QueueFull as e:
            raise HTTPException(503, str(e)) from None
        return job.summary(queue.position(job))

    @app.get("/api/maps/{job_id}/pdf")
    def atlas_pdf(job_id: str, download: bool = False) -> FileResponse:
        job = find(job_id)
        if job.status != "done" or job.task is None or not job.path or not os.path.isfile(job.path):
            raise HTTPException(404, "This atlas has no PDF yet.")
        return FileResponse(job.path, media_type="application/pdf", filename=job.filename,
                            content_disposition_type="attachment" if download else "inline")

    @app.get("/api/maps/{job_id}/image")
    def map_image(job_id: str, download: bool = False) -> FileResponse:
        job = find(job_id)
        if job.status != "done" or job.task is not None or not job.path or not os.path.isfile(job.path):
            raise HTTPException(404, "This map has no image yet.")
        return FileResponse(job.path, media_type="image/png", filename=job.filename,
                            content_disposition_type="attachment" if download else "inline")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


def main(argv: list[str] | None = None) -> None:
    """Serve the web app (``ortho-web``)."""
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the orthographic globe maps as a local web app.")
    parser.add_argument("--host", default=os.environ.get("ORTHO_WEB_HOST", "127.0.0.1"),
                        help="Address to listen on (default: 127.0.0.1, this computer only).")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("ORTHO_WEB_PORT") or os.environ.get("PORT") or 8000),
                        help="Port to listen on (default: 8000).")
    options = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ortho.load_env_files()
    uvicorn.run(create_app(), host=options.host, port=options.port)


if __name__ == "__main__":
    main()
