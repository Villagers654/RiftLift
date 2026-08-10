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

overrides='^(ovr_PlatformInitializeUnrealWindows|ovr_PlatformInitializeWindows|ovr_IsPlatformInitialized|ovr_IsEntitled|ovr_GetLoggedInUserID|ovr_User_GetLoggedInUser|ovr_Entitlement_GetIsViewerEntitled|ovr_Achievements_GetAllDefinitions|ovr_Achievements_GetAllProgress|ovr_CloudStorage_LoadBucketMetadata|ovr_CloudStorage_Load|ovr_User_GetLoggedInUserFriends|ovr_PopMessage|ovr_Message_GetRequestID|ovr_Message_GetType|ovr_Message_IsError|ovr_Message_GetUser|ovr_Message_GetAchievementDefinitionArray|ovr_AchievementDefinitionArray_GetSize|ovr_AchievementDefinitionArray_HasNextPage|ovr_Message_GetAchievementProgressArray|ovr_AchievementProgressArray_GetSize|ovr_AchievementProgressArray_HasNextPage|ovr_Message_GetCloudStorageMetadataArray|ovr_CloudStorageMetadataArray_GetSize|ovr_CloudStorageMetadataArray_HasNextPage|ovr_Message_GetUserArray|ovr_UserArray_GetSize|ovr_UserArray_HasNextPage|ovr_User_GetID|ovr_User_GetOculusID|ovr_FreeMessage)$'

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
  -o "$output"

for initializer in ovr_PlatformInitializeUnrealWindows ovr_PlatformInitializeWindows; do
  if ! llvm-readobj --coff-exports "$output" \
    | grep "Name: $initializer" >/dev/null; then
    echo "built DLL is missing the $initializer export" >&2
    exit 1
  fi
done
echo "Built $output"
