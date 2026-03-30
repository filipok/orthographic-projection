import argparse
import os
import sys

import cartopy
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
import cartopy.feature as cfeature
from cartopy.mpl.geoaxes import GeoAxes

# ---------------------------------------------------------------------------
# Tile cache configuration
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "ortho_tiles")


def configure_tile_cache(cache_dir=None):
    """Point Cartopy's download cache at *cache_dir* (created if needed).

    Cartopy already caches tiles internally, but its default location is
    buried under ``~/.local/share/cartopy``.  This helper lets users and
    the CLI control where that cache lives.
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    cartopy.config["data_dir"] = cache_dir
    return cache_dir


MAJOR_METROPOLISES = {
    "NYC": {"lat": 40.7128, "lon": -74.0060, "slug": "nyc"},
    "DC": {"lat": 38.9072, "lon": -77.0369, "slug": "dc"},
    "Moscow": {"lat": 55.7558, "lon": 37.6173, "slug": "moscow"},
    "Shanghai": {"lat": 31.2304, "lon": 121.4737, "slug": "shanghai"},
    "Singapore": {"lat": 1.3521, "lon": 103.8198, "slug": "singapore"},
    "London": {"lat": 51.5074, "lon": -0.1278, "slug": "london"},
    "Paris": {"lat": 48.8566, "lon": 2.3522, "slug": "paris"},
    "Berlin": {"lat": 52.5200, "lon": 13.4050, "slug": "berlin"},
    "Ankara": {"lat": 39.9334, "lon": 32.8597, "slug": "ankara"},
    "Tehran": {"lat": 35.6892, "lon": 51.3890, "slug": "tehran"},
    "New Delhi": {"lat": 28.6139, "lon": 77.2090, "slug": "new_delhi"},
    "Jerusalem": {"lat": 31.7683, "lon": 35.2137, "slug": "jerusalem"},
}

TILE_PROVIDERS = [
    "osm",
    "google",
    "google_satellite"
]


class BufferedTileSource:
    """Fetch an extra ring of Web Mercator tiles to avoid edge underfill."""

    def __init__(self, tile_source, tile_buffer_factor=0.5):
        self.tile_source = tile_source
        self.tile_buffer_factor = tile_buffer_factor
        self.crs = tile_source.crs

    def __getattr__(self, name):
        return getattr(self.tile_source, name)

    def image_for_domain(self, target_domain, target_z):
        x0, x1 = self.crs.x_limits
        world_width = x1 - x0
        tile_width = world_width / (2 ** target_z)
        buffered_domain = target_domain.buffer(tile_width * self.tile_buffer_factor)
        return self.tile_source.image_for_domain(buffered_domain, target_z)


def create_tile_source(tile_provider="osm", tile_buffer_factor=2, **tile_kwargs):
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

    return BufferedTileSource(tile_source, tile_buffer_factor=tile_buffer_factor)


def generate_orthographic_map(
    lat,
    lon,
    output_filename,
    zoom=3,
    dpi=300,
    background_color="#a6d3e0",
    tile_provider="osm",
    tile_kwargs=None,
    tile_buffer_factor=2,
    max_regrid_shape=4096,
    output_dir=None,
):
    """
    Generates an orthographic map projection centered at a specific point using web map tiles.

    Parameters:
    - lat (float): The central latitude (e.g., 40.7128 for New York).
    - lon (float): The central longitude (e.g., -74.0060 for New York).
    - output_filename (str): The name/path of the output PNG file.
    - zoom (int): The tile zoom level. Keep between 2 and 4 for a full globe to avoid massive downloads.
    - dpi (int): Dots per inch for the output image. 300+ is considered high resolution.
    - output_dir (str|None): Optional directory to save the output file into. Created if needed.
    """

    # Resolve output path
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        output_filename = os.path.join(output_dir, output_filename)

    print(f"Setting up the map centered at Latitude: {lat}, Longitude: {lon}...")

    # Step 1: Initialize the requested image tile source
    tile_kwargs = tile_kwargs or {}
    tiles = create_tile_source(
        tile_provider=tile_provider,
        tile_buffer_factor=tile_buffer_factor,
        **tile_kwargs,
    )

    # Step 2: Define the Orthographic projection
    # We pass the user-defined latitude and longitude as the center of the "globe"
    ortho_proj = ccrs.Orthographic(central_longitude=lon, central_latitude=lat)

    # Step 3: Create a high-resolution figure and axes using matplotlib
    # Using subplot_kw with projection ensures 'ax' is a GeoAxes object.
    # figsize=(10, 10) sets the base size of the image in inches.
    fig, ax = plt.subplots(figsize=(20, 20), subplot_kw={"projection": ortho_proj})
    fig.patch.set_facecolor(background_color)
    
    # Cast or type hint 'ax' as GeoAxes to help IDE resolve Cartopy-specific methods
    if not isinstance(ax, GeoAxes):
        raise RuntimeError("Failed to create GeoAxes")

    ax.set_facecolor(background_color)

    # Step 4: Make the map show the entire visible hemisphere (a full globe view)
    ax.set_global()

    # Cartopy reprojects raster tiles through imshow(); its default
    # regrid_shape is only 750 px, which makes large exports look blurry.
    regrid_shape = min(
        max(750, int(min(fig.get_size_inches()) * dpi)),
        max_regrid_shape,
    )

    # Add a base globe color so areas not covered by imagery do not render blank.
    ax.add_feature(cfeature.OCEAN, facecolor=background_color, edgecolor="none", zorder=0)
    ax.add_feature(cfeature.LAND, facecolor="#f1efe6", edgecolor="none", zorder=0)

    # Step 5: Add the background tiles to the map
    print(f"Fetching '{tile_provider}' tiles at zoom level {zoom}. This may take a moment...")
    # zoom is passed as a positional argument to add_image
    try:
        ax.add_image(
            tiles,
            zoom,
            regrid_shape=regrid_shape,
            interpolation="nearest",
        )
    except Exception as e:
        print(f"WARNING: Failed to fetch map tiles: {e}")
        print("The map will be saved with fallback land/ocean features only.")

    # Step 6: Add a grid (latitude/longitude lines) to make the globe look more spherical
    ax.gridlines(draw_labels=False, color='black', alpha=0.3, linestyle='--')

    # Step 7: Export the result as a high-resolution PNG
    print(f"Saving high-resolution map to '{output_filename}' at {dpi} DPI...")

    # bbox_inches='tight' ensures that no extra whitespace is saved around the globe
    plt.savefig(output_filename, dpi=dpi, bbox_inches="tight", transparent=True)

    # Close the plot to free up memory
    plt.close(fig)

    print("Map successfully created!")





def prompt_for_selection(prompt_text, options):
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


def prompt_for_zoom(default_zoom=3, min_zoom=1, max_zoom=8):
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


def build_output_filename(city_slug, tile_provider, zoom):
    sanitized_provider = tile_provider.replace(" ", "_")
    return f"orthographic_map_{city_slug}_{sanitized_provider}_z{zoom}.png"


# --- Custom coordinate prompt ---


def prompt_for_coordinates():
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


def build_cli_parser():
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
        choices=range(1, 9),
        metavar="ZOOM",
        help="Tile zoom level 1-8 (default: 3)",
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

    return parser


# --- Entry point modes ---


def run_interactive():
    """Original interactive prompt flow, now with custom coordinate support."""
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

    generate_orthographic_map(
        lat=lat,
        lon=lon,
        output_filename=output_file,
        tile_provider=tile_provider,
        zoom=zoom,
        dpi=600,
    )


def run_cli(args):
    """Non-interactive CLI mode driven by argparse namespace."""
    # Validate coordinate pairing first
    if args.lon is not None and args.lat is None:
        print("Error: --lon requires --lat.", file=sys.stderr)
        sys.exit(1)
    if args.lat is not None and args.lon is None:
        print("Error: --lat requires --lon.", file=sys.stderr)
        sys.exit(1)

    # Resolve coordinates
    if args.lat is not None:
        if not (-90 <= args.lat <= 90):
            print("Error: --lat must be between -90 and 90.", file=sys.stderr)
            sys.exit(1)
        if not (-180 <= args.lon <= 180):
            print("Error: --lon must be between -180 and 180.", file=sys.stderr)
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
        print("Error: provide --city or --lat/--lon.", file=sys.stderr)
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

    print(
        f"\nGenerating map for {city_label} using '{args.provider}' at zoom level {args.zoom}."
    )
    print(f"Output file: {output_file}\n")

    generate_orthographic_map(
        lat=lat,
        lon=lon,
        output_filename=output_file,
        tile_provider=args.provider,
        zoom=args.zoom,
        dpi=args.dpi,
        output_dir=output_dir,
    )


def main():
    """Entry point for console_scripts and direct invocation."""
    if len(sys.argv) == 1:
        configure_tile_cache()
        run_interactive()
    else:
        parser = build_cli_parser()
        parsed_args = parser.parse_args()
        run_cli(parsed_args)


if __name__ == "__main__":
    main()
