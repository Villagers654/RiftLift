#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
data_root=${XDG_DATA_HOME:-$HOME/.local/share}/riftlift
bin_root=${XDG_BIN_HOME:-$HOME/.local/bin}
venv="$data_root/venv"

command -v python3 >/dev/null || { echo "Python 3.10 or newer is required." >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
  echo "Python 3.10 or newer is required." >&2
  exit 1
}

mkdir -p "$data_root" "$bin_root"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install --quiet --upgrade pip
"$venv/bin/python" -m pip install --quiet "$repo_root"
ln -sfn "$venv/bin/riftlift" "$bin_root/riftlift"
ln -sfn "$venv/bin/riftlift-gui" "$bin_root/riftlift-gui"

applications_root=${XDG_DATA_HOME:-$HOME/.local/share}/applications
icons_root=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps
mkdir -p "$applications_root" "$icons_root"
cp "$repo_root/assets/io.github.villagers654.RiftLift.desktop" "$applications_root/"
cp "$repo_root/assets/io.github.villagers654.RiftLift.svg" "$icons_root/"

echo "Installed RiftLift at $bin_root/riftlift"
"$bin_root/riftlift" setup
cat <<'EOF'

RiftLift is ready.

Open RiftLift from your application menu, then:
  1. Click Check system.
  2. Click Sign in and finish Meta's sign-in.
  3. Click Add game and paste an owned Rift / PC VR store URL.

To open the desktop app from a terminal:
  riftlift gui

Command-line setup is also available:
  riftlift login
  riftlift add 'https://www.meta.com/experiences/APP_ID/'
EOF
