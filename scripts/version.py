#!/usr/bin/env python3
"""One version for the whole repository, kept in step across:

- python/aiowac/pyproject.toml           [project] version
- custom_components/wac_provision/manifest.json   "version" and the "aiowac==" requirement

    scripts/version.py show
    scripts/version.py check           exit 1 if the places disagree
    scripts/version.py bump patch|minor|major
    scripts/version.py set 1.2.3
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "python" / "aiowac" / "pyproject.toml"
MANIFEST = ROOT / "custom_components" / "wac_provision" / "manifest.json"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PYPROJECT_VERSION = re.compile(r'(?m)^version = "([^"]+)"$')


def read() -> dict[str, str]:
    manifest = json.loads(MANIFEST.read_text())
    pins = [r.split("==", 1)[1] for r in manifest["requirements"] if r.startswith("aiowac==")]
    match = PYPROJECT_VERSION.search(PYPROJECT.read_text())
    return {
        "pyproject": match.group(1) if match else "",
        "manifest": manifest["version"],
        "manifest aiowac pin": pins[0] if len(pins) == 1 else "",
    }


def write(version: str) -> None:
    if not SEMVER.match(version):
        sys.exit(f"not a MAJOR.MINOR.PATCH version: {version}")
    text = PYPROJECT.read_text()
    PYPROJECT.write_text(PYPROJECT_VERSION.sub(f'version = "{version}"', text, count=1))
    manifest = json.loads(MANIFEST.read_text())
    manifest["version"] = version
    manifest["requirements"] = [
        f"aiowac=={version}" if r.startswith("aiowac==") else r for r in manifest["requirements"]
    ]
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


def bump(current: str, part: str) -> str:
    match = SEMVER.match(current)
    if not match:
        sys.exit(f"current version is not MAJOR.MINOR.PATCH: {current}")
    major, minor, patch = map(int, match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    sys.exit(f"unknown part: {part} (patch, minor or major)")


def main(argv: list[str]) -> int:
    versions = read()
    distinct = set(versions.values())
    if not argv or argv[0] == "show":
        print(versions["manifest"])
        return 0
    if argv[0] == "check":
        if len(distinct) != 1 or "" in distinct:
            for where, value in versions.items():
                print(f"{where}: {value or '(missing)'}", file=sys.stderr)
            return 1
        print(f"ok: {distinct.pop()}")
        return 0
    if len(distinct) != 1:
        sys.exit(f"versions disagree, fix them first: {versions}")
    if argv[0] == "bump" and len(argv) == 2:
        new = bump(versions["manifest"], argv[1])
    elif argv[0] == "set" and len(argv) == 2:
        new = argv[1]
    else:
        sys.exit(__doc__)
    write(new)
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
