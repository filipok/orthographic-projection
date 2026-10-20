"""Tests for indicators.py (no network; World Bank answers and Natural Earth countries written here)."""

import json
import os
import urllib.parse
from collections import Counter

import pytest
import shapefile
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import PathCollection
from matplotlib.figure import Figure

import indicators as ind
from rotation import globe_projection


def _globe(lat=0, lon=0):
    ax = Figure(figsize=(5, 5)).add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _square(x, y, size=10):
    return [[(x, y), (x, y + size), (x + size, y + size), (x + size, y), (x, y)]]


# (ADM0_A3, ISO_A3_EH, NAME, west, south)
COUNTRIES = [
    ("FRA", "-99", "France", 0, 40),           # Natural Earth's -99: the code comes from ADM0_A3
    ("DEU", "DEU", "Germany", 10, 45),
    ("KOS", "-99", "Kosovo", 20, 40),          # the World Bank's XKX
    ("ESH", "ESH", "W. Sahara", -15, 20),       # no figures
    ("ATA", "ATA", "Antarctica", 0, -80),       # left out
]


def _natural_earth(tmp_path):
    base = str(tmp_path / "ne_50m_admin_0_countries")
    with shapefile.Writer(base, shapeType=shapefile.POLYGON) as w:
        for name in ("ADM0_A3", "ISO_A3_EH", "NAME"):
            w.field(name, "C", 40)
        for adm, iso, name, x, y in COUNTRIES:
            w.poly(_square(x, y))
            w.record(adm, iso, name)

    def natural_earth(resolution, category, name):
        assert (resolution, category, name) == ("50m", "cultural", "admin_0_countries")
        return base + ".shp"

    return natural_earth


def _rows(code, title, rows):
    """A World Bank answer: [paging, [observation, ...]]."""
    return [{"page": 1, "pages": 1, "total": len(rows)},
            [{"indicator": {"id": code, "value": title}, "countryiso3code": iso, "date": str(year), "value": value}
             for iso, year, value in rows]]


class _Asked(list):
    """The URLs asked for, with the answers to serve by indicator code."""
    answers: dict


@pytest.fixture
def world_bank(monkeypatch):
    """Serve World Bank answers; returns the URLs asked for."""
    asked = _Asked()
    asked.answers = {}

    def fake(url, timeout=60):
        asked.append(url)
        code = urllib.parse.urlparse(url).path.split("/")[-1]
        if code not in asked.answers:
            return [{"message": [{"id": "120", "key": "Invalid value",
                                  "value": "The provided parameter value is not valid"}]}]
        return asked.answers[code]

    monkeypatch.setattr(ind, "_get_json", fake)
    return asked


LIFE = [("FRA", 2023, 83.1), ("DEU", 2023, 81.2), ("XKX", 2022, 76.0), ("AFE", 2023, 60.0),
        ("DEU", 2021, 80.0), ("", 2023, 1.0)]


@pytest.mark.parametrize("name, code", [("life-expectancy", "SP.DYN.LE00.IN"), ("Life Expectancy", "SP.DYN.LE00.IN"),
                                        ("co2_per_person", "EN.GHG.CO2.PC.CE.AR5"), ("sp.dyn.le00.in", "SP.DYN.LE00.IN"),
                                        ("NY.ADJ.NNTY.PC.CD", "NY.ADJ.NNTY.PC.CD")])
def test_names(name, code):
    assert ind.resolve_indicator(name).code == code


def test_unknown_name():
    with pytest.raises(ValueError, match="Unknown indicator 'happiness'.*life-expectancy"):
        ind.resolve_indicator("happiness")


@pytest.mark.parametrize("year, value", [(None, None), ("", None), ("2015", 2015), (1960, 1960)])
def test_years(year, value):
    assert ind.resolve_indicator_year(year) == value


@pytest.mark.parametrize("year", [1959, 3000, "last"])
def test_bad_years(year):
    with pytest.raises(ValueError, match="Indicator years run from 1960"):
        ind.resolve_indicator_year(year)


class TestValues:
    def test_latest_of_each_country(self, world_bank, tmp_path):
        world_bank.answers["SP.DYN.LE00.IN"] = _rows("SP.DYN.LE00.IN", "Life expectancy at birth, total", LIFE)
        found = ind.load_values(ind.resolve_indicator("life-expectancy"), cache_dir=str(tmp_path))
        assert found.values == {"FRA": 83.1, "DEU": 81.2, "XKX": 76.0, "AFE": 60.0}
        assert found.years["DEU"] == 2023 and found.indicator.title == "Life expectancy at birth"
        assert "mrnev=1" in world_bank[0] and len(found.retrieved) == 10
        ind.load_values(ind.resolve_indicator("life-expectancy"), cache_dir=str(tmp_path))
        assert len(world_bank) == 1                                    # from the cache

    def test_a_year_and_the_years_before(self, world_bank, tmp_path):
        world_bank.answers["SP.DYN.LE00.IN"] = _rows("SP.DYN.LE00.IN", "Life expectancy", LIFE)
        found = ind.load_values(ind.resolve_indicator("life-expectancy"), 2023, str(tmp_path))
        assert "date=2014%3A2023" in world_bank[0]
        assert ind._when(found) == "2023 (1 countries from earlier years)"

    def test_raw_code_titled_and_scaled_from_the_answer(self, world_bank, tmp_path):
        rows = [(f"C{i:02d}", 2020, 10.0 ** (i / 4)) for i in range(40)]
        world_bank.answers["XX.MONEY"] = _rows("XX.MONEY", "Money per person (US$)", rows)
        found = ind.load_values(ind.resolve_indicator("xx.money"), cache_dir=str(tmp_path))
        assert found.indicator.title == "Money per person (US$)" and found.indicator.log is True

    def test_unknown_code(self, world_bank, tmp_path):
        with pytest.raises(ValueError, match="The World Bank has no indicator 'XX.NOPE'"):
            ind.load_values(ind.resolve_indicator("XX.NOPE"), cache_dir=str(tmp_path))

    def test_offline(self, tmp_path, monkeypatch):
        def offline(url, timeout=60):
            raise ind.IndicatorDataError("Could not reach the World Bank: no network")

        monkeypatch.setattr(ind, "_get_json", offline)
        with pytest.raises(ind.IndicatorDataError, match="no network"):
            ind.load_values(ind.resolve_indicator("life-expectancy"), cache_dir=str(tmp_path))

    def test_stale_latest_refreshed_or_kept(self, world_bank, tmp_path, monkeypatch, caplog):
        world_bank.answers["SP.DYN.LE00.IN"] = _rows("SP.DYN.LE00.IN", "Life expectancy", LIFE)
        indicator = ind.resolve_indicator("life-expectancy")
        ind.load_values(indicator, cache_dir=str(tmp_path))
        path = next(os.path.join(tmp_path, f) for f in os.listdir(tmp_path))
        os.utime(path, (0, 0))                                          # 1970: stale
        ind.load_values(indicator, cache_dir=str(tmp_path))
        assert len(world_bank) == 2
        os.utime(path, (0, 0))

        def offline(url, timeout=60):
            raise ind.IndicatorDataError("no network")

        monkeypatch.setattr(ind, "_get_json", offline)
        assert ind.load_values(indicator, cache_dir=str(tmp_path)).values["FRA"] == 83.1
        assert "Using the World Bank figures of" in caplog.text


def test_countries(tmp_path):
    countries = ind.load_countries(_natural_earth(tmp_path))
    assert [(c.code, c.name) for c in countries] == [("FRA", "France"), ("DEU", "Germany"), ("XKX", "Kosovo"),
                                                     ("ESH", "W. Sahara")]


class TestClasses:
    def test_even_classes_rounded(self):
        breaks = ind.class_breaks([float(v) for v in range(1, 101)], log=False)
        assert breaks == [15, 29, 43, 58, 72, 86]

    def test_log_classes(self):
        breaks = ind.class_breaks([10.0 ** (i / 10) for i in range(51)], log=True)
        assert breaks[0] == pytest.approx(5.2, abs=0.1) and breaks[-1] == pytest.approx(19000, rel=0.1)

    def test_few_values(self):
        assert ind.class_breaks([], log=False) == []
        assert ind.class_breaks([5.0, 5.0, 5.0], log=False) == []

    @pytest.mark.parametrize("value, text", [(12345.6, "12,346"), (45.0, "45"), (4.56, "4.6"), (0.456, "0.46"),
                                             (1200.0, "1,200")])
    def test_numbers(self, value, text):
        assert ind.format_number(value) == text

    def test_labels(self):
        assert ind.class_labels([10, 20]) == ["under 10", "10–20", "20 and over"]
        assert ind.class_of(9.9, [10, 20]) == 0 and ind.class_of(10, [10, 20]) == 1 and ind.class_of(25, [10, 20]) == 2


class TestDrawing:
    def test_drawn_keyed_and_credited(self, world_bank, tmp_path):
        world_bank.answers["SP.DYN.LE00.IN"] = _rows("SP.DYN.LE00.IN", "Life expectancy", LIFE)
        found = ind.load_values(ind.resolve_indicator("life-expectancy"), cache_dir=str(tmp_path))
        countries = ind.load_countries(_natural_earth(tmp_path))
        ax = _globe(45, 10)
        counts = ind.draw_indicator(ax, found, countries, [80.0])
        assert counts == Counter({1: 2, 0: 1, -1: 1})                   # FRA, DEU over 80; XKX under; ESH none
        assert len(ax.collections) == 3 or all(not isinstance(c, PathCollection) for c in ax.collections)
        legend = ind.add_indicator_legend(ax, found, [80.0], counts)
        assert [t.get_text() for t in legend.get_texts()] == ["under 80", "80 and over", "no figures"]
        assert legend.get_title().get_text() == ("Life expectancy at birth (years), latest figures (mostly 2023): "
                                                 "World Bank")
        assert ind.indicator_attribution(found).startswith(
            "Statistics: World Bank, World Development Indicators (SP.DYN.LE00.IN), CC BY 4.0, retrieved ")


def test_cache_file_is_json(world_bank, tmp_path):
    world_bank.answers["SP.POP.TOTL"] = _rows("SP.POP.TOTL", "Population, total", [("FRA", 2024, 68e6)])
    ind.load_values(ind.resolve_indicator("population"), 2024, str(tmp_path))
    (name,) = os.listdir(tmp_path)
    with open(tmp_path / name, encoding="utf-8") as f:
        assert json.load(f)["rows"] == [["FRA", 2024, 68e6]]


# ---------------------------------------------------------------------------
# Eurostat
# ---------------------------------------------------------------------------


def _jsonstat(label, regions, years, values, extra=(("unit", {"PC": "Percentage"}),)):
    """A Eurostat JSON-stat answer: dimensions freq, *extra*, geo, time; *values* by (region, year)."""
    dims = [("freq", {"A": "Annual"}), *extra, ("geo", {r: r for r in regions}),
            ("time", {str(y): str(y) for y in years})]
    flat = {}
    for (region, year), value in values.items():
        flat[str(regions.index(region) * len(years) + years.index(year))] = value
    return {"label": label, "id": [d for d, _ in dims], "size": [len(c) for _, c in dims], "value": flat,
            "dimension": {d: {"category": {"index": {code: i for i, code in enumerate(c)}, "label": c}}
                          for d, c in dims}}


@pytest.fixture
def eurostat(monkeypatch):
    """Serve Eurostat answers by dataset; returns the URLs asked for."""
    asked = _Asked()
    asked.answers = {}

    def fake(url, timeout=120):
        asked.append(url)
        dataset = urllib.parse.urlparse(url).path.split("/")[-1]
        if dataset not in asked.answers:
            return {"error": [{"status": 404, "id": 100, "label": f"{dataset.upper()} is not available"}]}
        return asked.answers[dataset]

    monkeypatch.setattr(ind, "_get_json", fake)
    return asked


@pytest.mark.parametrize("name, code, filters, level", [
    ("eurostat:unemployment", "tgs00010", (("isced11", "TOTAL"), ("sex", "T"), ("age", "Y15-74"), ("unit", "PC")), 2),
    ("Eurostat:Population Density", "demo_r_d3dens", (("unit", "PER_KM2"),), 3),
    ("eurostat:nama_10r_2gdp?unit=EUR_HAB", "nama_10r_2gdp", (("unit", "EUR_HAB"),), 2),
    ("eurostat:demo_r_pjanind3?indic_de=MEDAGEPOP&unit=YR&level=nuts3", "demo_r_pjanind3",
     (("indic_de", "MEDAGEPOP"), ("unit", "YR")), 3),
    ("eurostat:tgs00010?level=1", "tgs00010", (), 1),
])
def test_eurostat_names(name, code, filters, level):
    found = ind.resolve_indicator(name)
    assert (found.source, found.code, found.filters, found.level) == (ind.EUROSTAT, code, filters, level)


@pytest.mark.parametrize("name, message", [("eurostat:Not A Dataset!", "Unknown Eurostat statistic"),
                                           ("eurostat:tgs00010?level=nuts4", "nuts1, nuts2 or nuts3")])
def test_eurostat_bad_names(name, message):
    with pytest.raises(ValueError, match=message):
        ind.resolve_indicator(name)


class TestEurostat:
    def test_values_by_region_and_year(self, eurostat, tmp_path):
        eurostat.answers["tgs00010"] = _jsonstat("Unemployment rate by NUTS 2 region", ["DE11", "ES61", "FR10"],
                                                 [2023, 2024], {("DE11", 2023): 3.1, ("DE11", 2024): 3.4,
                                                                ("ES61", 2023): 18.0, ("FR10", 2024): 7.5})
        found = ind.load_values(ind.resolve_indicator("eurostat:unemployment"), 2024, str(tmp_path))
        assert found.values == {"DE11": 3.4, "ES61": 18.0, "FR10": 7.5} and found.years["ES61"] == 2023
        query = urllib.parse.parse_qs(urllib.parse.urlparse(eurostat[0]).query)
        assert query["geoLevel"] == ["nuts2"] and query["sinceTimePeriod"] == ["2015"]
        assert query["untilTimePeriod"] == ["2024"] and query["age"] == ["Y15-74"]
        assert ind._when(found) == "2024 (1 regions from earlier years)"

    def test_raw_dataset_titled_from_the_answer(self, eurostat, tmp_path):
        eurostat.answers["xyz_r_1"] = _jsonstat("Something by NUTS 3 region", ["AT111"], [2022],
                                                {("AT111", 2022): 5.0}, extra=(("unit", {"NR": "Number"}),))
        found = ind.load_values(ind.resolve_indicator("eurostat:xyz_r_1?level=nuts3"), cache_dir=str(tmp_path))
        assert (found.indicator.title, found.indicator.unit) == ("Something by NUTS 3 region", "Number")

    def test_open_dimension_named(self, eurostat, tmp_path):
        eurostat.answers["nama_10r_2gdp"] = _jsonstat(
            "GDP", ["DE11"], [2022], {("DE11", 2022): 1.0},
            extra=(("unit", {"MIO_EUR": "Million euro", "EUR_HAB": "Euro per inhabitant"}),))
        with pytest.raises(ValueError, match=r"several values of 'unit'.*EUR_HAB \(Euro per inhabitant\)"):
            ind.load_values(ind.resolve_indicator("eurostat:nama_10r_2gdp"), cache_dir=str(tmp_path))

    def test_unknown_dataset(self, eurostat, tmp_path):
        with pytest.raises(ValueError, match="Eurostat cannot answer for 'nope_r'"):
            ind.load_values(ind.resolve_indicator("eurostat:nope_r"), cache_dir=str(tmp_path))

    def test_cached_apart_by_filters(self, eurostat, tmp_path):
        eurostat.answers["nama_10r_2gdp"] = _jsonstat("GDP", ["DE11"], [2022], {("DE11", 2022): 1.0})
        ind.load_values(ind.resolve_indicator("eurostat:nama_10r_2gdp?unit=EUR_HAB"), cache_dir=str(tmp_path))
        ind.load_values(ind.resolve_indicator("eurostat:nama_10r_2gdp?unit=MIO_EUR"), cache_dir=str(tmp_path))
        assert len(eurostat) == 2 and len(os.listdir(tmp_path)) == 2


def _nuts(regions):
    """A GISCO NUTS GeoJSON with a 1-degree square per region code."""
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"NUTS_ID": code, "NAME_LATN": f"Region {code}"},
         "geometry": {"type": "Polygon", "coordinates": [[[i, 50], [i + 1, 50], [i + 1, 51], [i, 51], [i, 50]]]}}
        for i, code in enumerate(regions)]}).encode()


class TestRegions:
    def test_downloaded_once(self, tmp_path, monkeypatch):
        asked = []

        def fake(url, timeout=120):
            asked.append(url)
            return _nuts(["DE11", "DE12"])

        monkeypatch.setattr(ind, "_get", fake)
        regions = ind.load_regions(2, 2024, str(tmp_path))
        assert [(r.code, r.name) for r in regions] == [("DE11", "Region DE11"), ("DE12", "Region DE12")]
        ind.load_regions(2, 2024, str(tmp_path))
        assert asked == ["https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
                         "NUTS_RG_10M_2024_4326_LEVL_2.geojson"]

    def test_unreachable(self, tmp_path, monkeypatch):
        def offline(url, timeout=120):
            raise OSError("no network")

        monkeypatch.setattr(ind, "_get", offline)
        with pytest.raises(ind.IndicatorDataError, match="Could not get the NUTS regions from GISCO"):
            ind.load_regions(3, 2024, str(tmp_path))

    def test_version_with_most_regions(self, tmp_path, monkeypatch):
        files = {2024: ["DE11", "XX99"], 2021: ["DE11", "DE12", "DE13"], 2016: ["DE11"]}
        monkeypatch.setattr(ind, "_get", lambda url, timeout=120: _nuts(files[int(url.split("_")[-4])]))
        values = ind.Values(ind.resolve_indicator("eurostat:unemployment"), None,
                            {"DE11": 1.0, "DE12": 2.0, "DE13": 3.0}, {}, "2026-10-20")
        assert [r.code for r in ind.load_areas(values, cache_dir=str(tmp_path))] == ["DE11", "DE12", "DE13"]

    def test_keyed_and_credited(self):
        values = ind.Values(ind.resolve_indicator("eurostat:unemployment"), None, {"DE11": 3.0},
                            {"DE11": 2025}, "2026-10-20")
        legend = ind.add_indicator_legend(_globe(50, 10), values, [], Counter({0: 1}))
        assert legend.get_title().get_text() == ("Unemployment (% of the labour force), latest figures (mostly 2025): "
                                                 "Eurostat, NUTS 2 regions")
        assert ind.indicator_attribution(values) == ("Statistics: Eurostat (tgs00010), retrieved 2026-10-20; "
                                                     "regions: © EuroGeographics for the administrative boundaries")


def test_certificates_include_certifi():
    import certifi
    import ssl

    context = ind._tls()
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    assert certifi.where()
