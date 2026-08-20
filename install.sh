#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
data_home=${XDG_DATA_HOME:-$HOME/.local/share}
bin_root=${XDG_BIN_HOME:-$HOME/.local/bin}
[[ $data_home == /* ]] || data_home=$HOME/.local/share
[[ $bin_root == /* ]] || bin_root=$HOME/.local/bin
data_root=$data_home/riftlift
venv="$data_root/venv"

command -v python3 >/dev/null || { echo "Python 3.10.12 or newer is required." >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10, 12))' || {
  echo "Python 3.10.12 or newer is required." >&2
  exit 1
}

mkdir -p "$data_root" "$bin_root"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet "$repo_root"
ln -sfn "$venv/bin/riftlift" "$bin_root/riftlift"
ln -sfn "$venv/bin/riftlift-gui" "$bin_root/riftlift-gui"

applications_root=$data_home/applications
icons_root=$data_home/icons/hicolor/scalable/apps
mkdir -p "$applications_root" "$icons_root"
sed "s|^Exec=.*|Exec=\"$bin_root/riftlift-gui\"|" \
  "$repo_root/assets/io.github.villagers654.RiftLift.desktop" \
  >"$applications_root/io.github.villagers654.RiftLift.desktop"
cp "$repo_root/assets/io.github.villagers654.RiftLift.svg" "$icons_root/"
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$applications_root" >/dev/null 2>&1 || true
fi

echo "Installed RiftLift at $bin_root/riftlift"
"$bin_root/riftlift" setup
cat <<'EOF'

RiftLift is ready.

Open RiftLift from your application menu, then:
  1. Click System.
  2. Click Sign In and finish Meta's sign-in.
  3. Click Add Game and paste an owned Rift / PC VR store URL.

To open the desktop app from a terminal:
  riftlift gui

Command-line setup is also available:
  riftlift login
  riftlift add 'https://www.meta.com/experiences/APP_ID/'
EOF
