# RetroFile
## A retro-inspired, cross-platform file manager

### On MacOS
![MacOS](macOS.png)

### On Windows
![Windows](windows.png)

### On Linux
![Linux](linux.png)

## Overview

A classic Windows-inspired file manager for macOS, Windows, and Linux, built with Python and PySide6.

## Included in this starter

- Dual-pane layout (folder tree on left, details list on right)
- Sidebar sections: Favorites, File System, Network
- Navigation toolbar (Back, Forward, Up, address bar)
- Status bar (item count and selected size)
- Context menu operations (Open, Rename, Delete/Move to Trash, New Folder..., New File..., Refresh)
- Global system Trash mode toggle beside Refresh; when enabled, selected items go to the OS Trash/Recycle Bin
- Drag-and-drop move between folders
- Drag selected files out to external apps and web upload drop zones
- Browse SMB / Samba / NFS network shares via a permanent **Network** node in the left panel
- Shortcuts: `F2` rename, `Delete` remove, `F5` refresh, `Alt+Left/Right` back/forward, `Alt+Up` or `Backspace` parent, `Ctrl+A` select all, `Enter` open, `Ctrl+C/X/V` copy-cut-paste, `Alt+D` or `Ctrl+L` focus path
- Right pane supports multi-selection for bulk move/delete
- Favorites are persisted across launches (bookmarks are restored on startup)
- Add **Recycle Bin** to Favorites to open the OS Trash/Recycle Bin and restore items
- Favorites context menu can add the current folder, Desktop, Downloads, or Recycle Bin
- Favorites can be re-ordered by drag-and-drop
- Git context menu: repository cloning and status, stage/unstage selected items, staged diffs, commits, and repository initialization
- SVN context menu: working-copy status, diffs, add selected items, commit selected items, and update
- Custom tools under **More...** in the right-pane context menu, with persistent name/command configuration
- Windows subst drives appear under Local Machine and Drives with their target paths. Right-click a mapped drive and choose **Unsubst...** to remove it. Answering **Yes** to **Drop content as well?** permanently deletes the original target folder's contents before unmapping; **No** preserves them, and **Cancel** makes no changes.

## Requirements

- Python 3.10+
- Git installed and available on `PATH` for Git operations (also required for packaged applications)
- Subversion's `svn` command installed and available on `PATH` for SVN operations (also required for packaged applications)

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

## Notes

- This is an MVP starter aimed at classic explorer behavior.
- System Trash mode is off by default, so Delete permanently removes selected items as before. Turn it on with the trash-can toolbar button beside Refresh to move items to the OS Trash/Recycle Bin instead.
- Drag-and-drop move now asks for confirmation with item count and destination preview.
- Delete confirmation now shows selected item names/count.
- To browse a network share: right-click the **Network** node in the left panel → *Connect Network Share…* and enter a network path. Use SMB URL on macOS/Linux (e.g. `smb://server/share` or `smb://server/share/folder`) and UNC path on Windows (e.g. `\\server\share` or `\\server\share\folder`). Alternatively, type it directly in the address bar and press Enter. On macOS, login prompts may appear and the share then appears under *Network*.
- Already-mounted shares (Windows/Samba/NFS/AFP) appear automatically under *Network* when the app starts.
- Right-click a mounted share under *Network* to **Browse**, **Disconnect**, or connect another share.
- Add a bookmark by dragging a folder from *File System* or *Network* into *Favorites*.
- Right-click a folder in *File System* or *Network* and choose **Add to Favorites**.
- On first launch, *Favorites* is pre-populated with Home, Desktop, and Downloads (if they exist).

## Custom Tools

Right-click the right pane and choose **More... > Configure...**. Add, edit, or
remove rows in the **Name** and **Command** columns, then choose **OK** to save.
Saved tools appear above **Configure...** and remain available across launches.

Commands contain an executable and optional arguments, such as `code --reuse-window`
or `open -a "TextEdit"` on macOS. Quote executable paths or arguments containing
spaces. The full path of the right-clicked file or folder is appended as one
argument. Right-clicking blank space passes the current folder instead, even if
an item is selected. The working directory is the folder displayed in the right
pane. Commands run directly, without shell expansion, pipes, or redirection.

## Git Integration

Right-click in the file pane and open **Git**. Less-used **Clone Repository...**
and **Initialize Repository Here...** actions are under **More**. Clone accepts
an HTTPS, SSH, `git://`, or local repository URL and checks it out into a chosen
parent folder. The destination name is suggested from the URL and can be edited;
the destination must be new or empty. Use a configured Git credential helper or
SSH agent for private repositories; terminal prompts are disabled.

Other repository actions use the repository containing the current folder,
including when browsing a subfolder. **Staging...**
and **Unstaging...** are available without selecting files in the explorer.
**Staging...** lists repository-wide **Modified Files** and **Untracked Files** in
separate sections: check files and click **Stage**; each section has **Check All**
and **Uncheck All** controls. **Unstaging...** lists staged files unchecked: check
the files to remove from staging, or use **Check All** to select every staged file.
**Uncheck All** clears the selection. **Close** leaves the index unchanged.
Unstaging preserves working files, including before the first commit. **View Staged
Changes** shows the changes that **Commit Staged Changes** will commit; other
working changes are not included.

**Repository Status** shows local changes and the current branch. Switch between
local branches, select a remote branch and create a tracking branch (the local
name is editable), or create and switch to a local branch without an upstream.
Branches without an upstream can be published to a selected remote, which also
sets the upstream (equivalent to `git push -u origin <branch>`). Switches that
would overwrite local changes are refused by Git. **Fetch** updates and prunes
remote branch references; **Push** and **Pull** use the configured remote and
upstream. Pull is fast-forward-only and refuses divergent history.
Commands use existing Git authentication without terminal credential prompts,
and display their output or errors in a dialog.

**Initialize Repository Here** creates a repository in the current folder after
confirmation, but refuses folders already inside a repository. Git commands run
in a background worker. Missing Git, repository errors, hook failures, and missing
commit identity are reported in a dialog. Configure identity with `git config
--global user.name "Your Name"` and `git config --global user.email "you@example.com"`.

This initial increment uses GitPython and standard Git, without hosting-service
APIs. Status overlays are not yet implemented. No discard, hard-reset, or
force-push actions are exposed.

## SVN Integration

Browse an existing SVN working copy, right-click in the file pane, and open
**SVN**. **Working Copy Status** and **Update** operate on the current folder
and its descendants. The status dialog also offers **Update** and **Close**.
**Diff** shows local changes for the selected items, or the current folder if
nothing is selected.

Use **Add from Status...**, **Delete from Status...**, or **Commit from Status...**
to open a checklist of working-copy items; explorer selection is not required.
Add lists unversioned files and folders for scheduling. Delete lists only
versioned items, asks for confirmation, removes checked items from the working
copy, and schedules their deletion on the next commit. Commit asks for a message and
sends only checked versioned changes directly to the SVN server. Selecting a
folder includes its descendants. SVN has no Git-style staging or Push.

Commands run in a background worker with a 120-second timeout per command and
use existing SVN configuration and cached credentials. If cached authentication
fails, the app prompts for a username and masked password, then retries once.
Credentials supplied in the prompt are not saved to the SVN authentication
cache. The SVN client receives the password as a command-line argument for that
retry, so use this only on a trusted machine. Failures and Update conflict
reports are displayed in a dialog; conflicts must be resolved outside this app.
**Checkout...** opens a dialog for an SVN URL and local parent folder. The local
checkout folder name is suggested from the URL and can be changed. The checkout
destination must be new or empty; after checkout, the app opens the working copy.
Authentication uses the SVN prompt described above. Revert and conflict
resolution are not included in this initial integration.

## Build

### Local unsigned app (for personal use)

```bash
make build
```

Outputs `dist/RetroFile.app`. Drag to `/Applications` or `open dist/RetroFile.app`.

To package an unsigned DMG without an Apple Developer certific
```bash
make unsigned-dmg
```

This creates `dist/RetroFile.dmg` using `hdiutil` (or `create-dmg` if installed).

### Trace packaged crashes (recommended when app exits immediately)

Build a console/debug variant and run it from Terminal to see the full traceback:

```bash
make trace-debug
```

This creates `dist/RetroFile-debug/RetroFile-debug` with PyInstaller debug logging.
If it still exits, check:

- `build/RetroFile-debug/warn-RetroFile-debug.txt`
- `build/RetroFile-debug/xref-RetroFile-debug.html`

Builds automatically check `.venv`, `.venv-*`, then `python3` and `python`, skipping
candidates that fail the Python 3.10+ and pip-import checks. An older environment
is not deleted or upgraded. To select an interpreter explicitly, use:

```bash
make build PYTHON=python3
```

You can also set `PYTHON` to a supported virtual environment's interpreter path.

### Ubuntu/Debian installer

On Ubuntu or another Debian-based Linux distribution, `make release` builds a
minimal native `.deb` installer. It includes the self-contained application,
desktop launcher, and application icons:

```bash
make release
sudo apt install ./dist/RetroFile_2.0.1_amd64.deb
```

The package installs the application under `/opt/RetroFile` and adds **RetroFile** to the desktop application menu. Set a different package version when needed:

```bash
make release DEB_VERSION=1.1.0
```

`make build-deb` remains available to build the package directly.

To remove it later:

```bash
sudo apt remove retrofile
```

### Windows installer

Install [Inno Setup 6](https://jrsoftware.org/isdl.php) once, or use:

```powershell
winget install --id JRSoftware.InnoSetup --exact
```

Then build the executable and a minimal Windows installer:

```powershell
make release
```

This creates `dist/RetroFile-2.0.1-Setup.exe`. The installer installs for the
current user without administrator rights, adds a Start menu shortcut, offers
an optional desktop shortcut, and registers an uninstaller in Windows Settings.
Python is bundled; users do not need to install it separately. Git and SVN
remain optional external tools for their respective features.

To set a release version or an explicit Inno Setup compiler path:

```powershell
make release WIN_VERSION=2.0.2
make release ISCC="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
```

The compiler is detected on `PATH` or in standard Inno Setup 6 installation
directories. Use `make build-windows` for just the portable executable.
The installer is unsigned, so Windows may display a SmartScreen warning.

### macOS signed + notarized release (for sharing)

Requirements before running:
1. Apple Developer ID certificate in Keychain
2. Store your App-specific password: `xcrun notarytool store-credentials notarytool-profile`
3. `brew install create-dmg`
4. Edit the top of `Makefile` to set `DEV_ID`, `PROFILE`, `BUNDLE_ID`

```bash
make release
```

This runs: build → sign → dmg → notarize → staple. Outputs `dist/RetroFile.dmg` ready to share.

## Cleanup

```bash
make clean      # remove Python caches
make clean-all  # also remove build/ and dist/
```

Cleanup supports Windows PowerShell and macOS/Linux. PyInstaller `.spec` files,
source files, and icon resources are preserved by both targets.