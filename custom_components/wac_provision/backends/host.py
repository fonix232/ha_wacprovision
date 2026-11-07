"""The HA host's own Wi-Fi as a radio.

Only spare interfaces are used: a Wi-Fi device that NetworkManager reports as disconnected
and that does not carry HA's primary connection. Taking a busy interface would cut HA off.

`HostRadio` holds the provisioning logic and talks to the interface through `WifiControl`.
No `WifiControl` implementation ships yet, so no host radio is registered at runtime.

Known gap: requests are bound to the soft-AP interface's address, not with SO_BINDTODEVICE,
so a soft-AP subnet that overlaps another interface's subnet can misroute.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from aiowac import AiohttpTransport, WacError, WacHttpError, WifiConfig, find_in_ies
from aiowac.exceptions import WacProtocolError
from aiowac.session import WacSession

from ..models import ProvisionResult, ProvisionStage, Sighting, WifiTarget

_LOGGER = logging.getLogger(__name__)

JOIN_TIMEOUT = 20.0
DISCOVER_TIMEOUT = 6.0


@dataclass(frozen=True)
class Bss:
    bssid: str  # upper-case colon form
    ssid: bytes
    frequency: int  # MHz
    signal: int  # dBm
    ies: bytes
    privacy: bool


@dataclass(frozen=True)
class JoinInfo:
    address: str  # this host's IPv4 address on the soft-AP
    gateway: str


class WifiControl(Protocol):
    interface: str

    async def scan(self) -> list[Bss]: ...

    async def join_open(self, ssid: bytes, bssid: str, timeout: float) -> JoinInfo: ...

    async def leave(self) -> None: ...


def channel_for(frequency: int) -> int:
    if frequency == 2484:
        return 14
    if 2412 <= frequency <= 2472:
        return (frequency - 2407) // 5
    if 5000 <= frequency <= 5900:
        return (frequency - 5000) // 5
    return 0


def sightings_from_scan(radio_id: str, bsss: list[Bss], now: Any) -> list[tuple[Sighting, bytes]]:
    """WAC soft-APs in a scan, with the raw SSID needed to join each."""
    out = []
    for bss in bsss:
        if bss.privacy:
            continue  # WAC soft-APs are open
        try:
            beacon = find_in_ies(bss.ies)
        except WacProtocolError:
            continue
        if beacon is None:
            continue
        sighting = Sighting(
            device_id=beacon.device_id,
            radio_id=radio_id,
            bssid=bss.bssid,
            channel=channel_for(bss.frequency),
            rssi=bss.signal,
            name=beacon.name,
            manufacturer=beacon.manufacturer,
            model=beacon.model,
            seen=now,
        )
        out.append((sighting, bss.ssid))
    return out


DiscoverFn = Any  # async (address, device_id) -> (host, port) | None


class HostRadio:
    def __init__(self, control: WifiControl, discover: DiscoverFn | None = None) -> None:
        self.control = control
        self.radio_id = f"host:{control.interface}"
        self._discover = discover or _discover_on_interface
        self._ssids: dict[str, bytes] = {}  # bssid -> raw SSID, from the last scans
        self._lock = asyncio.Lock()

    async def scan(self, now: Any) -> list[Sighting]:
        found = sightings_from_scan(self.radio_id, await self.control.scan(), now)
        for sighting, ssid in found:
            self._ssids[sighting.bssid] = ssid
        return [s for s, _ in found]

    async def provision(self, sighting: Sighting, name: str, target: WifiTarget | None) -> ProvisionResult:
        if target is None:
            return ProvisionResult(
                False, ProvisionStage.JOIN, "set the target network in the integration's settings to use the host's Wi-Fi"
            )
        ssid = self._ssids.get(sighting.bssid)
        if ssid is None:
            return ProvisionResult(False, ProvisionStage.JOIN, "accessory not seen in a recent scan of this interface")
        try:
            config = WifiConfig(name=name, ssid=target.ssid, psk=target.psk)
        except ValueError as err:
            return ProvisionResult(False, ProvisionStage.JOIN, str(err))

        async with self._lock:
            stage = ProvisionStage.JOIN
            try:
                join = await self.control.join_open(ssid, sighting.bssid, JOIN_TIMEOUT)
                stage = ProvisionStage.DISCOVER
                found = await self._discover(join.address, sighting.device_id)
                host, port = found if found else (join.gateway, 80)
                stage = ProvisionStage.AUTH_SETUP
                async with AiohttpTransport(host, port, local_addr=(join.address, 0)) as transport:
                    session = WacSession(transport)
                    await session.auth_setup()
                    stage = ProvisionStage.CONFIG_SENT
                    info = await session.configure(config)
            except (WacError, OSError, TimeoutError) as err:
                return ProvisionResult(False, stage, _describe(stage, err))
            finally:
                try:
                    await self.control.leave()
                except Exception:  # noqa: BLE001  - best effort; NM drops volatile profiles itself
                    _LOGGER.exception("Could not leave the accessory's soft-AP on %s", self.control.interface)
            self._ssids.pop(sighting.bssid, None)
            return ProvisionResult(
                True,
                ProvisionStage.DONE,
                serial_number=info.serial_number if info else "",
                model=info.model if info else "",
                firmware_version=info.firmware_version if info else "",
                hardware_version=info.hardware_version if info else "",
            )


def _describe(stage: ProvisionStage, err: Exception) -> str:
    if isinstance(err, WacHttpError):
        return f"accessory answered HTTP {err.status} to {err.path}"
    prefix = {
        ProvisionStage.JOIN: "could not join the accessory's soft-AP",
        ProvisionStage.DISCOVER: "could not find the accessory's _mfi-config service",
        ProvisionStage.AUTH_SETUP: "auth-setup failed",
        ProvisionStage.CONFIG_SENT: "no valid answer to /config (the accessory may still apply it)",
    }.get(stage, "provisioning failed")
    return f"{prefix}: {err}" if str(err) else prefix


async def _discover_on_interface(address: str, device_id: str) -> tuple[str, int] | None:
    from aiowac.discovery import find
    from zeroconf.asyncio import AsyncZeroconf

    aiozc = AsyncZeroconf(interfaces=[address])
    try:
        service = await find(device_id, timeout=DISCOVER_TIMEOUT, aiozc=aiozc)
    finally:
        await aiozc.async_close()
    return (service.host, service.port) if service else None
