"""The test suite must never reach the network (see tests/conftest.py)."""

import socket
import urllib.request

import cartopy
import cartopy.feature as cfeature
import pytest


def test_real_downloads_are_blocked():
    with pytest.raises(OSError, match="Test tried to reach the network"):
        urllib.request.urlopen("https://tile.openstreetmap.org/0/0/0.png", timeout=5)


def test_direct_ip_connections_are_blocked():
    sock = socket.socket()
    try:
        with pytest.raises(OSError, match="Test tried to reach the network"):
            sock.connect(("93.184.216.34", 80))
    finally:
        sock.close()


def test_localhost_still_resolves():
    assert socket.getaddrinfo("localhost", 80)


def test_natural_earth_features_are_offline_stand_ins():
    assert isinstance(cfeature.LAND, cfeature.ShapelyFeature)
    assert isinstance(cfeature.OCEAN, cfeature.ShapelyFeature)


def test_cartopy_data_dir_is_empty_temp_folder():
    import os

    assert os.listdir(cartopy.config["data_dir"]) == []
