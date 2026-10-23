"""Atlases: many maps bound into one PDF, with a cover, contents, sections and text.

An atlas is a TOML file (``atlases/romania_school.toml`` is one): a title and
a few settings, then its pages in order. A page is a section title, a map or
a page of text, and a map page has a title, a caption, questions for the
reader and the map's options, as a recipe holds them::

    title = "School atlas"
    language = "en"            # the labels: "en" or "ro"

    [[pages]]
    type = "section"
    title = "Europe"
    text = "What this part shows."

    [[pages]]
    title = "Relief and rivers"
    text = "A caption below the map."
    questions = ["Which river ...?"]
    [pages.map]
    lat = 50
    lon = 12
    radius = 2200
    elevation = true
    rivers = true

Run ``python atlas.py atlases/romania_school.toml`` to draw every map (each
checked like a command line first) and write the PDF. The maps are kept in a
folder beside it and drawn again only when their options change, so editing
the text and building again takes seconds; ``--proof`` lays the pages out
with a placeholder for every map, without drawing any.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import io
import json
import logging
import os
import re
import sys
import textwrap
import tomllib
import unicodedata
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from PIL import Image

import frame
import ortho
import recipe_store

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
ATLAS_NAME = recipe_store.RECIPE_NAME

# Paper sizes in inches, portrait
PAPER_SIZES = {"a4": (8.27, 11.69), "a3": (11.69, 16.54), "letter": (8.5, 11.0)}
DEFAULT_ATLAS_DPI = 120         # the maps' resolution: a 20 in globe at 120 dpi is 2,400 px
MIN_ATLAS_DPI, MAX_ATLAS_DPI = 50, 300
IMAGE_PPI = 300                 # maps are scaled down to this on the page, to keep the PDF small
PAGE_KINDS = ("map", "section", "text")
ORIENTATIONS = ("auto", "portrait", "landscape")
LANDSCAPE_ASPECT = 1.25         # "auto" turns a page whose map is this much wider than tall
ATLAS_KEYS = ("title", "subtitle", "author", "edition", "paper", "dpi", "text_scale", "language", "cover", "pages")
PAGE_KEYS = ("type", "title", "text", "questions", "orientation", "recipe", "map")
COVER_KEYS = ("recipe", "map")
# Options the atlas sets for every map, which a page's map may not
# The options whose names a map writes in the atlas's language (see ortho's --language)
NAMED_IN_LANGUAGE = ("borders",)
RUN_OPTIONS = {"output", "output_dir", "dpi", "cache_dir", "no_cache", "config"}
ACCENTS = ("#1d5c8c", "#2f7d3a", "#b0532a", "#6c4a9e", "#8a6d1c", "#a23a5a")
INK, GREY, RULE, TINT = "#1f2328", "#5b636e", "#c9ced6", "#f2f4f7"

LABELS = {
    "en": {"contents": "Contents", "questions": "Questions and exercises", "part": "Part",
           "proof": "The map is drawn when the atlas is built", "failed": "This map could not be drawn",
           "in_part": "In this part"},
    "ro": {"contents": "Cuprins", "questions": "Întrebări și exerciții", "part": "Partea",
           "proof": "Harta va fi desenată la construirea atlasului", "failed": "Harta nu a putut fi desenată",
           "in_part": "În această parte"},
}


class AtlasError(ValueError):
    """An atlas file that cannot be read, or a page whose map would be refused."""


@dataclass
class Page:
    """One page: a section title, a map with its caption, or a page of text."""
    kind: str
    title: str
    text: str = ""
    questions: list[str] = field(default_factory=list)
    orientation: str = "auto"
    recipe: str | None = None                     # as written: relative to the atlas file
    map: dict[str, Any] = field(default_factory=dict)


@dataclass
class Atlas:
    """An atlas file, read: its settings and its pages in order."""
    path: str
    title: str
    subtitle: str = ""
    author: str = ""
    edition: str = ""
    paper: str = "a4"
    dpi: int = DEFAULT_ATLAS_DPI
    text_scale: float = 1.0                       # every map's, unless its own map table sets text-scale
    language: str = "en"
    cover: Page | None = None                     # a map for the cover, without text
    pages: list[Page] = field(default_factory=list)

    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def labels(self) -> dict[str, str]:
        return LABELS[self.language]

    def maps(self) -> list[tuple[str, Page]]:
        """The pages with a map, the cover first, each with a key naming it: ``"cover"`` or its number."""
        found = [("cover", self.cover)] if self.cover is not None else []
        return found + [(str(i), page) for i, page in enumerate(self.pages, 1) if page.kind == "map"]


# ---------------------------------------------------------------------------
# Reading an atlas
# ---------------------------------------------------------------------------


def _unknown(keys: Collection[str], allowed: Sequence[str], where: str) -> None:
    for key in keys:
        if key not in allowed:
            close = difflib.get_close_matches(key, allowed, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise AtlasError(f"{where}: unknown key {key!r}.{hint}")


def _string(data: dict[str, Any], key: str, where: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise AtlasError(f"{where}: {key} must be text, not {value!r}")
    return value


def _map_options(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AtlasError(f"{where}: map must be a table of map options, like a recipe's")
    actions = ortho._option_actions(ortho.build_cli_parser())
    for key in value:
        action = actions.get(key.replace("_", "-"))
        if action is not None and action.dest in RUN_OPTIONS:
            raise AtlasError(f"{where}: {key} is set for the whole atlas, not for one map")
    return dict(value)


def _page(data: Any, where: str) -> Page:
    if not isinstance(data, dict):
        raise AtlasError(f"{where} must be a table")
    _unknown(data, PAGE_KEYS, where)
    has_map = "map" in data or "recipe" in data
    kind = _string(data, "type", where, "map" if has_map else "text")
    if kind not in PAGE_KINDS:
        raise AtlasError(f"{where}: type must be one of {', '.join(PAGE_KINDS)}, not {kind!r}")
    if kind != "map" and has_map:
        raise AtlasError(f"{where}: a {kind} page has no map; leave out map and recipe, or make it type = \"map\"")
    if kind == "map" and not has_map:
        raise AtlasError(f"{where}: a map page needs a map table or a recipe")
    title = _string(data, "title", where).strip()
    if not title:
        raise AtlasError(f"{where} needs a title")
    questions = data.get("questions", [])
    if isinstance(questions, str):
        questions = [questions]
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise AtlasError(f"{where}: questions must be a list of text")
    orientation = _string(data, "orientation", where, "auto")
    if orientation not in ORIENTATIONS:
        raise AtlasError(f"{where}: orientation must be one of {', '.join(ORIENTATIONS)}, not {orientation!r}")
    recipe = _string(data, "recipe", where) or None
    return Page(kind, title, _string(data, "text", where), [q for q in questions if q.strip()], orientation,
                recipe, _map_options(data.get("map", {}), where))


def parse_atlas(data: dict[str, Any], path: str) -> Atlas:
    """The atlas in *data*, a TOML file's contents read from *path*; :class:`AtlasError` if it is wrong."""
    where = os.path.basename(path)
    _unknown(data, ATLAS_KEYS, where)
    title = _string(data, "title", where).strip()
    if not title:
        raise AtlasError(f"{where} needs a title")
    paper = _string(data, "paper", where, "a4").lower()
    if paper not in PAPER_SIZES:
        raise AtlasError(f"{where}: paper must be one of {', '.join(PAPER_SIZES)}, not {paper!r}")
    language = _string(data, "language", where, "en").lower()
    if language not in LABELS:
        raise AtlasError(f"{where}: language must be one of {', '.join(LABELS)}, not {language!r}")
    dpi = data.get("dpi", DEFAULT_ATLAS_DPI)
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not MIN_ATLAS_DPI <= dpi <= MAX_ATLAS_DPI:
        raise AtlasError(f"{where}: dpi must be a whole number from {MIN_ATLAS_DPI} to {MAX_ATLAS_DPI}")
    text_scale = data.get("text_scale", 1.0)
    if (isinstance(text_scale, bool) or not isinstance(text_scale, (int, float))
            or not ortho.MIN_TEXT_SCALE <= text_scale <= ortho.MAX_TEXT_SCALE):
        raise AtlasError(f"{where}: text_scale must be a number from {ortho.MIN_TEXT_SCALE:g} "
                         f"to {ortho.MAX_TEXT_SCALE:g}")
    cover = None
    if "cover" in data:
        if not isinstance(data["cover"], dict):
            raise AtlasError(f"{where}: cover must be a table holding a map or a recipe")
        _unknown(data["cover"], COVER_KEYS, f"{where} cover")
        cover = _page({**data["cover"], "type": "map", "title": "cover"}, f"{where} cover")
    pages = data.get("pages", [])
    if not isinstance(pages, list):
        raise AtlasError(f"{where}: pages must be written as [[pages]] tables")
    return Atlas(
        path=os.path.abspath(path), title=title, subtitle=_string(data, "subtitle", where),
        author=_string(data, "author", where), edition=str(data.get("edition", "")), paper=paper, dpi=dpi,
        text_scale=float(text_scale), language=language, cover=cover,
        pages=[_page(page, f"{where} page {i}") for i, page in enumerate(pages, 1)],
    )


def load_atlas(path: str) -> Atlas:
    """Read and check the atlas file at *path*."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as e:
        raise AtlasError(f"cannot read atlas {path}: {e.strerror or e}") from None
    except tomllib.TOMLDecodeError as e:
        raise AtlasError(f"atlas {path} is not valid TOML: {e}") from None
    return parse_atlas(data, path)


# ---------------------------------------------------------------------------
# Planning the maps
# ---------------------------------------------------------------------------


class MapPlan(NamedTuple):
    """One map of an atlas, checked: where it goes and how to draw it."""
    key: str                    # "cover" or the page's number
    page: Page
    path: str                   # the finished PNG, named after its options
    plan: ortho.RenderPlan

    @property
    def cached(self) -> bool:
        return os.path.isfile(self.path)


def _slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:40] or "map"


_MAP_FILE = re.compile(r"^[a-z0-9-]+-[0-9a-f]{12}\.png$")
# What draws differently, hashed into a map's file name; the rest is where it goes
_NOT_HASHED = RUN_OPTIONS | {"list_crops"}


@functools.cache
def _defaults() -> dict[str, Any]:
    """Each command-line option's default, which a map's file name leaves out."""
    return {action.dest: action.default for action in ortho.build_cli_parser()._actions}


def page_args(atlas: Atlas, page: Page, where: str, output: str, cache_dir: str | None = None) -> argparse.Namespace:
    """The command-line options that draw *page*'s map into *output*: its recipe, its map table on top."""
    parser = ortho.build_cli_parser()

    def refuse(message: str) -> None:
        raise AtlasError(f"{where}: {message}")

    parser.error = refuse  # type: ignore[method-assign]  # a bad option is the atlas's error, not an exit
    tokens = ortho.option_tokens(parser, page.map, "map", atlas.base_dir)
    if page.recipe:
        recipe = os.path.join(atlas.base_dir, page.recipe)
        tokens = ortho._recipe_tokens(parser, recipe, tokens) + tokens
    if atlas.text_scale != 1:
        tokens = [f"--text-scale={atlas.text_scale:g}"] + tokens       # first: a recipe's or page's own wins
    tokens += [f"--dpi={atlas.dpi}", f"--output={output}"]
    if cache_dir:
        tokens.append(f"--cache-dir={cache_dir}")
    args = parser.parse_args(tokens)
    # The map's names in the atlas's language, unless the page says otherwise; only the layers that name things in
    # it take it (so the other maps keep their file names)
    own = any(t.startswith("--language") for t in tokens)
    if (atlas.language in ortho.LANGUAGES and atlas.language != "en" and not own
            and any(getattr(args, k) for k in NAMED_IN_LANGUAGE)):
        args.language = atlas.language
    return args


def _fingerprint(args: argparse.Namespace) -> str:
    # Options at their defaults are left out, so a new option leaves the maps drawn before it as they are
    defaults = _defaults()
    options = {k: v for k, v in sorted(vars(args).items())
               if k not in _NOT_HASHED and not (k in defaults and v == defaults[k])}
    digest = hashlib.sha256(json.dumps(options, sort_keys=True, default=str).encode())
    digest.update(str(args.dpi).encode())
    for route in args.route or []:
        try:
            digest.update(Path(route).read_bytes())
        except OSError:
            pass                                    # prepare_render says what is wrong with it
    return digest.hexdigest()[:12]


def plan_maps(atlas: Atlas, maps_dir: str, cache_dir: str | None = None) -> list[MapPlan]:
    """Check every map of *atlas* like a command line, and name the file each is drawn into."""
    plans = []
    for key, page in atlas.maps():
        where = f"{os.path.basename(atlas.path)} " + ("cover" if key == "cover" else f"page {key} ({page.title})")
        args = page_args(atlas, page, where, os.path.join(maps_dir, "pending.png"), cache_dir)
        name = f"{_slug('cover' if key == 'cover' else page.title)}-{_fingerprint(args)}.png"
        args.output = os.path.join(maps_dir, name.replace(".png", ".tmp.png"))
        try:
            plan = ortho.prepare_render(args)
        except ValueError as e:
            raise AtlasError(f"{where}: {e}") from None
        plans.append(MapPlan(key, page, os.path.join(maps_dir, name), plan))
    return plans


def render_maps(
    plans: Sequence[MapPlan], force: bool = False, progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, str]:
    """Draw the maps in *plans* that are not drawn yet; their error messages by key, for those that fail."""
    failures: dict[str, str] = {}
    for done, item in enumerate(plans):
        if progress:
            progress(done, len(plans), item.page.title)
        if item.cached and not force:
            continue
        os.makedirs(os.path.dirname(item.path), exist_ok=True)
        part = item.plan.kwargs["output_filename"]
        logger.info("Drawing %s (%d of %d) …", item.page.title, done + 1, len(plans))
        try:
            ortho.generate_orthographic_map(**item.plan.kwargs)
            os.replace(part, item.path)
        except Exception as e:  # one map failing leaves a placeholder; the rest still print
            logger.error("Could not draw %s: %s", item.page.title, e)
            failures[item.key] = str(e) or type(e).__name__
            if os.path.exists(part):
                os.remove(part)
    if progress:
        progress(len(plans), len(plans), "")
    return failures


def prune_maps(maps_dir: str, keep: Collection[str]) -> int:
    """Delete the maps in *maps_dir* an atlas no longer uses (only files named as it names them)."""
    keep_names = {os.path.basename(path) for path in keep}
    removed = 0
    if os.path.isdir(maps_dir):
        for name in os.listdir(maps_dir):
            if _MAP_FILE.match(name) and name not in keep_names:
                os.remove(os.path.join(maps_dir, name))
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# Laying out the pages
# ---------------------------------------------------------------------------


def _roman(n: int) -> str:
    numerals = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    out = ""
    for value, letters in numerals:
        while n >= value:
            out, n = out + letters, n - value
    return out


def paragraphs(text: str) -> list[str]:
    """*text* as paragraphs: a blank line ends one, other line breaks are spaces."""
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


class _Sheet:
    """One PDF page, laid out in inches from the bottom left."""

    _renderer = RendererAgg(10, 10, 72)          # measures text in points

    def __init__(self, size: tuple[float, float]) -> None:
        self.width, self.height = size
        self.fig = Figure(figsize=size)
        self.fig.patch.set_facecolor("white")
        self.scale = min(size) / PAPER_SIZES["a4"][0]

    def font(self, size: float, bold: bool = False) -> FontProperties:
        return FontProperties(family="DejaVu Sans", size=size * self.scale, weight="bold" if bold else "normal")

    def measure(self, text: str, prop: FontProperties) -> float:
        """The width of *text* in inches."""
        width, _, _ = self._renderer.get_text_width_height_descent(text, prop, ismath=False)
        return width / 72

    def wrap(self, text: str, prop: FontProperties, width: float) -> list[str]:
        """*text* broken into lines no wider than *width* inches, at spaces."""
        lines: list[str] = []
        for paragraph in text.split("\n"):
            line = ""
            for word in paragraph.split():
                trial = f"{line} {word}" if line else word
                if line and self.measure(trial, prop) > width:
                    lines.append(line)
                    line = word
                else:
                    line = trial
            lines.append(line)
        return lines

    def fit(self, text: str, prop: FontProperties, width: float) -> str:
        """*text* on one line no wider than *width* inches, shortened with an ellipsis if need be."""
        lines = self.wrap(text, prop, width)
        if len(lines) == 1:
            return lines[0]
        line = lines[0]
        while line and self.measure(line + " …", prop) > width:
            line = line[:-1]
        return line.rstrip(" ,:;") + " …"

    def text(self, x: float, y: float, s: str, prop: FontProperties, color: str = INK, **kwargs: Any) -> None:
        self.fig.text(x / self.width, y / self.height, s, fontproperties=prop, color=color, **kwargs)

    def rect(self, x: float, y: float, w: float, h: float, color: str, rounded: bool = False) -> None:
        box = (FancyBboxPatch((x / self.width, y / self.height), w / self.width, h / self.height,
                              boxstyle="round,pad=0,rounding_size=0.008", facecolor=color, edgecolor="none",
                              transform=self.fig.transFigure)
               if rounded else
               Rectangle((x / self.width, y / self.height), w / self.width, h / self.height,
                         facecolor=color, edgecolor="none", transform=self.fig.transFigure))
        self.fig.add_artist(box)

    def line(self, x0: float, x1: float, y: float, color: str = RULE, width: float = 0.6, style: str = "-") -> None:
        self.fig.add_artist(Line2D([x0 / self.width, x1 / self.width], [y / self.height] * 2, color=color,
                                   linewidth=width, linestyle=style, transform=self.fig.transFigure))

    def axes(self, x: float, y: float, w: float, h: float) -> Any:
        ax = self.fig.add_axes((x / self.width, y / self.height, w / self.width, h / self.height))
        ax.set_axis_off()
        return ax


def _load_image(path: str, max_px: tuple[int, int]) -> np.ndarray:
    """The map at *path* on white paper, scaled down to fit *max_px* (width, height) if it is larger."""
    with Image.open(path) as img:
        img = img.convert("RGBA")
        paper = Image.new("RGBA", img.size, "white")
        paper.alpha_composite(img)
        rgb = paper.convert("RGB")
    scale = min(max_px[0] / rgb.width, max_px[1] / rgb.height, 1.0)
    if scale < 1.0:
        rgb = rgb.resize((max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))), Image.LANCZOS)
    return np.asarray(rgb)


def _image_aspect(path: str | None, page: Page) -> float:
    """Width over height of the map: the file's, or a guess from its options before it is drawn."""
    if path and os.path.isfile(path):
        with Image.open(path) as img:
            return img.width / img.height
    wide = page.map.get("both-hemispheres", page.map.get("both_hemispheres")) or page.map.get("beside")
    try:
        shape = frame.parse_aspect(page.map["rectangle"]) if page.map.get("rectangle") is not None else 1.0
    except ValueError:
        shape = 1.0   # drawing the map reports the mistake
    return 2.0 * shape if wide else shape


class _Atlas:
    """Writes an atlas's pages into a PDF, numbering them as it goes."""

    def __init__(self, atlas: Atlas, images: dict[str, str], failures: dict[str, str]) -> None:
        self.atlas = atlas
        self.images = images
        self.failures = failures
        self.portrait = PAPER_SIZES[atlas.paper]
        self.landscape = self.portrait[::-1]
        self.margin = 0.6 * min(self.portrait) / PAPER_SIZES["a4"][0]

    # --- the plan: which page goes where -------------------------------------------------------

    def entries(self) -> list[tuple[int, int, Page, str]]:
        """Each page's index, its section's number (0 before the first section) and accent colour."""
        entries, section = [], 0
        for i, page in enumerate(self.atlas.pages):
            if page.kind == "section":
                section += 1
            entries.append((i, section, page, ACCENTS[(section - 1) % len(ACCENTS)] if section else ACCENTS[0]))
        return entries

    def contents_chunks(self) -> list[list[int]]:
        """The indexes of the pages each contents page lists: a section takes two lines, and starts no page's foot."""
        sheet_h = self.portrait[1] - 2 * self.margin - 1.3
        per = max(10, int(sheet_h / 0.27))
        chunks: list[list[int]] = []
        chunk: list[int] = []
        used = 0
        for i, page in enumerate(self.atlas.pages):
            cost = 2 if page.kind == "section" else 1
            if chunk and used + cost + (page.kind == "section") > per:
                chunks.append(chunk)
                chunk, used = [], 0
            chunk.append(i)
            used += cost
        return chunks + [chunk] if chunk or not chunks else chunks

    # --- the pages ----------------------------------------------------------------------------

    def write(self, output: str) -> int:
        """Write the PDF to *output*; the number of pages."""
        atlas = self.atlas
        metadata = {"Title": atlas.title, "Author": atlas.author or None, "Subject": atlas.subtitle or None,
                    "Creator": "orthographic-projection atlas.py"}
        first_page = 2 + len(self.contents_chunks())    # the cover, then the contents
        numbers = [first_page + i for i in range(len(atlas.pages))]
        count = 0
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        with PdfPages(output, metadata={k: v for k, v in metadata.items() if v}) as pdf:
            def emit(sheet: _Sheet) -> None:
                nonlocal count
                pdf.savefig(sheet.fig)
                count += 1

            emit(self.cover())
            for sheet in self.contents(numbers):
                emit(sheet)
            current = ("", ACCENTS[0])
            for i, section, page, accent in self.entries():
                if page.kind == "section":
                    current = (page.title, accent)
                    emit(self.section(i, section, accent, numbers))
                else:
                    emit(self.content(i, current, numbers[i]))
        return count

    def _frame(self, sheet: _Sheet, number: int, section_title: str, accent: str) -> float:
        """The running header and the page number; the top of the space below the header."""
        m = self.margin
        top = sheet.height - m
        sheet.rect(0, sheet.height - 0.12 * sheet.scale, sheet.width, 0.12 * sheet.scale, accent)
        small = sheet.font(8)
        if section_title:
            sheet.text(m, top, section_title.upper(), sheet.font(8, bold=True), accent, va="top")
        sheet.text(sheet.width - m, top, self.atlas.title, small, GREY, va="top", ha="right")
        sheet.line(m, sheet.width - m, top - 0.2 * sheet.scale)
        sheet.text(sheet.width / 2, m * 0.55, str(number), sheet.font(9), GREY, ha="center", va="bottom")
        return top - 0.3 * sheet.scale

    def cover(self) -> _Sheet:
        atlas = self.atlas
        sheet = _Sheet(self.portrait)
        m, s = self.margin, sheet.scale
        band = 3.0 * s
        sheet.rect(0, sheet.height - band, sheet.width, band, ACCENTS[0])
        width = sheet.width - 2 * m
        y = sheet.height - 1.0 * s
        for line in sheet.wrap(atlas.title, sheet.font(30, bold=True), width):
            sheet.text(m, y, line, sheet.font(30, bold=True), "white", va="top")
            y -= 0.5 * s
        for line in sheet.wrap(atlas.subtitle, sheet.font(14), width) if atlas.subtitle else []:
            sheet.text(m, y - 0.1 * s, line, sheet.font(14), "#dce8f2", va="top")
            y -= 0.28 * s
        art_top, art_bottom = sheet.height - band - 0.35 * s, 1.4 * s
        if atlas.cover is not None:
            self._map(sheet, "cover", atlas.cover, m, art_bottom, width, art_top - art_bottom)
        lines = [atlas.author, atlas.edition]
        y = 0.9 * s
        for line in (line for line in lines if line):
            sheet.text(sheet.width / 2, y, line, sheet.font(11), GREY, ha="center", va="bottom")
            y -= 0.25 * s
        return sheet

    def contents(self, numbers: Sequence[int]) -> list[_Sheet]:
        entries = self.entries()
        sheets = []
        for n, chunk in enumerate(self.contents_chunks()):
            sheet = _Sheet(self.portrait)
            m, s = self.margin, sheet.scale
            top = self._frame(sheet, 2 + n, "", ACCENTS[0])
            sheet.text(m, top - 0.1 * s, self.atlas.labels["contents"], sheet.font(22, bold=True), va="top")
            y = top - 0.9 * s
            for i in chunk:
                _, section, page, accent = entries[i]
                number = str(numbers[i])
                if page.kind == "section":
                    y -= 0.12 * s
                    prop = sheet.font(12, bold=True)
                    label = f"{self.atlas.labels['part']} {_roman(section)} · {page.title}"
                    sheet.text(m, y, label, prop, accent, va="baseline")
                    sheet.text(sheet.width - m, y, number, prop, accent, ha="right", va="baseline")
                    y -= 0.15 * s
                else:
                    prop = sheet.font(10.5)
                    indent = m + 0.3 * s
                    title = sheet.fit(page.title, prop, sheet.width - 2 * m - 1.2 * s)
                    sheet.text(indent, y, title, prop, INK, va="baseline")
                    sheet.text(sheet.width - m, y, number, prop, INK, ha="right", va="baseline")
                    end = indent + sheet.measure(title, prop) + 0.08 * s
                    start = sheet.width - m - sheet.measure(number, prop) - 0.08 * s
                    if start > end:
                        sheet.line(end, start, y + 0.02 * s, GREY, 0.8, (0, (1, 3)))
                y -= 0.27 * s
            sheets.append(sheet)
        return sheets

    def section(self, index: int, section: int, accent: str, numbers: Sequence[int]) -> _Sheet:
        page = self.atlas.pages[index]
        sheet = _Sheet(self.portrait)
        m, s = self.margin, sheet.scale
        width = sheet.width - 2 * m
        band = 4.2 * s
        sheet.rect(0, sheet.height - band, sheet.width, band, accent)
        sheet.text(m, sheet.height - 1.0 * s, f"{self.atlas.labels['part']} {_roman(section)}",
                   sheet.font(16, bold=True), "white", va="top", alpha=0.8)
        y = sheet.height - 1.6 * s
        for line in sheet.wrap(page.title, sheet.font(32, bold=True), width):
            sheet.text(m, y, line, sheet.font(32, bold=True), "white", va="top")
            y -= 0.6 * s
        y = sheet.height - band - 0.5 * s
        y = self._paragraphs(sheet, page.text, m, y, width, 11.5)
        # The pages of this part
        following = []
        for later_index in range(index + 1, len(self.atlas.pages)):
            if self.atlas.pages[later_index].kind == "section":
                break
            following.append(later_index)
        if following:
            y -= 0.3 * s
            sheet.text(m, y, self.atlas.labels["in_part"].upper(), sheet.font(9, bold=True), accent, va="top")
            y -= 0.4 * s
            # One column, or two when they would run off the page
            fits = max(1, int((y - (m + 0.5 * s)) / (0.26 * s)) + 1)
            columns = 1 if len(following) <= fits else 2
            per = min(fits, -(-len(following) // columns))
            gap = 0.3 * s
            col_w = (width - gap * (columns - 1)) / columns
            size = 10.5 if columns == 1 else 9.5
            prop = sheet.font(size)
            for n, later_index in enumerate(following[:per * columns]):
                x = m + (n // per) * (col_w + gap)
                row_y = y - (n % per) * 0.26 * s
                number = str(numbers[later_index])
                title = sheet.fit(self.atlas.pages[later_index].title, prop,
                                  col_w - sheet.measure(number, prop) - 0.15 * s)
                sheet.text(x, row_y, title, prop, INK, va="baseline")
                sheet.text(x + col_w, row_y, number, prop, GREY, ha="right", va="baseline")
        sheet.text(sheet.width / 2, m * 0.55, str(numbers[index]), sheet.font(9), GREY, ha="center", va="bottom")
        return sheet

    def _paragraphs(self, sheet: _Sheet, text: str, x: float, y: float, width: float, size: float,
                    draw: bool = True) -> float:
        """Draw *text* from *y* down; where it ends."""
        prop = sheet.font(size)
        leading = size * 1.42 / 72 * sheet.scale
        for n, paragraph in enumerate(paragraphs(text)):
            if n:
                y -= leading * 0.5
            for line in sheet.wrap(paragraph, prop, width):
                if draw:
                    sheet.text(x, y, line, prop, INK, va="top")
                y -= leading
        return y

    def _questions(self, sheet: _Sheet, questions: Sequence[str], x: float, y: float, width: float, size: float,
                   accent: str, draw: bool = True) -> float:
        """Draw the questions in a tinted box from *y* down; where it ends."""
        if not questions:
            return y
        s = sheet.scale
        pad = 0.14 * s
        prop = sheet.font(size)
        leading = size * 1.38 / 72 * s
        head = sheet.font(size, bold=True)
        lines = []
        for i, question in enumerate(questions, 1):
            number = f"{i}."
            wrapped = sheet.wrap(" ".join(question.split()), prop, width - 2 * pad - 0.3 * s)
            lines += [(number if j == 0 else "", line) for j, line in enumerate(wrapped)]
        height = 2 * pad + leading * 1.5 + leading * len(lines)
        if draw:
            sheet.rect(x, y - height, width, height, TINT, rounded=True)
            sheet.rect(x, y - height, 0.05 * s, height, accent)
            sheet.text(x + pad, y - pad, self.atlas.labels["questions"], head, accent, va="top")
            row = y - pad - leading * 1.5
            for number, line in lines:
                sheet.text(x + pad, row, number, prop, accent, va="top")
                sheet.text(x + pad + 0.3 * s, row, line, prop, INK, va="top")
                row -= leading
        return y - height

    def content(self, index: int, current: tuple[str, str], number: int) -> _Sheet:
        """A map page or a page of text: header, title, map, caption and questions."""
        page = self.atlas.pages[index]
        key = str(index + 1)
        aspect = _image_aspect(self.images.get(key), page) if page.kind == "map" else 1.0
        landscape = page.orientation == "landscape" or (page.orientation == "auto" and aspect >= LANDSCAPE_ASPECT)
        sheet = _Sheet(self.landscape if landscape and page.kind == "map" else self.portrait)
        m, s = self.margin, sheet.scale
        section_title, accent = current
        top = self._frame(sheet, number, section_title, accent)
        width = sheet.width - 2 * m
        title_prop = sheet.font(18, bold=True)
        title_lines = sheet.wrap(page.title, title_prop, width)
        y = top - 0.08 * s
        for line in title_lines:
            sheet.text(m, y, line, title_prop, INK, va="top")
            y -= 0.36 * s
        body_top = y - 0.12 * s
        bottom = m + 0.35 * s
        # The text and questions take what they need, smaller if they would leave the map too little
        for size in (10.5, 10, 9.5, 9, 8.5, 8):
            text_h = body_top - self._paragraphs(sheet, page.text, m, body_top, width, size, draw=False)
            q_h = body_top - self._questions(sheet, page.questions, m, body_top, width, size - 0.5, accent, False)
            gap = 0.18 * s if page.text and page.questions else 0
            below = text_h + q_h + gap
            if page.kind != "map" or body_top - bottom - below >= 0.5 * (body_top - bottom):
                break
        else:
            logger.warning("The text of page %d (%s) leaves its map little room; shorten it.", number, page.title)
        if page.kind == "map":
            map_bottom = bottom + below + (0.2 * s if below else 0)
            self._map(sheet, key, page, m, map_bottom, width, body_top - map_bottom)
            y = map_bottom - 0.12 * s
        else:
            y = body_top
        y = self._paragraphs(sheet, page.text, m, y, width, size)
        if page.questions:
            self._questions(sheet, page.questions, m, y - gap, width, size - 0.5, accent)
        return sheet

    def _map(self, sheet: _Sheet, key: str, page: Page, x: float, y: float, w: float, h: float) -> None:
        """The map of *page* fitted into the box at (*x*, *y*), *w* by *h* inches, centred."""
        if h <= 0.2:
            return
        path = self.images.get(key)
        aspect = _image_aspect(path, page)
        fit_w, fit_h = (w, w / aspect) if w / aspect <= h else (h * aspect, h)
        left, bottom = x + (w - fit_w) / 2, y + (h - fit_h) / 2
        if path and os.path.isfile(path):
            pixels = _load_image(path, (round(fit_w * IMAGE_PPI), round(fit_h * IMAGE_PPI)))
            ax = sheet.axes(left, bottom, fit_w, fit_h)
            ax.imshow(pixels, interpolation="none", aspect="auto")
            return
        # Not drawn: a pale globe or rectangle (two for a map of two globes) and why
        ax = sheet.axes(left, bottom, fit_w, fit_h)
        ax.set_xlim(0, aspect)
        ax.set_ylim(0, 1)
        wide = page.map.get("both-hemispheres", page.map.get("both_hemispheres")) or page.map.get("beside")
        globes = 2 if wide or (page.map.get("rectangle") is None and aspect >= 1.8) else 1
        for i in range(globes):
            cx = (i + 0.5) * aspect / globes
            if page.map.get("rectangle") is not None:
                w = 0.94 * aspect / globes
                ax.add_patch(Rectangle((cx - w / 2, 0.03), w, 0.94, facecolor=TINT, edgecolor=RULE, linewidth=1))
            else:
                ax.add_patch(Circle((cx, 0.5), 0.46, facecolor=TINT, edgecolor=RULE, linewidth=1))
        failed = self.failures.get(key)
        label = self.atlas.labels["failed" if failed else "proof"]
        prop = sheet.font(10, bold=True)
        ax.text(aspect / 2, 0.56, label, ha="center", va="center", fontproperties=prop, color=GREY)
        detail = failed or ", ".join(f"{k} = {v}" if v is not True else k for k, v in page.map.items())
        if page.recipe:
            detail = f"recipe = {page.recipe}" + (f", {detail}" if detail else "")
        wrapped = textwrap.wrap(detail, 60)[:6]
        ax.text(aspect / 2, 0.47, "\n".join(wrapped), ha="center", va="top", fontproperties=sheet.font(8),
                color=GREY, linespacing=1.4)


def write_pdf(atlas: Atlas, output: str, images: dict[str, str] | None = None,
              failures: dict[str, str] | None = None) -> int:
    """Lay out *atlas* into the PDF *output*, with *images* (map files by key); the number of pages."""
    return _Atlas(atlas, images or {}, failures or {}).write(output)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


class BuildResult(NamedTuple):
    output: str
    pages: int
    drawn: int                  # maps drawn this time
    reused: int                 # maps already drawn, used as they were
    failures: dict[str, str]    # error messages by map key


def default_paths(atlas_path: str) -> tuple[str, str]:
    """The PDF and the map folder for the atlas at *atlas_path*: ``atlas_<name>.pdf`` and ``atlas_<name>_maps``."""
    stem = Path(atlas_path).stem
    return f"atlas_{stem}.pdf", f"atlas_{stem}_maps"


def build_atlas(
    atlas: Atlas, output: str, maps_dir: str, *, proof: bool = False, force: bool = False,
    cache_dir: str | None = None, progress: Callable[[int, int, str], None] | None = None,
) -> BuildResult:
    """Check every map, draw those not drawn yet (none for a *proof*), and write the PDF."""
    plans = plan_maps(atlas, maps_dir, cache_dir)
    reused = sum(item.cached for item in plans) if not force else 0
    failures: dict[str, str] = {}
    if not proof:
        failures = render_maps(plans, force=force, progress=progress)
        prune_maps(maps_dir, [item.path for item in plans])
    images = {item.key: item.path for item in plans if item.cached and item.key not in failures}
    pages = write_pdf(atlas, output, images, failures)
    drawn = 0 if proof else len(plans) - reused - len(failures)
    return BuildResult(output, pages, drawn, reused, failures)


# ---------------------------------------------------------------------------
# Atlas files for the web app
# ---------------------------------------------------------------------------


def atlas_path(atlases_dir: str, name: str) -> Path:
    if not ATLAS_NAME.match(name):
        raise AtlasError(f"{name!r} is not an atlas name: use lower-case letters, digits, '_' and '-'.")
    return Path(atlases_dir) / f"{name}.toml"


def _existing(atlases_dir: str, name: str) -> Path:
    path = atlas_path(atlases_dir, name)
    if not path.is_file():
        raise FileNotFoundError(f"There is no atlas {name!r}.")
    return path


def list_atlases(atlases_dir: str) -> list[dict[str, Any]]:
    """Every atlas's name, title and number of pages (or why it cannot be read), by name."""
    found = []
    for path in sorted(Path(atlases_dir).glob("*.toml")):
        if not ATLAS_NAME.match(path.stem):
            continue
        try:
            atlas = load_atlas(str(path))
        except AtlasError as e:
            found.append({"name": path.stem, "title": path.stem, "pages": 0, "error": str(e)})
        else:
            found.append({"name": path.stem, "title": atlas.title, "pages": len(atlas.pages)})
    return found


def _page_json(page: Page, base_dir: Path, routes_dir: str, fields: Collection[str], where: str) -> dict[str, Any]:
    out: dict[str, Any] = {"type": page.kind, "title": page.title, "text": "\n\n".join(paragraphs(page.text)),
                           "questions": page.questions, "orientation": page.orientation}
    if page.kind == "map":
        try:
            request, kept = recipe_store.request_from_options(page.map, base_dir, routes_dir, fields, where)
        except recipe_store.RecipeError as e:
            raise AtlasError(str(e)) from None
        out.update(recipe=page.recipe, map=request, kept=kept)
    return out


def read_atlas_json(atlases_dir: str, routes_dir: str, name: str, fields: Collection[str]) -> dict[str, Any]:
    """Atlas *name* for the web form: its settings, and each page's map as a form request.

    A page's ``map`` holds its options as :func:`recipe_store.request_from_options`
    gives them; ``kept`` holds those the form leaves alone, which a save writes
    back as they were.
    """
    path = _existing(atlases_dir, name)
    atlas = load_atlas(str(path))
    base = path.parent
    data: dict[str, Any] = {"title": atlas.title, "subtitle": atlas.subtitle, "author": atlas.author,
                            "edition": atlas.edition, "paper": atlas.paper, "dpi": atlas.dpi,
                            "text_scale": atlas.text_scale,
                            "language": atlas.language}
    if atlas.cover is not None:
        data["cover"] = {k: v for k, v in _page_json(atlas.cover, base, routes_dir, fields, f"{path.name} cover").items()
                         if k in ("recipe", "map", "kept")}
    data["pages"] = [_page_json(page, base, routes_dir, fields, f"{path.name} page {i}")
                     for i, page in enumerate(atlas.pages, 1)]
    return {"name": name, "atlas": data}


def map_request(atlases_dir: str, routes_dir: str, name: str, page_key: str, fields: Collection[str]) -> dict[str, Any]:
    """The whole map of a page (``"cover"`` or its number) as a form request: its recipe's options, its own on top."""
    path = _existing(atlases_dir, name)
    atlas = load_atlas(str(path))
    page = _find_page(atlas, page_key)
    request: dict[str, Any] = {}
    if page.recipe:
        recipe = (path.parent / page.recipe).resolve()
        try:
            with open(recipe, "rb") as fh:
                data = tomllib.load(fh)
            request, _ = recipe_store.request_from_options(data, recipe.parent, routes_dir, fields, recipe.name)
        except (OSError, tomllib.TOMLDecodeError, recipe_store.RecipeError) as e:
            raise AtlasError(f"cannot read the recipe {page.recipe}: {e}") from None
    own, _ = recipe_store.request_from_options(page.map, path.parent, routes_dir, fields, path.name)
    if own.keys() & {"city", "lat", "lon"}:
        for key in ("city", "lat", "lon"):
            request.pop(key, None)
    return {k: v for k, v in {**request, **own}.items() if k not in RUN_OPTIONS}


def page_title(atlases_dir: str, name: str, page_key: str) -> str:
    """The title of a page with a map (``"cover"`` gives the atlas's)."""
    atlas = load_atlas(str(_existing(atlases_dir, name)))
    page = _find_page(atlas, page_key)
    return atlas.title if page_key == "cover" else page.title


def _find_page(atlas: Atlas, page_key: str) -> Page:
    if page_key == "cover":
        if atlas.cover is None:
            raise AtlasError("This atlas has no cover map.")
        return atlas.cover
    try:
        page = atlas.pages[int(page_key) - 1]
        if int(page_key) < 1:
            raise IndexError
    except (ValueError, IndexError):
        raise AtlasError(f"This atlas has no page {page_key}.") from None
    if page.kind != "map":
        raise AtlasError(f"Page {page_key} has no map.")
    return page


def _toml_text(text: str) -> str:
    """*text* as a TOML string: multi-line, wrapped, for anything longer than a line."""
    text = "\n\n".join(paragraphs(text))
    if len(text) <= 70 and "\n" not in text:
        return json.dumps(text, ensure_ascii=False)
    lines = []
    for n, paragraph in enumerate(text.split("\n\n")):
        if n:
            lines.append("")
        lines += textwrap.wrap(paragraph, 88, break_on_hyphens=False, break_long_words=False)
    body = "\n".join(lines).replace("\\", "\\\\").replace('"', '\\"')
    return f'"""\n{body}"""'


def _map_lines(table: str, request: dict[str, Any], kept: Sequence[Sequence[Any]], base_dir: str, routes_dir: str,
               fields: Collection[str]) -> list[str]:
    try:
        entries = recipe_store.options_from_request(
            {k: v for k, v in request.items() if k not in RUN_OPTIONS}, base_dir, routes_dir, fields)
    except recipe_store.RecipeError as e:
        raise AtlasError(str(e)) from None
    for key, value in kept:
        if key == "route":
            routes = next((v for k, v in entries if k == "route"), [])
            entries = [(k, v) for k, v in entries if k != "route"] + [("route", routes + list(value))]
        elif not any(k == key for k, _ in entries):
            entries.append((key, value))
    lines = [f"[{table}]"]
    for key, value in entries:
        if isinstance(value, float) and value.is_integer():
            value = int(value)                          # 450, as typed, not the form's 450.0
        lines.append(f"{key} = {recipe_store._toml(value, len(key) + 3)}")
    return lines


DEFAULT_HEADER = "# An atlas for atlas.py: python atlas.py <this file>."


def leading_comment(text: str) -> str:
    """The comment lines at the top of a TOML file, before its first key, as they are."""
    lines = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def atlas_text(data: dict[str, Any], base_dir: str, routes_dir: str, fields: Collection[str],
               header: str = DEFAULT_HEADER) -> str:
    """The TOML file for an atlas as :func:`read_atlas_json` gives it (and the web form sends it back).

    *header* is the comment at its top; other comments are not kept.
    """
    if not isinstance(data, dict) or not isinstance(data.get("pages", []), list):
        raise AtlasError("An atlas is a table with a list of pages.")
    lines = [header or DEFAULT_HEADER, ""]
    for key in ("title", "subtitle", "author", "edition", "paper", "language"):
        value = data.get(key)
        if value not in (None, ""):
            lines.append(f"{key} = {json.dumps(str(value), ensure_ascii=False)}")
    if data.get("dpi") is not None:
        lines.append(f"dpi = {int(data['dpi'])}")
    if data.get("text_scale") not in (None, "", 1, 1.0):
        lines.append(f"text_scale = {float(data['text_scale']):g}")
    cover = data.get("cover")
    if cover:
        lines.append("")
        if cover.get("recipe"):
            lines += ["[cover]", f"recipe = {json.dumps(cover['recipe'], ensure_ascii=False)}"]
        lines += _map_lines("cover.map", cover.get("map") or {}, cover.get("kept") or [], base_dir, routes_dir, fields)
    for page in data.get("pages", []):
        if not isinstance(page, dict):
            raise AtlasError("Each page is a table.")
        lines += ["", "[[pages]]"]
        kind = page.get("type", "map")
        if kind != "map":
            lines.append(f"type = {json.dumps(kind)}")
        lines.append(f"title = {json.dumps(str(page.get('title', '')), ensure_ascii=False)}")
        if page.get("text"):
            lines.append(f"text = {_toml_text(str(page['text']))}")
        questions = [str(q) for q in page.get("questions") or [] if str(q).strip()]
        if questions:
            lines.append("questions = [")
            lines += [f"    {json.dumps(' '.join(q.split()), ensure_ascii=False)}," for q in questions]
            lines.append("]")
        if page.get("orientation", "auto") != "auto":
            lines.append(f"orientation = {json.dumps(page['orientation'])}")
        if kind == "map":
            if page.get("recipe"):
                lines.append(f"recipe = {json.dumps(page['recipe'], ensure_ascii=False)}")
            if page.get("map") or page.get("kept") or not page.get("recipe"):
                lines += _map_lines("pages.map", page.get("map") or {}, page.get("kept") or [], base_dir,
                                    routes_dir, fields)
    return "\n".join(lines) + "\n"


def write_atlas(
    atlases_dir: str, routes_dir: str, name: str, data: dict[str, Any], fields: Collection[str],
    overwrite: bool = False, cache_dir: str | None = None,
) -> Path:
    """Save the atlas *data* (as the web form has it) as *name*, once it reads back and every map checks out."""
    path = atlas_path(atlases_dir, name)
    if path.exists() and not overwrite:
        raise FileExistsError(f"There is already an atlas {name!r}.")
    header = leading_comment(path.read_text(encoding="utf-8")) if path.is_file() else DEFAULT_HEADER
    text = atlas_text(data, atlases_dir, routes_dir, fields, header)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # a bug here rather than in the request, but say so plainly
        raise AtlasError(f"The atlas could not be written as TOML: {e}") from None
    atlas = parse_atlas(parsed, str(path))
    plan_maps(atlas, os.path.join(atlases_dir, ".check"), cache_dir)
    os.makedirs(atlases_dir, exist_ok=True)
    recipe_store._write_atomically(path, text)
    return path


def set_page_map(
    atlases_dir: str, routes_dir: str, name: str, page_key: str, request: dict[str, Any], fields: Collection[str],
    cache_dir: str | None = None,
) -> Path:
    """Give a page (``"cover"`` or its number) of atlas *name* the map in *request*, which replaces its recipe."""
    current = read_atlas_json(atlases_dir, routes_dir, name, fields)["atlas"]
    atlas = load_atlas(str(atlas_path(atlases_dir, name)))
    page = _find_page(atlas, page_key)
    kept: list[Any] = []
    if page.recipe:                              # what the form left alone in the recipe stays, beside the atlas
        recipe = (Path(atlases_dir) / page.recipe).resolve()
        try:
            with open(recipe, "rb") as fh:
                data = tomllib.load(fh)
            _, recipe_kept = recipe_store.request_from_options(data, recipe.parent, routes_dir, fields, recipe.name)
        except (OSError, tomllib.TOMLDecodeError, recipe_store.RecipeError) as e:
            raise AtlasError(f"cannot read the recipe {page.recipe}: {e}") from None
        for key, value in recipe_kept:
            if key.replace("-", "_") in RUN_OPTIONS:
                continue
            if key == "route":
                value = [os.path.relpath(recipe.parent / entry, atlases_dir).replace(os.sep, "/") for entry in value]
            kept.append((key, value))
    target = current["cover"] if page_key == "cover" else current["pages"][int(page_key) - 1]
    target["kept"] = kept + [entry for entry in target.get("kept", []) if entry[0] not in {k for k, _ in kept}]
    target["map"] = request
    target["recipe"] = None
    return write_atlas(atlases_dir, routes_dir, name, current, fields, overwrite=True, cache_dir=cache_dir)


def delete_atlas(atlases_dir: str, name: str) -> None:
    _existing(atlases_dir, name).unlink()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas.py",
        description="Draw the maps of an atlas file and bind them into a PDF, with a cover, contents, "
                    "section pages, captions and questions.")
    parser.add_argument("atlas", help="The atlas file (TOML), e.g. atlases/romania_school.toml.")
    parser.add_argument("-o", "--output", help="The PDF to write (default: atlas_<name>.pdf).")
    parser.add_argument("--maps-dir", help="Where the drawn maps are kept, to be used again by the next build "
                                           "(default: atlas_<name>_maps).")
    parser.add_argument("--dpi", type=int, help=f"Draw the maps at this resolution instead of the atlas's "
                                                f"({MIN_ATLAS_DPI}-{MAX_ATLAS_DPI}; default {DEFAULT_ATLAS_DPI}).")
    parser.add_argument("--proof", action="store_true",
                        help="Lay out the pages without drawing any map: a placeholder for each map not drawn yet.")
    parser.add_argument("--check", action="store_true",
                        help="Check the atlas and every map's options, list the pages, and stop.")
    parser.add_argument("--force", action="store_true", help="Draw every map again, even those already drawn.")
    parser.add_argument("--cache-dir", help="The tile and data cache (default: ortho.py's).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point (``ortho-atlas``)."""
    args = build_parser().parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")    # the titles' ș and ț, beyond a Windows code page
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ortho.load_env_files()
    try:
        atlas = load_atlas(args.atlas)
        if args.dpi is not None:
            if not MIN_ATLAS_DPI <= args.dpi <= MAX_ATLAS_DPI:
                raise AtlasError(f"--dpi must be from {MIN_ATLAS_DPI} to {MAX_ATLAS_DPI}")
            atlas.dpi = args.dpi
        default_pdf, default_maps = default_paths(args.atlas)
        output, maps_dir = args.output or default_pdf, args.maps_dir or default_maps
        if args.check:
            plans = {item.key: item for item in plan_maps(atlas, maps_dir, args.cache_dir)}
            for i, page in enumerate(atlas.pages, 1):
                item = plans.get(str(i))
                state = "" if item is None else (" (drawn)" if item.cached else " (to draw)")
                print(f"{i:3d}  {page.kind:<7}  {page.title}{state}")
            drawn = sum(item.cached for item in plans.values())
            print(f"{len(atlas.pages)} pages, {len(plans)} maps, {drawn} of them drawn already. The atlas checks out.")
            return 0

        def progress(done: int, total: int, title: str) -> None:
            if title:
                logger.info("Map %d of %d: %s", done + 1, total, title)

        result = build_atlas(atlas, output, maps_dir, proof=args.proof, force=args.force,
                             cache_dir=args.cache_dir, progress=progress)
    except AtlasError as e:
        logger.error("%s", e)
        return 1
    logger.info("Wrote %s: %d pages; %d maps drawn, %d reused%s.", result.output, result.pages, result.drawn,
                result.reused, f", {len(result.failures)} failed" if result.failures else "")
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
