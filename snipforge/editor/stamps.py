"""Stamp annotations: full-color icons (check, warning, cursor...) you drop onto a snip."""
from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .items import BoxItem

DEFAULT_SIZE = 56.0
_W = 'stroke="#fff" stroke-linecap="round" stroke-linejoin="round" fill="none"'

# name -> (label, svg body in a 64x64 viewBox)
STAMPS: dict[str, tuple[str, str]] = {
    "check": ("Check", f'<circle cx="32" cy="32" r="29" fill="#34c759"/><path d="M19 33l9 9 18-19" {_W} stroke-width="7"/>'),
    "cross": ("Cross", f'<circle cx="32" cy="32" r="29" fill="#ff3b30"/><path d="M22 22l20 20M42 22L22 42" {_W} stroke-width="7"/>'),
    "warning": ("Warning", '<path d="M32 5L61 56Q62.5 59.5 58.5 59.5H5.5Q1.5 59.5 3 56z" fill="#ff9f0a"/>'
                f'<path d="M32 23v17" {_W} stroke-width="7"/><circle cx="32" cy="50" r="4.2" fill="#fff"/>'),
    "info": ("Info", f'<circle cx="32" cy="32" r="29" fill="#0a84ff"/><circle cx="32" cy="19" r="4.5" fill="#fff"/>'
             f'<path d="M32 29v18" {_W} stroke-width="7"/>'),
    "question": ("Question", f'<circle cx="32" cy="32" r="29" fill="#bf5af2"/>'
                 f'<path d="M23.5 24a8.5 8.5 0 1 1 12.5 7.5c-2.6 1.4-4 3.2-4 6V39" {_W} stroke-width="6"/>'
                 '<circle cx="32" cy="48.5" r="4" fill="#fff"/>'),
    "plus": ("Plus", f'<circle cx="32" cy="32" r="29" fill="#34c759"/><path d="M32 18v28M18 32h28" {_W} stroke-width="7"/>'),
    "minus": ("Minus", f'<circle cx="32" cy="32" r="29" fill="#ff3b30"/><path d="M18 32h28" {_W} stroke-width="7"/>'),
    "star": ("Star", '<path d="M32 4l8.6 17.5 19.4 2.8-14 13.6 3.3 19.3L32 48.1 14.7 57.2 18 37.9 4 24.3l19.4-2.8z" '
             'fill="#ffcc00" stroke="#c79100" stroke-width="2" stroke-linejoin="round"/>'),
    "heart": ("Heart", '<path d="M32 57S5 41 5 22.5A13.5 13.5 0 0 1 32 16a13.5 13.5 0 0 1 27 6.5C59 41 32 57 32 57z" fill="#ff2d55"/>'),
    "thumbsup": ("Thumbs up", '<circle cx="32" cy="32" r="29" fill="#0a84ff"/>'
                 '<path d="M17 30h7v17h-7zM27 47h13.5c1.8 0 3.3-1.2 3.8-2.9l3.4-11.6c.6-2.1-1-4.2-3.2-4.2H36l1.6-7.3'
                 'c.4-2-1-3.9-3-3.9L27 29z" fill="#fff"/>'),
    "smile": ("Smile", '<circle cx="32" cy="32" r="29" fill="#ffd60a"/><circle cx="23" cy="26" r="4" fill="#1c1c1e"/>'
              '<circle cx="41" cy="26" r="4" fill="#1c1c1e"/><path d="M20 38a14 14 0 0 0 24 0" stroke="#1c1c1e" '
              'stroke-width="4.5" stroke-linecap="round" fill="none"/>'),
    "bulb": ("Idea", '<path d="M32 5a18 18 0 0 0-10.5 32.6C23.6 39.2 25 41.6 25 44.5h14c0-2.9 1.4-5.3 3.5-6.9A18 18 0 0 0 32 5z" '
             'fill="#ffd60a" stroke="#c79100" stroke-width="2"/><rect x="24.5" y="47.5" width="15" height="5" rx="2" '
             'fill="#8e8e93"/><rect x="26.5" y="54.5" width="11" height="4.5" rx="2" fill="#8e8e93"/>'),
    "flag": ("Flag", '<path d="M15 6v53" stroke="#636366" stroke-width="4.5" stroke-linecap="round"/>'
             '<path d="M17 8h33l-7.5 10.5L50 29H17z" fill="#ff3b30"/>'),
    "pin": ("Pin", '<path d="M32 4a19 19 0 0 0-19 19c0 14.5 19 37 19 37s19-22.5 19-37A19 19 0 0 0 32 4z" fill="#ff3b30"/>'
            '<circle cx="32" cy="23" r="7" fill="#fff"/>'),
    "lock": ("Lock", '<path d="M21 28v-7a11 11 0 0 1 22 0v7" stroke="#8e8e93" stroke-width="6" fill="none"/>'
             '<rect x="12" y="27" width="40" height="31" rx="5" fill="#ff9f0a"/><circle cx="32" cy="41" r="4.5" fill="#fff"/>'
             '<path d="M32 43v6" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/>'),
    "cursor": ("Mouse pointer", '<path d="M16 5v45l11.5-10.5 7.5 17 8.5-3.8-7.5-16.6H52z" fill="#fff" stroke="#000" '
               'stroke-width="3" stroke-linejoin="round"/>'),
}


def _svg(name: str) -> bytes:
    body = STAMPS.get(name, STAMPS["check"])[1]
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">{body}</svg>'.encode()


@cache
def renderer(name: str) -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_svg(name)))


@cache
def stamp_icon(name: str, size: int = 40) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    renderer(name).render(p, QRectF(0, 0, size, size))
    p.end()
    return QIcon(pix)


class StampItem(BoxItem):
    kind = "stamp"
    always_square = True
    min_size = (12.0, 12.0)

    def _margin(self) -> float:
        return 2.0

    def paint(self, p: QPainter, _opt, _widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        renderer(self.style.stamp).render(p, self._rect)

    def is_degenerate(self) -> bool:
        return self._rect.width() < 8
