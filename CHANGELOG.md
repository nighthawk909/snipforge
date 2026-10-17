# Changelog

All notable changes to SnipForge. From 0.4.0 on, this file is updated automatically by
release-please from [conventional commit](https://www.conventionalcommits.org/) messages.

## [0.8.0](https://github.com/nighthawk909/snipforge/compare/v0.7.0...v0.8.0) (2026-10-17)


### Features

* **settings:** a Settings window, with full control over how SnipForge updates ([#22](https://github.com/nighthawk909/snipforge/issues/22)) ([54df708](https://github.com/nighthawk909/snipforge/commit/54df7084793c1e3c18e0d705bfb05195ce4d07fe))


### Bug fixes

* moving SnipForge to another repository keeps line endings and never doubles names ([#20](https://github.com/nighthawk909/snipforge/issues/20)) ([bce61cd](https://github.com/nighthawk909/snipforge/commit/bce61cd6b2f5a593b302177e07c0bd4507b056c9))

## [0.7.0](https://github.com/nighthawk909/snipforge/compare/v0.6.0...v0.7.0) (2026-10-17)


### Features

* a new look with light and dark themes, a properties panel and quick styles ([#15](https://github.com/nighthawk909/snipforge/issues/15)) ([3a40fd0](https://github.com/nighthawk909/snipforge/commit/3a40fd03b10c75071416b558703fc9fcdba3bff9))
* **library:** a new Library with search inside your snips, favorites, tags and filters ([#19](https://github.com/nighthawk909/snipforge/issues/19)) ([f02e15f](https://github.com/nighthawk909/snipforge/commit/f02e15f017882c80027b2477d71f2a61f048872c))
* run SnipForge from your organization's own GitHub repository ([#18](https://github.com/nighthawk909/snipforge/issues/18)) ([aceab74](https://github.com/nighthawk909/snipforge/commit/aceab74a25c05d4061c84334ef2226745d5ebf08))
* **updates:** choose how SnipForge updates, try previews, roll back, and use your company's update location ([#13](https://github.com/nighthawk909/snipforge/issues/13)) ([c660f15](https://github.com/nighthawk909/snipforge/commit/c660f1536bfcbff1d379938dac3a2b6741fdddd8))


### Bug fixes

* opening SnipForge while it's already running can no longer crash it ([#12](https://github.com/nighthawk909/snipforge/issues/12)) ([4b02385](https://github.com/nighthawk909/snipforge/commit/4b023850fffc29a0cff50d1222a9138fdd875101))
* **updates:** update prompts show the version you actually have ([#17](https://github.com/nighthawk909/snipforge/issues/17)) ([b5c8639](https://github.com/nighthawk909/snipforge/commit/b5c8639e8e6bb14789dba6aecce07c97df81c8e7))

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
