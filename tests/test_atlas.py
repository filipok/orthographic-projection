"""Tests for atlases: reading the file, checking and naming the maps, building and laying out the PDF."""

import itertools
import json
import io
import os
import sys
import re
import tomllib
from pathlib import Path

import pytest
from PIL import Image

import atlas
import ortho

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "atlases" / "romania_school.toml"
FIELDS = [
    "city", "lat", "lon", "provider", "zoom", "dpi", "radius", "both_hemispheres", "beside", "no_circles", "koppen",
    "temperature", "elevation", "rivers", "route", "route_legend",
]
LINE = {"type": "Feature", "properties": {"name": "A line"},
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 10]]}}

SMALL = """
title = "Test atlas"
subtitle = "Two parts"
language = "en"

[cover.map]
city = "Paris"

[[pages]]
type = "section"
title = "Europe"
text = "About Europe."

[[pages]]
title = "Relief"
text = \"\"\"
First paragraph,
over two lines.

Second paragraph.
\"\"\"
questions = ["Which mountains?", "Which rivers?"]
[pages.map]
lat = 50
lon = 12
radius = 2000
elevation = true

[[pages]]
title = "Both sides"
[pages.map]
city = "Lisbon"
both-hemispheres = true

[[pages]]
title = "Notes"
text = "A page of text."
"""


TWO_MAPS = """
title = "Two maps a page"
language = "en"

[[pages]]
title = "January and July"
heading = "January"
questions = ["Where is it warmest?"]
[pages.map]
lat = 46
lon = 25
radius = 400
temperature = "january"
[pages.second]
title = "July"
[pages.second.map]
lat = 46
lon = 25
radius = 400
temperature = "july"

[[pages]]
title = "Relief"
layout = "detail"
[pages.map]
lat = 46
lon = 25
radius = 400
elevation = true
[pages.second]
title = "Detail: the Făgăraș Mountains"
[pages.second.map]
lat = 45.7
lon = 24.8
radius = 100
elevation = true
no-key = true
"""


def _write(tmp_path, text=SMALL, name="test.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path, text=SMALL):
    return atlas.load_atlas(str(_write(tmp_path, text)))


@pytest.fixture
def renders(monkeypatch):
    """Replace the renderer with one that writes a small transparent PNG, two globes wide for two globes."""
    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        wide = kwargs.get("both_hemispheres") or kwargs.get("beside")
        img = Image.new("RGBA", (400, 220) if wide else (200, 230), (0, 0, 0, 0))
        img.paste((90, 150, 200, 255), (10, 10, 190, 190))
        img.save(kwargs["output_filename"])
        return kwargs["output_filename"]

    monkeypatch.setattr(ortho, "generate_orthographic_map", render)
    return calls


def _media_boxes(pdf: Path) -> list[tuple[float, float]]:
    boxes = re.findall(rb"/MediaBox \[ ?0 0 ([\d.]+) ([\d.]+) ?\]", pdf.read_bytes())
    return [(float(w), float(h)) for w, h in boxes]


class TestReading:
    def test_settings_and_pages(self, tmp_path):
        book = _load(tmp_path)
        assert (book.title, book.subtitle, book.language, book.paper, book.dpi) == (
            "Test atlas", "Two parts", "en", "a4", atlas.DEFAULT_ATLAS_DPI)
        assert [p.kind for p in book.pages] == ["section", "map", "map", "text"]
        assert book.pages[1].questions == ["Which mountains?", "Which rivers?"]
        assert book.pages[1].map == {"lat": 50, "lon": 12, "radius": 2000, "elevation": True}
        assert book.cover is not None and book.cover.map == {"city": "Paris"}
        assert [key for key, _ in book.maps()] == ["cover", "2", "3"]
        assert book.notes == "below"

    def test_paragraphs_join_lines_and_split_at_blank_lines(self, tmp_path):
        assert atlas.paragraphs(_load(tmp_path).pages[1].text) == [
            "First paragraph, over two lines.", "Second paragraph."]

    def test_a_page_of_two_maps(self, tmp_path):
        book = _load(tmp_path, TWO_MAPS)
        pair, relief = book.pages
        assert (pair.heading, pair.layout, pair.second.title) == ("January", "pair", "July")
        assert pair.second.map["temperature"] == "july" and pair.questions == ["Where is it warmest?"]
        assert (relief.heading, relief.layout, relief.second.title) == ("", "detail", "Detail: the Făgăraș Mountains")
        assert [key for key, _ in book.maps()] == ["1", "1b", "2", "2b"]

    @pytest.mark.parametrize("text, message", [
        ('title = "A"\n[[pages]]\ntitle = "T"\ntext = "x"\n[pages.second]\ntitle = "S"\n[pages.second.map]\ncity = "Paris"',
         "a text page has no map to put a second one under"),
        ('title = "A"\n[[pages]]\ntitle = "M"\nlayout = "detail"\n[pages.map]\ncity = "Paris"',
         "layout is for a page with a second map"),
        ('title = "A"\n[[pages]]\ntitle = "M"\nheading = "H"\n[pages.map]\ncity = "Paris"',
         "heading is for a page with a second map"),
        ('title = "A"\n[[pages]]\ntitle = "M"\nlayout = "side"\n[pages.map]\ncity = "Paris"\n'
         '[pages.second]\ntitle = "S"\n[pages.second.map]\ncity = "Rome"', "layout must be one of pair, detail"),
        ('title = "A"\n[[pages]]\ntitle = "M"\norientation = "landscape"\n[pages.map]\ncity = "Paris"\n'
         '[pages.second]\ntitle = "S"\n[pages.second.map]\ncity = "Rome"', "a page of two maps is portrait"),
        ('title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\n[pages.second]\ntitle = "S"\n'
         'text = "x"\n[pages.second.map]\ncity = "Rome"', "second: unknown key 'text'"),
        ('title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\n[pages.second]\n'
         '[pages.second.map]\ncity = "Rome"', "second needs a title"),
    ])
    def test_two_map_mistakes_are_named(self, tmp_path, text, message):
        with pytest.raises(atlas.AtlasError, match=re.escape(message)):
            _load(tmp_path, text)

    @pytest.mark.parametrize("text, message", [
        ('title = "A"\npapr = "a4"', "unknown key 'papr'. Did you mean 'paper'?"),
        ('subtitle = "no title"', "needs a title"),
        ('title = "A"\npaper = "b5"', "paper must be one of a4, a3, letter"),
        ('title = "A"\nlanguage = "fr"', "language must be one of en, ro"),
        ('title = "A"\ndpi = 5000', "dpi must be a whole number"),
        ('title = "A"\ntext_scale = 9', "text_scale must be a number from 0.5 to 4"),
        ('title = "A"\ntext_scale = "big"', "text_scale must be a number"),
        ('title = "A"\nnotes = "margin"', "notes must be one of below, appendix"),
        ('title = "A"\n[[pages]]\ntitle = "S"\ntype = "section"\n[pages.map]\ncity = "Paris"', "a section page has no map"),
        ('title = "A"\n[[pages]]\ntitle = "M"\ntype = "map"', "a map page needs a map table or a recipe"),
        ('title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\ndpi = 300', "dpi is set for the whole atlas"),
        ('title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\noutput = "x.png"', "output is set for"),
        ('title = "A"\n[[pages]]\ntitle = "M"\norientation = "sideways"\n[pages.map]\ncity = "Paris"', "orientation"),
        ('title = "A"\n[[pages]]\ntext = "no title"', "page 1 needs a title"),
        ('title = "A"\n[[pages]]\ntitle = "Q"\nquestions = [1, 2]', "questions must be a list of text"),
    ])
    def test_mistakes_are_named(self, tmp_path, text, message):
        with pytest.raises(atlas.AtlasError, match=re.escape(message)):
            _load(tmp_path, text)

    def test_bad_toml_and_missing_file(self, tmp_path):
        with pytest.raises(atlas.AtlasError, match="not valid TOML"):
            _load(tmp_path, "title = ")
        with pytest.raises(atlas.AtlasError, match="cannot read atlas"):
            atlas.load_atlas(str(tmp_path / "missing.toml"))


class TestPlanning:
    def test_map_options_go_through_the_command_line_parser(self, tmp_path):
        book = _load(tmp_path)
        args = atlas.page_args(book, book.pages[1], "here", str(tmp_path / "m.png"))
        assert (args.lat, args.lon, args.radius, args.elevation) == (50, 12, 2000, True)
        assert args.dpi == atlas.DEFAULT_ATLAS_DPI and args.output == str(tmp_path / "m.png")

    def test_a_bad_option_is_the_atlas_error_not_an_exit(self, tmp_path):
        book = _load(tmp_path, 'title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\nradis = 50')
        with pytest.raises(atlas.AtlasError, match=r"page 1 \(M\): map: unknown option 'radis'. Did you mean 'radius'"):
            atlas.plan_maps(book, str(tmp_path / "maps"), str(tmp_path / "cache"))

    def test_the_renderers_checks_name_the_page(self, tmp_path):
        book = _load(tmp_path, 'title = "A"\n[[pages]]\ntitle = "Climate"\n[pages.map]\ncity = "Paris"\n'
                               'koppen-class = "Zzz"')
        with pytest.raises(atlas.AtlasError, match=r"test.toml page 1 \(Climate\): Unknown Köppen-Geiger class"):
            atlas.plan_maps(book, str(tmp_path / "maps"), str(tmp_path / "cache"))

    def test_a_recipe_with_the_map_table_on_top(self, tmp_path):
        (tmp_path / "recipes").mkdir()
        (tmp_path / "recipes" / "r.toml").write_text('city = "Paris"\nkoppen = true\nradius = 900\n'
                                                     'output = "ignored.png"\n', encoding="utf-8")
        book = _load(tmp_path, 'title = "A"\n[[pages]]\ntitle = "M"\nrecipe = "recipes/r.toml"\n'
                               '[pages.map]\nlat = 10\nlon = 20\nradius = 500')
        args = atlas.page_args(book, book.pages[0], "here", str(tmp_path / "m.png"))
        assert (args.city, args.lat, args.lon, args.radius, args.koppen) == (None, 10, 20, 500, True)
        assert args.output == str(tmp_path / "m.png")

    def test_routes_are_relative_to_the_atlas(self, tmp_path):
        (tmp_path / "routes").mkdir()
        (tmp_path / "routes" / "line.geojson").write_text(json.dumps(LINE), encoding="utf-8")
        book = _load(tmp_path, 'title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\n'
                               'route = ["routes/line.geojson"]')
        args = atlas.page_args(book, book.pages[0], "here", str(tmp_path / "m.png"))
        assert args.route == [str(tmp_path / "routes" / "line.geojson")]

    def test_maps_are_named_by_title_and_options(self, tmp_path):
        book = _load(tmp_path)
        maps = str(tmp_path / "maps")
        plans = atlas.plan_maps(book, maps, str(tmp_path / "cache"))
        assert [p.key for p in plans] == ["cover", "2", "3"]
        assert all(re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{12}\.png", os.path.basename(p.path)) for p in plans)
        assert os.path.basename(plans[1].path).startswith("relief-")
        book.pages[1].text = "Other words."                        # text alone draws nothing again
        assert atlas.plan_maps(book, maps, str(tmp_path / "cache"))[1].path == plans[1].path
        book.pages[1].map["radius"] = 1500
        assert atlas.plan_maps(book, maps, str(tmp_path / "cache"))[1].path != plans[1].path
        book.dpi = 150
        assert atlas.plan_maps(book, maps, str(tmp_path / "cache"))[2].path != plans[2].path

    def test_options_at_their_defaults_leave_the_name_alone(self, tmp_path):
        book = _load(tmp_path)
        args = atlas.page_args(book, book.pages[1], "here", str(tmp_path / "m.png"))
        name = atlas._fingerprint(args)
        delattr(args, "relief_alpha")                   # as before the option was added
        assert atlas._fingerprint(args) == name
        args.relief_alpha = 0.3
        assert atlas._fingerprint(args) != name

    def test_the_atlas_language_for_the_maps_that_name_states(self, tmp_path):
        book = _load(tmp_path, SMALL.replace('language = "en"', 'language = "ro"'))
        relief = book.pages[1]
        assert atlas.page_args(book, relief, "here", str(tmp_path / "m.png")).language == "en"   # names nothing
        relief.map["borders"] = "1914"
        assert atlas.page_args(book, relief, "here", str(tmp_path / "m.png")).language == "ro"
        relief.map["language"] = "en"                                                           # its own wins
        assert atlas.page_args(book, relief, "here", str(tmp_path / "m.png")).language == "en"
        relief.map.pop("language")
        assert atlas.page_args(_load(tmp_path), relief, "here", str(tmp_path / "m.png")).language == "en"

    def test_the_atlas_language_for_a_named_base_map(self, tmp_path):
        book = _load(tmp_path, SMALL.replace('language = "en"', 'language = "ro"'))
        page = book.pages[1]
        assert atlas.page_args(book, page, "here", str(tmp_path / "m.png")).language == "en"
        page.map["provider"] = "natural_earth"
        assert atlas.page_args(book, page, "here", str(tmp_path / "m.png")).language == "ro"

    def test_text_scale_for_every_map_unless_its_own(self, tmp_path):
        book = _load(tmp_path, SMALL.replace('language = "en"', 'language = "en"\ntext_scale = 2'))
        assert book.text_scale == 2
        args = atlas.page_args(book, book.pages[1], "here", str(tmp_path / "m.png"))
        assert args.text_scale == 2
        plain = atlas.page_args(_load(tmp_path), book.pages[1], "here", str(tmp_path / "m.png"))
        assert plain.text_scale == 1 and atlas._fingerprint(plain) != atlas._fingerprint(args)
        book.pages[1].map["text-scale"] = 1.5
        assert atlas.page_args(book, book.pages[1], "here", str(tmp_path / "m.png")).text_scale == 1.5

    def test_slugs_drop_diacritics(self):
        assert atlas._slug("Harta fizică a României") == "harta-fizica-a-romaniei"
        assert atlas._slug("!!!") == "map"


class TestBuilding:
    def test_build_draws_each_map_once(self, tmp_path, renders):
        book = _load(tmp_path)
        out, maps = tmp_path / "a.pdf", str(tmp_path / "maps")
        result = atlas.build_atlas(book, str(out), maps, cache_dir=str(tmp_path / "cache"))
        assert (result.drawn, result.reused, result.failures) == (3, 0, {})
        assert len(renders) == 3 and all(r["dpi"] == atlas.DEFAULT_ATLAS_DPI for r in renders)
        assert out.read_bytes().startswith(b"%PDF")
        # Cover, one contents page, four pages
        assert result.pages == 6 == len(_media_boxes(out))
        assert sorted(os.listdir(maps)) == sorted(os.path.basename(p) for p in os.listdir(maps) if p.endswith(".png"))

        again = atlas.build_atlas(book, str(out), maps, cache_dir=str(tmp_path / "cache"))
        assert (again.drawn, again.reused) == (0, 3) and len(renders) == 3

        book.pages[1].map["radius"] = 1500                          # one map changes: it alone is drawn
        third = atlas.build_atlas(book, str(out), maps, cache_dir=str(tmp_path / "cache"))
        assert (third.drawn, third.reused) == (1, 2) and len(renders) == 4
        assert len(os.listdir(maps)) == 3                           # the old drawing is pruned

    def test_force_draws_them_all(self, tmp_path, renders):
        book = _load(tmp_path)
        atlas.build_atlas(book, str(tmp_path / "a.pdf"), str(tmp_path / "maps"), cache_dir=str(tmp_path / "c"))
        result = atlas.build_atlas(book, str(tmp_path / "a.pdf"), str(tmp_path / "maps"), force=True,
                                   cache_dir=str(tmp_path / "c"))
        assert result.drawn == 3 and len(renders) == 6

    def test_proof_draws_nothing(self, tmp_path, renders):
        book = _load(tmp_path)
        result = atlas.build_atlas(book, str(tmp_path / "p.pdf"), str(tmp_path / "maps"), proof=True,
                                   cache_dir=str(tmp_path / "cache"))
        assert renders == [] and result.pages == 6 and result.drawn == 0
        assert (tmp_path / "p.pdf").is_file()

    def test_a_failed_map_leaves_a_placeholder(self, tmp_path, monkeypatch):
        def render(**kwargs):
            raise RuntimeError("tiles unreachable")

        monkeypatch.setattr(ortho, "generate_orthographic_map", render)
        book = _load(tmp_path)
        result = atlas.build_atlas(book, str(tmp_path / "a.pdf"), str(tmp_path / "maps"),
                                   cache_dir=str(tmp_path / "cache"))
        assert set(result.failures) == {"cover", "2", "3"}
        assert result.failures["2"] == "tiles unreachable" and result.pages == 6
        assert not [f for f in os.listdir(tmp_path / "maps") if f.endswith(".png")]

    def test_wide_maps_turn_the_page(self, tmp_path, renders):
        book = _load(tmp_path)
        out = tmp_path / "a.pdf"
        atlas.build_atlas(book, str(out), str(tmp_path / "maps"), cache_dir=str(tmp_path / "cache"))
        boxes = _media_boxes(out)
        portrait = boxes[0]
        assert portrait[1] > portrait[0]
        assert boxes[4] == (portrait[1], portrait[0])               # "Both sides": two globes, landscape
        assert all(box == portrait for i, box in enumerate(boxes) if i != 4)

    @pytest.mark.parametrize("shape, landscape", [("3:2", True), ("2:3", False), ("1:1", False)])
    def test_a_rectangle_turns_the_page_before_it_is_drawn(self, tmp_path, shape, landscape):
        text = SMALL.replace("radius = 2000\n", f'radius = 2000\nrectangle = "{shape}"\n')
        book = _load(tmp_path, text)
        assert atlas._image_aspect(None, book.pages[1]) == pytest.approx(ortho.parse_aspect(shape))
        out = tmp_path / "a.pdf"
        atlas.build_atlas(book, str(out), str(tmp_path / "maps"), proof=True, cache_dir=str(tmp_path / "cache"))
        width, height = _media_boxes(out)[3]                        # after the cover, contents and section
        assert (width > height) is landscape

    def test_orientation_can_be_set(self, tmp_path, renders):
        text = SMALL.replace('title = "Both sides"', 'title = "Both sides"\norientation = "portrait"')
        book = _load(tmp_path, text)
        out = tmp_path / "a.pdf"
        atlas.build_atlas(book, str(out), str(tmp_path / "maps"), proof=True, cache_dir=str(tmp_path / "cache"))
        assert len(set(_media_boxes(out))) == 1

    def test_paper_sizes(self, tmp_path):
        book = _load(tmp_path, 'title = "A"\npaper = "a3"\n[[pages]]\ntitle = "T"\ntext = "x"')
        out = tmp_path / "a.pdf"
        atlas.write_pdf(book, str(out))
        width, height = _media_boxes(out)[0]
        assert (round(width / 72, 2), round(height / 72, 2)) == atlas.PAPER_SIZES["a3"]

    def test_long_contents_take_several_pages_and_number_the_rest(self, tmp_path):
        pages = "".join(f'\n[[pages]]\ntype = "section"\ntitle = "Part {i}"\n' if i % 10 == 0 else
                        f'\n[[pages]]\ntitle = "Page {i}"\ntext = "Words."\n' for i in range(60))
        book = _load(tmp_path, 'title = "Long"' + pages)
        writer = atlas._Atlas(book, {}, {})
        chunks = writer.contents_chunks()
        assert len(chunks) == 2 and sum(map(len, chunks)) == 60
        assert all(book.pages[chunk[-1]].kind != "section" for chunk in chunks)   # no part title at a page's foot
        assert atlas.write_pdf(book, str(tmp_path / "a.pdf")) == 1 + 2 + 60

    def test_notes_in_an_appendix(self, tmp_path, renders):
        book = _load(tmp_path, SMALL.replace('language = "en"', 'language = "en"\nnotes = "appendix"'))
        writer = atlas._Atlas(book, {}, {})
        assert writer.appendix_parts() == ["notes", "questions"]
        assert [row[:2] for row in writer.contents_rows()[-3:]] == [
            ("appendix", "Appendices"), ("map", "Notes on the maps"), ("map", "Questions and exercises")]
        numbers = [3, 4, 5, 6]                                      # after the cover and the contents
        appendices, refs = writer.appendices(numbers)
        assert [(part, number, len(pages)) for part, number, pages in appendices] == [
            ("notes", 7, 1), ("questions", 8, 1)]
        # Only "Relief" has notes; "Both sides" has none, and the page of text keeps its own
        assert refs == {1: [("Notes on the maps", 7), ("Questions and exercises", 8)]}
        notes = [row.text for _, row in appendices[0][2][0]]
        assert notes == ["Part I · Europe", "Relief", "First paragraph, over two lines.", "Second paragraph."]
        questions = [(row.left, row.text, row.right) for _, row in appendices[1][2][0]]
        assert questions[1:] == [("", "Relief", "p. 4"), ("1.", "Which mountains?", ""), ("2.", "Which rivers?", "")]
        # The map page draws its title and map, and where its notes are, but not the notes
        page = writer.content(1, ("Europe", atlas.ACCENTS[0]), 4, refs[1])
        texts = [t.get_text() for t in page.fig.texts]
        assert "Relief" in texts and "Notes on the maps p. 7 · Questions and exercises p. 8" in texts
        assert not any("paragraph" in t or "mountains" in t for t in texts)
        out = tmp_path / "a.pdf"
        result = atlas.build_atlas(book, str(out), str(tmp_path / "maps"), cache_dir=str(tmp_path / "cache"))
        assert result.pages == 6 + 2 == len(_media_boxes(out))

    def test_the_map_takes_the_page_without_its_notes(self, tmp_path, renders):
        sizes = []
        for notes in ("below", "appendix"):
            book = _load(tmp_path, SMALL.replace('language = "en"', f'language = "en"\nnotes = "{notes}"'))
            atlas.build_atlas(book, str(tmp_path / "a.pdf"), str(tmp_path / "maps"), cache_dir=str(tmp_path / "c"))
            images = {p.key: p.path for p in atlas.plan_maps(book, str(tmp_path / "maps"), str(tmp_path / "c"))}
            page = atlas._Atlas(book, images, {}).content(1, ("Europe", atlas.ACCENTS[0]), 4)
            sizes.append(page.fig.axes[0].get_position().width * page.width)
        margin = atlas._Atlas(book, {}, {}).margin
        assert sizes[0] < sizes[1] == pytest.approx(atlas.PAPER_SIZES["a4"][0] - 2 * margin)
        # With nothing below it, the map sits under the title and leaves the room it does not need at the foot
        box = page.fig.axes[0].get_position()
        assert (1 - box.y1) * page.height < 1.5 < box.y0 * page.height

    def test_two_maps_on_a_page(self, tmp_path, renders):
        book = _load(tmp_path, TWO_MAPS)
        result = atlas.build_atlas(book, str(tmp_path / "a.pdf"), str(tmp_path / "maps"), cache_dir=str(tmp_path / "c"))
        assert len(renders) == 4 and not result.failures
        assert renders[3]["show_keys"] is False and renders[2]["show_keys"] is True     # the detail leaves out its key
        images = {p.key: p.path for p in atlas.plan_maps(book, str(tmp_path / "maps"), str(tmp_path / "c"))}
        writer = atlas._Atlas(book, images, {})
        # A pair: the same size, one under the other, each under its heading, on a portrait page
        page = writer.content(0, ("", atlas.ACCENTS[0]), 3)
        first, second = (ax.get_position() for ax in page.fig.axes)
        assert (page.width, page.height) == atlas.PAPER_SIZES["a4"]
        assert first.width == pytest.approx(second.width) and first.height == pytest.approx(second.height)
        assert second.y1 < first.y0
        texts = [t.get_text() for t in page.fig.texts]
        assert "January" in texts and "July" in texts
        # A detail: the first map, a 7:5 rectangle, as wide as the page, the detail in the room under it
        wide = tmp_path / "wide.png"
        Image.new("RGB", (700, 500), "white").save(wide)
        writer.images["2"] = str(wide)
        page = writer.content(1, ("", atlas.ACCENTS[0]), 4)
        main, detail = (ax.get_position() for ax in page.fig.axes)
        margin = writer.margin
        assert main.width * page.width == pytest.approx(page.width - 2 * margin)
        assert detail.y1 < main.y0 and detail.height * page.height >= atlas.MIN_DETAIL * 0.9
        assert "Detail: the Făgăraș Mountains" in [t.get_text() for t in page.fig.texts]

    def test_a_tall_first_map_leaves_its_detail_room(self, tmp_path):
        book = _load(tmp_path, TWO_MAPS)
        tall = tmp_path / "tall.png"
        Image.new("RGB", (100, 300), "white").save(tall)       # far taller than the page could fit with a detail
        page = atlas._Atlas(book, {"2": str(tall)}, {}).content(1, ("", atlas.ACCENTS[0]), 4)
        main, detail = (ax.get_position() for ax in page.fig.axes)
        assert detail.height * page.height >= atlas.MIN_DETAIL * 0.99 and detail.y1 < main.y0

    def test_text_is_wrapped_to_the_width(self):
        sheet = atlas._Sheet(atlas.PAPER_SIZES["a4"])
        prop = sheet.font(10)
        lines = sheet.wrap("Relieful României este variat și dispus aproape concentric " * 6, prop, 3.0)
        assert len(lines) > 3 and all(sheet.measure(line, prop) <= 3.0 for line in lines)
        short = sheet.fit("Agricultura: grâul, porumbul și floarea-soarelui", prop, 1.5)
        assert short.endswith("…") and sheet.measure(short, prop) <= 1.5

    def test_roman_numerals(self):
        assert [atlas._roman(n) for n in (1, 2, 3, 4, 9, 14)] == ["I", "II", "III", "IV", "IX", "XIV"]


class TestCommandLine:
    def test_check_lists_the_pages(self, tmp_path, capsys):
        path = _write(tmp_path)
        assert atlas.main([str(path), "--check", "--maps-dir", str(tmp_path / "m"),
                           "--cache-dir", str(tmp_path / "c")]) == 0
        out = capsys.readouterr().out
        assert "  2  map      Relief (to draw)" in out and "4 pages, 3 maps, 0 of them drawn already" in out

    def test_check_prints_diacritics_to_a_narrow_code_page(self, tmp_path, monkeypatch):
        path = _write(tmp_path, SMALL.replace('title = "Relief"', 'title = "Harta fizică a României"', 1))
        out = io.BytesIO()
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, encoding="cp1252"))
        assert atlas.main([str(path), "--check", "--maps-dir", str(tmp_path / "m"),
                           "--cache-dir", str(tmp_path / "c")]) == 0
        sys.stdout.flush()
        assert "Harta fizică a României" in out.getvalue().decode("utf-8")

    def test_build_and_exit_status(self, tmp_path, renders):
        path = _write(tmp_path)
        out = tmp_path / "out.pdf"
        assert atlas.main([str(path), "-o", str(out), "--maps-dir", str(tmp_path / "m"),
                           "--cache-dir", str(tmp_path / "c"), "--dpi", "80"]) == 0
        assert out.is_file() and {r["dpi"] for r in renders} == {80}

    def test_errors_exit_with_one(self, tmp_path, caplog):
        path = _write(tmp_path, 'title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Atlantis"')
        assert atlas.main([str(path), "--proof", "--cache-dir", str(tmp_path / "c")]) == 1
        assert "page 1 (M): argument --city: invalid choice: 'atlantis'" in caplog.text
        assert atlas.main([str(path), "--dpi", "9000"]) == 1

    def test_default_paths(self):
        assert atlas.default_paths("atlases/romania_school.toml") == (
            "atlas_romania_school.pdf", "atlas_romania_school_maps")


class TestFiles:
    """Reading an atlas into the web form's terms and writing it back."""

    @pytest.fixture
    def dirs(self, tmp_path):
        atlases, routes = tmp_path / "atlases", tmp_path / "routes"
        atlases.mkdir()
        routes.mkdir()
        (routes / "line.geojson").write_text(json.dumps(LINE), encoding="utf-8")
        return atlases, routes

    def test_round_trip(self, dirs, tmp_path):
        atlases, routes = dirs
        text = SMALL.replace('elevation = true', 'elevation = true\nroute = ["../routes/line.geojson"]\n'
                                                 'route-legend = true\ntemperature = "July"')
        text = text.replace('language = "en"', 'language = "en"\ntext_scale = 2.5\nnotes = "appendix"')
        (atlases / "small.toml").write_text("# My atlas\n# for class 8\n" + text, encoding="utf-8")
        data = atlas.read_atlas_json(str(atlases), str(routes), "small", FIELDS)["atlas"]
        relief = data["pages"][1]
        assert relief["map"] == {"lat": 50, "lon": 12, "radius": 2000, "elevation": True,
                                 "route": ["line.geojson"], "route_legend": True, "temperature": "july"}
        assert relief["text"] == "First paragraph, over two lines.\n\nSecond paragraph."
        atlas.write_atlas(str(atlases), str(routes), "small", data, FIELDS, overwrite=True,
                          cache_dir=str(tmp_path / "c"))
        saved = (atlases / "small.toml").read_text(encoding="utf-8")
        assert saved.startswith("# My atlas\n# for class 8\n")
        again = atlas.load_atlas(str(atlases / "small.toml"))
        before = atlas.parse_atlas(tomllib.loads(text), str(atlases / "small.toml"))
        assert [(p.kind, p.title, atlas.paragraphs(p.text), p.questions) for p in again.pages] == \
               [(p.kind, p.title, atlas.paragraphs(p.text), p.questions) for p in before.pages]
        assert again.pages[1].map["route"] == ["../routes/line.geojson"]
        assert again.cover.map == {"city": "Paris"}
        assert data["text_scale"] == 2.5 and again.text_scale == 2.5
        assert data["notes"] == "appendix" and again.notes == "appendix"

    def test_a_second_map_survives_the_web_form(self, dirs, tmp_path):
        atlases, routes = dirs
        (atlases / "two.toml").write_text(TWO_MAPS, encoding="utf-8")
        data = atlas.read_atlas_json(str(atlases), str(routes), "two", FIELDS)["atlas"]
        pair, relief = data["pages"]
        assert pair["heading"] == "January" and pair["second"]["title"] == "July"
        assert pair["second"]["map"]["temperature"] == "july"
        assert relief["layout"] == "detail" and ["no-key", True] in [list(k) for k in relief["second"]["kept"]]
        atlas.write_atlas(str(atlases), str(routes), "two", data, FIELDS, overwrite=True, cache_dir=str(tmp_path / "c"))
        again = atlas.load_atlas(str(atlases / "two.toml"))
        before = atlas.parse_atlas(tomllib.loads(TWO_MAPS), str(atlases / "two.toml"))
        assert [(p.heading, p.layout, p.second.title, p.second.map) for p in again.pages] == \
               [(p.heading, p.layout, p.second.title, p.second.map) for p in before.pages]

    def test_options_the_form_lacks_are_kept(self, dirs, tmp_path):
        atlases, routes = dirs
        (atlases / "a.toml").write_text('title = "A"\n[[pages]]\ntitle = "M"\n[pages.map]\ncity = "Paris"\n'
                                        'volcanoes = true\n', encoding="utf-8")
        data = atlas.read_atlas_json(str(atlases), str(routes), "a", FIELDS)["atlas"]
        assert data["pages"][0]["kept"] == [("volcanoes", True)]
        atlas.write_atlas(str(atlases), str(routes), "a", json.loads(json.dumps(data)), FIELDS, overwrite=True,
                          cache_dir=str(tmp_path / "c"))
        assert atlas.load_atlas(str(atlases / "a.toml")).pages[0].map == {"city": "Paris", "volcanoes": True}

    def test_a_bad_map_is_not_saved(self, dirs, tmp_path):
        atlases, routes = dirs
        data = {"title": "A", "pages": [{"type": "map", "title": "M", "map": {"city": "Atlantis"}}]}
        with pytest.raises(atlas.AtlasError, match="invalid choice: 'atlantis'"):
            atlas.write_atlas(str(atlases), str(routes), "a", data, FIELDS, cache_dir=str(tmp_path / "c"))
        assert not (atlases / "a.toml").exists()
        data["pages"][0]["map"] = {"city": "Paris"}
        atlas.write_atlas(str(atlases), str(routes), "a", data, FIELDS, cache_dir=str(tmp_path / "c"))
        with pytest.raises(FileExistsError):
            atlas.write_atlas(str(atlases), str(routes), "a", data, FIELDS, cache_dir=str(tmp_path / "c"))

    def test_setting_a_pages_map_replaces_its_recipe(self, dirs, tmp_path):
        atlases, routes = dirs
        (tmp_path / "recipes").mkdir()
        (tmp_path / "recipes" / "r.toml").write_text('city = "Paris"\nvolcanoes = true\nradius = 900\n'
                                                     'output = "x.png"\n', encoding="utf-8")
        (atlases / "a.toml").write_text('title = "A"\n[[pages]]\ntitle = "M"\nrecipe = "../recipes/r.toml"\n'
                                        '[pages.map]\nkoppen = true\n', encoding="utf-8")
        assert atlas.map_request(str(atlases), str(routes), "a", "1", FIELDS) == {
            "city": "Paris", "radius": 900, "koppen": True}
        atlas.set_page_map(str(atlases), str(routes), "a", "1", {"city": "Lisbon", "radius": 500, "dpi": 150},
                           FIELDS, cache_dir=str(tmp_path / "c"))
        page = atlas.load_atlas(str(atlases / "a.toml")).pages[0]
        assert page.recipe is None
        assert page.map == {"city": "Lisbon", "radius": 500, "volcanoes": True}   # kept from the recipe; no dpi

    def test_pages_without_maps_are_refused(self, dirs):
        atlases, routes = dirs
        (atlases / "a.toml").write_text('title = "A"\n[[pages]]\ntitle = "T"\ntext = "x"\n', encoding="utf-8")
        with pytest.raises(atlas.AtlasError, match="Page 1 has no map"):
            atlas.map_request(str(atlases), str(routes), "a", "1", FIELDS)
        with pytest.raises(atlas.AtlasError, match="no page 7"):
            atlas.map_request(str(atlases), str(routes), "a", "7", FIELDS)
        with pytest.raises(atlas.AtlasError, match="no cover map"):
            atlas.map_request(str(atlases), str(routes), "a", "cover", FIELDS)

    def test_list_and_delete(self, dirs):
        atlases, _ = dirs
        (atlases / "good.toml").write_text('title = "Good"\n[[pages]]\ntitle = "T"\n', encoding="utf-8")
        (atlases / "bad.toml").write_text('titel = "Bad"\n', encoding="utf-8")
        (atlases / "Not A Name.toml").write_text('title = "X"\n', encoding="utf-8")
        listed = atlas.list_atlases(str(atlases))
        assert [a["name"] for a in listed] == ["bad", "good"]
        assert listed[1] == {"name": "good", "title": "Good", "pages": 1} and "error" in listed[0]
        atlas.delete_atlas(str(atlases), "good")
        assert not (atlases / "good.toml").exists()
        with pytest.raises(FileNotFoundError):
            atlas.delete_atlas(str(atlases), "good")
        with pytest.raises(atlas.AtlasError, match="not an atlas name"):
            atlas.atlas_path(str(atlases), "../etc")

    def test_text_is_written_as_toml_strings(self):
        for text in ('Short "quoted" text', "Long " * 40 + 'with "quotes" and a \\ backslash',
                     "One.\n\nTwo \"\"\" three."):
            written = atlas._toml_text(text)
            assert atlas.paragraphs(tomllib.loads(f"t = {written}")["t"]) == atlas.paragraphs(text)


class TestBundledAtlas:
    def test_romania_then_the_eu_then_the_world(self):
        book = atlas.load_atlas(str(BUNDLED))
        assert book.language == "ro" and book.cover is not None
        assert [p.title for p in book.pages if p.kind == "section"] == ["România", "Uniunea Europeană", "Lumea"]
        sections = [i for i, p in enumerate(book.pages) if p.kind == "section"]
        assert sections[0] == 0 and all(b - a > 5 for a, b in itertools.pairwise(sections))
        assert all(p.text and p.questions for p in book.pages if p.kind == "map")

    def test_every_map_checks_out(self, tmp_path):
        book = atlas.load_atlas(str(BUNDLED))
        plans = atlas.plan_maps(book, str(tmp_path / "maps"), str(tmp_path / "cache"))
        assert len(plans) == 1 + sum(p.kind == "map" for p in book.pages) + sum(p.second is not None for p in book.pages)
        assert len({p.path for p in plans}) == len(plans)          # each map has a file of its own

    def test_proof_lays_out_every_page(self, tmp_path, renders):
        book = atlas.load_atlas(str(BUNDLED))
        out = tmp_path / "proof.pdf"
        result = atlas.build_atlas(book, str(out), str(tmp_path / "maps"), proof=True,
                                   cache_dir=str(tmp_path / "cache"))
        writer = atlas._Atlas(book, {}, {})
        appendix = sum(len(pages) for _, _, pages in writer.appendices(list(range(len(book.pages))))[0])
        assert book.notes == "appendix" and appendix > 1
        assert renders == [] and result.pages == 1 + len(writer.contents_chunks()) + len(book.pages) + appendix
