"""Data shared between radios, the registry and the flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class Sighting:
    """One radio hearing one unconfigured accessory's beacon."""

    device_id: str  # WAC device ID from the beacon, upper-case colon form
    radio_id: str  # "esphome:<node>" or "host:<iface>"
    bssid: str
    channel: int
    rssi: int
    name: str
    manufacturer: str
    model: str
    seen: datetime


@dataclass(frozen=True)
class WifiTarget:
    ssid: str
    psk: str | None


class ProvisionStage(StrEnum):
    """How far a session got; decides whether another radio may retry."""

    JOIN = "join"
    DISCOVER = "discover"
    AUTH_SETUP = "auth_setup"
    CONFIG_SENT = "config_sent"
    DONE = "done"


@dataclass(frozen=True)
class ProvisionResult:
    ok: bool
    stage: ProvisionStage
    error: str | None = None
    serial_number: str = ""
    model: str = ""
    firmware_version: str = ""
    hardware_version: str = ""
    log_tail: str = ""

    @property
    def retryable_elsewhere(self) -> bool:
        """Only failures before /config was sent may move to another radio."""
        return not self.ok and self.stage in (
            ProvisionStage.JOIN,
            ProvisionStage.DISCOVER,
            ProvisionStage.AUTH_SETUP,
        )
