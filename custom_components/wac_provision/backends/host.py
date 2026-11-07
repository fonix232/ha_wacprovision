"""The HA host's own Wi-Fi as a radio (NetworkManager + wpa_supplicant over D-Bus).

Not implemented yet. Plan:
- Scan: read wpa_supplicant `fi.w1.wpa_supplicant1.BSS` objects; their `IEs` property holds
  the raw IE list for `aiowac.find_in_ies`. NetworkManager's AccessPoint objects do not.
- Provision: add a temporary open NM connection bound to the accessory's BSSID on a spare
  interface, activate it, find `_mfi-config._tcp` with a zeroconf instance bound to that
  interface, run `aiowac.provision` over a transport pinned to the interface
  (SO_BINDTODEVICE; the soft-AP subnet can overlap the LAN), then delete the connection so
  NM restores the previous profile.
- Refuse the interface carrying HA's default route.
"""

from __future__ import annotations

from ..models import ProvisionResult, Sighting, WifiTarget


class HostRadio:
    def __init__(self, interface: str) -> None:
        self.interface = interface
        self.radio_id = f"host:{interface}"

    async def provision(self, sighting: Sighting, name: str, target: WifiTarget | None) -> ProvisionResult:
        raise NotImplementedError("host Wi-Fi backend is not implemented yet")
