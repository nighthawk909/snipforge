"""Tray application: owns the hotkey, capture flow, recording, library and open windows."""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Slot
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__, logs, prefs, preset_output, presets, project, screen_access, theme, wording
from .capture import CaptureSession, on_screen
from .editor.window import EditorWindow
from .home import HomeWindow
from .hotkey import SUPPORTED as HOTKEYS_SUPPORTED
from .hotkey import HotkeyListener
from .indexer import TextIndexer
from .library import DocumentUnreadable, Library
from .pin import PinWindow
from .privacy import CHECK_VERSION as PRIVACY_CHECK
from .reopen import ReopenWatcher
from .settings_window import SECTIONS, SettingsWindow
from .shortcuts import GlobalShortcuts
from .shortcuts import label as shortcut_label
from .single_instance import InstanceServer, send_to_running
from .theme.tokens import BRAND
from .update_flow import UpdateFlow

log = logging.getLogger(__name__)
CAPTURE_DELAY_MS = 120  # let the Start menu and key-up settle before freezing the screen
REHOOK_MS = 5 * 60 * 1000  # reinstall the keyboard hook in case Windows dropped it
PRIVACY_POLL_MS = 500  # how often the GUI thread looks for the private-title sweep's result
CRASH_NOTICE_MS = 10_000  # how long the "closed unexpectedly" notice is up, and clicking it opens the report
CRASH_NOTICE_DELAY_MS = 3_000  # after start, so the notification area is there (it isn't at once at sign-in)
LOG_TICK_MS = 30_000  # how often the log session tidies up (snipforge.logs.LogSession.tick)
APP_ID = "SnipForge.SnipForge"


def build_icon(size: int = 256) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)  # drawn on a 64-unit grid, rendered at full resolution
    p.setBrush(QColor(BRAND))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QPen(Qt.white, 5, Qt.SolidLine, Qt.SquareCap))
    for x, y, dx, dy in ((16, 16, 1, 1), (48, 16, -1, 1), (16, 48, 1, -1), (48, 48, -1, -1)):
        p.drawLine(x, y, x + 12 * dx, y)
        p.drawLine(x, y, x, y + 12 * dy)
    p.end()
    return QIcon(pix)


class _Follower(QObject):
    """Calls `react` for a signal of something that lives longer; ends with its parent."""

    def __init__(self, parent: QObject, react) -> None:
        super().__init__(parent)
        self._react = react

    @Slot()
    def react(self) -> None:
        self._react()


class SnipForgeApp:
    def __init__(self, app: QApplication, library: Library | None = None) -> None:
        self._app = app
        self.library = library or Library()
        self.indexer = TextIndexer(self.library)  # reads the words in snips so Library search finds them
        self._session: CaptureSession | None = None
        self._recording = None  # snipforge.video.record_flow.RecordFlow, while one is under way
        self._windows: list = []  # editors and pins kept alive until closed
        self._home: HomeWindow | None = None
        self._settings: SettingsWindow | None = None
        self._crash_notice_until = 0.0  # clicking the tray's notice before then opens Report a problem
        self._icon = build_icon()
        app.setWindowIcon(self._icon)
        app.setQuitOnLastWindowClosed(False)

        self._hotkey = HotkeyListener()
        self._hotkey.set_keys(*prefs.hotkeys())
        self._hotkey.triggered.connect(self.capture, Qt.QueuedConnection)
        self._hook_ok = self._hotkey.start()
        self._rehook = QTimer(interval=REHOOK_MS)
        self._rehook.timeout.connect(self._hotkey.refresh)
        self._rehook.start()
        self._shortcuts = GlobalShortcuts()  # shortcuts of your own (RegisterHotKey, not the hook)
        self._shortcuts.triggered.connect(self._on_shortcut, Qt.QueuedConnection)
        shortcut_problems = self._shortcuts.apply_saved()
        shortcut_problems |= self._shortcuts.apply_presets(presets.load())
        self._build_tray()
        if shortcut_problems:
            self._tray.showMessage(
                "SnipForge", "Some of your shortcuts couldn't be set. " + " ".join(
                    f"{self._shortcuts.title(action, capital=True)}: {problem}"
                    for action, problem in shortcut_problems.items())
                + " Change them in Settings > Capture.", QSystemTrayIcon.Warning)
        app.aboutToQuit.connect(self._hotkey.stop)
        app.aboutToQuit.connect(self._shortcuts.close)
        self._forget_private_titles()
        app.aboutToQuit.connect(self.indexer.close)
        app.aboutToQuit.connect(self._stop_recording_on_quit)
        self._reopen = ReopenWatcher(app)  # a Mac: opened again from Finder or the Dock
        self._reopen.reopened.connect(self.show_home)
        QTimer.singleShot(0, screen_access.warm_up)  # a Mac's permission check, ready for the first snip

    def _forget_private_titles(self) -> None:
        """Once per version of the private-window check, in the background: clear private
        browsing windows' titles that older versions saved with snips."""
        if prefs.private_titles_checked() >= PRIVACY_CHECK:
            return
        # The worker never touches a Qt object (one destroyed meanwhile, at quit say, would crash
        # the process): it leaves its result here, and a timer on the GUI thread picks it up.
        stop, result = threading.Event(), {}
        self._app.aboutToQuit.connect(stop.set)

        def sweep() -> None:
            try:
                result["swept"] = self.library.forget_private_titles(stop.is_set)
            except Exception:
                log.exception("Couldn't check the Library for private browsing titles")

        worker = threading.Thread(target=sweep, name="snipforge-privacy", daemon=True)
        self._privacy_check = QTimer(interval=PRIVACY_POLL_MS)
        self._privacy_check.timeout.connect(lambda: self._privacy_swept(worker, result))
        worker.start()
        self._privacy_check.start()

    def _privacy_swept(self, worker: threading.Thread, result: dict) -> None:
        if worker.is_alive():
            return
        self._privacy_check.stop()
        cleared, complete = result.get("swept", (0, False))
        if cleared:
            log.info("Cleared %d private browsing titles from the Library", cleared)
        if complete:  # anything skipped (locked, quitting, or an error) is tried again next start
            prefs.set_private_titles_checked(PRIVACY_CHECK)

    # ------------------------------------------------------------------ tray
    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(self._icon)
        self._update_tray_tip()
        # Before UpdateFlow's own: a click on its "updated" notice is seen while that notice is still up.
        self._tray.messageClicked.connect(self._message_clicked)
        self._updates = UpdateFlow(self._tray)
        self._updates.ui_shows_updates = self._settings_shows_updates
        menu = QMenu()
        menu.addAction("New snip", self.capture)
        self._repeat_action = menu.addAction("Repeat last snip", self.repeat_last)
        self._repeat_action.setEnabled(prefs.last_area() is not None)  # after the first snip
        self._presets_menu = menu.addMenu("Snip with a preset")
        self._show_presets()
        # Through an object that goes with the menu, so a menu that's gone stops listening
        self._presets_follower = _Follower(menu, self._presets_changed)
        presets.changes().changed.connect(self._presets_follower.react)
        self._cancel_snip_action = menu.addAction("Cancel snip", self.cancel_snip)
        self._cancel_snip_action.setVisible(False)  # only while a snip is under way
        self._record_action = menu.addAction("Record video", self.record)
        self._stop_record_action = menu.addAction("Stop recording", self.stop_recording)
        self._stop_record_action.setVisible(False)  # only while a recording is under way
        menu.addAction("Library", self.show_home)
        menu.addAction("Settings…", self.show_settings)
        menu.addSeparator()
        menu.addAction("Check for updates…", self.check_updates)
        menu.addAction("Report a problem…", self.report_problem)
        menu.addAction("About SnipForge", self.about)
        menu.addAction("Quit", self._app.quit)
        self._menu = menu
        self._tray.setContextMenu(menu)
        if sys.platform != "darwin":  # a Mac menu bar icon opens its menu on click, and New snip is in it
            self._tray.activated.connect(
                lambda reason: self.capture() if reason == QSystemTrayIcon.Trigger else None)
        self._tray.show()
        if not self._hook_ok and HOTKEYS_SUPPORTED:
            self._tray.showMessage("SnipForge", "Couldn't install the keyboard hook, so Win+Shift+S "
                                   "won't work. Use the tray icon to snip.", QSystemTrayIcon.Warning)
        self._updates.start()

    def _presets_changed(self) -> None:
        """Presets added, changed or deleted (Settings checked each new shortcut before saving it)."""
        self._shortcuts.apply_presets(presets.load())
        self._show_presets()

    def _show_presets(self) -> None:
        """The tray's capture presets (a submenu shown only when there are any)."""
        self._presets_menu.clear()
        saved = presets.load()
        for preset in saved:
            text = preset.name.replace("&", "&&")
            if bound := self._shortcuts.bindings().get(preset.action):
                text += f"\t{shortcut_label(bound)}"
            action = self._presets_menu.addAction(text, lambda p=preset.id: self.capture_preset(p))
            action.setToolTip(preset.summary())
        self._presets_menu.addSeparator()
        self._presets_menu.addAction("Manage presets…", self.manage_presets)
        self._presets_menu.menuAction().setVisible(bool(saved))

    def manage_presets(self) -> None:
        self.show_settings("capture")
        if self._settings is not None:
            self._settings.show_presets()

    def _update_tray_tip(self) -> None:
        keys = [name for name, on in zip(("Win+Shift+S", "Print Screen"), prefs.hotkeys(), strict=True)
                if on and HOTKEYS_SUPPORTED]
        if own := self._shortcuts.bindings().get("snip"):
            keys.append(shortcut_label(own))
        click = "click this icon, then New snip" if sys.platform == "darwin" else "click this icon to snip"
        how = " or ".join(keys) if keys else click
        self._tray.setToolTip(f"SnipForge {__version__} — {how}")

    def check_updates(self) -> None:
        """Open Settings > Updates and check there (the tray flow explains when IT turned updates off)."""
        if self._updates.controller.policy.disabled:
            self._updates.check_now()
            return
        self.show_settings("updates")
        if not self._updates.controller.busy:
            self._updates.controller.check_now()

    def _settings_shows_updates(self) -> bool:
        """Settings > Updates is in front of the user right now, so it already shows what's happening."""
        s = self._settings
        return (s is not None and s.isVisible() and not s.isMinimized() and s.isActiveWindow()
                and QGuiApplication.applicationState() == Qt.ApplicationActive
                and s.current_section() == "updates")

    def show_settings(self, section: str = "general") -> None:
        if self._settings is None:
            self._settings = SettingsWindow(theme_manager=theme.get_manager(),
                                            update_controller=self._updates.controller,
                                            hotkey=self._hotkey, library_root=self.library.root,
                                            on_shortcuts_changed=self._update_tray_tip,
                                            shortcuts=self._shortcuts)
        self._settings.show_section(section if section in SECTIONS else "general")
        self._settings.showNormal()
        self._bring_to_front(self._settings)

    def about(self) -> None:
        box = QMessageBox(QMessageBox.NoIcon, "About SnipForge", about_text())
        box.setTextFormat(Qt.RichText)
        box.setIconPixmap(self._icon.pixmap(64, 64))
        report = box.addButton("Report a problem…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is report:
            self.report_problem()

    def report_problem(self) -> None:
        """Report a problem: the details for a bug report, shown before they're saved anywhere."""
        from . import report_dialog

        self._crash_notice_until = 0.0
        report_dialog.open_report(library_root=self.library.root)

    def notice_crash(self, wait: bool = True) -> None:
        """The last run ended in a crash: say so, and offer the report that has its details. Not
        over the "SnipForge was updated" notice: it gets one turn later, or none (the report keeps
        the crash either way)."""
        if self._updates.showing_whats_new:
            if wait:
                QTimer.singleShot(CRASH_NOTICE_MS + 1000, lambda: self.notice_crash(wait=False))
            return
        self._crash_notice_until = time.monotonic() + CRASH_NOTICE_MS / 1000
        self._tray.showMessage("SnipForge", "SnipForge closed unexpectedly last time. Click here to save the "
                               "details for a bug report (or choose Report a problem in SnipForge's menu later).",
                               QSystemTrayIcon.Warning, CRASH_NOTICE_MS)

    def _message_clicked(self) -> None:
        if time.monotonic() < self._crash_notice_until and not self._updates.showing_whats_new:
            self.report_problem()

    def handle_command(self, command: str) -> None:
        if command == "snip":
            self.capture()
        elif command == "repeat":
            self.repeat_last()
        elif command == "home":
            self.show_home()
        elif command == "settings":
            self.show_settings()

    # ------------------------------------------------------------------ windows
    def show_home(self) -> None:
        if self._home is None:
            self._home = HomeWindow(self.library, self.capture, self.open_entry, indexer=self.indexer,
                                    on_settings=self.show_settings, on_combine=self.combine)
        if self._home.isVisible():
            self._home.refresh()  # already open: pick up anything new (opening it refreshes by itself)
        self._home.showNormal()
        self._bring_to_front(self._home)

    def capture(self) -> None:
        if self._session is not None:
            self._session.snip_now()  # pressed again during a countdown: don't wait
            return
        if not screen_access.ready_to_snip():  # a Mac without Screen Recording: explained instead
            return
        # R switches a snip to recording, unless one is under way already
        on_area = self._record_area if self._recording is None else None
        self._start(CaptureSession(self._on_captured, on_area=on_area, on_manage_presets=self.manage_presets))

    def capture_preset(self, preset_id: str) -> None:
        """Snip with a capture preset (the tray, or its shortcut)."""
        if self._session is not None:
            self._session.snip_now()  # as the snip key does during a countdown
            return
        preset = presets.get(preset_id)
        if preset is None:
            self._note("That preset isn't there any more. Choose one in Settings > Capture.")
            return
        if not screen_access.ready_to_snip():  # a Mac without Screen Recording: explained instead
            return
        on_area = self._record_area if self._recording is None else None
        self._start(CaptureSession(self._on_captured, on_area=on_area, preset=preset,
                                   on_manage_presets=self.manage_presets))

    def record(self) -> None:
        """Record video (the tray, or its shortcut): choose an area to record. Pressed again while
        recording, it stops the recording."""
        if self._recording is not None:
            self.stop_recording()
        elif self._session is None and screen_access.ready_to_snip():
            self._start(CaptureSession(self._on_captured, on_area=self._record_area, record=True))

    def stop_recording(self) -> None:
        if self._recording is not None:
            self._recording.stop()

    def _record_area(self, area, screen) -> None:
        """The area chosen to record (physical desktop pixels): the recording takes it from here."""
        from .video import record_flow

        if self._recording is not None:
            self._note("A recording is already under way: stop it first.")
            return
        self._recording = record_flow.RecordFlow(area, screen, self.library.root, notify=self._note,
                                                 open_video=self._open_video)
        self._recording.ended.connect(self._recording_ended)
        self._record_action.setVisible(False)
        self._stop_record_action.setVisible(True)
        try:
            self._recording.start()
        except Exception as exc:  # never leave a recording that blocks every later one
            log.exception("The recording couldn't start")
            self._recording._end(f"The recording couldn't start: {exc}")

    def _recording_ended(self) -> None:
        self._recording = None
        self._record_action.setVisible(True)
        self._stop_record_action.setVisible(False)

    def _stop_recording_on_quit(self) -> None:
        """Quitting SnipForge stops a recording; the recorder finishes and keeps the file."""
        self.stop_recording()

    def _open_video(self, path) -> None:
        """A finished recording: shown in the video editor, and in the Library if it's open."""
        from .video import editor_window

        try:
            self._track(editor_window.open_video(path))
        finally:  # listed even if it can't be shown
            if self._home is not None and self._home.isVisible():
                self._home.update_entry(Path(path).parent.name)

    def _open_recording(self, path) -> None:
        """From the Library: the video editor already showing it, or a new one."""
        for window in self._windows:
            if Path(getattr(window, "path", "")) == Path(path):
                self._bring_to_front(window)
                return
        try:
            self._open_video(path)
        except Exception as exc:  # FFmpegError: a file that can't be read
            log.exception("Couldn't open the recording %s", path)
            QMessageBox.warning(None, "SnipForge", f"Couldn't open that recording: {exc}")

    def repeat_last(self) -> None:
        """Repeat last snip: the same area of the same monitor again, straight away (after the
        delay, if one is set). With nothing to repeat, or an area that's gone, a normal snip."""
        if self._session is not None:
            self._session.snip_now()
            return
        area = prefs.last_area()
        if area is not None and on_screen(area) and not screen_access.ready_to_snip():
            return  # (a normal snip, below, checks for itself)
        if area is None or not on_screen(area):
            self._note("Nothing to repeat yet: choose what to snip." if area is None
                       else "Your last snip's area isn't on screen any more, so choose what to snip.")
            self.capture()
            return
        self._start(CaptureSession(self._on_captured, repeat=area, on_note=self._note))

    def _note(self, text: str) -> None:
        self._tray.showMessage("SnipForge", text, QSystemTrayIcon.Information, 4000)

    def _start(self, session: CaptureSession) -> None:
        self._session = session
        self._cancel_snip_action.setVisible(True)
        QTimer.singleShot(CAPTURE_DELAY_MS, session.start)

    def _on_shortcut(self, action: str) -> None:
        if (preset_id := presets.from_action(action)) is not None:
            self.capture_preset(preset_id)
            return
        {"snip": self.capture, "repeat": self.repeat_last, "library": self.show_home, "record": self.record}[action]()

    def cancel_snip(self) -> None:
        """From the tray: stop a snip in progress, e.g. during a countdown, which has no keyboard."""
        if self._session is not None:
            self._session.cancel()

    def _on_captured(self, image: QImage | None) -> None:
        source = self._session.source if self._session is not None else None
        preset = getattr(self._session, "preset", None)
        note = getattr(self._session, "note", None)
        area = getattr(self._session, "area", None)
        self._session = None
        self._cancel_snip_action.setVisible(False)
        if note:  # a scrolling capture that ended early says why
            self._tray.showMessage("SnipForge", note, QSystemTrayIcon.Information, 6000)
        if image is None:
            return
        if area is not None:  # for Repeat last snip; never worth losing the snip over
            try:
                prefs.set_last_area(area)
                self._repeat_action.setEnabled(True)
            except ValueError as exc:
                log.warning("Couldn't remember the snip's area for Repeat last snip: %s", exc)
        if prefs.copy_on_capture() and preset is None:  # like Win+Shift+S: the raw snip is on the clipboard too
            QGuiApplication.clipboard().setImage(image)
        try:
            entry = self.library.create(image, source=source)
        except OSError as exc:
            log.warning("Couldn't add snip to library: %s", exc)
            entry = None
        if preset is not None:
            self._finish_preset(image, entry, preset)
        else:
            if entry is not None:
                self.indexer.index(entry)
            self._open_editor(image, entry, None)
        if self._home is not None and self._home.isVisible() and entry is not None:
            self._home.update_entry(entry.id)

    def _finish_preset(self, image: QImage, entry, preset: presets.Preset) -> None:
        """A capture preset's snip: its edges and Beautify, then the editor, the clipboard or a folder.
        It's in the Library whatever happens, so a snip that couldn't be saved isn't lost."""
        document, rendered = preset_output.with_edges(image, preset)
        kept = "" if entry is None else " It's in your Library too."
        if prefs.copy_on_capture() and preset.destination != "copy":  # as Win+Shift+S, with its effects
            copy = rendered if preset.destination == "editor" else preset_output.final_image(rendered, preset)
            QGuiApplication.clipboard().setImage(copy)
        if document is not None and entry is not None:
            try:
                self.library.save(entry, document, rendered)
            except OSError as exc:
                log.warning("Couldn't keep the preset's edges with the snip: %s", exc)
        if entry is not None:
            self.indexer.index(entry)
        if preset.destination == "editor":
            choice = None if preset.beautify == "settings" else preset_output.beautify_on(preset)
            self._open_editor(image, entry, document, beautify_on=choice)
            return
        final = preset_output.final_image(rendered, preset)
        if preset.destination == "copy":
            QGuiApplication.clipboard().setImage(final)
            self._note(f"Copied ({preset.name}).{kept}")
            return
        from .editor.export import ExportError, save_image
        try:
            path = preset_output.new_path(preset)
            saved = save_image(final, path)
            problem = "" if saved else "it couldn't be written"
        except (OSError, ExportError) as exc:
            path, problem = None, str(exc)
        if problem:
            log.warning("A preset couldn't save its snip: %s", problem)
            self._note(f"Couldn't save the snip ({preset.name}): {problem.rstrip('.')}. "
                       + ("It's in your Library, opened here." if entry is not None else "It's opened here."))
            self._open_editor(image, entry, document)
            return
        self._note(f"Saved to {path} ({preset.name}).")

    def combine(self, entry_ids: list[str], parent=None) -> None:
        """Combine snips into one (the Library's selection, or an editor's snip and others): the
        result is a new snip, with any step numbers and captions as markup, opened in the editor."""
        from . import combine_dialog

        combined = combine_dialog.open_combine(self.library, entry_ids, parent)
        if combined is None:
            return
        entry = None
        QApplication.setOverrideCursor(Qt.WaitCursor)  # a big combined image takes a moment to save
        try:
            entry = self.library.create(combined.image)
            if combined.items:  # step numbers and captions: editable markup, drawn into its preview
                self.library.save(entry, *combined.finished())
        except Exception as exc:  # never a half-made snip: one without its steps goes again
            log.exception("Couldn't add the combined snip to the Library")
            if entry is not None:
                self.library.move_to_trash(entry)
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(None, "SnipForge", f"Couldn't save the combined snip: {exc}")
            return
        QApplication.restoreOverrideCursor()
        self.indexer.index(entry)
        if self._home is not None and self._home.isVisible():
            self._home.update_entry(entry.id)
        self.open_entry(entry.id)

    def open_entry(self, entry_id: str) -> None:
        for window in self._windows:
            if isinstance(window, EditorWindow) and window._entry and window._entry.id == entry_id:
                self._bring_to_front(window)
                return
        entry = self.library.get(entry_id)
        if entry is None:
            QMessageBox.information(None, "SnipForge", "That snip is no longer in the library.")
            return
        if entry.is_recording:
            self._open_recording(entry.recording_path)
            return
        try:
            image = self.library.load_image(entry)
        except OSError as exc:
            QMessageBox.warning(None, "SnipForge", f"Couldn't open that snip: {exc}")
            return
        try:
            document, autosave = self.library.load_document(entry), True
        except DocumentUnreadable as exc:
            log.warning("%s", exc)
            QMessageBox.warning(None, "SnipForge", "Couldn't read this snip's saved markup, so it opens "
                                "without it. Autosave is off for this window so the saved file isn't "
                                "overwritten. Use Save or Copy to keep anything you change.")
            document, autosave = None, False
        self._open_editor(image, entry, document, autosave)

    def _open_editor(self, image: QImage, entry, document, autosave: bool = True,
                     beautify_on: bool | None = None) -> None:
        editor = EditorWindow(image, self.library, entry, document, on_new_snip=self.capture,
                              on_open_entry=self.open_entry, on_pin=self.pin, autosave=autosave,
                              on_open_library=self.show_home, beautify_on=beautify_on,
                              on_combine=lambda entry_id, window: self.combine([entry_id], window))
        self._track(editor)
        editor.show()
        self._bring_to_front(editor)

    def pin(self, image: QImage) -> None:
        window = PinWindow(image, QPoint(*self._pin_position()))
        self._track(window)
        window.show()

    def _pin_position(self) -> tuple[int, int]:
        avail = QGuiApplication.primaryScreen().availableGeometry()
        offset = 30 * (sum(isinstance(w, PinWindow) for w in self._windows) % 8)
        return avail.right() - 520 - offset, avail.top() + 60 + offset

    def _track(self, window) -> None:
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.destroyed.connect(lambda _=None, w=window: self._forget(w))
        self._windows.append(window)

    def _forget(self, window) -> None:
        if window in self._windows:
            self._windows.remove(window)
        entry = getattr(window, "_entry", None)
        if entry is None:
            return  # a pin: nothing in the library changed
        self.indexer.index(entry, again=True)  # its saved markup cleared the old words: read it again
        if self._home is not None and self._home.isVisible():
            self._home.update_entry(entry.id)

    @staticmethod
    def _bring_to_front(window) -> None:
        window.setWindowState((window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        window.raise_()
        window.activateWindow()
        try:  # Windows only lets the foreground process steal focus; nudge it
            hwnd = int(window.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except (OSError, AttributeError):
            pass


def about_text() -> str:
    """What About SnipForge says, in this platform's words."""
    if wording.on_mac():
        made_for, licenses = "Mac", "inside SnipForge.app (Contents/Resources)"
    else:
        made_for, licenses = "Windows", "in the install folder"
    return (f"<b>SnipForge {__version__}</b><br>Open-source screenshot and markup tool for {made_for}."
            f"<br><br>Built with Python, Qt and PySide6 (LGPL-3.0). License texts are {licenses}."
            f"<br><br>MIT License. Your snips stay on {wording.this_computer()}. SnipForge only goes online "
            f"to check for updates (choose how in Settings) or when you use Share."
            f"<br><br><a href='{project.REPO_URL}'>{project.REPO_URL.removeprefix('https://')}</a>")


def _command_from_args(argv: list[str]) -> str:
    if "--background" in argv:
        return "background"
    if "--snip" in argv:
        return "snip"
    if "--repeat" in argv:
        return "repeat"
    if "--settings" in argv:
        return "settings"
    return "home"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if sys.platform == "win32":  # taskbar grouping and toast notifications use it
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv)
    if "--self-test" in sys.argv:
        from .selftest import run
        index = sys.argv.index("--self-test")
        report = sys.argv[index + 1] if index + 1 < len(sys.argv) else "snipforge-selftest.json"
        return run(report)
    command = _command_from_args(sys.argv)
    if send_to_running(command):
        return 0  # the running instance handles it (e.g. taskbar click opens the library)
    server = InstanceServer()
    if not server.listening and send_to_running(command):
        return 0  # lost a start-up race with another launch; let that one handle it
    logging_session = logs.setup()  # only the copy that keeps running writes the log
    if logging_session is not None:
        app.aboutToQuit.connect(logging_session.close)
        app.commitDataRequest.connect(lambda _manager: logging_session.ending())  # signing out, shutting down
        tidy = QTimer(app, interval=LOG_TICK_MS)
        tidy.timeout.connect(logging_session.tick)
        tidy.start()
    theme.install(app)
    forge = SnipForgeApp(app)
    server.command.connect(forge.handle_command)
    if logging_session is not None and logging_session.crashed_last_time:
        QTimer.singleShot(CRASH_NOTICE_DELAY_MS, forge.notice_crash)
    if command != "background":
        QTimer.singleShot(0, lambda: forge.handle_command(command))
    return app.exec()
