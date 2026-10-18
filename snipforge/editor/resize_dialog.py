"""Resize the snip: a new width and height in pixels or percent, with the aspect ratio kept unless
you turn that off."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLayout,
    QSpinBox,
    QVBoxLayout,
)

from ..widgets import Segmented
from .image_ops import MAX_PIXELS, MAX_SIDE, fits

MAX_PERCENT = 1000
WIDTH = 340
UNITS = (("pixels", "Pixels"), ("percent", "Percent"))


class ResizeDialog(QDialog):
    def __init__(self, width: int, height: int, allowed=fits, parent=None) -> None:
        """`allowed(w, h)` says whether the canvas can make the snip that size (the default only
        checks SnipForge's limits for the size itself)."""
        super().__init__(parent)
        self.setWindowTitle("Resize")
        self._width, self._height = width, height
        self._allowed = allowed
        self._unit = "pixels"
        self._syncing = False

        column = QVBoxLayout(self)
        column.setSizeConstraint(QLayout.SetFixedSize)  # grows with the warning instead of squashing the boxes
        column.setContentsMargins(20, 18, 20, 16)
        column.setSpacing(12)
        now = QLabel(f"Now {width} × {height} px")
        now.setProperty("sfRole", "secondary")
        column.addWidget(now)
        self.units = Segmented(list(UNITS), value="pixels")
        self.units.changed.connect(self.set_unit)
        column.addWidget(self.units)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self.width_box, self.height_box = QSpinBox(), QSpinBox()
        for box, name in ((self.width_box, "Width"), (self.height_box, "Height")):
            box.setAccessibleName(name)
            box.setAlignment(Qt.AlignRight)
            box.setMinimumWidth(120)
            form.addRow(name, box)
        column.addLayout(form)
        self.keep_ratio = QCheckBox("Keep the aspect ratio")
        self.keep_ratio.setChecked(True)
        column.addWidget(self.keep_ratio)
        self._result = QLabel()
        self._result.setProperty("sfRole", "secondary")
        self._result.setWordWrap(True)
        self._result.setFixedWidth(WIDTH - 40)
        column.addWidget(self._result)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText("Resize")
        self.ok_button.setProperty("variant", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        self.width_box.valueChanged.connect(lambda v: self._edited(self.width_box, v))
        self.height_box.valueChanged.connect(lambda v: self._edited(self.height_box, v))
        self._load(width, height)

    # ------------------------------------------------------------------ units
    def set_unit(self, unit: str) -> None:
        if unit == self._unit:
            return
        w, h = self.result_size()
        self._unit = unit
        self.units.set_value(unit)
        self._load(w, h)

    def _load(self, w: int, h: int) -> None:
        self._syncing = True
        if self._unit == "percent":
            for box, value, whole in ((self.width_box, w, self._width), (self.height_box, h, self._height)):
                box.setRange(1, MAX_PERCENT)
                box.setSuffix(" %")
                box.setValue(round(value * 100 / whole))
        else:
            for box, value in ((self.width_box, w), (self.height_box, h)):
                box.setRange(1, MAX_SIDE * 4)  # room to type a too-big size and be told why
                box.setSuffix(" px")
                box.setValue(value)
        self._syncing = False
        self._refresh()

    # ------------------------------------------------------------------ editing
    def _edited(self, box: QSpinBox, value: int) -> None:
        if self._syncing:
            return
        if self.keep_ratio.isChecked():
            self._syncing = True
            other = self.height_box if box is self.width_box else self.width_box
            if self._unit == "percent":
                other.setValue(value)
            elif box is self.width_box:
                other.setValue(max(1, round(value * self._height / self._width)))
            else:
                other.setValue(max(1, round(value * self._width / self._height)))
            self._syncing = False
        self._refresh()

    def result_size(self) -> tuple[int, int]:
        """The new size in pixels."""
        w, h = self.width_box.value(), self.height_box.value()
        if self._unit == "percent":
            return max(1, round(self._width * w / 100)), max(1, round(self._height * h / 100))
        return w, h

    def _refresh(self) -> None:
        w, h = self.result_size()
        ok = self._allowed(w, h)
        self.ok_button.setEnabled(ok)
        if ok:
            self._result.setText(f"New size {w} × {h} px")
        else:
            self._result.setText(f"{w} × {h} px is too big. The largest is {MAX_SIDE:,} px on a side and "
                                 f"{MAX_PIXELS // 1_000_000} megapixels in all, counting any part you cropped.")

    def warning_text(self) -> str:
        return "" if self.ok_button.isEnabled() else self._result.text()
