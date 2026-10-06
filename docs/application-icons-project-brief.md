# Cross-Platform Application Icon Project

Status: Draft planning brief, pending scope, artwork, and identity approval.
Prepared: 2026-10-05.

This document captures the requested project before implementation. It is not
an approved, execution-ready migration plan. There is no existing formal feature
specification, constitution, or approved artwork design supplied for this work;
formal implementation-plan checkpoints are deferred until those inputs are
approved. No application code or third-party artwork is changed by this brief.

## Objective

Give WinFile XP a recognizable, coherent application identity across Windows,
macOS, and Linux, from installation and launchers to running windows. Preserve
the application's existing XP-inspired appearance and behavior. Treat optional
in-app icon replacement as a separate workstream, not an implicit scope expansion.

## Verified Starting Point

- Runtime: Python and PySide6; startup and window creation are in
  `src/file_explorer.py`, in `run()` and `ExplorerWindow.__init__()`.
- There is no explicit application/window icon setup in the inspected startup.
- Application naming currently differs: `WinFile`, `WinFile XP`, and `WinFileXP`.
  Harmonization must not silently change existing QSettings storage keys.
- `Makefile` builds macOS bundles/DMGs, a Windows executable, and Linux
  executables/Debian packages. Its Windows command explicitly uses `--icon NONE`.
- The generated Linux desktop entry has no `Icon=` field.
- `WinFileXP.spec` currently disables the icon. It already has a local change;
  implementation must inspect and preserve that change.
- `WinFileXP-debug.spec` is another packaging entry point to account for.
- `src/ui_theme.py` owns generic file/folder icons and tree branch arrows.
- `src/file_explorer.py` draws toolbar/view-mode icons and an FTP-sharing badge.
- Native macOS application-bundle icons and thumbnail previews already have
  special handling; optional theme work must preserve those behaviors.

## Artwork Provenance

The application icon is original, self-drawn placeholder artwork: a yellow square
with a brown border and transparent outer padding. No third-party icon artwork
was imported or used to create it.

- Editable 1024-pixel master:
  `resources/icons/application/source/application-master-1024.png`.
- Generation and validation tooling: `tools/build_icons.py`, using Pillow and
  icnsutil as build-time dependencies, not sources of artwork.
- Platform outputs: size-labelled PNGs in `resources/icons/application/png/`,
  Windows `resources/icons/application/windows/application.ico`, and macOS
  `resources/icons/application/macos/application.icns`.
- Editing and regeneration instructions: `resources/icons/README.md`.

Keep the editable master and generated outputs together in version control.
Release builds must use local resources without downloading external artwork.
If third-party artwork is introduced later, document its provenance and applicable
license or permission before including it in a release.

## Proposed Requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| REQ-001 | Document permission and provenance for all shipped artwork. | Mandatory |
| REQ-002 | Approve a distinct visual identity and readable small/large variants. | Mandatory |
| REQ-003 | Generate repeatable platform assets from approved source artwork. | Mandatory |
| REQ-004 | Apply runtime icons to all application windows and appropriate dialogs. | Mandatory |
| REQ-005 | Integrate Windows executable, shortcut, and running-app identity. | Mandatory |
| REQ-006 | Integrate macOS bundle, Finder, Dock, launcher, and switcher identity. | Mandatory |
| REQ-007 | Integrate Linux desktop entry, installed icons, and running-app identity. | Mandatory |
| REQ-008 | Include assets in existing release/debug builds without resource-path failures. | Mandatory |
| REQ-009 | Verify applicable surfaces, scaling, OS states, and install/update behavior. | Mandatory |
| REQ-010 | Document asset maintenance, packaging, evidence, and known limitations. | Mandatory |
| REQ-011 | Inventory optional in-app icons and application-status indicators. | Discovery only |

## Platform Coverage

Core means coverage through current runtime/packaging mechanisms. Conditional
means applicable only when an associated integration exists; it is not a promise
to introduce an installer, notification system, or file association.

| Platform | Surface | Classification / Integration |
| --- | --- | --- |
| Windows | Desktop executable; File Explorer list through large-icon views | Core: embedded multi-size ICO |
| Windows | Desktop and Start-menu shortcuts; Windows Search | Core verification with representative shortcuts; automatic installation is conditional |
| Windows | Title-bar corner; system menu; secondary windows/dialogs | Core: Qt runtime window icon where native decoration supports it |
| Windows | Running/pinned taskbar button; grouping; window previews | Core: runtime icon plus consistent process/shortcut identity |
| Windows | Alt+Tab and Task View | Core: inspect native icon/preview presentation |
| Windows | Installer, Installed Apps, UAC, Open With, file/protocol associations | Conditional: separate installer/registration metadata |
| macOS | Finder Applications/Desktop; list/icon/gallery; Get Info/Quick Look | Core: bundle ICNS and icon metadata; OS decides presentation |
| macOS | Dock running/retained icon; Command+Tab | Core: packaged bundle identity; inspect runtime override behavior |
| macOS | Launchpad or OS-version equivalent; Spotlight | Core: installed bundle, subject to OS indexing/launcher behavior |
| macOS | Application icon inside DMG | Core: copied bundle retains icon |
| macOS | Custom DMG-file or mounted-volume icon | Optional: separate distribution artwork/metadata |
| macOS | Window-title proxy icon | Not an application-logo surface; only if represented-file behavior is approved |
| macOS | Notifications, permission dialogs, Open With | Conditional: corresponding OS integrations |
| Linux | Applications menu, search, desktop launcher | Core: desktop entry and installed icon-theme assets; desktop launchers vary by environment |
| Linux | Running/pinned panel/dock; switcher/overview | Core: desktop identity matching under supported X11/Wayland environments |
| Linux | Title-bar icon and secondary windows | Core where the window manager/compositor shows it |
| Linux | File-manager launcher presentation | Core: desktop launcher; a raw ELF executable need not show a custom icon |
| Linux | Software center, AppImage, Flatpak, Snap, notifications, associations | Conditional: AppStream or packaging/integration-specific metadata |

## Visual and Behavioral States

- Inspect small, medium, large, and high-DPI/Retina representations.
- Inspect contrast on light/dark desktop backgrounds and high-contrast settings;
  this does not introduce a new application-wide dark theme.
- Inspect OS-rendered hover, selection, active/inactive, minimized, pinned,
  not-running, launching, and multi-window presentations.
- Leave native shortcut arrows, shields, running dots, focus highlights, and
  other OS decorations to the OS. Do not bake them into the base icon.
- Inventory busy/progress, failure, notification count, and active FTP-sharing
  indicators, but implement none until their semantics and scope are approved.
- Do not add a tray/menu-bar presence or replace native window controls implicitly.

## Artwork and Asset Contract

- Keep an editable original and an approved 1024-pixel raster export with alpha.
  Prefer a genuine scalable original where the chosen art style supports it.
- Use optically adjusted small variants where fine details disappear. Do not
  upscale a 48-pixel PNG and claim it is a high-quality 1024-pixel original.
- Windows ICO target frames: 16, 20, 24, 32, 40, 48, 64, 128, and 256 pixels.
  Validate actual embedded frames; avoid unnecessary legacy color-depth variants.
- macOS ICNS: standard 16/32/128/256/512 point representations and their Retina
  counterparts, up to 1024 pixels. Let the encoder handle shared pixel sizes.
- Linux PNG target sizes: 16, 24, 32, 48, 64, 128, 256, and 512 pixels; scalable
  SVG only if an approved vector original exists and renders correctly.
- Qt runtime: load an approved multi-size icon from a packaged resource, without
  depending on the process working directory or a user cache.
- Preserve transparency and intended padding; inspect edge halos and clipping.
- Use established image encoders rather than inventing ICO/ICNS serialization.
  Conversion dependencies should be build-time only where practical.

## Work Plan and Approval Gates

Each phase depends on the preceding gate. Windows/macOS/Linux integration can
proceed independently after the shared artwork and runtime contract are approved.
Task paths below are proposed destinations, not files already created.

### Phase 1: Scope and Rights (P1.1)

- [ ] T001 [Plan:1.1] Approve this brief's requirements, platform support versions, and core/conditional boundaries in `docs/application-icons-project-brief.md`.
- [ ] T002 [Plan:1.1] Approve visible name, stable application IDs, artwork direction, and QSettings compatibility policy in this brief.
- [ ] T003 [Plan:1.1] Create an approved asset provenance manifest at `assets/icons/manifest.json`, or document original-art ownership there.
- [ ] T004 [Plan:1.1] Obtain the approved feature/artwork specification and project constraints, then create the formal implementation plan and traceability checkpoints under `docs/application-icons/`.

Gate A: No import or redistribution of unverified artwork; no implementation until
scope, identity, specification, and artwork sourcing are approved.

### Phase 2: Artwork and Reproducible Conversion (P2.1)

- [ ] T005 [Plan:2.1] Place approved editable artwork and small-size variants under `assets/icons/source/`; review contact sheets at actual display sizes.
- [ ] T006 [Plan:2.1] Add conversion tooling at `tools/build_icons.py` using a selected established encoder; record build dependencies and versions.
- [ ] T007 [Plan:2.1] Produce validated ICO, ICNS, and PNG outputs under `assets/icons/generated/`, with traceability to the manifest.

Gate B: Rights evidence complete; transparent, sharp assets approved at small and
large sizes; regeneration works without fetching external artwork.

### Phase 3: Shared Runtime Integration (P3.1)

- [ ] T008 [Plan:3.1] Add one shared icon/resource-loading abstraction at `src/application_identity.py`; choose packaged files versus Qt resources explicitly.
- [ ] T009 [Plan:3.1] Wire startup and inherited window/dialog icons in `src/file_explorer.py`; verify new windows and source/frozen launch modes.

Gate C: No null icons, working-directory dependence, user-cache dependence, or
startup/navigation regression. Preserve existing settings and native file icons.

### Phase 4: Platform Packaging (P4.1-P4.3)

- [ ] T010 [Plan:4.1] Wire Windows ICO and runtime assets in `Makefile`, `WinFileXP.spec`, and applicable `WinFileXP-debug.spec` paths, preserving local edits.
- [ ] T011 [Plan:4.1] Set a stable Windows AppUserModelID before creating windows via `src/application_identity.py`; document matching shortcut identity and verify pinning/grouping.
- [ ] T012 [Plan:4.2] Wire macOS ICNS, bundle identifier, icon metadata, and runtime assets through the actual `Makefile` bundle-generation path; confirm direct-spec builds only if supported.
- [ ] T013 [Plan:4.2] Check DMG copying/signing order in `Makefile`; final icon resources must be in place before signing and notarization.
- [ ] T014 [Plan:4.3] Install Linux icons under the appropriate `hicolor` directories and add matching `Icon=`/desktop identity metadata in `Makefile`.
- [ ] T015 [Plan:4.3] Set Qt desktop-file identity and assess actual X11 window-class/Wayland app-ID matching in `src/application_identity.py`; do not assume one property solves every environment.
- [ ] T016 [Plan:4.1,4.2,4.3] Resolve Makefile/spec ownership and document canonical build entry points in `README.md`; prevent divergent/generated specs from silently losing icons.

Gate D: Native Windows, macOS, and Linux builds contain the intended artwork and
metadata. A Windows-only run is not evidence that macOS/Linux packaging passed.

### Phase 5: Optional UI/Status Discovery (P5.1)

- [ ] T017 [Plan:5.1] Inventory toolbar/view modes, file/folder/drive types, branch arrows, network locations, dialog symbols, FTP badges, and thumbnail placeholders across `src/file_explorer.py`, `src/ui_theme.py`, `src/network_panel.py`, `src/dialogs.py`, and `src/thumbnail_previews.py`.
- [ ] T018 [Plan:5.1] Record normal/disabled/hover/pressed/checked/selected/expanded/loading/error/shared states and platform-native fallbacks in this brief; obtain approval before any replacement.

Gate E: Discovery only. Do not change file associations, thumbnail logic, global
desktop themes, or UI artwork as a side effect of application branding.

### Phase 6: Acceptance and Release Evidence (P6.1)

- [ ] T019 [Plan:6.1] Record asset decode/frame/alpha checks and source/frozen runtime checks in `docs/application-icons/acceptance.md`.
- [ ] T020 [Plan:6.1] Record native-platform screenshots and install, upgrade, uninstall, multi-window, pin/unpin, scaling, and light/dark-shell results in `docs/application-icons/acceptance.md`.
- [ ] T021 [Plan:6.1] Document cache troubleshooting, provenance updates, regeneration, canonical builds, known exceptions, and rollback in `README.md` and `docs/application-icons/acceptance.md`.

Gate F: All mandatory applicable surfaces pass with evidence; conditional or
unsupported surfaces explicitly marked not applicable or deferred. No release
with missing permission evidence. This brief defines acceptance checks, not new
automated test files; choose test scope during formal planning.

## Proposed Acceptance Matrix

| Environment | Minimum checks |
| --- | --- |
| Windows, supported versions to approve | Explorer sizes; title bar; Alt+Tab; pinned/running taskbar; two windows; representative Start/Desktop shortcuts; 100/150/200% scaling |
| macOS, supported versions to approve | Installed .app in Finder; Dock retained/running; Command+Tab; Spotlight/launcher when indexed; standard/Retina display; DMG; signed release if credentials available |
| Linux, supported distro/desktop set to approve | Debian install/remove; GNOME Wayland and an X11 desktop such as Xfce; KDE if targeted; menu/search; pinned/running identity; switcher; decorations where supported; fractional scaling |
| All | Launch from a different working directory; packaged resources present; no opaque icon background; small-size readability; multiple windows/dialogs; upgrade from previous icon |

Keep first-install and stale-cache observations separate. Test fresh installs and
normal upgrades before any cache reset. Do not delete system caches as a default
fix. Builds need their native OS; macOS signing additionally needs approved
credentials. Lack of an OS runner must be reported as unverified, not passed.

## Requirement Mapping

| Requirement | Plan items | Tasks | Expected evidence |
| --- | --- | --- | --- |
| REQ-001 | P1.1, P2.1 | T003-T007 | Approved manifest, permission/notices, generated-asset provenance |
| REQ-002 | P1.1, P2.1 | T002, T005, T007 | Approved identity and small/large contact sheets |
| REQ-003 | P2.1 | T006-T007 | Repeatable encoder outputs and frame validation |
| REQ-004 | P3.1 | T008-T009 | Runtime icon setup; new-window/dialog observations |
| REQ-005 | P4.1, P6.1 | T010-T011, T019-T020 | Windows package metadata and native screenshots |
| REQ-006 | P4.2, P6.1 | T012-T013, T019-T020 | Bundle icon/ID metadata, Dock/Finder/DMG evidence |
| REQ-007 | P4.3, P6.1 | T014-T015, T019-T020 | Installed icons/desktop entry; X11/Wayland matching |
| REQ-008 | P3.1, P4.1-P4.3 | T008-T016 | Source/frozen launch results and canonical build documentation |
| REQ-009 | P6.1 | T019-T020 | Per-platform acceptance record, explicit exceptions |
| REQ-010 | P1.1, P4.1-P4.3, P6.1 | T004, T016, T021 | Approved plan/checkpoints, maintenance and rollback instructions |
| REQ-011 | P5.1 | T017-T018 | Optional UI/status inventory and scope decision |

## Risks and Decisions Needed

| Item | Proposed handling |
| --- | --- |
| Artwork rights | Prefer original or individually verified licensed art; block unverified imports. |
| App versus Microsoft branding | Distinct WinFile artwork; XP-inspired style does not imply Microsoft endorsement. |
| Insufficient large source images | Create appropriate original high-resolution art, rather than enlarge tiny PNGs. |
| Platform identity mismatch | Approve stable IDs; verify pinned/running matching on each targeted shell. |
| macOS bundle icon versus runtime override | Verify both source runs and packaged Dock behavior before deciding overrides. |
| Icon caching | Record fresh-install/update evidence; document targeted OS-specific troubleshooting. |
| Build/spec drift | Choose a canonical owner and make every supported build entry point consistent. |
| Native environments unavailable | Schedule native acceptance; disclose unverified platforms. |
| Scope expansion | In-app replacement, status badges, installer development, and new package formats need separate approval. |

Approval decisions: artwork sourcing route; visual concept; visible name and stable
IDs; minimum Windows/macOS/Linux versions and desktops; application branding only
versus additional UI work. Recommended first release: application branding only,
with a distinct original XP-inspired icon and the existing EXE/.app/DMG/.deb paths.

## Delivery and Rollback

Milestones: approved scope/rights -> approved artwork -> runtime integration ->
native packages -> acceptance evidence -> release. Estimate effort only after
artwork readiness, platform matrix, and access to native runners are confirmed.

Completion means all mandatory requirements are evidenced, optional surfaces have
an explicit disposition, notices are shipped where required, and a clean checkout
can reproduce the approved assets and packages. Assets, resource loading, and
packaging changes should be reviewable independently. If integration fails,
restore the preceding known-good application identity/package through a normal
reviewed change, retaining provenance records and without reverting unrelated
user edits.