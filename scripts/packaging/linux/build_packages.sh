#!/usr/bin/env bash
# Package a Linux build as RiftLift-<version>-linux-x86_64.tar.gz and .AppImage
# (the name the launcher's updater looks for).
#
#   scripts/packaging/linux/build_packages.sh [--build DIR] [--out DIR] [--version X.Y.Z] [FORMAT...]
#   FORMAT: tar appimage (default: both)
#
# The AppImage needs appimagetool (APPIMAGETOOL, default appimagetool on PATH).
# The layout gets licenses/ for everything linked into the programs
# (../release_notices.py) and is audited for build paths and identities
# (../release_audit.py) before it is archived. Run it where the build was made
# (the baseline container for releases): the statically linked OpenSSL and
# libc++ notices come from that system's package documentation.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
build=$root/build/linux-release
out=$root/dist
version=$(sed -n 's/^project(RiftLift VERSION \([0-9.]*\).*/\1/p' "$root/CMakeLists.txt")
formats=()
while [ $# -gt 0 ]; do
  case "$1" in
    --build) build=$(realpath "$2"); shift 2 ;;
    --out) out=$(realpath -m "$2"); shift 2 ;;
    --version) version=$2; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) formats+=("$1"); shift ;;
  esac
done
[ ${#formats[@]} -gt 0 ] || formats=(tar appimage)
for binary in riftlift riftlift-cli riftlift-runtime; do
  [ -x "$build/bin/$binary" ] || { echo "Missing $build/bin/$binary; build the linux-release preset first." >&2; exit 1; }
done
id=io.github.villagers654.RiftLift
base=RiftLift-$version-linux-x86_64
work=$out/.work
rm -rf "$work"; mkdir -p "$work" "$out"

# The release layout: the three programs side by side (the launcher finds
# riftlift-runtime beside itself), plus the licenses (licenses/<component>/).
layout=$work/RiftLift-linux64
mkdir -p "$layout"
strip=${STRIP:-strip}
for binary in riftlift riftlift-cli riftlift-runtime; do
  install -m755 "$build/bin/$binary" "$layout/$binary"
  "$strip" --strip-unneeded "$layout/$binary"
done
install -m644 "$root/LICENSE" "$root/THIRD_PARTY_NOTICES.md" "$layout/"
python3 "$here/../release_notices.py" --platform linux --build "$build" --layout "$layout"
python3 "$here/../release_audit.py" --build "$build" "$layout"

desktop_files() {  # DEST: share/{applications,icons,metainfo}
  install -Dm644 "$root/assets/$id.desktop" "$1/applications/$id.desktop"
  install -Dm644 "$root/assets/$id.svg" "$1/icons/hicolor/scalable/apps/$id.svg"
  install -d "$1/metainfo"
  sed "s/@VERSION@/$version/" "$here/$id.metainfo.xml" > "$1/metainfo/$id.metainfo.xml"
}

do_tar() {
  local stage=$work/tar/RiftLift-linux64
  mkdir -p "$stage"; cp -a "$layout/." "$stage/"
  desktop_files "$stage/share"
  tar -C "$work/tar" --owner=0 --group=0 --sort=name -czf "$out/$base.tar.gz" RiftLift-linux64
  echo "tar: $out/$base.tar.gz"
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

for format in "${formats[@]}"; do "do_$format"; done
rm -rf "$work"
