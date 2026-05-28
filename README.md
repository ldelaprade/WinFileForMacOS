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
- Shortcuts: `F2` rename, `Delete` remove, `F5` refresh, `Alt+Left/Right` back/forward, `Alt+Up` or `Backspace` parent, `Ctrl+A` select all, `Enter` open, `Ctrl+C/X/V` copy-cut-paste, `Alt+D` or `Ctrl+L` focus path
- Right pane supports multi-selection for bulk move/delete

## Requirements

- Python 3.10+

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
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

Outputs `dist/eXPlorer.app`. Drag to `/Applications` or `open dist/eXPlorer.app`.

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