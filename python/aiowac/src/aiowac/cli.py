"""Command-line configurator, for testing against real accessories.

    aiowac decode-beacon <hex>
    aiowac provision --ssid NAME [--psk PSK | --open] [--name NAME] [--host H --port P]
    aiowac confirm --device-id 00:11:22:33:44:55

`provision` runs while this machine is joined to the accessory's open soft-AP. It then
waits for you to rejoin the target network and sends /configured.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from . import beacon, discovery
from .exceptions import WacError
from .session import WifiConfig, confirm_configured, provision
from .transport import AiohttpTransport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aiowac")
    sub = parser.add_subparsers(dest="command", required=True)

    dec = sub.add_parser("decode-beacon", help="decode a WAC vendor IE or IE list (hex)")
    dec.add_argument("hex")

    prov = sub.add_parser("provision", help="send Wi-Fi credentials to an accessory")
    prov.add_argument("--ssid", required=True)
    secret = prov.add_mutually_exclusive_group()
    secret.add_argument("--psk", help="avoid: ends up in shell history; omit to be prompted")
    secret.add_argument("--open", action="store_true", help="target network has no password")
    prov.add_argument("--name", help="name the accessory should use (default: its mDNS name)")
    prov.add_argument("--host", help="skip discovery and use this address")
    prov.add_argument("--port", type=int, default=80)
    prov.add_argument("--no-confirm", action="store_true", help="skip the /configured step")

    conf = sub.add_parser("confirm", help="send /configured to an accessory on this network")
    conf.add_argument("--device-id", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "decode-beacon":
            return _decode(args.hex)
        if args.command == "provision":
            return asyncio.run(_provision(args))
        return asyncio.run(_confirm(args.device_id))
    except (WacError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def _decode(hex_input: str) -> int:
    data = bytes.fromhex(hex_input.replace(" ", "").replace(":", ""))
    if data.startswith(beacon.WAC_PREFIX):
        result = beacon.parse_payload(data[len(beacon.WAC_PREFIX) :])
    else:
        result = beacon.find_in_ies(data)
    if result is None:
        print("no WAC element found", file=sys.stderr)
        return 1
    print(f"device id    : {result.device_id}")
    print(f"name         : {result.name}")
    print(f"manufacturer : {result.manufacturer}")
    print(f"model        : {result.model}")
    print(f"flags        : {result.flags.hex()}")
    for key, value in sorted(result.extra.items()):
        print(f"field 0x{key:02x}   : {value.hex()}")
    return 0


async def _provision(args: argparse.Namespace) -> int:
    psk = None if args.open else (args.psk or getpass.getpass(f"PSK for {args.ssid}: "))
    device_id = None
    default_name = "Accessory"
    if args.host:
        host, port = args.host, args.port
    else:
        print("Looking for _mfi-config._tcp on this network...")
        service = await discovery.find(timeout=15)
        if service is None:
            print("error: no accessory found; are you joined to its soft-AP?", file=sys.stderr)
            return 1
        host, port, device_id = service.host, service.port, service.device_id
        default_name = service.name.split(".", 1)[0] or default_name
        print(f"Found {service.name} ({device_id}) at {host}:{port}")

    config = WifiConfig(name=args.name or default_name, ssid=args.ssid, psk=psk)
    async with AiohttpTransport(host, port) as transport:
        result = await provision(transport, config)

    print("Accessory accepted the configuration.")
    print(f"  certificate  : {len(result.auth.certificate)} bytes (not verified)")
    if result.info is None:
        print("  (accessory returned no details)")
    else:
        for label, value in (
            ("name", result.info.name),
            ("model", result.info.model),
            ("serial", result.info.serial_number),
            ("firmware", result.info.firmware_version),
            ("hardware", result.info.hardware_version),
        ):
            if value:
                print(f"  {label:<12} : {value}")

    if args.no_confirm:
        return 0
    if device_id is None:
        print("No device ID known (--host was used); run `aiowac confirm` once rejoined.")
        return 0
    input(f"Rejoin {args.ssid} on this machine, then press Enter... ")
    return await _confirm(device_id)


async def _confirm(device_id: str) -> int:
    print(f"Looking for {device_id} on this network...")
    service = await discovery.find(device_id, timeout=60)
    if service is None:
        print("error: accessory did not appear; it may have failed to join", file=sys.stderr)
        return 1
    async with AiohttpTransport(service.host, service.port) as transport:
        await confirm_configured(transport)
    print("Accessory confirmed configured. HomeKit Device should now discover it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
