#!/usr/bin/env bash
# Linux counterpart of build_engine_cluster_apk.ps1. Requires a pristine
# Apktool decode of the supported APK, the compiled probe, a JDK, and
# zipalign/apksigner from SDK build-tools (CR_BUILD_TOOLS) or on PATH.
# Usage: build_engine_cluster_apk.sh DECODED_APK [PROBE] [OUTPUT_APK] [ENGINE_COUNT] [--debuggable]
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${script_dir}/local_config.sh"
debuggable=()
positional=()
for argument in "$@"; do
  if [[ $argument == --debuggable ]]; then debuggable=(--debuggable); else positional+=("$argument"); fi
done
decoded_apk=${positional[0]:-${CR_DECODED_APK:?Set CR_DECODED_APK in .env or pass DECODED_APK}}
probe=${positional[1]:-${CR_PROBE:-${script_dir}/probe/out/libcrprobe.so}}
output_apk=${positional[2]:-${CR_OUTPUT_APK:-$(dirname -- "$script_dir")/builds/cr_engine_cluster_signed.apk}}
engine_count=${positional[3]:-${CR_BUILD_ENGINE_COUNT:-1}}
python=${CR_PYTHON:-python3}
java=${CR_JAVA:-java}
keytool=${CR_KEYTOOL:-keytool}

tool() {
  if [[ -n ${CR_BUILD_TOOLS:-} && -x ${CR_BUILD_TOOLS}/$1 ]]; then echo "${CR_BUILD_TOOLS}/$1"; else command -v "$1"; fi
}
zipalign=$(tool zipalign) || { echo "Missing zipalign (set CR_BUILD_TOOLS)" >&2; exit 1; }
apksigner=$(tool apksigner) || { echo "Missing apksigner (set CR_BUILD_TOOLS)" >&2; exit 1; }

apktool_version=2.12.1
apktool_sha256=66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa
apktool=${script_dir}/probe/out/tools/apktool_${apktool_version}.jar
build_root=${script_dir}/probe/out/engine-cluster-apk
source_root=${build_root}/source
keystore=${script_dir}/probe/crprobe-cluster.keystore
password_file=${keystore}.pass

for required in "$decoded_apk/AndroidManifest.xml" "$decoded_apk/apktool.yml" \
  "$decoded_apk/smali/com/supercell/clashroyale/GameApp.smali" "$probe"; do
  [[ -f $required ]] || { echo "Missing required cluster-build input: $required" >&2; exit 1; }
done
command -v "$java" >/dev/null && command -v "$keytool" >/dev/null || { echo "Missing JDK java/keytool" >&2; exit 1; }

mkdir -p "$(dirname -- "$apktool")"
if [[ ! -f $apktool ]]; then
  curl -fsSL -o "$apktool" "https://github.com/iBotPeaches/Apktool/releases/download/v${apktool_version}/apktool_${apktool_version}.jar"
fi
echo "${apktool_sha256}  ${apktool}" | sha256sum -c --quiet - || { echo "Apktool ${apktool_version} SHA-256 mismatch" >&2; exit 1; }
reported=$("$java" -jar "$apktool" --version 2>/dev/null | tail -n 1)
[[ $reported == "$apktool_version" ]] || { echo "Expected Apktool ${apktool_version}, got '${reported}'" >&2; exit 1; }

rm -rf -- "$build_root"
mkdir -p "$source_root"
cp -a "$decoded_apk/." "$source_root/"
"$python" "${script_dir}/offline_build.py" patch --source "$source_root"
"$python" "${script_dir}/offline_build.py" cluster --source "$source_root" --probe "$probe" \
  --engine-count "$engine_count" "${debuggable[@]}"

if [[ -f $keystore && ! -f $password_file ]] || [[ ! -f $keystore && -f $password_file ]]; then
  echo "Cluster signing key/password files are incomplete; keep or remove both." >&2
  exit 1
fi
if [[ ! -f $keystore ]]; then
  (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$password_file")
  password=$(<"$password_file")
  "$keytool" -genkeypair -keystore "$keystore" -storepass "$password" -keypass "$password" \
    -alias crprobe-cluster -keyalg RSA -keysize 2048 -validity 36500 \
    -dname "CN=Firstlight CR Local Build" -noprompt >/dev/null
fi
password=$(tr -d '\r\n' < "$password_file")
[[ -n $password ]] || { echo "The local cluster signing password file is empty." >&2; exit 1; }

"$java" -jar "$apktool" build "$source_root" --force --output "$build_root/cluster-unsigned.apk"
"$zipalign" -f -p 4 "$build_root/cluster-unsigned.apk" "$build_root/cluster-aligned.apk"
"$apksigner" sign --ks "$keystore" --ks-key-alias crprobe-cluster \
  --ks-pass "pass:$password" --key-pass "pass:$password" \
  --out "$build_root/cluster-signed.apk" "$build_root/cluster-aligned.apk"
"$apksigner" verify --verbose --print-certs "$build_root/cluster-signed.apk"

mkdir -p "$(dirname -- "$output_apk")"
cp -f "$build_root/cluster-signed.apk" "$output_apk"
"$python" - "$output_apk" "$probe" "$engine_count" <<'PY'
import hashlib, json, sys
from pathlib import Path
apk, probe, engines = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), int(sys.argv[3])
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({
    "ok": True, "version": "engine-cluster-apk.v1", "package": "nullsroyale.rel.free",
    "engineProcesses": engines, "interactiveMainActivity": True, "probeSha256": digest(probe),
    "apkSha256": digest(apk), "apkBytes": apk.stat().st_size, "outputApk": str(apk),
}, indent=2))
PY
