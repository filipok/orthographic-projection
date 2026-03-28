# Orthographic Map Generator

This project generates high-resolution orthographic globe images centered on a selected major city using Cartopy, Matplotlib, and online web map tiles.

The main script is [ortho.py](/C:/Users/fgadi/PyCharmMiscProject/ortho.py). A sample generated output is `orthographic_map_paris_osm_z5.png`.

## Features

- Interactive city selection from a built-in list of major metropolitan areas
- Orthographic globe projection centered on the chosen city
- Support for multiple tile providers
- High-resolution PNG export with transparent background
- Buffered tile fetching to reduce missing imagery near the edge of the globe

## Supported Cities

The script currently includes:

- NYC
- DC
- Moscow
- Shanghai
- Singapore
- London
- Paris
- Berlin
- Ankara
- Tehran
- New Delhi
- Jerusalem

## Supported Tile Providers

The interactive menu exposes these providers:

- `osm`
- `google`
- `google_satellite`

## Requirements

- Python 3.14
- Internet access for downloading map tiles

Packages present in the local virtual environment include:

- `cartopy==0.25.0`
- `matplotlib==3.10.8`
- `numpy==2.4.3`
- `pyproj==3.7.2`
- `shapely==2.1.2`
- `scipy==1.17.1`
- `pillow==12.1.1`

## Setup

If you want to recreate the environment from scratch:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install cartopy matplotlib numpy pyproj shapely scipy pillow
```

## Usage

Run the script:

```powershell
python ortho.py
```

You will be prompted to choose:

1. A city
2. A tile provider
3. A zoom level

The script then writes a PNG named like:

```text
orthographic_map_<city>_<provider>_z<zoom>.png
```

Example:

```text
orthographic_map_paris_osm_z5.png
```

## Programmatic Use

You can also import the generator directly:

```python
from ortho import generate_orthographic_map

generate_orthographic_map(
    lat=48.8566,
    lon=2.3522,
    output_filename="orthographic_map_paris_osm_z3.png",
    tile_provider="osm",
    zoom=3,
    dpi=600,
)
```

## Notes

- Lower zoom levels are safer for full-globe renders. The script recommends keeping zoom roughly between `2` and `4` to avoid excessive tile downloads.
- Output uses `bbox_inches="tight"` and `transparent=True`, so the resulting PNG has minimal padding around the globe.
- Google tile backends depend on Cartopy tile services and may be subject to provider availability or usage limits.

## Project Files

- [ortho.py](/C:/Users/fgadi/PyCharmMiscProject/ortho.py): main script and reusable map-generation functions
- `orthographic_map_paris_osm_z5.png`: sample rendered output
