"""Tests for koppen.py — data download, caching and colormap (network mocked)."""

import hashlib
import io
import os
import urllib.error
import zipfile
from unittest import mock

import pytest

import koppen

TIF_NAME = "Beck_KG_V1_present_0p083.tif"
TIFF_BYTES = b"II*\x00" + b"\x00" * 64


class FakeResponse(io.BytesIO):
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, body=b"", status=200, headers=None, fail_after=None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
        self._fail_after = fail_after

    def read(self, size: int | None = -1):
        if self._fail_after is not None and self.tell() >= self._fail_after:
            raise ConnectionError("connection reset")
        return super().read(size)


def _make_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point the manual-folder lookup at an empty temp dir and stub out sleeps."""
    monkeypatch.setattr(koppen, "__file__", str(tmp_path / "pkg" / "koppen.py"))
    monkeypatch.setattr(koppen.time, "sleep", lambda s: None)
    cache = tmp_path / "cache"
    return cache


@pytest.fixture
def archive(monkeypatch):
    """A small valid V1 archive whose MD5 the module will accept."""
    data = _make_zip({TIF_NAME: TIFF_BYTES, "legend.txt": "legend"})
    monkeypatch.setattr(koppen, "KOPPEN_ZIP_MD5", hashlib.md5(data).hexdigest())
    return data


@pytest.fixture
def urlopen():
    with mock.patch.object(koppen.urllib.request, "urlopen") as m:
        yield m


def _leftover_parts(directory):
    return [p for p in os.listdir(directory) if p.endswith(".part")] if os.path.isdir(directory) else []


# ===================================================================
# Lookup order and validation
# ===================================================================


class TestEnsureKoppenData:
    def test_rejects_unknown_resolution_and_period(self, isolated):
        with pytest.raises(ValueError, match="resolution"):
            koppen.ensure_koppen_data(str(isolated), resolution="1km")
        with pytest.raises(ValueError, match="period"):
            koppen.ensure_koppen_data(str(isolated), period="past")

    def test_prefers_manual_folder(self, isolated, tmp_path, urlopen):
        manual = tmp_path / "pkg" / "Beck_KG_V1"
        manual.mkdir(parents=True)
        (manual / TIF_NAME).write_bytes(TIFF_BYTES)
        assert koppen.ensure_koppen_data(str(isolated)) == str(manual / TIF_NAME)
        urlopen.assert_not_called()

    def test_ignores_confidence_rasters(self, isolated, tmp_path, urlopen, archive):
        manual = tmp_path / "pkg" / "Beck_KG_V1"
        manual.mkdir(parents=True)
        (manual / "Beck_KG_V1_present_conf_0p083.tif").write_bytes(TIFF_BYTES)
        urlopen.return_value = FakeResponse(archive)
        path = koppen.ensure_koppen_data(str(isolated))
        assert os.path.basename(path) == TIF_NAME

    def test_uses_valid_cache_without_network(self, isolated, urlopen):
        isolated.mkdir()
        (isolated / TIF_NAME).write_bytes(TIFF_BYTES)
        assert koppen.ensure_koppen_data(str(isolated)) == str(isolated / TIF_NAME)
        urlopen.assert_not_called()

    def test_discards_corrupt_cache_and_redownloads(self, isolated, urlopen, archive):
        isolated.mkdir()
        (isolated / TIF_NAME).write_bytes(b"<html>not a tiff</html>")
        urlopen.return_value = FakeResponse(archive)
        path = koppen.ensure_koppen_data(str(isolated))
        assert open(path, "rb").read() == TIFF_BYTES

    def test_downloads_verifies_and_extracts(self, isolated, urlopen, archive):
        urlopen.return_value = FakeResponse(archive)
        path = koppen.ensure_koppen_data(str(isolated))

        assert path == str(isolated / TIF_NAME)
        assert open(path, "rb").read() == TIFF_BYTES
        assert (isolated / koppen.KOPPEN_ZIP_NAME).read_bytes() == archive
        assert urlopen.call_args.args[0].full_url == koppen.KOPPEN_ZIP_URL
        assert _leftover_parts(isolated) == []

    def test_existing_archive_is_reused(self, isolated, urlopen, archive):
        isolated.mkdir()
        (isolated / koppen.KOPPEN_ZIP_NAME).write_bytes(archive)
        koppen.ensure_koppen_data(str(isolated))
        urlopen.assert_not_called()

    def test_md5_mismatch_is_rejected(self, isolated, urlopen, archive):
        urlopen.return_value = FakeResponse(archive + b"tampered")
        with pytest.raises(koppen.KoppenDataError, match="MD5"):
            koppen.ensure_koppen_data(str(isolated))
        assert not (isolated / koppen.KOPPEN_ZIP_NAME).exists()
        assert not (isolated / TIF_NAME).exists()

    def test_missing_member_is_reported(self, isolated, urlopen, monkeypatch):
        data = _make_zip({"legend.txt": "legend"})
        monkeypatch.setattr(koppen, "KOPPEN_ZIP_MD5", hashlib.md5(data).hexdigest())
        urlopen.return_value = FakeResponse(data)
        with pytest.raises(koppen.KoppenDataError, match="missing"):
            koppen.ensure_koppen_data(str(isolated))
        assert _leftover_parts(isolated) == []


# ===================================================================
# Download retries
# ===================================================================


class TestDownload:
    def test_bot_challenge_fails_fast(self, tmp_path, urlopen):
        urlopen.return_value = FakeResponse(status=202, headers={"x-amzn-waf-action": "challenge"})
        with pytest.raises(koppen.KoppenDataError, match="bot challenge"):
            koppen._download_with_progress("https://x", str(tmp_path / "f"))
        assert urlopen.call_count == 1

    def test_permanent_http_error_is_not_retried(self, tmp_path, urlopen):
        urlopen.side_effect = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        with pytest.raises(koppen.KoppenDataError, match="404"):
            koppen._download_with_progress("https://x", str(tmp_path / "f"))
        assert urlopen.call_count == 1

    def test_transient_errors_are_retried_then_give_up(self, tmp_path, urlopen, monkeypatch):
        sleeps = []
        monkeypatch.setattr(koppen.time, "sleep", sleeps.append)
        urlopen.side_effect = urllib.error.HTTPError("https://x", 503, "Unavailable", {}, None)  # type: ignore[arg-type]
        with pytest.raises(koppen.KoppenDataError, match="503"):
            koppen._download_with_progress("https://x", str(tmp_path / "f"), attempts=3)
        assert urlopen.call_count == 3
        assert sleeps == [2, 4]

    def test_recovers_after_network_error(self, tmp_path, urlopen, monkeypatch):
        monkeypatch.setattr(koppen.time, "sleep", lambda s: None)
        urlopen.side_effect = [urllib.error.URLError("timed out"), FakeResponse(b"data")]
        dest = tmp_path / "f"
        koppen._download_with_progress("https://x", str(dest))
        assert dest.read_bytes() == b"data"

    def test_interrupted_download_leaves_nothing_behind(self, tmp_path, urlopen, monkeypatch):
        monkeypatch.setattr(koppen.time, "sleep", lambda s: None)
        urlopen.side_effect = lambda *a, **k: FakeResponse(b"x" * (1 << 17), fail_after=1 << 16)
        dest = tmp_path / "f"
        with pytest.raises(koppen.KoppenDataError):
            koppen._download_with_progress("https://x", str(dest))
        assert os.listdir(tmp_path) == []

    def test_passes_timeout(self, tmp_path, urlopen):
        urlopen.return_value = FakeResponse(b"data")
        koppen._download_with_progress("https://x", str(tmp_path / "f"), timeout=12)
        assert urlopen.call_args.kwargs["timeout"] == 12


# ===================================================================
# Colormap
# ===================================================================


class TestColormap:
    def test_zero_is_transparent_and_codes_map_to_their_colour(self):
        cmap, norm = koppen.build_koppen_colormap()
        assert cmap(norm(0))[3] == 0.0
        for code, (_sym, _desc, rgb) in koppen.KOPPEN_CLASSES.items():
            r, g, b, a = cmap(norm(code))
            assert (round(r * 255), round(g * 255), round(b * 255)) == rgb
            assert a == 1.0


# ===================================================================
# Legend
# ===================================================================


class TestLegend:
    def test_lists_every_class_in_group_order_with_its_colour(self):
        import cartopy.crs as ccrs
        import matplotlib.colors as mcolors
        from matplotlib.figure import Figure

        fig = Figure()
        ax = fig.add_subplot(projection=ccrs.Orthographic(0, 0))
        koppen.add_koppen_legend(ax)

        legend = ax.get_legend()
        assert legend.get_title().get_text() == "Köppen-Geiger Climate Classification"
        labels = [t.get_text() for t in legend.get_texts()]
        expected_codes = [code for _l, _n, codes in koppen._GROUPS for code in codes]
        assert sorted(expected_codes) == list(koppen.KOPPEN_CLASSES)   # every class exactly once
        assert labels == [koppen.KOPPEN_CLASSES[c][0] for c in expected_codes]
        for patch, code in zip(legend.get_patches(), expected_codes):
            r, g, b, _a = mcolors.to_rgba(patch.get_facecolor())
            assert (round(r * 255), round(g * 255), round(b * 255)) == koppen.KOPPEN_CLASSES[code][2]
