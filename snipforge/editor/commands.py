"""Undo commands. Edits are recorded as before/after state snapshots."""
from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QUndoCommand


class AddItemsCommand(QUndoCommand):
    def __init__(self, scene, items, text: str = "Add") -> None:
        super().__init__(text)
        self._scene, self._items = scene, list(items)

    def redo(self) -> None:
        for item in self._items:
            if item.scene() is None:
                self._scene.addItem(item)

    def undo(self) -> None:
        for item in self._items:
            item.setSelected(False)
            self._scene.removeItem(item)


class RemoveItemsCommand(QUndoCommand):
    def __init__(self, scene, items) -> None:
        super().__init__("Delete")
        self._scene, self._items = scene, list(items)

    def redo(self) -> None:
        for item in self._items:
            item.setSelected(False)
            self._scene.removeItem(item)

    def undo(self) -> None:
        for item in self._items:
            self._scene.addItem(item)


class StateCommand(QUndoCommand):
    """Moves, resizes, restyles and text edits: anything expressible as state."""

    MERGE_ID = 1

    def __init__(self, changes: list[tuple[object, dict, dict]], text: str = "Edit",
                 already_applied: bool = True, merge_key=None) -> None:
        super().__init__(text)
        self._changes = changes
        self._skip_first = already_applied
        self._merge_key = merge_key  # consecutive edits with the same key collapse into one undo step

    def id(self) -> int:
        return self.MERGE_ID if self._merge_key is not None else -1

    def mergeWith(self, other) -> bool:
        if other._merge_key != self._merge_key:
            return False
        afters = {id(item): after for item, _b, after in other._changes}
        self._changes = [(item, before, afters.get(id(item), after)) for item, before, after in self._changes]
        return True

    def redo(self) -> None:
        if self._skip_first:
            self._skip_first = False
            return
        for item, _before, after in self._changes:
            item.set_state(after)

    def undo(self) -> None:
        for item, before, _after in self._changes:
            item.set_state(before)


class ImageCommand(QUndoCommand):
    """Rotate, flip or resize: the image's geometry, the crop and every annotation, as one step."""

    def __init__(self, canvas, text: str, geometries: tuple, crops: tuple[QRectF, QRectF],
                 changes: list[tuple[object, dict, dict]]) -> None:
        super().__init__(text)
        self._canvas, self._geometries, self._changes = canvas, geometries, changes
        self._crops = (QRectF(crops[0]), QRectF(crops[1]))

    def _apply(self, side: int) -> None:
        self._canvas.set_image_geometry(self._geometries[side])
        self._canvas.set_crop_rect(self._crops[side])
        for item, *states in self._changes:
            item.set_state(states[side])
        self._canvas.fit_image()

    def redo(self) -> None:
        self._apply(1)

    def undo(self) -> None:
        self._apply(0)


class CropCommand(QUndoCommand):
    def __init__(self, canvas, old: QRectF, new: QRectF) -> None:
        super().__init__("Crop")
        self._canvas, self._old, self._new = canvas, QRectF(old), QRectF(new)

    def redo(self) -> None:
        self._canvas.set_crop_rect(self._new)

    def undo(self) -> None:
        self._canvas.set_crop_rect(self._old)
