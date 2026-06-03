# WinFileForMacOS
## Windows Style Alternative For MacOS Finder

# WinFile XP (Starter)

A minimal Windows XP-style file explorer starter for macOS using Python and PySide6.

## Included in this starter

- Dual-pane layout (folder tree on left, details list on right)
- Navigation toolbar (Back, Forward, Up, address bar)
- Status bar (item count and selected size)
- Context menu operations (Open, Rename, Delete, New Folder, Refresh)
- Drag-and-drop move between folders
- Drag selected files out to external apps and web upload drop zones
- Shortcuts: `F2` rename, `Delete` remove, `F5` refresh, `Alt+Left/Right` back/forward, `Alt+Up` or `Backspace` parent, `Ctrl+A` select all, `Enter` open, `Ctrl+C/X/V` copy-cut-paste, `Alt+D` or `Ctrl+L` focus path
- Right pane supports multi-selection for bulk move/delete

## Requirements

- Python 3.10+

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

## Notes

- This is an MVP starter aimed at classic explorer behavior.
- Deletion is permanent in this version (no trash integration yet).
- Drag-and-drop move now asks for confirmation with item count and destination preview.
- Delete confirmation now shows selected item names/count.

## Build

### Local unsigned app (for personal use)

```bash
make build
```

Outputs `dist/WinFileXP.app`. Drag to `/Applications` or `open dist/WinFileXP.app`.

### Trace packaged crashes (recommended when app exits immediately)

Build a console/debug variant and run it from Terminal to see the full traceback:

```bash
make trace-debug
```

This creates `dist/WinFileXP-debug/WinFileXP-debug` with PyInstaller debug logging.
If it still exits, check:

- `build/WinFileXP-debug/warn-WinFileXP-debug.txt`
- `build/WinFileXP-debug/xref-WinFileXP-debug.html`

Note: `make build` and `make trace-debug` now prefer `.venv/bin/python` when available, so GUI dependencies like `PySide6` are included from your project environment.

### Signed + notarized release (for sharing)

Requirements before running:
1. Apple Developer ID certificate in Keychain
2. Store your App-specific password: `xcrun notarytool store-credentials notarytool-profile`
3. `brew install create-dmg`
4. Edit the top of `Makefile` to set `DEV_ID`, `PROFILE`, `BUNDLE_ID`

```bash
make release
```

This runs: build → sign → dmg → notarize → staple. Outputs `dist/eXPlorer.dmg` ready to share.

## Cleanup

```bash
make clean      # remove Python caches
make clean-all  # also remove build/ dist/ *.spec
```