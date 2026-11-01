#!/usr/bin/env bash
# Build RiftLift from this checkout and install it for the current user:
# the programs in ~/.local/lib/riftlift, riftlift and riftlift-cli in
# ~/.local/bin, and a desktop entry. Needs clang, cmake, ninja, pkg-config
# and the OpenSSL and Wayland development headers.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
data_home=${XDG_DATA_HOME:-$HOME/.local/share}
bin_root=${XDG_BIN_HOME:-$HOME/.local/bin}
[[ $data_home == /* ]] || data_home=$HOME/.local/share
[[ $bin_root == /* ]] || bin_root=$HOME/.local/bin
app_root=$HOME/.local/lib/riftlift

for tool in clang++ cmake ninja pkg-config; do
  command -v "$tool" >/dev/null || { echo "$tool is required to build RiftLift." >&2; exit 1; }
done

(cd "$repo_root" && cmake --preset linux-release -DRIFTLIFT_BUILD_TESTS=OFF >/dev/null)
cmake --build "$repo_root/build/linux-release"

mkdir -p "$app_root" "$bin_root"
for program in riftlift riftlift-cli riftlift-runtime; do
  # Replace, not overwrite: a running copy keeps its own file.
  install -m755 "$repo_root/build/linux-release/bin/$program" "$app_root/.$program.new"
  mv -f -- "$app_root/.$program.new" "$app_root/$program"
done
ln -sfn "$app_root/riftlift" "$bin_root/riftlift"
ln -sfn "$app_root/riftlift-cli" "$bin_root/riftlift-cli"

# Left behind by the Python releases.
rm -rf -- "$data_home/riftlift/venv" "$data_home/riftlift/.venv-previous"
[[ -L $bin_root/riftlift-gui ]] && rm -f -- "$bin_root/riftlift-gui"

applications_root=$data_home/applications
icons_root=$data_home/icons/hicolor/scalable/apps
mkdir -p "$applications_root" "$icons_root"
sed "s|^Exec=.*|Exec=\"$app_root/riftlift\"|" \
  "$repo_root/assets/io.github.villagers654.RiftLift.desktop" \
  >"$applications_root/io.github.villagers654.RiftLift.desktop"
cp "$repo_root/assets/io.github.villagers654.RiftLift.svg" "$icons_root/"
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$applications_root" >/dev/null 2>&1 || true
fi

cat <<EOF
Installed RiftLift in $app_root.

Open RiftLift from your application menu (or run riftlift), sign in with
the Meta account that owns your Rift games, and install them from Library.
Add Game adds Steam games with an Oculus mode and games already on this PC.

The command line: riftlift-cli --help
EOF
