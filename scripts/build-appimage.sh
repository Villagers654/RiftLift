#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF' >&2
usage: scripts/build-appimage.sh [OUTPUT_DIR]

Builds a RiftLift AppImage (x86_64) from the current checkout, producing
OUTPUT_DIR/riftlift-<version>-x86_64.AppImage. OUTPUT_DIR defaults to ./dist.

Requires Linux (AppImages cannot be built on macOS/Windows), git and python3.
Downloads a prebuilt manylinux Python AppImage on first run.
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$(uname -s)" != Linux ]]; then
  echo "AppImages can only be built on Linux." >&2
  exit 1
fi

command -v git >/dev/null || { echo "git is required." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output_dir=${1:-"$repo_root/dist"}
mkdir -p "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)

version=$(PYTHONPATH="$repo_root/src" python3 -c 'from riftlift import __version__; print(__version__)')

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

echo "Setting up build tools..." >&2
python3 -m venv "$work/build-venv"
build_python="$work/build-venv/bin/python"
"$build_python" -m pip install --quiet --upgrade pip
"$build_python" -m pip install --quiet build python-appimage

echo "Building the RiftLift wheel..." >&2
"$build_python" -m build --wheel --outdir "$work/wheel" "$repo_root" >&2
wheel=("$work"/wheel/riftlift-*-py3-none-any.whl)
[[ ${#wheel[@]} == 1 ]] || { echo "expected exactly one built wheel" >&2; exit 1; }

echo "Assembling the AppImage recipe..." >&2
recipe="$work/recipe"
mkdir -p "$recipe"
cp "$repo_root/scripts/appimage/recipe/entrypoint.sh" "$recipe/"
cp "$repo_root/scripts/appimage/recipe/io.github.villagers654.RiftLift.appdata.xml" "$recipe/"
cp "$repo_root/assets/io.github.villagers654.RiftLift.desktop" "$recipe/"
cp "$repo_root/assets/io.github.villagers654.RiftLift.svg" "$recipe/"
printf '%s\n' "${wheel[0]}" >"$recipe/requirements.txt"

echo "Building the AppImage (this bundles PySide6, so it can take a few minutes)..." >&2
export APPIMAGE_EXTRACT_AND_RUN=1
(cd "$output_dir" && "$work/build-venv/bin/python-appimage" build app -p 3.12 "$recipe")

built=("$output_dir"/RiftLift-*.AppImage)
[[ ${#built[@]} == 1 ]] || { echo "python-appimage did not produce exactly one AppImage" >&2; exit 1; }
final="$output_dir/riftlift-${version}-x86_64.AppImage"
mv -f -- "${built[0]}" "$final"
chmod +x "$final"

echo "Built $final"
