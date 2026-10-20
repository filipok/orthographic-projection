"""Statistics by country or region: shade each by a World Bank or Eurostat figure.

Data sources
------------
World Bank, World Development Indicators (WDI): about 1,400 yearly statistics
for 217 economies since 1960 — CC BY 4.0, read from the World Bank's API
(https://api.worldbank.org/v2; no key). A handful have short names (see
:data:`PRESETS`); any WDI code (``NY.GDP.PCAP.CD``) works too. Country shapes:
Natural Earth 1:50m admin-0 countries (public domain), read through Cartopy
like the other Natural Earth layers, matched by ISO 3166-1 alpha-3 code.

Eurostat: statistics of European regions (NUTS 1, 2 and 3) from Eurostat's API
(https://ec.europa.eu/eurostat/api/dissemination; no key; reuse with
acknowledgement). ``eurostat:NAME`` names a preset (see
:data:`EUROSTAT_PRESETS`), ``eurostat:DATASET?dim=CODE&level=nuts3`` any
dataset. Region shapes: Eurostat GISCO's NUTS boundaries at 1:10 million,
© EuroGeographics for the administrative boundaries; the NUTS version (2024,
2021 or 2016) that matches the most region codes is used.

Each country or region takes its value for the year asked or, failing that,
its latest in the nine years before; with no year, its latest. Answers are
cached under ``~/.cache/ortho_tiles/indicators/``; the latest values are asked
for again after 30 days.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from typing import NamedTuple

import numpy as np
import shapely
from shapely.geometry import shape

import cartopy.crs as ccrs
import matplotlib.patches as mpatches
from cartopy.io import shapereader
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib import colormaps
from matplotlib.legend import Legend

from biomes import _key

logger = logging.getLogger(__name__)

WB_API = "https://api.worldbank.org/v2"
EUROSTAT_API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
GISCO_NUTS = ("https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
              "NUTS_RG_10M_{version}_4326_LEVL_{level}.geojson")
NUTS_VERSIONS = (2024, 2021, 2016)
WORLD_BANK, EUROSTAT = "World Bank", "Eurostat"
DATA_VERSION = 1
LOOK_BACK = 9                    # years before the one asked for that may stand in for it
REFRESH_DAYS = 30                # latest values are asked for again after this many days
CLASSES = 7
FIRST_YEAR = 1960
NO_DATA = "#d9d9d9"
PALETTE = "YlGnBu"


class Preset(NamedTuple):
    code: str
    title: str
    unit: str
    log: bool                    # classes on a log scale (for skewed figures: money, people)


PRESETS: dict[str, Preset] = {
    "gdp-per-person": Preset("NY.GDP.PCAP.CD", "GDP per person", "US$", True),
    "gdp-per-person-ppp": Preset("NY.GDP.PCAP.PP.CD", "GDP per person at purchasing power parity",
                                 "international $", True),
    "gdp": Preset("NY.GDP.MKTP.CD", "GDP", "US$", True),
    "population": Preset("SP.POP.TOTL", "Population", "people", True),
    "population-density": Preset("EN.POP.DNST", "Population density", "people per km²", True),
    "population-growth": Preset("SP.POP.GROW", "Population growth", "% a year", False),
    "aged-65": Preset("SP.POP.65UP.TO.ZS", "People aged 65 and over", "% of people", False),
    "urban": Preset("SP.URB.TOTL.IN.ZS", "People living in towns and cities", "% of people", False),
    "life-expectancy": Preset("SP.DYN.LE00.IN", "Life expectancy at birth", "years", False),
    "fertility": Preset("SP.DYN.TFRT.IN", "Fertility rate", "births per woman", False),
    "infant-mortality": Preset("SP.DYN.IMRT.IN", "Infant mortality", "deaths per 1,000 live births", True),
    "health-spending": Preset("SH.XPD.CHEX.GD.ZS", "Health spending", "% of GDP", False),
    "literacy": Preset("SE.ADT.LITR.ZS", "Adult literacy", "% of people 15 and over", False),
    "internet": Preset("IT.NET.USER.ZS", "People using the internet", "% of people", False),
    "electricity": Preset("EG.ELC.ACCS.ZS", "Access to electricity", "% of people", False),
    "poverty": Preset("SI.POV.DDAY", "People living on under $3.00 a day", "% of people", False),
    "inequality": Preset("SI.POV.GINI", "Income inequality (Gini index)", "0-100", False),
    "unemployment": Preset("SL.UEM.TOTL.ZS", "Unemployment", "% of the labour force", False),
    "co2-per-person": Preset("EN.GHG.CO2.PC.CE.AR5", "CO₂ emitted per person", "tonnes a year", True),
    "renewable-energy": Preset("EG.FEC.RNEW.ZS", "Renewable energy", "% of final energy use", False),
    "forest": Preset("AG.LND.FRST.ZS", "Forest", "% of land", False),
    "military-spending": Preset("MS.MIL.XPND.GD.ZS", "Military spending", "% of GDP", False),
}


class EurostatPreset(NamedTuple):
    dataset: str
    filters: tuple[tuple[str, str], ...]     # the dataset's other dimensions, each fixed to one code
    level: int                               # NUTS level: 1, 2 or 3
    title: str
    unit: str
    log: bool


EUROSTAT_PRESETS: dict[str, EurostatPreset] = {
    "gdp-per-person": EurostatPreset("nama_10r_2gdp", (("unit", "PPS_EU27_2020_HAB"),), 2, "GDP per person",
                                     "purchasing power standard", False),
    "gdp-per-person-nuts3": EurostatPreset("nama_10r_3gdp", (("unit", "PPS_EU27_2020_HAB"),), 3, "GDP per person",
                                           "purchasing power standard", False),
    "population": EurostatPreset("demo_r_pjangrp3", (("sex", "T"), ("age", "TOTAL"), ("unit", "NR")), 3,
                                 "Population", "people", True),
    "population-density": EurostatPreset("demo_r_d3dens", (("unit", "PER_KM2"),), 3, "Population density",
                                         "people per km²", True),
    "median-age": EurostatPreset("demo_r_pjanind3", (("indic_de", "MEDAGEPOP"), ("unit", "YR")), 3, "Median age",
                                 "years", False),
    "life-expectancy": EurostatPreset("demo_r_mlifexp", (("sex", "T"), ("age", "Y_LT1"), ("unit", "YR")), 2,
                                      "Life expectancy at birth", "years", False),
    "unemployment": EurostatPreset("tgs00010", (("isced11", "TOTAL"), ("sex", "T"), ("age", "Y15-74"),
                                                ("unit", "PC")), 2, "Unemployment", "% of the labour force", False),
    "youth-unemployment": EurostatPreset("lfst_r_lfu3rt", (("isced11", "TOTAL"), ("sex", "T"), ("age", "Y15-24"),
                                                           ("unit", "PC")), 2, "Unemployment of people aged 15-24",
                                         "% of the labour force", False),
    "employment-rate": EurostatPreset("lfst_r_lfe2emprt", (("sex", "T"), ("age", "Y20-64"), ("unit", "PC")), 2,
                                      "Employment rate", "% of people aged 20-64", False),
    "tertiary-education": EurostatPreset("edat_lfse_04", (("sex", "T"), ("age", "Y25-64"), ("isced11", "ED5-8"),
                                                          ("unit", "PC")), 2, "University-level education",
                                         "% of people aged 25-64", False),
    "poverty-risk": EurostatPreset("ilc_peps11n", (("unit", "PC"),), 2, "At risk of poverty or social exclusion",
                                   "% of people", False),
}

# Natural Earth code -> World Bank code, where they differ
NE_TO_WB = {"KOS": "XKX"}


class IndicatorDataError(RuntimeError):
    """The statistics could not be fetched or read."""


class Indicator(NamedTuple):
    """What to map: a World Bank code or a Eurostat dataset (with its filters and NUTS level), with its title,
    unit and scale (``None`` until fetched, for a raw code)."""
    code: str
    title: str | None
    unit: str | None
    log: bool | None
    source: str = WORLD_BANK
    filters: tuple[tuple[str, str], ...] = ()
    level: int | None = None


class Values(NamedTuple):
    indicator: Indicator
    year: int | None               # the year asked for; None for each area's latest
    values: dict[str, float]       # area code (ISO3 or NUTS) -> value
    years: dict[str, int]          # area code -> the year of its value
    retrieved: str                 # YYYY-MM-DD


class Area(NamedTuple):
    code: str                      # ISO 3166-1 alpha-3 (as the World Bank has it) or NUTS code
    name: str
    geometry: object               # shapely (multi)polygon, degrees


def _words(name: str) -> str:
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def _eurostat_indicator(rest: str) -> Indicator:
    wanted = _words(rest)
    if wanted in EUROSTAT_PRESETS:
        p = EUROSTAT_PRESETS[wanted]
        return Indicator(p.dataset, p.title, p.unit, p.log, EUROSTAT, p.filters, p.level)
    dataset, _, query = rest.strip().partition("?")
    dataset = dataset.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", dataset):
        raise ValueError(f"Unknown Eurostat statistic {rest!r}; choose from "
                         f"{', '.join('eurostat:' + n for n in EUROSTAT_PRESETS)}, or give a dataset code such as "
                         "eurostat:nama_10r_2gdp?unit=EUR_HAB&level=nuts2.")
    filters = urllib.parse.parse_qsl(query)
    level = 2
    kept = []
    for key, value in filters:
        if key.lower() == "level":
            match = re.fullmatch(r"(?:nuts)?([123])", value.strip().lower())
            if not match:
                raise ValueError(f"The NUTS level must be nuts1, nuts2 or nuts3, not {value!r}.")
            level = int(match.group(1))
        else:
            kept.append((key.strip(), value.strip()))
    return Indicator(dataset, None, None, None, EUROSTAT, tuple(kept), level)


def resolve_indicator(name: str) -> Indicator:
    """The statistic *name* stands for: a World Bank preset (``life-expectancy``) or code (``SP.DYN.LE00.IN``),
    or a Eurostat preset (``eurostat:unemployment``) or dataset (``eurostat:tgs00010?sex=T``).

    Raises ``ValueError`` for anything else.
    """
    text = (name or "").strip()
    if text.lower().startswith("eurostat:"):
        return _eurostat_indicator(text.split(":", 1)[1])
    wanted = _words(text)
    if wanted in PRESETS:
        preset = PRESETS[wanted]
        return Indicator(preset.code, preset.title, preset.unit, preset.log)
    code = text.upper()
    for preset in PRESETS.values():
        if preset.code == code:
            return Indicator(preset.code, preset.title, preset.unit, preset.log)
    if re.fullmatch(r"[A-Z0-9]+(\.[A-Z0-9]+)+", code):
        return Indicator(code, None, None, None)
    raise ValueError(f"Unknown indicator {name!r}; choose from {', '.join(PRESETS)}, or give a World Bank code "
                     "such as NY.GDP.PCAP.CD (see https://data.worldbank.org/indicator); for European regions, "
                     f"{', '.join('eurostat:' + n for n in EUROSTAT_PRESETS)}.")


def resolve_indicator_year(year: int | str | None) -> int | None:
    """The year of the statistics; ``None`` (each area's latest) if not given. Raises ``ValueError``."""
    if year is None or str(year).strip() == "":
        return None
    this_year = time.localtime().tm_year
    try:
        value = int(str(year).strip())
    except ValueError:
        value = None
    if value is None or not FIRST_YEAR <= value <= this_year:
        raise ValueError(f"Indicator years run from {FIRST_YEAR} to {this_year}, not {year!r}.")
    return value


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "indicators")


def _tls() -> ssl.SSLContext:
    """The system's trusted roots plus certifi's (Mozilla's): Windows may lack a root Eurostat's servers chain to
    (GlobalSign Root R46) until it has been fetched once. Certificates are still checked."""
    context = ssl.create_default_context()
    try:
        import certifi                     # pyproj depends on it
        context.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return context


def _get(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout, context=_tls()) as resp:
        return resp.read()


def _get_json(url: str, timeout: float = 120):
    """The JSON at *url*; an HTTP error's JSON body comes back too (both APIs explain errors there)."""
    try:
        return json.loads(_get(url, timeout))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except (OSError, ValueError):
            raise IndicatorDataError(f"Could not read {urllib.parse.urlparse(url).netloc}: {exc}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise IndicatorDataError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {exc}") from exc


def _world_bank(indicator: Indicator, year: int | None) -> dict:
    """The World Bank's answer for *indicator*: ``{"title", "unit", "rows": [[iso3, year, value], ...]}``."""
    query = {"format": "json", "per_page": 20000}
    if year is None:
        query["mrnev"] = 1                       # each country's most recent value
    else:
        query["date"] = f"{year - LOOK_BACK}:{year}"
    url = f"{WB_API}/country/all/indicator/{urllib.parse.quote(indicator.code)}?{urllib.parse.urlencode(query)}"
    answer = _get_json(url)
    if not isinstance(answer, list) or len(answer) < 2:
        message = answer[0].get("message", [{}])[0].get("value", "") if isinstance(answer, list) and answer else ""
        if "not valid" in message.lower() or "doesn't exist" in message.lower() or not message:
            raise ValueError(f"The World Bank has no indicator {indicator.code!r}; see "
                             "https://data.worldbank.org/indicator for the codes.")
        raise IndicatorDataError(f"The World Bank answered: {message}")
    rows = answer[1] or []
    title = next((r["indicator"]["value"] for r in rows if r.get("indicator")), indicator.code)
    return {"title": title, "unit": "",
            "rows": [[r["countryiso3code"], int(r["date"]), float(r["value"])] for r in rows
                     if r.get("value") is not None and len(r.get("countryiso3code") or "") == 3]}


def _eurostat(indicator: Indicator, year: int | None) -> dict:
    """Eurostat's answer for *indicator* (JSON-stat): ``{"title", "unit", "rows": [[nuts, year, value], ...]}``."""
    since = (year or time.localtime().tm_year) - LOOK_BACK - (0 if year else 1)
    query = [("format", "JSON"), ("lang", "EN"), ("geoLevel", f"nuts{indicator.level or 2}"),
             ("sinceTimePeriod", str(since)), *indicator.filters]
    if year is not None:
        query.append(("untilTimePeriod", str(year)))
    url = f"{EUROSTAT_API}/{urllib.parse.quote(indicator.code)}?{urllib.parse.urlencode(query)}"
    answer = _get_json(url)
    if "error" in answer:
        errors = answer["error"] if isinstance(answer["error"], list) else [answer["error"]]
        message = "; ".join(str(e.get("label", e)) for e in errors)
        if any(e.get("status") in (400, 404) for e in errors if isinstance(e, dict)):
            raise ValueError(f"Eurostat cannot answer for {indicator.code!r}: {message}")
        raise IndicatorDataError(f"Eurostat answered: {message}")
    ids, sizes, dims = answer["id"], answer["size"], answer["dimension"]
    open_dims = [d for d, n in zip(ids, sizes) if n > 1 and d not in ("geo", "time")]
    if open_dims:
        dim = open_dims[0]
        labels = dims[dim]["category"].get("label", {})
        choices = ", ".join(f"{code} ({labels.get(code, code)})" for code in list(dims[dim]["category"]["index"])[:8])
        raise ValueError(f"Eurostat's {indicator.code} has several values of {dim!r}; choose one, e.g. "
                         f"eurostat:{indicator.code}?{dim}=CODE: {choices}.")
    if "geo" not in ids or "time" not in ids:
        raise IndicatorDataError(f"Eurostat's {indicator.code} has no regions or years.")
    strides = [int(np.prod(sizes[i + 1:])) for i in range(len(sizes))]
    position = {d: {code: k for code, k in dims[d]["category"]["index"].items()} for d in ("geo", "time")}
    values = answer.get("value", {})
    rows = []
    for geo, g in position["geo"].items():
        for when, t in position["time"].items():
            flat = g * strides[ids.index("geo")] + t * strides[ids.index("time")]
            value = values.get(str(flat)) if isinstance(values, dict) else values[flat]
            if value is not None and re.fullmatch(r"\d{4}", when):
                rows.append([geo, int(when), float(value)])
    unit = ""
    if "unit" in ids and sizes[ids.index("unit")] == 1:
        unit = next(iter(dims["unit"]["category"].get("label", {}).values()), "")
    return {"title": answer.get("label", indicator.code), "unit": unit, "rows": rows}


def _cache_name(indicator: Indicator, year: int | None) -> str:
    if indicator.source == EUROSTAT:
        detail = hashlib.sha1(repr((indicator.filters, indicator.level)).encode()).hexdigest()[:8]
        return f"es_{indicator.code}_{detail}_{year or 'latest'}_v{DATA_VERSION}.json"
    return f"wb_{indicator.code}_{year or 'latest'}_v{DATA_VERSION}.json"


def load_values(indicator: Indicator, year: int | None = None, cache_dir: str | None = None) -> Values:
    """Each country's (or region's) value of *indicator* in *year*, or its latest in the nine years before; with
    no year, its latest. Raises ``ValueError`` for a statistic the source does not have,
    :class:`IndicatorDataError` when it cannot be reached (a stale cached answer is used instead, if there is
    one)."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, _cache_name(indicator, year))
    cached = None
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        pass
    stale = cached is not None and year is None and time.time() - os.path.getmtime(path) > REFRESH_DAYS * 86400
    if cached is None or stale:
        try:
            fetch = _eurostat if indicator.source == EUROSTAT else _world_bank
            answer = fetch(indicator, year)
            answer["retrieved"] = time.strftime("%Y-%m-%d")
            os.makedirs(cache_dir, exist_ok=True)
            with open(path + ".part", "w", encoding="utf-8") as f:
                json.dump(answer, f)
            os.replace(path + ".part", path)
            cached = answer
        except IndicatorDataError as exc:
            if cached is None:
                raise
            logger.warning("Using the %s figures of %s: %s", indicator.source, cached.get("retrieved"), exc)
    values: dict[str, float] = {}
    years: dict[str, int] = {}
    for code, when, value in sorted(cached["rows"], key=lambda r: r[1]):      # the latest year wins
        values[code], years[code] = value, when
    title = indicator.title or cached["title"]
    unit = indicator.unit or cached.get("unit", "")
    log = indicator.log if indicator.log is not None else _skewed(list(values.values()))
    return Values(indicator._replace(title=title, unit=unit, log=log), year, values, years, cached["retrieved"])


def _skewed(values: list[float]) -> bool:
    """Whether figures are spread over orders of magnitude, so classes are better on a log scale."""
    positive = sorted(v for v in values if v > 0)
    return len(positive) == len(values) and len(positive) > 10 and positive[-len(positive) // 10] > 50 * \
        positive[len(positive) // 10]


# ---------------------------------------------------------------------------
# Countries and regions
# ---------------------------------------------------------------------------


def load_countries(natural_earth: Callable[..., str] = shapereader.natural_earth) -> list[Area]:
    """Natural Earth's countries (1:50m), with the World Bank's codes; Antarctica is left out."""
    countries = []
    for record in shapereader.Reader(natural_earth("50m", "cultural", "admin_0_countries")).records():
        attrs = record.attributes
        code = str(attrs.get("ISO_A3_EH") or "-99")
        if code == "-99":
            code = str(attrs.get("ADM0_A3") or "")
        code = NE_TO_WB.get(code, code)
        if code in ("", "ATA") or record.geometry is None:
            continue
        countries.append(Area(code, str(attrs.get("NAME") or code), record.geometry))
    return countries


def load_regions(level: int, version: int, cache_dir: str | None = None) -> list[Area]:
    """GISCO's NUTS regions of *level* in NUTS *version*, downloaded once (1-3 MB). Raises
    :class:`IndicatorDataError`."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"nuts_{version}_level{level}.geojson")
    if not os.path.isfile(path):
        url = GISCO_NUTS.format(version=version, level=level)
        logger.info("Downloading the NUTS %d regions of %d from GISCO (one-time) …", level, version)
        try:
            data = _get(url)
            json.loads(data)
        except urllib.error.HTTPError as exc:
            raise IndicatorDataError(f"GISCO has no NUTS {level} regions of {version}: {exc}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise IndicatorDataError(f"Could not get the NUTS regions from GISCO: {exc}") from exc
        os.makedirs(cache_dir, exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    with open(path, encoding="utf-8") as f:
        features = json.load(f)["features"]
    return [Area(str(f["properties"]["NUTS_ID"]), str(f["properties"].get("NAME_LATN") or f["properties"]["NUTS_ID"]),
                 shapely.make_valid(shape(f["geometry"])))
            for f in features if f.get("geometry")]


def load_areas(values: Values, natural_earth: Callable[..., str] = shapereader.natural_earth,
               cache_dir: str | None = None) -> list[Area]:
    """The countries, or the NUTS regions, *values* are for. For Eurostat, the NUTS version holding the most of
    the regions with figures (nearly all, and it stops looking)."""
    if values.indicator.source != EUROSTAT:
        return load_countries(natural_earth)
    level = values.indicator.level or 2
    best: list[Area] = []
    best_matches = -1
    for version in NUTS_VERSIONS:
        try:
            regions = load_regions(level, version, cache_dir)
        except IndicatorDataError as exc:
            logger.info("Skipping NUTS %d: %s", version, exc)
            continue
        matches = sum(1 for r in regions if r.code in values.values)
        if matches > best_matches:
            best, best_matches = regions, matches
        if matches >= 0.95 * len(values.values):
            break
    if best_matches < 0:
        raise IndicatorDataError("Could not get any NUTS regions from GISCO.")
    return best


# ---------------------------------------------------------------------------
# Classes and drawing
# ---------------------------------------------------------------------------


def _nice(value: float) -> float:
    """*value* rounded to two significant figures (1,234 -> 1,200; 0.0456 -> 0.046)."""
    if value == 0:
        return 0.0
    digits = 1 - int(math.floor(math.log10(abs(value))))
    return round(value, digits)


def class_breaks(values: list[float], log: bool, classes: int = CLASSES) -> list[float]:
    """Up to *classes* - 1 rounded breaks splitting *values* into classes of about as many areas each."""
    data = np.array(sorted(values), float)
    if len(data) == 0:
        return []
    if log and data.min() > 0:
        cuts = np.exp(np.quantile(np.log(data), np.linspace(0, 1, classes + 1)[1:-1]))
    else:
        cuts = np.quantile(data, np.linspace(0, 1, classes + 1)[1:-1])
    breaks: list[float] = []
    for cut in cuts:
        nice = _nice(float(cut))
        if nice > data.min() and nice <= data.max() and (not breaks or nice > breaks[-1]):
            breaks.append(nice)
    return breaks


def class_of(value: float, breaks: list[float]) -> int:
    return int(np.searchsorted(breaks, value, side="right"))


def class_colours(n: int) -> list[tuple[float, float, float, float]]:
    cmap = colormaps[PALETTE]
    return [cmap(0.15 + 0.85 * i / max(n - 1, 1)) for i in range(n)]


def format_number(value: float) -> str:
    """``12,345`` / ``45`` / ``4.5`` / ``0.45``: as many decimals as the value's size needs."""
    size = abs(value)
    if size >= 100 or value == int(value):
        return f"{value:,.0f}"
    return f"{value:.1f}" if size >= 1 else f"{value:.2g}"


def class_labels(breaks: list[float]) -> list[str]:
    if not breaks:
        return ["all"]
    labels = [f"under {format_number(breaks[0])}"]
    labels += [f"{format_number(a)}–{format_number(b)}" for a, b in zip(breaks, breaks[1:])]
    labels.append(f"{format_number(breaks[-1])} and over")
    return labels


def draw_indicator(ax: GeoAxes, values: Values, areas: list[Area], breaks: list[float],
                   alpha: float = 0.75) -> Counter[int]:
    """Fill each country or region in its class's colour (grey where there is no figure). Returns how many are
    in each class (``-1`` for no figure)."""
    colours = class_colours(len(breaks) + 1)
    groups: dict[int, list] = {}
    for area in areas:
        value = values.values.get(area.code)
        groups.setdefault(-1 if value is None else class_of(value, breaks), []).append(area.geometry)
    for k, geometries in sorted(groups.items()):
        colour = NO_DATA if k < 0 else colours[k]
        ax.add_geometries(geometries, crs=ccrs.PlateCarree(), facecolor=colour, edgecolor="white", linewidth=0.3,
                          alpha=alpha, zorder=5, hatch="////" if k < 0 else None)
    logger.info("%s drawn for %d areas (%d without figures).", values.indicator.title,
                sum(len(g) for k, g in groups.items() if k >= 0), len(groups.get(-1, [])))
    return Counter({k: len(g) for k, g in groups.items()})


def _when(values: Values) -> str:
    kind = "regions" if values.indicator.source == EUROSTAT else "countries"
    if values.year is None:
        common = Counter(values.years.values()).most_common(1)
        return f"latest figures (mostly {common[0][0]})" if common else "latest figures"
    late = sum(1 for y in values.years.values() if y < values.year)
    return f"{values.year}" + (f" ({late} {kind} from earlier years)" if late else "")


def _source(values: Values) -> str:
    if values.indicator.source == EUROSTAT:
        return f"Eurostat, NUTS {values.indicator.level or 2} regions"
    return WORLD_BANK


def add_indicator_legend(ax: GeoAxes, values: Values, breaks: list[float], counts: Counter[int],
                         alpha: float = 0.75, y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the classes, with the statistic's title, unit, year and source."""
    colours = class_colours(len(breaks) + 1)
    handles = [mpatches.Patch(facecolor=c, edgecolor="#888888", alpha=alpha) for c in colours]
    labels = class_labels(breaks)
    if counts.get(-1):
        handles.append(mpatches.Patch(facecolor=NO_DATA, edgecolor="#888888", hatch="////", alpha=alpha))
        labels.append("no figures")
    unit = f" ({values.indicator.unit})" if values.indicator.unit else ""
    title = f"{values.indicator.title}{unit}, {_when(values)}: {_source(values)}"
    return _key(ax, handles, labels, title, min(len(handles), 4), x, y)


def indicator_attribution(values: Values) -> str:
    if values.indicator.source == EUROSTAT:
        return (f"Statistics: Eurostat ({values.indicator.code}), retrieved {values.retrieved}; "
                "regions: © EuroGeographics for the administrative boundaries")
    return (f"Statistics: World Bank, World Development Indicators ({values.indicator.code}), CC BY 4.0, "
            f"retrieved {values.retrieved}; countries: Natural Earth")
