0# Adversarial Review: orthographic-projection

**Date:** 2026-10-02
**Scope:** `ortho.py`, `koppen.py`, `tests/test_ortho.py`, `pyproject.toml`, `requirements.txt`, `README.md`, plus the uncommitted diff to `ortho.py`.
**Baseline:** `main` @ `71bdbfb` + working-tree changes.
**Updated:** 2026-10-04 against the latest `main`. All 22 findings are resolved; the Google Maps setup under #7 is finished. #23 (polar caps) is a known cosmetic limitation, left as is by owner decision; since 2026-10-08 the opt-in `--ice` overlay covers it. Line references in resolved findings point to the code as it was when each was written.
**Method:** Read all source, then tried to break each claim the code, docstrings and README make. Every finding marked **[verified]** was reproduced in this environment (Python 3.14.3, cartopy 0.25.0). The rest come from reading the code and have a concrete failure path.

Test suite status: `29 passed in 1.98s` at review time; `53 passed` after the route and packaging fixes. The tests pass, but most of the findings below are bugs the suite cannot see.

---

## Summary

| # | Severity | Finding |
|---|---|---|
| 1 | ~~Critical~~ Resolved | `pip install` / `pip install -e .` is broken: invalid build backend |
| 2 | ~~Critical~~ Resolved | Köppen auto-download can never succeed (AWS WAF challenge); it hangs about 5 min and then crashes |
| 3 | ~~High~~ Resolved | Uncommitted change draws a hard-coded red route on **every** map |
| 4 | ~~High~~ Resolved | Tile caching does not exist; `--cache-dir` moves Natural Earth data instead |
| 5 | ~~High~~ Resolved | README's own CLI example `--city NYC` is rejected by the parser |
| 6 | ~~High~~ Resolved | Köppen data is attributed to the wrong paper (license compliance) |
| 7 | ~~High~~ Resolved | No attribution for OSM/Google tiles; Google tiles are used without an API key |
| 8 | ~~Medium~~ Resolved | Köppen failure aborts the whole render after the tiles were already fetched |
| 9 | ~~Medium~~ Resolved | Partial or corrupt Köppen downloads get cached permanently (no atomic write) |
| 10 | ~~Medium~~ Resolved | Download retries 30× on permanent errors (404, DNS, etc.) |
| 11 | ~~Medium~~ Resolved | `--koppen-alpha` is not validated in CLI mode; crashes late |
| 12 | ~~Medium~~ Resolved | `-o` into a missing directory crashes after all the work is done |
| 13 | ~~Medium~~ Resolved | Default 600 DPI × 20 in figure gives about 144 MP output; regrid is capped at 4096, so the extra DPI is upscaling |
| 14 | ~~Medium~~ Resolved | Köppen overlay is regridded at cartopy's default 750 px and comes out blocky |
| 15 | ~~Medium~~ Resolved | Figures leak on any exception (no `try/finally` around `plt.close`) |
| 16 | ~~Low~~ Resolved | Distance circles draw chords across the far side for radii ≳ 10 000 km |
| 17 | ~~Low~~ Resolved | Cache scan can pick up a `_conf_` (confidence) raster |
| 18 | ~~Low~~ Resolved | Dead code, unused imports and contradictory comments in `koppen.py` |
| 19 | ~~Low~~ Resolved | Docstring and README statements that are false |
| 20 | ~~Low~~ Resolved | Test-suite gaps and test pollution |
| 21 | ~~Low~~ Resolved | Packaging/metadata inconsistencies |
| 22 | ~~Medium~~ Resolved | Tile download failures crash `savefig`; the `try/except` around `add_image` never fires |
| 23 | Low (won't fix; mitigated by `--ice`) | Polar caps beyond Web Mercator's ±85° show the flat fallback colour, a visible disc on satellite renders |

---

## Critical

### 1. Package cannot be built or installed **[verified]**
> **Resolved 2026-10-02:** backend set to `setuptools.build_meta` (setuptools>=77 for the SPDX `license` string) with explicit `py-modules`; wheel build and editable install verified.

`pyproject.toml:3`
```toml
build-backend = "setuptools.backends._legacy:_Backend"
```
This module does not exist in setuptools. Running `pip wheel . --no-deps` gives:
```
pip._vendor.pyproject_hooks._impl.BackendUnavailable: Cannot import 'setuptools.backends._legacy'
```
So the README's `pip install -e ".[dev]"` instructions and the `ortho` console script cannot work.
**Fix:** `build-backend = "setuptools.build_meta"`. Also add `[tool.setuptools] py-modules = ["ortho", "koppen"]` so discovery does not depend on flat-layout heuristics.

### 2. Köppen auto-download is dead on arrival **[verified]**
> **Resolved 2026-10-02:** the root cause was the file ID itself: Figshare's API reports `10808306` as `Entity not found`. The code now downloads the real V1 archive (`Beck_KG_V1.zip`, file `12407516`, 71 MB), verifies Figshare's published MD5 `69594689d3cdd8323a0f74ce658125a1`, and extracts only the requested raster. A bot-challenge response now fails immediately with manual-download instructions. Verified live: download + extract in 3.7 s, second call served from cache.

`koppen.py:40`, `koppen.py:121-179`, `koppen.py:209-215`

`Beck_KG_V1/` is git-ignored (`.gitignore:13`), so any fresh clone falls through to the Figshare download. Figshare now sits behind an AWS WAF bot challenge:
```
HTTP/1.1 202 Accepted
Content-Length: 0
x-amzn-waf-action: challenge
```
The code treats `202` as "file still generating" and sleeps 10 s per attempt for 30 attempts. A non-browser client never passes the challenge, so `--koppen` on a clean machine hangs for **about 5 minutes** and then raises `RuntimeError`. Finding #8 means that error kills the whole render.

Two more problems in the same path:
- File ID `10808306` is saved as `Beck_KG_V1_{period}_{resolution}.tif` without checking what it is. If it is the V1 ZIP (the existence of `_find_tif_in_zip` suggests someone expected a ZIP), PIL will fail on a ZIP named `.tif`, and #9 means that file then stays in the cache.
- The only success check is `size > 100_000`.

**Fix:** document the manual download as the supported path and fail fast with a clear message. Alternatively, use a mirror that serves the file directly (GloH2O hosts the V1/V2 data), verify a SHA-256, and extract from the ZIP when needed.

---

## High

### 3. Hard-coded route drawn on every map (uncommitted) **[verified in diff]**
> **Resolved 2026-10-02:** moved to `routes/gibraltar_ascension_falklands.geojson`; routes are now opt-in via `--route` / `routes=` (`routes.py`).

`ortho.py:257-271`
```python
# Step 7c: plot hard-coded line on map
ax.plot([-5.35, -9.5, ... -56.933], [36.14, 33.5, ... -53.067], color="red", ...)
```
A 39-point red polyline (roughly Gibraltar → Ascension Island → Falklands) is added to **every** render, whatever the centre, with no flag, parameter, docstring or test. For a Tokyo or Sydney render it appears as a stray red line on the limb or is clipped away; for anything near the Atlantic it covers the map. The point `(-14.36, -7.95)` is listed twice in a row, which creates a zero-length segment. The continuation lines are also mis-indented.

This looks like experiment code that would leak into `main` on the next `git commit -a`.
**Fix:** remove it, or turn it into an opt-in `routes: list[list[tuple[float, float]]] | None` parameter with a CLI flag such as `--route-file`.

### 4. "Tile cache" is fictional **[verified]**
> **Resolved 2026-10-02:** OSM tiles now go through `CachedOSM`, which caches under `<cache dir>/osm/`. Turning on Cartopy's own cache would have made things worse: its `GoogleWTS.get_image` saves the grey placeholder it substitutes for a failed download, so one network blip becomes a permanent hole, and it never expires tiles. `CachedOSM` caches only successful downloads, writes `.npy` files atomically, refetches tiles older than 7 days (OSM tile policy) and sends a 30 s timeout. Google tiles are never cached (Google Maps Platform terms). `configure_tile_cache` no longer touches `cartopy.config["data_dir"]`, so Natural Earth data stays in Cartopy's default location. New `--no-cache` flag. Verified live: 53 tiles cached on the first Berlin zoom-3 render and reused on the next.

`ortho.py:39-53`, README "Notes"

- Cartopy tile sources only cache when constructed with `cache=True`. The signature is `GoogleWTS.__init__(self, desired_tile_form='RGB', user_agent='CartoPy/0.25.0', cache=False)`, and `create_tile_source` never passes `cache`, so **every run re-downloads every tile**.
- Even with `cache=True`, cartopy reads `cartopy.config["cache_dir"]`. `configure_tile_cache` sets `cartopy.config["data_dir"]`.
- `data_dir` is where cartopy stores **Natural Earth shapefiles** (the `cfeature.LAND/OCEAN` fallback). So `--cache-dir` only moves the Natural Earth data, which then downloads again into the new location.

The README states: *"Subsequent runs reuse cached tiles, avoiding redundant downloads."* That is false.
**Fix:** pass `cache=cache_dir` (cartopy accepts a path) into the tile constructors, and stop overwriting `data_dir`.

### 5. README's first CLI example fails **[verified]**
> **Resolved 2026-10-02:** `--city` now uses `type=str.lower`, so any casing is accepted; covered by CLI parser and `run_cli` tests.

`ortho.py:480-486`, `README.md` ("`python ortho.py --city NYC ...`", "Pre-defined city (case-insensitive)")
```
error: argument --city: invalid choice: 'NYC' (choose from nyc, moscow, ...)
```
`choices` are lower-cased, but argparse compares the raw string. The case-insensitive lookup at `ortho.py:657-659` can therefore never see a non-lowercase value, so it is dead code.
**Fix:** add `type=str.lower` to the `--city` argument.

### 6. Köppen data attributed to the wrong paper (CC BY 4.0 compliance) **[verified]**
> **Resolved 2026-10-02:** credit now reads *Climate data: Beck et al. (2018), CC BY 4.0* (`koppen.KOPPEN_ATTRIBUTION`); module docstring, comments and README cite doi `10.1038/sdata.2018.214` and state 0.083° (~10 km). The `koppen.py:356` reference below is to the removed footer.

`koppen.py:1-13`, `koppen.py:356`, README "Data Sources"

The raster actually loaded is `Beck_KG_V1_present_0p083.tif` (about 10 km, 2160×4320). Its own `legend.txt` says:
> Please cite Beck et al. [2018] … Nature Scientific Data, 2018.

The image footer, module docstring and README all credit **Beck et al. (2023)**, "1 km", "CMIP6", and doi `10.1038/s41597-023-02549-6`. That is a different dataset. CC BY requires correct attribution, so every published `--koppen` render currently misattributes its data. The README also overstates the resolution by 10×.
**Fix:** credit Beck et al. (2018), *Sci. Data* 5, 180214, doi `10.1038/sdata.2018.214`, and state "0.083° (~10 km)".

### 7. Tile licensing / Terms of Service
> **Resolved 2026-10-02:** every render carries a provider credit in the bottom-right corner. The Google providers now use the official Map Tiles API (`google_tiles.py`) and are credited "Google Maps" plus the copyright string from the API's viewport endpoint. The key is read from `GOOGLE_MAPS_API_KEY`, falling back to `GOOGLE_API_KEY`; the CLI first loads `$ORTHO_ENV_FILE`, `~/myapikeys.env` and `./.env` (same scheme as newsgrab, never overriding variables already set).
>
> **Live check 2026-10-02:** the `GOOGLE_API_KEY` in `~/myapikeys.env` is a Gemini key and Google rejects it: `HTTP 403: Requests to this API tile method … are blocked`. The code path works; the key lacks permission.
>
> **Finished 2026-10-04:** a Maps-only key was added to `~/myapikeys.env` as `GOOGLE_MAPS_API_KEY`. Sessions succeed for both map types; the viewport copyright is "Map data ©2026 Google, INEGI" (roadmap, zoom 3) and "Imagery ©2026 NASA" (satellite, zoom 3). Smoke renders of Tokyo with `google` and `google_satellite` loaded all tiles, logged that Google tiles are not cached, and showed "Google Maps" plus the copyright line in the bottom-right corner. `sample_sao_paulo.png` was re-rendered (Google Satellite, zoom 3, 300 DPI: 4680×4680 px, 7.0 MB) and now carries the credit.
>
> **Caveats:** the text "Google Maps" is used instead of Google's preferred logo, and Google's policies restrict caching and offline use of exported imagery; check before publishing Google-based maps.

`ortho.py:119-121`, `ortho.py:293-295`

- OSM tiles require visible "© OpenStreetMap contributors" attribution on the output. None is drawn, even though the code already adds a footer for Köppen.
- `GoogleTiles` reads Google's internal tile endpoints without an API key. That is against Google Maps Platform ToS, and the endpoint can be rate-limited or blocked at any time. The README only says "may be subject to provider availability."
- The README describes OSM tiles as "ODbL". The data is ODbL; the rendered tiles need attribution under OSMF's tile usage policy.

**Fix:** draw a provider-specific attribution footer for every render. Either drop the Google providers or label them clearly as unofficial and against ToS.

---

## Medium

### 8. Köppen failure kills the render; tile failure does not
> **Resolved 2026-10-02:** `KoppenDataError`/`OSError` from the overlay are caught in `generate_orthographic_map`; the map is saved without the overlay, legend or Köppen credit, and a warning is logged.

`ortho.py:242-257`

Tile fetching is wrapped in `try/except` and degrades gracefully. `add_koppen_overlay` is not wrapped. Any download, PIL or alpha error happens *after* the expensive tile fetch and loses all of that work. Combined with #2, this is the default outcome for `--koppen` on a fresh clone.

### 9. Non-atomic download poisons the cache
> **Resolved 2026-10-02:** downloads and ZIP extraction write to `*.part` and `os.replace` into place. Cached and manual rasters are checked for a TIFF header; an invalid cached file is deleted and fetched again.

`koppen.py:142-147`, `koppen.py:203-207`, `koppen.py:213`

The download writes directly to the final `.tif` path. If the process is interrupted (Ctrl+C, crash, power loss, or two concurrent runs), a truncated file is left behind. On the next run, the cache scan at line 203 returns **any** `.tif` whose name matches, with no size or integrity check, so the render fails every time until the user finds and deletes the file. `tempfile` is imported (line 20) but never used, which suggests this was intended.
**Fix:** download to `dest + ".part"` (or a `NamedTemporaryFile` in the same directory), verify it, then `os.replace`.

### 10. Retry loop retries non-retryable errors
> **Resolved 2026-10-02:** at most 3 attempts with 2 s / 4 s backoff, only for HTTP 429/5xx and network errors; `urlopen(..., timeout=60)`. A 404 fails on the first attempt.

`koppen.py:172-177`

`except Exception` covers `HTTPError 404/403`, DNS failure, SSL errors, disk-full `OSError`, and so on. Each is retried 30 times with a 10 s sleep, so a typo in the URL costs 5 minutes. There is also no `timeout=` on `urlopen`, so one stalled connection can hang forever.
**Fix:** retry only on 5xx/202/`URLError` timeouts, use exponential backoff with a small cap, and pass `urlopen(req, timeout=30)`.

### 11. `--koppen-alpha` unvalidated in CLI **[verified]**
> **Resolved 2026-10-02:** `validate_render_options` checks `dpi` (10–1200) and `koppen_alpha` (0–1). `run_cli` calls it before anything else and exits with `ERROR: Invalid option: …`; `generate_orthographic_map` calls it before creating tiles or a figure and raises `ValueError`.

`ortho.py:659-665`

Interactive mode clamps alpha to 0–1 (`ortho.py:728`), but CLI mode passes it straight through. `--koppen-alpha 5` fetches all tiles and then dies with `ValueError: alpha (5) is outside 0-1 range`. `--dpi` has the same problem (no lower or upper bound): `--dpi 0` crashes and `--dpi 5000` will run out of memory.
**Fix:** validate in `run_cli` alongside lat/lon, or use a custom argparse `type=`.

### 12. `-o` into a non-existent directory fails at the last step
> **Resolved 2026-10-02:** `generate_orthographic_map` creates the output file's parent folder up front, for `-o` paths as well as `--output-dir`. Passing both now logs a warning that `--output-dir` is ignored.

`ortho.py:308-310`, `ortho.py:808-810`

`output_dir` gets `os.makedirs`, but an explicit `-o renders/x.png` does not. `savefig` raises `FileNotFoundError` only after all tiles are downloaded and regridded. `--output-dir` is also silently ignored when `-o` is given.
**Fix:** `os.makedirs(os.path.dirname(os.path.abspath(output_filename)), exist_ok=True)` before rendering, and warn or error on `-o` combined with `--output-dir`.

### 13. Default output size is excessive and mostly upscaled
> **Resolved 2026-10-03:** the owner chose a 300 DPI default (`DEFAULT_DPI`) for the CLI, interactive mode and the library: ~6,000 px instead of ~12,000 px. A London `--koppen` render went from 9360×9887 px / 4.4 MB / ~32 s to 4680×4943 px / 2.9 MB / 25 s. `--dpi 600` still works (range 10–1200, see #11). The README explains that imagery holds only ~2,000–4,000 px of real detail at zoom 3–4, while higher DPIs still sharpen text and lines.

`ortho.py:386`, `ortho.py:398-401`, `ortho.py:706`, `ortho.py:820`

`figsize=(20, 20)` at the CLI/interactive default of `dpi=600` is about **12 000 × 12 000 px (~144 MP)**, roughly a 576 MB RGBA canvas before PNG encoding. Meanwhile `regrid_shape = min(max(750, 20*dpi), 4096)` saturates at 4096 for any DPI above ~205. Above that point the "dynamic" regrid is a constant, and the extra pixels are nearest-neighbour upscaling of a 4096 px warp: larger files with no extra detail. Defaults also disagree (library 300, CLI 600, interactive hard-coded 600 with no prompt).
**Fix:** pick one default (e.g. 300), derive `max_regrid_shape` from the target pixel size, or expose `figsize`.

### 14. Köppen overlay rendered at low resolution
> **Resolved 2026-10-02:** `add_koppen_overlay(..., regrid_shape=...)` now receives the same regrid shape as the tiles. A side-by-side crop of a 3,000 px render shows the staircase edges gone; that render took 11.7 s instead of 7.9 s.

`koppen.py:360-369`

`GeoAxes.imshow` with a `transform` that differs from the axes projection warps through `regrid_shape`, which defaults to **750**. On a 6 000–12 000 px globe, the climate layer is visibly blocky next to the 4096 px tiles. Pass the same `regrid_shape` the tiles use.

### 15. Figure leak on exceptions
> **Resolved 2026-10-02:** the figure is created with `matplotlib.figure.Figure` and saved with `fig.savefig`, so it is never registered in pyplot's global state and is freed even when rendering raises. `ortho.py` no longer imports pyplot at all, which also removes the hidden "current figure" dependency.

`ortho.py:386-467`

`plt.subplots` creates a pyplot-managed 20×20 in figure. Any exception before `plt.close(fig)` (#8, #11, #12) leaves it registered in pyplot's global state. In programmatic or batch use (the README advertises `from ortho import generate_orthographic_map`), memory grows with every failure, and matplotlib eventually warns about >20 open figures.
**Fix:** wrap the body in `try/finally: plt.close(fig)`, or use `matplotlib.figure.Figure` directly and avoid pyplot. Also use `fig.savefig` instead of `plt.savefig` at line 466, since it depends on hidden global "current figure" state. (The `plt.gcf()` call was removed with the attribution fix.)

### 22. Tile download failures are not handled **[verified]**
> **Resolved 2026-10-02:** the full picture was worse than first described. Cartopy's `GoogleWTS.image_for_domain` silently drops any tile whose fetch raises `OSError` (which includes `URLError`, timeouts and resets), and `_merge_tiles` fills the hole with opaque white (`np.zeros(...) - 1`); if every tile fails it raises `ValueError` inside `savefig`. Tiles that fail inside `get_image` come back as opaque grey placeholders. So a network outage gave a white or grey globe, or a crash, never the fallback map. `BufferedTileSource.image_for_domain` now fetches the tiles itself and makes each failed tile fully transparent, so the Natural Earth land/ocean fallback shows through. `CachedOSM` and `GoogleMapTiles` raise on failure via `tile_fetch.download_tile` instead of returning placeholders. After saving, a warning reports how many tiles failed. The `try/except` around `add_image`, which never fired, was removed. Verified live by refusing every tile, and half the tiles, via a closed local port: both maps saved with the fallback showing where tiles were missing. Exceptions that are not tile failures still leak the figure (#15).

`ortho.py:365-374`, `ortho.py:425`

`GeoAxes.add_image` only registers the tile source (`self.img_factories.append(...)`); cartopy's `SlippyImageArtist.draw` fetches the tiles when the figure is drawn, i.e. inside `plt.savefig`. So the `try/except` around `add_image` never sees a network error. Simulating a failure in `image_for_domain` raises `ConnectionError` from `ortho.py:425` (`plt.savefig`), no map is saved, and the figure leaks (#15). Separately, cartopy's `GoogleWTS.get_image` catches `HTTPError`/`URLError` per tile and substitutes a blank grey tile, so HTTP-level failures (quota, 403, timeouts) don't raise at all and instead produce grey patches with only a printed message. The README's "Graceful error handling for network tile fetch failures" and "the map will still be saved with fallback land/ocean features" are both false.
**Fix:** fetch eagerly so failures happen where they can be handled. For example, wrap the tile source so `image_for_domain` catches the error, logs it, and returns an empty image, or call `savefig` in a `try` and on a tile error remove the slippy-image artists and save again with only the fallback features.

---

## Low

### 16. Distance-circle chord artefact for large radii
> **Resolved 2026-10-02:** `_visible_runs` splits a ring into its visible stretches and each is drawn separately, so no chord can cross the hidden part. Note the scenario is mostly theoretical here: the circles are centred on the view centre, so a ring is either fully visible (< ~10,000 km) or fully hidden; only off-centre rings can straddle the horizon. The new test uses one (80°E ring on a 0°E map) and fails on the old code.

`ortho.py:547-561`

Non-finite (far-side) vertices are removed and the remaining points are joined with a single `plot`. For any radius past the visible limb (≈10 000 km), the gap becomes a straight chord across the globe. The default radii (2 500/5 000 km) are safe, but `radii_km` is a public parameter. Splitting the line into runs at the NaN gaps (or keeping NaNs, which matplotlib breaks lines at) fixes it. The label code (`argmax(y)`) also assumes at least one visible vertex near the top.

### 17. Cache scan does not exclude confidence rasters
> **Resolved 2026-10-02:** lookups match the exact file name `Beck_KG_V1_{period}_{resolution}.tif`, so `_conf_` rasters can no longer be picked; `period`/`resolution` are validated.

`koppen.py:203-205` vs `koppen.py:193`

The local-folder scan excludes `_conf_`, but the cache-dir scan does not. With `os.listdir` order arbitrary, a confidence raster (values 0–100) could be picked and rendered as climate classes. `_find_tif_in_zip` sorts by length for this reason, but it is never called.

### 18. Dead code and contradictory comments in `koppen.py`
> **Resolved 2026-10-02:** dead code, unused imports and contradictory comments went with #2; the legend docstring now describes the flat 15-column grid. The `print` progress line is kept deliberately: it redraws in place (`
`), which a log line can't do, and it now redraws at most 101 times.

- ~~Unused imports: `re`, `tempfile`, `Any`; `zipfile` is only used by the dead function below.~~ Resolved with #2.
- ~~`_find_tif_in_zip` (line 105) is never called.~~ Removed with #2.
- ~~Line 36 says *"V3 archive (Beck et al. 2023)"*; line 39 says *"Beck et al. (2018) V1"*.~~ Resolved with #6.
- ~~`ensure_koppen_data` duplicates `DEFAULT_RESOLUTION`/`DEFAULT_PERIOD` as literals in its signature.~~ Resolved with #2.
- `add_koppen_legend` docstring says the legend is *"organised by major climate group (A–E)"*, but `_group_letter`/`_group_name` are discarded and the result is a flat 15×2 grid.
- Mixing `print(...)` progress output with `logging` means `--quiet` style control is impossible.

### 19. Docs and docstrings that are false
| Claim | Reality |
|---|---|
| ~~`background_color`: "ocean / figure background"~~ | ~~only the OCEAN feature uses it~~ Resolved: docstring fixed and the no-op facecolor calls removed |
| ~~`configure_tile_cache`: "Cartopy already caches tiles internally"~~ | ~~False by default (#4)~~ Resolved with #4 |
| ~~README: circles labelled "at their northernmost point"~~ | ~~Labelled at max projected *y*~~ Resolved: README says "at the top of each circle as drawn" |
| ~~README: `requirements.txt` is a "lock file" / "pinned dependencies"~~ | ~~nothing is pinned~~ Resolved: README calls them minimum versions and lists the tested versions |
| ~~README: "unit test suite (24 tests)"~~ | ~~29 tests~~ Resolved: README no longer states a count |
| ~~README: "Requirements: Python 3.14"~~ | ~~`pyproject.toml` says `>=3.12`~~ Resolved: "Python 3.12 or newer (developed on 3.14)" |
| ~~README: Köppen "1 km"~~ | ~~0.083° (~10 km) (#6)~~ Resolved: README now states 0.083° (~10 km) |
| ~~`BufferedTileSource` docstring: "extra ring"~~ | ~~two tile widths~~ Resolved: docstring rewritten with #22 |
| ~~Integration test: "without hitting the network"~~ | ~~downloads Natural Earth on first use~~ Resolved 2026-10-04: with a network guard and an empty Cartopy data folder, 10 render tests failed by reaching `naturalearth.s3.amazonaws.com`. `tests/conftest.py` now blocks non-local connections, isolates Cartopy's data folder and swaps `cfeature.LAND/OCEAN` for in-memory stand-ins; `tests/test_offline.py` checks the guard itself |

### 20. Test-suite gaps
> **Resolved 2026-10-03** except where noted below. 138 tests now pass with plain `pytest`.

- ~~**No tests for `koppen.py` at all.**~~ Resolved: `tests/test_koppen.py` covers lookup order, validation, download/MD5/extraction, retries, cleanup and the colormap. Legend construction is now tested too.
- ~~Nothing covers #11 (alpha range) or #12 (`-o` dir).~~ Resolved: #3, #5, #11 and #12 are all covered now.
- ~~`test_image_for_domain_calls_inner` only checks that a call happened.~~ Replaced with tests of the real fetch/merge path (#22).
- ~~`test_spaces_in_provider` uses `zoom=5`.~~ Now uses zoom 3.
- ~~`test_default_when_none` mutates global `cartopy.config["data_dir"]` and never restores it, so later tests run with a modified global.~~ Resolved with #4: `configure_tile_cache` no longer mutates it, and a test asserts that.
- ~~The integration test stubs `add_image`.~~ `test_tile_failures_still_save_map` now renders through the real `add_image` → `BufferedTileSource` → cartopy draw path (only `get_image` is stubbed).
- `_draw_distance_circles` tests assert `len(ax.lines) == 2`. Kept deliberately: since #16 a fully visible ring must be exactly one closed line, so the count is now the behaviour under test.

### 21. Packaging / repo hygiene
> **Resolved 2026-10-03:** MIT `LICENSE` added (shipped in the wheel); `[tool.pytest.ini_options]` sets `testpaths` and `pythonpath`, so the `sys.path.insert` lines are gone from all test files; `.obsidian/` is ignored; `BufferedTileSource.__getattr__` guards `tile_source`.

- `license = "MIT"` but there is no `LICENSE` file.
- `pyproject.toml` has no `[tool.pytest.ini_options]`, so tests rely on `sys.path.insert` hacking (`tests/test_ortho.py:14`).
- `.obsidian/` is untracked and not ignored.
- `BufferedTileSource.__getattr__` recurses infinitely if `tile_source` is ever accessed before `__init__` (e.g. `copy.copy`, unpickling). Guard with `if name == "tile_source": raise AttributeError`.

### 23. Polar caps show the fallback colour **[verified]**
`ortho.py` fallback features (`cfeature.OCEAN` / `cfeature.LAND` with fixed colours)

Web Mercator tiles stop at ±85.05°, so the area around each pole visible on the globe has no tile imagery and shows the
Natural Earth fallback in its flat colours: light blue (`#a6d3e0`) over the Arctic Ocean, beige (`#f1efe6`) over Antarctica.
With OSM this is nearly invisible because the fallback colours match OSM's own. On satellite imagery it is an obvious disc:
visible in the Tokyo smoke render (North Pole) and in both the old and new `sample_sao_paulo.png` (South Pole).
**Possible fixes:** choose fallback colours per provider (e.g. deep blue ocean and white ice for `google_satellite`), or fill
the caps with the colour of the nearest tile row, or draw a polar ice overlay. Cosmetic only; the data is correct.

> **Won't fix (owner decision, 2026-10-04).** Prototyped on Google Satellite at zoom 3 (Moscow view for the North Pole,
> São Paulo for the South Pole) before deciding:
>
> - **Satellite palette** (navy ocean, ice-white land): still a flat disc, and too dark against Google's Arctic imagery.
> - **Ice caps** (white above 85°): plausible for Antarctica, wrong for the Arctic, where Google shows open water. A plain
>   lon/lat box also projects to a triangle on the globe; the polygon needs densified edges.
> - **Edge fill** (best): fill each cap from the tiles' outermost pixel row. Stretching the row as-is gives radial streaks;
>   fading it to the row's mean colour at the pole, blurring it slightly along the row, and starting the cap at 84.5°
>   *under* the tiles (zorder 0, so no seam of fallback colour shows at 85°) made it near-invisible at the South Pole and
>   left only faint rays at the North Pole. Works for every provider without per-provider colours.
>
> If revisited, the edge fill is the one to implement.

> **Mitigated (2026-10-08, `6f363d8`):** the opt-in `--ice` overlay (`ice.py`) draws polar ice opaquely above the tiles,
> which covers both discs. It answers the objection to the "ice caps" prototype above: instead of a white cap at 85°, it
> draws measured ice for the month each hemisphere's ice peaks. That is NSIDC Sea Ice Index v4 extent for March (Arctic) and
> September (Antarctic), plus Natural Earth polar land ice. In March the sea ice covers the North Pole, and Antarctica's
> ice sheet covers the South Pole. Verified on Google Satellite (Lisbon, both hemispheres) and OSM (North Pole view): no
> disc visible at either pole.
>
> Without `--ice` the default renders are unchanged and still show the discs, so the finding stays open as a won't-fix
> for that case. The edge fill above remains the option for default renders.

---

## Recommended order of work
1. ~~Delete or flag-gate the hard-coded route (#3) **before committing**.~~ Done in `05e937a`.
2. ~~Fix the build backend (#1)~~ (done in `dfb65d2`) and ~~`--city` casing (#5)~~ (done in `3635fb0`). These are one-line fixes for user-facing breakage.
3. ~~Correct the Köppen attribution (#6) and add tile attribution (#7).~~ Done; Google now uses the Map Tiles API with an API key.
4. ~~Make Köppen failure fast and non-fatal, with an atomic download (#2, #8, #9, #10).~~ Done (also #17).
5. ~~Make tile caching real (#4).~~ Done.
6. ~~Validate CLI inputs and create the `-o` parent directory (#11, #12), and add tests for each.~~ Done.
7. ~~Revisit the default DPI and regrid shapes (#13, #14), and add `try/finally` for figure cleanup (#15).~~ Done (also #16, #18, #20, #21).
8. ~~Make the test suite fully offline (#19).~~ Done.
9. ~~Finish the Google setup (#7: Maps API key, smoke test, re-render `sample_sao_paulo.png`).~~ Done 2026-10-04.
10. ~~Optional: polar-cap colours on satellite renders (#23).~~ Prototyped; left as is by owner decision. Covered since 2026-10-08 when `--ice` is used.
