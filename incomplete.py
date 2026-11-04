"""What a map leaves out that it was asked for: a layer whose data could not be loaded, or tiles that failed.

The renderer draws what it can rather than fail, and says so in a warning.
:func:`note` logs that warning and also records it for whoever draws the map
inside :func:`noting`, so that a map kept to be used again (an atlas's) can be
drawn again once the data is there.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Generator

_noted: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("incomplete", default=None)


def note(logger: logging.Logger, message: str, *args: object) -> None:
    """Log *message* (``%`` formatted with *args*) as a warning, and record it for :func:`noting`."""
    logger.warning(message, *args, stacklevel=2)
    noted = _noted.get()
    if noted is not None:
        noted.append(message % args if args else message)


@contextlib.contextmanager
def noting() -> Generator[list[str]]:
    """The messages :func:`note` records while the context is open, in order."""
    noted: list[str] = []
    token = _noted.set(noted)
    try:
        yield noted
    finally:
        _noted.reset(token)
