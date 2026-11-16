#!/usr/bin/env bash
set -euo pipefail
package_dir=$(realpath "$1")
checkout=$(mktemp -d)
trap 'rm -rf "$checkout"' EXIT
repository=${AUR_REPOSITORY:-ssh://aur@aur.archlinux.org/workbuddy-cn-bin.git}

git -c init.defaultBranch=master clone -- "$repository" "$checkout"
cd "$checkout"

package_version() {
  awk '$1 == "pkgver" { version=$3 } $1 == "pkgrel" { revision=$3 } END { print version "-" revision }' "$1"
}

candidate=$(package_version "$package_dir/.SRCINFO")
if [[ -f .SRCINFO ]]; then
  current=$(package_version .SRCINFO)
  if (( $(vercmp "$candidate" "$current") < 0 )); then
    echo "AUR already has newer version $current; skipping $candidate"
    exit 0
  fi
fi

for name in PKGBUILD .SRCINFO WorkBuddy.desktop workbuddy-launcher LICENSE; do
  cp "$package_dir/$name" "$name"
done
git add PKGBUILD .SRCINFO WorkBuddy.desktop workbuddy-launcher LICENSE
if git diff --cached --quiet; then
  echo "AUR is already up to date ($candidate)"
  exit 0
fi
git -c user.name=acgq -c user.email=chen330021@live.com commit -m "Update workbuddy-cn-bin to $candidate"
git push origin HEAD:master
