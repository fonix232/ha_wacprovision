"""The radio interface that ESPHome proxies and the host's own Wi-Fi both implement."""

from __future__ import annotations

from typing import Protocol

from .models import ProvisionResult, Sighting, WifiTarget


class Radio(Protocol):
    radio_id: str

    async def provision(self, sighting: Sighting, name: str, target: WifiTarget | None) -> ProvisionResult:
        """Join the accessory's soft-AP, run WAC auth-setup and /config, return to normal.

        `target=None` means use the radio's own network (ESPHome nodes only). Must report
        the furthest stage reached so the caller knows whether another radio may retry.
        """
        ...


class RadioRegistry:
    def __init__(self) -> None:
        self._radios: dict[str, Radio] = {}

    def register(self, radio: Radio) -> None:
        self._radios[radio.radio_id] = radio

    def unregister(self, radio_id: str) -> None:
        self._radios.pop(radio_id, None)

    def get(self, radio_id: str) -> Radio | None:
        return self._radios.get(radio_id)
