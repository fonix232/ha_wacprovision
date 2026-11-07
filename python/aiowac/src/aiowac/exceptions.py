"""Errors raised by aiowac."""

from __future__ import annotations


class WacError(Exception):
    """Base class for all aiowac errors."""


class WacProtocolError(WacError):
    """The accessory sent something that does not follow the WAC wire format."""


class WacHttpError(WacError):
    """The accessory answered with a non-200 HTTP status."""

    def __init__(self, path: str, status: int) -> None:
        super().__init__(f"POST {path} returned HTTP {status}")
        self.path = path
        self.status = status


class WacStateError(WacError):
    """A session method was called out of order."""
