#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )); then
  echo "usage: $0 ORIGINAL_IMPL_DLL OUTPUT_DLL" >&2
  exit 2
fi

original=$1
output=$2
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT

command -v llvm-readobj >/dev/null
command -v x86_64-w64-mingw32-gcc >/dev/null

# Every export the shim defines itself; the rest forward to Meta's DLL.
overrides="^($(grep -oE '__cdecl[[:space:]]+ovr[A-Za-z0-9_]+\(|^(USER|ERROR)_STRING_GETTER\(ovr[A-Za-z0-9_]+' "$repo_root/compat/riftlift-platform-shim.c" \
  | grep -oE 'ovr[A-Za-z0-9_]+' | sort -u | paste -sd'|'))$"

{
  echo 'LIBRARY LibOVRPlatformImpl64_1'
  echo 'EXPORTS'
  llvm-readobj --coff-exports "$original" \
    | sed -n 's/^  Name: //p' \
    | grep '^ovr' \
    | grep -Ev "$overrides" \
    | while IFS= read -r name; do
        printf '  %s=LibOVRPlatformImpl64_1_real.%s\n' "$name" "$name"
      done
} >"$build_dir/shim.def"

x86_64-w64-mingw32-gcc \
  -O2 -Wall -Wextra -Werror -shared \
  "$repo_root/compat/riftlift-platform-shim.c" \
  "$build_dir/shim.def" \
  -Wl,--no-insert-timestamp \
  -lws2_32 \
  -o "$output"

for initializer in ovr_PlatformInitializeUnrealWindows ovr_PlatformInitializeWindows \
  ovr_PlatformInitializeUnityWindows ovr_UnityInitWrapperWindows \
  ovr_PlatformInitializeWindowsAsynchronous ovr_UnityInitWrapperWindowsAsynchronous; do
  if ! llvm-readobj --coff-exports "$output" \
    | grep "Name: $initializer" >/dev/null; then
    echo "built DLL is missing the $initializer export" >&2
    exit 1
  fi
done
echo "Built $output"
