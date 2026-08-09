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

echo "Installed RiftLift at $bin_root/riftlift"
"$bin_root/riftlift" setup
cat <<'EOF'

RiftLift is ready. Sign into Meta once:
  riftlift login

Then add an owned Rift game using its Meta store URL:
  riftlift add 'https://www.meta.com/experiences/APP_ID/'
EOF
