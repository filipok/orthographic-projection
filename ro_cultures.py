"""Archaeological cultures of Romania: the sites of chosen cultures, from the national register.

Data sources
------------
RAN, Repertoriul Arheologic Național (National Archaeological Register),
Institutul Național al Patrimoniului, the table of 7 June 2017 on data.gov.ro
(https://data.gov.ro/dataset/6ec693fc-6676-4f31-b504-9bb61e7a4717), Open
Government Licence of Romania (OGL-ROU 1.0): 16,138 sites, 6,580 of them with
a culture. Later releases leave the cultures and nearly all coordinates out.

Where a site lies: fewer than half of those sites have coordinates, and very
unevenly (681 of 681 in Botoșani, 10 of 153 in Iași), so a map of these alone
would show where the register is complete, not where a culture was. A site
without coordinates, or whose coordinates lie more than 10 km from the commune
it is registered in, is placed at that commune (GISCO LAU 2024, see
:mod:`ro_admin`), found from its locality's SIRUTA code (INS), and drawn
hollow. On the located sites, that point is off by 3 km in the median.

Names are RAN's. Spellings that differ only in case, accents, spaces and
hyphens are one culture, written as RAN most often writes it, and so are the
spellings listed in :data:`JOINED`; cultures are never joined otherwise
(``Cucuteni`` and ``Cucuteni - Ariușd`` stay two). A
list (``Boian, Gumelnița``) is each of its cultures; ``Glina/Tei`` and ``Glina
sau Tei`` are names of their own, as they say a passage or a doubt, and so is
``Cucuteni (?)``. An empty county is not an empty past: it may be one nobody
has surveyed.

Everything is downloaded once (~22 MB) and kept in a compact form under
``~/.cache/ortho_tiles/ro_cultures/``.
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import os
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np
import shapely

from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.transforms import ScaledTranslation

from biomes import _download
from ports import _scale, in_view
from ro_admin import SIRUTA_URL, Unit, _fold, load_uats, read_uat_codes
from tectonics import project

logger = logging.getLogger(__name__)

RAN_URL = ("https://data.gov.ro/dataset/6ec693fc-6676-4f31-b504-9bb61e7a4717/resource/"
           "9de2aca3-e8fc-413c-a3d2-6b4b854bd1a9/download/inp-repertoriul-arheologic-national-2017-06-07.csv")
DATA_VERSION = 3          # bump when the names or places are read otherwise, so old caches are rebuilt
MAX_KM = 10.0             # coordinates farther than this from their commune are not used
ATTRIBUTION = "Archaeological sites: RAN, Institutul Național al Patrimoniului, 2017, OGL-ROU 1.0"

CULTURE_FIELDS = ("cultura (ansamblu)", "cultura (complex)", "cultura (obiect)")
SITE, SIRUTA, LON, LAT = ("codul RAN al sitului", "codul SIRUTA", "longitudine (zecimală)", "latitudine (zecimală)")
# What RAN writes for no culture, or by mistake, folded
UNKNOWN = {"neprecizata", "necunoscuta", "necunoscut", "neidentificata", "faraincadrareculturalacerta", "474", "b1b2",
           "corbasa", "seciiipchr", "secviiix", "secxiiixiv"}
# A culture's name, and RAN's other spellings of it. Names that may be a phase or a regional group
# (Cucuteni B, Cernavodă I, Sighișoara - Wietenberg) are not joined
JOINED: dict[str, tuple[str, ...]] = {
    "Yamnaia": ("Yamnaja", "Jamnaia", "Jamnaja", "Iamnaia", "Iamnaja"),
    "Cucuteni": ("Cututeni",),
    "Cucuteni - Ariușd": ("Ariușd-Cucuteni",),
    "Precucuteni": ("Precututeni",),
    "Noua": ("Noa",),
    "Wietenberg": ("Wiettenberg", "Wittenberg"),
    "Sântana de Mureș - Cerneahov": ("Sântana de Mureș-Cernehov", "Sântana de Mureș-Černjachov",
                                     "Sîntana de Mureș-Cerneahov"),
    "gravettian": ("Gravetian", "Gravetiană", "gravettiană"),
    "Musterian": ("Musteriană",),
    "tardenoisian": ("Tardenoasian",),
    "Costișa-Komariv": ("Costișa-Komarov", "Costișa - Komariw"),
    "Komarov": ("Komarovo", "Komarovow"),
    "Przeworsk": ("Pzeworsk",),
    "Suplacu de Barcău": ("Surplacu de Barcău",),
    "Șoimuș": ("Șoimiș",),
    "dacilor liberi": ("Daci liberi",),
    "geto-dacică": ("Geto-dacă",),
    "dacică": ("dacice",),
    "scitică": ("scitice",),
    "romano-bizantină": ("romană-bizantină",),
    "ceramicii liniare": ("ceramică lineară", "liniar ceramic"),
    "Ceramica lineară cu capete de note muzicale": ("Ceramicii liniare cu capete de note muzicale",),
    "Cehăluț": ("grupul Cehăluț", "grupul cultural Cehăluț"),
    "Baden": ("cultura Baden",),
    "Bucovăț": ("Grupul Bucovăț",),
    "scitică (grupul Ciumbrud)": ("grupul Ciumbrud",),
    "Cincu - Suseni": ("seria Cincu-Suseni",),
    "Ciurel": ("Ciurelu",),
    "vaselor cu toarte pastilate": ("cultura vaselor pastilate",),
}
_JOINED_KEYS = {_fold(other): _fold(name) for name, others in JOINED.items() for other in others}
_JOINED_NAMES = {_fold(name): name for name in JOINED}

COLOURS = ("#c1272d", "#1f4e9e", "#e08a00", "#0f9aa5", "#7b3fa0", "#2a8f3a", "#8c5a2b", "#d6408f")
# Sites at a commune: from how many, and the mark's width in points on a 15-inch globe (see ports._scale)
SIZES: tuple[tuple[int, float], ...] = ((1, 4.0), (2, 5.8), (5, 8.0))
SITE_POINTS = 4.0

TEXT = {
    "en": {"title": "Archaeological sites by culture (RAN, 2017): filled where the site is known, hollow at its commune",
           "sites": "{name} ({located:,} known, {placed:,} by commune)", "sizes": ("1 site", "2-4", "5 or more")},
    "ro": {"title": "Situri arheologice după cultură (RAN, 2017): pline unde situl e localizat, goale la comună",
           "sites": "{name} ({located} localizate, {placed} pe comună)", "sizes": ("1 sit", "2-4", "5 sau mai multe")},
}


class CulturesError(RuntimeError):
    """The register of sites, or the communes, could not be downloaded or read."""


class UnknownCultureError(ValueError):
    """RAN has no culture of that name."""


class Sites(NamedTuple):
    """The sites of one culture."""
    name: str
    lon: np.ndarray
    lat: np.ndarray
    located: np.ndarray       # True at the site itself, False at its commune
    place: np.ndarray         # the site's number, or minus its commune's SIRUTA code: the same for the same point


class Mark(NamedTuple):
    """One mark on the map: *sites* sites of culture number *culture*, *shift* points east of its point."""
    culture: int
    lon: float
    lat: float
    located: bool
    sites: int
    width: float
    shift: float


class Shown(NamedTuple):
    """How many sites of a culture show on a globe, at the site and at the commune."""
    located: int
    placed: int


def parse_cultures(names: str | Sequence[str]) -> list[str]:
    """The cultures named in *names*, a list or text with commas or ``+`` between them.

    Raises ``ValueError`` for none, or for more than there are colours.
    """
    text = names if isinstance(names, str) else ",".join(names)
    chosen = [name for name in (re.sub(r"\s+", " ", part).strip() for part in re.split(r"[,+]", text)) if name]
    unique = list({culture_key(name): name for name in chosen}.values())
    if not unique:
        raise ValueError("Name the cultures to draw, e.g. Cucuteni, Gumelnița.")
    if len(unique) > len(COLOURS):
        raise ValueError(f"At most {len(COLOURS)} cultures fit on one map, got {len(unique)}.")
    return unique


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def culture_key(name: str) -> str:
    """*name* as it is compared: without case, accents, spaces and hyphens, and in one spelling where several are
    :data:`JOINED`; a doubt (``?``) is kept."""
    folded = _fold(name)
    return _JOINED_KEYS.get(folded, folded) + ("?" if "?" in name else "")


def _written(key: str, spellings: Counter[str]) -> str:
    """The culture *key* as it is named: as :data:`JOINED` names it, else in its commonest spelling."""
    joined = _JOINED_NAMES.get(key.rstrip("?"))
    return joined + " (?)" * key.endswith("?") if joined else spellings.most_common(1)[0][0]


def split_cultures(text: str) -> list[str]:
    """The cultures a field of RAN's lists; none for one not known. See the module's notes on names."""
    found = []
    for part in re.split(r"[,;]|\s+și\s+", text or ""):
        name = re.sub(r"\s+", " ", part).strip(" .")
        if "?" in name:
            name = re.sub(r"\s*\(?\s*\?\s*\)?", "", name).strip() + " (?)"
        if _fold(name) and _fold(name) not in UNKNOWN:
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# Download and caching
# ---------------------------------------------------------------------------


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "ro_cultures")


def _number(text: str) -> float:
    """A decimal number written with a comma or a point; NaN for anything else."""
    try:
        return float((text or "").strip().replace(",", "."))
    except ValueError:
        return float("nan")


def km_outside(lon: float, lat: float, outline: shapely.Geometry) -> float:
    """How far (lon, lat) lies outside *outline*, in km: 0 inside it."""
    shrink = float(np.cos(np.radians(lat)))
    squeezed = shapely.transform(outline, lambda xy: xy * [shrink, 1])
    return float(shapely.distance(squeezed, shapely.Point(lon * shrink, lat))) * 111.2


def read_sites(text: str, uat_codes: dict[int, int], uats: dict[int, Unit]) -> dict[str, np.ndarray]:
    """The table of sites and their cultures from RAN's CSV *text*: a row for each culture of each site."""
    # The table uses no quoting: a quotation mark in it is part of the text
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter="|", quoting=csv.QUOTE_NONE)
    missing = {SITE, SIRUTA, LON, LAT, *CULTURE_FIELDS} - set(reader.fieldnames or ())
    if missing:
        raise CulturesError(f"RAN's table has no column {sorted(missing)[0]!r}")
    cultures: defaultdict[str, dict[str, str]] = defaultdict(dict)       # site -> culture key -> as written
    where: dict[str, tuple[str, float, float]] = {}
    spellings: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in reader:
        site = (row[SITE] or "").strip()
        for field in CULTURE_FIELDS:
            for name in split_cultures(row[field]):
                cultures[site][culture_key(name)] = name
                spellings[culture_key(name)][name] += 1
        where[site] = ((row[SIRUTA] or "").strip(), _number(row[LON]), _number(row[LAT]))
    if not cultures:
        raise CulturesError("RAN's table names no cultures")
    names = sorted(spellings)
    numbers = {key: i for i, key in enumerate(names)}
    rows, lost = [], 0
    for number, (site, held) in enumerate(cultures.items(), start=1):
        code, lon, lat = where[site]
        uat = uat_codes.get(int(code)) if code.isdigit() else None
        unit = uats.get(uat) if uat is not None else None
        if uat is None or unit is None:
            lost += 1
            continue
        located = bool(np.isfinite(lon) and np.isfinite(lat)) and km_outside(lon, lat, unit.outline) <= MAX_KM
        place = number if located else -uat
        x, y = (lon, lat) if located else unit.label
        rows += [(numbers[key], x, y, located, place) for key in held]
    if lost:
        logger.warning("%d archaeological sites are in a commune that is not on the map, and are left out.", lost)
    if not rows:
        raise CulturesError("none of RAN's sites is in a commune on the map")
    index, lons, lats, located_, place_ = zip(*rows)
    return {"names": np.array([_written(key, spellings[key]) for key in names]),
            "culture": np.array(index, np.int32), "lon": np.array(lons, float), "lat": np.array(lats, float),
            "located": np.array(located_, bool), "place": np.array(place_, np.int64)}


def ensure_sites(cache_dir: str | None = None) -> str:
    """Path to the cached table of sites, downloading and reading it if needed."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"ro_cultures_v{DATA_VERSION}.npz")
    if os.path.isfile(path):
        return path
    os.makedirs(cache_dir, exist_ok=True)
    logger.info("Downloading Romania's archaeological sites (RAN; one-time, ~22 MB) …")
    ran, siruta = os.path.join(cache_dir, "ran.download"), os.path.join(cache_dir, "siruta.download")
    try:
        uats = load_uats()
        _download(SIRUTA_URL, siruta, label="SIRUTA")
        _download(RAN_URL, ran, timeout=300, label="RAN")
        with open(ran, "rb") as fh, open(siruta, "rb") as codes:
            table = read_sites(fh.read().decode("utf-8-sig"), read_uat_codes(codes.read().decode("utf-8-sig")), uats)
    except CulturesError:
        raise
    except Exception as exc:                  # network, commune and table errors alike
        raise CulturesError(f"Could not read Romania's archaeological sites: {exc}") from exc
    finally:
        for download in (ran, siruta):
            if os.path.exists(download):
                os.remove(download)
    part = path + ".part.npz"
    np.savez_compressed(part, **table)  # pyright: ignore[reportArgumentType]
    os.replace(part, path)
    return path


def load_cultures(names: str | Sequence[str], cache_dir: str | None = None) -> list[Sites]:
    """The sites of the cultures *names* names, in that order.

    Raises :class:`UnknownCultureError`, naming the cultures spelled most like it, for one RAN does not have.
    """
    chosen = parse_cultures(names)
    with np.load(ensure_sites(cache_dir)) as data:
        table = {key: data[key] for key in data.files}
    known = {culture_key(str(name)): i for i, name in enumerate(table["names"])}
    found = []
    for name in chosen:
        key = culture_key(name)
        if key not in known:
            close = [k for k in known if key and key in k] + difflib.get_close_matches(key, known, n=6, cutoff=0.6)
            several = Counter(table["culture"].tolist())
            best = sorted(dict.fromkeys(close), key=lambda k: -several[known[k]])[:6]
            hint = "; did you mean " + ", ".join(str(table["names"][known[k]]) for k in best) + "?" if best else "."
            raise UnknownCultureError(f"RAN has no culture {name!r}{hint}")
        mine = table["culture"] == known[key]
        found.append(Sites(str(table["names"][known[key]]), table["lon"][mine], table["lat"][mine],
                           table["located"][mine], table["place"][mine]))
    return found


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def mark_width(count: int, scale: float = 1.0) -> float:
    """The width, in points, of the mark of *count* sites at a commune."""
    return [width for least, width in SIZES if count >= least][-1] * scale


def place_marks(cultures: Sequence[Sites], scale: float = 1.0) -> list[Mark]:
    """The marks of *cultures*: one for a site, one for a culture's sites at a commune, and side by side where
    several share a point."""
    points: defaultdict[int, list[tuple[int, float, float, bool, int]]] = defaultdict(list)
    for index, sites in enumerate(cultures):
        places, first, counts = np.unique(sites.place, return_index=True, return_counts=True)
        for place, i, count in zip(places.tolist(), first, counts.tolist()):
            points[place].append((index, float(sites.lon[i]), float(sites.lat[i]), place > 0, count))
    marks = []
    for shared in points.values():
        widths = [SITE_POINTS * scale if located else mark_width(count, scale) for _, _, _, located, count in shared]
        left = -sum(widths) / 2
        for (index, lon, lat, located, count), width in zip(shared, widths):
            marks.append(Mark(index, lon, lat, located, count, width, left + width / 2))
            left += width
    return marks


def draw_cultures(ax: GeoAxes, cultures: Sequence[Sites], alpha: float = 0.9) -> list[Shown]:
    """Draw the sites of *cultures*, each in its colour: filled at the site, hollow at the commune.

    Returns how many of each culture's sites show.
    """
    scale = _scale(ax)
    marks = place_marks(cultures, scale)
    seen = in_view(ax, np.array([m.lon for m in marks], float), np.array([m.lat for m in marks], float))
    marks = [m for m, shows in zip(marks, seen) if shows]
    x, y = project(ax, np.array([m.lon for m in marks], float), np.array([m.lat for m in marks], float))
    line = 1.1 * scale
    groups: defaultdict[tuple[bool, float], list[int]] = defaultdict(list)
    for i, mark in enumerate(marks):
        groups[mark.located, round(mark.shift, 2)].append(i)
    for (located, shift), members in sorted(groups.items()):          # hollow first, so known sites lie on top
        colours = [COLOURS[marks[i].culture] for i in members]
        # A hollow mark's line is drawn half outside its width
        sizes = [(marks[i].width - (0 if located else line)) ** 2 for i in members]
        offset = ax.transData + ScaledTranslation(shift / 72, 0, ax.figure.dpi_scale_trans)
        ax.scatter(x[members], y[members], s=sizes, alpha=alpha, transform=offset, zorder=7.5 if located else 7.4,
                   facecolors=colours if located else "none", edgecolors="white" if located else colours,
                   linewidths=0.3 * scale if located else line)
    shown = [Shown(sum(m.sites for m in marks if m.culture == i and m.located),
                   sum(m.sites for m in marks if m.culture == i and not m.located)) for i in range(len(cultures))]
    logger.info("Archaeological sites drawn: %s.", ", ".join(
        f"{sites.name} {counts.located + counts.placed:,}" for sites, counts in zip(cultures, shown)))
    return shown


def add_cultures_legend(ax: GeoAxes, cultures: Sequence[Sites], shown: Sequence[Shown], language: str = "en",
                        y: float = -0.01, x: float = 0.5, alpha: float = 0.9) -> Legend | None:
    """A key to the cultures, with how many of their sites show, and to the marks; ``None`` when none shows.

    *shown* is what the globe that shows most of them shows, as a site on two globes is one site.
    """
    if not any(counts.located + counts.placed for counts in shown):
        return None
    text = TEXT.get(language, TEXT["en"])
    scale = _scale(ax)
    handles, labels = [], []
    for colour, sites, counts in zip(COLOURS, cultures, shown):
        handles.append(Line2D([], [], linestyle="none", marker="o", markersize=8, markerfacecolor=colour,
                              markeredgecolor="white", alpha=alpha))
        labels.append(str(text["sites"]).format(name=sites.name, located=counts.located, placed=counts.placed))
    if any(counts.placed for counts in shown):
        for (least, _), label in zip(SIZES, text["sizes"]):
            handles.append(Line2D([], [], linestyle="none", marker="o", markerfacecolor="none",
                                  markeredgecolor="#555555", markersize=mark_width(least, scale)))
            labels.append(label)
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=min(len(handles), 4),
                    fontsize=10, title=str(text["title"]), title_fontsize=10, frameon=True, fancybox=True,
                    framealpha=0.85, edgecolor="#444444", handlelength=1.6, columnspacing=1.4, borderpad=0.8)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend

