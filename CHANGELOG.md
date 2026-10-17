# Changelog

All notable changes to SnipForge. From 0.4.0 on, this file is updated automatically by
release-please from [conventional commit](https://www.conventionalcommits.org/) messages.

## [0.6.0](https://github.com/nighthawk909/snipforge/compare/v0.5.0...v0.6.0) (2026-10-17)


### Features

* **install:** one-click download link, download page and company update feeds ([#9](https://github.com/nighthawk909/snipforge/issues/9)) ([7bd73cb](https://github.com/nighthawk909/snipforge/commit/7bd73cb66efefe2414670e51d80fd7a373748fd5))

## [0.5.0](https://github.com/nighthawk909/snipforge/compare/v0.4.0...v0.5.0) (2026-10-14)


### Features

* SnipForge updates itself: checks daily, asks first, then downloads, verifies and installs the new version ([b430965](https://github.com/nighthawk909/snipforge/commit/b4309658b8854cbeed7982d3609da5c47a113123))


### Bug fixes

* harden installer, crop, hotkey and release pipeline ([#3](https://github.com/nighthawk909/snipforge/issues/3)) ([bce369b](https://github.com/nighthawk909/snipforge/commit/bce369b65485af2140ca4b2f58de3284a2fc37de))
* **share:** "Teams chat, Outlook and more apps" opens the Windows share sheet instead of an "Invalid window handle" error ([b430965](https://github.com/nighthawk909/snipforge/commit/b4309658b8854cbeed7982d3609da5c47a113123))

## 0.4.0 (2026-10-14)

First public release.

### Features

* **crop:** crop now actually cuts: the removed area disappears and exports use only what you kept.
  Re-enter Crop to drag edges back out and restore anything you cut. Apply, Cancel and Show all
  buttons; Enter applies, Esc cancels.
* **share:** "Send in Teams" asks who to send to and opens that chat directly (remembers recent people).
* **installer:** per-user Windows installer (no admin rights) with Start menu and desktop shortcuts,
  optional start-at-sign-in, clean uninstall, and a portable zip.
* **app:** Check for updates and About in the tray menu.

### Bug fixes

* **drag out:** dropping a snip into Teams no longer fails with a "file is missing" error.
* **start with Windows:** works for the installed app.

## 0.3.0 (2026-10-13)

### Features

* Select, move and edit anything you've placed with any tool; hover highlights what you'd pick up.
* Callouts (bubble, box, ellipse or pill with an aimable pointer), stamps, and dashed lines.
* Share menu: Outlook draft with the snip inline, Teams, OneDrive link or sharing, Windows share sheet.

### Bug fixes

* Undoing a font or shape change on a callout restores its size.
* Quick click-then-drag moves are undoable; Esc cancels drawing, resizing and moving.
* Share no longer leaks Windows resources or freezes the editor while waiting on OneDrive.

## 0.2.0 (2026-10-13)

### Features

* Editor rebuilt: resize handles, property bar that edits the selection, per-tool styles.
* Grab text and auto-redact using Windows' built-in OCR.
* Snip library with re-editable snips, pin to screen, drag out.
* Window detection and magnifier while snipping.

## 0.1.0 (2026-10-13)

* First version: Win+Shift+S capture and a basic annotation editor.
