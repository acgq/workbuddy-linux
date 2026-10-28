#!/usr/bin/env python3
"""Port the macOS resources, keeping a standard Electron resources layout."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "work/cache"
UPDATE = "https://copilot.tencent.com/v2/update?platform="
PACKAGING_REVISION = 2


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def digest(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).digest()


def download(url, checksum=None, filename=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (filename or url.rsplit("/", 1)[-1])
    if not path.exists():
        partial = path.with_name(path.name + ".partial")
        run("curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
            "--connect-timeout", "30", "--output", partial, url)
        partial.replace(path)
    if checksum and digest(path).hex() != checksum.lower():
        raise ValueError(f"SHA-256 mismatch: {path}; remove this cached download and retry")
    return path


def metadata(platform):
    # Refresh metadata on every build; only binary downloads are cached.
    result = run("curl", "-fsSL", "--retry", "3", "--max-time", "60", UPDATE + platform,
                 capture_output=True, text=True)
    data = json.loads(result.stdout)
    target = platform.removeprefix("workbuddy-")
    pattern = r"https://download\.codebuddy\.cn/workbuddy/saas/" + re.escape(target) + r"/WorkBuddy-" + re.escape(target) + r"-(\d+(?:\.\d+)+)-([0-9a-f]+)\.(zip|deb)"
    match = re.fullmatch(pattern, data["url"])
    if not match or match[1] != data["version"]:
        raise ValueError(f"Unexpected upstream metadata for {platform}: {data}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", data.get("sha256hash", "")):
        raise ValueError(f"Missing source checksum for {platform}")
    data["packageVersion"] = match[1] + "-" + match[2]
    return data


def electron_version(contents):
    candidates = list((contents / "Frameworks/Electron Framework.framework").glob("**/Resources/Info.plist"))
    versions = {plistlib.loads(path.read_bytes())["CFBundleVersion"] for path in candidates}
    if len(versions) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+", next(iter(versions))):
        raise ValueError(f"Cannot determine Electron version from source framework: {versions}")
    return versions.pop()


def safe_path(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError(f"Unsafe archive path: {relative}")
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Archive path escapes destination: {relative}")
    return result


def extract_asar(archive, destination):
    """Extract packed and sidecar files; never create fake native binaries."""
    destination.mkdir(parents=True)
    sidecar = archive.with_name(archive.name + ".unpacked")
    skipped = []
    with archive.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError("Truncated ASAR")
        _, size = struct.unpack("<II", prefix)
        if size < 8 or size > archive.stat().st_size - 8:
            raise ValueError("Invalid ASAR header size")
        header = stream.read(size)
        length = struct.unpack("<I", header[4:8])[0]
        tree = json.loads(header[8:8 + length])

        def walk(files, parent=""):
            for name, entry in files.items():
                if name in ("", ".", "..") or "/" in name:
                    raise ValueError(f"Invalid ASAR entry name: {name}")
                relative = str(PurePosixPath(parent) / name)
                target = safe_path(destination, relative)
                if "files" in entry:
                    target.mkdir(parents=True, exist_ok=True)
                    walk(entry["files"], relative)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if "link" in entry:
                    link = safe_path(destination, entry["link"])
                    target.symlink_to(os.path.relpath(link, target.parent))
                elif entry.get("unpacked"):
                    source = safe_path(sidecar, relative)
                    if source.is_file():
                        shutil.copy2(source, target)
                    elif re.search(r"(?:^|/)(?:darwin[-_]|win32[-_]|freebsd[-_]|openbsd[-_]|musl[-_]|linux[-_])", relative):
                        # The macOS ZIP omits other platforms' sidecars.
                        # Required Linux bindings are restored and loaded below.
                        skipped.append(relative)
                    else:
                        raise FileNotFoundError(f"Required ASAR sidecar is missing: {relative}")
                else:
                    offset, count = int(entry["offset"]), entry["size"]
                    if offset < 0 or count < 0 or 8 + size + offset + count > archive.stat().st_size:
                        raise ValueError(f"Invalid ASAR file bounds: {relative}")
                    stream.seek(8 + size + offset)
                    target.write_bytes(stream.read(count))
                if target.exists() and entry.get("executable"):
                    target.chmod(0o755)
        walk(tree["files"])
    return skipped


def unpack_tar(archive, destination, strip=0):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            parts = PurePosixPath(member.name).parts[strip:]
            if not parts:
                continue
            member.name = str(PurePosixPath(*parts))
            source.extract(member, destination, filter="data")


def pack_unpacked_asar(app, archive):
    """Keep the custom runtime's ASAR-only loading fuse enabled.

    All files live in the sidecar so Node CLI processes can also access them.
    The ASAR provides the standard index used by Electron's module loader.
    """
    def entries(directory):
        result = {}
        for path in sorted(directory.iterdir()):
            if path.is_symlink():
                link = path.resolve()
                if not link.is_relative_to(app.resolve()):
                    raise ValueError(f"ASAR link escapes application: {path}")
                result[path.name] = {"link": str(link.relative_to(app.resolve()))}
            elif path.is_dir():
                result[path.name] = {"files": entries(path), "unpacked": True}
            else:
                result[path.name] = {"size": path.stat().st_size, "unpacked": True}
                if path.stat().st_mode & 0o111:
                    result[path.name]["executable"] = True
        return result

    data = json.dumps({"files": entries(app)}, separators=(",", ":")).encode()
    payload = struct.pack("<I", len(data)) + data
    payload += b"\0" * (-len(payload) % 4)
    header = struct.pack("<I", len(payload)) + payload
    archive.write_bytes(struct.pack("<II", 4, len(header)) + header)


def validate_runtime_version(source_version, runtime_version):
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:-[\w.-]+)?", runtime_version)
    if not match or match[1] != source_version:
        raise ValueError(f"Tencent Linux Electron {runtime_version} does not match source framework {source_version}")


def npm_package(name, version, destination):
    url = "https://registry.npmjs.org/" + name + "/" + version
    info = json.loads(run("curl", "-fsSL", "--retry", "3", url, capture_output=True, text=True).stdout)
    dist = info["dist"]
    archive = download(dist["tarball"])
    algorithm, expected = dist["integrity"].split("-", 1)
    if base64.b64encode(digest(archive, algorithm)).decode() != expected:
        raise ValueError(f"npm integrity mismatch: {name}@{version}")
    if destination.exists():
        shutil.rmtree(destination)
    unpack_tar(archive, destination, strip=1)


def package_json(app, module=None):
    path = app / "node_modules" / module if module else app
    return json.loads((path / "package.json").read_text())


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def patch_linux(app):
    # The new enterprise IM SDK ships only Mach-O/Windows native components.
    # Upstream already has a fallback backend that excludes this extension.
    target = app / "main/daemon-bootstrap.js"
    code = target.read_text()
    pattern = r"(function createDesktopImChannelsBackend\([^)]*\)\{)if\(([^;]+?isOversea===!0)\)return (create(?:Headless|Unavailable)ImChannelsBackend)\(\);"
    code, count = re.subn(pattern, r"\1if(process.platform===`linux`||\2)return \3();", code)
    if count != 1:
        raise ValueError("Upstream enterprise IM startup guard changed; review the Linux patch")
    target.write_text(code)
    return ["Use upstream IM fallback on Linux (native enterprise IM SDK unavailable)"]


def build(args):
    arch = os.environ.get("ARCH", "amd64")
    if arch not in ("amd64", "arm64"):
        raise ValueError(f"Unsupported architecture: {arch}")
    machine = {"amd64": "x64", "arm64": "arm64"}[arch]
    host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(os.uname().machine)
    if host_arch != arch:
        raise ValueError("Build on the target architecture so native-module checks can run")
    if os.environ.get("SOURCE_URL"):
        mac = dict(url=os.environ["SOURCE_URL"], sha256hash=os.environ["SOURCE_SHA256"],
                   packageVersion=os.environ["VERSION"].replace("_", "-"))
    else:
        mac = metadata("workbuddy-darwin-" + machine)
    version = mac["packageVersion"]
    if not re.fullmatch(r"\d+(?:\.\d+)+-[0-9a-f]+", version):
        raise ValueError(f"Invalid package version: {version}")
    (ROOT / "work").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{version}-{arch}-", dir=ROOT / "work"))
    source = download(mac["url"], mac["sha256hash"])
    with zipfile.ZipFile(source) as archive:
        archive.extractall(work / "mac")
    contents = work / "mac/WorkBuddy.app/Contents"
    electron = electron_version(contents)
    override = os.environ.get("ELECTRON_VERSION")
    if override and override != electron:
        raise ValueError(f"ELECTRON_VERSION={override} differs from source framework {electron}")
    print(f"WorkBuddy {version}: source Electron {electron}, Linux {machine}", flush=True)
    # A stock Electron of the same version lacks Tencent's workbuddyStorage
    # API. Preserve the vendor runtime and its storage keys and loading fuses.
    linux = metadata(f"workbuddy-linux-{machine}-deb")
    donor = download(linux["url"], linux["sha256hash"])
    run("dpkg-deb", "-x", donor, work / "linux")
    linux_runtime = work / "linux/opt/WorkBuddy"
    runtime_version = (linux_runtime / "version").read_text().strip()
    validate_runtime_version(electron, runtime_version)
    port = work / "WorkBuddy"
    port.mkdir()
    for entry in linux_runtime.iterdir():
        if entry.name != "resources":
            copy(entry, port / entry.name)
    resources = port / "resources"
    app = resources / "app.asar.unpacked"
    skipped = extract_asar(contents / "Resources/app.asar", app)
    patches = patch_linux(app)
    for name in ["wb-guest-sdk", "conversation-report.html", "startup-report.html"]:
        copy(contents / "Resources" / name, resources / name)

    # UI, main-process code, CLI and prompts all come from the macOS source.
    native = linux_runtime / "resources"
    copy(native / "runtime", resources / "runtime")
    runtime_source = app / "main/shell-env-async.js"
    main_code = (runtime_source if runtime_source.exists() else app / "main/index.js").read_text()
    runtime_versions = {}
    for name, command, pattern in [
        ("node", "node", r'name:`node`,archiveFile:`node\.tar\.gz`,version:`([^`]+)`'),
        ("python", "python3", r'name:`python`,archiveFile:`python\.tar\.gz`,version:`([^`]+)`'),
    ]:
        expected = re.search(pattern, main_code)
        if not expected:
            raise ValueError(f"Source runtime declaration changed: {name}")
        output = run(resources / "runtime" / name / "bin" / command, "--version", capture_output=True, text=True).stdout.strip()
        if output.removeprefix("v").removeprefix("Python ") != expected[1]:
            raise ValueError(f"Linux donor runtime {output} does not match macOS {name} {expected[1]}")
        runtime_versions[name] = expected[1]
    native_cli = native / "app.asar.unpacked/cli"
    for name in [f"ripgrep/{machine}-linux", f"genie-trash/linux-{machine}"]:
        copy(native_cli / "vendor" / name, app / "cli/vendor" / name)

    modules = app / "node_modules"
    executable = port / "workbuddy"
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    abi = run(executable, "-p", "process.versions.modules", env=env, capture_output=True, text=True).stdout.strip()
    sqlite_archive = None
    if "better-sqlite3" in package_json(app)["dependencies"]:
        sqlite_version = package_json(app, "better-sqlite3")["version"]
        sqlite_file = f"better-sqlite3-v{sqlite_version}-electron-v{abi}-linux-{machine}.tar.gz"
        sqlite_archive = download(f"https://github.com/WiseLibs/better-sqlite3/releases/download/v{sqlite_version}/{sqlite_file}")
        unpack_tar(sqlite_archive, modules / "better-sqlite3")
    elif 'require("node:sqlite")' in (app / "main/sqlite.js").read_text():
        sqlite_version = "node:sqlite"
    else:
        raise ValueError("Unknown upstream database backend")
    koffi_version = package_json(app, "koffi")["version"]
    npm_package("koffi", koffi_version, modules / "koffi")
    pty_version = package_json(app, "@lydell/node-pty")["version"]
    pty_module = f"@lydell/node-pty-linux-{machine}"
    npm_package(pty_module, pty_version, modules / pty_module)
    copy(modules / pty_module, app / "cli/node_modules" / pty_module)
    harness_version = None
    if (modules / "@genie/agent-harness").exists():
        harness_name = f"@tencent-ai/agent-harness-linux-{machine}"
        harness_version = package_json(app, "@genie/agent-harness")["optionalDependencies"][harness_name]
        npm_package(harness_name, harness_version, modules / harness_name)
    cli_meta = package_json(app / "cli")
    sandbox_name = f"@tencent-ai/sandbox-cli-linux-{machine}"
    sandbox_version = cli_meta["optionalDependencies"][sandbox_name]
    sandbox = work / "sandbox"
    npm_package(sandbox_name, sandbox_version, sandbox)
    sandbox_target = app / "cli/vendor/sandbox" / sandbox_version
    for binary in (sandbox / "bin").iterdir():
        copy(binary, sandbox_target / binary.name)

    pack_unpacked_asar(app, resources / "app.asar")
    copy(ROOT / "scripts/launch.sh", port / "launch-workbuddy")
    (port / "launch-workbuddy").chmod(0o755)
    copy(ROOT / "README.md", port / "README-linux.md")
    (port / "NOTICE-linux.txt").write_text(
        "Unofficial compatibility package. WorkBuddy and its resources belong to Tencent\n"
        "or their respective rights holders; this package grants no additional license.\n"
        "The MIT license in this repository covers only the packaging scripts.\n"
        "Electron: see LICENSE and LICENSES.chromium.html in this directory.\n"
        "Other third-party notices are retained alongside their packages and runtimes.\n"
        "Sources and platform helper provenance: port-manifest.json.\n")
    manifest = dict(version=version, packagingRevision=PACKAGING_REVISION, source=mac, linuxHelpers=linux,
                    electron=electron, tencentElectron=runtime_version, electronABI=abi, runtimes=runtime_versions,
                    nativeModules=dict(sqlite=sqlite_version, koffi=koffi_version, pty=pty_version, sandbox=sandbox_version,
                                       harness=harness_version),
                    omittedSidecars=skipped, patches=patches,
                    sqliteArtifactSHA256=digest(sqlite_archive).hex() if sqlite_archive else None)
    (port / "port-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    run(executable, ROOT / "scripts/verify-native.cjs", app, env=env)
    run(resources / "runtime/node/bin/node", ROOT / "scripts/verify-native.cjs", app, "--cli")
    if harness_version:
        run(resources / "runtime/node/bin/node", modules / "@genie/agent-harness/bin/agent-harness.js", "--version")
    run(app / f"cli/vendor/ripgrep/{machine}-linux/rg", "--version")
    run(sandbox_target / "sandbox-cli", "--version")

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    outputs = []
    package_version = f"{version}-{PACKAGING_REVISION}"
    if args.format in ("all", "portable"):
        output = dist / f"workbuddy_{package_version}_{arch}.tar.gz"
        run("tar", "-czf", output, "-C", work, "WorkBuddy")
        outputs.append(output)
    if args.format in ("all", "deb"):
        package = work / "deb"
        copy(port, package / "opt/WorkBuddy")
        launch = package / "usr/bin/workbuddy"
        launch.parent.mkdir(parents=True)
        launch.write_text('#!/bin/sh\nexec /opt/WorkBuddy/launch-workbuddy "$@"\n')
        launch.chmod(0o755)
        copy(ROOT / "WorkBuddy.desktop", package / "usr/share/applications/workbuddy.desktop")
        copy(app / "resources/icon.png", package / "usr/share/icons/hicolor/1024x1024/apps/workbuddy.png")
        control = package / "DEBIAN/control"
        control.parent.mkdir()
        size = run("du", "-sk", package, capture_output=True, text=True).stdout.split()[0]
        control.write_text(f"""Package: workbuddy
Version: {package_version}
Architecture: {arch}
Maintainer: acgq
Installed-Size: {size}
Depends: libasound2t64 | libasound2, libatk-bridge2.0-0, libatk1.0-0, libc6 (>= 2.31), libcairo2, libcups2, libdbus-1-3, libdrm2, libexpat1, libgbm1, libglib2.0-0, libgtk-3-0, libnspr4, libnss3, libpango-1.0-0, libx11-6, libxcb1, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxkbcommon0, libxrandr2, libatomic1, libstdc++6, zlib1g, xdg-utils
Recommends: libayatana-appindicator3-1 | libappindicator3-1
Conflicts: workbuddy-port
Replaces: workbuddy-port
Section: utils
Priority: optional
Homepage: https://www.workbuddy.cn/app
Description: Unofficial macOS-to-Linux port of Tencent WorkBuddy
 Includes matching Tencent Linux Electron and native modules.
 Uses the standard WorkBuddy command, installation and profile paths.
""")
        (package / "opt/WorkBuddy/chrome-sandbox").chmod(0o4755)
        copy(ROOT / "LICENSE", package / "usr/share/doc/workbuddy/packaging-LICENSE")
        output = dist / f"workbuddy_{package_version}_{arch}.deb"
        run("dpkg-deb", "--root-owner-group", "--build", package, output)
        outputs.append(output)
    for output in outputs:
        output.with_name(output.name + ".sha256").write_text(digest(output).hex() + "  " + output.name + "\n")
        print(f"Built {output}", flush=True)
    current = ROOT / "work" / f"current-{arch}"
    if current.is_symlink():
        current.unlink()
    current.symlink_to(work.name, target_is_directory=True)
    print(f"Run: {port / 'launch-workbuddy'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["all", "deb", "portable"], default="all")
    parser.add_argument("--metadata", action="store_true", help="Print latest macOS metadata without building")
    args = parser.parse_args()
    if args.metadata:
        data = metadata("workbuddy-darwin-x64")
        data["packagingRevision"] = PACKAGING_REVISION
        print(json.dumps(data, indent=2))
    else:
        build(args)
