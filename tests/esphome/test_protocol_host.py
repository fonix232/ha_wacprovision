"""Build the ESPHome component's protocol code on the host and run it against aiowac's fake
accessory. Skipped when g++ or mbedTLS headers are missing.

Run from the repo root in a venv that has aiowac's test extras:
    python -m pytest -q tests/esphome
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components" / "wac_provisioner"
sys.path.insert(0, str(ROOT / "python" / "aiowac"))

from tests.fake_accessory import FakeAccessory  # noqa: E402  (aiowac's test package)


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> Path:
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    out = tmp_path_factory.mktemp("build") / "wac_host_client"
    cmd = [
        "g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1",
        f"-I{COMPONENT}",
        str(COMPONENT / "wac_protocol.cpp"),
        str(COMPONENT / "wac_http.cpp"),
        str(Path(__file__).parent / "host" / "wac_host_client.cpp"),
        "-lmbedcrypto", "-o", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and "mbedtls/" in result.stderr and "No such file" in result.stderr:
        pytest.skip("mbedTLS headers not installed (libmbedtls-dev)")
    assert result.returncode == 0, result.stderr
    return out


def parse(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def test_selftest(client: Path) -> None:
    result = subprocess.run([str(client), "selftest"], capture_output=True, text=True)
    out = parse(result.stdout)
    assert result.returncode == 0, result.stdout
    assert out["wac_id"] == "00:11:22:33:44:55"
    assert (out["name"], out["manufacturer"], out["model"]) == ("Name", "waclib", "Type")


async def run_against_fake(client: Path, fake: FakeAccessory, psk: str) -> tuple[int, dict[str, str]]:
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        proc = await asyncio.create_subprocess_exec(
            str(client), "provision", "127.0.0.1", str(port), "Kitchen Plug", "TestSSID", psk,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), 20)
        return proc.returncode or 0, parse(stdout.decode())
    finally:
        await runner.cleanup()


async def test_provision_against_fake_accessory(client: Path) -> None:
    fake = FakeAccessory()
    code, out = await run_against_fake(client, fake, "testpassword")
    assert code == 0, out
    assert fake.received_config == {0x08: b"Kitchen Plug", 0x0C: b"TestSSID", 0x0B: b"testpassword"}
    assert (out["serial"], out["model"], out["firmware"]) == ("SN0000000", "TP-1", "1.2.3")
    assert int(out["certificate_size"]) == len(fake.certificate)


async def test_open_network_and_empty_response(client: Path) -> None:
    fake = FakeAccessory(response_fields=[])
    code, out = await run_against_fake(client, fake, "")
    assert code == 0, out
    assert fake.received_config is not None and 0x0B not in fake.received_config
    assert out["serial"] == ""


async def test_trailing_auth_data_rejected(client: Path) -> None:
    fake = FakeAccessory(trailing_auth_bytes=b"\x00")
    code, out = await run_against_fake(client, fake, "testpassword")
    assert code == 1 and "trailing" in out["error"]
    assert fake.received_config is None
