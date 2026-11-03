#!/usr/bin/env bash
# Package a Linux build as RiftLift-<version>-linux-x86_64.{tar.gz,deb,rpm,AppImage,flatpak}
# and an AUR PKGBUILD (riftlift-bin). The AppImage keeps the name the launcher's
# updater looks for.
#
#   scripts/packaging/linux/build_packages.sh [--build DIR | --layout DIR] [--out DIR] [--version X.Y.Z] [FORMAT...]
#   FORMAT: tar deb rpm aur appimage flatpak (default: all)
#
# Without --layout, the release layout is made from the build in DIR (default
# build/linux-release) as $out/RiftLift-linux64: the three programs, licenses/
# for everything linked into them (../release_notices.py), audited for build
# paths and identities (../release_audit.py). Make the layout where the build was
# made (the baseline container for releases): the statically linked OpenSSL and
# libc++ notices come from that system's package documentation. --layout reuses
# one, so the other formats can be made outside that container.
#
# deb and rpm use dpkg-deb and rpmbuild when installed, otherwise a container
# (CONTAINER, default podman: Ubuntu 22.04 and Fedora images). The AppImage needs
# appimagetool (APPIMAGETOOL, default appimagetool on PATH); the Flatpak needs
# flatpak with org.gnome.Platform//$FLATPAK_BRANCH (default 51) installed.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
build=$root/build/linux-release
layout=
out=$root/dist
version=$(sed -n 's/^project(RiftLift VERSION \([0-9.]*\).*/\1/p' "$root/CMakeLists.txt")
formats=()
while [ $# -gt 0 ]; do
  case "$1" in
    --build) build=$(realpath "$2"); shift 2 ;;
    --layout) layout=$(realpath "$2"); shift 2 ;;
    --out) out=$(realpath -m "$2"); shift 2 ;;
    --version) version=$2; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) formats+=("$1"); shift ;;
  esac
done
[ ${#formats[@]} -gt 0 ] || formats=(tar deb rpm aur appimage flatpak)
id=io.github.villagers654.RiftLift
base=RiftLift-$version-linux-x86_64
container=${CONTAINER:-podman}
deb_image=${RIFTLIFT_DEB_IMAGE:-docker.io/library/ubuntu:22.04}
fedora_image=${RIFTLIFT_FEDORA_IMAGE:-registry.fedoraproject.org/fedora:44}
flatpak_branch=${FLATPAK_BRANCH:-51}
work=$out/.work
rm -rf "$work"; mkdir -p "$work" "$out"

# The release layout: the three programs side by side (the launcher finds
# riftlift-runtime beside itself), plus the licenses (licenses/<component>/).
if [ -z "$layout" ]; then
  for binary in riftlift riftlift-cli riftlift-runtime; do
    [ -x "$build/bin/$binary" ] || { echo "Missing $build/bin/$binary; build the linux-release preset first." >&2; exit 1; }
  done
  layout=$out/RiftLift-linux64
  rm -rf "$layout"; mkdir -p "$layout"
  strip=${STRIP:-strip}
  for binary in riftlift riftlift-cli riftlift-runtime; do
    install -m755 "$build/bin/$binary" "$layout/$binary"
    "$strip" --strip-unneeded "$layout/$binary"
  done
  install -m644 "$root/LICENSE" "$root/THIRD_PARTY_NOTICES.md" "$layout/"
  python3 "$here/../release_notices.py" --platform linux --build "$build" --layout "$layout"
  python3 "$here/../release_audit.py" --build "$build" "$layout"
fi
for binary in riftlift riftlift-cli riftlift-runtime; do
  [ -x "$layout/$binary" ] || { echo "Not a release layout: $layout" >&2; exit 1; }
done

in_container() {  # IMAGE CMD... with the sources, the layout and $out mounted at their own paths
  local image=$1; shift
  "$container" run --rm --security-opt label=disable -v "$root:$root:ro" -v "$layout:$layout:ro" \
    -v "$out:$out" -w "$out" "$image" "$@"
}

# Desktop integration files shared by every format.
desktop_files() {  # DEST: share/{applications,icons,metainfo}
  install -Dm644 "$root/assets/$id.desktop" "$1/applications/$id.desktop"
  install -Dm644 "$root/assets/$id.svg" "$1/icons/hicolor/scalable/apps/$id.svg"
  install -d "$1/metainfo"
  sed "s/@VERSION@/$version/" "$here/$id.metainfo.xml" > "$1/metainfo/$id.metainfo.xml"
}
# FHS tree for deb/rpm: /opt/riftlift, /usr/bin/riftlift{,-cli} and the desktop files.
fhs_tree() {
  mkdir -p "$1/opt" "$1/usr/bin"
  cp -a "$layout" "$1/opt/riftlift"
  ln -s ../../opt/riftlift/riftlift "$1/usr/bin/riftlift"
  ln -s ../../opt/riftlift/riftlift-cli "$1/usr/bin/riftlift-cli"
  desktop_files "$1/usr/share"
}

do_tar() {
  local stage=$work/tar/RiftLift-linux64
  mkdir -p "$stage"; cp -a "$layout/." "$stage/"
  desktop_files "$stage/share"
  # Reproducible: fixed times, owners and order, and no gzip timestamp.
  tar -C "$work/tar" --owner=0 --group=0 --numeric-owner --sort=name --mtime=@0 -cf - RiftLift-linux64 \
    | gzip -n -9 > "$out/$base.tar.gz"
  echo "tar: $out/$base.tar.gz"
}

do_deb() {
  local stage=$work/deb
  fhs_tree "$stage"
  install -Dm644 "$root/LICENSE" "$stage/usr/share/doc/riftlift/copyright"
  mkdir -p "$stage/DEBIAN"
  cat > "$stage/DEBIAN/control" <<EOF
Package: riftlift
Version: $version
Architecture: amd64
Maintainer: RiftLift developers <https://github.com/Villagers654/RiftLift/issues>
Installed-Size: $(du -sk "$stage" | cut -f1)
Depends: libc6 (>= 2.35), python3
Recommends: libwebkitgtk-6.0-4 | libwebkit2gtk-4.1-0, libsecret-1-0, libvulkan1, libwayland-client0, libwayland-egl1, libxkbcommon0, libegl1, libfontconfig1
Section: games
Priority: optional
Homepage: https://github.com/Villagers654/RiftLift
Description: Play your Meta Rift games on Linux
 RiftLift installs the Rift / PC VR games you own on Meta and runs them
 through your OpenXR runtime or SteamVR, alongside Steam games with an Oculus
 mode and Rift games you installed yourself. Games run under GE-Proton, which
 RiftLift downloads the first time you play.
EOF
  if command -v dpkg-deb >/dev/null; then
    dpkg-deb --root-owner-group -Zxz -b "$stage" "$out/$base.deb" >/dev/null
  else
    in_container "$deb_image" dpkg-deb --root-owner-group -Zxz -b "$stage" "$out/$base.deb" >/dev/null
  fi
  echo "deb: $out/$base.deb"
}

do_rpm() {
  local stage=$work/rpm build_rpm
  fhs_tree "$stage/root"
  sed -e "s/@VERSION@/$version/" "$here/riftlift.spec" > "$stage/riftlift.spec"
  build_rpm="rpmbuild -bb --quiet --define '_topdir $stage/top' --define 'stage $stage/root' \
    --define '_build_id_links none' $stage/riftlift.spec"
  if command -v rpmbuild >/dev/null; then
    bash -c "$build_rpm"
  else
    in_container "$fedora_image" bash -c "set -e; (command -v rpmbuild || dnf -y -q install rpm-build) >/dev/null 2>&1
      $build_rpm"
  fi
  mv "$stage"/top/RPMS/x86_64/riftlift-*.rpm "$out/$base.rpm"
  echo "rpm: $out/$base.rpm"
}

do_aur() {
  [ -f "$out/$base.tar.gz" ] || do_tar
  local sum aur=$out/aur
  sum=$(sha256sum "$out/$base.tar.gz" | cut -d' ' -f1)
  mkdir -p "$aur"
  sed -e "s/@VERSION@/$version/g" -e "s/@SHA256@/$sum/g" "$here/PKGBUILD.in" > "$aur/PKGBUILD"
  cat > "$aur/.SRCINFO" <<EOF
pkgbase = riftlift-bin
	pkgdesc = Play your Meta Rift games on Linux (prebuilt release)
	pkgver = $version
	pkgrel = 1
	url = https://github.com/Villagers654/RiftLift
	arch = x86_64
	license = MIT
	depends = glibc>=2.35
	depends = python
	optdepends = webkitgtk-6.0: Meta sign-in window
	optdepends = libsecret: keep sign-ins in the desktop keyring
	optdepends = vulkan-icd-loader: Vulkan loader (required to run games)
	optdepends = fontconfig: system fonts in the launcher
	provides = riftlift
	conflicts = riftlift
	options = !strip
	options = !debug
	source = $base.tar.gz::https://github.com/Villagers654/RiftLift/releases/download/v$version/$base.tar.gz
	sha256sums = $sum

pkgname = riftlift-bin
EOF
  echo "aur: $aur/PKGBUILD"
}

do_appimage() {
  local appdir=$work/RiftLift.AppDir tool=${APPIMAGETOOL:-appimagetool}
  mkdir -p "$appdir/usr"
  cp -a "$layout" "$appdir/usr/riftlift"
  desktop_files "$appdir/usr/share"
  cp "$appdir/usr/share/applications/$id.desktop" "$appdir/$id.desktop"
  cp "$appdir/usr/share/icons/hicolor/scalable/apps/$id.svg" "$appdir/$id.svg"
  ln -s "$id.svg" "$appdir/.DirIcon"
  cat > "$appdir/AppRun" <<'APPRUN'
#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
# "riftlift-cli ..." runs the command line instead of the window.
if [ "${1:-}" = cli ]; then
  shift
  exec "$here/usr/riftlift/riftlift-cli" "$@"
fi
exec "$here/usr/riftlift/riftlift" "$@"
APPRUN
  chmod +x "$appdir/AppRun"
  ARCH=x86_64 VERSION=$version "$tool" --no-appstream "$appdir" "$out/$base.AppImage" >/dev/null
  echo "appimage: $out/$base.AppImage"
}

do_flatpak() {
  # Binary repackaging needs no SDK build step: the flatpak CLI alone (build-init,
  # build-finish, build-export) assembles the app from the layout, with the
  # finish-args of io.github.villagers654.RiftLift.yml (which flatpak-builder can also use).
  local fp=$work/flatpak app args
  app=$fp/app
  flatpak build-init "$app" "$id" org.gnome.Platform org.gnome.Platform "$flatpak_branch" >/dev/null
  cp -a "$layout" "$app/files/riftlift"
  install -Dm755 "$here/riftlift-flatpak.sh" "$app/files/bin/riftlift-flatpak.sh"
  ln -s ../riftlift/riftlift-cli "$app/files/bin/riftlift-cli"
  desktop_files "$app/files/share"
  sed -i "s/^Exec=riftlift /Exec=riftlift-flatpak.sh /" "$app/files/share/applications/$id.desktop"
  mapfile -t args < <(sed -n 's/^  - \(--[a-z-]*=.*\|--[a-z-]*\)$/\1/p' "$here/$id.yml")
  flatpak build-finish "$app" --command=riftlift-flatpak.sh "${args[@]}" >/dev/null
  # OSTree refuses writes when less than 3% of the disk is free; a bundle's
  # throwaway repository only needs room for itself.
  if command -v ostree >/dev/null; then
    ostree init --repo="$fp/repo" --mode=archive-z2
    ostree config --repo="$fp/repo" set core.min-free-space-size 256MB
  fi
  flatpak build-export "$fp/repo" "$app" stable >/dev/null
  # Installing the bundle fetches its runtime from Flathub when it is missing.
  flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo "$fp/repo" "$out/$base.flatpak" "$id" stable
  echo "flatpak: $out/$base.flatpak"
}

for format in "${formats[@]}"; do "do_$format"; done
rm -rf "$work"
