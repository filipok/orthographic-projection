# Romanian data sources

Candidate sources of fine-grained data for Romania, for future map layers: censuses,
vital statistics, climate and the boundaries to map them on. Collected on 2026-10-22
from web searches. **None has been downloaded or checked yet**: the formats, table
codes and terms below are as the sources describe them and need confirming before a
layer is built on them.

## Census and population

| Source | Detail | Years | Access |
|---|---|---|---|
| **2021 census, final results** (INS) | Population by UAT. Ethnicity, religion and mother tongue by locality; other characteristics by county only | 2021 | Excel files on recensamantromania.ro and the county statistics offices' sites. About 2.5 million people (provisional results) have no ethnicity or religion recorded, because they were counted from administrative records |
| **Eurostat GEOSTAT census grid** | 1 km squares: population by sex, age, employment and place of birth (13 variables) | 2021 | Free GeoTIFF, GeoPackage, GeoParquet or CSV from Eurostat GISCO, in ETRS89-LAEA (EPSG:3035); download after accepting Eurostat's terms |
| **INS TEMPO Online** | Population by domicile (legal residence) for each commune, town and municipality, every year | 1990s to now | Free, crediting INS. No documented public API, but the R package `TEMPO` scripts its bulk downloads, so the request format can be read from its source |
| **1992 census** | INS published the locality tables only on paper. Varga E. Árpád's five-volume *Erdély etnikai és felekezeti statisztikája, 1850–1992* gives every Transylvanian locality's ethnicity and religion | 1850–1992 | Print only (Teleki László Foundation and Pro-Print, 1998–2002). Some of Varga's work is on mek.oszk.hu as PDF; no spreadsheet found |
| **IPUMS International** | Census microdata samples (individual records), coded by county only | 1977, 1992, 2002, 2011 | Free after registering. The 1977 sample lacks Alba and Arad |
| **1930 census** | Volume II lists every town and village by ethnicity and native language; other volumes cover literacy, birthplace, age and citizenship | 1930 | Scanned PDFs with OCR text (Internet Archive, BCU Cluj). No tables and no GIS: the figures would need extracting and matching to the interwar county and *plasă* boundaries, which were not found digitised |

## Vital statistics (INS TEMPO)

- **POP201D**: live births by county and locality.
- **POP208C, POP208G**: infant deaths by county and locality.
- **POP206G** (deaths) and **POP214A/B** (natural increase): only down to county level in the table list found.

Natural increase by commune would need POP201D paired with a locality-level deaths
table, if one exists. Table codes not yet checked against the live database.

## Climate and weather

| Source | Detail | Years | Access |
|---|---|---|---|
| **CarpatClim** | Daily grid at 0.1° (about 10 km) over the Carpathian region, Romania included. Downloadable: mean, minimum and maximum temperature and precipitation. The project also produced wind, sunshine, humidity and pressure, plus 37 derived indices | 1961–2010 | Free gzipped NetCDF, crediting "CARPATCLIM Database © European Commission – JRC, 2013" and citing the project's papers |
| **ROCADA** (ANM, Romania's national weather service) | Daily grid at 0.1° for nine variables, Romania only, homogenised (MASH) and gridded (MISH) | 1961–2013 | Listed on PANGAEA (doi:10.1594/PANGAEA.833627) but described as available on request; one mirror lists no files |
| **E-OBS** | Daily grid at 0.1° or 0.25° over Europe: temperature, precipitation, sea-level pressure, radiation, wind (from 1980) | 1950–2024 (v31.0e) | Free from Copernicus, crediting E-OBS and the ECA&D data providers |
| **ANM station data** | About 160 synoptic and 100 automatic stations; climate records from 1961 at 260 stations | 1961– | Historically sold under contract (per a 2015 assessment); current terms not confirmed |

## Boundaries

- **UAT polygons** (every commune, town and municipality) from geo-spatial.org, under
  "România - seturi de date vectoriale generale > Limitele unităților administrative
  din România". The GitHub repo akaleeroy/romania-uat links a 2019 GeoJSON of them
  (~189 MB) and the mapshaper commands to split and simplify it. Census and TEMPO
  tables join to them by SIRUTA code, the official numeric ID of each UAT; whether
  the attribute table carries it, and the licence, are to be confirmed.

## What would fit this project

1. **A per-UAT layer**, working like the Eurostat layer in `indicators.py`. It would
   shade communes by any TEMPO or 2021-census table: ethnicity, religion,
   population change, births. The most new data for the least work.
2. **The 1 km GEOSTAT grid** as a raster layer, like `population.py`, for finer
   population maps than the NASA grid gives.
3. **CarpatClim** as a climate source for Romanian maps. It is finer than what
   `climate.py` uses now, and its indices (frost days, droughts) suit school-atlas
   questions.
4. **The 1930 census**: a data-entry project (extract Volume II from the OCR,
   rebuild the interwar boundaries). Worth it for a historical atlas, but the
   largest of these.

## Links

- CarpatClim: [data](https://surfobs.climate.copernicus.eu/dataaccess/access_carpatclim.php),
  [indices](https://surfobs.climate.copernicus.eu/dataaccess/access_carpatclim_indices.php),
  [description (OMSZ)](https://odp.met.hu/climate/homogenized_data/gridded_data_series/Description_CarpatClim_gridded_data.pdf)
- ROCADA: [PANGAEA](https://doi.pangaea.de/10.1594/PANGAEA.833627),
  [paper](https://link.springer.com/doi/10.1007/s11069-015-1757-z)
- E-OBS: [daily data](https://surfobs.climate.copernicus.eu/dataaccess/access_eobs_daily.php),
  [on the Climate Data Store](https://forum.ecmwf.int/t/e-obs-daily-gridded-meteorological-data-for-europe-from-1950-to-present-derived-from-in-situ-observations-now-available-on-the-cds/885)
- Eurostat: [GEOSTAT](https://ec.europa.eu/eurostat/en/web/gisco/geodata/population-distribution/geostat),
  [population grids](https://ec.europa.eu/eurostat/en/web/gisco/geodata/population-distribution/population-grids)
- INS: [TEMPO table list](https://statistici.insse.ro/shop/?context=11&lang=en&page=tempo2),
  [population by domicile to locality level](https://insse.ro/cms/sites/default/files/com_presa/com_pdf/popdom1ian2021r.pdf),
  [R package TEMPO](https://cran.r-universe.dev/TEMPO/doc/using-tempo.html)
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
- Boundaries: [romania-uat](https://github.com/akaleeroy/romania-uat)
- ANM: [Open Knowledge Index, 2015](https://2015.index.okfn.org/place/romania/weather),
  [station network (UNFCCC, 2008)](https://unfccc.int/files/adaptation/sbsta_agenda_item_adaptation/application/pdf/20080306_romania_data.pdf)
