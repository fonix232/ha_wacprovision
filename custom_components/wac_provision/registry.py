"""Which radios hear which unconfigured accessories, and how well."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .const import SIGHTING_MAX_AGE
from .models import Sighting


class SightingRegistry:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now
        self._by_device: dict[str, dict[str, Sighting]] = {}

    def add(self, sighting: Sighting) -> bool:
        """Record a sighting. Returns True the first time this device ID is seen."""
        radios = self._by_device.setdefault(sighting.device_id, {})
        is_new = not radios
        radios[sighting.radio_id] = sighting
        return is_new

    def ranked(self, device_id: str, exclude: set[str] | None = None) -> list[Sighting]:
        """Fresh sightings of `device_id`, best RSSI first."""
        cutoff = self._now() - SIGHTING_MAX_AGE
        fresh = [
            s
            for s in self._by_device.get(device_id, {}).values()
            if s.seen >= cutoff and s.radio_id not in (exclude or set())
        ]
        return sorted(fresh, key=lambda s: s.rssi, reverse=True)

    def last_seen(self, device_id: str) -> datetime | None:
        radios = self._by_device.get(device_id)
        return max((s.seen for s in radios.values()), default=None) if radios else None

    def forget(self, device_id: str) -> None:
        self._by_device.pop(device_id, None)
