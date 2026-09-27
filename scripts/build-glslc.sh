#!/usr/bin/env bash
set -euo pipefail

output=${1:?usage: scripts/build-glslc.sh OUTPUT}
mkdir -p "$(dirname -- "$output")"
output=$(realpath -- "$output")
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT

# Ubuntu 22.04 has no glslc package. Build the compiler on our glibc baseline.
git -C "$work" init -q
git -C "$work" fetch -q --depth=1 https://github.com/google/shaderc.git \
  3cd72062f297df05e6a042f2616c42bc8956c326
git -C "$work" checkout -q --detach FETCH_HEAD
(cd "$work" && python3 utils/git-sync-deps)
cmake -S "$work" -B "$work/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DSHADERC_SKIP_TESTS=ON \
  -DSHADERC_SKIP_EXAMPLES=ON -DSHADERC_SKIP_COPYRIGHT_CHECK=ON
cmake --build "$work/build" --target glslc_exe --parallel 2
install -m 755 "$work/build/glslc/glslc" "$output"
