#!/usr/bin/env bash
# Linux counterpart of build_probe.ps1: compile libcrprobe.so with the NDK's
# linux-x86_64 clang. Usage: build_probe.sh [NDK_ROOT] [OUTPUT]
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${script_dir}/../local_config.sh"
ndk_root=${1:-${CR_NDK_ROOT:?Set CR_NDK_ROOT in .env or pass NDK_ROOT}}
output=${2:-${CR_PROBE:-${script_dir}/out/libcrprobe.so}}
compiler="${ndk_root}/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android24-clang++"

if [[ ! -x $compiler ]]; then
  echo "Missing NDK compiler: $compiler" >&2
  exit 1
fi
mkdir -p "$(dirname -- "$output")"

"$compiler" \
  -std=c++17 \
  -O2 \
  -fPIC \
  -fvisibility=hidden \
  -Wall \
  -Wextra \
  -shared \
  "-Wl,-z,max-page-size=16384" \
  -o "$output" \
  "${script_dir}/cr_replay_probe.cpp" \
  "${script_dir}/native_touch_interceptor.cpp" \
  "${script_dir}/native_call_arm64.S" \
  "${script_dir}/remaining_runtime_arm64.S" \
  -llog \
  -ldl \
  -lz

ls -l -- "$output"
