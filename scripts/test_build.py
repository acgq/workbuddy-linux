import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest

from build import electron_version, extract_asar, patch_linux, pack_unpacked_asar, validate_runtime_version


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, files, data=b""):
        header = json.dumps({"files": files}).encode()
        body = struct.pack("<II", len(header) + 4, len(header)) + header
        archive = self.root / "app.asar"
        archive.write_bytes(struct.pack("<II", 4, len(body)) + body + data)
        return archive

    def test_framework_version_is_read_from_source(self):
        # A future source version must not inherit the packaging host's runtime.
        plist = self.root / "Frameworks/Electron Framework.framework/Versions/A/Resources/Info.plist"
        plist.parent.mkdir(parents=True)
        plist.write_bytes(plistlib.dumps({"CFBundleVersion": "42.3.7"}))
        self.assertEqual(electron_version(self.root), "42.3.7")

    def test_missing_framework_fails(self):
        with self.assertRaises(ValueError):
            electron_version(self.root)

    def test_packed_files_sidecars_and_executable_permissions(self):
        archive = self.archive({
            "main.js": {"offset": "0", "size": 5, "executable": True},
            "native.node": {"size": 3, "unpacked": True},
        }, b"hello")
        sidecar = self.root / "app.asar.unpacked"
        sidecar.mkdir()
        (sidecar / "native.node").write_bytes(b"ELF")
        output = self.root / "out"
        self.assertEqual(extract_asar(archive, output), [])
        self.assertEqual((output / "main.js").read_bytes(), b"hello")
        self.assertEqual((output / "native.node").read_bytes(), b"ELF")
        self.assertTrue((output / "main.js").stat().st_mode & 0o111)

    def test_absent_foreign_sidecar_is_reported_without_placeholder(self):
        archive = self.archive({"win32-x64": {"files": {"addon.node": {"unpacked": True, "size": 3}}}})
        output = self.root / "out"
        self.assertEqual(extract_asar(archive, output), ["win32-x64/addon.node"])
        self.assertFalse((output / "win32-x64/addon.node").exists())

    def test_missing_application_sidecar_fails(self):
        archive = self.archive({"main.js": {"unpacked": True, "size": 3}})
        with self.assertRaises(FileNotFoundError):
            extract_asar(archive, self.root / "out")

    def test_archive_cannot_escape_destination(self):
        archive = self.archive({"..": {"files": {"escaped": {"offset": "0", "size": 1}}}}, b"x")
        with self.assertRaises(ValueError):
            extract_asar(archive, self.root / "out")
        self.assertFalse((self.root / "escaped").exists())

    def test_truncated_packed_file_fails(self):
        archive = self.archive({"main.js": {"offset": "0", "size": 10}}, b"x")
        with self.assertRaises(ValueError):
            extract_asar(archive, self.root / "out")

    def test_changed_im_guard_stops_build(self):
        main = self.root / "main"
        main.mkdir()
        (main / "daemon-bootstrap.js").write_text("function changedUpstream(){}")
        with self.assertRaises(ValueError):
            patch_linux(self.root)

    def test_vendor_runtime_requires_matching_electron_base(self):
        validate_runtime_version("37.10.3", "37.10.3-24")
        with self.assertRaises(ValueError):
            validate_runtime_version("38.0.0", "37.10.3-24")

    def test_unpacked_asar_roundtrip(self):
        app = self.root / "app.asar.unpacked"
        (app / "main").mkdir(parents=True)
        (app / "main/index.js").write_text("module.exports = 42")
        binary = app / "tool"
        binary.write_bytes(b"ELF")
        binary.chmod(0o755)
        archive = self.root / "app.asar"
        pack_unpacked_asar(app, archive)
        output = self.root / "out"
        self.assertEqual(extract_asar(archive, output), [])
        self.assertEqual((output / "main/index.js").read_text(), "module.exports = 42")
        self.assertTrue((output / "tool").stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
