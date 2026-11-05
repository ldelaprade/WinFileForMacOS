import multiprocessing
import json
import os
import sys
from pathlib import Path

multiprocessing.freeze_support()

try:
    from .file_explorer import run
except ImportError:
    # PyInstaller may execute this as a top-level script where package-relative
    # imports are unavailable.

    from src.file_explorer import run


def _loaded_windows_modules() -> list[str]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enumerate_modules = kernel32.K32EnumProcessModulesEx
    enumerate_modules.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
    ]
    enumerate_modules.restype = wintypes.BOOL
    module_name = kernel32.K32GetModuleFileNameExW
    module_name.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    module_name.restype = wintypes.DWORD
    modules = (wintypes.HMODULE * 2048)()
    needed = wintypes.DWORD()
    process = wintypes.HANDLE(-1)
    if not enumerate_modules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed), 3):
        raise ctypes.WinError(ctypes.get_last_error())
    paths = []
    for module in modules[:min(len(modules), needed.value // ctypes.sizeof(wintypes.HMODULE))]:
        buffer = ctypes.create_unicode_buffer(32768)
        if module_name(process, module, buffer, len(buffer)):
            paths.append(buffer.value)
    return paths


def write_listing_diagnostics(directory: str, output: str) -> None:
    from PySide6.QtCore import QDir, QFileInfo

    report = {
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "directory": os.path.abspath(directory),
        "python_entries": [],
        "qt_entries": [],
        "modules": _loaded_windows_modules(),
    }
    with os.scandir(directory) as entries:
        report["python_entries"] = [entry.name for entry in entries]
    report["qt_entries"] = [
        {"name": info.fileName(), "hidden": info.isHidden(), "exists": info.exists()}
        for info in QDir(directory).entryInfoList(
            QDir.AllEntries | QDir.Hidden | QDir.System | QDir.NoDotAndDotDot
        )
    ]
    report["reported_paths"] = [
        {"name": name, "exists": QFileInfo(os.path.join(directory, name)).exists()}
        for name in ("ZZZZZ1802596832.doc", "ZZZZZ4270261263.doc")
    ]
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--diagnose-listing":
        write_listing_diagnostics(sys.argv[2], sys.argv[3])
    else:
        run()
