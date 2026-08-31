#!/usr/bin/env bash
set -euo pipefail

VERSION="${VERSION:?VERSION is required (for example 5.3.14.36279234_825709d4)}"
SOURCE_URL="${SOURCE_URL:?SOURCE_URL is required}"
ELECTRON_VERSION="${ELECTRON_VERSION:-44.0.0}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"

case "$ARCH" in
  amd64) electron_arch=x64; node_pty_arch=x64 ;;
  arm64) electron_arch=arm64; node_pty_arch=arm64 ;;
  *) echo "Unsupported Debian architecture: $ARCH" >&2; exit 1 ;;
esac

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work_dir="$root_dir/work/$ARCH"
dist_dir="$root_dir/dist"
package_root="$work_dir/package"
upstream_version=${VERSION%%_*}
deb_version=${VERSION//_/-}

rm -rf "$work_dir"
mkdir -p "$work_dir/downloads" "$package_root/opt/workbuddy" \
  "$package_root/usr/bin" "$package_root/usr/share/applications" \
  "$package_root/usr/share/icons/hicolor/1024x1024/apps" \
  "$package_root/usr/share/doc/workbuddy" "$package_root/DEBIAN" "$dist_dir"

download() {
  local url=$1 output=$2
  curl --fail --location --retry 3 --output "$output" "$url"
}

app_zip="$work_dir/downloads/workbuddy.zip"
download "$SOURCE_URL" "$app_zip"
unzip -tq "$app_zip"
unzip -q "$app_zip" -d "$work_dir/app"
resources="$work_dir/app/WorkBuddy.app/Contents/Resources"

if [[ ! -f "$resources/app.asar" ]]; then
  echo "Upstream archive does not contain WorkBuddy.app/Contents/Resources/app.asar" >&2
  exit 1
fi

# ASAR entries marked as unpacked are stored beside app.asar and must be present
# while extracting. Merge both parts exactly as Electron expects at runtime.
if [[ -d "$resources/app.asar.unpacked" ]]; then
  cp -a "$resources/app.asar.unpacked" "$package_root/opt/workbuddy/app"
fi
# The upstream ASAR header references a few unpacked files that are absent from
# the macOS ZIP. asar reports ENOENT after extracting the available application;
# this is also tolerated by the AUR recipe.
npx --yes asar@3.2.0 extract "$resources/app.asar" "$package_root/opt/workbuddy/app" || true
if [[ ! -f "$package_root/opt/workbuddy/app/main/index.js" ]]; then
  echo "ASAR extraction did not produce main/index.js" >&2
  exit 1
fi

better_sqlite="$work_dir/downloads/better-sqlite3.tgz"
node_pty="$work_dir/downloads/node-pty.tgz"
download "https://registry.npmjs.org/better-sqlite3/-/better-sqlite3-13.0.3.tgz" "$better_sqlite"
download "https://registry.npmjs.org/@lydell/node-pty-linux-${node_pty_arch}/-/node-pty-linux-${node_pty_arch}-1.2.0-beta.14.tgz" "$node_pty"
rm -rf "$package_root/opt/workbuddy/app/node_modules/better-sqlite3" \
  "$package_root/opt/workbuddy/app/node_modules/@lydell/node-pty-linux-x64" \
  "$package_root/opt/workbuddy/app/node_modules/@lydell/node-pty-linux-arm64"
mkdir -p "$package_root/opt/workbuddy/app/node_modules/better-sqlite3" \
  "$package_root/opt/workbuddy/app/node_modules/@lydell/node-pty-linux-${node_pty_arch}"
tar -xzf "$better_sqlite" --strip-components=1 -C "$package_root/opt/workbuddy/app/node_modules/better-sqlite3"
tar -xzf "$node_pty" --strip-components=1 -C "$package_root/opt/workbuddy/app/node_modules/@lydell/node-pty-linux-${node_pty_arch}"

# Match the two portability patches maintained by the AUR package.
main_js="$package_root/opt/workbuddy/app/main/index.js"
sed -i 's/tray\.on("right-click", () => this\.tray?\.popUpContextMenu(contextMenu))/tray.setContextMenu(contextMenu)/g' "$main_js"
while IFS= read -r -d '' file; do
  sed -i "s#process\.resourcesPath#'/opt/workbuddy'#g" "$file"
done < <(grep -IlZR --null 'process\.resourcesPath' "$package_root/opt/workbuddy/app")

electron_zip="$work_dir/downloads/electron.zip"
download "https://github.com/electron/electron/releases/download/v${ELECTRON_VERSION}/electron-v${ELECTRON_VERSION}-linux-${electron_arch}.zip" "$electron_zip"
mkdir -p "$package_root/opt/workbuddy/electron"
unzip -q "$electron_zip" -d "$package_root/opt/workbuddy/electron"
chmod 4755 "$package_root/opt/workbuddy/electron/chrome-sandbox"

install -Dm644 "$root_dir/WorkBuddy.desktop" "$package_root/usr/share/applications/workbuddy.desktop"
icon="$package_root/opt/workbuddy/app/resources/icon.png"
[[ -f "$icon" ]] && install -Dm644 "$icon" "$package_root/usr/share/icons/hicolor/1024x1024/apps/workbuddy.png"

cat > "$package_root/usr/bin/workbuddy" <<'EOF'
#!/bin/sh
exec /opt/workbuddy/electron/electron /opt/workbuddy/app "$@"
EOF
chmod 0755 "$package_root/usr/bin/workbuddy"

installed_size=$(du -sk "$package_root" | cut -f1)
cat > "$package_root/DEBIAN/control" <<EOF
Package: workbuddy
Version: ${deb_version}
Architecture: ${ARCH}
Maintainer: acgq
Installed-Size: ${installed_size}
Depends: libasound2t64 | libasound2, libatk-bridge2.0-0, libatk1.0-0, libc6, libcairo2, libcups2, libdbus-1-3, libdrm2, libexpat1, libgbm1, libglib2.0-0, libgtk-3-0, libnspr4, libnss3, libpango-1.0-0, libx11-6, libxcb1, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxkbcommon0, libxrandr2
Recommends: libappindicator3-1 | libayatana-appindicator3-1
Section: utils
Priority: optional
Homepage: https://www.workbuddy.cn/app
Description: Unofficial Linux package of Tencent WorkBuddy
 WorkBuddy is an AI Agent office tool from Tencent Cloud Code Assistant.
 This package ports the upstream macOS application resources to Linux.
EOF

cat > "$package_root/usr/share/doc/workbuddy/copyright" <<EOF
This is an unofficial redistribution/compatibility package.
WorkBuddy and its application resources are proprietary software owned by Tencent
or their respective rights holders. No additional license is granted by this package.
Upstream: https://www.workbuddy.cn/app
AUR packaging reference: https://aur.archlinux.org/packages/workbuddy
Packaged upstream version: ${upstream_version}
Electron license: https://github.com/electron/electron/blob/main/LICENSE
EOF

find "$package_root" -type d -exec chmod 0755 {} +
output="$dist_dir/workbuddy_${deb_version}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$package_root" "$output"
sha256sum "$output" > "$output.sha256"
echo "Built $output"
