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

mkdir -p "$(dirname -- "$output")"
sed \
  -e "s|@VERSION@|$version|g" \
  -e "s|@RELEASE_TAG@|$release_tag|g" \
  -e "s|@WHEEL_SHA256@|$wheel_sha256|g" \
  "$template" >"$output"
chmod 0755 "$output"
bash -n "$output"
echo "Generated $output for $release_tag ($wheel_sha256)"
