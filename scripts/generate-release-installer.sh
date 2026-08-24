#!/usr/bin/env bash
set -euo pipefail

if (( $# != 3 )); then
  echo "usage: $0 RELEASE_TAG RELEASE_DIRECTORY OUTPUT" >&2
  exit 2
fi

release_tag=$1
release_directory=$2
output=$3
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
template="$repo_root/scripts/riftlift-installer.sh.in"

[[ -d $release_directory ]] || {
  echo "release directory not found: $release_directory" >&2
  exit 1
}
[[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?(-alpha\.[0-9]+)?$ ]] || {
  echo "invalid release tag: $release_tag" >&2
  exit 1
}

shopt -s nullglob
wheels=("$release_directory"/riftlift-*-py3-none-any.whl)
if (( ${#wheels[@]} != 1 )); then
  echo "expected exactly one RiftLift wheel in $release_directory" >&2
  exit 1
fi
wheel=${wheels[0]}
wheel_name=$(basename -- "$wheel")
if [[ ! $wheel_name =~ ^riftlift-([0-9]+\.[0-9]+\.[0-9]+(\.[0-9]+)?(a[0-9]+)?)-py3-none-any\.whl$ ]]; then
  echo "unexpected RiftLift wheel name: $wheel_name" >&2
  exit 1
fi
version=${BASH_REMATCH[1]}
wheel_sha256=$(sha256sum "$wheel" | awk '{print $1}')
runtime="$release_directory/riftlift-compat.zip"
openvr="$release_directory/riftlift-xrizer.tar.gz"
dxvk="$release_directory/riftlift-dxvk.tar.gz"
for payload in "$runtime" "$openvr" "$dxvk"; do
  [[ -f $payload ]] || { echo "release payload not found: $payload" >&2; exit 1; }
done
runtime_sha256=$(sha256sum "$runtime" | awk '{print $1}')
openvr_sha256=$(sha256sum "$openvr" | awk '{print $1}')
dxvk_sha256=$(sha256sum "$dxvk" | awk '{print $1}')
desktop="$repo_root/assets/io.github.villagers654.RiftLift.desktop"
icon="$repo_root/assets/io.github.villagers654.RiftLift.svg"

mkdir -p "$(dirname -- "$output")"
python3 - "$template" "$output" "$version" "$release_tag" \
  "$wheel_sha256" "$runtime_sha256" "$openvr_sha256" "$dxvk_sha256" \
  "$desktop" "$icon" <<'PY'
import base64
import pathlib
import sys

(
    template,
    output,
    version,
    release_tag,
    wheel_sha256,
    runtime_sha256,
    openvr_sha256,
    dxvk_sha256,
    desktop,
    icon,
) = sys.argv[1:]
replacements = {
    "@VERSION@": version,
    "@RELEASE_TAG@": release_tag,
    "@WHEEL_SHA256@": wheel_sha256,
    "@RUNTIME_SHA256@": runtime_sha256,
    "@OPENVR_SHA256@": openvr_sha256,
    "@DXVK_SHA256@": dxvk_sha256,
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
echo "Generated $output for $release_tag"
