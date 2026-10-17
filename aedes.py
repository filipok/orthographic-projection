"""Aedes mosquitoes: where *Aedes aegypti* and *Ae. albopictus* have been recorded, 1958-2014.

Data source
-----------
Kraemer, M. U. G., Sinka, M. E., Duda, K. A., et al. (2015). The global
compendium of *Aedes aegypti* and *Ae. albopictus* occurrence. *Scientific
Data* 2, 150035. https://doi.org/10.1038/sdata.2015.35 — CC0. The two
mosquitoes carry dengue, chikungunya, Zika and yellow fever.

The records are sightings collated from the literature, not a map of risk: a
region with few studies shows few dots. Each species' Darwin Core archive
(~0.3 MB) is read once from GBIF, where the compendium is published, and
cached under ``~/.cache/ortho_tiles/aedes/``.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import urllib.request
import zipfile
from typing import NamedTuple

import numpy as np

from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

from tectonics import project

logger = logging.getLogger(__name__)

DATA_VERSION = 1
AEDES_ATTRIBUTION = "Aedes records: Kraemer et al. (2015), global compendium of Aedes occurrence, via GBIF, CC0"

# Species -> (GBIF dataset key, name in the key, dot colour)
SPECIES: dict[str, tuple[str, str, str]] = {
    "aegypti": ("d4eb19bc-fdce-415f-9a61-49b036009840", "Aedes aegypti (yellow fever mosquito)", "#d7301f"),
    "albopictus": ("33614778-513a-4ec0-814d-125021cca5fe", "Aedes albopictus (Asian tiger mosquito)", "#2b6cb0"),
}
ARCHIVE_URL = "https://orphans.gbif.org/GB/{key}.zip"
CHOICES = ("both", *SPECIES)


class AedesDataError(RuntimeError):
    """The Aedes records could not be downloaded or read."""


class AedesRecords(NamedTuple):
    species: str
    lon: np.ndarray
    lat: np.ndarray
    year: np.ndarray      # NaN where the record has none


def resolve_aedes(name: str) -> tuple[str, ...]:
    """The species *name* asks for: ``"both"``, ``"aegypti"`` or ``"albopictus"``; raises ``ValueError``."""
    key = name.strip().lower().removeprefix("aedes ").removeprefix("ae. ")
    key = {"tiger": "albopictus", "yellow-fever": "aegypti", "all": "both"}.get(key, key)
    if key == "both":
        return tuple(SPECIES)
    if key not in SPECIES:
        raise ValueError(f"Unknown Aedes species {name!r}; use {', '.join(CHOICES)}.")
    return (key,)


def read_occurrences(text: str) -> dict[str, np.ndarray]:
    """The coordinates and years of a Darwin Core occurrence table (tab-separated)."""
    lon, lat, year = [], [], []
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        try:
            x, y = float(row["decimalLongitude"]), float(row["decimalLatitude"])
        except (KeyError, ValueError):
            continue
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            continue
        lon.append(x)
        lat.append(y)
        year.append(float(row["year"]) if (row.get("year") or "").isdigit() else np.nan)
    return {"lon": np.array(lon), "lat": np.array(lat), "year": np.array(year)}


def _get(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ortho/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _default_cache_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles", "aedes")


def load_aedes(species: str, cache_dir: str | None = None) -> AedesRecords:
    """The records of one of :data:`SPECIES`, reading its archive from GBIF once (~0.3 MB)."""
    cache_dir = cache_dir or _default_cache_dir()
    path = os.path.join(cache_dir, f"kraemer_{species}_v{DATA_VERSION}.npz")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("Downloading the Aedes %s records from GBIF (one-time, ~0.3 MB) …", species)
        try:
            archive = zipfile.ZipFile(io.BytesIO(_get(ARCHIVE_URL.format(key=SPECIES[species][0]))))
            table = next(n for n in archive.namelist() if n.endswith("occurrence.txt"))
            records = read_occurrences(archive.read(table).decode("utf-8"))
        except Exception as exc:              # network and archive errors alike
            raise AedesDataError(f"Could not read the Aedes {species} records: {exc}") from exc
        if not len(records["lon"]):
            raise AedesDataError(f"The Aedes {species} archive holds no records")
        part = path + ".part.npz"
        np.savez_compressed(part, lon=records["lon"], lat=records["lat"], year=records["year"])
        os.replace(part, path)
    with np.load(path) as data:
        return AedesRecords(species, data["lon"], data["lat"], data["year"])


def draw_aedes(ax: GeoAxes, records: list[AedesRecords], size: float = 7.0) -> None:
    """Draw each species' records as dots of its colour, the more widespread *albopictus* beneath."""
    for found in sorted(records, key=lambda r: r.species != "albopictus"):
        x, y = project(ax, found.lon, found.lat)
        seen = np.isfinite(x)
        ax.scatter(x[seen], y[seen], s=size, c=SPECIES[found.species][2], edgecolors="white", linewidths=0.25,
                   alpha=0.85, zorder=7.3)
    logger.info("Aedes records drawn: %s.", ", ".join(f"{r.species} {len(r.lon):,}" for r in records))


def add_aedes_legend(ax: GeoAxes, records: list[AedesRecords], y: float = -0.01, x: float = 0.5) -> Legend:
    """A key to the dots, with each species' number of records."""
    handles = [Line2D([], [], linestyle="none", marker="o", markersize=7, markerfacecolor=SPECIES[r.species][2],
                      markeredgecolor="white", markeredgewidth=0.5) for r in records]
    labels = [f"{SPECIES[r.species][1]}: {len(r.lon):,} records" for r in records]
    years = np.concatenate([r.year for r in records])
    years = years[np.isfinite(years)]
    span = f", {int(years.min())}-{int(years.max())}" if len(years) else ""
    title = f"Aedes mosquitoes recorded{span} (Kraemer et al. 2015): sightings, not risk"
    legend = Legend(ax, handles, labels, loc="upper center", bbox_to_anchor=(x, y), ncol=len(handles), fontsize=10,
                    title=title, title_fontsize=10, frameon=True, fancybox=True, framealpha=0.85,
                    edgecolor="#444444", handlelength=1.4, columnspacing=1.4, borderpad=0.7)
    legend.set_clip_on(False)
    ax.add_artist(legend)
    return legend
