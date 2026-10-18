"""Local APK/resource preparation. Never launches a game or downloads game content."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile

PACKAGE = Path(__file__).resolve().parent


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def supported_engine():
    return json.loads((PACKAGE / "supported_engine.json").read_text(encoding="utf-8"))


def relative_file(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Invalid resource path: {name}")
    return path


def validate_update(update: Path):
    release = supported_engine()
    if sha256(update / "fingerprint.json") != release["runtime_fingerprint_sha256"]:
        raise ValueError("Runtime update fingerprint differs from supported_engine.json")
    manifest = json.loads((PACKAGE / "data/competitive/manifest.json").read_text(encoding="utf-8"))
    # Check all required update facts before producing a build tree.
    for name, expected in manifest["source_files"].items():
        if name.startswith("runtime-update/") and sha256(update / name.removeprefix("runtime-update/")) != expected:
            raise ValueError(f"Runtime source hash mismatch: {name}")
    return manifest


def prepare(input_apk: Path, update: Path | None, workspace: Path):
    release = supported_engine()
    if sha256(input_apk) != release["apk_sha256"]:
        raise ValueError("Input APK SHA-256 differs from supported_engine.json; supply the unmodified supported APK")
    if update is None:
        raise ValueError("Local resource compilation requires --runtime-update.")
    manifest = validate_update(update)
    workspace.mkdir(parents=True, exist_ok=True)
    base = workspace / release["resource_directory"]
    with zipfile.ZipFile(input_apk) as apk:
        for item in apk.infolist():
            if item.is_dir() or not (item.filename.startswith("assets/") or item.filename == "lib/arm64-v8a/libg.so"):
                continue
            destination = base / relative_file(item.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with apk.open(item) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    shutil.copytree(update, workspace / "runtime-update")
    for name, expected in manifest["source_files"].items():
        if sha256(workspace / name) != expected:
            raise ValueError(f"Prepared resource hash mismatch: {name}")
    print(f"Verified APK and all {len(manifest['source_files'])} pinned resource files", flush=True)


def patch(source: Path):
    game = source / "smali/com/supercell/clashroyale/GameApp.smali"
    text = game.read_text(encoding="utf-8")
    if '"crprobe"' in text:
        raise ValueError("Decoded APK is already patched; use a pristine decode of the supported input APK")
    anchor = ".method public final a()Z\n    .locals 3\n"
    if text.count(anchor) != 1:
        raise ValueError("Unsupported GameApp.a() layout")
    bootstrap = '\n    const-string v0, "crprobe"\n    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n'
    # Return false from the online-loader gate so Titan initializes libg and its renderer directly.
    bootstrap += "    const/4 v0, 0x0\n    return v0\n"
    game.write_text(text.replace(anchor, anchor + bootstrap), encoding="utf-8")
    titan = source / "smali/com/supercell/titan/GameApp.smali"
    text = titan.read_text(encoding="utf-8")
    pattern = r'(    const-string v3, "g"\s+(?:\.line \d+\s+)*invoke-static \{v3\}, Ljava/lang/System;->loadLibrary\(Ljava/lang/String;\)V)'
    text, count = re.subn(
        pattern,
        lambda m: '    const-string v3, "scid_sdk"\n'
        "    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n\n"
        + m[0]
        + '\n\n    const-string v3, "crprobe"\n    invoke-static {v3}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V\n    invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V',
        text,
    )
    if count != 1 or text.count("# direct methods") != 1:
        raise ValueError("Unsupported Titan native-library bootstrap layout")
    text = text.replace(
        "# direct methods", "# direct methods\n.method private static native nativeBootstrapProbe()V\n.end method\n"
    )
    titan.write_text(text, encoding="utf-8")


CLUSTER_APPLICATION_SMALI = """.class public Lcom/supercell/clashroyale/EngineClusterApplication;
.super Landroid/app/Application;


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Application;-><init>()V

    return-void
.end method


# virtual methods
.method protected attachBaseContext(Landroid/content/Context;)V
    .locals 4

    invoke-super {p0, p1}, Landroid/app/Application;->attachBaseContext(Landroid/content/Context;)V

    sget v0, Landroid/os/Build$VERSION;->SDK_INT:I

    const/16 v1, 0x1c

    if-lt v0, v1, :done

    invoke-static {}, Landroid/app/Application;->getProcessName()Ljava/lang/String;

    move-result-object v0

    const-string v1, ":engine"

    invoke-virtual {v0, v1}, Ljava/lang/String;->lastIndexOf(Ljava/lang/String;)I

    move-result v2

    if-ltz v2, :done

    add-int/lit8 v2, v2, 0x1

    invoke-virtual {v0, v2}, Ljava/lang/String;->substring(I)Ljava/lang/String;

    move-result-object v3

    const-string v0, "engine0"

    invoke-virtual {v3, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-nez v0, :done

    invoke-static {v3}, Landroid/webkit/WebView;->setDataDirectorySuffix(Ljava/lang/String;)V

    :done
    return-void
.end method"""


def cluster(source: Path, probe: Path, engine_count: int, debuggable: bool = False):
    """Add the engine-cluster manifest entries, smali classes and probe (same edits as build_engine_cluster_apk.ps1)."""
    if not 1 <= engine_count <= 24:
        raise ValueError("The cluster APK supports between 1 and 24 engine processes.")
    package = "nullsroyale.rel.free"
    manifest = source / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    application_anchor = '<application android:allowBackup="true"'
    manifest_anchor = '        <meta-data android:name="io.sentry.auto-init"'
    if application_anchor not in text or manifest_anchor not in text:
        raise ValueError("Could not find the application manifest insertion point.")
    named = '<application android:name="com.supercell.clashroyale.EngineClusterApplication"'
    text = text.replace(application_anchor, named + ' android:allowBackup="true"')
    if debuggable:
        text = text.replace(named, named + ' android:debuggable="true"')
    activities = [
        '        <activity android:configChanges="keyboard|keyboardHidden|navigation|orientation|screenLayout|'
        'screenSize|smallestScreenSize" android:excludeFromRecents="true" android:exported="true" '
        f'android:launchMode="singleTask" android:name="com.supercell.clashroyale.Engine{engine}App" '
        f'android:process=":engine{engine}" android:screenOrientation="sensorPortrait" '
        f'android:taskAffinity="{package}.engine{engine}" android:theme="@android:style/Theme.NoTitleBar.Fullscreen" '
        'android:windowSoftInputMode="adjustNothing|stateUnchanged"/>'
        for engine in range(engine_count)
    ]
    text = text.replace(manifest_anchor, "\r\n".join(activities) + "\r\n" + manifest_anchor)
    manifest.write_bytes(text.encode("utf-8"))
    smali = source / "smali/com/supercell/clashroyale"
    (smali / "EngineClusterApplication.smali").write_bytes(CLUSTER_APPLICATION_SMALI.encode("utf-8"))
    for engine in range(engine_count):
        name = f"Engine{engine}App"
        (smali / f"{name}.smali").write_bytes(
            (
                f".class public Lcom/supercell/clashroyale/{name};\n.super Lcom/supercell/clashroyale/GameApp;\n\n\n"
                "# direct methods\n.method public constructor <init>()V\n    .locals 0\n\n"
                "    invoke-direct {p0}, Lcom/supercell/clashroyale/GameApp;-><init>()V\n\n"
                "    return-void\n.end method"
            ).encode("utf-8")
        )
    destination = source / "lib/arm64-v8a/libcrprobe.so"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(probe, destination)


def verify(apk: Path, probe: Path):
    release = supported_engine()
    with zipfile.ZipFile(apk) as archive:
        for name, expected in (
            ("lib/arm64-v8a/libg.so", release["libg_sha256"]),
            ("lib/arm64-v8a/libcrprobe.so", sha256(probe)),
        ):
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError(f"Output APK payload mismatch: {name}")
        if any(name.startswith("assets/firstlight/") for name in archive.namelist()):
            raise ValueError("Offline APK must reuse device resources, not bundle an update")
    report = {
        "schema": "firstlight-offline-build.v1",
        "apk_sha256": sha256(apk),
        "probe_sha256": sha256(probe),
        "resources": "existing-device-update",
        "supported_engine": release,
    }
    apk.with_suffix(".build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Verified output payload and wrote build receipt", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-input")
    p.add_argument("--input-apk", type=Path, required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--input-apk", type=Path, required=True)
    p.add_argument("--runtime-update", type=Path)
    p.add_argument("--workspace", type=Path, required=True)
    p = sub.add_parser("patch")
    p.add_argument("--source", type=Path, required=True)
    p = sub.add_parser("cluster")
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--probe", type=Path, required=True)
    p.add_argument("--engine-count", type=int, required=True)
    p.add_argument("--debuggable", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("--apk", type=Path, required=True)
    p.add_argument("--probe", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "check-input":
        if sha256(args.input_apk) != supported_engine()["apk_sha256"]:
            raise ValueError("Input APK SHA-256 differs from supported_engine.json")
        print("Verified original APK", flush=True)
    elif args.command == "prepare":
        prepare(args.input_apk, args.runtime_update, args.workspace)
    elif args.command == "patch":
        patch(args.source)
    elif args.command == "cluster":
        cluster(args.source, args.probe, args.engine_count, args.debuggable)
    else:
        verify(args.apk, args.probe)


if __name__ == "__main__":
    main()
