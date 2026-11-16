import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from aur import prepare_aur
from build import ROOT, digest


class AurTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, version="9.12.4.12345678-deadbeef", revision=3, manifest=None):
        archive = self.root / f"workbuddy_{version}-{revision}_amd64.tar.gz"
        data = json.dumps(manifest or {"version": version, "packagingRevision": revision}).encode()
        with tarfile.open(archive, "w:gz") as output:
            entry = tarfile.TarInfo("WorkBuddy/port-manifest.json")
            entry.size = len(data)
            output.addfile(entry, io.BytesIO(data))
        archive.with_name(archive.name + ".sha256").write_text(f"{digest(archive).hex()}  {archive.name}\n")
        return archive

    def test_future_release_uses_immutable_url_and_verified_checksums(self):
        archive = self.archive()
        output = self.root / "recipe"
        prepare_aur(archive, output)
        recipe = (output / "PKGBUILD").read_text()
        self.assertIn("pkgname=workbuddy-cn-bin", recipe)
        self.assertIn("pkgver=9.12.4.12345678_deadbeef\npkgrel=3", recipe)
        self.assertIn(f"/releases/download/port-v9.12.4.12345678-deadbeef-r3/{archive.name}", recipe)
        self.assertIn(digest(archive).hex(), recipe)
        self.assertNotIn("SKIP", recipe)
        for name in ("WorkBuddy.desktop", "workbuddy-launcher", "LICENSE"):
            self.assertIn(digest(output / name).hex(), recipe)

    def test_changed_archive_cannot_generate_recipe(self):
        archive = self.archive()
        archive.write_bytes(archive.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            prepare_aur(archive, self.root / "recipe")

    def test_release_filename_must_match_manifest(self):
        archive = self.archive(manifest={"version": "1.0.0-abcdef", "packagingRevision": 3})
        with self.assertRaisesRegex(ValueError, "metadata"):
            prepare_aur(archive, self.root / "recipe")

    @unittest.skipUnless(shutil.which("vercmp"), "Arch vercmp is required for AUR publishing")
    def test_publish_is_idempotent_and_cannot_downgrade(self):
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        package = self.root / "recipe"
        package.mkdir()
        for name in ("PKGBUILD", "WorkBuddy.desktop", "workbuddy-launcher", "LICENSE"):
            (package / name).write_text("fixture\n")
        metadata = package / ".SRCINFO"
        metadata.write_text("pkgbase = workbuddy-cn-bin\n\tpkgver = 9.0\n\tpkgrel = 1\npkgname = workbuddy-cn-bin\n")
        env = dict(os.environ, AUR_REPOSITORY=str(remote))

        def publish():
            return subprocess.run(["bash", str(ROOT / "scripts/publish-aur.sh"), str(package)],
                                  env=env, check=True, capture_output=True, text=True).stdout

        def head():
            return subprocess.run(["git", "--git-dir", str(remote), "rev-parse", "master"],
                                  check=True, capture_output=True, text=True).stdout

        publish()
        original = head()
        self.assertIn("already up to date", publish())
        self.assertEqual(head(), original)
        metadata.write_text(metadata.read_text().replace("9.0", "10.0"))
        publish()
        upgraded = head()
        self.assertNotEqual(upgraded, original)
        metadata.write_text(metadata.read_text().replace("10.0", "9.0"))
        self.assertIn("already has newer version", publish())
        self.assertEqual(head(), upgraded)


if __name__ == "__main__":
    unittest.main()
