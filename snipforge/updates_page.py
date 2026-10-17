"""Settings > Updates: status, how updates install, release channel, versions and rollback.

Drives the UpdateController; everything shown comes from its state, release list, prefs and the
IT policy. Release notes are plain text only."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, project, updates
from .icons import icon
from .widgets import Segmented, SettingRow, ToggleSwitch, stack

MODES = (
    ("auto", "Automatically",
     "Download in the background and install the next time SnipForge starts. You'll see what changed."),
    ("ask", "Ask me first", "Download in the background, then ask before installing. You can skip any version."),
    ("manual", "Only when I check", "SnipForge never checks on its own. Use Check now when you want the latest."),
)
CHANNELS = {"stable": "Tested releases. Recommended for everyday use.",
            "preview": "New features a few weeks early. They may have rough edges; you can switch back any time."}
MANAGED = "Set by your organization"
VERSIONS_SHOWN = 8


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("sfRole", "heading")
    label.setContentsMargins(0, 18, 0, 6)
    return label


class UpdatesPage(QWidget):
    def __init__(self, controller, current_version: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._current = current_version or getattr(controller, "current_version", __version__)
        self._row_buttons: list[tuple[QPushButton, object, bool]] = []  # button, release, newer
        self._notes_open = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addWidget(self._build_status())
        col.addWidget(_heading("How updates install"))
        col.addWidget(self._build_modes())
        col.addWidget(_heading("Release channel"))
        col.addWidget(self._build_channel())
        col.addWidget(_heading("Versions"))
        self._versions = QVBoxLayout()
        self._versions.setContentsMargins(0, 0, 0, 0)
        self._versions.setSpacing(4)
        versions = QWidget()
        versions.setLayout(self._versions)
        col.addWidget(versions)
        self._pin = ToggleSwitch("Stay on the version I choose")
        self._pin.toggled.connect(self._set_pinned)
        self._pin_row = SettingRow("Stay on the version I choose", "", self._pin)
        col.addWidget(self._pin_row)
        col.addWidget(_heading("Where updates come from"))
        self._source_row = SettingRow("", "")
        col.addWidget(self._source_row)
        privacy = QLabel("SnipForge only goes online to check for and download updates, and when you use Share. "
                         "Every download is checked against its published checksum before it installs.")
        privacy.setProperty("sfRole", "muted")
        privacy.setWordWrap(True)
        privacy.setContentsMargins(0, 14, 0, 0)
        col.addWidget(privacy)
        col.addStretch(1)

        controller.state_changed.connect(self._on_state)
        controller.releases_changed.connect(self._on_releases)
        self._load_prefs()
        self._on_state(controller.state)
        self._on_releases(controller.releases)

    # ------------------------------------------------------------------ status card
    def _build_status(self) -> QWidget:
        card = QFrame()
        card.setObjectName("SettingCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(20, 18, 20, 18)
        row.setSpacing(16)
        self._badge = QLabel()
        self._badge.setObjectName("StatusBadge")
        self._badge.setFixedSize(44, 44)
        self._badge.setAlignment(Qt.AlignCenter)
        row.addWidget(self._badge, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        self._title = QLabel()
        self._title.setProperty("sfRole", "heading")
        self._title.setWordWrap(True)
        self._title.setTextFormat(Qt.PlainText)
        self._message = QLabel()
        self._message.setProperty("sfRole", "secondary")
        self._message.setWordWrap(True)
        self._message.setTextFormat(Qt.PlainText)  # can quote server text: never rich text
        self._progress = QProgressBar()
        self._progress.setAccessibleName("Update progress")
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._notes = QLabel()
        self._notes.setTextFormat(Qt.PlainText)  # release text from the network is never rendered
        self._notes.setWordWrap(True)
        self._notes.setProperty("sfRole", "secondary")
        self._notes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._primary = QPushButton()
        self._primary.setProperty("variant", "primary")
        self._primary.clicked.connect(self._primary_clicked)
        self._whats_new = QPushButton("What's new")
        self._whats_new.clicked.connect(self._toggle_notes)
        self._skip = QPushButton("Skip this version")
        self._skip.setProperty("variant", "ghost")
        self._skip.clicked.connect(self._skip_clicked)
        for b in (self._primary, self._whats_new, self._skip):
            buttons.addWidget(b)
        buttons.addStretch(1)
        for w in (self._title, self._message, self._progress, self._notes):
            text.addWidget(w)
        text.addSpacing(6)
        text.addLayout(buttons)
        foot = QHBoxLayout()
        self._last_checked = QLabel()
        self._last_checked.setProperty("sfRole", "muted")
        foot.addWidget(self._last_checked)
        foot.addStretch(1)
        self._check = QPushButton("Check now")
        self._check.clicked.connect(self._controller.check_now)
        foot.addWidget(self._check)
        text.addLayout(foot)
        row.addLayout(text, 1)
        return card

    def _on_state(self, state) -> None:
        policy = self._controller.policy
        release = state.release
        kind = "disabled" if policy.disabled else state.kind
        blocker = self._controller.install_blocker(release) if release is not None else ""
        titles = {
            "disabled": "Updates are managed by your organization",
            "idle": "Updates", "checking": "Checking for updates…", "current": "You're up to date",
            "available": f"SnipForge {release.version} is available" if release else "An update is available",
            "downloading": f"Downloading SnipForge {release.version}…" if release else "Downloading…",
            "ready": f"SnipForge {release.version} is ready to install" if release else "Ready to install",
            "installing": "Installing…", "error": "Couldn't update",
        }
        self._title.setText(titles.get(kind, "Updates"))
        message = state.message or (f"You have SnipForge {self._current}." if kind in ("idle", "current") else "")
        if kind == "disabled":
            message = "Your IT team turned off update checks on this PC."
        if blocker and kind in ("available", "ready") and blocker not in message:
            message = f"{message} {blocker}".strip()
        self._message.setText(message)
        self._message.setVisible(bool(message))
        self._badge.setPixmap(icon({"ready": "save", "available": "save", "error": "info", "disabled": "lock"}
                                   .get(kind, "refresh")).pixmap(22, 22))
        self._progress.setVisible(kind in ("checking", "downloading", "installing"))
        self._progress.setRange(0, 0 if kind in ("checking", "installing") else 100)
        self._progress.setValue(state.progress)
        primary = {"available": "Install now", "ready": "Restart and update", "checking": "Cancel",
                   "downloading": "Cancel", "error": "Try again"}.get(kind)
        self._primary.setText(primary or "")
        self._primary.setVisible(primary is not None)
        self._primary.setEnabled(not (blocker and kind in ("available", "ready")))
        offer = kind in ("available", "ready") and release is not None
        self._whats_new.setVisible(offer and bool(release.notes))
        self._skip.setVisible(offer and not self._controller.is_skipped(release))
        self._notes.setText(release.notes if release is not None else "")
        self._notes.setVisible(offer and self._notes_open and bool(release.notes))
        self._check.setEnabled(not policy.disabled and not self._controller.busy)
        when = state.last_checked
        self._last_checked.setText(f"Last checked {when:%b} {when.day} at {when.strftime('%I:%M %p').lstrip('0')}"
                                   if when else "Not checked yet")
        self._kind = kind
        self._state = state
        self._refresh_row_buttons()

    def _primary_clicked(self) -> None:
        kind, release = self._kind, self._state.release
        if kind in ("checking", "downloading"):
            self._controller.cancel()
        elif kind == "ready":
            self._controller.install_ready()
        elif kind == "available" and release is not None:
            self._controller.install_version(release.version)
        elif kind == "error":
            self._controller.check_now()

    def _skip_clicked(self) -> None:
        if self._state.release is not None:
            self._controller.skip(self._state.release.version)
            self._on_state(self._controller.state)

    def _toggle_notes(self) -> None:
        self._notes_open = not self._notes_open
        self._on_state(self._state)

    # ------------------------------------------------------------------ preferences
    def _build_modes(self) -> QWidget:
        self._mode_group = QButtonGroup(self)
        self._mode_buttons: dict[str, QRadioButton] = {}
        cards = []
        for key, label, description in MODES:
            radio = QRadioButton()
            radio.setAccessibleName(label)
            radio.toggled.connect(lambda on, k=key: on and self._set_mode(k))
            self._mode_group.addButton(radio)
            self._mode_buttons[key] = radio
            card = SettingRow(label, description, radio)
            cards.append(card)
        self._mode_note = QLabel(MANAGED)
        self._mode_note.setProperty("sfRole", "muted")
        return stack(*cards, self._mode_note)

    def _build_channel(self) -> QWidget:
        self._channel = Segmented([("stable", "Stable"), ("preview", "Preview")])
        self._channel.changed.connect(self._set_channel)
        self._channel_row = SettingRow("Stable", CHANNELS["stable"], self._channel)
        return self._channel_row

    def _load_prefs(self) -> None:
        prefs, policy = self._controller.prefs, self._controller.policy
        self._loading = True
        self._mode_buttons[prefs.mode].setChecked(True)
        for button in self._mode_buttons.values():
            button.setEnabled(policy.mode is None and not policy.disabled)
        self._mode_note.setVisible(policy.mode is not None)
        self._channel.set_value(prefs.channel)
        self._channel.setEnabled(policy.channel is None and not policy.disabled)
        self._channel_row.title.setText("Preview" if prefs.channel == "preview" else "Stable")
        self._channel.setAccessibleName(f"Release channel: {self._channel_row.title.text()}")
        self._channel_row.set_description(CHANNELS[prefs.channel] + (f" {MANAGED}." if policy.channel else ""))
        pinned = prefs.pinned_version
        self._pin.setChecked(pinned is not None)
        self._pin.setEnabled(not policy.disabled)
        self._pin_row.set_description(
            f"SnipForge stays on {pinned} and won't offer newer versions until you turn this off." if pinned else
            "Turn on to stop SnipForge offering newer versions, for example after going back to an older one.")
        if policy.source == "feed":
            self._source_row.title.setText("Managed by your organization")
            self._source_row.set_description(f"Updates come from {policy.feed or 'a location your IT team chose'}.")
        else:
            self._source_row.title.setText("GitHub releases")
            where = project.REPO_URL.removeprefix("https://")
            self._source_row.set_description(f"{where}: releases, each with a checksum file.")
        self._loading = False

    def _set_prefs(self, **changes) -> None:
        if self._loading:
            return
        self._controller.set_prefs(replace(self._controller.prefs, **changes))
        self._load_prefs()

    def _set_mode(self, mode: str) -> None:
        self._set_prefs(mode=mode)

    def _set_channel(self, channel: str) -> None:
        self._set_prefs(channel=channel)

    def _set_pinned(self, on: bool) -> None:
        self._set_prefs(pinned_version=self._current if on else None)

    # ------------------------------------------------------------------ versions
    def _on_releases(self, releases) -> None:
        self._row_buttons = []
        while self._versions.count():
            widget = self._versions.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        if not releases:
            empty = QLabel("Check for updates to see the versions you can install.")
            empty.setProperty("sfRole", "muted")
            self._versions.addWidget(empty)
            return
        newest_stable = next((r.version for r in releases if not r.prerelease), None)
        for release in releases[:VERSIONS_SHOWN]:
            self._versions.addWidget(self._version_row(release, newest_stable))

    def _version_row(self, release, newest_stable: str | None) -> QWidget:
        tags = []
        if release.version == self._current:
            tags.append("Installed")
        if release.version == newest_stable:
            tags.append("Latest")
        if release.prerelease:
            tags.append("Preview")
        summary = next((line.strip(" -*#") for line in release.notes.splitlines() if line.strip(" -*#")), "")
        when = release.published.strftime("%B %d, %Y").replace(" 0", " ") if release.published else ""
        detail = " · ".join(x for x in (", ".join(tags), when, summary[:90]) if x)
        button = None
        if release.version != self._current:
            newer = updates.is_newer(release.version, self._current)
            button = QPushButton("Install" if newer else "Go back to this version")
            button.clicked.connect(lambda _=False, r=release, n=newer: self._install(r, n))
            self._row_buttons.append((button, release, newer))
        row = SettingRow(release.version, detail, button)
        self._refresh_row_buttons()
        return row

    def _refresh_row_buttons(self) -> None:
        """Install and Go back follow the engine's state: off (with the reason) while it's busy."""
        busy = self._controller.busy or self._kind in ("installing", "disabled")
        for button, release, newer in self._row_buttons:
            reason = self._controller.install_blocker(release)
            if not reason and not newer and not self._controller.policy.allow_rollback:
                reason = "Your IT team turned off going back to older versions."
            if not reason and busy:
                reason = "Wait for the current check, download or install to finish."
            button.setEnabled(not reason)
            button.setToolTip(reason)
            button.setAccessibleDescription(reason)

    def _install(self, release, newer: bool) -> None:
        prefs = self._controller.prefs
        if not newer:
            box = QMessageBox(QMessageBox.Question, "Go back to an older version",
                              f"Install SnipForge {release.version}? SnipForge will stay on that version until you "
                              "turn off \"Stay on the version I choose\". Your snips and settings are kept.",
                              QMessageBox.Yes | QMessageBox.No, self)
            box.setDefaultButton(QMessageBox.No)
            if box.exec() != QMessageBox.Yes:
                return
        elif prefs.pinned_version:
            box = QMessageBox(QMessageBox.Question, "Stay on this version?",
                              f"You chose to stay on {prefs.pinned_version}. After installing {release.version}, "
                              "keep getting newer versions?", QMessageBox.Yes | QMessageBox.No, self)
            box.setDefaultButton(QMessageBox.Yes)
            if box.exec() == QMessageBox.Yes:
                self._controller.set_prefs(replace(prefs, pinned_version=None))
        self._controller.install_version(release.version)
        self._load_prefs()
