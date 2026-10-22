# Romanian data sources

Sources of fine-grained data for Romania, for future map layers: censuses, vital
statistics, climate and the boundaries to map them on, with what the project already
uses and a plan. Collected on 2026-10-22 from web searches; the entries marked
**checked** were queried or downloaded that day, the others are as the sources
describe them and need confirming before a layer is built on them.

## Already in the project

| Layer | Source | Notes |
|---|---|---|
| `--relief-units` ([relief.py](relief.py)) | geo-spatial.org relief map after Posea & Badea 1984 (Candrea et al. 2008), CC BY-SA 3.0 | 820 units, names restored from the legend |
| `--indicator eurostat:…` ([indicators.py](indicators.py)) | Eurostat, on GISCO's NUTS boundaries at 1:10 million | NUTS 3 in Romania is the 41 counties and Bucharest, so county statistics that Eurostat holds can already be mapped |
| `--cities` ([cities.py](cities.py)) | GHSL urban centres (JRC) | Towns of over 50,000 |
| `--population` ([population.py](population.py)) | GPWv4 via NASA GIBS | **Wrong for Călărași county** (below); draw Romania for 2010 |

### GPWv4 and Călărași county (checked)

GPWv4 carries census counts to its other years by the growth between censuses.
Călărași county's input is wrong, and the error grows away from 2010. Densities read
from the GIBS tiles at a rural point near Lehliu-Gară, in people per km²:

| 2000 | 2005 | 2010 | 2015 | 2020 |
|---|---|---|---|---|
| 1 | 7.5 | 75 | 475 | 1,000+ |

The neighbouring counties (Ialomița, Giurgiu, Teleorman) only thin out gradually,
as Romania's population fell, and the colour scale is the same in every year, so the
fault is in the data, not in reading the tiles. The real density is about 50. The school atlas draws Romania and Europe for
2010; official figures by UAT (below) would remove the problem for Romania.

## Census and population

| Source | Detail | Years | Access |
|---|---|---|---|
| **INS TEMPO Online** (checked) | Population by domicile (legal residence) for each of the 3,181 communes, towns and municipalities: **POP107D** (1 January, 1992–2026) and **POP108D** (1 July, 1992–2025), by age and sex | 1992 to now | Undocumented but open JSON API, no key (see *Using TEMPO* below). Each locality is labelled with its SIRUTA code, so the tables join to UAT boundaries by code |
| **2021 census, final results** (INS) | Population by UAT. Ethnicity, religion and mother tongue by locality; other characteristics by county only | 2021 | Excel files on recensamantromania.ro and the county statistics offices' sites. About 2.5 million people (provisional results) have no ethnicity or religion recorded, because they were counted from administrative records |
| **Eurostat GEOSTAT census grid** | 1 km squares: population by sex, age, employment and place of birth (13 variables) | 2021 | Free GeoTIFF, GeoPackage, GeoParquet or CSV from Eurostat GISCO, in ETRS89-LAEA (EPSG:3035); download after accepting Eurostat's terms |
| **1992 census** | INS published the locality tables only on paper. Varga E. Árpád's five-volume *Erdély etnikai és felekezeti statisztikája, 1850–1992* gives every Transylvanian locality's ethnicity and religion | 1850–1992 | Print only (Teleki László Foundation and Pro-Print, 1998–2002). Some of Varga's work is on mek.oszk.hu as PDF; no spreadsheet found |
| **IPUMS International** | Census microdata samples (individual records), coded by county only | 1977, 1992, 2002, 2011 | Free after registering. The 1977 sample lacks Alba and Arad |
| **1930 census** | Volume II lists every town and village by ethnicity and native language; other volumes cover literacy, birthplace, age and citizenship | 1930 | Scanned PDFs with OCR text (Internet Archive, BCU Cluj). No tables and no GIS: the figures would need extracting and matching to the interwar county and *plasă* boundaries, which were not found digitised |

## Vital statistics (INS TEMPO, checked)

| Table | Content | Down to | Years |
|---|---|---|---|
| **POP201D** | Live births | locality | 1990–2025 |
| **POP206D** | Deaths | locality | 1990–2025 |
| **POP208C** | Infant deaths | locality | 1990–2025 |
| **POP208G** | Infant deaths, usual residents | locality | 2012–2025 |
| POP206G | Deaths of usual residents by sex, area and age | country | 2012–2025 |
| POP206C | Deaths by cause | county | 1990–2025 |
| POP214A, POP214B | Natural increase | county | 1990–2025, 2012–2025 |

Natural increase by commune is POP201D minus POP206D. All the locality tables carry
SIRUTA codes.

## Climate and weather

| Source | Detail | Years | Access |
|---|---|---|---|
| **CarpatClim** | Daily grid at 0.1° (about 10 km) over the Carpathian region, Romania included. Downloadable: mean, minimum and maximum temperature and precipitation. The project also produced wind, sunshine, humidity and pressure, plus 37 derived indices | 1961–2010 | Free gzipped NetCDF, crediting "CARPATCLIM Database © European Commission – JRC, 2013" and citing the project's papers |
| **ROCADA** (ANM, Romania's national weather service) | Daily grid at 0.1° for nine variables, Romania only, homogenised (MASH) and gridded (MISH) | 1961–2013 | Listed on PANGAEA (doi:10.1594/PANGAEA.833627) but described as available on request; one mirror lists no files |
| **E-OBS** | Daily grid at 0.1° or 0.25° over Europe: temperature, precipitation, sea-level pressure, radiation, wind (from 1980) | 1950–2024 (v31.0e) | Free from Copernicus, crediting E-OBS and the ECA&D data providers |
| **ANM station data** | About 160 synoptic and 100 automatic stations; climate records from 1961 at 260 stations | 1961– | Historically sold under contract (per a 2015 assessment); current terms not confirmed |

## Boundaries

| Source | Detail | Access |
|---|---|---|
| **GISCO LAU 2024** (checked) | All 3,181 UATs at 1:1 million. `GISCO_ID` is `RO_` and the SIRUTA code (`RO_1017` is Municipiul Alba Iulia), with the 2024 population and area | Europe-wide only: GeoPackage 78 MB, GeoJSON, CSV of the attributes (5.6 MB) or Parquet, in EPSG:4326, 3857 or 3035; LAU years 2011–2024. © EuroGeographics for the administrative boundaries, as for the NUTS regions already used |
| **geo-spatial.org UAT polygons** (download checked) | `ro_uat_poligon.geojson` of 2019-12-20, 200 MB, from [geo-spatial.org/vechi/file_download/29535](https://geo-spatial.org/vechi/file_download/29535) (the old `www` address gives 404). Its fields include `natcode`, likely the SIRUTA code, and `countyMn`. More detailed than GISCO's 1:1 million | Licence not confirmed. The GitHub repo akaleeroy/romania-uat shows how to split and simplify it with mapshaper; the repo itself has no licence |

Counties are the UATs merged by county, so county and UAT borders match exactly. In
TEMPO every locality records its county (`parentId`).

## Using TEMPO (checked)

- `GET http://statistici.insse.ro:8077/tempo-ins/matrix/{CODE}` returns a table's
  description: its dimensions, each with its options (`label`, `nomItemId`,
  `parentId`), and flags such as `nomLoc` (has a localities dimension) and
  `matSiruta`, plus the date it was last updated.
- `POST http://statistici.insse.ro:8077/tempo-ins/pivot` with
  `{"language": "ro", "encQuery": …, "matCode": CODE, "matMaxDim": …, "matUMSpec": …, "matRegJ": …}`
  returns the figures as CSV. `encQuery` lists the chosen `nomItemId`s, joined by
  commas within a dimension and colons between dimensions, in order.
- A query for Călărași county's 55 localities in 2024 (POP107D, all ages, both sexes)
  came back whole, e.g. `93888 ORAS LEHLIU-GARA, Anul 2024, 6213`. Queries have a
  size limit, so a national table is best asked for county by county and cached.
- Names have no diacritics (`CALARASI`); the UAT names should come from the
  boundaries instead.
- INS's reuse terms are not confirmed yet ("free, crediting INS" is as described).

## Plan

1. **Counties and UATs** (`--ro-admin counties|uats`): GISCO LAU 2024 for Romania,
   downloaded once and cached compactly as the relief layer is, counties merged from
   the UATs. Outlines and names: counties always, municipalities and towns where they
   fit. Works over any other layer.
2. **TEMPO in `--indicator`** (`tempo:CODE?dimension=value`), like `eurostat:`, by UAT
   or county, joined by SIRUTA code, defaulting to the totals and the latest year.
   Presets first: population (POP107D), density (with GISCO's area), population
   change (e.g. 2011–2024), natural increase (POP201D − POP206D) and births per
   1,000. The key, classes and credits already exist for Eurostat, and the web app and
   atlases get it with them. The school atlas's density page would then use official
   figures instead of GPWv4.
3. **The 2021 census by UAT**: ethnicity, religion and mother tongue by locality
   from INS's Excel files.
4. **The 1 km GEOSTAT grid** as a raster layer, like `population.py`, for finer
   population maps than the NASA grid gives.
5. **CarpatClim** as a climate source for Romanian maps. It is finer than what
   `climate.py` uses now, and its indices (frost days, droughts) suit school-atlas
   questions.
6. **The 1930 census**: a data-entry project (extract Volume II from the OCR,
   rebuild the interwar boundaries). Worth it for a historical atlas, but the
   largest of these.

To confirm before step 1 or 2: INS's reuse terms, and whether the geo-spatial.org
polygons' licence allows them in place of GISCO's for close-ups.

### Other open work

- **Atlas cache**: a new map option changes the hash of every map, so adding
  `--rectangle` made the next build redraw all 50. The hash could leave out options
  at their defaults.
- **Atlas size**: the school atlas is 135 MB; a build with JPEG-compressed pages
  would fit an e-mail (25 MB).
- **GIBS tiles** are not retried: a tile that answered HTTP 500 left a gap in the
  world population map.

## Links

- INS: [TEMPO table list](https://statistici.insse.ro/shop/?context=11&lang=en&page=tempo2),
  [TEMPO API, table description (POP107D)](http://statistici.insse.ro:8077/tempo-ins/matrix/POP107D),
  [population by domicile to locality level](https://insse.ro/cms/sites/default/files/com_presa/com_pdf/popdom1ian2021r.pdf),
  [R package TEMPO](https://cran.r-universe.dev/TEMPO/doc/using-tempo.html)
- GISCO: [LAU datasets](https://gisco-services.ec.europa.eu/distribution/v2/lau/datasets.json),
  [LAU 2024 files](https://gisco-services.ec.europa.eu/distribution/v2/lau/lau-2024-files.json)
- Boundaries: [geo-spatial.org UAT polygons](https://geo-spatial.org/vechi/file_download/29535),
  [romania-uat](https://github.com/akaleeroy/romania-uat)
- CarpatClim: [data](https://surfobs.climate.copernicus.eu/dataaccess/access_carpatclim.php),
  [indices](https://surfobs.climate.copernicus.eu/dataaccess/access_carpatclim_indices.php),
  [description (OMSZ)](https://odp.met.hu/climate/homogenized_data/gridded_data_series/Description_CarpatClim_gridded_data.pdf)
- ROCADA: [PANGAEA](https://doi.pangaea.de/10.1594/PANGAEA.833627),
  [paper](https://link.springer.com/doi/10.1007/s11069-015-1757-z)
- E-OBS: [daily data](https://surfobs.climate.copernicus.eu/dataaccess/access_eobs_daily.php),
  [on the Climate Data Store](https://forum.ecmwf.int/t/e-obs-daily-gridded-meteorological-data-for-europe-from-1950-to-present-derived-from-in-situ-observations-now-available-on-the-cds/885)
- Eurostat: [GEOSTAT](https://ec.europa.eu/eurostat/en/web/gisco/geodata/population-distribution/geostat),
  [population grids](https://ec.europa.eu/eurostat/en/web/gisco/geodata/population-distribution/population-grids)
- 2021 census: [Constanța release on the final data](https://constanta.insse.ro/wp-content/uploads/2023/07/comunicatDJSCT_nr10_2023_dateDefinitiveRPL2021_iul-2023_Constanta.pdf),
  [Sălaj release](https://salaj.insse.ro/wp-content/uploads/2023/06/SALAJ-CP-judet_date-definitiveRPL2021-1.pdf)
- 1930 census: [Vol. 2 pt. 1](https://archive.org/details/recensamntulgene02inst),
  [Vol. 1](https://archive.org/details/recensamantul-romaniei-din-1930-vol.-1),
  [Vol. 2](https://archive.org/details/recensamantul-romaniei-din-1930-vol.-2),
  [Vol. 3](https://archive.org/details/recensamantul-romaniei-din-1930-vol.-3),
  [Vol. 6](https://archive.org/details/recensamantul-romaniei-din-1930-vol.-6)
  (Internet Archive), [Vol. 3 at BCU Cluj](https://dspace.bcucluj.ro/handle/123456789/176865),
  [INS library census catalogue](https://www.recensamantromania.ro/wp-content/uploads/2021/11/recensamintele_in_arhiva_bibliotecii_ins.pdf),
  [Bukovina 1930 with coordinates](http://czernowitz.blogspot.com/p/census-of-romania-for-year-1930_6.html)
- Varga E. Árpád: [censuses of present-day Transylvania](https://kisebbsegkutato.tk.hu/uploads/files/olvasoszoba/regiokonyvek/nepszamlalasok_a_jelenkori_erdely_teruleten.pdf),
  [articles on MEK](https://www.mek.oszk.hu/00900/00983/pdf/censual.pdf)
- IPUMS: [1992 sample (World Bank Microdata)](https://microdata.worldbank.org/catalog/548/data-dictionary),
  [county variable GEO1_RO](https://microdata.worldbank.org/catalog/7070/variable/GEO1_RO)
- ANM: [Open Knowledge Index, 2015](https://2015.index.okfn.org/place/romania/weather),
  [station network (UNFCCC, 2008)](https://unfccc.int/files/adaptation/sbsta_agenda_item_adaptation/application/pdf/20080306_romania_data.pdf)
