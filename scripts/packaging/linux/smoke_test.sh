#!/usr/bin/env bash
# Install and start each Linux package in a bare container of its target
# distribution (no GPU, no headset): `riftlift --version`, `riftlift-cli --version`
# and `riftlift-runtime --check`. Also lists, for every program in the layout,
# each library the system has to provide.
#
#   scripts/packaging/linux/smoke_test.sh [--layout DIR] [--packages DIR] [TEST...]
#   TEST: ldd deb rpm aur appimage flatpak (default: all)
#
# Needs podman (CONTAINER to use another engine) and, for the Flatpak, flatpak
# with org.gnome.Platform//51 installed.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../../.." && pwd)
layout=$root/dist/RiftLift-linux64
packages=$root/dist
container=${CONTAINER:-podman}
tests=()
while [ $# -gt 0 ]; do
  case "$1" in
    --layout) layout=$(realpath "$2"); shift 2 ;;
    --packages) packages=$(realpath "$2"); shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) tests+=("$1"); shift ;;
  esac
done
[ ${#tests[@]} -gt 0 ] || tests=(ldd deb rpm aur appimage flatpak)
pkg() { ls "$packages"/RiftLift-*-linux-x86_64."$1" 2>/dev/null | head -n 1; }
failures=0
result() { echo "== $1: $2"; [ "$2" = PASS ] || failures=$((failures + 1)); }
box() {  # IMAGE SCRIPT: run SCRIPT in a bare container with the inputs mounted read-only
  "$container" run --rm --network=host --security-opt label=disable -v "$layout:/layout:ro" \
    -v "$packages:/packages:ro" "$1" bash -c "$2"
}

# $1 = installed layout directory inside the container. The component check
# reports what is missing (no Steam, no Proton yet) but must run.
checks='
set -e; d=$1
"$d/riftlift" --version
"$d/riftlift-cli" --version
"$d/riftlift-runtime" --check | head -n 3
'

ldd_check='
rm -f /tmp/missing
for f in /layout/riftlift /layout/riftlift-cli /layout/riftlift-runtime; do
  out=$(ldd "$f" 2>&1) || true
  for lib in $(echo "$out" | sed -n "s/^[[:space:]]*\([^ ]*\) => not found.*/\1/p"); do
    echo "MISSING $lib (needed by ${f#/layout/})"; touch /tmp/missing
  done
  echo "$out" | sed -n "s/^[[:space:]]*\([^ ]*\) => \(\/[^ ]*\).*/\1/p"
done | sort | uniq -c | sort -rn > /tmp/system-libs
echo "system libraries linked (count of programs):"; cat /tmp/system-libs
[ ! -e /tmp/missing ]'

for t in "${tests[@]}"; do case $t in
  ldd)
    for image in docker.io/library/ubuntu:22.04 docker.io/library/ubuntu:24.04 docker.io/library/debian:12 \
                 registry.fedoraproject.org/fedora:41 docker.io/library/archlinux:latest docker.io/opensuse/tumbleweed:latest; do
      echo "--- ldd + start, bare $image"
      if box "$image" "command -v ldd >/dev/null || zypper -nq in glibc-devel >/dev/null 2>&1
          ( $ldd_check ) && bash -c '$checks' _ /layout"
      then result "layout on $image" PASS; else result "layout on $image" FAIL; fi
    done ;;
  deb)
    for image in docker.io/library/ubuntu:22.04 docker.io/library/ubuntu:24.04 docker.io/library/debian:12; do
      echo "--- deb on $image (no recommends)"
      if box "$image" "set -e; export DEBIAN_FRONTEND=noninteractive; apt-get -qq update >/dev/null
          cp /packages/$(basename "$(pkg deb)") /tmp/r.deb; apt-get -qq install -y --no-install-recommends /tmp/r.deb >/dev/null
          dpkg -s riftlift | grep -E '^(Version|Depends):'; command -v riftlift riftlift-cli; bash -c '$checks' _ /opt/riftlift"
      then result "deb on $image" PASS; else result "deb on $image" FAIL; fi
    done ;;
  rpm)
    for image in registry.fedoraproject.org/fedora:41 registry.fedoraproject.org/fedora:44 docker.io/opensuse/tumbleweed:latest; do
      echo "--- rpm on $image (no weak deps)"
      if box "$image" "set -e; f=/packages/$(basename "$(pkg rpm)")
          if command -v dnf >/dev/null; then dnf -y -q install --setopt=install_weak_deps=False \$f >/dev/null
          else zypper -nq --no-gpg-checks in --no-recommends --allow-unsigned-rpm \$f >/dev/null; fi
          rpm -q riftlift; command -v riftlift riftlift-cli; bash -c '$checks' _ /opt/riftlift"
      then result "rpm on $image" PASS; else result "rpm on $image" FAIL; fi
    done ;;
  aur)
    echo "--- AUR riftlift-bin with makepkg -si on archlinux"
    if box docker.io/library/archlinux:latest "set -e; pacman -Sqyu --noconfirm --needed base-devel >/dev/null 2>&1
        useradd -m build; echo 'build ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/build
        cp -r /packages/aur /home/build/riftlift-bin; cp /packages/$(basename "$(pkg tar.gz)") /home/build/riftlift-bin/
        chown -R build /home/build/riftlift-bin
        su build -c 'cd ~/riftlift-bin && makepkg -si --noconfirm >/dev/null 2>&1'
        pacman -Qi riftlift-bin | grep -E '^(Version|Depends On)'; command -v riftlift riftlift-cli; bash -c '$checks' _ /opt/riftlift"
    then result "AUR on archlinux" PASS; else result "AUR on archlinux" FAIL; fi ;;
  appimage)
    echo "--- AppImage on bare ubuntu:22.04 (no FUSE: --appimage-extract-and-run)"
    if box docker.io/library/ubuntu:22.04 "set -e; cp /packages/$(basename "$(pkg AppImage)") /tmp/r.AppImage
        /tmp/r.AppImage --appimage-extract-and-run --version
        /tmp/r.AppImage --appimage-extract-and-run cli --version"
    then result "AppImage on ubuntu:22.04" PASS; else result "AppImage on ubuntu:22.04" FAIL; fi ;;
  flatpak)
    echo "--- Flatpak bundle, user installation (a throwaway FLATPAK_USER_DIR; runtime from the system)"
    id=io.github.villagers654.RiftLift
    probe=$(mktemp -d)
    export FLATPAK_USER_DIR=$probe/flatpak-user
    mkdir -p "$FLATPAK_USER_DIR"
    if command -v ostree >/dev/null; then  # OSTree's 3%-free-disk guard, for this throwaway repo only
      ostree init --repo="$FLATPAK_USER_DIR/repo" --mode=bare-user-only
      ostree config --repo="$FLATPAK_USER_DIR/repo" set core.min-free-space-size 256MB
    fi
    if flatpak --user install -y --noninteractive --bundle "$(pkg flatpak)" >/dev/null && \
       flatpak run --command=/app/riftlift/riftlift $id --version && \
       flatpak run --command=riftlift-cli $id --version && \
       flatpak run --command=/app/riftlift/riftlift-runtime $id --check | head -n 3 && \
       flatpak run --command=sh $id -c 'ls /usr/lib/*/libwebkitgtk-6.0.so.4 && python3 --version'
    then result "Flatpak (user install)" PASS; else result "Flatpak (user install)" FAIL; fi
    flatpak --user info $id | grep -E "Runtime|Version|Installed" || true
    flatpak --user uninstall -y --noninteractive $id >/dev/null; unset FLATPAK_USER_DIR; rm -rf "$probe" ;;
esac; done
echo "failures: $failures"; exit $((failures > 0))
