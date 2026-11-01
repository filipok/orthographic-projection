"""Tests for pyproject.toml: an installed copy has every module and the files the web app reads."""

import fnmatch
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETUPTOOLS = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]


def test_every_module_is_installed():
    modules = {path.stem for path in REPO.glob("*.py")}
    assert modules - set(SETUPTOOLS["py-modules"]) == set()


def test_the_web_apps_files_are_installed_side_by_side():
    data = SETUPTOOLS["data-files"]
    assert sorted(data) == [f"share/orthographic-projection/{name}" for name in ("atlases", "recipes", "routes", "web")]
    for target, patterns in data.items():
        folder = REPO / target.rsplit("/", 1)[1]
        files = [path.relative_to(REPO).as_posix() for path in folder.iterdir() if path.is_file()]
        assert files and all(any(fnmatch.fnmatch(name, pattern) for pattern in patterns) for name in files), target
