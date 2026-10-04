"""Shared pytest setup: off-screen rendering and a fully offline test run."""

import ipaddress
import socket

import cartopy
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
import pytest

# Render off-screen. The default Windows backend (TkAgg) intermittently fails
# to initialise Tcl/Tk when many figures are created in one session.
matplotlib.use("Agg")


def _is_local(host) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that tries to reach a non-local host.

    Network calls in tests must be mocked; this makes an accidental real
    download (tiles, Natural Earth, Köppen data, Google) fail loudly instead
    of silently passing on a machine that happens to be online.
    """
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            raise OSError(f"Test tried to reach the network (DNS lookup for {host!r})")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if not _is_local(host):
            raise OSError(f"Test tried to reach the network (connect to {address!r})")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def offline_land_ocean(monkeypatch):
    """Replace Natural Earth LAND/OCEAN with small in-memory shapes.

    ``cfeature.LAND`` and ``cfeature.OCEAN`` download shapefiles from Natural
    Earth on first use. Reading real shapefiles is Cartopy's job; the tests
    only need features that render, so these stand-ins keep them offline.
    """
    from shapely.geometry import box

    plate = ccrs.PlateCarree()
    monkeypatch.setattr(cfeature, "OCEAN", cfeature.ShapelyFeature([box(-180, -90, 180, 90)], plate))
    monkeypatch.setattr(cfeature, "LAND", cfeature.ShapelyFeature(
        [box(-10, 35, 40, 70), box(-120, 25, -70, 50), box(110, -40, 155, -10)], plate,
    ))


@pytest.fixture(autouse=True, scope="session")
def empty_cartopy_data_dir(tmp_path_factory):
    """Point Cartopy at empty data folders so tests behave like a fresh machine.

    Without this, tests pass only because Natural Earth shapefiles happen to
    be cached locally from earlier runs.
    """
    data_dir = tmp_path_factory.mktemp("cartopy_data")
    saved = {key: cartopy.config.get(key) for key in ("data_dir", "pre_existing_data_dir")}
    cartopy.config["data_dir"] = str(data_dir)
    cartopy.config["pre_existing_data_dir"] = str(data_dir)
    yield
    cartopy.config.update(saved)
