from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import numpy as np

import cartopy
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
import cartopy.feature as cfeature
import matplotlib.patheffects as pe
from cartopy.geodesic import Geodesic
from cartopy.mpl.geoaxes import GeoAxes
from shapely.geometry import Polygon

from koppen import add_koppen_overlay, add_koppen_legend

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tile cache configuration
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles")


def configure_tile_cache(cache_dir: str | None = None) -> str:
    """Point Cartopy's download cache at *cache_dir* (created if needed).

    Cartopy already caches tiles internally, but its default location is
    buried under ``~/.local/share/cartopy``.  This helper lets users and
    the CLI control where that cache lives.
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    cartopy.config["data_dir"] = cache_dir
    logger.debug("Tile cache directory: %s", cache_dir)
    return cache_dir


MAJOR_METROPOLISES = {
    "NYC": {"lat": 40.7128, "lon": -74.0060, "slug": "nyc"},
    "Moscow": {"lat": 55.7558, "lon": 37.6173, "slug": "moscow"},
    "Shanghai": {"lat": 31.2304, "lon": 121.4737, "slug": "shanghai"},
    "London": {"lat": 51.5074, "lon": -0.1278, "slug": "london"},
    "Paris": {"lat": 48.8566, "lon": 2.3522, "slug": "paris"},
    "Berlin": {"lat": 52.5200, "lon": 13.4050, "slug": "berlin"},
    "Ankara": {"lat": 39.9334, "lon": 32.8597, "slug": "ankara"},
    "New Delhi": {"lat": 28.6139, "lon": 77.2090, "slug": "new_delhi"},
    "Tokyo": {"lat": 35.6762, "lon": 139.6503, "slug": "tokyo"},
    "Jakarta": {"lat": -6.2088, "lon": 106.8456, "slug": "jakarta"},
    "Manila": {"lat": 14.5995, "lon": 120.9842, "slug": "manila"},
    "Sao Paulo": {"lat": -23.5505, "lon": -46.6333, "slug": "sao_paulo"},
    "Lagos": {"lat": 6.5244, "lon": 3.3792, "slug": "lagos"},
    "Johannesburg": {"lat": -26.2041, "lon": 28.0473, "slug": "johannesburg"},
    "Sydney": {"lat": -33.8688, "lon": 151.2093, "slug": "sydney"},
    "Lisbon": {"lat": 38.7223, "lon": -9.1393, "slug": "lisbon"},
    "Honolulu": {"lat": 21.3069, "lon": -157.8583, "slug": "honolulu"},
    "Papeete": {"lat": -17.5516, "lon": -149.5585, "slug": "papeete"},
    "San Francisco": {"lat": 37.7749, "lon": -122.4194, "slug": "san_francisco"},
}

TILE_PROVIDERS = [
    "osm",
    "google",
    "google_satellite"
]


class BufferedTileSource:
    """Fetch an extra ring of Web Mercator tiles to avoid edge underfill."""

    def __init__(self, tile_source: Any, tile_buffer_factor: float = 0.5) -> None:
        self.tile_source = tile_source
        self.tile_buffer_factor = tile_buffer_factor
        self.crs = tile_source.crs

    def __getattr__(self, name: str) -> Any:
        return getattr(self.tile_source, name)

    def image_for_domain(self, target_domain: Any, target_z: int) -> Any:
        x0, x1 = self.crs.x_limits
        world_width = x1 - x0
        tile_width = world_width / (2 ** target_z)
        buffered_domain = target_domain.buffer(tile_width * self.tile_buffer_factor)
        return self.tile_source.image_for_domain(buffered_domain, target_z)


def create_tile_source(
    tile_provider: str = "osm",
    tile_buffer_factor: float = 2,
    **tile_kwargs: Any,
) -> BufferedTileSource:
    """Create a Cartopy tile source from a simple provider name."""
    provider = tile_provider.lower()

    if provider == "osm":
        tile_source = cimgt.OSM(**tile_kwargs)
    elif provider == "google":
        tile_source = cimgt.GoogleTiles(style="street", **tile_kwargs)
    elif provider == "google_satellite":
        tile_source = cimgt.GoogleTiles(style="satellite", **tile_kwargs)
    else:
        raise ValueError(
            "Unsupported tile_provider. Choose one of: "
            "osm, google, google_satellite."
        )

    logger.debug("Created tile source: %s (buffer_factor=%.1f)", provider, tile_buffer_factor)
    return BufferedTileSource(tile_source, tile_buffer_factor=tile_buffer_factor)


def generate_orthographic_map(
    lat: float,
    lon: float,
    output_filename: str,
    zoom: int = 3,
    dpi: int = 300,
    background_color: str = "#a6d3e0",
    tile_provider: str = "osm",
    tile_kwargs: dict[str, Any] | None = None,
    tile_buffer_factor: float = 2,
    max_regrid_shape: int = 4096,
    output_dir: str | None = None,
    city_name: str | None = None,
    koppen: bool = False,
    koppen_alpha: float = 0.45,
) -> str:
    """
    Generate an orthographic map projection centered at a specific point.

    Parameters
    ----------
    lat : float
        Central latitude (e.g. 40.7128 for New York).
    lon : float
        Central longitude (e.g. -74.0060 for New York).
    output_filename : str
        Name/path of the output PNG file.
    zoom : int
        Tile zoom level (1–4). Higher values fetch exponentially more tiles.
    dpi : int
        Dots per inch for the output image. 300+ is high resolution.
    background_color : str
        Hex colour for ocean / figure background.
    tile_provider : str
        One of ``"osm"``, ``"google"``, ``"google_satellite"``.
    tile_kwargs : dict, optional
        Extra keyword arguments forwarded to the Cartopy tile constructor.
    tile_buffer_factor : float
        How aggressively to over-fetch tiles near the globe edge.
    max_regrid_shape : int
        Upper bound on the re-gridding resolution (pixels).
    output_dir : str or None
        Optional directory to save the output file into. Created if needed.
    city_name : str or None
        If provided, a marker and label are drawn at the centre point.
    koppen : bool
        When True, render a Köppen-Geiger climate classification overlay.
    koppen_alpha : float
        Opacity of the Köppen-Geiger overlay (0–1).

    Returns
    -------
    str
        Absolute path of the saved PNG.
    """

    # Resolve output path
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        output_filename = os.path.join(output_dir, output_filename)

    logger.info("Setting up the map centred at lat=%.4f, lon=%.4f", lat, lon)

    # Step 1: Initialize the requested image tile source
    tile_kwargs = tile_kwargs or {}
    tiles = create_tile_source(
        tile_provider=tile_provider,
        tile_buffer_factor=tile_buffer_factor,
        **tile_kwargs,
    )

    # Step 2: Define the Orthographic projection
    ortho_proj = ccrs.Orthographic(central_longitude=lon, central_latitude=lat)

    # Step 3: Create a high-resolution figure and axes
    fig, ax = plt.subplots(figsize=(20, 20), subplot_kw={"projection": ortho_proj})
    fig.patch.set_facecolor(background_color)

    if not isinstance(ax, GeoAxes):
        raise RuntimeError("Failed to create GeoAxes")

    ax.set_facecolor(background_color)

    # Step 4: Full hemisphere view
    ax.set_global()

    # Dynamic regrid_shape for sharp exports
    regrid_shape = min(
        max(750, int(min(fig.get_size_inches()) * dpi)),
        max_regrid_shape,
    )

    # Fallback land/ocean so blank areas aren't white
    ax.add_feature(cfeature.OCEAN, facecolor=background_color, edgecolor="none", zorder=0)
    ax.add_feature(cfeature.LAND, facecolor="#f1efe6", edgecolor="none", zorder=0)

    # Step 5: Fetch and add tiles
    logger.info("Fetching '%s' tiles at zoom level %d …", tile_provider, zoom)
    try:
        ax.add_image(
            tiles,
            zoom,
            regrid_shape=regrid_shape,
            interpolation="nearest",
        )
    except Exception as e:
        logger.warning("Failed to fetch map tiles: %s", e)
        logger.warning("The map will be saved with fallback land/ocean features only.")

    # Step 5b: Köppen-Geiger overlay (above tiles, below gridlines)
    if koppen:
        logger.info("Applying Köppen-Geiger climate overlay (alpha=%.2f) …", koppen_alpha)
        add_koppen_overlay(ax, alpha=koppen_alpha)
        add_koppen_legend(plt.gcf(), ax)

    # Step 6: Gridlines
    ax.gridlines(draw_labels=False, color='black', alpha=0.3, linestyle='--')

    # Step 7: City marker & label
    if city_name:
        ax.plot(
            lon, lat,
            marker="o", markersize=10, markeredgewidth=2,
            color="#e74c3c", markeredgecolor="white",
            transform=ccrs.PlateCarree(), zorder=10,
        )
        ax.text(
            lon, lat, f"  {city_name}",
            transform=ccrs.PlateCarree(),
            fontsize=14, fontweight="bold", color="white",
            va="center", ha="left", zorder=10,
            path_effects=[
                pe.withStroke(linewidth=3, foreground="black")
            ],
        )

    # Step 7b: Concentric distance circles (2 500 km and 5 000 km)
    _draw_distance_circles(ax, lon, lat)

    # Step 8: Export
    logger.info("Saving high-resolution map to '%s' at %d DPI …", output_filename, dpi)
    plt.savefig(output_filename, dpi=dpi, bbox_inches="tight", transparent=True)
    plt.close(fig)

    output_path = os.path.abspath(output_filename)
    logger.info("Map successfully created: %s", output_path)
    return output_path



def _geodesic_circle(lon: float, lat: float, radius_m: float, n_points: int = 180) -> Polygon:
    """Return a Shapely Polygon tracing a geodesic circle on WGS-84.

    Parameters
    ----------
    lon, lat : float
        Centre of the circle in degrees.
    radius_m : float
        Radius in **metres**.
    n_points : int
        Number of vertices (more = smoother).
    """
    geod = Geodesic()
    coords = geod.circle(lon=lon, lat=lat, radius=radius_m, n_samples=n_points, endpoint=False)
    return Polygon(coords)


def _draw_distance_circles(
    ax: GeoAxes,
    lon: float,
    lat: float,
    radii_km: tuple[float, ...] = (2_500, 5_000),
) -> None:
    """Draw concentric geodesic circles on *ax* at the given radii.

    A ``PlateCarree`` CRS centred on *lon* is used so that geodesic arcs
    crossing the antimeridian (±180°) don't produce degenerate polygons.
    """
    # Centre the source CRS on the circle origin so the polygon never
    # straddles the ±180° boundary of the coordinate system.
    source_crs = ccrs.PlateCarree(central_longitude=lon)
    colors = ["#ffffff", "#ffffff"]
    alphas = [0.7, 0.5]

    for idx, radius_km in enumerate(radii_km):
        circle_poly = _geodesic_circle(lon, lat, radius_km * 1_000)

        # Re-centre longitudes relative to *lon* so they stay in [-180, 180]
        # within the shifted CRS and never straddle its boundary.
        ring = np.array(circle_poly.exterior.coords)
        ring[:, 0] = ((ring[:, 0] - lon + 180) % 360) - 180
        shifted_poly = Polygon(ring)

        ax.add_geometries(
            [shifted_poly],
            crs=source_crs,
            facecolor="none",
            edgecolor=colors[idx % len(colors)],
            linewidth=1.4,
            linestyle="--",
            alpha=alphas[idx % len(alphas)],
            zorder=9,
        )
        # Place a small label on the circle (at the top, i.e. northward)
        # Pick the point closest to due-north (max latitude)
        top_idx = int(np.argmax(ring[:, 1]))
        label_x, label_lat = ring[top_idx]
        ax.text(
            label_x, label_lat, f" {int(radius_km):,} km",
            transform=source_crs,
            fontsize=9, color="white", alpha=alphas[idx % len(alphas)],
            fontweight="bold", va="bottom", ha="center", zorder=10,
            path_effects=[pe.withStroke(linewidth=2, foreground="black")],
        )


def prompt_for_selection(prompt_text: str, options: list[str]) -> str:
    """Prompt the user to choose one option from a numbered list."""
    while True:
        print(prompt_text)
        for index, option in enumerate(options, start=1):
            print(f"  {index}. {option}")

        choice = input("Enter the number of your choice: ").strip()
        if not choice.isdigit():
            print("Please enter a valid number.\n")
            continue

        selected_index = int(choice) - 1
        if 0 <= selected_index < len(options):
            return options[selected_index]

        print("Choice out of range. Try again.\n")


def prompt_for_zoom(default_zoom: int = 3, min_zoom: int = 1, max_zoom: int = 4) -> int:
    """Prompt the user for a zoom level within a safe range."""
    while True:
        raw_zoom = input(
            f"Enter zoom level ({min_zoom}-{max_zoom}) [default: {default_zoom}]: "
        ).strip()

        if not raw_zoom:
            return default_zoom

        if raw_zoom.isdigit():
            zoom = int(raw_zoom)
            if min_zoom <= zoom <= max_zoom:
                return zoom

        print(f"Please enter an integer between {min_zoom} and {max_zoom}.\n")


def build_output_filename(city_slug: str, tile_provider: str, zoom: int) -> str:
    """Build a descriptive output filename from the render parameters."""
    sanitized_provider = tile_provider.replace(" ", "_")
    return f"orthographic_map_{city_slug}_{sanitized_provider}_z{zoom}.png"


# --- Custom coordinate prompt ---


def prompt_for_coordinates() -> tuple[float, float]:
    """Prompt the user for custom latitude and longitude."""
    while True:
        try:
            lat = float(input("Enter latitude (-90 to 90): ").strip())
            lon = float(input("Enter longitude (-180 to 180): ").strip())
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
            print("Values out of range.\n")
        except ValueError:
            print("Please enter valid numbers.\n")


# --- CLI argument parser ---


def build_cli_parser() -> argparse.ArgumentParser:
    """Build and return the argparse parser."""
    parser = argparse.ArgumentParser(
        description="Generate high-resolution orthographic globe maps.",
        epilog="Run without arguments for interactive mode.",
    )

    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "--city",
        choices=[k.lower() for k in MAJOR_METROPOLISES],
        metavar="CITY",
        help=f"Pre-defined city ({', '.join(MAJOR_METROPOLISES.keys())})",
    )
    location.add_argument(
        "--lat",
        type=float,
        help="Custom latitude (-90 to 90). Must be used with --lon.",
    )

    parser.add_argument(
        "--lon",
        type=float,
        help="Custom longitude (-180 to 180). Must be used with --lat.",
    )
    parser.add_argument(
        "--provider",
        choices=TILE_PROVIDERS,
        default="osm",
        help="Tile provider (default: osm)",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=3,
        choices=range(1, 5),
        metavar="ZOOM",
        help="Tile zoom level 1-4 (default: 3)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=600,
        help="Output DPI (default: 600)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Explicit output filepath (overrides auto-naming)",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory to save auto-named files into (default: current dir)",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=f"Tile cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--koppen",
        action="store_true",
        default=False,
        help="Enable Köppen-Geiger climate classification overlay.",
    )
    parser.add_argument(
        "--koppen-alpha",
        type=float,
        default=0.45,
        metavar="ALPHA",
        help="Opacity of the Köppen-Geiger overlay (0-1, default: 0.45).",
    )

    return parser


# --- Entry point modes ---


def run_interactive() -> None:
    """Interactive prompt flow with custom coordinate support."""
    city_options = ["[Custom coordinates]"] + list(MAJOR_METROPOLISES.keys())
    selection = prompt_for_selection(
        "Choose a location to center the orthographic map on:",
        city_options,
    )

    if selection == "[Custom coordinates]":
        lat, lon = prompt_for_coordinates()
        city_slug = "custom"
        city_label = f"({lat}, {lon})"
    else:
        city = MAJOR_METROPOLISES[selection]
        lat, lon = city["lat"], city["lon"]
        city_slug = city["slug"]
        city_label = selection

    tile_provider = prompt_for_selection(
        "Choose a tile provider:",
        TILE_PROVIDERS,
    )
    zoom = prompt_for_zoom(default_zoom=3)
    output_file = build_output_filename(city_slug, tile_provider, zoom)

    print(
        f"\nGenerating map for {city_label} using '{tile_provider}' at zoom level {zoom}."
    )
    print(f"Output file: {output_file}\n")

    # Köppen-Geiger overlay prompt
    koppen_input = input("Enable Köppen-Geiger climate overlay? [y/N]: ").strip().lower()
    enable_koppen = koppen_input in ("y", "yes")

    koppen_alpha = 0.45
    if enable_koppen:
        raw_alpha = input("Köppen overlay opacity (0-1) [default: 0.45]: ").strip()
        if raw_alpha:
            try:
                val = float(raw_alpha)
                if 0.0 <= val <= 1.0:
                    koppen_alpha = val
                else:
                    print("Out of range. Using default 0.45.")
            except ValueError:
                print("Invalid number. Using default 0.45.")

    generate_orthographic_map(
        lat=lat,
        lon=lon,
        output_filename=output_file,
        tile_provider=tile_provider,
        zoom=zoom,
        dpi=600,
        city_name=city_label if city_slug != "custom" else None,
        koppen=enable_koppen,
        koppen_alpha=koppen_alpha,
    )


def run_cli(args: argparse.Namespace) -> None:
    """Non-interactive CLI mode driven by argparse namespace."""
    # Validate coordinate pairing first
    if args.lon is not None and args.lat is None:
        logger.error("--lon requires --lat.")
        sys.exit(1)
    if args.lat is not None and args.lon is None:
        logger.error("--lat requires --lon.")
        sys.exit(1)

    # Resolve coordinates
    if args.lat is not None:
        if not (-90 <= args.lat <= 90):
            logger.error("--lat must be between -90 and 90.")
            sys.exit(1)
        if not (-180 <= args.lon <= 180):
            logger.error("--lon must be between -180 and 180.")
            sys.exit(1)
        lat, lon = args.lat, args.lon
        city_slug = "custom"
        city_label = f"({lat}, {lon})"
    elif args.city:
        # Find the city (case-insensitive match)
        city_name = next(
            k for k in MAJOR_METROPOLISES if k.lower() == args.city.lower()
        )
        city = MAJOR_METROPOLISES[city_name]
        lat, lon = city["lat"], city["lon"]
        city_slug = city["slug"]
        city_label = city_name
    else:
        logger.error("Provide --city or --lat/--lon.")
        sys.exit(1)

    # Configure tile cache
    configure_tile_cache(args.cache_dir)

    # Determine output filename
    if args.output:
        output_file = args.output
        output_dir = None  # explicit path, don't prepend output_dir
    else:
        output_file = build_output_filename(city_slug, args.provider, args.zoom)
        output_dir = args.output_dir

    logger.info(
        "Generating map for %s using '%s' at zoom level %d.",
        city_label, args.provider, args.zoom,
    )
    logger.info("Output file: %s", output_file)

    generate_orthographic_map(
        lat=lat,
        lon=lon,
        output_filename=output_file,
        tile_provider=args.provider,
        zoom=args.zoom,
        dpi=args.dpi,
        output_dir=output_dir,
        city_name=city_label if city_slug != "custom" else None,
        koppen=args.koppen,
        koppen_alpha=args.koppen_alpha,
    )


def main() -> None:
    """Entry point for console_scripts and direct invocation."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if len(sys.argv) == 1:
        configure_tile_cache()
        run_interactive()
    else:
        parser = build_cli_parser()
        parsed_args = parser.parse_args()
        run_cli(parsed_args)


if __name__ == "__main__":
    main()
