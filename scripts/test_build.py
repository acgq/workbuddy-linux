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

    def test_unindexed_sidecars_survive_extraction_and_repacking(self):
        archive = self.archive({"main": {"files": {
            "index.js": {"offset": "0", "size": 5},
        }}}, b"hello")
        sidecar = self.root / "app.asar.unpacked"
        preload = sidecar / "main/preload"
        preload.mkdir(parents=True)
        for name in ("fork-preload.cjs", "background-worker.cjs"):
            (preload / name).write_text(f"// {name}")
        # An unindexed executable must retain its permissions as well.
        tool = sidecar / "helper"
        tool.write_bytes(b"ELF")
        tool.chmod(0o755)
        # A stale sidecar must not override a packed entry.
        (sidecar / "main/index.js").write_text("stale")
        output = self.root / "out"
        self.assertEqual(extract_asar(archive, output), [])
        self.assertEqual((output / "main/index.js").read_text(), "hello")
        self.assertTrue((output / "helper").stat().st_mode & 0o111)
        repacked = self.root / "out.asar"
        output.rename(self.root / "out.asar.unpacked")
        pack_unpacked_asar(self.root / "out.asar.unpacked", repacked)
        final = self.root / "final"
        extract_asar(repacked, final)
        for name in ("fork-preload.cjs", "background-worker.cjs"):
            self.assertEqual((final / "main/preload" / name).read_text(), f"// {name}")

    def test_unindexed_sidecar_links_stay_inside_application(self):
        archive = self.archive({})
        sidecar = self.root / "app.asar.unpacked"
        sidecar.mkdir()
        (sidecar / "worker.cjs").write_text("// worker")
        (sidecar / "alias.cjs").symlink_to("worker.cjs")
        output = self.root / "out"
        extract_asar(archive, output)
        self.assertTrue((output / "alias.cjs").is_symlink())
        self.assertEqual((output / "alias.cjs").resolve(), output / "worker.cjs")

    def test_unindexed_sidecar_link_cannot_escape_source(self):
        archive = self.archive({})
        sidecar = self.root / "app.asar.unpacked"
        sidecar.mkdir()
        (self.root / "outside.cjs").write_text("outside")
        (sidecar / "escape.cjs").symlink_to("../outside.cjs")
        with self.assertRaises(ValueError):
            extract_asar(archive, self.root / "out")

    def test_missing_application_sidecar_fails(self):
        archive = self.archive({"main.js": {"unpacked": True, "size": 3}})
        with self.assertRaises(FileNotFoundError):
            extract_asar(archive, self.root / "out")

    def test_absent_macos_framework_signature_is_reported_without_placeholder(self):
        relative = ("node_modules/@tencent/qimei-node/build/Release/QimeiSDKMac.xcframework/"
                    "macos-arm64_x86_64/QimeiSDKMac.framework/_CodeSignature/CodeRequirements-1")
        entry = {"unpacked": True, "size": 3}
        for name in reversed(relative.split("/")):
            entry = {"files": {name: entry}}
        archive = self.archive(entry["files"])
        output = self.root / "out"
        self.assertEqual(extract_asar(archive, output), [relative])
        self.assertFalse((output / relative).exists())

    def test_missing_framework_binary_still_fails(self):
        archive = self.archive({"QimeiSDKMac.framework": {"files": {
            "QimeiSDKMac": {"unpacked": True, "size": 3},
        }}})
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

    def test_im_patch_preserves_the_upstream_fallback_for_both_versions(self):
        main = self.root / "main"
        main.mkdir()
        target = main / "daemon-bootstrap.js"
        for fallback in ("createHeadlessImChannelsBackend", "createUnavailableImChannelsBackend"):
            with self.subTest(fallback=fallback):
                target.write_text("function createDesktopImChannelsBackend(e){"
                                  f"if(config()?.isOversea===!0)return {fallback}();"
                                  "return nativeBackend(e)}")
                patch_linux(self.root)
                self.assertEqual(target.read_text(), "function createDesktopImChannelsBackend(e){"
                                 f"if(process.platform===`linux`||config()?.isOversea===!0)return {fallback}();"
                                 "return nativeBackend(e)}")

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
