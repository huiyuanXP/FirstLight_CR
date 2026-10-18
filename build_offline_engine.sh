#!/usr/bin/env bash
# Linux counterpart of build_offline_engine.ps1: verify the original APK,
# build the probe, decode/patch/sign the offline APK and write its receipt.
# Usage: build_offline_engine.sh [INPUT_APK] [OUTPUT_APK] [ENGINE_COUNT]
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${root}/native_runner/local_config.sh"
input_apk=${1:-${CR_INPUT_APK:?Set CR_INPUT_APK in .env or pass INPUT_APK}}
output_apk=${2:-${CR_OUTPUT_APK:-${root}/build/cr-ai-offline.apk}}
engine_count=${3:-${CR_BUILD_ENGINE_COUNT:-1}}
python=${CR_PYTHON:-python3}
java=${CR_JAVA:-java}
probe=${CR_PROBE:-${root}/native_runner/probe/out/libcrprobe.so}
input_apk=$(realpath -- "$input_apk")
output_apk=$(realpath -m -- "$output_apk")
[[ $input_apk != "$output_apk" ]] || { echo "OutputApk must not overwrite the user-provided original APK." >&2; exit 1; }
work=${root}/build/work-$(date +%Y%m%d%H%M%S)-$$
mkdir -p "$work"

echo "[1/4] Verify the user-provided original APK"
"$python" "${root}/native_runner/offline_build.py" check-input --input-apk "$input_apk"

echo "[2/4] Build the native probe from source"
bash "${root}/native_runner/probe/build_probe.sh" "${CR_NDK_ROOT:?Set CR_NDK_ROOT in .env}" "$probe"

echo "[3/4] Decode, patch, build and sign locally"
apktool=${root}/native_runner/probe/out/tools/apktool_2.12.1.jar
mkdir -p "$(dirname -- "$apktool")"
if [[ ! -f $apktool ]]; then
  curl -fsSL -o "$apktool" "https://github.com/iBotPeaches/Apktool/releases/download/v2.12.1/apktool_2.12.1.jar"
fi
echo "66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa  ${apktool}" | sha256sum -c --quiet -
"$java" -jar "$apktool" decode "$input_apk" --output "$work/decoded"
bash "${root}/native_runner/build_engine_cluster_apk.sh" "$work/decoded" "$probe" "$output_apk" "$engine_count"

echo "[4/4] Verify the signed APK payload"
"$python" "${root}/native_runner/offline_build.py" verify --apk "$output_apk" --probe "$probe"
printf 'CR_DECODED_APK=%s\nCR_PROBE=%s\nCR_OUTPUT_APK=%s\n' "$work/decoded" "$probe" "$output_apk" > "$work/generated.env"
echo "Built: $output_apk"
echo "Build receipt settings: $work/generated.env"
