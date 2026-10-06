# Application Icon Resources

The initial artwork is an original yellow square with a brown border and
transparent outer padding. No third-party icons are included.

These are application-identity placeholders, not toolbar, folder, file-type,
network, or status icons. Those are separate optional workstreams. Runtime startup
loads the ICO for application windows. `make build-windows` embeds it in the EXE
and includes a copy for runtime loading. macOS/Linux packaging is not wired yet.

## Files and Functions

Paths below are relative to this directory.

| File | Function |
| --- | --- |
| `application/source/application-master-1024.png` | Editable high-resolution master for rebuilding every platform output. |
| `application/windows/application.ico` | Windows executable and inherited shortcut icons; usable by Qt for window/taskbar identity. Includes 16, 20, 24, 32, 40, 48, 64, 128, and 256-pixel frames. |
| `application/macos/application.icns` | macOS application bundle: Finder, Dock, launcher, and application switcher. Contains standard and Retina representations up to 1024 pixels. |
| `application/png/application-16.png` | Small list/menu and window-icon representations. |
| `application/png/application-20.png` | Small icons at 125% display scaling. |
| `application/png/application-24.png` | Small icons at 150% scaling; medium menu/list representations. |
| `application/png/application-32.png` | Medium shell/taskbar icons; small icons at 200% scaling. |
| `application/png/application-40.png` | 32-pixel representations at 125% scaling. |
| `application/png/application-48.png` | Medium/large shell representations and 32-pixel icons at 150% scaling. |
| `application/png/application-64.png` | Large launcher representations and 32-pixel icons at 200% scaling. |
| `application/png/application-128.png` | Large launcher/file-manager views. |
| `application/png/application-256.png` | Extra-large shell/launcher views and Windows ICO's largest frame. |
| `application/png/application-512.png` | High-resolution launchers and macOS bundle representations. |
| `application/png/application-1024.png` | Highest-resolution macOS representation; full-resolution runtime/export asset. |

Sizes are physical pixels, not guarantees about which image a shell will choose.
One application identity is shared across surfaces. Do not create separate
taskbar, Dock, and desktop artwork unless intentionally introducing a distinct
identity. Linux will use the PNG set with a desktop entry and installed icon-theme
paths; a raw executable does not need another embedded icon container.

There is no SVG placeholder: PNG is directly editable, and an SVG is useful only
when you choose a vector editing workflow. A scalable original can be added later.

## Editing and Rebuilding

Recommended: edit the master PNG, keeping its 1024x1024 dimensions and alpha
channel, then regenerate the outputs. PNG files can also be edited independently
for optical corrections at small sizes, but rebuilding from the master discards
those output-only edits. ICO and ICNS are multi-image containers; changing a PNG
does not automatically update either container.

Install the generation-only dependency from the repository root:

```sh
python -m pip install -r tools/requirements-icons.txt
```

Rebuild all generated assets from the edited master, explicitly permitting
replacement of generated files:

```sh
python tools/build_icons.py --overwrite-generated
```

Check dimensions, container frames, decoding, and nonempty alpha without writing:

```sh
python tools/build_icons.py --check
```

The command without flags refuses to replace existing generated outputs. The
`--create-placeholder` mode is for initial creation only and refuses to replace
any existing master/output artwork, even with an overwrite flag. It draws each
PNG size separately to keep the initial border crisp. Pillow and icnsutil assemble
the Windows/macOS containers from those PNGs, including small and Retina sizes.

Commit the editable master and generated outputs together. After regenerating,
run `make build-windows` and close/relaunch the application to see the updated
Windows icon. Regeneration alone does not change an existing EXE. OS icon caches
and pinned shortcuts may retain older artwork; refresh Explorer or unpin/re-pin
the rebuilt application before considering any system-cache troubleshooting.