#!/usr/bin/env bash
# The Linux release baseline (Containerfile beside this script, glibc 2.35).
#
#   scripts/packaging/linux/baseline.sh image        # build the container image
#   scripts/packaging/linux/baseline.sh run CMD...   # run CMD in the checkout, in the container
#   scripts/packaging/linux/baseline.sh release      # configure, build, test, tarball + AppImage as CI does
#   (then build_packages.sh --layout dist/RiftLift-linux64 deb rpm aur flatpak on the host)
#
# The checkout is mounted at its own path and commands run as the calling user,
# so build trees, caches and outputs are the same inside and outside.
# Environment: CONTAINER (podman or docker; default podman when installed),
# RIFTLIFT_BASELINE_IMAGE (default localhost/riftlift-baseline), CCACHE_DIR
# (mounted and used when set, with CCACHE_MAXSIZE), APPIMAGETOOL and
# APPIMAGE_EXTRACT_AND_RUN (passed through for the AppImage).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
image=${RIFTLIFT_BASELINE_IMAGE:-localhost/riftlift-baseline}
engine=${CONTAINER:-$(command -v podman >/dev/null && echo podman || echo docker)}

run() {
  local home=$root/build/.baseline-home args=()
  mkdir -p "$home"
  if [ "$engine" = podman ]; then
    args+=(--userns=keep-id --security-opt label=disable)
  else
    args+=(--user "$(id -u):$(id -g)")
  fi
  if [ -n "${CCACHE_DIR:-}" ]; then
    mkdir -p "$CCACHE_DIR"
    args+=(-v "$CCACHE_DIR:$CCACHE_DIR" -e CCACHE_DIR -e CCACHE_BASEDIR="$root" -e CCACHE_NOHASHDIR=1
           -e CCACHE_COMPILERCHECK=content -e CCACHE_MAXSIZE="${CCACHE_MAXSIZE:-2G}")
  fi
  for name in APPIMAGETOOL APPIMAGE_EXTRACT_AND_RUN; do
    [ -z "${!name:-}" ] || args+=(-e "$name")
  done
  "$engine" run --rm "${args[@]}" -v "$root:$root" -w "$root" -e HOME="$home" "$image" "$@"
}

case "${1:-}" in
  image) "$engine" build -t "$image" -f "$here/Containerfile" "$here" ;;
  run) shift; run "$@" ;;
  release)
    run bash -c "cmake --preset linux-baseline -DRIFTLIFT_BUILD_TESTS=ON && cmake --build build/linux-baseline &&
      ctest --test-dir build/linux-baseline --output-on-failure &&
      scripts/packaging/linux/build_packages.sh --build build/linux-baseline --out dist tar appimage" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
