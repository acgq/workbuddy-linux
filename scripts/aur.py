#!/usr/bin/env python3
"""Generate the AUR recipe for an already verified portable release."""
import argparse
import json
from pathlib import Path
import re
import shutil
import tarfile

from build import ROOT, digest


def prepare_aur(archive, destination, repository="acgq/workbuddy-linux"):
    archive, destination = Path(archive), Path(destination)
    match = re.fullmatch(r"workbuddy_(\d+(?:\.\d+)+)-([0-9a-f]+)-(\d+)_amd64\.tar\.gz", archive.name)
    if not match or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Unexpected portable archive name or GitHub repository")
    expected, filename = archive.with_name(archive.name + ".sha256").read_text().split()
    if filename != archive.name or digest(archive).hex() != expected:
        raise ValueError("Portable archive SHA-256 mismatch")
    with tarfile.open(archive) as source:
        manifest = json.load(source.extractfile("WorkBuddy/port-manifest.json"))
    version, revision = f"{match[1]}-{match[2]}", int(match[3])
    if manifest["version"] != version or manifest["packagingRevision"] != revision:
        raise ValueError("Portable archive metadata differs from its filename")
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("WorkBuddy.desktop", "LICENSE"):
        shutil.copyfile(ROOT / name, destination / name)
    (destination / "workbuddy-launcher").write_text('#!/bin/sh\nexec /opt/WorkBuddy/launch-workbuddy "$@"\n')
    checksums = [expected] + [digest(destination / name).hex()
                              for name in ("WorkBuddy.desktop", "workbuddy-launcher", "LICENSE")]
    values = {
        "PKGVER": f"{match[1]}_{match[2]}", "PKGREL": str(revision),
        "SOURCE_URL": f"https://github.com/{repository}/releases/download/port-v{version}-r{revision}/{archive.name}",
        "CHECKSUMS": "\n            ".join(f"'{checksum}'" for checksum in checksums),
    }
    recipe = (ROOT / "packaging/aur/PKGBUILD.in").read_text()
    for key, value in values.items():
        recipe = recipe.replace("@" + key + "@", value)
    (destination / "PKGBUILD").write_text(recipe)
    print(f"AUR recipe: {destination / 'PKGBUILD'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repository", default="acgq/workbuddy-linux")
    args = parser.parse_args()
    prepare_aur(args.archive, args.destination, args.repository)
