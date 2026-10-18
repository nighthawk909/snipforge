"""A capture session's preset half (snipforge/presets.py): the choices a snip starts with (a preset's,
or your usual ones), picking a preset on the capture toolbar, and a Full screen preset's snip of
the monitor under the pointer, with nothing to choose.

Only your usual choices are remembered when the mode, delay, pointer or size change on the toolbar;
during a preset's snip such a change applies to that snip only.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRect

from . import prefs, presets, scroll_input


class PresetChoices:
    """Mixed into CaptureSession, which provides the overlays, the countdown and the finish."""

    preset: presets.Preset | None
    _whole_screen: bool  # a Full screen preset's mode, not changed by hand: snipped at once

    def _use_preset(self, preset: presets.Preset | None) -> None:
        self.preset = preset
        self._whole_screen = preset is not None and preset.mode == "screen"

    def _delay(self) -> int:
        return self.preset.delay if self.preset is not None else prefs.capture_delay()

    def _pointer(self) -> bool:
        return self.preset.pointer if self.preset is not None else prefs.capture_pointer()

    def _fixed_size(self) -> tuple[int, int]:
        return self.preset.fixed_size if self.preset is not None else prefs.capture_fixed_size()

    def _start_mode(self) -> str:
        if self.preset is not None:
            mode = self.preset.mode
            return "area" if mode == "scroll" and not scroll_input.available() else mode
        mode = prefs.capture_mode()
        return "area" if mode == "scroll" else mode  # remembered by an older version: never start in it

    def _snips_whole_screen(self) -> bool:
        return self._whole_screen and not self.record

    def _offered(self) -> dict:
        """What the overlays' toolbar needs to offer the presets."""
        return {"presets": [(p.id, p.name) for p in presets.load()],
                "preset": self.preset.id if self.preset is not None else ""}

    def _changed_by_hand(self, **choice) -> bool:
        """A choice made on the toolbar: kept for this snip only during a preset's snip (True), or
        remembered as one of your usual choices (False: the caller saves it)."""
        if self.preset is None:
            return False
        self.preset = replace(self.preset, **choice)
        return True

    def _snip_screen(self, screen, shot, windows: list[QRect], infos: list) -> None:
        """All of `screen`, straight away, through an overlay that's never shown (as Repeat last
        snip does), so the pointer and the image come out as a chosen snip's would."""
        from . import capture

        overlay = capture._ScreenOverlay(screen, capture._grab_screen(screen), windows, infos, mode="screen",
                                         pointer_shot=shot, include_pointer=self._pointer(), scrolling=False)
        try:
            overlay.selected.connect(lambda image: self._took(overlay, image))
            overlay._emit(overlay.rect())
        finally:
            overlay.deleteLater()

    def _set_preset(self, preset_id: str) -> None:
        """A preset picked on the toolbar (or "" for your usual choices). Its choices apply to every
        monitor straight away; its delay starts a countdown, as picking a delay does, and a Full
        screen preset takes the monitor the pointer is on. Picking the one in use changes nothing."""
        current = self.preset.id if self.preset is not None else ""
        if self._finished or preset_id == current:
            return
        self._use_preset(presets.get(preset_id) if preset_id else None)
        self._mode = mode = self._start_mode()
        delay = self._delay()
        for overlay in self._overlays:
            overlay.set_preset(self.preset.id if self.preset is not None else "")
            overlay.set_mode(mode)
            overlay.set_include_pointer(self._pointer())
            overlay.set_fixed_size(*self._fixed_size())
            overlay.set_delay(delay)
        if delay:
            self._close_overlays()
            self._count_down(delay)
        elif self._snips_whole_screen() and self.focus is not None:
            self.focus._emit(self.focus.rect())

    def _manage_presets(self) -> None:
        """The toolbar's Manage presets: this snip is over, and Settings takes it from here."""
        self._finish(None)
        if self._on_manage_presets is not None:
            self._on_manage_presets()
