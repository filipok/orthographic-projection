"""Tests for biomes.py — biome names, rasterising the shapefile, caching, colours and keys (no network)."""

import io
import zipfile
from collections import Counter

import numpy as np
import pytest
import shapefile
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.figure import Figure

import biomes
from rotation import globe_projection


def _square(west, south, east, north, clockwise=True):
    """A closed ring round a lon/lat box, clockwise on the map (round land) or not (round a hole)."""
    ring = [(west, south), (west, north), (east, north), (east, south), (west, south)]
    return ring if clockwise else ring[::-1]


# (ECO_ID, name, biome, colour, realm, rings)
SHAPES = [
    (0, "Rock and Ice", 11, "#FFEAAF", "N/A", [_square(10, 60, 12, 62)]),
    # A forest with a lake, and an island in the lake
    (1, "Lake forest", 4, "#112233", "Palearctic",
     [_square(0, 0, 4, 4), _square(1, 1, 3, 3, clockwise=False), _square(2, 2, 3, 3)]),
    (2, "Cerrado", 7, "#AABBCC", "Neotropic", [_square(-50, -20, -45, -15)]),
]


def _archive(shapes=SHAPES):
    """The shapes as an in-memory zip laid out like Ecoregions2017.zip."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf,  # pyright: ignore[reportArgumentType]
                          shapeType=shapefile.POLYGON) as writer:
        writer.field("ECO_NAME", "C", 150)
        writer.field("BIOME_NUM", "N", 32, 10)
        writer.field("ECO_ID", "N", 11)
        writer.field("COLOR", "C", 7)
        writer.field("REALM", "C", 254)
        for number, name, biome, colour, realm, rings in shapes:
            writer.poly(rings)
            writer.record(name, biome, number, colour, realm)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for ext, part in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            zf.writestr(f"{biomes.ECOREGIONS_NAME}.{ext}", part.getvalue())
    return buf.getvalue()


def _layer(ids):
    """An Ecoregions table for the three SHAPES over the grid *ids*."""
    return biomes.Ecoregions(
        np.asarray(ids, np.uint16),
        np.array(["Rock and Ice", "Lake forest", "Cerrado"]),
        np.array([biomes.ROCK_AND_ICE, 4, 7], np.uint8),
        np.array([[255, 234, 175], [17, 34, 51], [170, 187, 204]], np.uint8),
        np.array(["N/A", "Palearctic", "Neotropic"]),
    )


class TestBiomeNames:
    @pytest.mark.parametrize("specs, numbers", [
        (["4"], (4,)),
        (["temperate"], (4, 5, 8)),
        (["forest"], (1, 2, 3, 4, 5, 6, 12)),
        (["Tropical grass"], (7,)),
        (["taiga", "MANGROVES"], (6, 14)),
        (["rock"], (0,)),
        (["ice", "13"], (13, 0)),                    # Rock & Ice comes last, as in the key
        (["tropical and subtropical dry"], (2,)),
    ])
    def test_resolve(self, specs, numbers):
        assert biomes.resolve_biome_classes(specs) == numbers

    @pytest.mark.parametrize("spec", ["15", "jungle", "", "temperate desert"])
    def test_unknown(self, spec):
        with pytest.raises(ValueError, match="Unknown biome"):
            biomes.resolve_biome_classes([spec])

    def test_fifteen_with_rock_and_ice_last(self):
        assert list(biomes.BIOMES) == list(range(1, 15)) + [biomes.ROCK_AND_ICE]


class TestRasterise:
    def test_cells_holes_and_islands(self):
        with zipfile.ZipFile(io.BytesIO(_archive())) as archive:
            data = biomes.rasterise(archive, cells_per_degree=1)
        ids = data["ids"]
        assert ids.shape == (180, 360)

        def cells(value):
            rows, cols = np.nonzero(ids == value)
            return {(90 - r - 1, c - 180) for r, c in zip(rows, cols)}   # (south edge, west edge) of each cell

        assert cells(0) == {(60, 10), (60, 11), (61, 10), (61, 11)}
        forest = {(s, w) for s in range(4) for w in range(4)} - {(1, 1), (1, 2), (2, 1)}   # the lake less its island
        assert cells(1) == forest
        assert len(cells(2)) == 25
        assert (ids != biomes.NODATA).sum() == 4 + len(forest) + 25
        assert list(data["names"]) == ["Rock and Ice", "Lake forest", "Cerrado"]
        assert list(data["biomes"]) == [biomes.ROCK_AND_ICE, 4, 7]     # Rock & Ice has no biome of its own
        assert data["colours"][1].tolist() == [0x11, 0x22, 0x33]
        assert list(data["realms"]) == ["N/A", "Palearctic", "Neotropic"]

    def test_ring_order_does_not_matter(self):
        rings = [_square(2, 2, 3, 3), _square(1, 1, 3, 3, clockwise=False), _square(0, 0, 4, 4)]
        grid = np.full((180, 360), biomes.NODATA, np.uint16)
        biomes.burn(grid, np.array(sum(rings, []), float), [0, 5, 10], 1, 1)
        assert (grid == 1).sum() == 16 - 3

    def test_tall_polygons_are_filled_in_blocks(self):
        grid = np.full((180 * 4, 360 * 4), biomes.NODATA, np.uint16)
        biomes.burn(grid, np.array(_square(-10, -80, 10, 80), float), [0], 3, 4)
        rows, cols = np.nonzero(grid == 3)
        assert (rows.min(), rows.max(), cols.min(), cols.max()) == (40, 679, 680, 759)
        assert len(rows) == 640 * 80

    def test_burn_clips_to_the_grid(self):
        grid = np.full((180, 360), biomes.NODATA, np.uint16)
        biomes.burn(grid, np.array(_square(178, 0, 182, 1), float), [0], 5, 1)
        assert np.nonzero(grid == 5)[1].tolist() == [358, 359]


class TestCache:
    def test_built_once_then_reused(self, tmp_path, monkeypatch):
        downloads = []

        def fake_download(url, dest):
            downloads.append(url)
            with open(dest, "wb") as fh:
                fh.write(_archive())

        monkeypatch.setattr(biomes, "_download", fake_download)
        monkeypatch.setattr(biomes, "CELLS_PER_DEGREE", 1)
        layer = biomes.load_ecoregions(str(tmp_path))
        assert downloads == [biomes.ECOREGIONS_URL]
        assert layer.ids.shape == (180, 360) and layer.names[2] == "Cerrado"
        assert [p.name for p in tmp_path.iterdir()] == ["ecoregions2017_1pd_v1.npz"]   # the zip is gone
        biomes.load_ecoregions(str(tmp_path))
        assert len(downloads) == 1

    def test_failures_are_biome_data_errors(self, tmp_path, monkeypatch):
        def broken(url, dest):
            with open(dest, "wb") as fh:
                fh.write(b"not a zip")

        monkeypatch.setattr(biomes, "_download", broken)
        with pytest.raises(biomes.BiomeDataError, match="Could not read"):
            biomes.ensure_ecoregion_data(str(tmp_path))
        assert not [p for p in tmp_path.iterdir() if p.suffix == ".npz"]


class TestColours:
    IDS = [[0, 1], [2, biomes.NODATA]]

    def test_by_biome(self):
        rgba = biomes.ecoregion_rgba(_layer(self.IDS), alpha=0.5)
        assert rgba[0, 0].tolist() == [*biomes.BIOMES[biomes.ROCK_AND_ICE][1], 128]
        assert rgba[0, 1].tolist() == [*biomes.BIOMES[4][1], 128]
        assert rgba[1, 0].tolist() == [*biomes.BIOMES[7][1], 128]
        assert rgba[1, 1, 3] == 0                                       # sea and lakes are clear

    def test_by_ecoregion_and_only_some_biomes(self):
        rgba = biomes.ecoregion_rgba(_layer(self.IDS), alpha=1.0, classes=(4,), ecoregions=True)
        assert rgba[0, 1].tolist() == [0x11, 0x22, 0x33, 255]
        assert rgba[0, 0, 3] == rgba[1, 0, 3] == rgba[1, 1, 3] == 0


def _globe(lat, lon, radius_km=None):
    fig = Figure(figsize=(4, 4))
    ax = fig.add_subplot(projection=globe_projection(lat, lon))
    assert isinstance(ax, GeoAxes)
    ax.set_global()
    if radius_km is not None:
        half = ax.projection.x_limits[1] * np.sin(radius_km * 1000 / 6_371_008.8)
        ax.set_extent((-half, half, -half, half), crs=ax.projection)
    return ax


class TestVisible:
    # Rock & Ice north of the equator, Cerrado south of it, at 1 cell per degree
    IDS = np.vstack([np.zeros((90, 360)), np.full((90, 360), 2)])

    def test_halves_of_the_globe(self):
        counts = biomes.visible_ecoregions([_globe(0, 0)], _layer(self.IDS), samples=100)
        assert set(counts) == {0, 2}
        assert abs(counts[0] - counts[2]) < 0.02 * sum(counts.values())
        assert sum(counts.values()) == pytest.approx(np.pi / 4 * 100 ** 2, rel=0.02)   # only the disc

    def test_a_radius_map_sees_only_its_disc(self):
        counts = biomes.visible_ecoregions([_globe(45, 0, radius_km=500)], _layer(self.IDS), samples=50)
        assert set(counts) == {0}

    def test_globes_add_up(self):
        layer = _layer(self.IDS)
        both = biomes.visible_ecoregions([_globe(45, 0, 500), _globe(-45, 0, 500)], layer, samples=50)
        assert set(both) == {0, 2} and both[0] == both[2]


class TestKeys:
    def _texts(self, legend):
        return legend.get_title().get_text(), [t.get_text() for t in legend.get_texts()]

    def test_biome_key_names_the_biomes_on_the_map_in_order(self):
        ax = _globe(0, 0)
        legend = biomes.add_biome_legend(ax, Counter({0: 5, 2: 9, 1: 1}), _layer([[0]]))
        title, labels = self._texts(legend)
        assert labels == [biomes.BIOMES[4][0], biomes.BIOMES[7][0], "Rock & Ice"]
        assert "Biomes" in title

    def test_biome_key_only_the_chosen(self):
        ax = _globe(0, 0)
        layer = _layer([[0]])
        _, labels = self._texts(biomes.add_biome_legend(ax, Counter({0: 5, 2: 9}), layer, classes=(7, 4)))
        assert labels == [biomes.BIOMES[7][0]]
        assert biomes.add_biome_legend(ax, Counter({0: 5}), layer, classes=(4,)) is None

    def test_ecoregion_key_names_the_largest(self):
        ax = _globe(0, 0)
        layer = _layer([[0]])
        title, labels = self._texts(biomes.add_ecoregion_legend(ax, Counter({0: 5, 2: 9, 1: 1}), layer, limit=2))
        assert labels == ["Cerrado", "Rock and Ice"]
        assert title.endswith("the 2 largest of 3 on the map")
        title, labels = self._texts(biomes.add_ecoregion_legend(ax, Counter({0: 5, 2: 9}), layer, classes=(7,)))
        assert labels == ["Cerrado"] and title == "Ecoregions (RESOLVE 2017)"
        assert biomes.add_ecoregion_legend(ax, Counter(), layer) is None
