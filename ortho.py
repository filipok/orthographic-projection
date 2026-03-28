import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.io.img_tiles as cimgt
import cartopy.feature as cfeature
from cartopy.mpl.geoaxes import GeoAxes


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
):
    """
    Generates an orthographic map projection centered at a specific point using web map tiles.

    Parameters:
    - lat (float): The central latitude (e.g., 40.7128 for New York).
    - lon (float): The central longitude (e.g., -74.0060 for New York).
    - output_filename (str): The name/path of the output PNG file.
    - zoom (int): The tile zoom level. Keep between 2 and 4 for a full globe to avoid massive downloads.
    - dpi (int): Dots per inch for the output image. 300+ is considered high resolution.
    """

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
    ax.add_image(
        tiles,
        zoom,
        regrid_shape=regrid_shape,
        interpolation="nearest",
    )

    # Step 6: Add a grid (latitude/longitude lines) to make the globe look more spherical
    ax.gridlines(draw_labels=False, color='black', alpha=0.3, linestyle='--')

    # Step 7: Export the result as a high-resolution PNG
    print(f"Saving high-resolution map to '{output_filename}' at {dpi} DPI...")

    # bbox_inches='tight' ensures that no extra whitespace is saved around the globe
    plt.savefig(output_filename, dpi=dpi, bbox_inches="tight", transparent=True)

    # Close the plot to free up memory
    plt.close(fig)

    print("Map successfully created!")


def generate_orthographic_osm_map(*args, **kwargs):
    """Backward-compatible wrapper for existing callers."""
    kwargs.setdefault("tile_provider", "osm")
    return generate_orthographic_map(*args, **kwargs)


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


# --- Interactive entry point ---

if __name__ == "__main__":
    city_name = prompt_for_selection(
        "Choose a metropolis to center the orthographic map on:",
        list(MAJOR_METROPOLISES.keys()),
    )
    city = MAJOR_METROPOLISES[city_name]

    tile_provider = prompt_for_selection(
        "Choose a tile provider:",
        TILE_PROVIDERS,
    )
    zoom = prompt_for_zoom(default_zoom=3)
    output_file = build_output_filename(city["slug"], tile_provider, zoom)

    print(
        f"\nGenerating map for {city_name} using '{tile_provider}' at zoom level {zoom}."
    )
    print(f"Output file: {output_file}\n")

    generate_orthographic_map(
        lat=city["lat"],
        lon=city["lon"],
        output_filename=output_file,
        tile_provider=tile_provider,
        zoom=zoom,
        dpi=600,
    )
