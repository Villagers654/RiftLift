#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)

checkout_sparse_submodule() {
  local name=$1
  shift

  local path url commit
  path=$(git -C "$repo_root" config -f .gitmodules --get "submodule.${name}.path")
  url=$(git -C "$repo_root" config -f .gitmodules --get "submodule.${name}.url")
  commit=$(git -C "$repo_root" ls-tree HEAD "$path" | awk '{print $3}')

  if [[ -z "$path" || -z "$url" || -z "$commit" ]]; then
    echo "Unable to resolve submodule $name" >&2
    return 1
  fi

  mkdir -p "$repo_root/$path"
  git -C "$repo_root/$path" init --quiet
  if git -C "$repo_root/$path" remote get-url origin >/dev/null 2>&1; then
    git -C "$repo_root/$path" remote set-url origin "$url"
  else
    git -C "$repo_root/$path" remote add origin "$url"
  fi
  git -C "$repo_root/$path" sparse-checkout init --cone
  git -C "$repo_root/$path" sparse-checkout set "$@"
  git -C "$repo_root/$path" fetch --quiet --no-tags --depth=1 --filter=blob:none origin "$commit"
  git -C "$repo_root/$path" checkout --quiet --detach FETCH_HEAD
}

# The Rift runtime only consumes the public headers/sources below. In particular, OpenVR's
# 400+ MB sample tree is not part of any RiftLift target.
checkout_sparse_submodule runtime/Externals/Vulkan include
checkout_sparse_submodule runtime/Externals/microprofile .
checkout_sparse_submodule runtime/Externals/openvr headers src
