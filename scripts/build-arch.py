#!/usr/bin/env python3
"""Package a verified portable WorkBuddy build for Arch Linux / Manjaro."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

from build import ROOT, digest, run


def build_arch(archive=None):
    if archive is None:
        current = ROOT / "work/current-amd64/WorkBuddy/port-manifest.json"
        if not current.exists():
            run(sys.executable, ROOT / "scripts/build.py", "--format", "portable")
        manifest = json.loads(current.read_text())
        archive = ROOT / "dist" / f"workbuddy_{manifest['version']}-{manifest['packagingRevision']}_amd64.tar.gz"
    archive = Path(archive).resolve()
    match = re.fullmatch(r"workbuddy_(\d+(?:\.\d+)+)-([0-9a-f]+)-(\d+)_(amd64|arm64)\.tar\.gz", archive.name)
    if not match:
        raise ValueError(f"Unexpected portable archive name: {archive.name}")
    checksum_file = archive.with_name(archive.name + ".sha256")
    expected, filename = checksum_file.read_text().strip().split(maxsplit=1)
    if filename != archive.name or digest(archive).hex() != expected:
        raise ValueError(f"Portable archive SHA-256 mismatch: {archive}")
    arch = {"amd64": "x86_64", "arm64": "aarch64"}[match[4]]
    if os.uname().machine != arch:
        raise ValueError(f"Build this package on {arch}")
    with tarfile.open(archive) as source:
        manifest = json.load(source.extractfile("WorkBuddy/port-manifest.json"))
    if manifest["version"] != f"{match[1]}-{match[2]}" or manifest["packagingRevision"] != int(match[3]):
        raise ValueError("Portable archive metadata differs from its filename")

    work = Path(tempfile.mkdtemp(prefix="arch-workbuddy-", dir=ROOT / "work"))
    try:
        os.link(archive, work / archive.name)
    except OSError:
        shutil.copyfile(archive, work / archive.name)
    for name in ["WorkBuddy.desktop", "LICENSE"]:
        shutil.copyfile(ROOT / name, work / name)
    (work / "workbuddy-launcher").write_text('#!/bin/sh\nexec /opt/WorkBuddy/launch-workbuddy "$@"\n')
    sources = [archive.name, "WorkBuddy.desktop", "workbuddy-launcher", "LICENSE"]
    values = {
        "PKGVER": f"{match[1]}_{match[2]}", "PKGREL": match[3], "ARCH": arch,
        "ARCHIVE": archive.name,
        "CHECKSUMS": "\n            ".join("'" + digest(work / name).hex() + "'" for name in sources),
    }
    recipe = (ROOT / "packaging/PKGBUILD.in").read_text()
    for key, value in values.items():
        recipe = recipe.replace("@" + key + "@", value)
    (work / "PKGBUILD").write_text(recipe)
    dist = ROOT / "dist"
    env = dict(os.environ, PKGDEST=str(dist), BUILDDIR=str(work), LOGDEST=str(work))
    print(f"Arch recipe: {work / 'PKGBUILD'}", flush=True)
    run("makepkg", "--force", "--noconfirm", "--nosign", "--nocolor", cwd=work, env=env)
    output = dist / f"workbuddy-{values['PKGVER']}-{values['PKGREL']}-{arch}.pkg.tar.zst"
    if not output.is_file():
        raise FileNotFoundError(output)
    output.with_name(output.name + ".sha256").write_text(digest(output).hex() + "  " + output.name + "\n")
    shutil.copyfile(work / "PKGBUILD", output.with_name(output.name + ".PKGBUILD"))
    print(f"Built {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", help="Portable archive (defaults to the current verified amd64 build)")
    build_arch(parser.parse_args().archive)
