#!/usr/bin/env bash
# Run one real-engine self-play episode on this Mac.
# Usage: local/run_mac_selfplay.sh [CKPT0] [CKPT1] [SEED] [--sample]
# Boots the $FL_AVD (default Pixel_9_FirstLight) AVD on $FL_PORT (default 5556) headless when it is not running, starts and
# attests 2 engines on host ports 26789/26790, then plays one episode.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
sdk=$HOME/Library/Android/sdk
adb=$sdk/platform-tools/adb
port=${FL_PORT:-5556}
serial=emulator-$port
avd=${FL_AVD:-Pixel_9_FirstLight}
ckpt0=${1:-checkpoints/General}
ckpt1=${2:-checkpoints/IL}
seed=${3:-1}
extra=("${@:4}")

if ! "$adb" devices | grep -q "^$serial[[:space:]]*device"; then
  nohup "$sdk/emulator/emulator" -avd "$avd" -port "$port" -no-window -no-snapshot -no-audio -no-boot-anim \
    > runs/emulator.log 2>&1 &
  "$adb" -s "$serial" wait-for-device
  until [[ $("$adb" -s "$serial" shell getprop sys.boot_completed | tr -d '\r') == 1 ]]; do sleep 2; done
fi
"$adb" -s "$serial" root >/dev/null
"$adb" -s "$serial" wait-for-device
# The probe validates object pointers against /proc/self/maps, so the game
# must run with untagged heap pointers on an ARM64 host.
"$adb" -s "$serial" shell am compat disable NATIVE_HEAP_POINTER_TAGGING nullsroyale.rel.free >/dev/null

CR_TRAINING_ADB=$adb .venv/bin/python -m native_runner.training.v4.configure_emulator_engine_guests \
  --serials "$serial" --host-bases 26789 --engines 2 --output runs/guest-ready.json >/dev/null

out=runs/selfplay-native-$(basename "$ckpt0")-vs-$(basename "$ckpt1")-seed$seed-$(date +%Y%m%d%H%M%S)
.venv/bin/python tools/policy_episode.py --engine native --port 26789 \
  --checkpoint-0 "$ckpt0" --checkpoint-1 "$ckpt1" --seed "$seed" --output "$out" ${extra[@]+"${extra[@]}"}
