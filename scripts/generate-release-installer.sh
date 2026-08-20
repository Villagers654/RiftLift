#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "usage: $0 RELEASE_TAG WHEEL OUTPUT" >&2
  exit 2
fi

release_tag=$1
wheel=$2
output=$3
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
template="$repo_root/scripts/riftlift-installer.sh.in"

[[ -f $wheel ]] || { echo "wheel not found: $wheel" >&2; exit 1; }
[[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?(-alpha\.[0-9]+)?$ ]] || {
  echo "invalid release tag: $release_tag" >&2
  exit 1
}

wheel_name=$(basename -- "$wheel")
if [[ ! $wheel_name =~ ^riftlift-([0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?(a[0-9]+)?)-py3-none-any\.whl$ ]]; then
  echo "unexpected RiftLift wheel name: $wheel_name" >&2
  exit 1
fi
version=${BASH_REMATCH[1]}
wheel_sha256=$(sha256sum "$wheel" | awk '{print $1}')
desktop="$repo_root/assets/io.github.villagers654.RiftLift.desktop"
icon="$repo_root/assets/io.github.villagers654.RiftLift.svg"

mkdir -p "$(dirname -- "$output")"
python3 - "$template" "$output" "$version" "$release_tag" "$wheel_sha256" \
  "$desktop" "$icon" <<'PY'
import base64
import pathlib
import sys

template, output, version, release_tag, wheel_sha256, desktop, icon = sys.argv[1:]
replacements = {
    "@VERSION@": version,
    "@RELEASE_TAG@": release_tag,
    "@WHEEL_SHA256@": wheel_sha256,
    "@DESKTOP_BASE64@": base64.b64encode(pathlib.Path(desktop).read_bytes()).decode(),
    "@ICON_BASE64@": base64.b64encode(pathlib.Path(icon).read_bytes()).decode(),
}
contents = pathlib.Path(template).read_text()
for marker, value in replacements.items():
    contents = contents.replace(marker, value)
unresolved = [marker for marker in replacements if marker in contents]
if unresolved:
    raise SystemExit(f"installer template contains unresolved placeholders: {unresolved}")
pathlib.Path(output).write_text(contents)
PY
chmod 0755 "$output"
bash -n "$output"
echo "Generated $output for $release_tag ($wheel_sha256)"
