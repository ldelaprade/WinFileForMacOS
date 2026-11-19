import argparse
import os
import platform
import runpy
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = runpy.run_path(str(ROOT / "src" / "version.py"))["APP_VERSION"]
VPK_VERSION = "1.2.161"
REPOSITORY_URL = "https://github.com/ldelaprade/WinFileForMacOS"


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


def release_runtime(system, machine):
    architectures = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
    if machine.lower() not in architectures:
        raise ValueError("Unsupported release architecture")
    targets = {"Windows": "win", "Darwin": "osx", "Linux": "linux"}
    if system not in targets:
        raise ValueError("Unsupported release operating system")
    return f"{targets[system]}-{architectures[machine.lower()]}"


def resolve_upload_token(root=ROOT):
    token = os.environ.get("VPK_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token.strip()
    token_file = root / "GITHUB_TOKEN.txt"
    if token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    raise RuntimeError("Set VPK_TOKEN or GITHUB_TOKEN, or create ignored GITHUB_TOKEN.txt.")


def upload_command(app_name, version, system, machine, vpk="vpk"):
    runtime = release_runtime(system, machine)
    output_dir = ROOT / "dist" / "releases" / runtime
    if not output_dir.is_dir():
        raise FileNotFoundError(f"Missing Velopack release directory: {output_dir}")
    return [
        vpk, "upload", "github", "--repoUrl", REPOSITORY_URL,
        "--outputDir", str(output_dir), "--channel", runtime,
        "--tag", f"v{version}", "--merge",
    ], output_dir


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
    parser.add_argument("--upload", action="store_true", help="Upload the current runtime build to GitHub.")
    args = parser.parse_args()
    try:
        args.vpk = resolve_vpk(args.vpk)
        if args.upload:
            token = resolve_upload_token()
            command, output_dir = upload_command(
                args.app_name, args.version, platform.system(), platform.machine(), args.vpk
            )
            environment = os.environ.copy()
            environment["VPK_TOKEN"] = token
            subprocess.run(command, cwd=ROOT, env=environment, check=True)
            print(f"Uploaded {args.app_name} v{args.version} assets from {output_dir}")
        else:
            command = pack_command(args.app_name, args.version, platform.system(), platform.machine(), args.vpk)
            if not Path(command[command.index("--packDir") + 1]).is_dir():
                parser.error("Missing application directory. Run make build first.")
            subprocess.run(command, cwd=ROOT, check=True)
    except (ValueError, FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()