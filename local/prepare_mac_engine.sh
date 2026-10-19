#!/usr/bin/env bash
# Prepare a real FirstLight engine on an Apple Silicon Mac from an Android
# Studio AVD that already has Null's Royale 15.535.13 installed and updated.
#
# Usage (run from the FirstLight_CR repo root, with this file in local/):
#   local/prepare_mac_engine.sh [SOURCE_AVD] [TARGET_AVD] [EMULATOR_PORT]
# Defaults: Pixel_9  Pixel_9_FirstLight  5556
#
# Steps: pull the installed APK from SOURCE_AVD, check its libg.so, build the
# 2-engine offline APK, clone SOURCE_AVD into TARGET_AVD (APFS clone, SOURCE_AVD
# stays untouched), boot TARGET_AVD headless, copy the 4 rule files the update
# directory lacks, install the offline APK, disable heap pointer tagging, and
# start + attest both engines on host ports 26789/26790.
set -euo pipefail

source_avd=${1:-Pixel_9}
target_avd=${2:-Pixel_9_FirstLight}
port=${3:-5556}
serial=emulator-$port
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
sdk=${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}
adb=$sdk/platform-tools/adb
emulator=$sdk/emulator/emulator
avd_home=$HOME/.android/avd
python=$root/.venv/bin/python
bash5=${BASH5:-/opt/homebrew/bin/bash}
build_tools=${CR_BUILD_TOOLS:-$(ls -d "$sdk"/build-tools/* | sort -V | tail -1)}
apk_in=user-provided/nullsroyale-15.535.13.apk
apk_out=build/cr-ai-offline.apk
probe=native_runner/probe/out/libcrprobe.so
pkg=nullsroyale.rel.free
libg_sha=$("$python" -c 'from native_runner.offline_build import supported_engine; print(supported_engine()["libg_sha256"])')
rule_files=(
  csv_logic/characters/ghost_ev1.toml
  csv_logic/characters/goblin_hut_rework.toml
  csv_logic/characters/hero_form/balloon_hero_spell.toml
  csv_logic/characters/skeleton_balloon_ev1.toml
)

[[ -x $python ]] || { echo "Create .venv first (uv venv -p 3.12 .venv && uv pip install -p .venv -r requirements.txt)" >&2; exit 1; }
[[ -f $probe ]] || { echo "Missing $probe (build it with NDK 27.3, see README section 3)" >&2; exit 1; }
[[ -x $bash5 ]] || { echo "Missing bash >= 4 at $bash5 (brew install bash)" >&2; exit 1; }
[[ -d $avd_home/$source_avd.avd ]] || { echo "Missing AVD $source_avd" >&2; exit 1; }
[[ ! -e $avd_home/$target_avd.avd ]] || { echo "$target_avd already exists; delete it or pick another name" >&2; exit 1; }

boot() {  # boot <avd> <port>
  nohup "$emulator" -avd "$1" -port "$2" -no-window -no-snapshot -no-audio -no-boot-anim \
    > "runs/emulator-$1.log" 2>&1 &
  "$adb" -s "emulator-$2" wait-for-device
  until [[ $("$adb" -s "emulator-$2" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r') == 1 ]]; do sleep 2; done
}
running_serial() {  # serial of a running emulator for <avd>, if any
  for s in $("$adb" devices | awk '/^emulator-/{print $1}'); do
    [[ $("$adb" -s "$s" emu avd name 2>/dev/null | head -1 | tr -d '\r') == "$1" ]] && { echo "$s"; return; }
  done
}
mkdir -p runs build user-provided

echo "[1/6] Pull the installed APK from $source_avd"
if [[ ! -f $apk_in ]]; then
  src=$(running_serial "$source_avd")
  started_source=0
  if [[ -z $src ]]; then boot "$source_avd" 5580; src=emulator-5580; started_source=1; fi
  path=$("$adb" -s "$src" shell pm path "$pkg" | tr -d '\r' | sed -n 's/^package://p')
  [[ $(grep -c . <<<"$path") == 1 ]] || { echo "Expected one base.apk for $pkg, got: $path" >&2; exit 1; }
  "$adb" -s "$src" pull "$path" "$apk_in"
  [[ $started_source == 1 ]] && "$adb" -s "$src" emu kill
fi
[[ $(unzip -p "$apk_in" lib/arm64-v8a/libg.so | shasum -a 256 | cut -d' ' -f1) == "$libg_sha" ]] \
  || { echo "libg.so in $apk_in is not the supported engine build" >&2; exit 1; }

echo "[2/6] Build the offline APK (2 engine processes)"
if [[ ! -f $apk_out ]]; then
  jar=native_runner/probe/out/tools/apktool_2.12.1.jar
  mkdir -p "$(dirname "$jar")"
  [[ -f $jar ]] || curl -fsSL -o "$jar" https://github.com/iBotPeaches/Apktool/releases/download/v2.12.1/apktool_2.12.1.jar
  echo "66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa  $jar" | shasum -a 256 -c -
  rm -rf build/decoded
  java -jar "$jar" decode "$apk_in" --output build/decoded
  CR_BUILD_TOOLS=$build_tools CR_PYTHON=$python \
    "$bash5" native_runner/build_engine_cluster_apk.sh build/decoded "$probe" "$apk_out" 2
  "$python" native_runner/offline_build.py verify --apk "$apk_out" --probe "$probe"
fi

echo "[3/6] Clone $source_avd into $target_avd"
src=$(running_serial "$source_avd")
[[ -z $src ]] || { echo "Stopping $source_avd ($src) for a consistent copy"; "$adb" -s "$src" emu kill; sleep 5; }
cp -c -R "$avd_home/$source_avd.avd" "$avd_home/$target_avd.avd"
rm -rf "$avd_home/$target_avd.avd"/*.lock "$avd_home/$target_avd.avd"/hardware-qemu.ini* "$avd_home/$target_avd.avd/snapshots"
sed "s#$source_avd.avd#$target_avd.avd#g" "$avd_home/$source_avd.ini" > "$avd_home/$target_avd.ini"
sed -i '' -e "s/^AvdId=.*/AvdId=$target_avd/" -e "s/^avd.ini.displayname=.*/avd.ini.displayname=$target_avd/" \
  -e 's/^hw.cpu.ncore=.*/hw.cpu.ncore=2/' -e 's/^hw.ramSize=.*/hw.ramSize=4096/' "$avd_home/$target_avd.avd/config.ini"

echo "[4/6] Boot $target_avd as $serial and add the 4 rule files to the update directory"
boot "$target_avd" "$port"
"$adb" -s "$serial" root >/dev/null; sleep 3; "$adb" -s "$serial" wait-for-device
tmp=$(mktemp -d)
for f in "${rule_files[@]}"; do mkdir -p "$tmp/$(dirname "$f")"; unzip -p "$apk_in" "assets/$f" > "$tmp/$f"; done
"$adb" -s "$serial" push "$tmp/csv_logic" /data/local/tmp/fl_csv >/dev/null
rm -rf "$tmp"
"$adb" -s "$serial" shell "D=/data/user/0/$pkg/update; U=\$(stat -c %u \$D); C=\$(ls -Z \$D/fingerprint.json | cut -d' ' -f1); \
  cp -r /data/local/tmp/fl_csv/. \$D/csv_logic/ && rm -rf /data/local/tmp/fl_csv && \
  chown -R \$U:\$U \$D/csv_logic && chcon -R \$C \$D/csv_logic && \
  cd \$D && chmod 600 ${rule_files[*]}"

echo "[5/6] Install the offline APK (keeps the downloaded update)"
"$python" tools/prepare_training_guest.py --adb "$adb" --serial "$serial" --apk "$apk_out"
"$adb" -s "$serial" shell am compat disable NATIVE_HEAP_POINTER_TAGGING "$pkg"

echo "[6/6] Start and attest 2 engines on host ports 26789/26790"
CR_TRAINING_ADB=$adb "$python" -m native_runner.training.v4.configure_emulator_engine_guests \
  --serials "$serial" --host-bases 26789 --engines 2 --output runs/guest-ready.json \
  | grep -c '"production_ready": true' | xargs -I{} echo "production_ready engines: {} / 2"
echo "Engine ready. Next: local/run_mac_selfplay.sh checkpoints/General checkpoints/IL 1 --sample"
