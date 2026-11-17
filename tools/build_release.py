import argparse
import platform
import runpy
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = runpy.run_path(str(ROOT / "src" / "version.py"))["APP_VERSION"]
VPK_VERSION = "1.2.161"


def pack_command(app_name, version, system, machine, vpk="vpk"):
    if version != APP_VERSION:
        raise ValueError("Release version must match APP_VERSION in src/version.py.")
    architectures = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    if machine.lower() not in architectures:
        raise ValueError(f"Unsupported release architecture: {machine}")
    targets = {
        "Windows": ("win", f"{app_name}.exe", "windows/application.ico"),
        "Darwin": ("osx", app_name, "macos/application.icns"),
        "Linux": ("linux", app_name, "png/application-256.png"),
    }
    target, executable, icon = targets[system]
    runtime = f"{target}-{architectures[machine.lower()]}"
    directory = ROOT / "dist" / (f"{app_name}.app" if system == "Darwin" else app_name)
    return [
        vpk, "pack", "--packId", "RetroFile", "--packTitle", app_name,
        "--packVersion", version, "--packDir", str(directory),
        "--mainExe", executable, "--runtime", runtime, "--channel", runtime,
        "--icon", str(ROOT / "resources" / "icons" / "application" / icon),
        "--outputDir", str(ROOT / "dist" / "releases" / runtime),
    ]


def resolve_vpk(vpk, root=ROOT):
    if shutil.which(vpk):
        return vpk
    if vpk != "vpk":
        raise FileNotFoundError(f"Velopack CLI was not found: {vpk}")

    executable = root / ".velopack" / ("vpk.exe" if platform.system() == "Windows" else "vpk")
    if executable.is_file():
        return str(executable)

    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise FileNotFoundError("Velopack requires the .NET SDK. Install it, then rerun the release build.")
    subprocess.run(
        [dotnet, "tool", "install", "--tool-path", str(executable.parent), "vpk", "--version", VPK_VERSION],
        cwd=root,
        check=True,
    )
    if not executable.is_file():
        raise FileNotFoundError(f"VeloPack installation did not create {executable}")
    return str(executable)


def main():
    parser = argparse.ArgumentParser(description="Package the native build with Velopack 1.2.161.")
    parser.add_argument("--app-name", default="RetroFile")
    parser.add_argument("--version", default=APP_VERSION)
    parser.add_argument("--vpk", default="vpk")
    args = parser.parse_args()
    try:
        args.vpk = resolve_vpk(args.vpk)
        command = pack_command(args.app_name, args.version, platform.system(), platform.machine(), args.vpk)
        if not Path(command[command.index("--packDir") + 1]).is_dir():
            parser.error("Missing application directory. Run make build first.")
        subprocess.run(command, cwd=ROOT, check=True)
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()