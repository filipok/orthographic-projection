"""Tests for storms.py — years, reading the ERA5 tracks, caching, choosing storms and drawing (no network)."""

import io
import zipfile

import numpy as np
import pytest
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.collections import LineCollection, PathCollection
from matplotlib.figure import Figure

import storms as st
from rotation import globe_projection

HEADER = "year,month,day,hour,time,id,lat,lon,p_cent,p_edge\n"


def _csv(steps, start=(2020, 12, 1)):
    """A track's CSV: (lat, lon, pressure) every three hours from *start*, a blank row in the middle."""
    year, month, day = start
    lines = [HEADER]
    for i, (lat, lon, pressure) in enumerate(steps):
        lines.append(f"{year},{month},{day + i // 8},{i % 8 * 3},0,1,{lat},{lon},{pressure},1010\n")
        if i == 1:
            lines.append(f"{year},{month},{day},6,0,1,,,,\n")      # the centre lost for a step
    return "".join(lines)


def _archive(tracks):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for i, text in enumerate(tracks):
            archive.writestr(f"2020/12/System_{i}.csv", text)
        archive.writestr("2020/readme.txt", "not a track")
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


@pytest.mark.parametrize("pressure, index", [(1012, 0), (990, 0), (985, 1), (970, 2), (961, 3), (955, 4),
                                             (940, 5), (925, 6)])
def test_levels(pressure, index):
    assert st.level(pressure) == index


@pytest.mark.parametrize("text, years", [(None, (st.LAST_YEAR, st.LAST_YEAR)), ("", (st.LAST_YEAR, st.LAST_YEAR)),
                                         ("2020", (2020, 2020)), (" 2010 - 2020 ", (2010, 2020)), (1999, (1999, 1999))])
def test_years(text, years):
    assert st.parse_storm_years(text) == years


@pytest.mark.parametrize("text", ["1939", "2020-2010", "2010-2030", "1990-2000-2010", "winter"])
def test_bad_years(text):
    with pytest.raises(ValueError, match="storms must be YEAR or FROM-TO"):
        st.parse_storm_years(text)


def test_dates():
    assert st.format_date(20201226) == "26 Dec 2020" and st.format_date(19400101) == "1 Jan 1940"
    assert st.describe_years((2020, 2020)) == "2020" and st.describe_years((2010, 2020)) == "2010-2020"


def test_read_a_year_keeps_the_deep_storms_of_a_day():
    deep = [(50 + i, 170 + i, 1000 - 5 * i) for i in range(10)]           # 30 hours, down to 955 hPa
    shallow = [(50, 170, 1004)] * 10                                    # never below 1,000 hPa
    short = [(50, 170, 960)] * 5                                        # 15 hours
    table = st.read_year(_archive([_csv(deep), _csv(shallow), _csv(short), _csv(deep)]))
    assert table["storm"].tolist() == [0] * 10 + [1] * 10               # the blank rows left out
    assert table["pressure"][:3].tolist() == [1000, 995, 990]
    assert table["date"][0] == 20201201 and table["date"][9] == 20201202


def _table(*storms):
    """A step table of storms, each (pressures, YYYYMMDD of every step)."""
    rows = [(10.0 * n, 50.0, p, d, n) for n, (pressures, days) in enumerate(storms) for p, d in zip(pressures, days)]
    a = np.array(rows, float)
    return {"lon": a[:, 0].astype(np.float32), "lat": a[:, 1].astype(np.float32), "pressure": a[:, 2],
            "date": a[:, 3].astype(np.int32), "storm": a[:, 4].astype(np.int32)}


def test_storms_chosen_by_the_year_and_pressure_of_their_deepest_point():
    table = _table(([990, 960, 980], [20191231, 20200101, 20200101]),   # deepest in 2020
                   ([970, 950, 990], [20201231, 20201231, 20210101]),   # deepest in 2020, ended in 2021
                   ([990, 975, 990], [20200601] * 3),                   # not deep enough
                   ([980, 940, 990], [20191230] * 3))                   # deepest in 2019
    chosen = st.select_storms(table, (2020, 2020), 970)
    assert chosen.starts.tolist() == [0, 3, 6]
    assert chosen.deepest.tolist() == [1, 4] and chosen.pressure[chosen.deepest].tolist() == [960, 950]
    assert chosen.lon.tolist() == [0, 0, 0, 10, 10, 10]
    none = st.select_storms(table, (2022, 2022), 970)
    assert len(none.lon) == 0 and none.starts.tolist() == [0]


def test_load_reads_the_next_year_too(tmp_path, monkeypatch):
    built = []

    def ensure(year, cache_dir):
        built.append(year)
        path = tmp_path / f"{year}.npz"
        days = [20201230, 20201231, 20210101] if year == 2021 else [year * 10_000 + 601] * 3
        np.savez(path, **_table(([990, 950, 990], days)))
        return str(path)

    monkeypatch.setattr(st, "ensure_year", ensure)
    chosen = st.load_storms((2020, 2020), 970, str(tmp_path))
    assert built == [2020, 2021] and len(chosen.starts) == 3            # both storms deepest in 2020, numbered apart
    built.clear()
    st.load_storms((st.LAST_YEAR, st.LAST_YEAR), 970, str(tmp_path))
    assert built == [st.LAST_YEAR]                                      # no archive after the last year
    with pytest.raises(ValueError, match="storm tracks run from"):
        st.load_storms((1930, 1931), cache_dir=str(tmp_path))


def test_cached_once(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("2020/12/a.csv", _csv([(50, 170, 1000 - 5 * i) for i in range(10)]))
    calls = []

    def download(url, dest, label):
        calls.append(url)
        open(dest, "wb").write(archive.getvalue())

    monkeypatch.setattr(st, "_download", download)
    first = st.ensure_year(2020, str(tmp_path))
    assert st.ensure_year(2020, str(tmp_path)) == first and calls == [st.STORMS_URL.format(year=2020)]
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"storms_2020_v{st.DATA_VERSION}.npz"]


def test_download_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_download", lambda url, dest, label: open(dest, "wb").write(b"not a zip"))
    with pytest.raises(st.StormDataError, match="of 2020"):
        st.ensure_year(2020, str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def _axes():
    ax = Figure(figsize=(6, 6), dpi=100).add_subplot(projection=globe_projection(55, 175))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    return ax


def _storms():
    """A deep storm across the date line near the Aleutians, a shallow one, and one on the far side."""
    lon = np.array([170, 175, 179.5, -178, 160, 162, 164, -10, -12, -14], float)
    lat = np.array([45, 48, 51, 53, 40, 41, 42, -40, -41, -42], float)
    pressure = np.array([985, 960, 948, 970, 999, 975, 990, 930, 920, 950], float)
    date = np.full(10, 20201226, np.int32)
    return st.Storms(lon, lat, pressure, date, np.array([0, 4, 7, 10]), np.array([2, 5, 8]))


def test_drawing_names_and_key():
    ax = _axes()
    assert st.draw_storms(ax, _storms()) == 2                          # the third is on the far side
    lines = [c for c in ax.collections if isinstance(c, LineCollection)]
    assert sum(len(c.get_segments()) for c in lines) == 5              # no step between storms
    lengths = [float(np.hypot(*(seg[1] - seg[0]))) for c in lines for seg in c.get_segments()]
    assert max(lengths) < 8e5                                          # every step short, the date line's too
    widths = [c.get_linewidths()[0] for c in lines]
    assert widths == sorted(widths)                                    # the deeper on top, wider
    dots = [c for c in ax.collections if isinstance(c, PathCollection)]
    assert len(dots) == 1 and len(dots[0].get_offsets()) == 2
    assert [t.get_text() for t in ax.texts] == ["948 hPa, 26 Dec 2020", "975 hPa, 26 Dec 2020"]
    legend = st.add_storms_legend(ax, (2010, 2020), 960)
    assert len(legend.get_texts()) == len(st.LEVELS) + 1
    assert "of 2010-2020 that fell to 960 hPa" in legend.get_title().get_text()


def test_drawing_without_names_or_storms():
    ax = _axes()
    st.draw_storms(ax, _storms(), labels=False)
    assert not ax.texts
    empty = np.zeros(0)
    assert st.draw_storms(_axes(), st.Storms(empty, empty, empty, empty, np.zeros(1, int), np.zeros(0, int))) == 0


def test_at_most_a_few_names(monkeypatch):
    monkeypatch.setattr(st, "MAX_STORM_LABELS", 1)
    ax = _axes()
    st.draw_storms(ax, _storms())
    assert [t.get_text() for t in ax.texts] == ["948 hPa, 26 Dec 2020"]
