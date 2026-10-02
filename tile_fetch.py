"""Download single web-map tiles.

Unlike Cartopy's ``GoogleWTS.get_image``, which swallows HTTP errors and
returns an opaque grey tile, :func:`download_tile` raises on any failure so
the caller can decide what a missing tile looks like (``ortho`` makes it
transparent, letting the land/ocean fallback show through).
"""

from __future__ import annotations

import io
import urllib.request

import numpy as np
from PIL import Image

DEFAULT_TIMEOUT = 30  # seconds per tile request


def download_tile(url: str, user_agent: str, timeout: float = DEFAULT_TIMEOUT) -> np.ndarray:
    """Return the tile at *url* as an RGBA ``uint8`` array.

    Raises ``OSError`` (including ``urllib.error.URLError`` and
    ``PIL.UnidentifiedImageError``) if the tile cannot be downloaded or decoded.
    Error messages never include *url*, which may contain an API key.
    """
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = resp.read()
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
